"""Tests for the authority gate wired into the Strands agent loop.

Every case id here is a fixture household. `TODAY` is pinned for the same
reason it is pinned in every other test module: `c-002` goes `closed` on
2026-10-31, so a `date.today()` anywhere in the stack turns the 9-act /
3-escalate demo into 8/4 on that date.
"""

import ast
import inspect
from dataclasses import replace
from datetime import date

import pytest

from grace.authority import ACTION_TOOLS
from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.steering import ALWAYS_ALLOWED, PREREQUISITES, AuthorityGate, _bare_tool_name
from grace.vendored_actions import Guide, Interrupt, Proceed

TODAY = date(2026, 10, 1)

# The reads the gate requires before it will consider an action. Named once so
# a change to PREREQUISITES does not need editing in fourteen places.
ALL_READS = {"read_case", "check_window", "list_documents"}


def _gate(case_id: str) -> AuthorityGate:
    return AuthorityGate(InMemoryCaseStore(load_fixture_cases()), case_id, TODAY)


async def _observe_all_reads(gate: AuthorityGate) -> None:
    """Drive the real read calls through the gate.

    Deliberately not `gate._seen = {...}`: assigning the set directly would
    let the prerequisite *recording* path rot without any test noticing,
    because every action test would still pass against a hand-written set.
    """
    for name in ("read_case", "check_window", "list_documents"):
        await gate.steer_before_tool(agent=None, tool_use={"name": name, "input": {}})


async def test_read_tools_always_proceed():
    gate = _gate("c-001")
    action = await gate.steer_before_tool(
        agent=None, tool_use={"name": "read_case", "input": {}}
    )
    assert isinstance(action, Proceed)


async def test_escalation_is_never_blocked():
    """Handing a decision to a human is always allowed (hard rule 7)."""
    gate = _gate("c-010")  # a case that must escalate
    action = await gate.steer_before_tool(
        agent=None,
        tool_use={"name": "escalate_to_caseworker", "input": {"question": "?"}},
    )
    assert isinstance(action, Proceed)


async def test_escalation_is_allowed_even_when_the_case_cannot_be_read():
    """The moment a human is most needed is when nothing loads.

    A precondition on escalating — even an implicit one like "the case must
    exist" — would trap a case with no exit.
    """
    gate = AuthorityGate(InMemoryCaseStore(load_fixture_cases()), "c-nope", TODAY)
    action = await gate.steer_before_tool(
        agent=None,
        tool_use={"name": "escalate_to_caseworker", "input": {"question": "?"}},
    )
    assert isinstance(action, Proceed)


async def test_escalation_is_allowed_before_any_read_has_happened():
    gate = _gate("c-010")
    assert gate._seen == set()
    action = await gate.steer_before_tool(
        agent=None, tool_use={"name": "escalate_to_caseworker", "input": {"question": "?"}}
    )
    assert isinstance(action, Proceed)


def test_escalation_is_exempted_by_policy_not_by_accident():
    """Hard rule 7 must not depend on `escalate_to_caseworker` being absent
    from `ACTION_TOOLS`.

    Today it is absent, so the read-only branch would let it through even with
    `ALWAYS_ALLOWED` deleted — deleting that check breaks no test. That makes
    the rule hold by coincidence: the moment someone adds
    `escalate_to_caseworker` to `ACTION_TOOLS` (a reasonable thing to do — it
    *does* change state, it writes a ledger row), it acquires no
    `PREREQUISITES` entry and starts failing closed on the unmapped-tool path.
    Escalation would then be blocked precisely when a human is most needed.

    So assert the invariant directly: every always-allowed tool is exempted
    ahead of any other classification.
    """
    assert ALWAYS_ALLOWED
    for name in ALWAYS_ALLOWED:
        assert name not in PREREQUISITES, name


async def test_escalation_survives_being_classified_as_an_action_tool():
    """The same invariant, executed rather than asserted structurally.

    Monkeypatches `escalate_to_caseworker` into `ACTION_TOOLS` and confirms
    the gate still lets it through. Without the `ALWAYS_ALLOWED` check, this
    is an Interrupt — escalation blocked on a case that must escalate.
    """
    import grace.steering as steering

    original = steering.ACTION_TOOLS
    steering.ACTION_TOOLS = original | {"escalate_to_caseworker"}
    try:
        gate = _gate("c-010")
        action = await gate.steer_before_tool(
            agent=None,
            tool_use={"name": "escalate_to_caseworker", "input": {"question": "?"}},
        )
        assert isinstance(action, Proceed), getattr(action, "reason", "")
    finally:
        steering.ACTION_TOOLS = original


async def test_clean_case_may_submit_renewal_after_prerequisites():
    gate = _gate("c-001")
    await _observe_all_reads(gate)
    action = await gate.steer_before_tool(
        agent=None, tool_use={"name": "submit_renewal", "input": {}}
    )
    assert isinstance(action, Proceed), getattr(action, "reason", "")


async def test_skipping_prerequisites_is_guided_not_interrupted():
    """Grace has not looked at the documents yet. That is a correctable
    mistake, so guide it rather than waking a human."""
    gate = _gate("c-001")
    await gate.steer_before_tool(agent=None, tool_use={"name": "read_case", "input": {}})
    action = await gate.steer_before_tool(
        agent=None, tool_use={"name": "submit_renewal", "input": {}}
    )
    assert isinstance(action, Guide)
    assert "list_documents" in action.reason


async def test_guide_lists_missing_prerequisites_in_a_stable_order():
    """Two runs of the same situation must produce the same guidance string.

    `_seen` is a set, so anything that derived the message by iterating it
    would order the missing reads by hash. The message is built from
    `PREREQUISITES`' declared tuple instead, which is ordered.
    """
    messages = set()
    for _ in range(5):
        gate = _gate("c-001")
        action = await gate.steer_before_tool(
            agent=None, tool_use={"name": "submit_renewal", "input": {}}
        )
        assert isinstance(action, Guide)
        messages.add(action.reason)
    assert len(messages) == 1, messages
    # And that single order is the declared one, not an accident.
    only = messages.pop()
    positions = [only.index(r) for r in PREREQUISITES["submit_renewal"]]
    assert positions == sorted(positions), only


async def test_missing_document_interrupts_for_a_human():
    gate = _gate("c-010")  # missing proof_of_residency
    await _observe_all_reads(gate)
    action = await gate.steer_before_tool(
        agent=None, tool_use={"name": "submit_renewal", "input": {}}
    )
    assert isinstance(action, Interrupt)
    assert "proof_of_residency" in action.reason


async def test_material_income_change_interrupts():
    gate = _gate("c-011")  # income moved 30%
    await _observe_all_reads(gate)
    action = await gate.steer_before_tool(
        agent=None, tool_use={"name": "submit_renewal", "input": {}}
    )
    assert isinstance(action, Interrupt)
    assert "income" in action.reason.lower()


async def test_source_conflict_interrupts():
    gate = _gate("c-012")
    await _observe_all_reads(gate)
    action = await gate.steer_before_tool(
        agent=None, tool_use={"name": "submit_renewal", "input": {}}
    )
    assert isinstance(action, Interrupt)


async def test_send_family_message_proceeds_when_the_only_problem_is_a_document():
    """The one thing outreach exists to do: chase a missing document, on the
    same case that must escalate rather than file.

    c-010 escalates submit_renewal (see test_missing_document_interrupts_for_a_
    human above) — proving outreach is gated on a genuinely different, narrower
    question, not on the same clean verdict submit_renewal needs.
    """
    gate = _gate("c-010")  # missing proof_of_residency, nothing else wrong
    await _observe_all_reads(gate)
    action = await gate.steer_before_tool(
        agent=None, tool_use={"name": "send_family_message", "input": {"body": "hola"}}
    )
    assert isinstance(action, Proceed)


async def test_send_family_message_still_interrupts_on_a_material_income_change():
    """Outreach must not fire when eligibility itself is in doubt — texting the
    family does not resolve an income discrepancy a human must review."""
    gate = _gate("c-011")  # income moved 30%, not a document problem
    await _observe_all_reads(gate)
    action = await gate.steer_before_tool(
        agent=None, tool_use={"name": "send_family_message", "input": {"body": "hola"}}
    )
    assert isinstance(action, Interrupt)


async def test_send_family_message_still_interrupts_on_a_source_conflict():
    gate = _gate("c-012")
    await _observe_all_reads(gate)
    action = await gate.steer_before_tool(
        agent=None, tool_use={"name": "send_family_message", "input": {"body": "hola"}}
    )
    assert isinstance(action, Interrupt)


async def test_send_family_message_still_interrupts_when_a_document_problem_coincides_with_an_income_problem():
    """A case that is off on both a document and income must still escalate:
    the document-only carve-out must not swallow an unrelated eligibility
    problem just because a document reason is also present."""
    store = InMemoryCaseStore(load_fixture_cases())
    case = replace(store.get("c-010"), reported_income_cents=400_000)  # +142%, material
    store = InMemoryCaseStore(
        [case if c.case_id == "c-010" else c for c in load_fixture_cases()]
    )
    gate = AuthorityGate(store, "c-010", TODAY)
    await _observe_all_reads(gate)
    action = await gate.steer_before_tool(
        agent=None, tool_use={"name": "send_family_message", "input": {"body": "hola"}}
    )
    assert isinstance(action, Interrupt)


async def test_verification_error_fails_closed():
    """If the case cannot be read, escalate. Never act on an unknown."""
    store = InMemoryCaseStore(load_fixture_cases())
    gate = AuthorityGate(store, "c-does-not-exist", TODAY)
    await _observe_all_reads(gate)
    action = await gate.steer_before_tool(
        agent=None, tool_use={"name": "submit_renewal", "input": {}}
    )
    assert isinstance(action, Interrupt)
    assert "verification" in action.reason.lower() or "could not" in action.reason.lower()


async def test_evaluate_raising_fails_closed_not_open():
    """`evaluate` itself can raise, and it must not escape the handler.

    A pack that loads cleanly can still make the date arithmetic inside
    `evaluate` overflow (`cert_end = date.max`), which raises `OverflowError`
    — neither `InvalidRulePack` nor `ValueError` nor `TypeError`. If that
    escaped `steer_before_tool`, the SDK would swallow it (see
    `test_a_raising_handler_would_let_the_tool_execute` below) and the tool
    would execute ungated. So the `try` must wrap the `evaluate` call itself,
    not only the loads above it.
    """
    from dataclasses import replace

    cases = load_fixture_cases()
    cases = [replace(c, cert_end=date.max) if c.case_id == "c-001" else c for c in cases]
    gate = AuthorityGate(InMemoryCaseStore(cases), "c-001", TODAY)
    await _observe_all_reads(gate)
    action = await gate.steer_before_tool(
        agent=None, tool_use={"name": "submit_renewal", "input": {}}
    )
    assert isinstance(action, Interrupt)


async def test_a_raising_handler_would_let_the_tool_execute():
    """Why the handler must never raise: the SDK swallows the exception.

    `SteeringHandler.provide_tool_steering_guidance` wraps the
    `steer_before_tool` call in `except Exception: return` — it logs at debug
    level and leaves `cancel_tool` unset, so the tool runs. This test pins
    that SDK behaviour so the fail-closed discipline in `steering.py` is
    justified by something executable rather than by a comment, and so an SDK
    upgrade that changes it is noticed here.
    """
    from strands.hooks import BeforeToolCallEvent

    from grace.vendored_actions import SteeringHandler

    class Raising(SteeringHandler):
        name = "raising"

        async def steer_before_tool(self, *, agent, tool_use, **kwargs):
            raise OverflowError("date value out of range")

    event = BeforeToolCallEvent(
        agent=None,
        selected_tool=None,
        tool_use={"name": "submit_renewal", "input": {}},
        invocation_state={},
    )
    await Raising().provide_tool_steering_guidance(event)
    assert not event.cancel_tool, (
        "SDK no longer swallows handler exceptions — revisit steering.py's "
        "fail-closed comment"
    )


async def test_steer_before_tool_never_raises_on_any_action_tool():
    """Belt and braces on the property above, across every action tool.

    Driven by `ACTION_TOOLS` rather than a hand-written list so a tool added
    to the set later is covered without anyone remembering to add it here.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    for case_id in ("c-001", "c-010", "c-nonexistent"):
        for name in sorted(ACTION_TOOLS) + ["escalate_to_caseworker", "read_case"]:
            gate = AuthorityGate(store, case_id, TODAY)
            await _observe_all_reads(gate)
            action = await gate.steer_before_tool(
                agent=None, tool_use={"name": name, "input": {}}
            )
            assert isinstance(action, (Proceed, Guide, Interrupt)), (case_id, name)


async def test_gate_records_reads_it_observes():
    """The gate tracks prerequisites itself, so it does not depend on the
    ledger provider being wired up."""
    gate = _gate("c-001")
    await _observe_all_reads(gate)
    assert gate._seen == ALL_READS


async def test_unknown_action_tool_fails_closed():
    """A state-changing tool the gate does not recognise must not pass.

    `close_case` is in `ACTION_TOOLS` but has no `PREREQUISITES` entry, so it
    is the live example of this today. The test does not depend on that
    staying true — it asserts the *rule*, using whatever action tool is
    currently unmapped, and skips only if every action tool has a policy.
    """
    unmapped = sorted(ACTION_TOOLS - set(PREREQUISITES))
    if not unmapped:
        pytest.skip("every action tool now has a gate policy")
    for name in unmapped:
        gate = _gate("c-001")
        await _observe_all_reads(gate)
        action = await gate.steer_before_tool(
            agent=None, tool_use={"name": name, "input": {}}
        )
        assert isinstance(action, Interrupt), name


def test_every_prerequisite_key_is_an_action_tool():
    """A policy for a tool the gate never gates is dead code that reads as
    protection. `send_family_message` is the case that matters: it is in
    `ACTION_TOOLS`, so a typo in the key here would silently drop it to the
    unmapped-and-interrupted path.
    """
    assert set(PREREQUISITES) <= ACTION_TOOLS, set(PREREQUISITES) - ACTION_TOOLS


def test_every_prerequisite_names_a_real_read_tool():
    """A prerequisite naming a tool that does not exist can never be
    satisfied, which turns a Guide into an infinite retry loop rather than a
    correctable mistake."""
    from grace.tools.read import make_read_tools

    real = {
        t.tool_name
        for t in make_read_tools(InMemoryCaseStore(load_fixture_cases()), "c-001", TODAY)
    }
    for name, required in PREREQUISITES.items():
        assert set(required) <= real, (name, set(required) - real)


def test_prerequisites_are_ordered_sequences_not_sets():
    """The Guide message is built from these, so they must have an order.

    A `set` here would make the guidance string depend on hash ordering,
    which is stable within a process and not across processes — the kind of
    nondeterminism that shows up only in a recorded demo.
    """
    for name, required in PREREQUISITES.items():
        assert isinstance(required, tuple), (name, type(required))


async def test_gateway_prefixed_action_tool_is_still_gated():
    """AgentCore Gateway exposes tools as `target___tool`. The gate must still
    recognise the action, or every gateway tool bypasses it (Appendix C.1)."""
    gate = _gate("c-010")  # a case that must escalate
    await _observe_all_reads(gate)
    action = await gate.steer_before_tool(
        agent=None,
        tool_use={"name": "grace-actions___submit_renewal", "input": {}},
    )
    assert isinstance(action, Interrupt)


async def test_gateway_prefixed_read_satisfies_a_prerequisite():
    """The prefix is stripped on the recording side too.

    Otherwise a gateway-provided `grace-reads___list_documents` would be
    recorded under its prefixed name, never satisfy `list_documents`, and the
    gate would Guide forever on a case where every read had in fact happened.
    """
    gate = _gate("c-001")
    for name in ("read_case", "check_window", "list_documents"):
        await gate.steer_before_tool(
            agent=None, tool_use={"name": f"grace-reads___{name}", "input": {}}
        )
    assert gate._seen == ALL_READS
    action = await gate.steer_before_tool(
        agent=None, tool_use={"name": "submit_renewal", "input": {}}
    )
    assert isinstance(action, Proceed), getattr(action, "reason", "")


def test_bare_tool_name_handles_the_awkward_shapes():
    """A trailing separator must not strip the name to nothing: an empty name
    would miss `ACTION_TOOLS` and be classified read-only."""
    assert _bare_tool_name("submit_renewal") == "submit_renewal"
    assert _bare_tool_name("grace-actions___submit_renewal") == "submit_renewal"
    assert _bare_tool_name("a___b___submit_renewal") == "submit_renewal"
    assert _bare_tool_name("___submit_renewal") == "submit_renewal"
    # Degenerate inputs fall back to the original rather than to "".
    assert _bare_tool_name("submit_renewal___") == "submit_renewal___"
    assert _bare_tool_name("") == ""


async def test_a_missing_tool_name_is_not_treated_as_a_read():
    """`tool_use` is a TypedDict, so a malformed one has no required keys.

    An absent name must not fall through to the read-only Proceed branch: an
    unnameable tool call is exactly the thing the gate cannot verify.
    """
    gate = _gate("c-001")
    await _observe_all_reads(gate)
    for bad in ({}, {"input": {}}, {"name": ""}, {"name": None}):
        action = await gate.steer_before_tool(agent=None, tool_use=bad)
        assert isinstance(action, Interrupt), bad
    # And the malformed call is not recorded as a satisfied prerequisite.
    assert gate._seen == ALL_READS


async def test_one_gate_serves_one_case():
    """`_seen` is per-instance, matching `make_read_tools`' one-case binding.

    Two gates over the same store must not share observed reads, or reads
    performed on one household would satisfy the prerequisites for another.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    a = AuthorityGate(store, "c-001", TODAY)
    b = AuthorityGate(store, "c-010", TODAY)
    await _observe_all_reads(a)
    assert b._seen == set()
    action = await b.steer_before_tool(
        agent=None, tool_use={"name": "submit_renewal", "input": {}}
    )
    assert isinstance(action, Guide)


async def test_gate_reads_the_case_at_decision_time_not_construction_time():
    """The verdict must reflect the case as of the action, not as of setup.

    Nothing in Plan 1 mutates a store mid-run, but Plan 2's DynamoDB store
    will be read repeatedly, and a gate that cached the case at construction
    would authorise a filing against facts that had since changed.
    """
    gate = _gate("c-001")
    await _observe_all_reads(gate)
    calls = []
    original = gate._store.get

    def counting_get(case_id):
        calls.append(case_id)
        return original(case_id)

    gate._store.get = counting_get  # type: ignore[method-assign]
    await gate.steer_before_tool(agent=None, tool_use={"name": "submit_renewal", "input": {}})
    assert calls == ["c-001"]


async def test_gate_never_reads_a_case_other_than_its_own():
    """Layer 2 of the escalation boundary: identity comes from construction.

    `tool_use["input"]` is model-controlled, so an injected `case_id` there
    must not reach the store.
    """
    gate = _gate("c-001")
    await _observe_all_reads(gate)
    seen = []
    original = gate._store.get
    gate._store.get = lambda case_id: (seen.append(case_id), original(case_id))[1]  # type: ignore[method-assign]
    await gate.steer_before_tool(
        agent=None,
        tool_use={"name": "submit_renewal", "input": {"case_id": "c-010"}},
    )
    assert seen == ["c-001"]


def test_gate_takes_today_and_never_calls_date_today():
    """A `date.today()` in the gate turns the 9/3 demo into 8/4 on 2026-10-31.

    Asserted structurally rather than by mocking the clock: mocking proves
    only that today's code path avoids it, while this fails on the next
    person's `date.today()` wherever they put it.
    """
    import grace.steering as steering

    tree = ast.parse(inspect.getsource(steering))
    offenders = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"today", "now", "utcnow"}
    ]
    assert not offenders, offenders
    assert "today" in inspect.signature(AuthorityGate.__init__).parameters


async def test_reason_selection_does_not_rely_on_reason_order():
    """Reason order is not a contract (Task 3). A multi-problem case must
    surface every reason, so the Interrupt text cannot be `reasons[0]`."""
    from dataclasses import replace

    cases = load_fixture_cases()
    # c-010 already misses a document; add a source conflict so there are two.
    cases = [
        replace(c, source_conflicts=("wage record disagrees with application",))
        if c.case_id == "c-010"
        else c
        for c in cases
    ]
    gate = AuthorityGate(InMemoryCaseStore(cases), "c-010", TODAY)
    await _observe_all_reads(gate)
    action = await gate.steer_before_tool(
        agent=None, tool_use={"name": "submit_renewal", "input": {}}
    )
    assert isinstance(action, Interrupt)
    assert "proof_of_residency" in action.reason
    assert "wage record" in action.reason


# ---------------------------------------------------------------------------
# End-to-end: the gate wired onto a real Agent, driven through the real tool
# executor. Everything above tests `steer_before_tool` in isolation, which
# proves the decision but not the enforcement — the SDK could return a
# perfectly correct action and still run the tool. These drive the actual
# `ToolExecutor._stream` path, so they fail if the plumbing is wrong even when
# every unit test passes. No model is involved: `model=None` is fine because
# the executor is invoked directly rather than through a model turn.
# ---------------------------------------------------------------------------


def _agent(store, case_id, *, gate=None, ledger=None):
    from strands import Agent

    from grace.tools.action import TranscriptChannel, make_action_tools
    from grace.tools.read import make_read_tools

    tools = [
        *make_read_tools(store, case_id, TODAY),
        *make_action_tools(store, case_id, TranscriptChannel()),
    ]
    return Agent(
        model=None,
        tools=tools,
        plugins=[gate] if gate else [],
        hooks=[ledger] if ledger else [],
        callback_handler=None,
    )


async def _call(agent, name, tool_use_id="tu-0"):
    """Run one tool through the real executor. Returns (events, results)."""
    from strands.tools.executors._executor import ToolExecutor

    results: list = []
    events: list[str] = []
    async for event in ToolExecutor._stream(
        agent, {"name": name, "input": {}, "toolUseId": tool_use_id}, results, {}
    ):
        events.append(type(event).__name__)
    return events, results


async def test_end_to_end_a_clean_case_actually_files_the_renewal():
    store = InMemoryCaseStore(load_fixture_cases())
    gate = AuthorityGate(store, "c-001", TODAY)
    agent = _agent(store, "c-001", gate=gate)
    for name in ("read_case", "check_window", "list_documents"):
        await _call(agent, name, name)
    _, results = await _call(agent, "submit_renewal", "tu-submit")
    assert results[-1]["status"] == "success", results
    assert "Renewal filed" in results[-1]["content"][0]["text"]
    # And the action tool's own ledger row exists, so the filing is auditable.
    assert any(e.kind == "renewal_submitted" for e in store.ledger("c-001"))


async def test_end_to_end_an_escalating_case_never_reaches_the_tool():
    """The claim this whole task exists to make, executed rather than asserted.

    `c-010` is missing `proof_of_residency`. The tool must not run: no
    `renewal_submitted` ledger row, and no success result.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    gate = AuthorityGate(store, "c-010", TODAY)
    agent = _agent(store, "c-010", gate=gate)
    for name in ("read_case", "check_window", "list_documents"):
        await _call(agent, name, name)
    events, results = await _call(agent, "submit_renewal", "tu-submit")
    assert "ToolInterruptEvent" in events, events
    assert not any(e.kind == "renewal_submitted" for e in store.ledger("c-010"))
    assert not any(
        r.get("status") == "success" and "Renewal filed" in str(r) for r in results
    )


async def test_end_to_end_a_guided_call_is_cancelled_with_the_guidance_text():
    """A skipped prerequisite becomes an error result carrying the instruction,
    so the model can correct itself without a human."""
    store = InMemoryCaseStore(load_fixture_cases())
    gate = AuthorityGate(store, "c-001", TODAY)
    agent = _agent(store, "c-001", gate=gate)
    _, results = await _call(agent, "submit_renewal", "tu-submit")
    assert results[-1]["status"] == "error"
    text = results[-1]["content"][0]["text"]
    assert "list_documents" in text
    assert not any(e.kind == "renewal_submitted" for e in store.ledger("c-001"))


async def test_end_to_end_without_the_gate_the_same_call_succeeds():
    """The control case. Proves the block above comes from the gate and not
    from some unrelated reason the tool would have failed anyway."""
    store = InMemoryCaseStore(load_fixture_cases())
    agent = _agent(store, "c-010")  # no gate
    _, results = await _call(agent, "submit_renewal", "tu-submit")
    assert results[-1]["status"] == "success", results
    assert any(e.kind == "renewal_submitted" for e in store.ledger("c-010"))


async def test_interrupt_path_leaves_an_unpaired_tool_call_in_the_ledger():
    """A known asymmetry, pinned so Task 8's evals are written against it.

    On the `Guide` path the SDK builds a synthetic error `ToolResult` and fires
    `AfterToolCallEvent`, so the ledger gets `tool_call` + `tool_result`. On
    the `Interrupt` path it yields `ToolInterruptEvent` and returns *before*
    the after-hook, so the ledger gets `tool_call` with no result at all.

    An eval that treats an unpaired `tool_call` as "a tool ran and was not
    logged" would therefore read every escalation as a logging failure. It is
    the opposite: the tool did not run. This is SDK behaviour, not a choice
    made here, so it is pinned rather than worked around.
    """
    from grace.ledger import LedgerHook

    store = InMemoryCaseStore(load_fixture_cases())
    gate = AuthorityGate(store, "c-010", TODAY)
    agent = _agent(store, "c-010", gate=gate, ledger=LedgerHook(store, "c-010"))
    for name in ("read_case", "check_window", "list_documents"):
        await _call(agent, name, name)
    await _call(agent, "submit_renewal", "tu-submit")

    rows = [(e.kind, e.detail.get("tool")) for e in store.ledger("c-010")]
    assert ("tool_call", "submit_renewal") in rows
    assert ("tool_result", "submit_renewal") not in rows
    # Every read, by contrast, is paired.
    for name in ("read_case", "check_window", "list_documents"):
        assert ("tool_call", name) in rows
        assert ("tool_result", name) in rows


async def test_guide_path_pairs_its_ledger_rows():
    """The other half of the asymmetry above."""
    from grace.ledger import LedgerHook

    store = InMemoryCaseStore(load_fixture_cases())
    gate = AuthorityGate(store, "c-001", TODAY)
    agent = _agent(store, "c-001", gate=gate, ledger=LedgerHook(store, "c-001"))
    await _call(agent, "submit_renewal", "tu-submit")
    rows = [(e.kind, e.detail.get("tool"), e.detail.get("status")) for e in store.ledger("c-001")]
    assert rows == [
        ("tool_call", "submit_renewal", None),
        ("tool_result", "submit_renewal", "error"),
    ]


async def test_the_ledger_and_the_gate_agree_on_what_was_seen():
    """`_seen` (gate, in-memory) and the ledger (durable) must not disagree
    about which reads happened.

    They are separate mechanisms updated from separate hooks, so nothing
    structural keeps them in step. If they ever diverge, the audit trail says
    one thing and the authority decision rested on another — and the ledger is
    what Task 8's evals read. Both are populated from the same
    `BeforeToolCallEvent`, so agreement is expected; this asserts it rather
    than assuming it.
    """
    from grace.ledger import LedgerHook

    store = InMemoryCaseStore(load_fixture_cases())
    gate = AuthorityGate(store, "c-001", TODAY)
    agent = _agent(store, "c-001", gate=gate, ledger=LedgerHook(store, "c-001"))
    for name in ("read_case", "check_window", "list_documents"):
        await _call(agent, name, name)
    logged_reads = {
        e.detail["tool"]
        for e in store.ledger("c-001")
        if e.kind == "tool_call" and e.detail["tool"] not in ACTION_TOOLS
    }
    assert gate._seen == logged_reads == ALL_READS
