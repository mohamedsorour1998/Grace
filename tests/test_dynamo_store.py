"""One test body, both stores.

`InMemoryCaseStore` and `DynamoDBCaseStore` must be behaviourally
interchangeable, because Task 8's trajectory evals read ledger *position* to
assert reads precede actions, and Task 6's `sweep` classifies a case by scanning
for a `renewal_submitted` row. If the two stores returned entries in different
orders, every eval would break in a way that looks like a gate regression rather
than a storage bug.

So the conformance tests below are parametrized over both. A separate test file
for the new store is exactly how the two would drift apart — the same reasoning
that makes `_most_recent` an import shared with the gate rather than a second
dict comprehension.

The DynamoDB store is driven against a local fake client here, not against AWS:
these must run in the fast suite, which is offline. Task 10's real deployed sweep
is what exercises the wire format.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from grace.cases.dynamo_store import DynamoDBCaseStore, _attr, to_dynamo
from grace.cases.models import LedgerEntry
from grace.cases.store import CaseStore, InMemoryCaseStore, load_fixture_cases

TODAY = date(2026, 10, 1)
AT = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)

# Small enough that the fake paginates on a realistic ledger (one tool call
# writes `tool_call` then `tool_result`, so four calls already exceed this) and
# large enough that most tests see a single page. The point is that the store's
# `LastEvaluatedKey` loop is *exercised*, not merely present: a pagination loop
# no test iterates is indistinguishable from one that does not work.
FAKE_PAGE_SIZE = 3


class FakeTable:
    """The slice of the DynamoDB client the store uses, in memory.

    Deliberately not `moto`: that is a new test dependency for four API calls,
    and Global Constraints keep dependencies minimal.

    It is faithful in the two ways that matter, both of which cost real bugs if
    faked loosely:

    1. **It validates `N` the way DynamoDB does.** A fake that cannot fail the
       way the real service fails is worse than no fake. Confirmed against the
       real table: `{"N": "NaN"}` and `{"N": "Infinity"}` are rejected with
       `ValidationException`, as is a number past DynamoDB's exponent range. The
       plan's original check — reject a `float` inside `{"N": ...}` — could
       never fire, because `_attr` always stringifies the number before it gets
       here, so it asserted nothing.
    2. **It paginates.** A real Query returns at most 1MB and signals more via
       `LastEvaluatedKey`. A fake that always returns everything lets a store
       with no pagination loop pass, and the failure mode is a *silently
       truncated audit trail* — `sweep` looks for a `renewal_submitted` row, so
       a dropped page reports a filed renewal as unfiled with no error.
    """

    # DynamoDB's documented numeric range: 38 significant digits, exponent
    # -128..126. Anything outside it is a ValidationException on the wire —
    # all three limits confirmed against the real `grace-cases` table.
    _MAX = Decimal("9.9999999999999999999999999999999999999E+125")
    _MAX_SIGNIFICANT_DIGITS = 38

    def __init__(self, page_size: int = FAKE_PAGE_SIZE) -> None:
        self.items: dict[tuple[str, str], dict] = {}
        self.page_size = page_size
        self.query_calls = 0

    def put_item(self, TableName: str, Item: dict) -> dict:
        for key, value in Item.items():
            if not isinstance(value, dict) or len(value) != 1:
                raise TypeError(f"not an attribute value for {key!r}: {value!r}")
            if "N" not in value:
                continue
            raw = value["N"]
            if not isinstance(raw, str):
                # The real service takes `N` as a string; boto3 does not coerce.
                raise TypeError(f"N must be a string for {key!r}, got {raw!r}")
            try:
                number = Decimal(raw)
            except Exception as exc:  # pragma: no cover - malformed literal
                raise TypeError(f"unparseable number for {key!r}: {raw!r}") from exc
            if not number.is_finite():
                raise TypeError(f"NaN/Infinity rejected for {key!r}: {raw!r}")
            if abs(number) > self._MAX:
                raise TypeError(f"number overflow for {key!r}: {raw!r}")
            # 38 significant digits, confirmed against the real table. This is
            # what catches `Decimal(1.1)` — the binary-float noise it captures
            # is 52 digits long, so the mistake fails the *write* rather than
            # only reading back imprecisely.
            digits = len(number.as_tuple().digits)
            if digits > self._MAX_SIGNIFICANT_DIGITS:
                raise TypeError(f"more than 38 significant digits for {key!r}: {raw!r}")
        self.items[(Item["pk"]["S"], Item["sk"]["S"])] = Item
        return {}

    def query(self, **kwargs) -> dict:
        self.query_calls += 1
        pk = kwargs["ExpressionAttributeValues"][":pk"]["S"]
        prefix = kwargs["ExpressionAttributeValues"].get(":prefix", {}).get("S", "")
        rows = [
            item
            for (item_pk, sk), item in self.items.items()
            if item_pk == pk and sk.startswith(prefix)
        ]
        forward = kwargs.get("ScanIndexForward", True)
        rows.sort(key=lambda i: i["sk"]["S"], reverse=not forward)
        start = kwargs.get("ExclusiveStartKey")
        if start is not None:
            after = start["sk"]["S"]
            rows = [r for r in rows if (r["sk"]["S"] > after) == bool(forward)]
        page, rest = rows[: self.page_size], rows[self.page_size :]
        response: dict = {"Items": page}
        if rest:
            # Real DynamoDB returns the last evaluated key, not a cursor object.
            response["LastEvaluatedKey"] = {"pk": page[-1]["pk"], "sk": page[-1]["sk"]}
        return response


def _dynamo_store(cases, **kwargs) -> DynamoDBCaseStore:
    return DynamoDBCaseStore(
        cases, table_name="grace-cases-test", client=FakeTable(**kwargs)
    )


@pytest.fixture(params=["memory", "dynamo"])
def store(request) -> CaseStore:
    cases = load_fixture_cases()
    if request.param == "memory":
        return InMemoryCaseStore(cases)
    return _dynamo_store(cases)


def test_the_parametrized_fixture_covers_both_implementations(store):
    """Guard against the conformance suite quietly testing one store twice.

    The whole value of the file is that every assertion below runs against
    both, and a fixture that silently built the same store for both params
    would look identical in every test report. Recorded per-run and asserted
    across runs by `test_both_store_types_were_actually_exercised` below.
    """
    _EXERCISED.add(type(store).__name__)
    assert isinstance(store, (InMemoryCaseStore, DynamoDBCaseStore))


_EXERCISED: set[str] = set()


# ---------------------------------------------------------------------------
# Conformance — every assertion below must hold for BOTH implementations
# ---------------------------------------------------------------------------


def test_the_store_satisfies_the_protocol(store):
    """`CaseStore` is `@runtime_checkable` precisely so this is checkable
    rather than merely documented."""
    assert isinstance(store, CaseStore)


def test_all_twelve_fixtures_are_open(store):
    """The demo's arithmetic depends on twelve. A store that dropped one
    would report 8/3 and look plausible."""
    assert len(store.open_cases()) == 12


def test_an_unknown_case_raises_key_error(store):
    """Fail closed: an unreadable case must escalate, never be assumed
    clean (Tasks 3 and 4). Both stores raise the same type so a caller's
    `except KeyError` works against either."""
    with pytest.raises(KeyError):
        store.get("c-999")


def test_a_ledger_entry_for_an_unknown_case_is_rejected(store):
    """A typo'd `case_id` is a bug at the call site, not a new case. Both
    stores refuse, so neither opens a phantom bucket that `ledger()` would
    later report as an innocent empty list."""
    with pytest.raises(KeyError):
        store.append_ledger(
            LedgerEntry(case_id="c-999", at=AT, kind="tool_call", detail={"tool": "read_case"})
        )


def test_the_ledger_starts_empty_and_is_per_case(store):
    """One family's trail must never leak into another's."""
    assert store.ledger("c-001") == []
    store.append_ledger(
        LedgerEntry(case_id="c-001", at=AT, kind="tool_call", detail={"tool": "read_case"})
    )
    assert len(store.ledger("c-001")) == 1
    assert store.ledger("c-002") == []


def test_entries_come_back_in_append_order(store):
    """The property the evals depend on. `read_case` must be readable as
    having preceded `submit_renewal`, and that is positional."""
    for n, tool in enumerate(["read_case", "check_window", "list_documents", "submit_renewal"]):
        store.append_ledger(
            LedgerEntry(
                case_id="c-001",
                at=AT + timedelta(seconds=n),
                kind="tool_call",
                detail={"tool": tool},
            )
        )
    assert [e.detail["tool"] for e in store.ledger("c-001")] == [
        "read_case",
        "check_window",
        "list_documents",
        "submit_renewal",
    ]


def test_append_order_survives_more_rows_than_one_page(store):
    """Append order must hold across a paginated read, not just within a page.

    A real Query returns at most 1MB and signals the rest via
    `LastEvaluatedKey`; the fake paginates at `FAKE_PAGE_SIZE` so this test
    actually crosses a page boundary. Without the store's pagination loop the
    tail of the audit trail vanishes silently — and `sweep` classifies a case
    by looking for a `renewal_submitted` row, which is written last.
    """
    tools = [f"tool_{n:02d}" for n in range(FAKE_PAGE_SIZE * 3 + 1)]
    for n, tool in enumerate(tools):
        store.append_ledger(
            LedgerEntry(
                case_id="c-001",
                at=AT + timedelta(seconds=n),
                kind="tool_call",
                detail={"tool": tool},
            )
        )
    assert [e.detail["tool"] for e in store.ledger("c-001")] == tools


def test_two_entries_with_an_identical_timestamp_both_survive(store):
    """The collision case, asserted on both stores. One tool call writes
    `tool_call` and `tool_result` back to back and they can share a
    microsecond; if the second overwrote the first, an audit row would
    vanish silently."""
    store.append_ledger(
        LedgerEntry(case_id="c-001", at=AT, kind="tool_call", detail={"tool": "submit_renewal"})
    )
    store.append_ledger(
        LedgerEntry(
            case_id="c-001",
            at=AT,
            kind="tool_result",
            detail={"tool": "submit_renewal", "status": "success"},
        )
    )
    assert [e.kind for e in store.ledger("c-001")] == ["tool_call", "tool_result"]


def test_a_returned_entry_cannot_be_edited(store):
    """`LedgerEntry.detail` is a `MappingProxyType` (Task 2), and the evals
    read the ledger as ground truth — a mutable `detail` would let something
    retroactively change what the eval sees."""
    store.append_ledger(
        LedgerEntry(case_id="c-001", at=AT, kind="tool_call", detail={"tool": "read_case"})
    )
    entry = store.ledger("c-001")[0]
    with pytest.raises(TypeError):
        entry.detail["tool"] = "submit_renewal"  # type: ignore[index]


def test_a_none_trace_id_round_trips_as_none(store):
    """Task 9 writes `trace_id: None` when tracing is not configured, and
    the key must be present rather than absent — a reader must not have to
    guess whether tracing was on. DynamoDB's NULL type must come back as
    `None`, not as the string "None"."""
    store.append_ledger(
        LedgerEntry(
            case_id="c-001",
            at=AT,
            kind="tool_call",
            detail={"tool": "read_case", "trace_id": None},
        )
    )
    entry = store.ledger("c-001")[0]
    assert "trace_id" in entry.detail
    assert entry.detail["trace_id"] is None


def test_a_real_trace_id_round_trips_as_the_same_string(store):
    """The 32-hex-char form Task 9 writes. All-digit trace IDs exist, so a
    reader that guessed at types from the value rather than the attribute tag
    would turn one into an int and break the join to CloudWatch."""
    trace_id = "0" * 31 + "1"
    store.append_ledger(
        LedgerEntry(
            case_id="c-001",
            at=AT,
            kind="tool_call",
            detail={"tool": "read_case", "trace_id": trace_id},
        )
    )
    value = store.ledger("c-001")[0].detail["trace_id"]
    assert value == trace_id
    assert isinstance(value, str)


def test_every_scalar_type_round_trips(store):
    """`LedgerDetailValue` is `str | int | float | bool | None`. All five
    must survive storage, because a type that fails at the storage boundary
    fails *after* the action already happened.

    The float is 1.1, not 1.5: 1.5 is exactly representable in binary, so it
    round-trips identically whether or not the store converted via `str()`
    first and therefore cannot detect the mistake. `Decimal(1.1)` carries
    binary float noise that `Decimal("1.1")` does not, and the comparison is
    exact rather than `pytest.approx` for the same reason — an approximate
    comparison would accept the noisy value.
    """
    store.append_ledger(
        LedgerEntry(
            case_id="c-001",
            at=AT,
            kind="tool_result",
            detail={"s": "text", "i": 42, "f": 1.1, "b": True, "n": None},
        )
    )
    d = store.ledger("c-001")[0].detail
    assert d["s"] == "text"
    assert d["i"] == 42
    assert d["f"] == 1.1
    assert d["b"] is True
    assert d["n"] is None


def test_an_int_does_not_come_back_as_a_bool_or_a_float(store):
    """`0`/`1` are the values a sloppy reader turns into booleans, and both
    are legitimate ledger integers. Asserted on both stores so the DynamoDB
    reader cannot drift from the in-memory store's exact types."""
    store.append_ledger(
        LedgerEntry(
            case_id="c-001",
            at=AT,
            kind="tool_result",
            detail={"zero": 0, "one": 1, "flag": False},
        )
    )
    d = store.ledger("c-001")[0].detail
    assert d["zero"] == 0 and not isinstance(d["zero"], bool)
    assert d["one"] == 1 and not isinstance(d["one"], bool)
    assert d["flag"] is False


def test_both_store_types_were_actually_exercised():
    """The anti-vacuity guard on the parametrization itself.

    Task 8's lesson: a parametrized case that silently checks nothing looks
    identical to a passing one in every report. If the fixture ever built the
    same store for both params — or one param stopped being collected — the
    conformance suite above would still report all-green while only covering
    one implementation. Ordered after the conformance tests by position in the
    file, which is pytest's default execution order.
    """
    assert _EXERCISED == {"InMemoryCaseStore", "DynamoDBCaseStore"}, _EXERCISED


# ---------------------------------------------------------------------------
# DynamoDB-specific: the serialization trap
# ---------------------------------------------------------------------------


def test_a_float_becomes_a_decimal_not_a_float():
    """DynamoDB has no float type and boto3's serializer *raises* on one
    rather than coercing.

    This matters more than it looks. A float in `detail` would fail the write
    **after** the underlying action already succeeded — the family's renewal
    filed, the audit row lost. That is hard rule 6 inverted, and it is the
    same failure `str(channel.send(...))` was added to prevent in Task 4:
    a `Channel` is a plain Protocol, so a real SNS implementation returning a
    boto3 response shape is exactly how a non-scalar reaches the ledger.

    1.1, not 1.5. `Decimal(1.5) == Decimal("1.5")` is True because 1.5 is
    exactly representable in binary, so 1.5 cannot tell the two constructions
    apart and the plan's original assertion passed either way.
    `Decimal(1.1) != Decimal("1.1")`, so this fails against a store that
    skipped the `str()` step.
    """
    converted = to_dynamo(1.1)
    assert isinstance(converted, Decimal)
    assert converted == Decimal("1.1")
    assert converted != Decimal(1.1)


def test_a_bool_is_written_as_a_bool_attribute_not_a_number():
    """`isinstance(True, int)` is True in Python, so a serializer that checks
    `int` before `bool` silently stores `True` as the number 1.

    Asserted at the attribute-value level, which is where the distinction
    actually exists. `to_dynamo(True) is True` — the plan's original check —
    passes against the buggy int-before-bool ordering too, because that
    ordering also returns `True` unchanged; only the wire shape differs.
    """
    assert _attr(True) == {"BOOL": True}
    assert _attr(False) == {"BOOL": False}
    assert _attr(1) == {"N": "1"}
    assert _attr(0) == {"N": "0"}


def test_a_non_scalar_is_refused_loudly():
    """Anything outside `LedgerDetailValue` must raise here rather than
    reaching the wire and failing inside botocore with an unrelated message."""
    with pytest.raises(TypeError):
        to_dynamo({"nested": "dict"})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        to_dynamo(["list"])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        to_dynamo(date(2026, 10, 1))  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_float_is_refused_before_the_wire(value):
    """NaN and the infinities are rejected by DynamoDB (confirmed against the
    real table: `ValidationException`), so they must be refused here.

    The read path is the sharper reason. `Decimal("Infinity")` would serialize
    happily and then raise a bare `ValueError` from `int()`/`float()` inside
    `_from_attr` on a *later* read of an audit row — a write that looked
    successful producing an unreadable ledger. And a NaN is the same family of
    bug CLAUDE.md records from Plan 1's Task 1: every comparison against NaN
    is False, so a NaN threshold disabled the income check silently.
    """
    with pytest.raises(TypeError):
        to_dynamo(value)


def test_the_fake_client_rejects_what_dynamodb_rejects():
    """The fake's own faithfulness, asserted rather than assumed.

    A fake that cannot fail the way the real service fails is worse than no
    fake: it makes a broken serializer look correct. These three shapes were
    confirmed rejected by the real `grace-cases` table.
    """
    fake = FakeTable()
    base = {"pk": {"S": "CASE#c-001"}, "sk": {"S": "LEDGER#x"}}
    for bad in (
        {"N": "NaN"},
        {"N": "Infinity"},
        {"N": "1E+308"},
        # The 52-digit expansion `Decimal(1.1)` produces. Real DynamoDB refuses
        # it: "Attempting to store more than 38 significant digits in a Number".
        {"N": str(Decimal(1.1))},
    ):
        with pytest.raises(TypeError):
            fake.put_item(TableName="t", Item={**base, "d_x": bad})
    # And it accepts a legitimate number, so the guard above is not simply
    # rejecting everything.
    assert fake.put_item(TableName="t", Item={**base, "d_x": {"N": "1.1"}}) == {}


def test_the_ledger_read_actually_paginates():
    """The `LastEvaluatedKey` loop is exercised, not merely present.

    A pagination loop no test iterates is indistinguishable from one that does
    not work. The fake pages at `FAKE_PAGE_SIZE`, so writing more rows than
    that must produce more than one `query` call and still return every row.
    """
    store = _dynamo_store(load_fixture_cases())
    rows = FAKE_PAGE_SIZE * 2 + 1
    for n in range(rows):
        store.append_ledger(
            LedgerEntry(
                case_id="c-001",
                at=AT + timedelta(seconds=n),
                kind="tool_call",
                detail={"tool": f"tool_{n:02d}"},
            )
        )
    store._client.query_calls = 0
    entries = store.ledger("c-001")
    assert len(entries) == rows
    assert store._client.query_calls > 1, "pagination loop never iterated"


def test_a_single_page_read_does_not_over_query():
    """The complement: no `LastEvaluatedKey` means exactly one call. A loop
    that re-queried on an absent key would double every read's cost and could
    repeat rows."""
    store = _dynamo_store(load_fixture_cases())
    store.append_ledger(
        LedgerEntry(case_id="c-001", at=AT, kind="tool_call", detail={"tool": "read_case"})
    )
    store._client.query_calls = 0
    assert len(store.ledger("c-001")) == 1
    assert store._client.query_calls == 1


def test_the_escalation_row_is_not_returned_as_a_ledger_entry():
    """Both row kinds share a partition key, so `ledger()` must filter on the
    `LEDGER#` sort-key prefix. An escalation row surfacing as a ledger entry
    would have no `kind` attribute and break the read — and would also make
    `sweep`'s `renewal_submitted` scan read rows it does not own."""
    store = _dynamo_store(load_fixture_cases())
    store.append_ledger(
        LedgerEntry(case_id="c-011", at=AT, kind="tool_call", detail={"tool": "read_case"})
    )
    store.write_escalation(
        "c-011", reason="material_income_change", question="Which figure?", deadline="2026-10-31"
    )
    entries = store.ledger("c-011")
    assert [e.kind for e in entries] == ["tool_call"]


def test_an_escalation_row_lands_on_the_queue_index():
    """The pending-caseworker row carries `status` and `escalated_at`, which
    are the escalation-queue GSI's keys. Only escalation rows carry them, so
    the index is sparse — it holds the queue, not a filtered scan of every
    ledger row."""
    store = _dynamo_store(load_fixture_cases())
    store.write_escalation(
        "c-011",
        reason="material_income_change: Income moved 30.0%",
        question="Which income figure applies?",
        deadline="2026-10-31",
    )
    row = next(i for (pk, sk), i in store._client.items.items() if sk.startswith("ESCALATION#"))
    assert row["status"]["S"] == "PENDING_CASEWORKER"
    assert "escalated_at" in row
    assert row["pk"]["S"] == "CASE#c-011"


def test_a_ledger_row_carries_no_household_identity():
    """Hard rule 9's reasoning applied to the table: the partition key and
    every attribute this store writes carry the case id only. A name, phone,
    or address here would fan household identity into DynamoDB, which is
    outside the Bedrock guardrail's redaction."""
    store = _dynamo_store(load_fixture_cases())
    case = store.get("c-001")
    store.append_ledger(
        LedgerEntry(case_id="c-001", at=AT, kind="tool_call", detail={"tool": "read_case"})
    )
    written = repr(store._client.items)
    for secret in (
        case.household.display_name,
        case.household.phone,
        case.household.household_id,
    ):
        assert secret not in written, secret


def test_duplicate_case_ids_are_rejected():
    """Same refusal as the in-memory store: keying by id would silently drop a
    duplicate, shrinking the caseload with no error while the sweep still
    reported success."""
    cases = load_fixture_cases()
    with pytest.raises(ValueError, match="duplicate"):
        _dynamo_store([*cases, cases[0]])
