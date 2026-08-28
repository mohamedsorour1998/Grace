from datetime import date, timedelta

import pytest

from grace.rules.clock import (
    OVERDUE_BAND_DAYS,
    WindowStatus,
    Window,
    renewal_window,
    window_status,
)
from grace.rules.pack import RulePack, RequiredDocument, load_pack


PACK = RulePack(
    program="medicaid",
    state="NY",
    version="2026.1",
    certification_period_months=12,
    window_opens_days_before_end=60,
    grace_period_days_after_end=90,
    required_documents=(RequiredDocument(doc_id="proof_of_income", max_age_days=60),),
    income_change_immaterial_pct=5.0,
)


def test_window_opens_60_days_before_cert_end():
    w = renewal_window(date(2026, 12, 31), PACK)
    assert w.opens == date(2026, 11, 1)
    assert w.due == date(2026, 12, 31)
    assert w.grace_ends == date(2027, 3, 31)


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 10, 31), "not_open"),
        (date(2026, 11, 1), "open"),
        (date(2026, 12, 31), "open"),
        (date(2027, 1, 1), "overdue"),
        (date(2027, 3, 31), "in_grace"),
        (date(2027, 4, 1), "closed"),
    ],
)
def test_window_status_transitions(today: date, expected: WindowStatus):
    w = renewal_window(date(2026, 12, 31), PACK)
    assert window_status(today, w) == expected


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2027, 1, 30), "overdue"),
        (date(2027, 1, 31), "in_grace"),
    ],
)
def test_overdue_band_edge_is_pinned(today: date, expected: WindowStatus):
    """The overdue/in_grace boundary must land exactly at OVERDUE_BAND_DAYS.

    Without this pair, any band value from 1 to 89 kept the transition test
    green, so the constant was unconstrained by the suite.
    """
    w = renewal_window(date(2026, 12, 31), PACK)
    assert window_status(today, w) == expected


@pytest.mark.parametrize("program", ["medicaid", "snap"])
def test_lapsed_window_reads_closed_for_every_shipped_pack(program: str):
    """A window past its grace period must never report an actionable status.

    snap-ny.yaml has a 30-day grace period, exactly OVERDUE_BAND_DAYS. When the
    band was checked before grace_ends, any pack with grace <= 30 reported dates
    past grace_ends as `overdue` — telling a caseworker a dead case could still
    be filed.
    """
    pack = load_pack(program, "NY")
    w = renewal_window(date(2026, 12, 31), pack)
    assert window_status(w.grace_ends, w) != "closed"
    assert window_status(w.grace_ends + timedelta(days=1), w) == "closed"


@pytest.mark.parametrize("program", ["medicaid", "snap"])
def test_status_is_monotonic_and_never_actionable_after_close(program: str):
    """Status must move forward only, and stay closed once closed."""
    order = ["not_open", "open", "overdue", "in_grace", "closed"]
    pack = load_pack(program, "NY")
    w = renewal_window(date(2026, 12, 31), pack)
    seen = -1
    for offset in range(-pack.window_opens_days_before_end - 5,
                        pack.grace_period_days_after_end + 6):
        rank = order.index(window_status(w.due + timedelta(days=offset), w))
        assert rank >= seen, f"status moved backwards at due+{offset} for {program}"
        seen = rank


def test_short_grace_period_collapses_the_overdue_band():
    """A grace period shorter than the band must not extend the window.

    The band is capped by the pack's own grace period, so `in_grace` is simply
    unreachable for such a program rather than the window outliving its own
    grace period.
    """
    pack = RulePack(
        program="snap",
        state="NY",
        version="test",
        certification_period_months=6,
        window_opens_days_before_end=30,
        grace_period_days_after_end=10,
        required_documents=(RequiredDocument(doc_id="proof_of_income", max_age_days=30),),
        income_change_immaterial_pct=10.0,
    )
    w = renewal_window(date(2026, 12, 31), pack)
    assert w.grace_ends == date(2027, 1, 10)
    assert window_status(date(2027, 1, 10), w) == "overdue"
    assert window_status(date(2027, 1, 11), w) == "closed"
    assert OVERDUE_BAND_DAYS > pack.grace_period_days_after_end


def test_inverted_window_raises_rather_than_reporting_actionable():
    """An inverted window must not silently produce an actionable status."""
    with pytest.raises(ValueError, match="Inverted renewal window"):
        renewal_window(
            date(2026, 12, 31),
            RulePack(
                program="snap",
                state="NY",
                version="test",
                certification_period_months=6,
                window_opens_days_before_end=30,
                grace_period_days_after_end=-10,  # type: ignore[arg-type]
                required_documents=(RequiredDocument(doc_id="x", max_age_days=1),),
                income_change_immaterial_pct=10.0,
            ),
        )


def test_leap_day_cert_end():
    """Day arithmetic must be exact across a leap day."""
    w = renewal_window(date(2028, 2, 29), PACK)
    assert w.opens == date(2027, 12, 31)
    assert w.due == date(2028, 2, 29)
    assert w.grace_ends == date(2028, 5, 29)
    assert window_status(date(2028, 2, 29), w) == "open"


def test_window_constructed_directly_is_still_evaluated():
    """window_status is total over any well-ordered Window."""
    w = Window(opens=date(2026, 1, 1), due=date(2026, 1, 31), grace_ends=date(2026, 3, 1))
    assert window_status(date(2025, 12, 31), w) == "not_open"
    assert window_status(date(2026, 3, 2), w) == "closed"
