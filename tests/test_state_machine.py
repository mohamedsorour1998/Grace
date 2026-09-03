"""The state machine definition, asserted as data.

Cheap, and it catches the things most likely to be wrong: a Catch branch that
does not write an escalation row, a returned `{"status": "error"}` with no branch
to catch it, and a concurrency setting that invites the throttling its own retry
policy then absorbs.
"""

from __future__ import annotations

import json

from infra import naming, provision_stepfunctions

ACCOUNT = "123456789012"
LAMBDA_ARN = f"arn:aws:lambda:us-east-1:{ACCOUNT}:function:{naming.LAMBDA}"


def _definition():
    return provision_stepfunctions.definition(ACCOUNT, LAMBDA_ARN)


def _next_map(definition):
    return next(s for s in definition["States"].values() if s["Type"] == "Map")


def _branch_states(definition):
    return _next_map(definition)["ItemProcessor"]["States"]


def _invoke_task(definition):
    return _branch_states(definition)["InvokeCase"]


def _writes_a_row(state) -> bool:
    return "dynamodb" in state.get("Resource", "")


def test_the_map_state_bounds_concurrency():
    """Twelve concurrent cases each open a graph invocation, and the two
    swarm-routed cases cost ~18-19 Bedrock invocations each. Unbounded
    concurrency invites the throttling the retry policy then has to absorb."""
    m = _next_map(_definition())
    assert m["MaxConcurrency"] == 3


def test_every_case_branch_has_a_catch_that_writes_an_escalation():
    """The fail-closed rule as infrastructure. A Lambda that times out
    produces no verdict, and 'no verdict' must become 'a human looks at it',
    never 'nothing happened'."""
    states = _branch_states(_definition())
    task = _invoke_task(_definition())
    assert task.get("Catch"), "a case branch with no Catch loses the family silently"
    for target in {c["Next"] for c in task["Catch"]}:
        # The Catch target must actually write a row, not merely succeed.
        assert _writes_a_row(states[target]), states[target]


def test_the_retry_policy_covers_throttling():
    """Bedrock throttling is the expected transient failure at this
    concurrency, and it must not spend the Catch branch."""
    task = _invoke_task(_definition())
    errors = {e for r in task.get("Retry", []) for e in r["ErrorEquals"]}
    assert any("Throttl" in e or "TooManyRequests" in e for e in errors), errors


def test_the_escalation_row_carries_the_pending_status():
    """It must land on the escalation-queue GSI, or the dashboard cannot
    find it."""
    assert naming.PENDING in json.dumps(_definition())


# ---------------------------------------------------------------------------
# A returned error is no verdict either
# ---------------------------------------------------------------------------


def test_a_returned_error_status_also_reaches_a_human():
    """**`Catch` does not fire on a handler that returns `{"status": "error"}`.**

    The Lambda is deliberately written never to raise — `grace/entrypoint.py`
    and `infra/lambda_src/handler.py` both convert every failure into a
    reportable outcome, so Step Functions sees a *successful* task with an error
    payload. `Catch` covers a killed or throttled Lambda; it cannot see this.

    Without a branch on `$.status`, the two are opposite: a Lambda killed at its
    deadline gets an escalation row, and a Lambda that reported the same failure
    politely gets none. The family that disappears is the one whose failure was
    handled *better*. So the definition routes on the status field as well.
    """
    states = _branch_states(_definition())
    choices = [s for s in states.values() if s["Type"] == "Choice"]
    assert choices, "a returned error status has no branch, so it passes as success"

    routed = []
    for choice in choices:
        for rule in choice["Choices"]:
            variable = rule.get("Variable")
            matches_error = rule.get("StringEquals") == "error"
            if variable == "$.status" and matches_error:
                routed.append(rule["Next"])
    assert routed, [c["Choices"] for c in choices]
    for target in routed:
        assert _writes_a_row(states[target]), states[target]


def test_the_error_branch_and_the_catch_branch_write_the_same_shaped_row():
    """One escalation row shape, whichever way the case failed.

    Plan 3's dashboard reads the escalation GSI, so a row missing `status` or
    `escalated_at` — the GSI's own key attributes — is invisible to it. Written
    as an assertion over every DynamoDB writer in the branch rather than over a
    list of state names someone remembered.
    """
    writers = [s for s in _branch_states(_definition()).values() if _writes_a_row(s)]
    assert writers
    for state in writers:
        item = state["Parameters"]["Item"]
        assert item["status"] == {"S": naming.PENDING}
        assert "escalated_at" in item
        assert item["pk"]["S.$"].startswith("States.Format('CASE#{}'")
        assert item["sk"]["S.$"].startswith("States.Format('ESCALATION#{}'")


def test_the_invoke_task_does_not_end_the_branch_before_the_status_check():
    """A defect that would be invisible: `"End": True` on the task means the
    Choice state is unreachable and never runs, while every assertion about the
    Choice state's own shape still passes."""
    task = _invoke_task(_definition())
    assert "Next" in task
    assert not task.get("End")
    assert _branch_states(_definition())[task["Next"]]["Type"] == "Choice"


def test_every_branch_state_is_reachable_from_the_branch_start():
    """A state that nothing routes to is dead configuration, and a state machine
    with a dead escalation writer looks identical to one with a live one."""
    processor = _next_map(_definition())["ItemProcessor"]
    states = processor["States"]

    reachable, frontier = set(), [processor["StartAt"]]
    while frontier:
        name = frontier.pop()
        if name in reachable:
            continue
        reachable.add(name)
        state = states[name]
        for target in (
            [state["Next"]] if "Next" in state else []
        ) + [c["Next"] for c in state.get("Catch", [])] + [
            c["Next"] for c in state.get("Choices", [])
        ] + ([state["Default"]] if "Default" in state else []):
            frontier.append(target)

    assert reachable == set(states), set(states) - reachable


def test_the_pinned_date_travels_with_the_scheduled_event():
    """A `date.today()` anywhere in this system turns the 9/3 demo into 8/4 from
    2026-10-31, and the schedule is the one caller with no human to notice."""
    from infra import provision_eventbridge

    assert provision_eventbridge.SWEEP_INPUT["today"] == "2026-10-01"
    assert provision_eventbridge.SWEEP_INPUT["case_ids"] == provision_stepfunctions.CASE_IDS


def test_the_case_list_is_the_twelve_fixture_households():
    """The demo's claim is about these twelve specifically."""
    assert provision_stepfunctions.CASE_IDS == [f"c-{n:03d}" for n in range(1, 13)]
    assert len(provision_stepfunctions.CASE_IDS) == 12
