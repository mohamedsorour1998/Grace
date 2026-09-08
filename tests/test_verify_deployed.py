"""DOES THE RUNNING SYSTEM MATCH THE REPOSITORY?

The state machine definition, the schedule's input, and the Step Functions IAM
policy were each edited live on 2026-09-07. All three matched their
provisioners afterwards — and nothing asserted it. A console edit, or a
provisioning run that half-applied, would leave this repository describing
infrastructure that is not deployed, which is the shape of the defect that
started that day's work: the code said one thing, the running system did
another, and nothing failed.

**Every assertion here is on an identifier, never on wording.** The messages
`check_drift` returns are prose for a human, and prose gets improved; a test
that pins the adjective breaks on the improvement and teaches nobody anything.
What the messages *must* carry is which resource drifted, so that is what is
asserted — `naming.STATE_MACHINE`, `naming.SCHEDULE_RULE`, `SFN_POLICY`, and
the `case_ids` key whose reappearance is the specific regression. Same
reasoning as comparing a `GateReason` on `.code` rather than on its sentence.
"""

from __future__ import annotations

import json

from infra import naming, provision_eventbridge, provision_iam, provision_stepfunctions
from infra.verify_deployed import check_drift

ACCOUNT = "339712964409"
LAMBDA_ARN = f"arn:aws:lambda:{naming.REGION}:{ACCOUNT}:function:{naming.LAMBDA}"
STATE_MACHINE_ARN = (
    f"arn:aws:states:{naming.REGION}:{ACCOUNT}:stateMachine:{naming.STATE_MACHINE}"
)

# Built from `provision_iam`'s own builders, **not** imported from the module
# under test. Importing `verify_deployed.SFN_ROLE` would make the assertion
# self-referential: it would agree with whatever that module happens to say,
# which is exactly the check that is wanted here. The state machine ARN and the
# rule name above are independent of the module for the same reason, and this
# was the one identifier that was not.
SFN_ROLE = provision_iam.role_name("stepfunctions")
SFN_POLICY = provision_iam.policy_name("stepfunctions")


# The three fakes record what they were asked about, and read their arguments by
# key rather than swallowing them into `**_kwargs`. A fake that ignores its
# arguments cannot tell a checker reading the *right* state machine from one
# reading a name that does not exist — and the live run would be the only other
# thing that ever noticed, which is exactly the gap this module exists to close.
class _FakeSfn:
    def __init__(self, definition):
        self._definition = definition
        self.asked_for: list[str] = []

    def describe_state_machine(self, **kwargs):
        self.asked_for.append(kwargs["stateMachineArn"])
        return {"definition": json.dumps(self._definition)}


class _FakeEvents:
    def __init__(self, target_input):
        self._input = target_input
        self.asked_for: list[str] = []

    def list_targets_by_rule(self, **kwargs):
        self.asked_for.append(kwargs["Rule"])
        return {"Targets": [{"Input": json.dumps(self._input)}]}


class _FakeIam:
    def __init__(self, policy):
        self._policy = policy
        self.asked_for: list[tuple[str, str]] = []

    def get_role_policy(self, **kwargs):
        self.asked_for.append((kwargs["RoleName"], kwargs["PolicyName"]))
        # boto3 decodes an IAM PolicyDocument from its url-encoded JSON into a
        # dict before the caller sees it (`json_decode_policies`, registered on
        # `after-call.iam`), so a dict here is the real response shape rather
        # than a convenience.
        return {"PolicyDocument": self._policy}


def _matching():
    return (
        _FakeSfn(provision_stepfunctions.definition(ACCOUNT, LAMBDA_ARN)),
        _FakeEvents(provision_eventbridge.SWEEP_INPUT),
        _FakeIam(provision_iam._stepfunctions_policy(ACCOUNT)),
    )


def _resources_named(drift: list[str]) -> set[str]:
    """Which of the three resources the returned messages actually name."""
    return {
        resource
        for resource in (naming.STATE_MACHINE, naming.SCHEDULE_RULE, SFN_POLICY)
        if any(resource in item for item in drift)
    }


def test_no_drift_when_everything_matches():
    sfn, events, iam = _matching()

    assert check_drift(sfn, events, iam, ACCOUNT) == []

    # The three assertions below are the ones that make this test mean
    # something. `check_drift(...) == []` on its own passes against a function
    # that returns `[]` having read nothing at all — the vacuity this codebase
    # has been bitten by before — and it also passes against one that compares
    # the wrong resources. "No drift" is only a claim if all three were
    # genuinely consulted, by the names the deployed resources actually have.
    assert sfn.asked_for == [STATE_MACHINE_ARN]
    assert events.asked_for == [naming.SCHEDULE_RULE]
    assert iam.asked_for == [(SFN_ROLE, SFN_POLICY)]


def test_a_changed_state_machine_is_reported():
    _, events, iam = _matching()
    stale = provision_stepfunctions.definition(ACCOUNT, LAMBDA_ARN)
    stale["StartAt"] = "SweepCases"  # the pre-2026-09-07 shape
    drift = check_drift(_FakeSfn(stale), events, iam, ACCOUNT)
    assert len(drift) == 1
    assert _resources_named(drift) == {naming.STATE_MACHINE}
    # An operator who is told only that something differs has to diff two
    # documents by hand. The deployed value is what tells them which way.
    assert "SweepCases" in drift[0]


def test_a_frozen_case_list_on_the_schedule_is_reported():
    """The exact regression: a schedule that names its caseload again."""
    sfn, _, iam = _matching()
    frozen = {"case_ids": [f"c-{n:03d}" for n in range(1, 13)], "today": "2026-10-01"}
    drift = check_drift(sfn, _FakeEvents(frozen), iam, ACCOUNT)
    assert len(drift) == 1
    assert _resources_named(drift) == {naming.SCHEDULE_RULE}
    # `case_ids` is the whole finding. A message that reported "the input
    # differs" without naming the key would leave the operator comparing two
    # dicts to rediscover what a comment in `provision_eventbridge` already
    # says.
    assert "case_ids" in drift[0]


def test_a_missing_iam_statement_is_reported():
    sfn, events, _ = _matching()
    reduced = provision_iam._stepfunctions_policy(ACCOUNT)
    reduced["Statement"] = [
        s for s in reduced["Statement"] if s.get("Sid") != "ReadTheCaseDirectory"
    ]
    drift = check_drift(sfn, events, _FakeIam(reduced), ACCOUNT)
    assert len(drift) == 1
    assert _resources_named(drift) == {SFN_POLICY}


def test_every_mismatch_is_reported_not_just_the_first():
    """A checker that stops at the first difference hides the rest, and an
    operator then fixes one thing and re-runs into the next."""
    stale = provision_stepfunctions.definition(ACCOUNT, LAMBDA_ARN)
    stale["StartAt"] = "SweepCases"
    frozen = {"case_ids": ["c-001"], "today": "2026-10-01"}
    reduced = {"Version": "2012-10-17", "Statement": []}
    drift = check_drift(_FakeSfn(stale), _FakeEvents(frozen), _FakeIam(reduced), ACCOUNT)
    assert len(drift) == 3, drift
    # Counting three is a weaker claim than "one per resource": a checker that
    # reported the same mismatch three times would satisfy the count while still
    # hiding two of them.
    assert _resources_named(drift) == {
        naming.STATE_MACHINE,
        naming.SCHEDULE_RULE,
        SFN_POLICY,
    }
