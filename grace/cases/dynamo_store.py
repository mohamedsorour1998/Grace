"""DynamoDB case store. The deployed ledger.

Behaviourally interchangeable with `InMemoryCaseStore` — that is a requirement,
not an aspiration. Task 8's trajectory evals read ledger *position* to assert
reads precede actions, and `sweep` classifies a case by scanning for a
`renewal_submitted` row, so a different ordering here would break both in a way
that reads as a gate regression. `tests/test_dynamo_store.py` parametrizes one
test body over both stores for exactly that reason.

**Household records now live here too, and that is a Plan 4 change.** Until
Plan 4 this store read cases from the list handed to its constructor, which
`build_store()` filled from `fixtures/households.yaml` — so the deployed agent's
view of *which households exist* was fixed at container image build time and a
case submitted through the dashboard would have been invisible to it. Records
are read from the table now (`grace/cases/record.py` is the row shape), and the
constructor list survives as a **fallback seed** so the local run and every
existing test keep working unchanged.

The precedence is one-directional and deliberate: a record in the table wins,
and the seed answers only where the table holds nothing. A *read failure* is
never a fallback — it propagates, because "the table could not be read" and
"this case is not in the table" are different claims and only the second one has
a safe answer. `open_cases()` returns the union of both, so neither source can
silently shrink the caseload.

**A record carries no household identity** — no name, phone, address, or email.
That is enforced in `record.py` by never writing them rather than by filtering
them out later, so the table-wide PII scan this project runs keeps returning
nothing, and DynamoDB (which sits outside the Bedrock guardrail's redaction)
never becomes a second place a name can leak from. Hard rule 9.

**Error posture, and it differs deliberately from Task 9's.** Read failures and
ledger-write failures both propagate. An unreadable case must escalate rather
than be assumed clean (Tasks 3 and 4), and an action that happened with no audit
row is worse than a visible error — Step Functions' Catch converts either into
an escalation row. This is the *opposite* of `_current_trace_id`'s fail-open
handling, for the reason Task 9 stated: a trace ID is observability and losing it
harms nobody, while a ledger row is evidence.
"""

from __future__ import annotations

import itertools
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

from grace.cases import record
from grace.cases.models import Case, LedgerDetailValue, LedgerEntry
from infra import naming

# Sort-key prefix for ledger rows. Ledger and escalation rows share a partition
# key, so `ledger()` must filter on this or an escalation row — which has no
# `kind` attribute — would surface as a ledger entry and break the read.
_LEDGER_PREFIX = "LEDGER#"

# How many pages any Query here may take before it gives up.
#
# Refuse to spin, and **throw rather than truncate**. A DynamoDB Query caps at
# 1MB and signals more with `LastEvaluatedKey`; a service that repeats the same
# key forever would otherwise loop without bound, and Plan 1 Task 6 measured
# exactly that shape (a resume loop reaching 500 rounds before being killed).
# Truncating is the worse failure of the two: a directory read that silently
# stopped early would drop households from the sweep with no error anywhere,
# which is the one thing this system exists to prevent.
_MAX_PAGES = 100

# Prefix on each `detail` key as stored. Namespaced so a detail key can never
# collide with a structural attribute: `detail={"kind": ...}` is legal at the
# `LedgerEntry` level and would otherwise overwrite the row's own `kind`,
# silently rewriting what the audit trail says happened.
_DETAIL_PREFIX = "d_"


def to_dynamo(value: LedgerDetailValue) -> Any:
    """Convert one `LedgerDetailValue` to something DynamoDB accepts.

    `bool` is checked **before** `int` on purpose: `isinstance(True, int)` is
    True in Python, so the obvious ordering silently stores `True` as the number
    1 and the ledger reads a boolean flag back as an integer.

    `float` becomes `Decimal` because DynamoDB has no float type and boto3's
    serializer *raises* rather than coercing. Left unhandled, that raise lands
    **after** the underlying action already succeeded — the renewal filed, the
    audit row lost, which is hard rule 6 inverted. Same failure shape as a
    `Channel` returning a boto3 dict (Task 4).

    A non-finite float is refused rather than converted. `Decimal("NaN")` and
    `Decimal("Infinity")` both construct happily, and DynamoDB then rejects them
    on the wire (confirmed against the real table: `ValidationException`) — but
    the read path is the sharper reason. A stored `"Infinity"` would raise a bare
    `ValueError` out of `_from_attr` on some *later* read, so a write that looked
    successful yields an unreadable audit row. And a NaN is the same family of
    bug Plan 1's Task 1 found in the rule packs: every comparison against NaN is
    False, so a NaN silently disables whatever compares against it.
    """
    if value is None or isinstance(value, bool) or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(
                f"LedgerEntry.detail values must be finite numbers, got {value!r}"
            )
        # `str()` first: Decimal(1.1) captures binary float noise, Decimal("1.1")
        # does not.
        return Decimal(str(value))
    raise TypeError(f"LedgerEntry.detail values must be JSON-safe scalars, got {value!r}")


def _attr(value: LedgerDetailValue) -> dict[str, Any]:
    """Wrap a scalar in DynamoDB's attribute-value shape.

    The `bool` branch sits ahead of the numeric one for the same reason
    `to_dynamo` orders its checks that way — this is where the difference is
    actually observable, since `to_dynamo` returns `True` unchanged either way
    and only the emitted attribute (`BOOL` vs `N`) differs.
    """
    converted = to_dynamo(value)
    if converted is None:
        return {"NULL": True}
    if isinstance(converted, bool):
        return {"BOOL": converted}
    if isinstance(converted, str):
        return {"S": converted}
    return {"N": str(converted)}


def _from_attr(attr: dict[str, Any]) -> LedgerDetailValue:
    """Read a scalar back. `NULL` must become `None`, never the string "None" —
    Task 9 writes `trace_id: None` when tracing is off, and a reader must be
    able to tell that apart from a real value.

    The type is taken from the attribute *tag*, never guessed from the value: a
    32-hex trace ID can be all digits, and inferring from the text would turn one
    into an int and break the join to CloudWatch.
    """
    if attr.get("NULL"):
        return None
    if "BOOL" in attr:
        return bool(attr["BOOL"])
    if "S" in attr:
        return str(attr["S"])
    if "N" in attr:
        raw = str(attr["N"])
        return int(raw) if "." not in raw and "e" not in raw.lower() else float(raw)
    raise TypeError(f"unreadable ledger attribute: {attr!r}")


class CaseAlreadyExists(Exception):
    """`create_case` refused to overwrite an existing case record.

    A separate type rather than a `ClientError` the caller has to decode,
    because the two outcomes need different answers: a conflict is the caseworker
    picking an id that is taken (a 409, and nothing was written), while any other
    write failure is an infrastructure problem (a 503, and something may have
    been). Collapsing them would report a taken id as an outage.
    """


class DynamoDBCaseStore:
    """One table, four row kinds: case records, ledger entries, escalation rows,
    and the directory that enumerates the first."""

    def __init__(self, cases: list[Case], table_name: str | None = None, client=None) -> None:
        ids = [c.case_id for c in cases]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            # Same refusal as the in-memory store: keying by id would silently
            # drop a duplicate, shrinking the caseload with no error while the
            # sweep still reported success.
            raise ValueError(f"duplicate case ids: {duplicates}")
        # The fallback seed, not the caseload. Read only where the table holds
        # no record for a case — see the module docstring for why the precedence
        # runs this way and why a read *failure* is never a fallback.
        self._cases = {c.case_id: c for c in cases}
        self._table = table_name or naming.TABLE
        self._client = client or boto3.client("dynamodb", region_name=naming.REGION)
        # Per-case monotonic sequence, making the sort key collision-proof
        # within this process. Two entries sharing a microsecond is routine —
        # one tool call writes `tool_call` then `tool_result` — and without the
        # sequence the second would overwrite the first.
        self._seq: dict[str, itertools.count] = {}
        # Case ids this process has already proved exist. **Positive results
        # only.** Caching an absence would make a case created later in the same
        # process permanently invisible to `append_ledger`, which is the
        # direction that loses an audit row; caching a presence can only ever
        # save a GetItem, because `create_case` refuses to overwrite and nothing
        # in this codebase deletes a record.
        self._known: set[str] = set(self._cases)

    # -- case records ------------------------------------------------------

    def _record_item(self, case_id: str) -> dict[str, Any] | None:
        """This case's record row, or `None` if the table holds none.

        A failed read propagates rather than returning `None`. "The table could
        not be read" and "this case is not in the table" are different claims,
        and only the second one has a safe answer — an unreadable case must
        escalate, never be assumed absent and quietly answered from a stale
        in-memory seed.
        """
        response = self._client.get_item(
            TableName=self._table,
            Key={"pk": {"S": naming.case_pk(case_id)}, "sk": {"S": naming.RECORD_SK}},
        )
        return response.get("Item")

    def _directory_ids(self) -> list[str]:
        """Every case id the directory partition names, paginated and capped."""
        ids: list[str] = []
        start_key: dict[str, Any] | None = None
        for _ in range(_MAX_PAGES):
            request: dict[str, Any] = {
                "TableName": self._table,
                "KeyConditionExpression": "pk = :pk",
                "ExpressionAttributeValues": {":pk": {"S": naming.CASE_DIRECTORY_PK}},
                "ScanIndexForward": True,
            }
            if start_key is not None:
                request["ExclusiveStartKey"] = start_key
            response = self._client.query(**request)
            for item in response.get("Items", []):
                ids.append(record.case_id_from_directory_item(item))
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                return ids
        raise RuntimeError(
            f"the case directory did not finish within {_MAX_PAGES} pages; "
            "refusing to truncate the caseload"
        )

    def open_cases(self) -> list[Case]:
        """Every case, from the table and the fallback seed, table first.

        A **union**, so neither source can shrink the caseload: a household in
        the seed but not yet seeded into the table is still swept, and a case
        submitted through the dashboard is swept even though no image was
        rebuilt. Sorted by case id so the order is a property of the data rather
        than of whichever page DynamoDB returned first — `InMemoryCaseStore`
        returns fixture order, which for the fixtures is the same order.

        A directory entry whose record row is missing **raises**. That is the
        loud direction on purpose: skipping it would quietly drop a household
        from the sweep while every count still looked plausible, which is
        exactly the failure the duplicate-id guard above already refuses.
        """
        by_id: dict[str, Case] = dict(self._cases)
        for case_id in self._directory_ids():
            item = self._record_item(case_id)
            if item is None:
                raise record.InvalidCaseRecord(
                    f"the case directory names {case_id!r} but the table holds no "
                    f"{naming.RECORD_SK} row for it"
                )
            by_id[case_id] = record.from_item(item)
        self._known.update(by_id)
        return [by_id[case_id] for case_id in sorted(by_id)]

    def get(self, case_id: str) -> Case:
        item = self._record_item(case_id)
        if item is not None:
            case = record.from_item(item)
            self._known.add(case_id)
            return case
        if case_id in self._cases:
            return self._cases[case_id]
        raise KeyError(f"No such case: {case_id}")

    def create_case(self, case: Case, *, created_by: str = "") -> None:
        """Write a new case record. Refuses to overwrite an existing one.

        `created_by` is the opaque id of whoever asserted this record's contents
        — a caseworker's Cognito `sub`, or `""` when the case came from
        `fixtures/households.yaml` and nobody asserted anything. It is a
        parameter rather than omitted because `record.to_item` writes the field
        either way: a writer that *could not* express it would guarantee the
        Python path always wrote `NULL`, which is the two-writers-one-shape drift
        `record.py` exists to prevent. `record.to_item` refuses a value that is
        not opaque (hard rule 9).

        `attribute_not_exists(sk)` is the whole guard. An intake that silently
        overwrote a household would destroy a case record whose ledger and
        escalation history stay in the same partition — that history would then
        belong to two different families, and nothing in the audit trail would
        say so.

        **The directory row is written first, and the order is the point.** Two
        puts cannot be made atomic without `TransactWriteItems`, which needs
        permissions neither the runtime role nor the dashboard's compute role
        holds today, so one of the two partial failures has to be chosen. Record
        first would leave a case the table can answer `get()` for but that
        `open_cases()` never lists — a household silently absent from the sweep.
        Directory first leaves the opposite: an id `open_cases()` names and
        cannot load, which raises with the case id in the message. A visible
        failure beats a silent omission, and the retry is safe because the
        directory write is idempotent.
        """
        self._client.put_item(TableName=self._table, Item=record.directory_item(case.case_id))
        try:
            self._client.put_item(
                TableName=self._table,
                Item=record.to_item(case, created_by=created_by),
                ConditionExpression="attribute_not_exists(sk)",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise CaseAlreadyExists(
                    f"a case record already exists for {case.case_id!r}"
                ) from exc
            raise
        self._known.add(case.case_id)

    # -- ledger ------------------------------------------------------------

    def _exists(self, case_id: str) -> bool:
        """Whether this case is one this store knows about.

        Checked against the table rather than against the constructor seed
        alone, or a case created after this process started could never have a
        ledger row written for it — the audit trail would be empty for exactly
        the household that most needs one. Positive answers are cached; see
        `_known`.
        """
        if case_id in self._known:
            return True
        if self._record_item(case_id) is None:
            return False
        self._known.add(case_id)
        return True

    def append_ledger(self, entry: LedgerEntry) -> None:
        if not self._exists(entry.case_id):
            # A ledger row for an unknown case is a typo at the call site, not a
            # new case. Failing loudly beats opening a phantom bucket that
            # `ledger()` would later report as an innocent empty list.
            raise KeyError(f"Cannot append ledger entry for unknown case: {entry.case_id}")
        seq = next(self._seq.setdefault(entry.case_id, itertools.count(1)))
        item = {
            "pk": {"S": naming.case_pk(entry.case_id)},
            "sk": {"S": naming.ledger_sk(entry.at, seq)},
            "case_id": {"S": entry.case_id},
            "at": {"S": entry.at.isoformat()},
            "kind": {"S": entry.kind},
        }
        for key, value in entry.detail.items():
            item[f"{_DETAIL_PREFIX}{key}"] = _attr(value)
        self._client.put_item(TableName=self._table, Item=item)

    def ledger(self, case_id: str) -> list[LedgerEntry]:
        """Every ledger row for one case, in append order.

        **Paginated, and that is not premature.** A Query returns at most 1MB
        and signals the rest via `LastEvaluatedKey`; a single-call read would
        *silently truncate the audit trail* on a long-running case. The rows lost
        would be the newest ones, which is where `renewal_submitted` lives — so
        `sweep` would classify a filed renewal as unfiled with no error anywhere.
        Same class of failure as the sort-key ordering bug in Task 1: correct on
        small inputs, wrong later, and invisible either way.
        """
        entries: list[LedgerEntry] = []
        start_key: dict[str, Any] | None = None
        while True:
            request: dict[str, Any] = {
                "TableName": self._table,
                "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefix)",
                "ExpressionAttributeValues": {
                    ":pk": {"S": naming.case_pk(case_id)},
                    ":prefix": {"S": _LEDGER_PREFIX},
                },
                # Chronological, matching `InMemoryCaseStore`'s append order. The
                # evals read position, so this is load-bearing.
                "ScanIndexForward": True,
            }
            if start_key is not None:
                request["ExclusiveStartKey"] = start_key
            response = self._client.query(**request)
            for item in response.get("Items", []):
                detail = {
                    key[len(_DETAIL_PREFIX) :]: _from_attr(value)
                    for key, value in item.items()
                    if key.startswith(_DETAIL_PREFIX)
                }
                entries.append(
                    LedgerEntry(
                        case_id=str(item["case_id"]["S"]),
                        at=datetime.fromisoformat(str(item["at"]["S"])),
                        kind=str(item["kind"]["S"]),
                        detail=detail,
                    )
                )
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                return entries

    def write_escalation(self, case_id: str, reason: str, question: str, deadline: str) -> None:
        """Record that this case is waiting for a human.

        `status` and `escalated_at` are the escalation-queue GSI's keys, and only
        these rows carry them — so the index is a sparse queue rather than a
        filtered scan over every ledger row. Plan 3's dashboard reads it
        directly.
        """
        at = datetime.now(timezone.utc)
        self._client.put_item(
            TableName=self._table,
            Item={
                "pk": {"S": naming.case_pk(case_id)},
                "sk": {"S": naming.escalation_sk(at)},
                "case_id": {"S": case_id},
                "status": {"S": naming.PENDING},
                "escalated_at": {"S": at.isoformat()},
                "reason": {"S": reason},
                "question": {"S": question},
                "deadline": {"S": deadline},
            },
        )
