"""The deployed entrypoint. One case per invocation, three possible outcomes.

Every test here uses a fake graph. The real graph is exercised by Task 8's
deployed sweep; what needs asserting here is the *contract* — that each case
lands in exactly one bucket, that an interrupt is never resumed, and that the
classification matches `sweep`'s rather than being re-derived.
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone

from strands.multiagent.base import Status

from grace import entrypoint
from grace.cases.models import LedgerEntry
from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.tools.action import TranscriptChannel

TODAY = "2026-10-01"


class FakeGraph:
    """Stands in for `build_case_graph`'s result.

    Counts its own invocations, because the property that matters most here is
    a *call count*: the deployed path must invoke the graph exactly once. A
    fake that did not count could not distinguish "never resumed" from
    "resumed and happened to reach the same verdict".
    """

    def __init__(self, status=Status.COMPLETED, interrupts=(), results=None):
        self._status = status
        self._interrupts = list(interrupts)
        self._results = results or {}
        self.calls = 0

    def __call__(self, task):
        self.calls += 1
        return self

    @property
    def status(self):
        return self._status

    @property
    def interrupts(self):
        return self._interrupts

    @property
    def results(self):
        return self._results


class FakeInterrupt:
    def __init__(self, message):
        self.id = "int-1"
        self.name = "authority_gate"
        # The shape the steering handler really produces:
        # `event.interrupt(name=..., reason={"message": action.reason})`.
        self.reason = {"message": message}


def _payload(case_id="c-001"):
    return {"case_id": case_id, "today": TODAY}


def _store():
    return InMemoryCaseStore(load_fixture_cases())


def test_a_clean_case_with_a_filed_renewal_is_acted(monkeypatch):
    """The only path that may report `acted`.

    The `renewal_submitted` row is appended for real rather than
    monkeypatching `renewal_filed` to return True. The plan's draft patched the
    function out, which passes even if `renewal_filed` searched for the wrong
    ledger `kind` — the one thing this branch actually depends on (hard rule 6:
    the ledger row is the only evidence a renewal was filed).
    """
    store = _store()
    graph = FakeGraph()
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: graph)
    store.append_ledger(
        LedgerEntry(
            case_id="c-001",
            at=datetime.now(timezone.utc),
            kind="renewal_submitted",
            detail={"tool": "submit_renewal"},
        )
    )
    out = entrypoint.process_case(_payload("c-001"), store=store,
                                  channel=TranscriptChannel())
    assert out["status"] == "acted"
    assert out["filed"] is True
    assert out["case_id"] == "c-001"


def test_a_clean_case_with_no_filed_renewal_is_not_reported_as_acted(monkeypatch):
    """Hard rule 6's inverse, and the reason the test above appends a real row.

    Clean case, clean run, empty ledger. `acted` is a claim that Grace handled
    the case; nothing here confirms it, so the case must go to a human instead
    of being counted as a success.
    """
    store = _store()
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: FakeGraph())
    out = entrypoint.process_case(_payload("c-001"), store=store,
                                  channel=TranscriptChannel())
    assert out["status"] == "escalated"
    assert out.get("filed") is not True
    assert "no renewal was filed" in out["reason"]


def test_an_interrupt_is_never_resumed(monkeypatch):
    """The safety property this design turns on.

    Task 6 proved that resuming with a truthy response *approves* the blocked
    tool: confirmed against the real executor, "Escalate.", "no, hold this
    one", and "needs review" all resumed and filed a renewal for `c-010`, a
    household missing a required document. The deployed path has no human to
    ask, so it must never resume at all — a path that cannot resume cannot be
    talked into filing.

    Asserted by call count: the graph must be invoked exactly once. A resume
    loop would call it again, so this fails against any implementation that
    grows one (verified by sabotage, not assumed).
    """
    store = _store()
    graph = FakeGraph(status=Status.INTERRUPTED,
                      interrupts=[FakeInterrupt("Cannot file: document missing")])
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: graph)
    out = entrypoint.process_case(_payload("c-010"), store=store,
                                  channel=TranscriptChannel())
    assert graph.calls == 1, "the deployed path must never resume an interrupt"
    assert out["status"] == "escalated"
    # The gate's own wording reaches the caseworker, unwrapped from the
    # `{"message": ...}` dict the steering handler wraps it in.
    assert "Cannot file: document missing" in out["reason"]


def test_the_module_carries_no_resume_machinery():
    """Structural backstop for the test above.

    A call-count assertion is only as good as the fake it counts. This one
    cannot be satisfied by a well-behaved fake: it reads the module's own
    source and fails if the vocabulary of resuming appears at all.
    `interruptResponse` is the SDK's resume payload key, and
    `APPROVE_DECISIONS` / `MAX_RESUME_ROUNDS` are `run.py`'s guards for the
    attended path — needed there because a human answers, meaningless here
    because nobody does.
    """
    source = inspect.getsource(entrypoint)
    tree = ast.parse(source)
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not any("interruptResponse" in literal for literal in literals), (
        "the deployed entrypoint must never build a resume payload"
    )
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "APPROVE_DECISIONS" not in names
    assert "MAX_RESUME_ROUNDS" not in names


def test_the_gates_typed_reason_beats_a_generic_run_status(monkeypatch):
    """Task 7's finding. A FAILED node does not stop the graph, so `decide`
    still runs and `evaluate()` still has a specific verdict — but a naive
    implementation reports "the run ended in state 'failed'" and drops
    `material_income_change: Income moved 30.0%`, the one fact the caseworker
    needed."""
    store = _store()
    monkeypatch.setattr(entrypoint, "build_case_graph",
                        lambda *a, **k: FakeGraph(status=Status.FAILED))
    out = entrypoint.process_case(_payload("c-011"), store=store,
                                  channel=TranscriptChannel())
    assert out["status"] == "escalated"
    assert "material_income_change" in out["reason"]
    assert "failed" not in out["reason"].lower()


def test_an_interrupt_reason_still_beats_the_gates_wording(monkeypatch):
    """Precedence is about the *generic* fallback, not about every reason.

    The mirror of the test above, and the one that stops the fix for it from
    becoming "always prefer the gate". An interrupt reason is the gate's own
    text about this specific household, so it must keep winning over
    `gate_reason`'s reconstruction — the same pairing `tests/test_graph.py`
    keeps on `sweep`.
    """
    store = _store()
    graph = FakeGraph(status=Status.INTERRUPTED,
                      interrupts=[FakeInterrupt("the gate's own wording for c-011")])
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: graph)
    out = entrypoint.process_case(_payload("c-011"), store=store,
                                  channel=TranscriptChannel())
    assert out["reason"].startswith("the gate's own wording for c-011")


def test_an_interrupt_with_no_interrupt_objects_still_escalates(monkeypatch):
    """Fail closed. An interrupt with nothing to explain it is still a paused
    run, and a paused run is not a filed renewal — but the case must land in a
    bucket rather than vanishing from a report whose whole purpose is that
    every family is accounted for."""
    store = _store()
    graph = FakeGraph(status=Status.INTERRUPTED, interrupts=[])
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: graph)
    out = entrypoint.process_case(_payload("c-001"), store=store,
                                  channel=TranscriptChannel())
    assert out["status"] == "escalated"
    assert out["reason"].startswith(entrypoint._UNEXPLAINED_INTERRUPT)
    assert graph.calls == 1


def test_an_escalating_case_reports_no_filing(monkeypatch):
    """Hard rule 6, at the contract boundary: the payload must never claim a
    renewal that the ledger does not confirm."""
    store = _store()
    monkeypatch.setattr(entrypoint, "build_case_graph",
                        lambda *a, **k: FakeGraph())
    out = entrypoint.process_case(_payload("c-012"), store=store,
                                  channel=TranscriptChannel())
    assert out["status"] == "escalated"
    assert out.get("filed") is not True


def test_a_raising_graph_becomes_an_error_not_a_silent_pass(monkeypatch):
    """Fail closed. An exception must not be reported as a handled case."""
    store = _store()

    def boom(*a, **k):
        raise RuntimeError("bedrock exploded")

    monkeypatch.setattr(entrypoint, "build_case_graph", boom)
    out = entrypoint.process_case(_payload("c-001"), store=store,
                                  channel=TranscriptChannel())
    assert out["status"] == "error"
    assert "bedrock exploded" in out["detail"]


def test_every_outcome_carries_exactly_one_consistent_status(monkeypatch):
    """Task 6's partition rule, asserted with something that can fail.

    A `status in {...}` check alone cannot fail on a correctly-behaving system:
    `process_case` returns one dict, so "counted twice" is not expressible in
    its return value. What *is* expressible — and what would make the 9/3 claim
    arithmetic that does not add up — is an outcome whose fields contradict its
    status: an `escalated` row that also claims `filed`, or an `acted` row with
    an escalation reason. Both are checked, and every case must produce the
    field its status is aggregated on.
    """
    store = _store()
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: FakeGraph())
    seen = set()
    for case_id in ("c-001", "c-010", "c-011", "c-012"):
        out = entrypoint.process_case(_payload(case_id), store=store,
                                      channel=TranscriptChannel())
        assert out["status"] in {"acted", "escalated", "error"}
        assert out["case_id"] == case_id
        seen.add(out["status"])
        if out["status"] == "acted":
            assert out["filed"] is True
            assert "reason" not in out
        elif out["status"] == "escalated":
            assert out.get("filed") is not True
            assert out["reason"], "an escalation with no reason is not actionable"
        else:
            assert out["detail"]
    # Not a vacuous loop: at least one branch was genuinely exercised.
    assert seen


def test_a_missing_case_id_is_an_error_not_a_crash():
    """The payload comes from Step Functions. A malformed one must produce a
    reportable outcome, not an unhandled exception that Step Functions has to
    interpret."""
    out = entrypoint.process_case({"today": TODAY})
    assert out["status"] == "error"
    assert "case_id" in out["detail"]


def test_a_non_dict_payload_is_an_error_not_a_crash():
    """Same contract, the case the plan's draft left to raise.

    `BedrockAgentCoreApp` passes payloads through **unchanged** (its own
    docstring), so a caller sending a JSON array reaches this function as a
    list — and `payload.get(...)` on a list is an `AttributeError` that escapes
    before the `try` block. `runtime_app` guards this too; both are cheap, and
    the entrypoint's stated contract is that no payload shape raises.
    """
    for payload in ([], "c-001", None, 7):
        out = entrypoint.process_case(payload)  # type: ignore[arg-type]
        assert out["status"] == "error", payload
        assert out["detail"], payload


def test_a_bad_today_is_refused_rather_than_defaulted():
    """A silent `date.today()` fallback evaluates every renewal window against
    the wrong day — and fixture c-002 flips from `in_grace` to `closed` on
    2026-10-31, turning 9/3 into 8/4 with no error."""
    out = entrypoint.process_case({"case_id": "c-001", "today": "not-a-date"})
    assert out["status"] == "error"


def test_the_default_today_is_pinned():
    """Never a live clock. See above.

    Both halves matter: the constant is the pinned value, and no line in the
    module calls `date.today()` / `datetime.now()`. **Parsed, not grepped** —
    this module *documents* why the live clock must not appear, so a substring
    check matches the comment warning against it and fails on correct code.
    `tests/test_graph.py` records the same lesson: a test that can only pass by
    deleting the explanation is a bad test.
    """
    assert entrypoint.DEFAULT_TODAY == "2026-10-01"
    tree = ast.parse(inspect.getsource(entrypoint))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"today", "now"}, ast.dump(node.func)


def test_the_classification_helpers_are_shared_with_the_sweep():
    """Imported, never re-implemented.

    Task 7 recorded what a second copy of `deliberation_note` costs: its
    failure mode is printing the advocate's unchecked argument to a caseworker
    as though a verifier had confirmed it. An identity check is the only
    assertion that a future edit cannot satisfy by copying the body.
    """
    from grace import run

    assert entrypoint.gate_reason is run.gate_reason
    assert entrypoint.renewal_filed is run.renewal_filed
    assert entrypoint.outreach_sent is run.outreach_sent
    assert entrypoint.deliberation_note is run.deliberation_note
    # And the private aliases still resolve, because six existing tests use them.
    assert run._deliberation_note is run.deliberation_note
    assert run._gate_reason is run.gate_reason


def test_an_escalation_row_is_written_when_the_store_supports_one(monkeypatch):
    """The caseworker's queue entry, written here rather than only in Step
    Functions, so an escalation always leaves durable evidence even if the
    state machine's own write later fails.

    `InMemoryCaseStore` has no `write_escalation`, which is why the deployed
    path is checked with a recording stand-in rather than assumed.
    """
    store = _store()
    written = []

    class RecordingStore:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def write_escalation(self, case_id, reason, question, deadline):
            written.append((case_id, reason, question, deadline))

    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: FakeGraph())
    out = entrypoint.process_case(_payload("c-012"), store=RecordingStore(store),
                                  channel=TranscriptChannel())
    assert out["status"] == "escalated"
    assert len(written) == 1
    case_id, reason, _question, deadline = written[0]
    assert case_id == "c-012"
    assert "source_conflict" in reason
    # c-012's cert_end, so the queue can be sorted by urgency.
    assert deadline == "2026-10-12"


def test_a_failed_escalation_write_is_reported_rather_than_swallowed(monkeypatch):
    """The row is the caseworker's queue entry, and a lost one is a family who
    reaches nobody.

    The plan's draft wrapped this write in `except Exception: pass`. That keeps
    the outcome payload — which is real evidence, and what the alarm's metric
    filter counts — but it makes the *absence* of the durable row invisible:
    the dashboard reads the escalation GSI, so it would show two escalations
    while the payload said three, with nothing anywhere explaining the gap.
    Propagating instead is worse still, because a returned
    `{"status": "error"}` does not trigger Step Functions' `Catch`, so no row
    would be written by anyone.

    So the failure is recorded in the reason the caller receives. The family
    still escalates; the missing row is stated rather than silent.
    """
    store = _store()

    class BrokenStore:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def write_escalation(self, case_id, reason, question, deadline):
            raise RuntimeError("dynamodb refused the write")

    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: FakeGraph())
    out = entrypoint.process_case(_payload("c-012"), store=BrokenStore(store),
                                  channel=TranscriptChannel())
    assert out["status"] == "escalated", "a failed row write must not lose the family"
    assert "source_conflict" in out["reason"]
    assert "dynamodb refused the write" in out["reason"]


def test_the_deliberation_note_is_appended_never_substituted(monkeypatch):
    """Task 7's rule, carried into the deployed path.

    The gate's typed reason is what makes an escalation auditable; the
    referee's question is what makes it useful to the human reading it. A
    version that reported only the referee's sentence would put a model's prose
    where the deterministic verdict belongs.
    """

    class Node:
        def __init__(self, result):
            self.result = result

    class SwarmResult:
        def __init__(self, results):
            self.results = results

    store = _store()
    graph = FakeGraph(results={
        "deliberate": Node(SwarmResult({"referee": "AMBIGUOUS: Which income figure applies?"}))
    })
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: graph)
    out = entrypoint.process_case(_payload("c-011"), store=store,
                                  channel=TranscriptChannel())
    assert "material_income_change" in out["reason"]
    assert "AMBIGUOUS: Which income figure applies?" in out["reason"]


def test_outreach_already_sent_is_surfaced_to_the_caseworker(monkeypatch):
    """A caseworker picking up the case needs to know the family has already
    been asked, or they ask a second time — and a duplicate request is exactly
    the confusion that makes families give up on paperwork."""
    store = _store()
    store.append_ledger(
        LedgerEntry(
            case_id="c-010",
            at=datetime.now(timezone.utc),
            kind="family_message_sent",
            detail={"tool": "send_family_message"},
        )
    )
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: FakeGraph())
    out = entrypoint.process_case(_payload("c-010"), store=store,
                                  channel=TranscriptChannel())
    assert out["status"] == "escalated"
    assert "already messaged the family" in out["reason"]


def test_invoke_sets_telemetry_up_before_processing(monkeypatch):
    """The Runtime handler's only added responsibility. Recorded in a list
    rather than asserted by raising: `AssertionError` is an `Exception`, and
    Plan 2's Task 3 found a draft test whose raise was swallowed by the very
    `except Exception` it was meant to probe, passing with the guard deleted.
    """
    order = []
    monkeypatch.setattr(entrypoint, "setup_telemetry", lambda: order.append("telemetry"))
    monkeypatch.setattr(entrypoint, "process_case",
                        lambda payload: order.append("process") or {"status": "acted"})
    entrypoint.invoke({"case_id": "c-001", "today": TODAY})
    assert order == ["telemetry", "process"]
