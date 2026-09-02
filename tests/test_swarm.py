"""The deliberation swarm.

Three things get tested here, and only the first is what the plan drafted:

1. **Shape** — three nodes, three distinct models, no banned or legacy model,
   an advocate entry point. Cheap assertions that pin hard rules 1 and 2.
2. **Capability absence** — the swarm's three agents are read-only *by
   construction*, the same property `intake`/`documents` have in
   `grace/graph.py`. They deliberate; they never act. Asserted structurally,
   because handing them `action_tools` would leave every other test passing.
3. **Loop safety that actually fires** — the plan configures
   `repetitive_handoff_detection_window` and asserts only that it is `> 0`.
   That assertion passes against a configuration where detection can never
   trigger for the advocate/verifier ping-pong CLAUDE.md names it for. The
   tests below drive the SDK's own `should_continue` with this swarm's real
   configured values, so the mechanism is tested rather than its presence.
"""

from __future__ import annotations

from datetime import date

import pytest

from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.swarm import build_deliberation_swarm
from grace.tools.read import make_read_tools

TODAY = date(2026, 10, 1)

ROLES = ("advocate", "verifier", "referee")


def _read_tools(case_id: str = "c-011"):
    store = InMemoryCaseStore(load_fixture_cases())
    return make_read_tools(store, case_id, TODAY)


def _swarm(case_id: str = "c-011"):
    return build_deliberation_swarm(_read_tools(case_id))


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_swarm_has_three_opposed_roles():
    swarm = build_deliberation_swarm([])
    assert set(swarm.nodes.keys()) == set(ROLES)


def test_swarm_nodes_is_a_dict_keyed_by_node_id():
    """Guards the accessor the assertion above depends on.

    The plan wrote `{n.name for n in swarm.nodes}`. `Swarm.nodes` is a
    `dict[str, SwarmNode]`, so iterating it yields the string keys and that
    expression raises `AttributeError: 'str' object has no attribute 'name'`
    — confirmed against a real `Swarm`. Pinned here so an SDK upgrade that
    changes the container fails on this test, which explains the shape, rather
    than on the one above, which would look like a missing role.
    """
    swarm = build_deliberation_swarm([])
    assert isinstance(swarm.nodes, dict)
    assert all(isinstance(k, str) for k in swarm.nodes)
    with pytest.raises(AttributeError):
        {n.name for n in swarm.nodes}  # noqa: B018 — the plan's own expression


def test_the_entry_point_is_the_advocate():
    """The advocate must speak first: the verifier's job is to check an
    argument, so entering on the verifier gives it nothing to check."""
    swarm = _swarm()
    assert swarm.entry_point is swarm.nodes["advocate"].executor


def test_verifier_runs_a_different_model_than_the_advocate():
    """Two instances of the same model agreeing proves nothing.

    All three swarm roles must be distinct so that no model ever adversarially
    checks, or referees, its own argument.
    """
    from grace.models import ADVOCATE, REFEREE, VERIFIER

    assert len({ADVOCATE, VERIFIER, REFEREE}) == 3


def test_the_built_swarm_really_runs_three_distinct_models():
    """Hard rule 2 checked on the constructed objects, not on the constants.

    `test_verifier_runs_a_different_model_than_the_advocate` proves the
    registry holds three distinct IDs; it does not prove `build_deliberation_
    swarm` wired each role to its own. A copy-paste of `nova("advocate")` into
    all three agents passes that test and defeats the entire point of the
    swarm — a model would be refereeing its own argument, with nothing
    anywhere reporting a problem.
    """
    swarm = _swarm()
    model_ids = [
        swarm.nodes[role].executor.model.get_config()["model_id"] for role in ROLES
    ]
    assert len(set(model_ids)) == 3, model_ids


def test_no_gated_role_uses_nova_lite_v1():
    """nova-lite-v1:0 filed a renewal it was explicitly told not to file.

    Verified 2026-08-28: told "NEVER submit a renewal when a required document
    is missing", it read the case, saw the missing document, and called
    submit_renewal anyway. Pro, 2-Lite, and Micro all escalated correctly on
    the identical prompt. Keep it away from any role that reasons about
    authority.
    """
    from grace import models

    for role in ("advocate", "verifier", "referee", "judge"):
        assert "nova-lite-v1:0" not in models._ROLES[role]


def test_no_role_uses_a_legacy_model():
    """nova-premier-v1:0 is Legacy and refused by Converse at runtime.

    A deprecated model ID passes every static check and fails only on a live
    call, so assert against it here rather than discovering it mid-demo.
    """
    from grace import models

    assert not any("premier" in mid for mid in models._ROLES.values())


def test_no_swarm_agent_uses_the_banned_model():
    """The registry check above is about roles; this one is about the objects
    actually built, for the same reason as the distinct-models test."""
    from grace.models import BANNED_MODEL_IDS

    swarm = _swarm()
    for role in ROLES:
        model_id = swarm.nodes[role].executor.model.get_config()["model_id"]
        assert model_id not in BANNED_MODEL_IDS, role


def test_every_swarm_agent_runs_a_nova_model():
    """Hard rule 1."""
    swarm = _swarm()
    for role in ROLES:
        model_id = swarm.nodes[role].executor.model.get_config()["model_id"]
        assert "amazon.nova" in model_id, (role, model_id)


# ---------------------------------------------------------------------------
# Routing context. Omitting `description=` costs nothing visible and makes
# every handoff decision worse.
# ---------------------------------------------------------------------------


def test_every_swarm_agent_has_a_description():
    """Python's swarm builds its routing context from `description`.

    `Swarm._build_node_input` does
    `if node and hasattr(node.executor, "description") and node.executor.description:`
    before adding an "Agent description:" line to the context that tells each
    agent what the others are for. An agent without one is listed by bare name,
    so the others are handing off to a name with no stated role. Nothing
    crashes and nothing logs — the swarm just routes worse, which is invisible
    until it behaves oddly on a real case. The plan's Step 3 code omitted all
    three.
    """
    swarm = _swarm()
    for role in ROLES:
        description = getattr(swarm.nodes[role].executor, "description", None)
        assert isinstance(description, str) and description.strip(), role


def test_the_descriptions_are_three_different_roles():
    """A description copied across all three tells the swarm nothing about who
    to hand off to, which is the same failure as having none."""
    swarm = _swarm()
    descriptions = {swarm.nodes[role].executor.description for role in ROLES}
    assert len(descriptions) == 3, descriptions


def test_the_referee_is_told_to_conclude():
    """Convergence depends on this instruction, not on the iteration cap.

    A swarm completes when a node runs and does *not* call `handoff_to_agent`.
    The referee is the only node with nobody to hand off to, so "never hand off"
    is what makes the swarm terminate normally instead of cycling until a limit
    stops it.

    The plan's wording was "Do not hand off further — you conclude." Observed on
    a real `c-011` run through the graph, the referee handed back to the advocate
    anyway and the swarm cycled advocate→verifier→referee twice more before
    stopping on "Max handoffs reached: 8" and reporting FAILED — eight paid
    Bedrock calls, no conclusion, and the caseworker's row reduced to "the run
    ended in state 'failed'". The instruction is now explicit about every target
    and about the case where the referee feels it lacks information, which is
    when it reached for a handoff.
    """
    swarm = _swarm()
    prompt = " ".join(swarm.nodes["referee"].executor.system_prompt.split())
    assert "AMBIGUOUS" in prompt
    assert "CLEAR" in prompt
    assert "NEVER call handoff_to_agent" in prompt
    # The escape hatch it actually used: "I need more information."
    assert "incomplete" in prompt


def test_each_debater_is_told_which_agent_to_hand_off_to():
    """The bug that made this a three-model swarm in name only.

    `Swarm._execute_swarm` marks the swarm COMPLETED the moment a node finishes
    its turn without calling `handoff_to_agent`. The plan's prompts said "hand
    off to the referee when you have checked every claim" — an instruction about
    timing, not an obligation, and the advocate's said only to hand off "if you
    genuinely cannot make the case."

    Measured on three consecutive real `c-011` runs: `node_history` came back
    `['advocate']`, `['advocate']`, and `['advocate', 'referee']`, with the swarm
    reporting COMPLETED every time. The advocate argued, nobody checked it, and
    the deliberation ended — hard rule 2's requirement that three different
    models argue reduced to one model's unchecked opinion, with no error, no
    timeout, and nothing in the result to distinguish it from a real
    deliberation.

    So each debater's prompt must name its own successor and make the handoff
    mandatory. This asserts the instruction is present and correctly targeted;
    a model can still ignore it, which is why
    `test_the_swarm_result_reveals_a_collapsed_deliberation` documents how that
    shows up.
    """
    swarm = _swarm()

    def unwrapped(role: str) -> str:
        """The prompt as one line. Asserting on a phrase that the source happens
        to wrap across a newline makes the test fail on a reflow that changed
        nothing — and tempts a fix that reflows the prompt to satisfy a test."""
        return " ".join(swarm.nodes[role].executor.system_prompt.split())

    advocate = unwrapped("advocate")
    assert 'agent_name="verifier"' in advocate
    assert "MUST call handoff_to_agent" in advocate
    # The advocate must not skip the check by going straight to the referee —
    # observed happening on a real run.
    assert "Do not hand off to the referee" in advocate
    # And it must hand off even when it cannot make the case, or a hopeless
    # case ends the deliberation after one turn.
    assert "even if you cannot make the case" in advocate

    verifier = unwrapped("verifier")
    assert 'agent_name="referee"' in verifier
    assert "MUST call handoff_to_agent" in verifier


def test_the_advocate_is_told_not_to_trust_the_document_summary():
    """The other half of the collapse, and the part the handoff wording missed.

    `Graph._build_node_input` prepends every upstream node's output to a nested
    `Swarm`'s task, so the advocate — the entry point, and the only node that
    sees this before any deliberation exists — opens by reading the `documents`
    node saying "all required documents are present and current". It believed
    that, concluded there was nothing to argue, and ended its turn, which ends
    the swarm.

    Reproduced deterministically outside the graph by handing the swarm that
    same ContentBlock list: 2 of 3 runs collapsed to `['advocate']` with it, 0 of
    4 without it. After this correction, 4 of 4 converged on the identical
    input.

    The correction is factual, not merely insistent: a document check genuinely
    cannot settle an income, household-size, or source-conflict question,
    because `document_problems` never looks at those fields. `make_needs_
    deliberation` only routes a case here for one of those three reason codes,
    so the advocate's premise — something is in doubt — is guaranteed true by
    the edge condition that admitted it.
    """
    swarm = _swarm()
    advocate = " ".join(swarm.nodes["advocate"].executor.system_prompt.split())
    assert "deterministic eligibility check already found a question" in advocate
    assert "does not settle it" in advocate
    assert "not a conclusion you are permitted to reach alone" in advocate
    # It must go and look for the disputed fact rather than reasoning from the
    # summary it was handed.
    assert "read_case" in advocate


def test_the_swarm_result_reveals_a_collapsed_deliberation():
    """How a one-model "deliberation" is detectable, pinned as a property.

    A swarm that ends after the advocate reports `Status.COMPLETED` — the same
    status a real three-agent deliberation reports. The only thing that
    distinguishes them is `node_history` / `results`, which is why
    `grace/run.py` reads the **referee** by key and says "the deliberation did
    not reach a conclusion" when that key is absent, rather than falling back to
    whatever agent happened to speak last. A fallback to `node_history[-1]`
    would print the advocate's unchecked argument to a caseworker as though a
    verifier had confirmed it.
    """
    import grace.run as run

    assert run._deliberation_note(None) is None

    class _Node:
        def __init__(self, result):
            self.result = result

        def __str__(self):
            return str(self.result)

    class _Swarm:
        def __init__(self, results):
            self.results = results

    class _Graph:
        def __init__(self, results):
            self.results = results

    collapsed = _Graph(
        {"deliberate": _Node(_Swarm({"advocate": _Node("AMBIGUOUS: my own argument")}))}
    )
    assert run._deliberation_note(collapsed) == (
        "The deliberation did not reach a conclusion."
    )


# ---------------------------------------------------------------------------
# Capability absence, applied to the swarm exactly as `grace/graph.py` applies
# it to `intake` and `documents`.
# ---------------------------------------------------------------------------


def test_no_swarm_agent_can_act():
    """The swarm deliberates. It never acts.

    All three agents get `read_tools` and nothing else, so no prompt reaching
    them — including a prompt-injection payload arriving through
    `source_conflicts`, which is untrusted free text that `read_case` surfaces
    verbatim and which is precisely what these agents are convened to argue
    about — can file a renewal or message a family. That is capability absence
    (CLAUDE.md layer 1), the same reason `intake` and `documents` carry no
    action tools, and it is stronger than any gate because there is nothing to
    disobey.

    `handoff_to_agent` is injected by the SDK at `Swarm.__init__` and is
    expected; it can only target another node of this same swarm.
    """
    from grace.authority import ACTION_TOOLS

    swarm = _swarm()
    for role in ROLES:
        names = set(swarm.nodes[role].executor.tool_names)
        assert names == {
            "read_case",
            "check_window",
            "list_documents",
            "handoff_to_agent",
        }, (role, names)
        assert not (names & ACTION_TOOLS), (role, names & ACTION_TOOLS)


def test_the_handoff_tool_cannot_reach_outside_the_swarm():
    """The one tool the swarm's agents have that Grace did not give them.

    `handoff_to_agent` resolves its target with `swarm_ref.nodes.get(agent_name)`
    and returns an error result for anything not in that dict. `decide` — the
    only node with action tools — is a graph node, never a swarm node, so no
    handoff can reach it. Asserted against the SDK's source because this is
    the only mechanism by which a read-only swarm agent could plausibly cause
    something to happen outside itself.
    """
    import inspect

    import strands.multiagent.swarm as swarm_module

    source = inspect.getsource(swarm_module.Swarm._create_handoff_tool)
    assert "swarm_ref.nodes.get(agent_name)" in source
    assert "not found in swarm" in source

    swarm = _swarm()
    handoff = swarm.nodes["advocate"].executor.tool_registry.registry[
        "handoff_to_agent"
    ]
    result = handoff._tool_func(agent_name="decide", message="file it")
    assert result["status"] == "error"


def test_building_the_swarm_does_not_add_handoff_to_an_unrelated_agent():
    """The swarm and `decide` are built from the same `read_tools` list.

    `Swarm._inject_swarm_tools` calls
    `node.executor.tool_registry.process_tools([...])` on each swarm agent. If
    that registration reached the shared tool objects rather than each agent's
    own registry, `decide` — built from the same list, in the same
    `build_case_graph` call — would silently acquire `handoff_to_agent`, a tool
    that mutates swarm state, on the one node that can also file a renewal.
    Verified it does not, and pinned here because the sharing is deliberate
    (`_most_recent` discipline: one tool implementation, not two) and so is
    easy to keep while an SDK change makes the injection leak.
    """
    from strands import Agent

    from grace.models import nova

    read_tools = _read_tools()
    build_deliberation_swarm(read_tools)
    unrelated = Agent(
        name="decide",
        model=nova("briefer"),
        system_prompt="x",
        tools=read_tools,
        callback_handler=None,
    )
    assert "handoff_to_agent" not in unrelated.tool_names


def test_the_swarm_carries_no_authority_gate_and_no_ledger():
    """Deliberately, for the reason `intake`/`documents` carry neither.

    There is nothing here for a gate to block — no action tool exists in this
    context — and a second `AuthorityGate` would keep its own `_seen` set,
    populated by the swarm's reads, which would then disagree with the gate on
    `decide` about what happened. The swarm's reads must *not* satisfy
    `decide`'s prerequisites: `decide` is the node that acts, so `decide` is
    the node that has to look first.
    """
    swarm = _swarm()
    assert swarm._plugin_registry._plugins == {}
    for role in ROLES:
        agent = swarm.nodes[role].executor
        plugins = {type(p).__name__ for p in agent._plugin_registry._plugins.values()}
        assert "AuthorityGate" not in plugins, role
        hook_owners = {
            type(entry.callback.__self__).__name__
            for entries in agent.hooks._registered_callbacks.values()
            for entry in entries
            if hasattr(entry.callback, "__self__")
        }
        assert "LedgerHook" not in hook_owners, role


def test_no_swarm_agent_has_its_own_session_manager():
    """`Swarm._validate_swarm` raises `ValueError` if one does — only the
    orchestrator may carry a session manager. Asserted so Plan 2's AgentCore
    wiring cannot add one here without a test noticing."""
    swarm = _swarm()
    assert swarm.session_manager is None
    for role in ROLES:
        assert swarm.nodes[role].executor._session_manager is None, role


# ---------------------------------------------------------------------------
# Loop safety. The plan asserts the window is `> 0`; these assert it fires.
# ---------------------------------------------------------------------------


def test_swarm_has_loop_safety_configured():
    """An advocate and a verifier will ping-pong forever without limits."""
    swarm = build_deliberation_swarm([])
    assert swarm.max_handoffs <= 10
    assert swarm.max_iterations <= 10
    assert swarm.node_timeout <= 120.0
    assert swarm.repetitive_handoff_detection_window > 0


def test_the_detection_window_is_smaller_than_the_iteration_cap():
    """CLAUDE.md's ordering constraint: with the window at or above
    `max_iterations`, the iteration cap trips first and detection never fires
    at all."""
    swarm = build_deliberation_swarm([])
    assert swarm.repetitive_handoff_detection_window < swarm.max_iterations


def _should_continue(swarm, history: list[str]) -> tuple[bool, str]:
    """Ask the SDK's own limit checker about a node history.

    Drives `SwarmState.should_continue` with this swarm's real configured
    values rather than reimplementing the check, so the test measures the
    configuration Grace ships instead of a restatement of it.
    """
    from strands.multiagent.base import Status
    from strands.multiagent.swarm import SwarmState

    state = SwarmState(
        current_node=swarm.nodes["advocate"],
        task="t",
        completion_status=Status.EXECUTING,
    )
    state.node_history = [swarm.nodes[node_id] for node_id in history]
    return state.should_continue(
        max_handoffs=swarm.max_handoffs,
        max_iterations=swarm.max_iterations,
        execution_timeout=swarm.execution_timeout,
        repetitive_handoff_detection_window=swarm.repetitive_handoff_detection_window,
        repetitive_handoff_min_unique_agents=swarm.repetitive_handoff_min_unique_agents,
    )


def test_repetitive_handoff_detection_actually_fires_on_a_ping_pong():
    """The mechanism, not its presence — and the plan's configuration failed this.

    `should_continue` stops the swarm only when
    `unique_nodes < repetitive_handoff_min_unique_agents`. The plan sets
    `min_unique_agents=2` with `window=4`, so the exact advocate/verifier
    ping-pong CLAUDE.md names the setting for — history
    `[advocate, verifier, advocate, verifier]`, 2 unique nodes in the last 4 —
    evaluates `2 < 2`, which is `False`, and the swarm continues. Detection was
    configured, passed the `> 0` assertion above, and could never trigger.

    Verified directly against the SDK's own checker, both before and after the
    fix. With `min_unique_agents=3` the same history stops the swarm at four
    iterations instead of eight. The outcome is the same either way — a swarm
    that ping-pongs runs out of iterations and reports FAILED regardless — so
    the fix is purely a matter of paying for four Bedrock calls instead of
    eight before reaching it.
    """
    swarm = build_deliberation_swarm([])
    ping_pong = ["advocate", "verifier", "advocate", "verifier"]
    should_continue, reason = _should_continue(swarm, ping_pong)
    assert should_continue is False, reason
    assert "Repetitive handoff" in reason


def test_repetitive_handoff_detection_leaves_a_healthy_rotation_alone():
    """The other half of the property, and the reason the threshold cannot
    simply be raised until something stops.

    A real deliberation is advocate → verifier → referee, and the referee
    concludes. Detection must not fire on that, nor on a second round that
    revisits the advocate — otherwise tightening loop safety would break the
    swarm's normal path, turning every ambiguous case into a FAILED node.
    """
    swarm = build_deliberation_swarm([])
    for history in (
        ["advocate", "verifier", "referee"],
        ["advocate", "verifier", "referee", "advocate"],
        ["verifier", "referee", "advocate", "verifier"],
    ):
        should_continue, reason = _should_continue(swarm, history)
        assert should_continue is True, (history, reason)


def test_a_swarm_that_never_converges_is_bounded():
    """Detection is the cheap stop; the handoff/iteration cap is the guaranteed one.

    A history with three unique agents in every window slips past repetitive-
    handoff detection by design, so `max_handoffs`/`max_iterations` has to be
    what finally stops it. Asserted so a future change that raises the cap for
    headroom cannot quietly remove the bound.

    Named without saying *which* of the two caps fires, on purpose:
    `SwarmState.should_continue` checks `max_handoffs` before `max_iterations`
    (`len(node_history) >= max_handoffs` comes first in the SDK source), and
    this swarm sets both to the same value (6), so the handoff message
    ("Max handoffs reached: 6") is what actually returns here, never the
    iteration one. A test named "...bounded_by_iterations_too" asserting only
    `"Max" in reason` would pass on that substring while claiming to test the
    cap it never reaches — verified by checking which branch fires, not just
    that the swarm stops.
    """
    swarm = build_deliberation_swarm([])
    endless = ["advocate", "verifier", "referee"] * 4
    should_continue, reason = _should_continue(
        swarm, endless[: swarm.max_iterations]
    )
    assert should_continue is False, reason
    assert reason == f"Max handoffs reached: {swarm.max_handoffs}"


def test_the_swarms_own_budget_bounds_it_before_the_graphs_node_timeout():
    """Which timeout wins decides whether an ambiguous case escalates or errors.

    The graph applies `node_timeout` to a nested `Swarm` as a whole
    (`Graph._stream_node_to_queue` wraps the entire node stream in
    `asyncio.wait_for`), and a graph node timeout is **fail-fast**: it puts the
    exception on the event queue and `_execute_nodes_parallel` re-raises it,
    so the graph call raises and `decide` never runs. `sweep` catches that in
    its `except Exception` and records the case as an *error* — exit code 1,
    and no escalation row.

    The swarm's own `execution_timeout` fails differently and better: the
    swarm reports FAILED, the graph marks the node failed without raising,
    `decide` still runs, and the case escalates. So the swarm's total budget
    must be the binding limit. This test only asserts the swarm side of that
    inequality; `test_the_graph_node_timeout_does_not_preempt_the_swarms_own_
    budget` in tests/test_graph.py asserts the graph side, and checks the true
    margin (`execution_timeout + node_timeout`, not `execution_timeout`
    alone) against the graph's own node timeout.
    """
    swarm = build_deliberation_swarm([])
    # Three agents, each bounded by node_timeout, must fit inside the total.
    assert swarm.node_timeout * len(ROLES) <= swarm.execution_timeout


# ---------------------------------------------------------------------------
# Binding. The swarm reads through the same no-argument tools as every other
# node, so it cannot be redirected to another household.
# ---------------------------------------------------------------------------


def test_the_swarm_reads_the_case_it_was_built_for():
    """Identity comes from the bound tools, not from the deliberation.

    The swarm's whole job is to argue about a case's facts, which makes it the
    node most exposed to `source_conflicts` — untrusted free text that
    `read_case` surfaces verbatim. The read tools take no arguments, so there
    is no parameter for that text to poison: whatever the three agents say to
    each other, `read_case` returns the household `build_deliberation_swarm`
    was handed.
    """
    swarm = build_deliberation_swarm(_read_tools("c-012"))
    read_case = swarm.nodes["verifier"].executor.tool_registry.registry["read_case"]
    assert "c-012" in read_case._tool_func()
    assert read_case.tool_spec["inputSchema"]["json"]["properties"] == {}
