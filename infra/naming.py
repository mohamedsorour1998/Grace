"""Every Grace resource name and key shape, in one place.

Two reasons this is a module rather than string literals at each call site:
a rename is a one-file change, and `tests/test_infra_naming.py` can assert the
shapes — a typo in a resource name fails a test instead of a deploy.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

REGION = os.getenv("AWS_REGION", "us-east-1")
ACCOUNT_ID = os.getenv("AWS_ACCOUNT_ID")

TABLE = "grace-cases"
ESCALATION_GSI = "escalation-queue"
RUNTIME = "grace"
MEMORY = "grace_household_memory"
LAMBDA = "grace-invoke-case"
STATE_MACHINE = "grace-sweep"
SCHEDULE_RULE = "grace-daily-sweep"
ALARM = "grace-escalations-below-expected"

# The state machine's CloudWatch log group. Named here, not rebuilt at each use,
# because two tasks must agree on it: `provision_stepfunctions` creates it and
# points Step Functions logging at it, and `provision_alarm` builds the
# escalation metric filter over it. If they disagreed the filter would match
# nothing, and `TreatMissingData: breaching` would report that as a permanent
# breach — a false alarm indistinguishable from a real one.
SFN_LOG_GROUP = f"/aws/vendedlogs/states/{STATE_MACHINE}-Logs"

# Tagged at creation so Grace's spend is separable in Cost Explorer against a
# $50 credit budget, and so teardown can identify what it owns.
TAGS = {"Project": "Grace", "Environment": "dev"}

# The escalation-queue GSI's partition key value. One value, so the GSI is a
# queue rather than a scan.
PENDING = "PENDING_CASEWORKER"


def case_pk(case_id: str) -> str:
    """The partition key for one case's rows.

    Carries the case id only — never a household name, phone, or address. Same
    rule as span attributes and the JWT `sub` (hard rule 9): this key appears in
    CloudWatch metrics, DynamoDB Streams, and anything that reads the table.
    """
    return f"CASE#{case_id}"


def _utc_stamp(at: datetime, builder: str) -> str:
    """The timestamp component of a sort key: UTC, ISO-8601, aware only.

    Two separate guards, in this order, and the order is load-bearing.

    **Naive is refused, not converted.** A naive `datetime.astimezone()`
    silently assumes the *local* system clock, so converting instead of raising
    would turn a caller's mistake into a timestamp that is wrong by the deploy
    host's offset — and correct-looking on a UTC host, which is where this runs
    in production and not where it is written. `LedgerEntry` already refuses a
    naive `at` at construction; this keeps the key builders from becoming the
    place one sneaks back in.

    **Aware is not the same claim as UTC.** `LedgerEntry` rejects naive and
    nothing more, so an aware datetime at *any* offset reaches here, and
    `isoformat()` renders that offset verbatim. DynamoDB compares a sort key
    bytewise: `2026-10-01T08:00:00-05:00` is one hour *after*
    `2026-10-01T12:00:00+00:00` in real time, but sorts before it as a string.
    That would silently invert `ScanIndexForward=True` — the ordering the
    trajectory evals read to assert that reads precede actions — with no error
    anywhere. Normalizing to UTC first makes every key comparable, and also
    means one instant has exactly one key however the caller expressed it.
    """
    if at.tzinfo is None or at.utcoffset() is None:
        # `utcoffset() is None` is the second half of Python's own definition of
        # naive: a `tzinfo` subclass may be attached and still return no offset,
        # in which case `isoformat()` emits no offset and sorts against UTC keys
        # as though it were an hours-off local time.
        raise ValueError(f"{builder} requires a timezone-aware datetime")
    return at.astimezone(timezone.utc).isoformat()


def ledger_sk(at: datetime, seq: int) -> str:
    """Sort key for one ledger entry.

    ISO-8601 **in UTC** plus a **zero-padded** sequence, because DynamoDB sorts
    the sort key lexically: an unpadded sequence puts 10 before 9, a non-UTC
    offset puts a later instant before an earlier one, and the trajectory evals
    read ledger *position* to assert that reads precede actions.

    The sequence is what makes collisions impossible. `LedgerEntry.at` is
    `datetime.now(timezone.utc)` and one tool call writes `tool_call` then
    `tool_result` back to back; two rows sharing a microsecond would collide and
    one would silently overwrite the other, losing an audit row — the one thing
    this table exists not to do.
    """
    return f"LEDGER#{_utc_stamp(at, 'ledger_sk')}#{seq:06d}"


def escalation_sk(at: datetime) -> str:
    """Sort key for a pending-caseworker row. One escalation per case per
    moment, so no sequence is needed."""
    return f"ESCALATION#{_utc_stamp(at, 'escalation_sk')}"
