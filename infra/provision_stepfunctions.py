"""The `grace-sweep` state machine: a Map over the twelve cases.

The Catch branch is the fail-closed rule expressed as infrastructure — see the
note above this task in the plan.

**And so is the Choice branch, which the plan's draft did not have.** `Catch`
fires when a task *fails*; it cannot see a task that succeeds while reporting
`{"status": "error"}`. Both `grace/entrypoint.py` and `infra/lambda_src/handler.py`
are deliberately written never to raise, so that reported-error shape is the
*normal* way a failure arrives here — which made the two paths opposite: a Lambda
killed at its deadline got an escalation row, and a Lambda that reported the same
failure politely got none. The family that disappeared was the one whose failure
was handled better. `CheckOutcome` closes that.
"""

from __future__ import annotations

import json

import boto3
from botocore.exceptions import ClientError

from infra import naming, provision_iam

# The twelve fixture cases. Listed rather than discovered because the state
# machine is provisioned from outside the Grace package, and because the demo's
# claim is about these twelve specifically.
CASE_IDS = [f"c-{n:03d}" for n in range(1, 13)]

# What a caseworker reads when the automated run produced no verdict at all.
_NO_VERDICT_REASON = (
    "The automated run failed before reaching a verdict. A caseworker must "
    "review this case."
)
_NO_VERDICT_QUESTION = (
    "Why did this case fail, and does the household still qualify?"
)


def _escalation_row(state_name: str) -> dict:
    """One escalation row, in DynamoDB's wire format.

    Shared by both failure branches so they cannot drift: `status` and
    `escalated_at` are the escalation-queue GSI's own key attributes, and a row
    missing either is invisible to Plan 3's dashboard while still existing in the
    table. Carries `case_id` and nothing that identifies the household — hard
    rule 9 applies to this row as much as to a span attribute.

    `escalated_at` uses `$$.State.EnteredTime`, which renders UTC as `...Z` while
    `grace/cases/dynamo_store.py` writes `...+00:00` for the same instant. Both
    sort correctly among rows written by the same writer, and the caseworker
    queue's meaningful order is by `deadline`, so this is recorded as a known
    cosmetic inconsistency rather than reformatted inside a `States.Format`
    intrinsic.
    """
    return {
        "Type": "Task",
        "Resource": "arn:aws:states:::dynamodb:putItem",
        "Parameters": {
            "TableName": naming.TABLE,
            "Item": {
                "pk": {"S.$": "States.Format('CASE#{}', $.case_id)"},
                "sk": {"S.$": f"States.Format('ESCALATION#{{}}', $$.State.EnteredTime)"},
                "case_id": {"S.$": "$.case_id"},
                "status": {"S": naming.PENDING},
                "escalated_at": {"S.$": "$$.State.EnteredTime"},
                "reason": {"S": _NO_VERDICT_REASON},
                "question": {"S": _NO_VERDICT_QUESTION},
                "deadline": {"S": ""},
            },
        },
        "ResultPath": None,
        "Next": state_name,
    }


def definition(account_id: str, lambda_arn: str) -> dict:
    """Build the state machine definition."""
    return {
        "Comment": "Grace daily sweep: one runtime invocation per household",
        "StartAt": "SweepCases",
        "States": {
            "SweepCases": {
                "Type": "Map",
                "ItemsPath": "$.case_ids",
                # Bounded on purpose: see the plan's note. Twelve at once
                # invites the throttling the Retry below then has to absorb.
                "MaxConcurrency": 3,
                "ItemSelector": {
                    "case_id.$": "$$.Map.Item.Value",
                    "today.$": "$.today",
                },
                "ItemProcessor": {
                    "ProcessorConfig": {"Mode": "INLINE"},
                    "StartAt": "InvokeCase",
                    "States": {
                        "InvokeCase": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::lambda:invoke",
                            "Parameters": {
                                "FunctionName": lambda_arn,
                                "Payload.$": "$",
                            },
                            "OutputPath": "$.Payload",
                            "Retry": [{
                                "ErrorEquals": [
                                    "Lambda.TooManyRequestsException",
                                    "Lambda.ServiceException",
                                    "ThrottlingException",
                                    "States.TaskFailed",
                                ],
                                "IntervalSeconds": 5,
                                "MaxAttempts": 2,
                                "BackoffRate": 2.0,
                            }],
                            "Catch": [{
                                "ErrorEquals": ["States.ALL"],
                                "ResultPath": "$.error",
                                "Next": "RecordEscalation",
                            }],
                            # Not `End`: the outcome still has to be inspected.
                            # An `"End": True` here would leave CheckOutcome
                            # unreachable while every assertion about its shape
                            # still passed.
                            "Next": "CheckOutcome",
                        },
                        "CheckOutcome": {
                            # `Catch` above cannot see a task that *succeeded*
                            # while reporting an error, and that is how a
                            # failure normally arrives: the handler and the
                            # entrypoint are both written never to raise.
                            "Type": "Choice",
                            "Choices": [{
                                "Variable": "$.status",
                                "StringEquals": "error",
                                "Next": "RecordReportedFailure",
                            }],
                            "Default": "ReportOutcome",
                        },
                        "RecordEscalation": _escalation_row("ReportFailure"),
                        # A second writer rather than one shared state, because
                        # the two arrive with different input shapes: the Catch
                        # path carries `$.error` and the reported path carries
                        # the handler's own `detail`. The row itself is built by
                        # one function, so the shape cannot drift.
                        "RecordReportedFailure": _escalation_row("ReportFailure"),
                        "ReportFailure": {
                            "Type": "Pass",
                            "Parameters": {
                                "status": "error",
                                "case_id.$": "$.case_id",
                                "detail": "run failed; escalation recorded",
                            },
                            "End": True,
                        },
                        "ReportOutcome": {
                            # Passes the runtime's own outcome through unchanged.
                            # `Counter(o['status'])` over the Map's output is the
                            # demo's 9/3 claim, so this must not reshape it.
                            "Type": "Pass",
                            "End": True,
                        },
                    },
                },
                "End": True,
            }
        },
    }


def provision(lambda_arn: str, client=None, role_arn: str | None = None,
              account_id: str | None = None) -> str:
    """Create or update the state machine; return its ARN.

    **`loggingConfiguration` is not optional here.** Step Functions logging
    defaults to `OFF`, and Task 9's alarm counts escalations from a metric filter
    over this state machine's log group — with logging off, no log events exist,
    the filter matches nothing, and the alarm sits on missing data forever. The
    two tasks have to agree about the log group name, so it comes from
    `naming.SFN_LOG_GROUP`.

    `includeExecutionData=True` is what puts the per-case outcome payload
    (`{"status": "escalated", ...}`) into the log event, which is what the filter
    pattern matches on. Without it the events carry state transitions only.
    Household identity is not in that payload — the outcome carries `case_id`,
    `reason`, and `deadline`, never a name, phone, or address (hard rule 9).
    """
    client = client or boto3.client("stepfunctions", region_name=naming.REGION)
    if account_id is None:
        account_id = boto3.client("sts").get_caller_identity()["Account"]
    if role_arn is None:
        role_arn = provision_iam.provision()["stepfunctions"]

    logs = boto3.client("logs", region_name=naming.REGION)
    try:
        logs.create_log_group(logGroupName=naming.SFN_LOG_GROUP)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    log_group_arn = (
        f"arn:aws:logs:{naming.REGION}:{account_id}:log-group:{naming.SFN_LOG_GROUP}:*"
    )
    logging_configuration = {
        "level": "ALL",
        "includeExecutionData": True,
        "destinations": [{"cloudWatchLogsLogGroup": {"logGroupArn": log_group_arn}}],
    }

    body = json.dumps(definition(account_id, lambda_arn))
    arn = f"arn:aws:states:{naming.REGION}:{account_id}:stateMachine:{naming.STATE_MACHINE}"
    try:
        response = client.create_state_machine(
            name=naming.STATE_MACHINE, definition=body, roleArn=role_arn,
            loggingConfiguration=logging_configuration,
            tags=[{"key": k, "value": v} for k, v in naming.TAGS.items()],
        )
        return str(response["stateMachineArn"])
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "StateMachineAlreadyExists":
            raise
        client.update_state_machine(
            stateMachineArn=arn, definition=body, roleArn=role_arn,
            loggingConfiguration=logging_configuration,
        )
        return arn
