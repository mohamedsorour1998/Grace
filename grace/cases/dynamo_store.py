"""DynamoDB case store. The deployed ledger.

Behaviourally interchangeable with `InMemoryCaseStore` — that is a requirement,
not an aspiration. Task 8's trajectory evals read ledger *position* to assert
reads precede actions, and `sweep` classifies a case by scanning for a
`renewal_submitted` row, so a different ordering here would break both in a way
that reads as a gate regression. `tests/test_dynamo_store.py` parametrizes one
test body over both stores for exactly that reason.

**Household records are not stored here.** Cases come from
`fixtures/households.yaml`, the same source the local store reads. This table
holds the ledger and the escalation queue, so there is no second copy of case
data to drift and hard rule 3 (synthetic data only) needs no new enforcement
surface. It also keeps household identity out of DynamoDB, which sits outside
the Bedrock guardrail's redaction — the same reasoning as hard rule 9.

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

from grace.cases.models import Case, LedgerDetailValue, LedgerEntry
from infra import naming

# Sort-key prefix for ledger rows. Ledger and escalation rows share a partition
# key, so `ledger()` must filter on this or an escalation row — which has no
# `kind` attribute — would surface as a ledger entry and break the read.
_LEDGER_PREFIX = "LEDGER#"

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


class DynamoDBCaseStore:
    """One table, two row kinds: ledger entries and escalation rows."""

    def __init__(self, cases: list[Case], table_name: str | None = None, client=None) -> None:
        ids = [c.case_id for c in cases]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            # Same refusal as the in-memory store: keying by id would silently
            # drop a duplicate, shrinking the caseload with no error while the
            # sweep still reported success.
            raise ValueError(f"duplicate case ids: {duplicates}")
        self._cases = {c.case_id: c for c in cases}
        self._table = table_name or naming.TABLE
        self._client = client or boto3.client("dynamodb", region_name=naming.REGION)
        # Per-case monotonic sequence, making the sort key collision-proof
        # within this process. Two entries sharing a microsecond is routine —
        # one tool call writes `tool_call` then `tool_result` — and without the
        # sequence the second would overwrite the first.
        self._seq: dict[str, itertools.count] = {}

    def open_cases(self) -> list[Case]:
        return list(self._cases.values())

    def get(self, case_id: str) -> Case:
        if case_id not in self._cases:
            raise KeyError(f"No such case: {case_id}")
        return self._cases[case_id]

    def append_ledger(self, entry: LedgerEntry) -> None:
        if entry.case_id not in self._cases:
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
