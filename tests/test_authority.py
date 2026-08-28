"""Exhaustive table tests for the authority gate.

This is the file that decides whether a family keeps coverage, so it is tested
hardest. Two classes of bug are specifically hunted here:

* **Too loose** — a case with a real problem is filed without human review.
* **Too strict** — a clean case escalates, which burns the caseworker time
  Grace exists to save and turns the 9-act/3-escalate demo into 8/4.

Boundary cases get their own tests on both sides of every threshold, because
every one of them is an off-by-one away from the wrong answer.
"""

import ast
import inspect
from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest

import grace.authority
from grace.authority import ACTION_TOOLS, GateReason, GateResult, _pct_change, evaluate
from grace.cases.models import Case, Document, Household
from grace.cases.store import load_fixture_cases
from grace.rules.pack import RulePack, load_pack

MEDICAID = load_pack("medicaid", "NY")
SNAP = load_pack("snap", "NY")

# Pinned, as every test module in this repo does: fixture c-002 goes `closed`
# on 2026-10-31, so a real `date.today()` would silently change the answers.
TODAY = date(2026, 10, 1)

# The complete set of reason codes the gate may emit. Duplicated here on purpose
# so a typo in authority.py produces a failure rather than a novel code that
# Task 5's ledger and Appendix E's span attributes would carry unnoticed.
REASON_CODES = frozenset(
    {
        "window_not_open",
        "window_closed",
        "missing_document",
        "stale_document",
        "material_income_change",
        "household_size_change",
        "source_conflict",
        "verification_error",
    }
)


def _clean_case(**overrides) -> Case:
    """A Medicaid case that should pass every gate condition on 2026-10-01.

    `reported_income_cents` and `reported_size` are deliberately **omitted**,
    not set to the household's own figures. They default to `None`, which is
    what "the family reported no change this cycle" means (see
    `grace/cases/models.py`). A test that wants to simulate an actual reported
    figure passes one explicitly.
    """
    base = Case(
        case_id="c-clean",
        household=Household(
            household_id="h-clean",
            display_name="The Test Household",
            language="en",
            phone="+15550009999",
            monthly_income_cents=200_000,
            size=3,
        ),
        program="medicaid",
        state="NY",
        cert_end=date(2026, 10, 15),
        documents=(
            Document(doc_id="proof_of_income", received=date(2026, 9, 20)),
            Document(doc_id="proof_of_residency", received=date(2026, 3, 1)),
        ),
        source_conflicts=(),
    )
    return replace(base, **overrides)


def _codes(result: GateResult) -> list[str]:
    return [r.code for r in result.reasons]


# --------------------------------------------------------------------------
# The clean path
# --------------------------------------------------------------------------


def test_clean_case_acts_autonomously():
    result = evaluate(_clean_case(), TODAY, MEDICAID)
    assert result.decision == "act"
    assert result.reasons == ()
    assert result.escalated is False


def test_unreported_income_and_size_do_not_escalate():
    """`None` means "not reported this cycle" and must short-circuit to "no
    discrepancy". Comparing `None` numerically, or treating it as a change from
    the on-file value, would escalate every ordinary renewal — the majority of
    the caseload."""
    case = _clean_case(reported_income_cents=None, reported_size=None)
    result = evaluate(case, TODAY, MEDICAID)
    assert result.decision == "act"
    assert result.reasons == ()


def test_escalated_property_tracks_the_decision():
    assert evaluate(_clean_case(), TODAY, MEDICAID).escalated is False
    assert evaluate(_clean_case(reported_size=9), TODAY, MEDICAID).escalated is True


# --------------------------------------------------------------------------
# The renewal window
# --------------------------------------------------------------------------


def test_window_not_yet_open_escalates():
    # cert_end far out: window opens 60 days before, so not open on TODAY
    result = evaluate(_clean_case(cert_end=date(2027, 6, 30)), TODAY, MEDICAID)
    assert result.decision == "escalate"
    assert _codes(result) == ["window_not_open"]


def test_window_opening_today_is_actionable():
    """The day the window opens is already open — an inclusive boundary."""
    # medicaid opens 60 days before cert_end; 2026-10-01 + 60 = 2026-11-30
    result = evaluate(_clean_case(cert_end=date(2026, 11, 30)), TODAY, MEDICAID)
    assert result.decision == "act"


def test_window_closed_escalates():
    result = evaluate(_clean_case(cert_end=date(2026, 1, 1)), TODAY, MEDICAID)
    assert result.decision == "escalate"
    assert "window_closed" in _codes(result)


def test_overdue_window_is_still_actionable():
    """Filing a late renewal inside the grace period is the procedural save
    Grace exists to make. `overdue` must not escalate — fixture c-002 is
    `overdue` on the pinned date and is one of the nine cases Grace files
    alone."""
    # due 2026-09-15, overdue band runs to 2026-10-15
    result = evaluate(_clean_case(cert_end=date(2026, 9, 15)), TODAY, MEDICAID)
    assert result.decision == "act"


def test_in_grace_window_is_still_actionable():
    """`in_grace` is distinguished from `overdue` for the caseworker briefing,
    not for the gate. Both are filable."""
    # due 2026-08-01: past the 30-day overdue band, inside the 90-day grace
    result = evaluate(_clean_case(cert_end=date(2026, 8, 1)), TODAY, MEDICAID)
    assert result.decision == "act"


def test_last_day_of_grace_is_actionable():
    """Inclusive on the far side too: the final day of grace can still be
    filed. Escalating here would abandon a renewal that was still savable."""
    # 2026-07-03 + 90 days = 2026-10-01 exactly
    result = evaluate(_clean_case(cert_end=date(2026, 7, 3)), TODAY, MEDICAID)
    assert result.decision == "act"


def test_day_after_grace_ends_escalates():
    # 2026-07-02 + 90 days = 2026-09-30, so grace ended yesterday
    result = evaluate(_clean_case(cert_end=date(2026, 7, 2)), TODAY, MEDICAID)
    assert result.decision == "escalate"
    assert _codes(result) == ["window_closed"]


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------


def test_missing_required_document_escalates():
    case = _clean_case(
        documents=(Document(doc_id="proof_of_income", received=date(2026, 9, 20)),)
    )
    result = evaluate(case, TODAY, MEDICAID)
    assert result.decision == "escalate"
    assert _codes(result) == ["missing_document"]
    assert "proof_of_residency" in result.reasons[0].detail


def test_no_documents_at_all_reports_every_missing_one():
    result = evaluate(_clean_case(documents=()), TODAY, MEDICAID)
    assert _codes(result) == ["missing_document", "missing_document"]
    details = " ".join(r.detail for r in result.reasons)
    assert "proof_of_income" in details and "proof_of_residency" in details


def test_stale_document_escalates():
    # proof_of_income has max_age_days=60; received 120 days ago
    case = _clean_case(
        documents=(
            Document(doc_id="proof_of_income", received=date(2026, 6, 1)),
            Document(doc_id="proof_of_residency", received=date(2026, 3, 1)),
        )
    )
    result = evaluate(case, TODAY, MEDICAID)
    assert result.decision == "escalate"
    assert "stale_document" in _codes(result)


def test_document_at_exactly_max_age_is_still_current():
    """max_age_days is inclusive. A document exactly at the limit is current;
    escalating here would make a clean case escalate on its last valid day."""
    # 2026-08-02 + 60 days = 2026-10-01 = TODAY
    case = _clean_case(
        documents=(
            Document(doc_id="proof_of_income", received=date(2026, 8, 2)),
            Document(doc_id="proof_of_residency", received=date(2026, 3, 1)),
        )
    )
    assert evaluate(case, TODAY, MEDICAID).decision == "act"


def test_document_one_day_past_max_age_is_stale():
    # 2026-08-01 + 60 days = 2026-09-30, one day short of TODAY
    case = _clean_case(
        documents=(
            Document(doc_id="proof_of_income", received=date(2026, 8, 1)),
            Document(doc_id="proof_of_residency", received=date(2026, 3, 1)),
        )
    )
    result = evaluate(case, TODAY, MEDICAID)
    assert result.decision == "escalate"
    assert _codes(result) == ["stale_document"]


def test_expired_document_escalates():
    case = _clean_case(
        documents=(
            Document(
                doc_id="proof_of_income",
                received=date(2026, 9, 20),
                expires=date(2026, 9, 30),  # expired yesterday
            ),
            Document(doc_id="proof_of_residency", received=date(2026, 3, 1)),
        )
    )
    result = evaluate(case, TODAY, MEDICAID)
    assert result.decision == "escalate"
    assert "stale_document" in _codes(result)


def test_document_expiring_today_is_not_yet_expired():
    """A document is valid through its expiry date. Pinned because "expires
    today" is genuinely ambiguous and the choice must not drift."""
    case = _clean_case(
        documents=(
            Document(
                doc_id="proof_of_income",
                received=date(2026, 9, 20),
                expires=TODAY,
            ),
            Document(doc_id="proof_of_residency", received=date(2026, 3, 1)),
        )
    )
    assert evaluate(case, TODAY, MEDICAID).decision == "act"


def test_document_both_stale_by_age_and_expired_reports_both_reasons():
    """A document can fail two independent checks at once. Both must reach the
    caseworker brief — an `elif` here would silently drop one, contradicting
    evaluate()'s own stated contract of reporting every failing condition
    rather than the first one found."""
    case = _clean_case(
        documents=(
            Document(
                doc_id="proof_of_income",
                received=date(2026, 6, 1),  # 122 days old at TODAY, max is 60
                expires=date(2026, 7, 1),  # also expired
            ),
            Document(doc_id="proof_of_residency", received=date(2026, 3, 1)),
        )
    )
    result = evaluate(case, TODAY, MEDICAID)
    assert result.decision == "escalate"
    assert _codes(result) == ["stale_document", "stale_document"]
    assert {r.detail for r in result.reasons} == {
        "proof_of_income received 2026-06-01, older than 60 days",
        "proof_of_income expired 2026-07-01",
    }


def test_documents_the_pack_does_not_require_are_ignored():
    """A household may have filed extra paperwork. Nothing the pack does not
    ask for may cause an escalation."""
    case = _clean_case(
        documents=_clean_case().documents
        + (Document(doc_id="proof_of_expenses", received=date(2019, 1, 1)),)
    )
    assert evaluate(case, TODAY, MEDICAID).decision == "act"


@pytest.mark.parametrize("stale_first", [True, False])
def test_duplicate_documents_are_judged_on_the_most_recent(stale_first: bool):
    """A re-submitted document supersedes the old copy still sitting in the
    file. Selecting by position instead of by date would make the decision
    depend on record order — the same facts could act or escalate."""
    stale = Document(doc_id="proof_of_income", received=date(2026, 6, 1))
    fresh = Document(doc_id="proof_of_income", received=date(2026, 9, 20))
    copies = (stale, fresh) if stale_first else (fresh, stale)
    case = _clean_case(
        documents=copies + (Document(doc_id="proof_of_residency", received=date(2026, 3, 1)),)
    )
    assert evaluate(case, TODAY, MEDICAID).decision == "act"


@pytest.mark.parametrize("expired_first", [True, False])
def test_duplicate_documents_with_an_exact_date_tie_break_on_expiry(expired_first: bool):
    """The bug this guards against is real, not hypothetical: two copies with
    an *identical* received date and differing expires produced opposite
    verdicts depending purely on which one came first in the tuple, because
    the original `max(key=received)` ignored expires on a tie entirely.

    Order must not matter, and the tie-break must be the conservative one —
    an expired copy present anywhere in the record must still escalate, even
    if a same-day valid copy also exists.
    """
    expired = Document(
        doc_id="proof_of_income", received=date(2026, 9, 20), expires=date(2026, 9, 30)
    )
    valid = Document(doc_id="proof_of_income", received=date(2026, 9, 20), expires=None)
    copies = (expired, valid) if expired_first else (valid, expired)
    case = _clean_case(
        documents=copies + (Document(doc_id="proof_of_residency", received=date(2026, 3, 1)),)
    )
    result = evaluate(case, TODAY, MEDICAID)
    assert result.decision == "escalate"
    assert _codes(result) == ["stale_document"]


# --------------------------------------------------------------------------
# Income
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reported,expected",
    [
        (None, "act"),          # not reported this cycle — no comparison applies
        (200_000, "act"),       # reported, unchanged
        (209_000, "act"),       # +4.5%, inside the 5% immaterial band
        (210_000, "act"),       # +5.0% exactly — boundary is inclusive
        (211_000, "escalate"),  # +5.5%, material
        (150_000, "escalate"),  # -25%, material (a drop matters too)
    ],
)
def test_income_change_band(reported: int | None, expected: str):
    result = evaluate(_clean_case(reported_income_cents=reported), TODAY, MEDICAID)
    assert result.decision == expected


def test_income_drop_is_material_not_just_increase():
    """A large income drop may mean the family qualifies for MORE. A human
    should look, rather than Grace quietly renewing at the old level."""
    result = evaluate(_clean_case(reported_income_cents=100_000), TODAY, MEDICAID)
    assert "material_income_change" in _codes(result)


def test_reported_income_of_zero_escalates():
    """`0` is a real reported income, not an absence marker — a family that
    lost all income is the most eligibility-relevant case Grace will ever see.
    A falsy check (`if not case.reported_income_cents`) in place of an
    `is None` check would silently renew them at the old income level."""
    result = evaluate(_clean_case(reported_income_cents=0), TODAY, MEDICAID)
    assert result.decision == "escalate"
    assert "material_income_change" in _codes(result)


def test_zero_on_file_income_with_a_reported_figure_escalates():
    """No percentage change is definable from a zero baseline, so any reported
    income at all is a change a human must see."""
    case = _clean_case(
        household=replace(_clean_case().household, monthly_income_cents=0),
        reported_income_cents=90_000,
    )
    result = evaluate(case, TODAY, MEDICAID)
    assert result.decision == "escalate"
    assert "material_income_change" in _codes(result)


def test_zero_on_file_income_reported_as_still_zero_acts():
    case = _clean_case(
        household=replace(_clean_case().household, monthly_income_cents=0),
        reported_income_cents=0,
    )
    assert evaluate(case, TODAY, MEDICAID).decision == "act"


def test_negative_on_file_income_escalates_as_a_verification_error():
    """On-file income below zero is corrupt data, not a real income figure.

    Before this check existed, `_pct_change`'s `abs()` on only the numerator
    let a negative baseline flip the sign of the whole comparison: any
    reported change compared as *negative* and could never exceed a
    non-negative threshold, so the income check silently stopped checking
    instead of failing closed on data it cannot make sense of.
    """
    case = _clean_case(
        household=replace(_clean_case().household, monthly_income_cents=-100_000),
        reported_income_cents=100_000,
    )
    result = evaluate(case, TODAY, MEDICAID)
    assert result.decision == "escalate"
    assert "verification_error" in _codes(result)
    assert "material_income_change" not in _codes(result)


def test_pct_change_treats_an_unreported_figure_as_no_change():
    """Second line of defence behind `evaluate`'s own `is None` guard: the
    helper must not be capable of returning a bogus number for `None`."""
    assert _pct_change(200_000, None) == 0.0
    assert _pct_change(0, None) == 0.0


# --------------------------------------------------------------------------
# Household size
# --------------------------------------------------------------------------


def test_household_size_change_escalates():
    result = evaluate(_clean_case(reported_size=4), TODAY, MEDICAID)
    assert result.decision == "escalate"
    assert "household_size_change" in _codes(result)


def test_reported_size_matching_the_record_acts():
    """The family confirmed the size on file. Confirmation is not a change."""
    assert evaluate(_clean_case(reported_size=3), TODAY, MEDICAID).decision == "act"


def test_reported_size_of_zero_escalates():
    """Same falsy-versus-`None` trap as income: `0` is a reported value, so it
    must be compared, not skipped."""
    result = evaluate(_clean_case(reported_size=0), TODAY, MEDICAID)
    assert result.decision == "escalate"
    assert "household_size_change" in _codes(result)


# --------------------------------------------------------------------------
# Source conflicts
# --------------------------------------------------------------------------


def test_source_conflict_escalates():
    case = _clean_case(source_conflicts=("size 5 on application, 3 on wage record",))
    result = evaluate(case, TODAY, MEDICAID)
    assert result.decision == "escalate"
    assert "source_conflict" in _codes(result)


def test_every_source_conflict_is_reported():
    case = _clean_case(source_conflicts=("conflicting address", "conflicting employer"))
    result = evaluate(case, TODAY, MEDICAID)
    assert _codes(result) == ["source_conflict", "source_conflict"]
    assert {r.detail for r in result.reasons} == {
        "conflicting address",
        "conflicting employer",
    }


# --------------------------------------------------------------------------
# Several problems at once
# --------------------------------------------------------------------------


def test_multiple_problems_report_every_reason():
    """The caseworker brief needs all of them, not the first one found."""
    case = _clean_case(
        documents=(Document(doc_id="proof_of_income", received=date(2026, 6, 1)),),
        reported_income_cents=260_000,
        source_conflicts=("conflicting address",),
    )
    result = evaluate(case, TODAY, MEDICAID)
    codes = {r.code for r in result.reasons}
    assert codes == {
        "missing_document",
        "stale_document",
        "material_income_change",
        "source_conflict",
    }


def test_an_unreported_field_stays_silent_among_other_problems():
    """The same case as above reports no household size. Every other problem
    must still be reported, and `household_size_change` must not appear — a
    `None` compared against the on-file size would add a phantom reason to a
    caseworker brief that is otherwise accurate."""
    case = _clean_case(
        documents=(Document(doc_id="proof_of_income", received=date(2026, 6, 1)),),
        reported_income_cents=260_000,
        reported_size=None,
        source_conflicts=("conflicting address",),
    )
    result = evaluate(case, TODAY, MEDICAID)
    assert "household_size_change" not in _codes(result)
    assert len(result.reasons) == 4


def test_every_condition_failing_at_once_reports_all_of_them():
    case = _clean_case(
        cert_end=date(2027, 6, 30),  # window not open
        documents=(
            Document(
                doc_id="proof_of_income",
                received=date(2026, 9, 20),
                expires=date(2026, 9, 30),  # expired
            ),
        ),  # proof_of_residency missing
        reported_income_cents=400_000,
        reported_size=7,
        source_conflicts=("conflicting employer",),
    )
    result = evaluate(case, TODAY, MEDICAID)
    assert {r.code for r in result.reasons} == {
        "window_not_open",
        "stale_document",
        "missing_document",
        "material_income_change",
        "household_size_change",
        "source_conflict",
    }


# --------------------------------------------------------------------------
# Failing closed
# --------------------------------------------------------------------------


def test_missing_rule_pack_fails_closed():
    """A pack that cannot be loaded must escalate, never act."""
    case = _clean_case(program="wic")
    result = evaluate(case, TODAY, pack=None)
    assert result.decision == "escalate"
    assert _codes(result) == ["verification_error"]


def test_pack_defaults_to_none_so_an_omitted_pack_cannot_act():
    """The parameter's default is the fail-closed value. A caller that forgets
    to pass a pack gets an escalation, not an unchecked renewal."""
    result = evaluate(_clean_case(), TODAY)
    assert result.decision == "escalate"
    assert _codes(result) == ["verification_error"]


def test_verification_error_names_the_program_and_state():
    """The caseworker needs to know which pack was unavailable."""
    result = evaluate(_clean_case(program="wic", state="CA"), TODAY, pack=None)
    detail = result.reasons[0].detail
    assert "wic" in detail and "CA" in detail


def test_a_structurally_invalid_pack_raises_rather_than_acting():
    """Pins the one input `evaluate` cannot render a verdict on. `load_pack`
    rejects negative day counts, so this pack can only be hand-built — but if
    one ever reaches the gate, the failure must be loud. The alternative to
    raising is *not* `act`.

    This is intentionally left raising, not converted to a `GateResult`: a
    `verification_error` result is indistinguishable from a normal verdict at
    the call site, so a caller that logs and moves on would treat a corrupt
    rule pack as an ordinary escalation. Raising cannot be ignored by
    accident. It is the CALLER's job to fail closed on it — Task 5's steering
    handler and Task 6's sweep do not exist yet, and whichever wraps this
    call must catch `Exception` broadly (see the TypeError case below), not
    just `ValueError`, or a different kind of malformed pack will escape
    unguarded.
    """
    inverted = replace(MEDICAID, window_opens_days_before_end=-30)
    with pytest.raises(ValueError, match="Inverted renewal window"):
        evaluate(_clean_case(), TODAY, inverted)


def test_a_pack_with_a_missing_threshold_raises_typeerror_not_valueerror():
    """A different kind of malformed pack escapes as a different exception
    type — confirming a caller must catch Exception, not just ValueError,
    to actually fail closed on every way a hand-built pack can be broken."""
    no_threshold = replace(MEDICAID, income_change_immaterial_pct=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        evaluate(_clean_case(reported_income_cents=260_000), TODAY, no_threshold)


# --------------------------------------------------------------------------
# The pack is the authority, not the gate
# --------------------------------------------------------------------------


def test_snap_uses_its_own_thresholds():
    """SNAP allows 10% income drift and requires three documents."""
    case = _clean_case(
        program="snap",
        cert_end=date(2026, 10, 15),
        documents=(
            Document(doc_id="proof_of_income", received=date(2026, 9, 25)),
            Document(doc_id="proof_of_identity", received=date(2025, 1, 1)),
            Document(doc_id="proof_of_expenses", received=date(2026, 9, 1)),
        ),
        reported_income_cents=218_000,  # +9%, immaterial for SNAP, material for Medicaid
    )
    assert evaluate(case, TODAY, SNAP).decision == "act"


def test_the_same_case_can_act_under_one_pack_and_escalate_under_another():
    """Proves the thresholds come from the pack rather than from constants in
    the gate: only the pack differs between these two calls."""
    case = _clean_case(
        documents=(
            Document(doc_id="proof_of_income", received=date(2026, 9, 25)),
            Document(doc_id="proof_of_identity", received=date(2025, 1, 1)),
            Document(doc_id="proof_of_expenses", received=date(2026, 9, 1)),
            Document(doc_id="proof_of_residency", received=date(2026, 3, 1)),
        ),
        reported_income_cents=218_000,  # +9%
    )
    assert evaluate(case, TODAY, SNAP).decision == "act"
    assert evaluate(case, TODAY, MEDICAID).decision == "escalate"


# --------------------------------------------------------------------------
# The demo: twelve fixture households, nine acted, three escalated
# --------------------------------------------------------------------------


def _evaluate_fixture(case: Case) -> GateResult:
    return evaluate(case, TODAY, load_pack(case.program, case.state))


def test_the_fixture_set_splits_nine_act_three_escalate():
    """CLAUDE.md's central claim, asserted against the gate that makes it. If a
    clean case escalates the gate is too strict; if one of the three acts it is
    too loose. Either is a bug worth stopping for."""
    results = {c.case_id: _evaluate_fixture(c) for c in load_fixture_cases()}
    escalated = sorted(cid for cid, r in results.items() if r.escalated)
    acted = sorted(cid for cid, r in results.items() if not r.escalated)
    assert escalated == ["c-010", "c-011", "c-012"]
    assert len(acted) == 9


@pytest.mark.parametrize(
    "case_id,expected_code",
    [
        ("c-010", "missing_document"),
        ("c-011", "material_income_change"),
        ("c-012", "source_conflict"),
    ],
)
def test_each_escalating_fixture_escalates_for_its_own_single_reason(
    case_id: str, expected_code: str
):
    """Each of the three demonstrates a *different* gate condition. A case that
    escalated for an extra, unintended reason would still pass a bare
    escalation count while making the demo's story wrong."""
    case = next(c for c in load_fixture_cases() if c.case_id == case_id)
    result = _evaluate_fixture(case)
    assert _codes(result) == [expected_code]


def test_no_fixture_case_produces_an_undocumented_reason_code():
    for case in load_fixture_cases():
        for reason in _evaluate_fixture(case).reasons:
            assert reason.code in REASON_CODES
            assert reason.detail, f"{case.case_id}: {reason.code} has an empty detail"


# --------------------------------------------------------------------------
# Structural guarantees
# --------------------------------------------------------------------------


def test_action_tools_are_explicitly_enumerated():
    """The gate must know exactly which tools change state."""
    assert "submit_renewal" in ACTION_TOOLS
    assert "send_family_message" in ACTION_TOOLS
    assert "read_case" not in ACTION_TOOLS


def test_evaluate_is_pure_and_does_no_io():
    """Two identical calls return identical results; the case is unmutated."""
    case = _clean_case()
    first = evaluate(case, TODAY, MEDICAID)
    second = evaluate(case, TODAY, MEDICAID)
    assert first == second
    assert case == _clean_case()


def test_gate_results_are_frozen():
    """A decision must not be editable after it is made — Task 5 hands this
    object to the steering layer and Task 6 records it in the ledger.

    Uses an escalating case so `reasons` is non-empty: on a clean result
    `reasons[0]` raises `IndexError`, which would make a bare `pytest.raises`
    pass without ever touching a `GateReason`.
    """
    result = evaluate(_clean_case(reported_size=4), TODAY, MEDICAID)
    assert result.reasons, "test needs a result that carries a reason"
    with pytest.raises(FrozenInstanceError):
        result.decision = "act"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.reasons[0].code = "x"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        result.reasons.append(  # type: ignore[attr-defined]
            GateReason(code="x", detail="y")
        )


FORBIDDEN_IMPORTS = frozenset({"strands", "boto3", "requests", "urllib", "yaml", "pathlib", "os", "io"})

# `load_pack` is here because the pack must be *passed in*, already loaded and
# validated: loading it inside the gate would add file I/O and would move the
# `InvalidRulePack` fail-closed decision away from the caller, which is the only
# layer that can retry, log, or brief a caseworker about it.
FORBIDDEN_NAMES = frozenset({"open", "load_pack", "Path", "print", "input"})


def _referenced_names(module) -> set[str]:
    """Every name the module's *code* mentions, ignoring strings and comments.

    Parsed rather than grepped because this file's own docstrings discuss the
    things it forbids. A raw substring search would fail on a comment explaining
    why `load_pack` is absent, which would push the next person to weaken the
    check instead of the code.
    """
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
            names.update(alias.name for alias in node.names)
    return names


def test_authority_module_is_free_of_frameworks_and_io():
    """CLAUDE.md hard rule 4. Enforced as a test rather than a manual grep so
    it cannot be skipped: the gate's exhaustive testability, and its immunity
    to prompt injection, both depend on it having no model and no I/O."""
    referenced = _referenced_names(grace.authority)
    offenders = sorted(referenced & (FORBIDDEN_IMPORTS | FORBIDDEN_NAMES))
    assert not offenders, f"authority.py must not reference {offenders}"


def test_authority_imports_only_pure_siblings():
    """Whitelisted rather than blacklisted: a new import of something impure
    should fail this test even if nobody thought to forbid it by name."""
    tree = ast.parse(inspect.getsource(grace.authority))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert modules <= {
        "__future__",
        "dataclasses",
        "datetime",
        "typing",
        "grace.cases.models",
        "grace.rules.clock",
        "grace.rules.pack",
    }, f"unexpected imports in authority.py: {sorted(modules)}"


def test_authority_does_not_expose_the_pack_loader():
    """Belt to the AST check's braces: `load_pack` must not be reachable
    through the module's namespace either, e.g. via a star import."""
    assert not hasattr(grace.authority, "load_pack")
    assert isinstance(MEDICAID, RulePack)  # the type it consumes, nothing more
