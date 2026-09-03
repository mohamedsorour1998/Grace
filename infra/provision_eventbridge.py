"""Daily schedule. What makes 'runs unattended in the background' literal."""

from __future__ import annotations

import json

import boto3

from infra import naming, provision_iam, provision_stepfunctions

# 09:00 UTC. The demo triggers manually, so the exact hour is not load-bearing.
SCHEDULE = "cron(0 9 * * ? *)"

# The pinned date travels with the event: a `date.today()` anywhere in this
# system turns the 9/3 demo into 8/4 from 2026-10-31, and the schedule is the one
# caller with no human present to notice.
SWEEP_INPUT = {
    "case_ids": provision_stepfunctions.CASE_IDS,
    "today": "2026-10-01",
}


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
