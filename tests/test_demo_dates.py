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

from datetime import date

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
    `stale_document`, none is a window closing.
    """
    expected = {
        "c-001": date(2026, 11, 20), "c-002": date(2026, 10, 16),
        "c-003": date(2026, 11, 25), "c-004": date(2026, 10, 23),
        "c-005": date(2026, 11, 28), "c-006": date(2026, 11, 1),
        "c-007": date(2026, 11, 30), "c-008": date(2026, 10, 27),
        "c-009": date(2026, 11, 29),
    }
    for case in load_fixture_cases():
        if case.case_id not in expected:
            continue
        pack = load_pack(case.program, case.state)
        day = expected[case.case_id]
        assert evaluate(case, day.fromordinal(day.toordinal() - 1), pack).decision == "act", (
            f"{case.case_id} should still be clean the day before {day}"
        )
        assert evaluate(case, day, pack).decision == "escalate", (
            f"{case.case_id} should escalate on {day}"
        )
