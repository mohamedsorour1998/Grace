"""WHAT A LIVE CLOCK ACTUALLY COSTS.

Five comments and CLAUDE.md asserted that c-002's window closing on 2026-10-31
turns the 9/3 demo into 8/4 on that date. Measured, c-002 escalates on
**2026-10-16** on `stale_document`, and the split is **6/6** by 2026-10-30 —
before the window closes at all. The named cause was real and 15 days late,
and the magnitude was wrong in the reassuring direction.

The conclusion the comments draw is right and more urgent than they said, so
this pins the measurement rather than the prose. Any fixture edit that moves a
degradation date fails here, where a comment would simply have gone quietly out
of date — which is how it went wrong the first time.
"""

from __future__ import annotations

from datetime import date, timedelta

from grace.authority import evaluate
from grace.cases.store import load_fixture_cases
from grace.rules.pack import load_pack

PINNED = date(2026, 10, 1)


def _split(day: date) -> tuple[list[str], list[str]]:
    acted, escalated = [], []
    for case in load_fixture_cases():
        verdict = evaluate(case, day, load_pack(case.program, case.state))
        (acted if verdict.decision == "act" else escalated).append(case.case_id)
    return acted, escalated


def test_the_pinned_date_gives_the_demos_split():
    acted, escalated = _split(PINNED)
    assert len(acted) == 9, acted
    assert escalated == ["c-010", "c-011", "c-012"], escalated


def test_the_first_clean_case_degrades_on_the_sixteenth_of_october():
    """c-002, and on a stale document rather than a closed window."""
    acted_before, _ = _split(date(2026, 10, 15))
    assert "c-002" in acted_before

    verdict = evaluate(
        next(c for c in load_fixture_cases() if c.case_id == "c-002"),
        date(2026, 10, 16),
        load_pack("snap", "NY"),
    )
    assert verdict.decision == "escalate"
    assert [r.code for r in verdict.reasons] == ["stale_document"], verdict.reasons


def test_the_split_is_six_six_before_any_window_closes():
    """The number the comments said was 8/4, and the date they said it began.

    Both wrong: three clean households have already gone stale by 2026-10-30,
    and c-002's grace period ends on that day rather than on the 31st.
    """
    acted, escalated = _split(date(2026, 10, 30))
    assert (len(acted), len(escalated)) == (6, 6), (acted, escalated)

    acted, escalated = _split(date(2026, 10, 31))
    assert (len(acted), len(escalated)) == (6, 6), (acted, escalated)


def test_every_clean_case_degrades_on_the_measured_day():
    """The full table, so a fixture edit that moves any one of them is caught.

    Measured 2026-09-08 against `fixtures/households.yaml`; every one is a
    `stale_document`, none is a window closing — asserted rather than merely
    stated, because *which* reason fires is the whole substance of this
    correction. The comments this test replaces blamed a closed window, and a
    docstring making that claim without checking it is the same defect again.

    `checked` guards the `continue` above. Renaming a clean fixture id would
    otherwise drop that household from the table in silence, and the 9/3 split
    test cannot catch it: nine clean cases are still nine however they are
    named, and the escalating three are asserted by id there rather than here.
    """
    expected = {
        "c-001": date(2026, 11, 20), "c-002": date(2026, 10, 16),
        "c-003": date(2026, 11, 25), "c-004": date(2026, 10, 23),
        "c-005": date(2026, 11, 28), "c-006": date(2026, 11, 1),
        "c-007": date(2026, 11, 30), "c-008": date(2026, 10, 27),
        "c-009": date(2026, 11, 29),
    }
    checked = set()
    for case in load_fixture_cases():
        if case.case_id not in expected:
            continue
        pack = load_pack(case.program, case.state)
        day = expected[case.case_id]
        assert evaluate(case, day - timedelta(days=1), pack).decision == "act", (
            f"{case.case_id} should still be clean the day before {day}"
        )
        verdict = evaluate(case, day, pack)
        assert verdict.decision == "escalate", f"{case.case_id} should escalate on {day}"
        codes = [r.code for r in verdict.reasons]
        assert codes == ["stale_document"], (
            f"{case.case_id} should escalate on {day} for a stale document alone, "
            f"not a closed window — found {codes}"
        )
        checked.add(case.case_id)

    assert checked == set(expected), (
        f"every id in the table must be checked, or an entry stops being tested "
        f"in silence — missed {sorted(set(expected) - checked)}"
    )
