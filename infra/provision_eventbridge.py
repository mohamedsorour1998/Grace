"""Daily schedule. What makes 'runs unattended in the background' literal."""

from __future__ import annotations

import json

import boto3

from infra import naming, provision_iam, provision_stepfunctions

# 09:00 UTC. The demo triggers manually, so the exact hour is not load-bearing.
SCHEDULE = "cron(0 9 * * ? *)"

# The pinned date travels with the event: a `date.today()` anywhere in this
# system degrades the 9/3 demo from 2026-10-16, when c-002's `proof_of_income`
# goes stale, and reaches 6/6 by 2026-10-30 — measured, and pinned by
# `tests/test_demo_dates.py`. The schedule is the one caller with no human
# present to notice.
#
# **`case_ids` is deliberately absent, and its absence is the fix for a real
# defect.** This input used to carry `provision_stepfunctions.CASE_IDS` — the
# twelve fixture ids, frozen into the schedule at provisioning time. A household
# submitted through the dashboard wrote a record row and a directory entry, the
# dashboard rendered it, and the daily sweep never visited it, because the
# schedule was still naming the same twelve. Nothing failed: the execution
# reported SUCCEEDED and the 9/3 count stayed correct, which is precisely what
# made it invisible. The state machine's `ListCases` reads the directory instead,
# so the caseload is a property of the table rather than of when this script last
# ran.
SWEEP_INPUT = {"today": "2026-10-01"}


def provision(state_machine_arn: str, client=None, role_arn: str | None = None) -> str:
    """Create or update the daily rule; return its ARN."""
    client = client or boto3.client("events", region_name=naming.REGION)
    if role_arn is None:
        role_arn = provision_iam.provision()["eventbridge"]

    rule = client.put_rule(
        Name=naming.SCHEDULE_RULE,
        ScheduleExpression=SCHEDULE,
        State="ENABLED",
        Description="Grace daily recertification sweep",
        Tags=[{"Key": k, "Value": v} for k, v in naming.TAGS.items()],
    )
    client.put_targets(
        Rule=naming.SCHEDULE_RULE,
        Targets=[{
            "Id": "grace-sweep",
            "Arn": state_machine_arn,
            "RoleArn": role_arn,
            "Input": json.dumps(SWEEP_INPUT),
        }],
    )
    return str(rule["RuleArn"])
