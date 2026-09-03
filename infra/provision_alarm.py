"""One alarm, on the invariant rather than on errors.

Read the plan's note above this task before changing the threshold: the failure
mode this catches produces no error, no throttle, and no latency spike, so it is
invisible to every conventional alarm. `SystemErrors` / `Throttles` / p99 latency
alarms are worth having as hygiene and would not have caught a single defect
found in Plan 1.

The metric is published by a metric filter over the state machine's own logs,
counting escalated outcomes. A metric filter rather than a custom `PutMetricData`
call from inside Grace, because the count must come from what the system actually
*reported*, not from a number Grace chose to publish about itself.

Raising is the right failure mode here, unlike Grace's observability paths which
deliberately fail open: this is a provisioning script, not the request path. A
loud failure blocks a deploy and the operator re-runs, which is what idempotence
exists for.
"""

from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

from infra import naming

NAMESPACE = "Grace"
METRIC_NAME = "EscalatedCases"
FILTER_NAME = "grace-escalated-cases"

# The state machine's log group. Read from `naming` rather than rebuilt here:
# `provision_stepfunctions` creates it and configures Step Functions to write
# there, and this module builds a metric filter over it. If the two names
# disagreed the filter would match nothing and the alarm would sit on missing
# data forever, which `TreatMissingData: breaching` then reports as a permanent
# breach — a false alarm indistinguishable from a real one.
LOG_GROUP = naming.SFN_LOG_GROUP

# The event type that carries one case's final outcome exactly once.
_EVENT_TYPE = "TaskStateExited"

# **Verified against real log events with `logs:test_metric_filter`, which is
# ingestion-independent** — `filter_log_events` returns 0 for *every* pattern,
# including the empty one, for several minutes after a run (and `storedBytes`
# reads 0), so it cannot be used to check a pattern promptly.
#
# Measured match counts on the real 12-case sweep's 160 log events:
#   { $.status = "escalated" }                        0  <- the plan's draft
#   { $.details.output = "*escalated*" }             14
#   the pattern below                                 3  correct
#
# Two things the draft got wrong. There is **no top-level `status` field**: a
# Step Functions log event's keys are `type`, `details`, `execution_arn`, `id`,
# `previous_event_id`, `redrive_count`, and `event_timestamp`, and the outcome
# payload sits at `$.details.output` as an **embedded JSON string**, not a nested
# object. And without the `$.type` anchor the same outcome is counted once per
# event type the case passes through (`TaskSucceeded`, `TaskStateExited`,
# `ChoiceStateEntered`, `ChoiceStateExited`, `PassStateEntered`,
# `PassStateExited`) — which against `Threshold: 3` would make the alarm
# permanently quiet and therefore useless.
#
# The `"status":"escalated"` substring rather than bare `escalated`: the outcome's
# `reason` field is free text from the gate, so a reason mentioning the word would
# be counted as an escalation on a case that acted.
FILTER_PATTERN = (
    f'{{ $.type = "{_EVENT_TYPE}" && '
    '$.details.output = "*\\"status\\":\\"escalated\\"*" }'
)

# Which alarm fields are read back and compared after writing. Everything the
# spec declares except the fields AWS may legitimately normalize or that carry no
# safety meaning: the name is how the alarm is looked up in the first place, the
# description is prose, and `Statistic`/`Period` are asserted by the unit tests
# against `alarm_spec()` directly. Derived as a set difference rather than typed
# out, so a field added to the spec is verified without anyone remembering to
# extend a list.
VERIFIED_ALARM_FIELDS = frozenset({
    "Namespace", "MetricName", "EvaluationPeriods", "Threshold",
    "ComparisonOperator", "TreatMissingData",
})


def metric_transformations() -> list[dict]:
    """What one matching log event contributes to the metric.

    `defaultValue: 0` is what separates "the sweep ran and escalated nothing"
    from "the sweep never ran". Both breach, deliberately — but only one of them
    is a gate that got looser, and without a published zero the operator cannot
    tell which they are looking at.
    """
    return [{
        "metricName": METRIC_NAME,
        "metricNamespace": NAMESPACE,
        "metricValue": "1",
        "defaultValue": 0.0,
    }]


def alarm_spec() -> dict:
    """The alarm definition, as data so it is testable without AWS."""
    return {
        "AlarmName": naming.ALARM,
        "AlarmDescription": (
            "Fewer than three cases escalated in a sweep. The fixture set is 12 "
            "cases, 3 of which must escalate (c-010 missing document, c-011 "
            "material income change, c-012 source conflict). A lower count means "
            "the authority gate got looser — a failure that produces no error, no "
            "throttle, and no latency spike, and therefore cannot be caught by any "
            "conventional alarm."
        ),
        "Namespace": NAMESPACE,
        "MetricName": METRIC_NAME,
        # `Sum`, not `Average`: three escalations across twelve cases average
        # well below 1, so an averaging alarm would breach on a correct sweep and
        # get turned off as noise.
        "Statistic": "Sum",
        "Period": 86400,
        # The sweep runs once a day. Requiring two datapoints would delay the
        # signal by 24 hours.
        "EvaluationPeriods": 1,
        "Threshold": 3,
        "ComparisonOperator": "LessThanThreshold",
        # A sweep that never ran published no metric. Treating that as healthy
        # would hide a total failure — the same class of bug as the comparison
        # being backwards.
        "TreatMissingData": "breaching",
    }


def matches(event: dict) -> bool:
    """Whether `FILTER_PATTERN` would match one Step Functions log event.

    A local re-implementation of the two conditions the pattern expresses, so the
    tests can assert on the real event shapes offline. **Not the arbiter** — the
    authoritative check is `logs:test_metric_filter` against real events, and the
    counts it produced are recorded above. This exists so a future edit to the
    pattern that drops the `$.type` anchor or the `status` key fails a test
    rather than only a re-measurement nobody re-runs.
    """
    if event.get("type") != _EVENT_TYPE:
        return False
    output = event.get("details", {}).get("output")
    # The payload is an embedded JSON *string*; a dict here would mean the event
    # shape changed and the pattern needs re-measuring, so this is not coerced.
    return isinstance(output, str) and '"status":"escalated"' in output


def _verify_filter(logs_client) -> None:
    """Read the filter back and refuse a mismatch.

    "`put_metric_filter` returned" and "the filter matches escalated outcomes"
    are different claims, and only the second one matters — the same distinction
    `provision_dynamodb` makes about point-in-time recovery. A filter left over
    from the draft pattern matches zero events, publishes nothing, and leaves the
    alarm reading a permanent breach on missing data.
    """
    filters = logs_client.describe_metric_filters(
        logGroupName=LOG_GROUP, filterNamePrefix=FILTER_NAME
    ).get("metricFilters", [])
    live = next((f for f in filters if f.get("filterName") == FILTER_NAME), None)
    if live is None:
        raise RuntimeError(
            f"metric filter {FILTER_NAME} is absent from {LOG_GROUP} after "
            "writing it. Without it the alarm has no metric to read and sits on "
            "missing data, which reads as a permanent breach. This script is "
            "idempotent — re-run it."
        )
    if live.get("filterPattern") != FILTER_PATTERN:
        raise RuntimeError(
            f"metric filter {FILTER_NAME} reads back with a different pattern:\n"
            f"  live:     {live.get('filterPattern')!r}\n"
            f"  expected: {FILTER_PATTERN!r}\n"
            "A pattern that does not anchor on $.type and reach into "
            "$.details.output matches the wrong number of events — measured 0 and "
            "14 against a correct 3."
        )
    published = {
        (t.get("metricNamespace"), t.get("metricName"))
        for t in live.get("metricTransformations", [])
    }
    if (NAMESPACE, METRIC_NAME) not in published:
        raise RuntimeError(
            f"metric filter {FILTER_NAME} publishes to {sorted(published)}, not to "
            f"{NAMESPACE}/{METRIC_NAME} — the alarm would read a metric nothing "
            "writes to."
        )


def _verify_alarm(cw_client) -> None:
    """Read the alarm back and refuse a mismatch.

    The failure this guards is the one the whole task exists for: an alarm left
    at `GreaterThanThreshold`, or with `TreatMissingData: notBreaching`, never
    fires — and nothing anywhere reports that. It looks exactly like a system
    that never breaks.
    """
    alarms = cw_client.describe_alarms(
        AlarmNames=[naming.ALARM]
    ).get("MetricAlarms", [])
    live = next((a for a in alarms if a.get("AlarmName") == naming.ALARM), None)
    if live is None:
        raise RuntimeError(
            f"alarm {naming.ALARM} is absent after writing it. This script is "
            "idempotent — re-run it."
        )
    spec = alarm_spec()
    wrong = {
        field: (live.get(field), spec[field])
        for field in sorted(VERIFIED_ALARM_FIELDS)
        if live.get(field) != spec[field]
    }
    if wrong:
        raise RuntimeError(
            f"alarm {naming.ALARM} reads back differently from its spec "
            f"(field: live vs expected) {wrong}. An alarm on the wrong comparison "
            "or the wrong metric never fires, and that is indistinguishable from "
            "a system that never breaks."
        )


def provision(logs_client=None, cw_client=None, account_id: str | None = None) -> str:
    """Create the log group, the metric filter, and the alarm. Idempotent.

    Both writes are followed by a read-back that is the sole arbiter of success.
    """
    logs_client = logs_client or boto3.client("logs", region_name=naming.REGION)
    cw_client = cw_client or boto3.client("cloudwatch", region_name=naming.REGION)
    if account_id is None:
        account_id = boto3.client("sts").get_caller_identity()["Account"]

    try:
        logs_client.create_log_group(logGroupName=LOG_GROUP)
    except ClientError as exc:
        # `ResourceAlreadyExistsException` is the normal path: Task 8's
        # `provision_stepfunctions` creates this group first. Anything else —
        # AccessDenied, an invalid name — is a real failure, and swallowing it
        # would report success with no filter and no alarm.
        if exc.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            raise

    logs_client.put_metric_filter(
        logGroupName=LOG_GROUP,
        filterName=FILTER_NAME,
        filterPattern=FILTER_PATTERN,
        metricTransformations=metric_transformations(),
    )
    _verify_filter(logs_client)

    cw_client.put_metric_alarm(**alarm_spec())
    _verify_alarm(cw_client)

    return f"arn:aws:cloudwatch:{naming.REGION}:{account_id}:alarm:{naming.ALARM}"


if __name__ == "__main__":
    print(f"provisioned {provision()}")
