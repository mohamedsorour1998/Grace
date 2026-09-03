"""Delete what `provision_all` created. Grace resources only.

Every deletion passes through `is_grace_resource` first, and that is a **guard,
not a convention**: this account holds `theagentorg-*`, `rosettacloud-*`,
`rosettaclaw_*`, and `bughunt-main`, and a teardown that matched anything looser
would delete another project's work. Unlike every other failure in this codebase,
that one is not recoverable by re-running a script.

The DynamoDB table is **not** deleted by default: it holds the audit trail for
every autonomous benefits decision, and `--include-table` must be passed
explicitly.

The AgentCore runtime and memory are not deleted here at all. Both are owned by
`agentcore deploy` and a deliberate CLI call, and deleting Memory would discard
every household's cross-cycle facts as a side effect of tearing down a state
machine.

"Already gone" is success; "you may not do that" is not. Only the not-found error
codes are swallowed — a bare `except Exception` would report a clean teardown with
the resources still present and still costing money.
"""

from __future__ import annotations

import argparse

import boto3
from botocore.exceptions import ClientError

from infra import naming, provision_alarm, provision_iam

# The prefix every Grace resource shares. The trailing separator is the whole
# point: `startswith("grace")` also matches `graceful-degradation-lambda` and the
# bare name `grace`, neither of which this project owns. Underscore as well as
# hyphen because AgentCore resource names reject hyphens (`grace_household_memory`
# was renamed for exactly that reason), so both spellings are legitimately Grace's.
_PREFIXES = ("grace-", "grace_")

# A log group name is a path, so the prefix sits after the last `/`. Only the
# one root teardown actually passes through the guard is listed: the metric filter
# it deletes lives on `naming.SFN_LOG_GROUP`. Extra roots here would be untested
# configuration inside a safety guard, which is the thing this codebase keeps
# finding — a control that looks present and is not exercised.
_LOG_GROUP_ROOTS = ("/aws/vendedlogs/states/",)

# Error codes that mean the resource is already gone. Everything else re-raises.
_ALREADY_GONE = frozenset({
    "ResourceNotFoundException",
    "ResourceNotFound",
    "StateMachineDoesNotExist",
    "NoSuchEntity",
    "NoSuchEntityException",
    "ValidationError",
})


def is_grace_resource(name: str) -> bool:
    """Whether this name belongs to Grace.

    Called before every deletion. Kept as one function so there is a single place
    to audit, and so `tests/test_infra_alarm.py` can assert it against the real
    neighbouring names in this account — including the near-misses
    (`graceful-degradation-lambda`, `not-grace-cases`, and a bare `grace`) that a
    naive `startswith("grace")` accepts.
    """
    if not isinstance(name, str) or not name:
        return False
    candidate = name
    for root in _LOG_GROUP_ROOTS:
        if candidate.startswith(root):
            # Compare the leaf, not the path: `/aws/vendedlogs/states/grace-...`
            # is Grace's, and the shared root prefix is not evidence either way.
            candidate = candidate[len(root):]
            break
    return candidate.startswith(_PREFIXES)


def _guard(name: str) -> str:
    """Refuse to name a resource that is not Grace's.

    A `raise`, not a skip: a teardown that silently declined to delete something
    would look identical to one that deleted it, and if this ever fires the bug
    is in the caller rather than in the account.
    """
    if not is_grace_resource(name):
        raise RuntimeError(
            f"refusing to delete {name!r}: teardown only ever touches Grace "
            "resources, and this account holds unrelated projects."
        )
    return name


def _ignore_missing(operation, **kwargs) -> None:
    """Call a delete, treating 'already gone' as success and nothing else."""
    try:
        operation(**kwargs)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in _ALREADY_GONE:
            raise


def main(include_table: bool = False, clients: dict | None = None,
         account_id: str | None = None) -> None:
    """Delete Grace's provisioned infrastructure.

    `clients` is injectable so the matching logic can be tested without an AWS
    call — the tests drive this function end to end against recording fakes and
    assert that every name it produced passes `is_grace_resource`.
    """
    region = naming.REGION
    clients = clients or {}

    def client(service: str):
        if service in clients:
            return clients[service]
        return boto3.client(service, region_name=region)

    if account_id is None:
        account_id = boto3.client("sts").get_caller_identity()["Account"]

    # EventBridge: targets before the rule. A rule with targets still attached
    # refuses to delete, so the order is load-bearing rather than stylistic.
    #
    # The target Ids are **discovered**, not predicted. `provision_eventbridge`
    # sets `Id: "grace-sweep"`, which happens to equal `naming.STATE_MACHINE`
    # today — restating that coincidence here would leave a target behind (and
    # therefore an undeletable rule) the moment either name changed
    # independently. Each discovered Id still passes the guard.
    events = client("events")
    try:
        target_ids = [
            t["Id"] for t in
            events.list_targets_by_rule(Rule=naming.SCHEDULE_RULE).get("Targets", [])
        ]
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in _ALREADY_GONE:
            raise
        target_ids = []
    if target_ids:
        _ignore_missing(events.remove_targets,
                        Rule=_guard(naming.SCHEDULE_RULE),
                        Ids=[_guard(t) for t in target_ids])
    _ignore_missing(events.delete_rule, Name=_guard(naming.SCHEDULE_RULE))
    print(f"deleted rule {naming.SCHEDULE_RULE}")

    sf = client("stepfunctions")
    _ignore_missing(
        sf.delete_state_machine,
        stateMachineArn=(
            f"arn:aws:states:{region}:{account_id}:stateMachine:"
            f"{_guard(naming.STATE_MACHINE)}"
        ),
    )
    print(f"deleted state machine {naming.STATE_MACHINE}")

    lam = client("lambda")
    _ignore_missing(lam.delete_function, FunctionName=_guard(naming.LAMBDA))
    print(f"deleted function {naming.LAMBDA}")

    # The alarm, then the filter that fed it. Leaving the filter behind would keep
    # publishing to a namespace whose alarm is gone, and the next `provision_all`
    # would find a filter it did not write.
    cw = client("cloudwatch")
    _ignore_missing(cw.delete_alarms, AlarmNames=[_guard(naming.ALARM)])
    print(f"deleted alarm {naming.ALARM}")

    logs = client("logs")
    _ignore_missing(logs.delete_metric_filter,
                    logGroupName=_guard(naming.SFN_LOG_GROUP),
                    filterName=_guard(provision_alarm.FILTER_NAME))
    print(f"deleted metric filter {provision_alarm.FILTER_NAME}")

    # IAM: the inline policy before the role. A role with a policy still attached
    # refuses to delete, and an orphaned role keeps its permissions.
    #
    # Iterated over `POLICY_BUILDERS` rather than a retyped tuple, so a fifth role
    # added there is torn down too — the discovery-from-source discipline the
    # model-id and ledger-writer guards use.
    iam = client("iam")
    for purpose in provision_iam.POLICY_BUILDERS:
        role = _guard(provision_iam.role_name(purpose))
        # Delete every inline policy actually attached, not only the one this
        # plan's naming predicts: a role left with an unexpected policy cannot be
        # deleted, and the teardown would fail on the next line for a reason that
        # reads like a permissions problem.
        try:
            attached = iam.list_role_policies(RoleName=role).get("PolicyNames", [])
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in _ALREADY_GONE:
                raise
            attached = []
        for policy in attached or [provision_iam.policy_name(purpose)]:
            _ignore_missing(iam.delete_role_policy,
                            RoleName=role, PolicyName=_guard(policy))
        _ignore_missing(iam.delete_role, RoleName=role)
        print(f"deleted role {role}")

    if include_table:
        ddb = client("dynamodb")
        _ignore_missing(ddb.delete_table, TableName=_guard(naming.TABLE))
        print(f"deleted table {naming.TABLE} (the audit trail is gone)")
    else:
        print(f"kept table {naming.TABLE} — pass --include-table to delete the ledger")

    print(
        "NOTE: the AgentCore runtime and memory are not deleted here. Use the "
        "`agentcore` CLI / `delete-memory` deliberately."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="teardown")
    parser.add_argument("--include-table", action="store_true",
                        help="also delete grace-cases (destroys the audit trail)")
    args = parser.parse_args()
    main(include_table=args.include_table)
