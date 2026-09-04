"""A caseworker's approval is an input to the gate's decision, never a bypass.

The dashboard's whole safety argument rests on this file: `c-010` is missing
`proof_of_residency`, and approving it must still file nothing.

**The clause is asserted at all three `_escalate` call sites, and its absence is
asserted too.** `process_case` escalates from three places — the gate's typed
reason, a run that did not finish, and a clean case that filed nothing (hard
rule 6) — and the plan's draft put the clause at one of them. A test that only
ever sets the flag `True` cannot distinguish "appended on approval" from
"appended always", so every positive assertion here has a negative twin.
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

# The sentence `_escalate` appends. Matched on a fragment rather than in full so
# a reworded clause does not fail these tests for a cosmetic reason, but on
# enough of it that an unrelated sentence cannot satisfy it by accident.
CLAUSE = "a caseworker approved this case"


class FakeGraph:
    """A graph that reports a status and never calls Bedrock.

    Returns `self` from `__call__` so it doubles as the `GraphResult`: the three
    members `process_case` reads (`status`, `interrupts`, `results`) are all
    here. `results` must be a real mapping because `deliberation_note` walks it.
    """

    def __init__(
        self,
        status: Status = Status.COMPLETED,
        interrupts: tuple[object, ...] = (),
    ) -> None:
        self._status = status
        self._interrupts = list(interrupts)
        self.calls = 0

    def __call__(self, task: str) -> FakeGraph:
        self.calls += 1
        return self

    @property
    def status(self) -> Status:
        return self._status

    @property
    def interrupts(self) -> list[object]:
        return self._interrupts

    @property
    def results(self) -> dict[str, object]:
        return {}


class FakeInterrupt:
    """The shape `_reason_text` unwraps — the steering handler's own wrapper."""

    def __init__(self, message: str) -> None:
        self.reason = {"message": message}


def _store() -> InMemoryCaseStore:
    return InMemoryCaseStore(load_fixture_cases())


def _run(
    monkeypatch,
    case_id: str,
    graph: FakeGraph | None = None,
    **payload_extra: object,
) -> dict:
    monkeypatch.setattr(
        entrypoint, "build_case_graph", lambda *a, **k: graph or FakeGraph()
    )
    return entrypoint.process_case(
        {"case_id": case_id, "today": TODAY, **payload_extra},
        store=_store(),
        channel=TranscriptChannel(),
    )


# --------------------------------------------------------------------------
# The headline safety property
# --------------------------------------------------------------------------


def test_approving_a_case_missing_a_document_still_does_not_file(monkeypatch):
    """The headline safety property, and the one to show in the demo.

    `c-010` is missing `proof_of_residency`. A caseworker approving it changes
    nothing about that fact, so `evaluate()` still says escalate and no renewal
    is filed.
    """
    store = _store()
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: FakeGraph())
    out = entrypoint.process_case(
        {"case_id": "c-010", "today": TODAY, "caseworker_approved": True},
        store=store,
        channel=TranscriptChannel(),
    )
    assert out["status"] == "escalated"
    assert out.get("filed") is not True
    assert not any(e.kind == "renewal_submitted" for e in store.ledger("c-010"))


def test_approving_c010_escalates_even_when_a_renewal_row_exists(monkeypatch):
    """The headline test that can actually fail, and the reason it exists.

    **The test above is not discriminating on its own.** `FakeGraph` files
    nothing, so `renewal_filed` is `False` for every input and hard rule 6's
    third branch escalates `c-010` regardless of what the gate said. Measured:
    with `if caseworker_approved: gate = None` spliced in — the plan's own Step 9
    sabotage — `c-010` still came back `escalated`, on the "clean but no renewal
    was filed" reason, and the assertions above all held. The safety claim was
    true of the run and unproven by the test.

    So this one removes that alibi. A `renewal_submitted` row is written first,
    which makes `renewal_filed` return `True` and gives the sabotaged code a
    path to `acted`. The gate is then the *only* thing standing between an
    approval and a household missing a required document being reported as
    filed — which is exactly the claim the demo makes.
    """
    store = _store()
    store.append_ledger(
        LedgerEntry(
            case_id="c-010",
            at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
            kind="renewal_submitted",
            detail={"confirmation": "test-only"},
        )
    )
    assert entrypoint.renewal_filed(store, "c-010"), "the fixture must arm the trap"

    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: FakeGraph())
    out = entrypoint.process_case(
        {"case_id": "c-010", "today": TODAY, "caseworker_approved": True},
        store=store,
        channel=TranscriptChannel(),
    )
    # The gate ran on the case record and found the document still missing, so
    # the presence of a renewal row cannot turn this into `acted`.
    assert out["status"] == "escalated", (
        "an approved case that is still missing a document was reported as "
        "handled — the approval reached the gate"
    )
    assert out.get("filed") is not True
    assert "missing_document" in out["reason"]

    # And the same fixture without the approval behaves identically, so the
    # assertion above is about the gate rather than about the flag.
    plain_store = _store()
    plain_store.append_ledger(
        LedgerEntry(
            case_id="c-010",
            at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
            kind="renewal_submitted",
            detail={"confirmation": "test-only"},
        )
    )
    plain = entrypoint.process_case(
        {"case_id": "c-010", "today": TODAY},
        store=plain_store,
        channel=TranscriptChannel(),
    )
    assert plain["status"] == "escalated"


def test_the_approval_is_visible_in_the_reason(monkeypatch):
    """So a caseworker can tell "Grace re-checked and still refused" apart from
    "nothing happened"."""
    out = _run(monkeypatch, "c-010", caseworker_approved=True)
    assert CLAUSE in out["reason"].lower()
    # The gate's own typed reason survives. The clause is appended, never
    # substituted — Task 7's finding about a generic message displacing the one
    # fact the caseworker needed.
    assert "missing_document" in out["reason"]


def test_the_flag_does_not_change_the_verdict_for_any_fixture(monkeypatch):
    """Structural: across all twelve households, approving changes no case's
    status. If it ever does, the flag has reached the gate."""
    checked = 0
    for n in range(1, 13):
        case_id = f"c-{n:03d}"
        plain = _run(monkeypatch, case_id)
        approved = _run(monkeypatch, case_id, caseworker_approved=True)
        assert plain["status"] == approved["status"], case_id
        checked += 1
    assert checked == 12, "the loop must actually run for every fixture"


# --------------------------------------------------------------------------
# One test per `_escalate` call site, each with its negative twin
# --------------------------------------------------------------------------


def test_the_clause_appears_on_the_gate_reason_branch(monkeypatch):
    """Call site 1: `gate_reason` returned a verdict. `c-010` reaches it."""
    approved = _run(monkeypatch, "c-010", caseworker_approved=True)
    assert CLAUSE in approved["reason"].lower()
    assert "missing_document" in approved["reason"]


def test_the_clause_is_absent_on_the_gate_reason_branch_without_the_flag(monkeypatch):
    """The negative twin. Without this, "appended on approval" and "appended
    always" are indistinguishable — and the second would tell every caseworker
    that somebody approved a case nobody had looked at."""
    plain = _run(monkeypatch, "c-010")
    assert CLAUSE not in plain["reason"].lower()
    assert "missing_document" in plain["reason"]


def test_the_clause_appears_on_the_unfinished_run_branch(monkeypatch):
    """Call site 2: the gate is clean but the run interrupted.

    `c-001` is a clean fixture, so `gate_reason` returns `None` and only an
    interrupt puts it on this branch.
    """
    graph = FakeGraph(Status.INTERRUPTED, (FakeInterrupt("the gate refused"),))
    out = _run(monkeypatch, "c-001", graph, caseworker_approved=True)
    assert out["status"] == "escalated"
    assert CLAUSE in out["reason"].lower()
    assert "the gate refused" in out["reason"]


def test_the_clause_is_absent_on_the_unfinished_run_branch_without_the_flag(
    monkeypatch,
):
    graph = FakeGraph(Status.INTERRUPTED, (FakeInterrupt("the gate refused"),))
    out = _run(monkeypatch, "c-001", graph)
    assert out["status"] == "escalated"
    assert CLAUSE not in out["reason"].lower()
    assert "the gate refused" in out["reason"]


def test_the_clause_appears_on_the_clean_but_unfiled_branch(monkeypatch):
    """Call site 3, and the one the plan's single-site edit would have missed.

    This is the hard-rule-6 branch: the gate is clean, the run finished, and no
    `renewal_submitted` row exists. A caseworker approving a case that then
    files nothing must see that Grace re-checked, and this is the branch where
    "nothing happened" is most easily mistaken for success.
    """
    out = _run(monkeypatch, "c-001", caseworker_approved=True)
    assert out["status"] == "escalated"
    assert CLAUSE in out["reason"].lower()
    assert "no renewal was filed" in out["reason"]


def test_the_clause_is_absent_on_the_clean_but_unfiled_branch_without_the_flag(
    monkeypatch,
):
    out = _run(monkeypatch, "c-001")
    assert out["status"] == "escalated"
    assert CLAUSE not in out["reason"].lower()
    assert "no renewal was filed" in out["reason"]


def test_every_escalate_call_site_passes_the_flag_explicitly(monkeypatch):
    """A keyword-only parameter with a default that nobody passes is a clause
    that never appears.

    The three behavioural tests above cover the sites that exist today; this one
    fails if a *fourth* is added without the argument, which is how the plan's
    draft went wrong in the first place. Counted from the AST rather than from a
    list someone remembered — the discovery-from-disk discipline Task 4's
    model-ID guard established.
    """
    tree = ast.parse(inspect.getsource(entrypoint))
    sites = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "_escalate"
    ]
    assert len(sites) == 3, f"expected 3 call sites, found {len(sites)}"
    for site in sites:
        passed = {kw.arg for kw in site.keywords}
        assert "caseworker_approved" in passed, (
            f"an _escalate call on line {site.lineno} does not pass "
            "caseworker_approved, so the clause can never appear on that path"
        )


# --------------------------------------------------------------------------
# The flag's polarity, and that it reaches nothing that decides
# --------------------------------------------------------------------------


def test_a_non_boolean_flag_is_not_treated_as_approval(monkeypatch):
    """`payload.get(...) is True`, not truthiness.

    The payload arrives from an HTTP body; `"false"`, `"no"`, and `1` are all
    truthy in Python, and this is the same allowlist-over-truthiness discipline
    the resume path taught.

    **The assertion is on the clause, not on `filed`.** `c-010` escalates for
    every input including the honest `True`, so `assert out["filed"] is not True`
    — which is what the plan's draft asserted — passes identically whether the
    check is `is True` or a bare truthiness test. It could not fail. What
    actually distinguishes them is whether a truthy non-boolean produces the
    approval wording, i.e. whether the row claims a human approved a case nobody
    approved.
    """
    checked = 0
    for value in ["true", "false", 1, 0, "yes", [], {}, None, "True", [0], {"a": 1}]:
        out = _run(monkeypatch, "c-010", caseworker_approved=value)
        assert out.get("filed") is not True, value
        assert CLAUSE not in out["reason"].lower(), (
            f"{value!r} was treated as an approval; only the JSON boolean "
            "`true` may be"
        )
        checked += 1
    assert checked == 11, "the loop must actually run for every value"
    # And the honest value still works, or the test above would pass against a
    # flag that is permanently off.
    approved = _run(monkeypatch, "c-010", caseworker_approved=True)
    assert CLAUSE in approved["reason"].lower()


def test_the_flag_never_reaches_the_gate_or_the_graph():
    """Structural, so a later edit cannot quietly wire it through.

    `evaluate` decides from the case record alone. If `caseworker_approved`
    appeared in a call to `build_case_graph`, `evaluate`, or `gate_reason`, the
    gate would be taking a caseworker's word for a fact it is supposed to check.
    """
    tree = ast.parse(inspect.getsource(entrypoint))
    inspected = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if callee not in {"build_case_graph", "evaluate", "gate_reason"}:
            continue
        inspected += 1
        rendered = ast.dump(node)
        assert "caseworker_approved" not in rendered, (
            f"{callee} must not receive the approval flag"
        )
    # The plan's draft had no such count, so the test would have passed
    # vacuously if the calls were ever renamed — Task 8's lesson.
    assert inspected >= 2, (
        "expected to inspect at least build_case_graph and gate_reason calls, "
        f"inspected {inspected}"
    )


def test_evaluate_has_no_parameter_an_approval_could_occupy():
    """The guarantee behind the headline test, asserted rather than argued.

    `evaluate(case, today, pack=None)` — three parameters, none of which is an
    approval. So the flag cannot reach the gate even by a mistaken edit: it
    would be a `TypeError` at the call site rather than a silently looser
    verdict. Hard rule 5 in its strongest available form.
    """
    from grace.authority import evaluate

    names = list(inspect.signature(evaluate).parameters)
    assert names == ["case", "today", "pack"], names
    assert not any("approv" in n.lower() for n in names)


def test_the_deployed_path_still_carries_no_resume_vocabulary():
    """Plan 2's guard, re-asserted here because this task is exactly the
    pressure that would reintroduce a resume.

    **A raw substring check over the source cannot express this, and the plan's
    draft used one.** The module's own docstring names all three words in order
    to explain why they are absent, so `assert "APPROVE_DECISIONS" not in source`
    fails against correct code — measured. `tests/test_entrypoint.py` already
    solved this by checking the AST: `interruptResponse` must not appear in a
    string *literal* (it is the SDK's resume payload key, so it could only arrive
    as one), and the two guard names must not appear as an identifier. Prose
    about a resume is documentation; a name or a payload key is machinery.
    """
    tree = ast.parse(inspect.getsource(entrypoint))
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not any("interruptResponse" in literal for literal in literals)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "APPROVE_DECISIONS" not in names
    assert "MAX_RESUME_ROUNDS" not in names


def test_the_flag_does_not_appear_in_the_ledger_or_the_escalation_row(monkeypatch):
    """The flag is wording, not a fact about the household.

    A `caseworker_approved` key on a ledger row would be a claim about the case
    record that the gate never verified, and `authority.py` would have no way to
    tell it from data it measured itself.
    """
    store = _store()
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: FakeGraph())
    entrypoint.process_case(
        {"case_id": "c-010", "today": TODAY, "caseworker_approved": True},
        store=store,
        channel=TranscriptChannel(),
    )
    for entry in store.ledger("c-010"):
        assert "caseworker_approved" not in entry.detail, entry.kind
        assert "caseworker_approved" not in entry.kind


def test_today_is_still_required_to_be_pinned_with_the_flag_present(monkeypatch):
    """The flag must not become a path that skips payload validation.

    A bad `today` evaluates every renewal window against the wrong day with no
    error, so it has to stay an `error` outcome whether or not an approval came
    with it.
    """
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: FakeGraph())
    out = entrypoint.process_case(
        {"case_id": "c-010", "today": "not-a-date", "caseworker_approved": True},
        store=_store(),
        channel=TranscriptChannel(),
    )
    assert out["status"] == "error"
    assert "ISO date" in out["detail"]
