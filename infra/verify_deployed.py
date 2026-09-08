"""Does the running system match this repository?

    .venv/bin/python -m infra.verify_deployed

Exit 0 when the deployed state machine, schedule input, and Step Functions IAM
policy are equal to what the provisioners in this package produce; exit 1 with a
list of the differences otherwise.

**Three resources, and the runtime image is not one of them.** The orchestration
can match this repository perfectly while the container serving every case runs
code that is weeks old — which is exactly what happened for three days. So the
question in the title is answered only for what is listed above, and `main()`'s
success line says so rather than claiming the whole system.

**Why this exists.** All three were edited live on 2026-09-07 to fix a sweep that
could not see a submitted household, and all three matched their provisioners
afterwards — but nothing asserted it. The defect that prompted that work was
precisely this shape: the repository described one system and another was
running, with no error anywhere, because a green schedule and a `SUCCEEDED`
execution look identical either way.

**Reads only.** This module issues no write of any kind. It is safe to run
against production at any time, which is the point — a drift check nobody dares
run is not a drift check.

**Every difference is reported, not just the first.** An operator who fixes one
mismatch and re-runs into the next learns the same lesson twice. The one thing
that does *not* accumulate is an AWS API error: a resource that is missing
outright makes its `describe`/`get` call raise, and the remaining checks do not
run. That is deliberate rather than overlooked. A `StateMachineDoesNotExist` or
`NoSuchEntity` is drift of the loudest kind, it names the resource in its own
message, and it exits non-zero — so the operator is not misled, only less
completely informed. Catching it here would mean inventing a second vocabulary
for "absent" alongside "different", and the honest limit is cheaper than the
guard. Say it in this docstring rather than letting the sentence above
over-promise.

**Mismatches are strings, not a structured result.** Weighed deliberately, since
this codebase elsewhere insists on comparing a `GateReason` by `.code` rather
than by its sentence. The difference is who reads it: a `GateResult` is branched
on by code, so its reasons need codes, whereas the only consumer here is `main()`
printing to a terminal for a human. A dataclass per mismatch would exist purely
to be formatted back into the string it replaced. The brittleness that argument
is really about lives in the *tests*, and is fixed there — `tests/
test_verify_deployed.py` asserts on the resource identifiers a message is
obliged to carry, never on its wording. If a programmatic consumer ever appears
(a CI gate branching on which resource drifted, say), that is the moment to add
the structure, not before.
"""

from __future__ import annotations

import json
import sys

import boto3

from infra import naming, provision_eventbridge, provision_iam, provision_stepfunctions

# The purpose key the Step Functions role and its inline policy are registered
# under in `provision_iam`. Both names come from that module's own builders
# rather than being written out here, for the reason `role_name`'s docstring
# already gives: a rename reflected in only one place orphans a role nobody
# notices.
#
# A drift check is the worst possible home for a second copy of that name.
# Every other identifier this module compares is rebuilt from `naming`, so a
# stale one reports drift — loudly, which is the job. A stale *role* name
# instead makes `get_role_policy` raise `NoSuchEntity`, and by the limit in this
# module's docstring that aborts the state machine and schedule checks too. One
# literal out of date here would quietly stop all three comparisons rather than
# failing the one it belongs to.
#
# `role_name` raises `KeyError` on an unrecognised purpose, so a typo in this
# constant fails at import rather than against the live account.
SFN_PURPOSE = "stepfunctions"
SFN_ROLE = provision_iam.role_name(SFN_PURPOSE)
SFN_POLICY = provision_iam.policy_name(SFN_PURPOSE)


def check_drift(sfn, events, iam, account_id: str) -> list[str]:
    """Every way the deployed system differs from the provisioners.

    Takes its three clients as parameters so the tests drive it with fakes and
    touch no network. Appends to one list and returns at the end — never returns
    early on the first mismatch, for the reason in the module docstring.
    """
    drift: list[str] = []
    lambda_arn = f"arn:aws:lambda:{naming.REGION}:{account_id}:function:{naming.LAMBDA}"

    state_machine_arn = (
        f"arn:aws:states:{naming.REGION}:{account_id}:"
        f"stateMachine:{naming.STATE_MACHINE}"
    )
    live_definition = json.loads(
        sfn.describe_state_machine(stateMachineArn=state_machine_arn)["definition"]
    )
    expected_definition = provision_stepfunctions.definition(account_id, lambda_arn)
    if live_definition != expected_definition:
        # Naming the two `StartAt` values is not decoration: the 2026-09-07 fix
        # was precisely a new first state, so this is the field that says at a
        # glance whether the deployed machine still reads its caseload from the
        # table or was rolled back to sweeping a handed-in list.
        drift.append(
            f"state machine {naming.STATE_MACHINE}: the deployed definition is not "
            f"what infra/provision_stepfunctions.py produces "
            f"(deployed starts at {live_definition.get('StartAt')!r}, "
            f"expected {expected_definition.get('StartAt')!r})"
        )

    targets = events.list_targets_by_rule(Rule=naming.SCHEDULE_RULE)["Targets"]
    live_input = json.loads(targets[0]["Input"]) if targets else None
    if live_input != provision_eventbridge.SWEEP_INPUT:
        drift.append(
            f"schedule {naming.SCHEDULE_RULE}: the target input is {live_input!r}, "
            f"expected {provision_eventbridge.SWEEP_INPUT!r}. A `case_ids` key here "
            f"means the caseload is frozen at provisioning time and a household "
            f"added since is never swept."
        )

    live_policy = iam.get_role_policy(
        RoleName=SFN_ROLE, PolicyName=SFN_POLICY
    )["PolicyDocument"]
    expected_policy = provision_iam._stepfunctions_policy(account_id)
    if live_policy != expected_policy:
        # Both statement lists, because the interesting drift is usually a
        # missing Sid rather than a changed one — `ReadTheCaseDirectory` is what
        # lets `ListCases` read the case directory at all, and without it the
        # sweep fails at its first state.
        live_sids = sorted(s.get("Sid", "?") for s in live_policy.get("Statement", []))
        expected_sids = sorted(
            s.get("Sid", "?") for s in expected_policy.get("Statement", [])
        )
        drift.append(
            f"policy {SFN_POLICY} on {SFN_ROLE}: the deployed document is not what "
            f"infra/provision_iam.py produces (deployed statements: {live_sids}, "
            f"expected: {expected_sids})"
        )

    return drift


def main() -> int:
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    drift = check_drift(
        boto3.client("stepfunctions", region_name=naming.REGION),
        boto3.client("events", region_name=naming.REGION),
        boto3.client("iam"),
        account_id,
    )
    if not drift:
        # Name the three things that were compared, and name the one that was
        # not. "the deployed sweep matches this repository" claims more than
        # this module checks: the **runtime container image** is not among the
        # comparisons, and a stale image is exactly the drift that went
        # unnoticed for three days — version 2 was serving code from
        # 2026-09-03 while the repository had moved on, with the schedule green
        # and every execution SUCCEEDED. A drift check that overstates its own
        # coverage is worse than none, because it is the thing an operator
        # trusts instead of looking.
        print(
            "no drift: the state machine definition, the schedule's target "
            "input, and the Step Functions role policy match this repository. "
            "The runtime container image version is NOT checked — compare it "
            "separately before trusting that deployed code matches this repo."
        )
        return 0
    print(f"DRIFT in {len(drift)} place(s):")
    for item in drift:
        print(f"  - {item}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
