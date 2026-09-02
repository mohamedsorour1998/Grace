"""The graph spine and the sweep.

Two things get tested hard here, and they are not the same thing:

1. `make_needs_deliberation` — a per-case predicate factory Task 7 wires into
   a conditional edge. It has no caller today, so nothing but these tests
   constrains it.
2. The interrupt-handling loop in `sweep` — where the escalation boundary
   either holds end-to-end or silently does not. Two independent fail-open
   bugs were found here in review (see `test_sweep_detects_an_interrupt_via_status`
   and `test_a_truthy_interrupt_response_would_approve_a_gated_tool`), so the
   loop is tested against fakes that reproduce the real SDK's shapes rather
   than only against a live Bedrock run whose cost keeps it out of CI.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import pytest

from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.graph import build_case_graph, make_needs_deliberation
from grace.rules.pack import RequiredDocument
from grace.run import SweepReport, main, sweep
from grace.tools.action import TranscriptChannel

TODAY = date(2026, 10, 1)

# The demo's central claim, as data. If either of these changes, the README,
# the eval suite, and the CloudWatch alarm threshold all change with it.
MUST_ESCALATE = ("c-010", "c-011", "c-012")
EXPECTED_ACTED = 9


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def test_graph_builds_with_the_expected_nodes():
    store = InMemoryCaseStore(load_fixture_cases())
    graph = build_case_graph(store, "c-001", TODAY, TranscriptChannel())
    node_ids = set(graph.nodes.keys()) if hasattr(graph, "nodes") else set()
    assert {"intake", "documents", "decide"} <= node_ids


def test_the_spine_is_exactly_three_nodes_until_task_7():
    """`deliberate` does not exist yet. Asserting the node set exactly means
    adding the swarm has to come with a deliberate test change, rather than
    silently satisfying a `<=` assertion that was never about the swarm."""
    store = InMemoryCaseStore(load_fixture_cases())
    graph = build_case_graph(store, "c-001", TODAY, TranscriptChannel())
    assert set(graph.nodes.keys()) == {"intake", "documents", "decide"}


def test_only_the_decide_node_can_act():
    """Capability absence, at the node level.

    `intake` and `documents` are read-only by construction: they never receive
    an action tool at all, so no prompt reaching them can file a renewal. The
    gate protects `decide`; absence protects the other two, and absence is the
    stronger layer (CLAUDE.md, layer 1). A future edit that hands
    `[*read_tools, *action_tools]` to every node would leave every gate test
    still passing, so this is asserted structurally.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    graph = build_case_graph(store, "c-001", TODAY, TranscriptChannel())
    for node_id in ("intake", "documents"):
        names = {t for t in graph.nodes[node_id].executor.tool_names}
        assert names == {"read_case", "check_window", "list_documents"}, node_id
    decide = {t for t in graph.nodes["decide"].executor.tool_names}
    assert "submit_renewal" in decide
    assert "escalate_to_caseworker" in decide


def _plugins(graph, node_id):
    """The plugins attached to a node's Agent.

    Reaches through `_plugin_registry._plugins` — a dict keyed by plugin name,
    not a list — because 1.54.0 exposes no public accessor. Verified by
    introspection, not taken from the docs. Indexing the private attribute
    directly means an upgrade that renames it fails these tests loudly, rather
    than a `getattr(..., [])` default quietly asserting over nothing.
    """
    return list(graph.nodes[node_id].executor._plugin_registry._plugins.values())


def _gate(graph, node_id="decide"):
    return next(
        p for p in _plugins(graph, node_id) if type(p).__name__ == "AuthorityGate"
    )


def test_only_the_decide_node_carries_the_gate_and_the_ledger():
    """The gate belongs where the action tools are.

    Read-only nodes need neither, and attaching a second `AuthorityGate` to
    them would give the `decide` gate's `_seen` set a sibling that observes
    different reads — two gates disagreeing about what happened.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    graph = build_case_graph(store, "c-001", TODAY, TranscriptChannel())

    def names(node_id):
        return {type(p).__name__ for p in _plugins(graph, node_id)}

    assert "AuthorityGate" in names("decide")
    for node_id in ("intake", "documents"):
        assert "AuthorityGate" not in names(node_id), node_id

    # The ledger is a HookProvider, not a plugin — two independent constructor
    # parameters, and swapping them silently attaches nothing. Callbacks are
    # wrapped in `_CallbackEntry`, so the bound method is on `.callback`.
    def hook_owners(node_id):
        registry = graph.nodes[node_id].executor.hooks
        return {
            type(entry.callback.__self__).__name__
            for entries in registry._registered_callbacks.values()
            for entry in entries
            if hasattr(entry.callback, "__self__")
        }

    assert "LedgerHook" in hook_owners("decide")
    for node_id in ("intake", "documents"):
        assert "LedgerHook" not in hook_owners(node_id), node_id


def test_each_case_gets_its_own_gate_and_ledger():
    """One graph per case, and nothing shared between them.

    `AuthorityGate._seen` and the ledger are both per-case. A cached or
    module-level gate would let reads observed on one household satisfy
    another household's prerequisites — the exact cross-household leak the
    no-argument tool design exists to prevent, reintroduced one layer up.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    a = build_case_graph(store, "c-001", TODAY, TranscriptChannel())
    b = build_case_graph(store, "c-002", TODAY, TranscriptChannel())
    gate_a = _gate(a)
    gate_b = _gate(b)
    assert gate_a is not gate_b
    assert gate_a._case_id == "c-001"
    assert gate_b._case_id == "c-002"
    gate_a._seen.add("read_case")
    assert gate_b._seen == set()


def test_the_decide_node_executes_tools_sequentially():
    """A correctness requirement, not a performance preference.

    The default executor is concurrent. This model routinely asks for
    read_case, check_window, list_documents and submit_renewal in one turn;
    run concurrently, `submit_renewal` reaches the gate before the reads have
    registered in `AuthorityGate._seen`, so the gate Guides a correctly-ordered
    call and whether the model retries is luck. Observed directly on real runs:
    the same clean fixture case filed on one sweep and not the next, moving the
    split from 9/3 to 8/4 with no error anywhere.

    Also relevant to the gate: `SequentialToolExecutor` stops at the first
    interrupt instead of running the rest of the batch.
    """
    from strands.tools.executors import SequentialToolExecutor

    store = InMemoryCaseStore(load_fixture_cases())
    graph = build_case_graph(store, "c-001", TODAY, TranscriptChannel())
    executor = graph.nodes["decide"].executor.tool_executor
    assert isinstance(executor, SequentialToolExecutor), type(executor)


def test_the_decide_prompt_tells_the_model_to_trust_list_documents():
    """The document verdict is computed by a tool, and the prompt must not
    invite the model to redo it.

    Before `document_problems` existed, `list_documents` reported raw dates and
    the model derived staleness itself — wrongly, on two of nine clean fixture
    cases in a real run, then texting those families that a current document
    had expired. The tool now states CURRENT/STALE/EXPIRED, and the prompt says
    to trust it.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    graph = build_case_graph(store, "c-001", TODAY, TranscriptChannel())
    prompt = graph.nodes["decide"].executor.system_prompt
    assert "Trust list_documents" in prompt
    assert "do not recompute" in prompt


def test_list_documents_states_the_verdict_rather_than_the_arithmetic():
    """What the model actually reads. `max_age_days` must not appear as a raw
    number for the model to subtract with."""
    from grace.tools.read import make_read_tools

    store = InMemoryCaseStore(load_fixture_cases())
    tools = {t.tool_name: t for t in make_read_tools(store, "c-002", TODAY)}
    out = tools["list_documents"]._tool_func()
    # Every required document on this clean case is current, and says so.
    assert out.count("CURRENT") == 3
    assert "STALE" not in out and "EXPIRED" not in out


def test_the_gates_seen_set_survives_an_in_process_resume(monkeypatch):
    """CLAUDE.md flagged this as untested after Task 5. It is fine — here is why.

    `AuthorityGate._seen` is per-instance and in-memory, so the worry was that
    resuming an interrupt would meet a freshly-constructed gate whose `_seen` is
    empty while the ledger already shows the prior reads, making the gate Guide
    reads that already happened.

    It does not happen on the resume path this sweep uses. `Graph._build_node_input`
    restores `messages`, `state`, `_interrupt_state`, and `_model_state` on the
    node's executor, but never touches the plugin registry — the `Agent` object,
    and therefore the same `AuthorityGate` instance, is reused. Confirmed against
    a real graph resume: the gate is the identical object and `_seen` still holds
    all three reads.

    This is asserted structurally rather than left as a note, because the
    conclusion depends on SDK internals that could change in an upgrade. It
    remains true only for an in-process resume; a resume in a *new* process
    (Plan 2, via a session manager) rebuilds the graph and would start with an
    empty `_seen`. That is still not a fail-open — an empty `_seen` makes the
    gate stricter, not looser — but it would Guide once before proceeding.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    graph = build_case_graph(store, "c-011", TODAY, TranscriptChannel())
    gate = _gate(graph)
    gate._seen.update({"read_case", "check_window", "list_documents"})

    # What the SDK restores on resume, applied directly to the node executor.
    node = graph.nodes["decide"]
    node.executor.messages = []
    assert _gate(graph) is gate
    assert gate._seen == {"read_case", "check_window", "list_documents"}

    restore_source = inspect.getsource(type(graph)._build_node_input)
    assert "_plugin_registry" not in restore_source
    assert "node.executor.messages" in restore_source


def test_no_node_has_its_own_session_manager():
    """Strands raises `ValueError` if an agent inside a Graph carries one —
    only the orchestrator may. Asserted here so Plan 2's AgentCore wiring
    cannot add one to a node without a test noticing."""
    store = InMemoryCaseStore(load_fixture_cases())
    graph = build_case_graph(store, "c-001", TODAY, TranscriptChannel())
    for node_id, node in graph.nodes.items():
        assert getattr(node.executor, "_session_manager", None) is None, node_id


def _calls_date_today(func) -> bool:
    """True if `func`'s body actually calls `date.today()`.

    Parsed rather than grepped: both `graph.py` and `run.py` *document* why
    `date.today()` must not appear, so a substring check on the source matches
    the comment warning against it and fails on correct code. A test that can
    only pass by deleting the explanation is a bad test.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "today":
                return True
    return False


def test_the_graph_binds_the_passed_date_not_todays():
    """A `date.today()` anywhere in graph construction turns the 9/3 demo into
    8/4 from 2026-10-31, when fixture c-002's grace period ends."""
    assert not _calls_date_today(build_case_graph)
    store = InMemoryCaseStore(load_fixture_cases())
    graph = build_case_graph(store, "c-001", date(2030, 1, 1), TranscriptChannel())
    assert _gate(graph)._today == date(2030, 1, 1)


def test_every_node_runs_a_nova_model():
    """Hard rule 1, checked on the built object rather than on the source."""
    store = InMemoryCaseStore(load_fixture_cases())
    graph = build_case_graph(store, "c-001", TODAY, TranscriptChannel())
    for node_id, node in graph.nodes.items():
        model_id = node.executor.model.get_config()["model_id"]
        assert "amazon.nova" in model_id, (node_id, model_id)


def test_no_node_uses_the_banned_model():
    """`nova-lite-v1:0` filed a renewal it was told not to file (see
    grace/models.py). `decide` is the gated role, so this matters most there,
    but no node may use it."""
    from grace.models import BANNED_MODEL_IDS

    store = InMemoryCaseStore(load_fixture_cases())
    graph = build_case_graph(store, "c-001", TODAY, TranscriptChannel())
    for node_id, node in graph.nodes.items():
        assert node.executor.model.get_config()["model_id"] not in BANNED_MODEL_IDS, node_id


# ---------------------------------------------------------------------------
# needs_deliberation — Task 7 wires this into a conditional edge; nothing
# calls it yet, so these tests are its only constraint.
#
# `make_needs_deliberation` is a factory bound to (store, case_id, today),
# matching every other per-case component in this file. An earlier version
# matched substrings in the `documents` node's free-text summary and was
# wrong: that node only ever calls `list_documents`, so its prose can never
# mention income, size, or a source conflict. Measured against the real
# fixtures, that version fired on c-010 (a missing document, needing no
# deliberation) and stayed silent on c-011/c-012 (the two cases a
# deliberation swarm exists for). This version re-runs evaluate() directly.
# ---------------------------------------------------------------------------


def _needs_deliberation_for(case_id: str, cases=None):
    store = InMemoryCaseStore(cases if cases is not None else load_fixture_cases())
    return make_needs_deliberation(store, case_id, TODAY)(None)


def test_needs_deliberation_is_false_for_a_clean_case():
    assert _needs_deliberation_for("c-001") is False


@pytest.mark.parametrize("case_id", ["c-011", "c-012"])
def test_needs_deliberation_is_true_for_the_ambiguous_fixtures(case_id):
    """c-011 (30% income change) and c-012 (source conflict) are exactly the
    two cases a deliberation swarm exists for."""
    assert _needs_deliberation_for(case_id) is True


def test_needs_deliberation_is_false_for_the_missing_document_fixture():
    """c-010 must escalate, but not through the swarm: no amount of
    deliberation resolves "the document is not on file". Routing it through
    three extra model calls to reach a foregone conclusion would burn cost for
    nothing — the case needs a document request, not an argument."""
    assert _needs_deliberation_for("c-010") is False


def test_needs_deliberation_matches_evaluate_across_every_fixture():
    """The predicate's routing, checked against the real gate on all twelve
    fixtures rather than three hand-picked ones — the property under test is
    "agrees with evaluate() on which reason codes need deliberation", and that
    should hold for every case, not just the ones named in the demo."""
    from grace.authority import evaluate
    from grace.rules.pack import load_pack

    store = InMemoryCaseStore(load_fixture_cases())
    for case in load_fixture_cases():
        pack = load_pack(case.program, case.state)
        result = evaluate(case, TODAY, pack)
        codes = {r.code for r in result.reasons}
        expected = bool(codes & {"material_income_change", "household_size_change", "source_conflict"})
        actual = make_needs_deliberation(store, case.case_id, TODAY)(None)
        assert actual == expected, (case.case_id, codes)


def test_needs_deliberation_fails_closed_on_an_unknown_case():
    """`store.get` raises `KeyError` for a case id that does not exist —
    deliberate rather than assume clean on a case that cannot even be read."""
    store = InMemoryCaseStore(load_fixture_cases())
    assert make_needs_deliberation(store, "c-does-not-exist", TODAY)(None) is True


def test_needs_deliberation_fails_closed_on_an_unloadable_pack():
    """A program/state with no rule pack raises `InvalidRulePack` inside
    `load_pack` — deliberate rather than assume clean on unverifiable data."""
    broken = replace(load_fixture_cases()[0], program="no_such_program", state="ZZ")
    store = InMemoryCaseStore([broken])
    assert make_needs_deliberation(store, broken.case_id, TODAY)(None) is True


def test_needs_deliberation_fails_closed_when_evaluate_itself_raises():
    """A structurally invalid pack — built directly, bypassing `load_pack`'s
    own validation, per Task 3/5's standing warning that `evaluate` can raise
    `ValueError`/`TypeError`/`OverflowError` from exactly this shape — must
    still deliberate, not crash the edge condition. An exception escaping a
    graph edge condition is not "deliberate", it is an unhandled failure
    mid-graph, the same class of bug Task 4 found in `check_window`.
    """
    import grace.graph as graph_module
    from grace.rules.pack import RulePack

    case = load_fixture_cases()[0]
    store = InMemoryCaseStore([case])

    def exploding_load_pack(program, state):
        return RulePack(
            program=program,
            state=state,
            version="test",
            certification_period_months=12,
            window_opens_days_before_end=-999,  # forces renewal_window to raise
            grace_period_days_after_end=1,
            required_documents=(RequiredDocument(doc_id="x", max_age_days=1),),
            income_change_immaterial_pct=5.0,
        )

    original = graph_module.load_pack
    graph_module.load_pack = exploding_load_pack
    try:
        assert make_needs_deliberation(store, case.case_id, TODAY)(None) is True
    finally:
        graph_module.load_pack = original


# ---------------------------------------------------------------------------
# The sweep's interrupt loop. Fakes reproduce the real SDK shapes: a live
# Bedrock sweep costs money and cannot run in CI, so the fail-open bugs below
# have to be catchable without one.
# ---------------------------------------------------------------------------


@dataclass
class FakeInterrupt:
    """Shaped like `strands.interrupt.Interrupt` (id/name/reason/response)."""

    id: str = "i-1"
    name: str = "steering_input_submit_renewal"
    # The real steering handler passes a dict, not a string:
    # `event.interrupt(name=..., reason={"message": action.reason})`.
    reason: Any = field(default_factory=lambda: {"message": "A caseworker must decide. x"})
    response: Any = None


@dataclass
class FakeResult:
    """Shaped like `GraphResult`: `status`/`interrupts`, and NO `stop_reason`."""

    status: Any
    interrupts: list = field(default_factory=list)


class FakeGraph:
    """Returns a queued sequence of results, recording each call's task.

    On a `COMPLETED` result it writes the `renewal_submitted` ledger row a real
    `submit_renewal` would, because `sweep` classifies from the ledger rather
    than from the graph's status — a fake that completes without filing is a
    case where Grace did nothing, and the sweep is right to escalate it. That
    behaviour is asserted directly in
    `test_a_clean_case_that_filed_nothing_is_escalated_not_acted`.
    """

    def __init__(self, results, store=None, case_id=None):
        self._results = list(results)
        self._store = store
        self._case_id = case_id
        self.calls: list[Any] = []

    def __call__(self, task, *args, **kwargs):
        self.calls.append(task)
        result = self._results.pop(0)
        from strands.multiagent.base import Status

        if result.status == Status.COMPLETED and self._store is not None:
            _file_renewal(self._store, self._case_id)
        return result


def _file_renewal(store, case_id):
    """Write the ledger row a successful `submit_renewal` writes."""
    from datetime import datetime, timezone

    from grace.cases.models import LedgerEntry

    store.append_ledger(
        LedgerEntry(
            case_id=case_id,
            at=datetime.now(timezone.utc),
            kind="renewal_submitted",
            detail={"program": "medicaid"},
        )
    )


def _interrupted(reason="A caseworker must decide. missing_document: x"):
    from strands.multiagent.base import Status

    return FakeResult(
        status=Status.INTERRUPTED,
        interrupts=[FakeInterrupt(reason={"message": reason})],
    )


def _completed():
    from strands.multiagent.base import Status

    return FakeResult(status=Status.COMPLETED)


def _sweep_with(monkeypatch, graphs: dict[str, FakeGraph], **kwargs) -> SweepReport:
    """Run `sweep` against per-case fake graphs.

    Binds each `FakeGraph` to the real store so a `COMPLETED` result writes the
    `renewal_submitted` ledger row a real filing would — `sweep` reads the
    ledger to decide what it may report as handled, so an unbound fake would
    make every case look like one where Grace did nothing.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    for case_id, graph in graphs.items():
        if isinstance(graph, FakeGraph):
            graph._store = store
            graph._case_id = case_id
    import grace.run as run

    monkeypatch.setattr(run, "build_case_graph", lambda s, cid, t, ch: graphs[cid])
    return run.sweep(store, TODAY, TranscriptChannel(), **kwargs)


def test_sweep_detects_an_interrupt_via_status_not_stop_reason(monkeypatch):
    """`GraphResult` has no `stop_reason` field at all.

    The plan checked `getattr(result, "stop_reason", None) == "interrupt"`,
    which is always `False` on a `GraphResult`, so the escalation branch never
    ran and its reason never reached the report. This fake has no `stop_reason`
    attribute, exactly like the real type, so a regression to `getattr` fails
    here rather than in a recorded demo.

    Asserted on a *clean* fixture case (`c-005`), so it isolates the interrupt
    path from the gate: the gate would escalate c-010/011/012 anyway, which
    would let a broken interrupt check still look right.
    """
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    graphs["c-005"] = FakeGraph([_interrupted("a gate reason only an interrupt can carry")])
    report = _sweep_with(monkeypatch, graphs, auto_decide="escalate")
    assert "c-005" not in report.acted
    assert dict(report.escalated)["c-005"] == "a gate reason only an interrupt can carry"
    # The nine clean cases minus the one this test interrupted.
    assert len(report.acted) == EXPECTED_ACTED - 1


def test_the_fake_result_really_has_no_stop_reason():
    """Guards the guard: if `FakeResult` grew a `stop_reason`, the test above
    would pass against a `getattr(result, "stop_reason", ...)` implementation
    and stop testing anything."""
    import dataclasses

    from strands.multiagent import GraphResult

    assert not hasattr(_completed(), "stop_reason")
    assert "stop_reason" not in {f.name for f in dataclasses.fields(GraphResult)}


# ---------------------------------------------------------------------------
# The 9/3 split. These are the demo's central claim, and the reason `sweep`
# classifies from the deterministic gate and the ledger rather than from
# whether an SDK interrupt happened to fire.
# ---------------------------------------------------------------------------


def test_the_split_is_nine_three_regardless_of_which_tool_the_model_picked(monkeypatch):
    """The bug this classification exists to prevent, reproduced exactly.

    Observed on a real Bedrock run: on `c-010` (missing `proof_of_residency`)
    the model called `send_family_message` rather than `submit_renewal`. The
    gate *correctly* allowed that — chasing one missing document by SMS is what
    Grace exists to do — so no interrupt fired, and a household with an
    incomplete file was reported as handled autonomously. The sweep printed
    10/2.

    Every graph here completes without interrupting, i.e. the model never tried
    anything the gate refused. The split must still be 9/3, because it is a
    property of the twelve case records, not of the model's tool choice.
    """
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    report = _sweep_with(monkeypatch, graphs, auto_decide="escalate")
    assert len(report.acted) == EXPECTED_ACTED
    assert sorted(cid for cid, _ in report.escalated) == list(MUST_ESCALATE)
    assert report.errors == ()


def test_each_escalation_names_its_own_distinct_reason(monkeypatch):
    """Three cases, three different reasons — the demo's actual content.

    A report where all three escalate for the same reason would still count
    3, so the counts alone do not establish the claim.
    """
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    report = _sweep_with(monkeypatch, graphs, auto_decide="escalate")
    reasons = dict(report.escalated)
    assert "proof_of_residency" in reasons["c-010"]
    assert "missing_document" in reasons["c-010"]
    assert "material_income_change" in reasons["c-011"]
    assert "30.0%" in reasons["c-011"]
    assert "source_conflict" in reasons["c-012"]
    assert "wage record" in reasons["c-012"]


def test_a_clean_case_that_filed_nothing_is_escalated_not_acted(monkeypatch):
    """`acted` is a claim that Grace handled the case. Hard rule 6 says never
    make such a claim without tool confirmation, and the ledger is the only
    confirmation there is — a graph that completed while doing nothing has not
    renewed anyone's coverage.

    This fake is deliberately unbound from the store, so it completes without
    writing `renewal_submitted`.
    """
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    store = InMemoryCaseStore(load_fixture_cases())
    import grace.run as run

    # Bind every fake except c-001, so only that case files nothing.
    for case_id, graph in graphs.items():
        if case_id != "c-001":
            graph._store, graph._case_id = store, case_id
    monkeypatch.setattr(run, "build_case_graph", lambda s, cid, t, ch: graphs[cid])
    report = run.sweep(store, TODAY, TranscriptChannel(), auto_decide="escalate")

    assert "c-001" not in report.acted
    assert "no renewal was filed" in dict(report.escalated)["c-001"]
    assert len(report.acted) == EXPECTED_ACTED - 1


def test_an_escalating_case_is_never_reported_as_acted_even_if_it_filed(monkeypatch):
    """Belt and braces on the gate's own verdict.

    If a renewal somehow reached the ledger for `c-010` — a gate bypass, a
    future tool that skips steering — the sweep must still not call that case
    handled. The gate's verdict, not the ledger, decides whether a human was
    needed; the ledger only decides whether Grace may claim it acted.
    """
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    store = InMemoryCaseStore(load_fixture_cases())
    _file_renewal(store, "c-010")
    import grace.run as run

    for case_id, graph in graphs.items():
        graph._store, graph._case_id = store, case_id
    monkeypatch.setattr(run, "build_case_graph", lambda s, cid, t, ch: graphs[cid])
    report = run.sweep(store, TODAY, TranscriptChannel(), auto_decide="escalate")

    assert "c-010" not in report.acted
    assert "c-010" in dict(report.escalated)


def test_an_interrupt_reason_beats_the_gates_wording(monkeypatch):
    """When both are available, the caseworker reads the interrupt's reason.

    It is the more specific of the two: it names the tool call that was refused
    at the moment it was refused, while the gate's own text is a restatement of
    the case record. Both say the same thing on the fixtures; on a real case
    they need not.
    """
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    graphs["c-011"] = FakeGraph([_interrupted("the interrupt said this")])
    report = _sweep_with(monkeypatch, graphs, auto_decide="escalate")
    assert dict(report.escalated)["c-011"] == "the interrupt said this"


def test_an_escalation_says_when_the_family_was_already_messaged(monkeypatch):
    """`c-010` is the document-only case: the gate lets Grace chase the missing
    document by SMS, and the case still goes to a human because the file is
    incomplete. The caseworker must be told the family has already been asked,
    or they ask again — and a duplicate request is exactly the confusion that
    makes families give up on paperwork.
    """
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    store = InMemoryCaseStore(load_fixture_cases())
    import grace.run as run
    from datetime import datetime, timezone

    from grace.cases.models import LedgerEntry

    for case_id, graph in graphs.items():
        graph._store, graph._case_id = store, case_id
    store.append_ledger(
        LedgerEntry(
            case_id="c-010",
            at=datetime.now(timezone.utc),
            kind="family_message_sent",
            detail={"ref": "recorded:1", "body": "please send proof of residency"},
        )
    )
    monkeypatch.setattr(run, "build_case_graph", lambda s, cid, t, ch: graphs[cid])
    report = run.sweep(store, TODAY, TranscriptChannel(), auto_decide="escalate")

    assert "already messaged the family" in dict(report.escalated)["c-010"]
    # And a case with no outreach does not claim any.
    assert "already messaged" not in dict(report.escalated)["c-011"]


def test_a_case_whose_pack_will_not_load_escalates_rather_than_acting(monkeypatch):
    """Fail closed on an unverifiable case.

    `_gate_reason` catches broadly for the reason Task 3 set out: a pack that
    passes `load_pack`'s own validation can still make `evaluate` raise
    `ValueError`, `TypeError`, or `OverflowError`. None of those may become an
    autonomous filing.
    """
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    import grace.run as run

    def exploding_load_pack(program, state):
        if program == "snap":
            raise OverflowError("date value out of range")
        return run.load_pack(program, state)

    monkeypatch.setattr(run, "load_pack", exploding_load_pack)
    report = _sweep_with(monkeypatch, graphs, auto_decide="escalate")
    # Every SNAP case escalates; no SNAP case is reported as handled.
    snap_ids = {c.case_id for c in load_fixture_cases() if c.program == "snap"}
    assert snap_ids.isdisjoint(report.acted)
    for cid in snap_ids:
        assert "Verification error" in dict(report.escalated)[cid]


def test_an_escalated_case_is_never_also_counted_as_acted(monkeypatch):
    """`acted` and `escalated` must partition the caseload.

    The plan used a `while/else`, whose `else` runs when the loop condition is
    first evaluated false — correct here, but only by accident of the loop's
    `break` placement. A case appearing in both lists would make "9 handled
    alone, 3 escalated" add to more than 12 while every count looked right in
    isolation.
    """
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    for cid in MUST_ESCALATE:
        graphs[cid] = FakeGraph([_interrupted()])
    report = _sweep_with(monkeypatch, graphs, auto_decide="escalate")
    assert set(report.acted).isdisjoint({cid for cid, _ in report.escalated})
    assert len(report.acted) + len(report.escalated) + len(report.errors) == 12


def test_a_case_is_escalated_at_most_once(monkeypatch):
    """A resume that interrupts again must not add a second row for the case.

    With `--auto escalate` the loop stops after the first answer, but a
    caseworker-supplied answer can legitimately resume into another interrupt.
    Two rows for one case would report 4 escalations out of 12 cases.
    """
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    graphs["c-010"] = FakeGraph([_interrupted("first"), _interrupted("second"), _completed()])
    report = _sweep_with(monkeypatch, graphs, auto_decide="approve")
    assert [cid for cid, _ in report.escalated].count("c-010") == 1


def test_sweep_does_not_resume_when_the_answer_is_to_escalate(monkeypatch):
    """Answering "escalate" means a human takes the case — Grace must not call
    the graph again to see what happens. Resuming with a truthy response
    *approves* the gated tool (see the next test), so a stray resume here is
    the difference between escalating and silently filing."""
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    graphs["c-010"] = FakeGraph([_interrupted()])
    _sweep_with(monkeypatch, graphs, auto_decide="escalate")
    assert len(graphs["c-010"].calls) == 1, graphs["c-010"].calls


async def test_a_truthy_interrupt_response_would_approve_a_gated_tool():
    """The second fail-open bug in the plan's resume loop, pinned as SDK fact.

    `SteeringHandler._handle_tool_steering_action` treats the resume response
    as a boolean: `can_proceed = event.interrupt(...)`, and it cancels the tool
    only `if not can_proceed`. The string `"escalate"` is truthy, so the plan's
    `graph([{"interruptResponse": {..., "response": "escalate"}}])` resume
    *approves* the very call the gate just blocked — on `c-010` that files a
    renewal for a household missing a required document, while the sweep report
    still lists the case as escalated.

    This is why `sweep` never resumes with a decision that means "escalate".
    Verified against the real executor rather than reasoned about.
    """
    from strands import Agent
    from strands.tools.executors._executor import ToolExecutor

    from grace.steering import AuthorityGate
    from grace.tools.action import make_action_tools
    from grace.tools.read import make_read_tools

    store = InMemoryCaseStore(load_fixture_cases())
    agent = Agent(
        model=None,
        tools=[
            *make_read_tools(store, "c-010", TODAY),
            *make_action_tools(store, "c-010", TranscriptChannel()),
        ],
        plugins=[AuthorityGate(store, "c-010", TODAY)],
        callback_handler=None,
    )

    async def call(name, tuid):
        results: list = []
        async for _ in ToolExecutor._stream(
            agent, {"name": name, "input": {}, "toolUseId": tuid}, results, {}
        ):
            pass
        return results

    for name in ("read_case", "check_window", "list_documents"):
        await call(name, name)
    await call("submit_renewal", "tu-submit")
    assert not any(e.kind == "renewal_submitted" for e in store.ledger("c-010"))

    # Resume with a truthy response, exactly as the plan's loop would.
    for interrupt in agent._interrupt_state.interrupts.values():
        interrupt.response = "escalate"
    results = await call("submit_renewal", "tu-submit")

    assert results[-1]["status"] == "success"
    assert any(e.kind == "renewal_submitted" for e in store.ledger("c-010"))


def test_sweep_records_the_interrupt_reason_from_a_dict(monkeypatch):
    """The steering handler wraps the reason: `reason={"message": ...}`.

    `str(reason)` on that gives `{'message': '...'}`, so the caseworker's
    escalation line would be a Python dict repr. The gate's own text — which
    names the failing condition — must survive to the report, because that
    text *is* the demo.
    """
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    graphs["c-010"] = FakeGraph(
        [_interrupted("A caseworker must decide. missing_document: proof_of_residency is not on file")]
    )
    report = _sweep_with(monkeypatch, graphs, auto_decide="escalate")
    reason = dict(report.escalated)["c-010"]
    assert "proof_of_residency" in reason
    assert "{" not in reason and "'message'" not in reason


def test_sweep_records_a_plain_string_reason_too(monkeypatch):
    """A bare-string reason must not be mangled by the dict unwrapping."""
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    graphs["c-011"] = FakeGraph(
        [FakeResult(status=_interrupted().status, interrupts=[FakeInterrupt(reason="plain text")])]
    )
    report = _sweep_with(monkeypatch, graphs, auto_decide="escalate")
    assert dict(report.escalated)["c-011"] == "plain text"


def test_sweep_reports_every_interrupt_reason_not_only_the_first(monkeypatch):
    """One node can raise several interrupts, and reason order is not a
    contract (Task 3). Dropping all but `interrupts[0]` would hide, say, an
    income problem behind a document problem in the caseworker brief."""
    from strands.multiagent.base import Status

    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    graphs["c-012"] = FakeGraph(
        [
            FakeResult(
                status=Status.INTERRUPTED,
                interrupts=[
                    FakeInterrupt(id="i-1", reason={"message": "source_conflict: wage record"}),
                    FakeInterrupt(id="i-2", reason={"message": "material_income_change: +30%"}),
                ],
            )
        ]
    )
    report = _sweep_with(monkeypatch, graphs, auto_decide="escalate")
    reason = dict(report.escalated)["c-012"]
    assert "wage record" in reason
    assert "+30%" in reason


def test_sweep_escalates_a_case_that_interrupts_with_no_interrupt_objects(monkeypatch):
    """`status == INTERRUPTED` with an empty `interrupts` list is a state the
    SDK should not produce. The plan `break`s out of the loop there, which
    falls through to... nothing, leaving the case in neither list — it silently
    vanishes from a report whose whole purpose is that every case is accounted
    for. Fail closed: an unexplained interrupt is an escalation."""
    from strands.multiagent.base import Status

    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    graphs["c-010"] = FakeGraph([FakeResult(status=Status.INTERRUPTED, interrupts=[])])
    report = _sweep_with(monkeypatch, graphs, auto_decide="escalate")
    assert "c-010" in dict(report.escalated)
    assert "c-010" not in report.acted
    assert len(report.acted) + len(report.escalated) == 12


def test_sweep_escalates_a_failed_graph_rather_than_counting_it_as_acted(monkeypatch):
    """`Status.FAILED` is not `INTERRUPTED` and not success.

    The plan's `while/else` sends anything that is not an interrupt to
    `acted` — including a graph that failed outright, which would report a
    renewal as filed on a case where no tool ran. Hard rule 6: never claim an
    action Grace did not confirm.
    """
    from strands.multiagent.base import Status

    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    graphs["c-005"] = FakeGraph([FakeResult(status=Status.FAILED)])
    report = _sweep_with(monkeypatch, graphs, auto_decide="escalate")
    assert "c-005" not in report.acted
    assert "c-005" in dict(report.escalated) or "c-005" in dict(report.errors)
    assert len(report.acted) + len(report.escalated) + len(report.errors) == 12


def test_sweep_keeps_going_after_one_case_raises(monkeypatch):
    """One unloadable case must not abandon the other eleven families."""

    class Exploding:
        def __call__(self, task, *a, **k):
            raise RuntimeError("bedrock unavailable")

    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    graphs["c-007"] = Exploding()
    report = _sweep_with(monkeypatch, graphs, auto_decide="escalate")
    assert dict(report.errors)["c-007"] == "bedrock unavailable"
    assert "c-007" not in report.acted
    # The other eight clean cases are still handled: c-007 was one of the nine.
    assert len(report.acted) == EXPECTED_ACTED - 1
    assert len(report.acted) + len(report.escalated) + len(report.errors) == 12


def test_sweep_catches_a_bare_exception_not_only_valueerror(monkeypatch):
    """`evaluate` and the graph can raise `TypeError`, `OverflowError`, or a
    `BaseException`-adjacent error from a model call. Task 3 mandated a broad
    catch on `evaluate`'s callers; the sweep is one."""
    for exc in (TypeError("t"), OverflowError("o"), KeyError("k"), RuntimeError("r")):

        class Exploding:
            def __init__(self, e):
                self._e = e

            def __call__(self, task, *a, **k):
                raise self._e

        graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
        graphs["c-003"] = Exploding(exc)
        report = _sweep_with(monkeypatch, graphs, auto_decide="escalate")
        assert "c-003" in dict(report.errors), exc
        assert len(report.acted) == EXPECTED_ACTED - 1, exc


def test_a_case_that_errors_is_never_reported_as_acted(monkeypatch):
    """An error after an interrupt already escalated the case must not produce
    two rows for it either."""

    class InterruptThenExplode:
        def __init__(self):
            self.calls = 0

        def __call__(self, task, *a, **k):
            self.calls += 1
            if self.calls == 1:
                return _interrupted()
            raise RuntimeError("resume failed")

    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    graphs["c-010"] = InterruptThenExplode()
    report = _sweep_with(monkeypatch, graphs, auto_decide="approve")
    all_ids = list(report.acted) + [c for c, _ in report.escalated] + [c for c, _ in report.errors]
    assert all_ids.count("c-010") == 1
    assert len(all_ids) == 12


def test_sweep_passes_the_pinned_date_into_every_graph(monkeypatch):
    """A `date.today()` in the sweep would break the demo on 2026-10-31."""
    seen: list[date] = []
    store = InMemoryCaseStore(load_fixture_cases())
    import grace.run as run

    def fake_build(s, cid, t, ch):
        seen.append(t)
        return FakeGraph([_completed()])

    monkeypatch.setattr(run, "build_case_graph", fake_build)
    run.sweep(store, date(2027, 5, 5), TranscriptChannel(), auto_decide="escalate")
    assert set(seen) == {date(2027, 5, 5)}
    assert not _calls_date_today(run.sweep)


def test_sweep_never_sends_a_null_interrupt_response(monkeypatch):
    """The server refuses a null response (Appendix B.1), and `auto_decide=None`
    means "prompt a human", not "answer with None". An empty typed answer must
    become a real string."""
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    graphs["c-010"] = FakeGraph([_interrupted()])
    import grace.run as run

    monkeypatch.setattr(run, "build_case_graph", lambda s, cid, t, ch: graphs[cid])
    monkeypatch.setattr("builtins.input", lambda *a: "")
    store = InMemoryCaseStore(load_fixture_cases())
    report = run.sweep(store, TODAY, TranscriptChannel())
    assert "c-010" in dict(report.escalated)
    # Blank input defaults to escalate, which does not resume at all.
    assert len(graphs["c-010"].calls) == 1


def test_sweep_resume_payload_matches_the_sdk_shape(monkeypatch):
    """The resume content block is `InterruptResponseContent`:
    `{"interruptResponse": {"interruptId": ..., "response": ...}}`, keyed by
    `interrupt.id` and never by `interrupt.name`."""
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    graphs["c-010"] = FakeGraph([_interrupted(), _completed()])
    _sweep_with(monkeypatch, graphs, auto_decide="approve")
    resume = graphs["c-010"].calls[1]
    assert isinstance(resume, list) and len(resume) == 1
    assert set(resume[0]) == {"interruptResponse"}
    assert resume[0]["interruptResponse"]["interruptId"] == "i-1"
    assert resume[0]["interruptResponse"]["response"] == "approve"


def test_sweep_answers_every_interrupt_on_resume(monkeypatch):
    """Two interrupts, two response blocks. Answering only the first leaves the
    graph paused on the second, and the SDK filters responses by interrupt id —
    so an unanswered interrupt is not resumed, it is stuck."""
    from strands.multiagent.base import Status

    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    graphs["c-010"] = FakeGraph(
        [
            FakeResult(
                status=Status.INTERRUPTED,
                interrupts=[FakeInterrupt(id="i-1"), FakeInterrupt(id="i-2")],
            ),
            _completed(),
        ]
    )
    _sweep_with(monkeypatch, graphs, auto_decide="approve")
    resume = graphs["c-010"].calls[1]
    assert [b["interruptResponse"]["interruptId"] for b in resume] == ["i-1", "i-2"]


def test_sweep_task_names_the_case_and_the_date(monkeypatch):
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    _sweep_with(monkeypatch, graphs, auto_decide="escalate")
    task = graphs["c-001"].calls[0]
    assert "c-001" in task
    assert "2026-10-01" in task


def test_sweep_processes_every_open_case(monkeypatch):
    """No family is skipped: twelve cases in, twelve outcomes out."""
    graphs = {c.case_id: FakeGraph([_completed()]) for c in load_fixture_cases()}
    report = _sweep_with(monkeypatch, graphs, auto_decide="escalate")
    seen = set(report.acted) | {c for c, _ in report.escalated} | {c for c, _ in report.errors}
    assert seen == {c.case_id for c in load_fixture_cases()}
    assert len(report.acted) + len(report.escalated) + len(report.errors) == 12


# ---------------------------------------------------------------------------
# SweepReport
# ---------------------------------------------------------------------------


def test_sweep_report_is_frozen():
    report = SweepReport(acted=("c-001",))
    with pytest.raises(Exception):
        report.acted = ()  # type: ignore[misc]


def test_summary_counts_every_case_and_names_each_escalation():
    report = SweepReport(
        acted=("c-001", "c-002"),
        escalated=(("c-010", "missing proof_of_residency"),),
        errors=(("c-012", "boom"),),
    )
    text = report.summary()
    assert "Swept 4 cases." in text
    assert "Handled autonomously: 2" in text
    assert "Escalated to a human: 1" in text
    assert "proof_of_residency" in text
    assert "c-012" in text and "boom" in text


def test_summary_omits_the_error_line_when_there_are_none():
    assert "Errors" not in SweepReport(acted=("c-001",)).summary()


def test_summary_of_an_empty_report_still_reads_as_a_report():
    assert "Swept 0 cases." in SweepReport().summary()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_defaults_today_to_the_pinned_date(monkeypatch, capsys):
    """`--today` defaults to 2026-10-01 rather than to the wall clock, or the
    demo becomes 8/4 from 2026-10-31."""
    seen: list[date] = []
    import grace.run as run

    def fake_sweep(store, today, channel, auto_decide=None):
        seen.append(today)
        return SweepReport(acted=("c-001",))

    monkeypatch.setattr(run, "sweep", fake_sweep)
    monkeypatch.setattr("sys.argv", ["grace", "sweep"])
    assert main() == 0
    assert seen == [date(2026, 10, 1)]


def test_main_passes_today_and_auto_through(monkeypatch):
    seen: dict[str, Any] = {}
    import grace.run as run

    def fake_sweep(store, today, channel, auto_decide=None):
        seen["today"] = today
        seen["auto"] = auto_decide
        return SweepReport()

    monkeypatch.setattr(run, "sweep", fake_sweep)
    monkeypatch.setattr("sys.argv", ["grace", "sweep", "--today", "2027-01-02", "--auto", "escalate"])
    assert main() == 0
    assert seen == {"today": date(2027, 1, 2), "auto": "escalate"}


def test_main_rejects_an_unparseable_date(monkeypatch, capsys):
    """A bad `--today` must fail loudly. Falling back to `date.today()` would
    silently evaluate every window against the wrong day."""
    monkeypatch.setattr("sys.argv", ["grace", "sweep", "--today", "not-a-date"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code != 0


def test_main_prints_the_family_transcript(monkeypatch, capsys):
    """The transcript is the always-works family channel: SMS is sandboxed with
    zero origination numbers, so the demo shows outreach here or nowhere."""
    import grace.run as run

    def fake_sweep(store, today, channel, auto_decide=None):
        channel.send("+15550000010", "Hola, falta un documento.")
        return SweepReport(acted=("c-001",))

    monkeypatch.setattr(run, "sweep", fake_sweep)
    monkeypatch.setattr("sys.argv", ["grace", "sweep"])
    assert main() == 0
    out = capsys.readouterr().out
    assert "+15550000010" in out
    assert "falta un documento" in out


def test_main_reports_a_nonzero_exit_when_a_case_errored(monkeypatch):
    """An error is an unswept family. A zero exit code from a cron/Step
    Functions invocation says the sweep succeeded, so it must not."""
    import grace.run as run

    monkeypatch.setattr(
        run, "sweep", lambda *a, **k: SweepReport(acted=("c-001",), errors=(("c-002", "boom"),))
    )
    monkeypatch.setattr("sys.argv", ["grace", "sweep"])
    assert main() != 0


def test_main_exits_zero_on_a_clean_sweep_with_escalations(monkeypatch):
    """Escalating is a success, not a failure — three escalations is the
    intended outcome, so they must not turn into a non-zero exit."""
    import grace.run as run

    monkeypatch.setattr(
        run,
        "sweep",
        lambda *a, **k: SweepReport(acted=("c-001",), escalated=(("c-010", "missing doc"),)),
    )
    monkeypatch.setattr("sys.argv", ["grace", "sweep"])
    assert main() == 0
