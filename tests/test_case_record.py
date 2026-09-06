"""The case record's wire shape, and the two properties that make it safe.

**Property one: it round-trips without changing a single verdict.** Moving case
records from the container image into DynamoDB is only correct if the twelve
households the demo rests on decide exactly the same way afterwards. That is
asserted here against the real gate and the real rule packs, per case and in
aggregate (nine act, three escalate), rather than inferred from "the fields look
the same".

**Property two: a record carries no household identity, ever.** Not by
filtering it out downstream but by never writing it — the same capability-absence
argument as `read_case` no longer returning `display_name`. A name in DynamoDB
would be a second place one can leak from, outside the Bedrock guardrail's
redaction, and the table-wide PII scan this project runs must keep returning
nothing.

`fixtures/case-record-shape.json` is the cross-language contract; see its own
`_comment` for why it exists. The TypeScript writer asserts against the same
file in `web/__tests__/intake.test.ts`.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from grace.authority import evaluate
from grace.cases import record
from grace.cases.models import Case, Document, Household
from grace.cases.store import load_fixture_cases
from grace.rules.pack import load_pack
from infra import naming

TODAY = date(2026, 10, 1)
PINNED = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)

SHAPE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "case-record-shape.json").read_text()
)


def _case(**overrides) -> Case:
    """A minimal, synthetic case. Identity fields present so the tests that
    assert they are dropped have something to look for."""
    base = dict(
        case_id="c-901",
        household=Household(
            household_id="h-901",
            display_name="The Testcase Household",
            language="es",
            phone="+15559990901",
            monthly_income_cents=184500,
            size=4,
        ),
        program="medicaid",
        state="NY",
        cert_end=date(2026, 11, 30),
        documents=(Document(doc_id="proof_of_income", received=date(2026, 9, 18)),),
        reported_income_cents=None,
        reported_size=None,
        source_conflicts=(),
    )
    base.update(overrides)
    return Case(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The cross-language contract
# ---------------------------------------------------------------------------


def test_the_pinned_item_decodes_to_the_pinned_case():
    """Every field in `decoded`, checked individually.

    Comparing whole objects would pass on a reader that mixed up two fields of
    the same type — `size` and `reported_size` are both small integers, and
    `program` and `state` are both short strings.
    """
    case = record.from_item(SHAPE["item"])
    expected = SHAPE["decoded"]
    assert case.case_id == expected["case_id"]
    assert case.program == expected["program"]
    assert case.state == expected["state"]
    assert case.cert_end.isoformat() == expected["cert_end"]
    assert case.household.language == expected["language"]
    assert case.household.monthly_income_cents == expected["monthly_income_cents"]
    assert case.household.size == expected["size"]
    assert case.reported_income_cents == expected["reported_income_cents"]
    assert case.reported_size is None
    assert [
        {
            "id": d.doc_id,
            "received": d.received.isoformat(),
            "expires": d.expires.isoformat() if d.expires else None,
        }
        for d in case.documents
    ] == expected["documents"]
    assert list(case.source_conflicts) == expected["source_conflicts"]
    # Read separately, because it is deliberately NOT a field on `Case`: the
    # gate's snapshot must not carry an identifier at all, so provenance about
    # the record has its own reader.
    assert record.created_by_from_item(SHAPE["item"]) == expected["created_by"]


def test_the_pinned_item_is_exactly_what_the_writer_emits():
    """Decode then re-encode must reproduce the committed item byte for byte.

    This is the assertion the TypeScript side mirrors. Without it the fixture
    would merely be an example; with it, either writer drifting from the other
    fails its own test rather than producing a row the other language cannot
    parse — a case that renders on the dashboard and is invisible to the agent.

    `created_by` has to be carried across explicitly because `from_item` returns
    a `Case`, which does not hold it. That is the point of the round trip rather
    than an inconvenience: if a future edit put the id on `Case`, every model in
    the graph would receive it.
    """
    assert (
        record.to_item(
            record.from_item(SHAPE["item"]),
            created_at=PINNED,
            created_by=record.created_by_from_item(SHAPE["item"]),
        )
        == SHAPE["item"]
    )


def test_the_directory_item_matches_the_pinned_shape():
    assert record.directory_item("c-901") == SHAPE["directory_item"]
    assert record.case_id_from_directory_item(SHAPE["directory_item"]) == "c-901"


def test_the_writer_emits_exactly_the_declared_attribute_set():
    """`RECORD_ATTRIBUTES` is what `web/__tests__/intake.test.ts` compares the
    TypeScript writer against, so it has to be the truth about this writer
    rather than a list someone remembered to update.

    That comparison named `tests/test_intake_contract.py` here and
    `fixtures/case-record-shape.json` in `web/lib/intake.ts`'s docstring, and
    neither existed on the TypeScript side — `grep case-record-shape web/`
    matched nothing at all. Both writers claimed to assert against the pinned
    item; only this one did.
    """
    assert set(record.to_item(_case(), created_at=PINNED)) == set(record.RECORD_ATTRIBUTES)
    assert len(record.RECORD_ATTRIBUTES) == len(set(record.RECORD_ATTRIBUTES))


# ---------------------------------------------------------------------------
# Hard rule 9 — no household identity in the table
# ---------------------------------------------------------------------------


def test_no_record_row_carries_a_name_a_phone_or_a_household_id():
    """All twelve fixtures, not a sample.

    Plan 3 recorded why the sample is not enough: a draft guard matched three of
    twelve surnames and missed `Fitzgerald` and `Yamamoto`, the two households
    most likely to carry a name in an escalation reason. Every case is
    serialized here and every one of its own identity values is searched for.
    """
    checked = 0
    for case in load_fixture_cases():
        blob = json.dumps(record.to_item(case, created_at=PINNED))
        for secret in (
            case.household.display_name,
            case.household.phone,
            case.household.household_id,
            # The surname alone, since `display_name` is "The X Household" and a
            # writer could plausibly emit only the middle word.
            case.household.display_name.removeprefix("The ").removesuffix(" Household"),
        ):
            assert secret and secret not in blob, f"{case.case_id}: {secret}"
        assert "+1555" not in blob
        checked += 1
    assert checked == 12


def test_the_asserters_id_never_reaches_the_object_the_gate_reasons_over():
    """`created_by` is provenance about the row, not a case fact.

    `Case` is what `evaluate` receives and what every model in the graph is
    handed — `read_case` builds its output from one. An identifier added there
    would be inside a model's context on every invocation, which is exactly how
    `display_name` reached a referee's prose and then CloudWatch. So the reader
    is a free function and the field stays off the dataclass, checked here rather
    than trusted.
    """
    assert "created_by" not in Case.__dataclass_fields__
    assert "created_by" not in Household.__dataclass_fields__
    case = record.from_item(SHAPE["item"])
    assert SHAPE["decoded"]["created_by"] not in json.dumps(case, default=str)


def test_the_identity_guard_can_actually_fail():
    """The companion that makes the guard above mean something.

    A scanner that matches nothing reports "clean" on every input. This feeds a
    name in through the one field a record *does* carry free text in —
    `source_conflicts`, which is caseworker-entered prose — and asserts the
    serialized item contains it. If this stops finding the name, the guard above
    has stopped looking.
    """
    blob = json.dumps(
        record.to_item(
            _case(source_conflicts=("the Mensah Household reports a different size",)),
            created_at=PINNED,
        )
    )
    assert "Mensah" in blob


@pytest.mark.parametrize(
    "not_opaque",
    [
        "caseworker@example.gov",
        "Ada Lovelace",
        "ada lovelace",
        "sub with spaces",
        "name<script>",
        "x" * 129,
        "sub\nnewline",
        # The one an anchored `^...$` lets through. Python's `$` also matches
        # immediately before a trailing newline, so `^[A-Za-z0-9._:-]{1,128}$`
        # accepts this — measured — while JavaScript's `$` does not, which would
        # leave the two writers disagreeing about the same value. `\Z` is what
        # closes it. A trailing newline in a durable id is also the shape that
        # splits one log line into two.
        "2448a4e8-c021-70f6-382c-e8acbb6cc956\n",
        "\n2448a4e8",
    ],
)
def test_a_created_by_that_is_not_opaque_is_refused_rather_than_stripped(not_opaque: str):
    """The provenance line's identity discipline, enforced at the writer.

    `verifySession` only checks that `sub` is a non-empty string, so nothing
    upstream refuses an email — and `created_by` is exactly the field someone
    would one day fill with a username "because it is more readable". A record
    row is read by a model and logged by Step Functions, which is the path a
    surname took to CloudWatch in Plan 2.

    Refused rather than silently dropped: dropping it would leave the case page
    saying nobody asserted the document status, which is a *different* false
    claim rather than a safe default.
    """
    with pytest.raises(record.InvalidCaseRecord, match="opaque"):
        record.to_item(_case(), created_at=PINNED, created_by=not_opaque)


@pytest.mark.parametrize(
    "opaque",
    [
        "2448a4e8-c021-70f6-382c-e8acbb6cc956",  # a real Cognito sub's shape
        "us-east-1:8b1c0e1e-0000-4000-8000-000000000000",
        "abc123",
    ],
)
def test_an_opaque_created_by_is_accepted(opaque: str):
    """Both directions, or "refuses" is true of every input and proves nothing."""
    item = record.to_item(_case(), created_at=PINNED, created_by=opaque)
    assert item["created_by"] == {"S": opaque}
    assert record.created_by_from_item(item) == opaque


def test_an_unasserted_record_says_so_rather_than_inventing_an_id():
    """The twelve seeded households were written from a fixture; no caseworker
    asserted anything about them. `NULL` is the honest value, and a placeholder
    like "system" would be a magic value a renderer could not tell from a real
    id — the same objection `lib/cases.ts` raises to a presentation dash."""
    item = record.to_item(_case(), created_at=PINNED)
    assert item["created_by"] == {"NULL": True}
    assert record.created_by_from_item(item) == ""
    # And an older row written before this field existed reads the same way.
    absent = dict(SHAPE["item"])
    del absent["created_by"]
    assert record.created_by_from_item(absent) == ""


def test_a_rebuilt_household_has_no_identity_to_leak():
    """What comes back out is as empty as what went in, and empty rather than
    invented. A placeholder like "Household c-901" would be a magic value a
    caller could not tell from real data — the same objection `lib/cases.ts`
    raises to returning a presentation dash."""
    case = record.from_item(record.to_item(_case(), created_at=PINNED))
    assert case.household.display_name == ""
    assert case.household.phone == ""
    assert case.household.household_id == ""
    # Language survives: it is a drafting preference, not an identifier, and
    # `read_case` surfaces it so outreach is written in the family's language.
    assert case.household.language == "es"


# ---------------------------------------------------------------------------
# The verdicts must not move
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", load_fixture_cases(), ids=lambda c: c.case_id)
def test_a_round_tripped_case_reaches_the_identical_verdict(case: Case):
    """Per case, against the real gate and the real pack.

    The whole risk of moving records into DynamoDB is that a household decides
    differently afterwards for a reason nobody sees. Comparing the decision and
    every reason code and detail is what makes that visible here rather than on
    a deployed sweep.
    """
    pack = load_pack(case.program, case.state)
    before = evaluate(case, TODAY, pack)
    after = evaluate(record.from_item(record.to_item(case, created_at=PINNED)), TODAY, pack)
    assert after.decision == before.decision
    assert [(r.code, r.detail) for r in after.reasons] == [
        (r.code, r.detail) for r in before.reasons
    ]


def test_the_demo_split_survives_the_round_trip():
    """Nine act, three escalate — measured through the record shape.

    The per-case test above would still pass if every case escalated for the
    same reason before and after, so the aggregate is asserted separately and
    names the three households by id.
    """
    verdicts = {}
    for case in load_fixture_cases():
        pack = load_pack(case.program, case.state)
        stored = record.from_item(record.to_item(case, created_at=PINNED))
        verdicts[case.case_id] = evaluate(stored, TODAY, pack).decision
    assert sorted(k for k, v in verdicts.items() if v == "escalate") == [
        "c-010",
        "c-011",
        "c-012",
    ]
    assert sum(1 for v in verdicts.values() if v == "act") == 9


# ---------------------------------------------------------------------------
# Fail closed — a malformed record raises rather than half-populating a Case
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "drop",
    ["case_id", "program", "state", "cert_end", "monthly_income_cents", "size"],
)
def test_a_missing_required_field_raises(drop: str):
    """No defaults, for any of them. A `cert_end` that fell back to today would
    evaluate the renewal window against a date nobody chose, and every verdict
    downstream — file, escalate, or wait — would be computed from it with no
    error anywhere."""
    item = dict(SHAPE["item"])
    del item[drop]
    with pytest.raises(record.InvalidCaseRecord, match=drop):
        record.from_item(item)


@pytest.mark.parametrize(
    "key,bad",
    [
        ("cert_end", {"S": "2026-13-45"}),
        ("cert_end", {"S": "next tuesday"}),
        ("cert_end", {"N": "20261130"}),
        ("program", {"S": "  "}),
        ("program", {"N": "42"}),
        ("state", {"NULL": True}),
        ("monthly_income_cents", {"S": "184500"}),
        ("monthly_income_cents", {"N": "184500.5"}),
        ("size", {"N": "4.0"}),
        ("size", {"N": "1E+30"}),
        ("case_id", {"S": ""}),
    ],
)
def test_a_wrongly_typed_field_raises(key: str, bad: dict):
    """Reject the type; never coerce it.

    `int("4.0")` raises rather than truncating, which is the behaviour wanted:
    a household size of 4.0 is a writer bug, and silently reading it as 4 hides
    the bug while looking correct. `1E+30` is the shape Plan 3 found reading
    back as **1** under `parseInt` — refused outright here.
    """
    item = dict(SHAPE["item"])
    item[key] = bad
    with pytest.raises(record.InvalidCaseRecord):
        record.from_item(item)


@pytest.mark.parametrize(
    "documents",
    [
        {"S": "proof_of_income"},
        {"L": [{"S": "proof_of_income"}]},
        {"L": [{"M": {"received": {"S": "2026-09-18"}}}]},
        {"L": [{"M": {"id": {"S": "proof_of_income"}}}]},
        {"L": [{"M": {"id": {"S": "proof_of_income"}, "received": {"S": "yesterday"}}}]},
        {
            "L": [
                {
                    "M": {
                        "id": {"S": "proof_of_income"},
                        "received": {"S": "2026-09-18"},
                        "expires": {"S": "soon"},
                    }
                }
            ]
        },
    ],
)
def test_a_malformed_document_list_raises(documents: dict):
    """A document the gate cannot read is a document the gate must not skip.

    `missing_document` and `stale_document` are two of the three reasons the
    demo escalates on, so a document entry silently dropped for being
    unparseable would move a household from escalate to act.
    """
    item = dict(SHAPE["item"])
    item["documents"] = documents
    with pytest.raises(record.InvalidCaseRecord):
        record.from_item(item)


@pytest.mark.parametrize(
    "conflicts",
    [{"S": "one conflict"}, {"L": [{"N": "1"}]}, {"L": [{"S": "   "}]}],
)
def test_a_malformed_conflict_list_raises(conflicts: dict):
    """`load_fixture_cases` refuses a string here for the same reason: iterating
    one yields a conflict per character, and any non-empty `source_conflicts`
    escalates — so a malformed field would escalate for twenty invented
    reasons."""
    item = dict(SHAPE["item"])
    item["source_conflicts"] = conflicts
    with pytest.raises(record.InvalidCaseRecord):
        record.from_item(item)


def test_an_oversized_document_or_conflict_list_is_refused():
    item = dict(SHAPE["item"])
    item["documents"] = {
        "L": [
            {"M": {"id": {"S": f"doc_{n}"}, "received": {"S": "2026-09-18"}}}
            for n in range(record.MAX_DOCUMENTS + 1)
        ]
    }
    with pytest.raises(record.InvalidCaseRecord, match="documents"):
        record.from_item(item)
    item = dict(SHAPE["item"])
    item["source_conflicts"] = {
        "L": [{"S": f"conflict {n}"} for n in range(record.MAX_SOURCE_CONFLICTS + 1)]
    }
    with pytest.raises(record.InvalidCaseRecord, match="source_conflicts"):
        record.from_item(item)


def test_a_reported_zero_is_a_reported_zero_and_not_an_absence():
    """Plan 1 Task 2's rule, at the storage boundary.

    A family whose income genuinely dropped to zero is the most
    eligibility-relevant case Grace will ever see, so `0` cannot double as
    "not reported". NULL and absence both mean absent; `0` means zero.
    """
    zero = record.to_item(_case(reported_income_cents=0, reported_size=0), created_at=PINNED)
    assert zero["reported_income_cents"] == {"N": "0"}
    back = record.from_item(zero)
    assert back.reported_income_cents == 0
    assert back.reported_size == 0

    nulled = dict(SHAPE["item"])
    nulled["reported_income_cents"] = {"NULL": True}
    assert record.from_item(nulled).reported_income_cents is None

    absent = dict(SHAPE["item"])
    del absent["reported_income_cents"]
    assert record.from_item(absent).reported_income_cents is None


def test_documents_and_conflicts_may_be_absent_but_never_guessed():
    item = dict(SHAPE["item"])
    del item["documents"]
    del item["source_conflicts"]
    case = record.from_item(item)
    assert case.documents == ()
    assert case.source_conflicts == ()


def test_a_naive_created_at_is_refused_rather_than_localised():
    """Same refusal as `infra/naming._utc_stamp`. A naive `astimezone()`
    silently assumes the deploy host's clock, which looks right on a UTC host
    and is wrong where this was written."""
    with pytest.raises(record.InvalidCaseRecord, match="timezone-aware"):
        record.to_item(_case(), created_at=datetime(2026, 9, 6, 12, 0, 0))


def test_a_non_utc_created_at_is_normalised_not_stored_verbatim():
    """Sort keys are not involved here, but the same bytewise-comparison hazard
    is: two records created at the same instant in different offsets must not
    read back as different times."""
    from datetime import timedelta

    east = datetime(2026, 9, 6, 17, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    item = record.to_item(_case(), created_at=east)
    assert item["created_at"] == {"S": "2026-09-06T12:00:00+00:00"}


def test_a_directory_item_refuses_an_empty_case_id():
    for bad in ["", "   ", None, 42]:
        with pytest.raises(record.InvalidCaseRecord):
            record.directory_item(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The row must not be mistaken for a different row kind
# ---------------------------------------------------------------------------


def test_a_record_row_reaches_neither_the_ledger_nor_the_escalation_queue():
    """Verified rather than assumed, as the plan asks.

    `ledger()` filters on the `LEDGER#` sort-key prefix and the escalation GSI
    is sparse on `status`/`escalated_at`. A record row that carried either would
    surface as a ledger entry with no `kind` (breaking the read) or as a phantom
    household in the caseworker's queue.
    """
    item = record.to_item(_case(), created_at=PINNED)
    assert item["sk"]["S"] == naming.RECORD_SK
    assert not item["sk"]["S"].startswith("LEDGER#")
    assert not item["sk"]["S"].startswith("ESCALATION#")
    assert not item["sk"]["S"].startswith("DECISION#")
    assert "status" not in item
    assert "escalated_at" not in item
    # And the directory row lives in its own partition, which cannot collide
    # with any household's — `readCase` in the dashboard queries `CASE#<id>`.
    directory = record.directory_item("c-901")
    assert directory["pk"]["S"] == naming.CASE_DIRECTORY_PK
    assert not directory["pk"]["S"].startswith("CASE#")
    assert "status" not in directory
