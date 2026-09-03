"""The four execution roles. Each scoped to what it actually needs.

The security-relevant content of this file is one `Deny` statement — see
`DENY_SID` and Appendix D.1. Everything else is ordinary least privilege.

Idempotent by design: `provision()` is the recovery path when a deploy fails
halfway, and re-running it *converges* on the policies below rather than leaving
whatever a previous version wrote. That distinction matters — an earlier deploy's
weaker trust policy surviving a re-run is the same "control looks present and is
absent" failure the DynamoDB point-in-time-recovery bug had.

Raising is the right failure mode here, unlike Grace's observability paths which
deliberately fail open: this is a provisioning script, not the request path. A
loud failure blocks a deploy and the operator re-runs.
"""

from __future__ import annotations

import json

import boto3
from botocore.exceptions import ClientError

from infra import naming

DENY_SID = "DenyUnverifiedUserIdPath"

# The three profiles from grace/models.py. Listed as ARNs rather than a wildcard
# so hard rule 1 (Amazon Nova only) is enforced by IAM as well as by the
# model-id test that walks `grace/`.
#
# Not imported from `grace.models` on purpose: `infra/` provisions
# infrastructure and must stay importable without Grace's dependencies (the same
# reason `infra/lambda_src/handler.py` is outside the package). `grace.models`
# imports `strands`. The duplication is covered by a test asserting these are
# real, current Nova ids rather than by an import.
_NOVA_PROFILES = (
    "global.amazon.nova-2-lite-v1:0",
    "us.amazon.nova-pro-v1:0",
    "us.amazon.nova-micro-v1:0",
)

# Which AWS service may assume each role. Public because the tests assert the
# mapping, and because a trust policy naming a second principal is how a role
# intended for one service quietly becomes assumable by another.
TRUST_PRINCIPALS = {
    "runtime": "bedrock-agentcore.amazonaws.com",
    "lambda": "lambda.amazonaws.com",
    "stepfunctions": "states.amazonaws.com",
    "eventbridge": "events.amazonaws.com",
}


def role_name(purpose: str) -> str:
    """The IAM role name for one purpose.

    One function rather than an f-string at each call site: `provision_all` and
    the runbook both look roles up by purpose, and a rename reflected in only
    one of them orphans a role nobody notices.
    """
    if purpose not in TRUST_PRINCIPALS:
        raise KeyError(f"Unknown Grace role purpose: {purpose!r}")
    return f"grace-{purpose}-role"


def policy_name(purpose: str) -> str:
    """The inline policy name for one purpose.

    Inline, never managed. `BedrockAgentCoreFullAccess` grants
    `GetWorkloadAccessTokenForUserId` — the action `DENY_SID` exists to block —
    and the docs mark it development-only, so nothing here calls
    `attach_role_policy` at all.
    """
    if purpose not in TRUST_PRINCIPALS:
        raise KeyError(f"Unknown Grace role purpose: {purpose!r}")
    return f"grace-{purpose}-policy"


def trust_policy(purpose: str, account_id: str) -> dict:
    """Trust policy with a source-account condition.

    The condition is what stops a confused-deputy call from another account
    assuming this role. The ARN-level condition the docs also recommend needs the
    resource ARN, which does not exist before creation — so account scoping is
    applied now and the ARN condition is a documented follow-up in the runbook.

    **Applied to all four services, including Lambda, and that was worth
    checking.** AWS documents this condition in the trust policy for AgentCore
    Runtime (which says the trust policy *must* carry it), Step Functions, and
    EventBridge, but never for Lambda — and a condition on a key the calling
    service does not populate evaluates false, which would leave the role
    permanently unassumable. Verified empirically rather than assumed: a
    throwaway Lambda whose role carried `aws:SourceAccount` for a *different*
    account was refused with "The role defined for the function cannot be
    assumed by Lambda", and the identical call succeeded once the value was
    corrected. So Lambda does populate the key, and the condition is genuinely
    evaluated rather than silently ignored. Both halves of that probe matter: the
    success alone would not have distinguished "condition satisfied" from
    "condition ignored".
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": TRUST_PRINCIPALS[purpose]},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": account_id}},
            }
        ],
    }


def runtime_policy(account_id: str) -> dict:
    """What the deployed runtime may do.

    Read the `Deny` first: `GetWorkloadAccessTokenForUserId` performs no
    verification of the user id it is handed, so it would let an authenticated
    caseworker obtain a token scoped to any household (Appendix D.1). An explicit
    Deny beats any Allow, including one attached later by a future task or a
    managed policy — which is why this is here even though Identity is deferred.

    Note what is *absent*: no `Allow` anywhere grants that action, so the Deny is
    not merely overriding a grant this file makes. That matters because AWS's own
    documented AgentCore execution-role example grants all three token actions
    together, so copying it in later is the realistic way the unsafe path comes
    back — and the Deny is what makes that copy harmless.
    """
    region = naming.REGION
    directory = (
        f"arn:aws:bedrock-agentcore:{region}:{account_id}:"
        "workload-identity-directory/default"
    )
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": DENY_SID,
                "Effect": "Deny",
                "Action": "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                # Both the directory and the workload identities nested beneath
                # it. Verified against the service reference: these actions are
                # authorized against `workload-identity-directory` *and* the
                # nested `workload-identity` resource type, and Appendix D.2
                # confirmed live that Runtime creates its identity at
                # `.../default/workload-identity/<runtime_name>-<suffix>`. A Deny
                # naming only the directory would leave the nested ARN
                # unprotected — the resource shape the request actually carries.
                "Resource": [directory, f"{directory}/workload-identity/*"],
            },
            {
                "Sid": "NovaOnly",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": [
                    f"arn:aws:bedrock:{region}:{account_id}:inference-profile/{p}"
                    for p in _NOVA_PROFILES
                ]
                + [
                    # The inference profile fans out to foundation models; both
                    # sides of that indirection need naming, and both stay Nova.
                    #
                    # The wildcard region is deliberate, not lazy. Verified
                    # against the live profiles: `us.amazon.nova-pro-v1:0` fans
                    # out to us-east-1, us-west-2 AND us-east-2, and
                    # `global.amazon.nova-2-lite-v1:0` fans out to
                    # `arn:aws:bedrock:::foundation-model/...` with an *empty*
                    # region field. Pinning `us-east-1` would match neither, and
                    # the symptom would be an AccessDenied at model-call time
                    # rather than a failing test. Every ARN still names one
                    # specific Nova model, so hard rule 1 holds.
                    f"arn:aws:bedrock:*::foundation-model/{p.split('.', 1)[-1]}"
                    for p in _NOVA_PROFILES
                ],
            },
            {
                "Sid": "LedgerTableOnly",
                "Effect": "Allow",
                # `DynamoDBCaseStore` calls `put_item` and `query` and nothing
                # else. No `DeleteItem` and no `DeleteTable`: the ledger is the
                # audit trail for every autonomous benefits decision, and an
                # agent able to delete a row could erase the
                # `renewal_submitted` evidence hard rule 6 depends on. Append
                # and read only.
                "Action": [
                    "dynamodb:PutItem",
                    "dynamodb:GetItem",
                    "dynamodb:Query",
                ],
                "Resource": [
                    f"arn:aws:dynamodb:{region}:{account_id}:table/{naming.TABLE}",
                    f"arn:aws:dynamodb:{region}:{account_id}:table/{naming.TABLE}/index/*",
                ],
            },
            {
                "Sid": "HouseholdMemory",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                    "bedrock-agentcore:ListMemoryRecords",
                ],
                # Scoped to a resource type rather than `*`: these actions treat
                # the `memory` resource as optional, which means they would also
                # accept `Resource: "*"`. The memory id is not known until
                # `provision_memory` runs, so this is the tightest shape
                # available at role-creation time.
                "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:memory/*",
            },
            {
                "Sid": "OwnLogs",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:/aws/*",
            },
        ],
    }


def _lambda_policy(account_id: str) -> dict:
    region = naming.REGION
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeTheGraceRuntimeOnly",
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:runtime/{naming.RUNTIME}*",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:runtime/{naming.RUNTIME}*/*",
                ],
            },
            {
                "Sid": "OwnLogs",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                # Scoped to log groups rather than every `logs:` resource in the
                # account. The plan's draft used a bare `:*` suffix here, which
                # also covers destinations and resource policies.
                "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:/aws/lambda/{naming.LAMBDA}*",
            },
        ],
    }


def _stepfunctions_policy(account_id: str) -> dict:
    region = naming.REGION
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeTheCaseLambdaOnly",
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": f"arn:aws:lambda:{region}:{account_id}:function:{naming.LAMBDA}",
            },
            {
                # The Catch branch writes the escalation row. Without this, a
                # failed case would fail *silently* at the point whose whole
                # purpose is not losing it.
                "Sid": "WriteEscalationRows",
                "Effect": "Allow",
                "Action": "dynamodb:PutItem",
                "Resource": f"arn:aws:dynamodb:{region}:{account_id}:table/{naming.TABLE}",
            },
        ],
    }


def _eventbridge_policy(account_id: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "StartTheSweepOnly",
                "Effect": "Allow",
                "Action": "states:StartExecution",
                "Resource": (
                    f"arn:aws:states:{naming.REGION}:{account_id}:"
                    f"stateMachine:{naming.STATE_MACHINE}"
                ),
            }
        ],
    }


# Public so the tests can iterate every role rather than a list someone
# remembered to keep in sync — a fifth role added here is covered by the shape
# assertions automatically. Same discovery-from-source discipline as Task 4's
# model-id guard and Task 9's ledger-writer guard.
POLICY_BUILDERS = {
    "runtime": runtime_policy,
    "lambda": _lambda_policy,
    "stepfunctions": _stepfunctions_policy,
    "eventbridge": _eventbridge_policy,
}


def provision(client=None, account_id: str | None = None) -> dict[str, str]:
    """Create or update the four roles; return purpose → ARN. Idempotent."""
    client = client or boto3.client("iam")
    if account_id is None:
        account_id = boto3.client("sts").get_caller_identity()["Account"]

    arns: dict[str, str] = {}
    for purpose, build in POLICY_BUILDERS.items():
        name = role_name(purpose)
        trust = json.dumps(trust_policy(purpose, account_id))
        try:
            client.create_role(
                RoleName=name,
                AssumeRolePolicyDocument=trust,
                Description=f"Grace {purpose} execution role",
                Tags=[{"Key": k, "Value": v} for k, v in naming.TAGS.items()],
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "EntityAlreadyExists":
                # Anything else — AccessDenied, a malformed document — is a real
                # failure. Swallowing it would report success with the role
                # absent or misconfigured.
                raise
            # Re-running must converge on the intended trust policy rather than
            # leaving whatever a previous version wrote. A role created before
            # the source-account condition existed would otherwise keep its
            # weaker policy forever, and nothing in the output would say so.
            client.update_assume_role_policy(RoleName=name, PolicyDocument=trust)
        # `put_role_policy` overwrites, so this is also how the Deny reaches a
        # role that already existed: re-run the script, do not delete the role.
        client.put_role_policy(
            RoleName=name,
            PolicyName=policy_name(purpose),
            PolicyDocument=json.dumps(build(account_id)),
        )
        arns[purpose] = client.get_role(RoleName=name)["Role"]["Arn"]
    return arns


if __name__ == "__main__":
    for _purpose, _arn in provision().items():
        print(f"{_purpose}: {_arn}")
