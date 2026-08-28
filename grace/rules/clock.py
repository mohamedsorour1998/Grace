"""Deadline math. Pure functions over dates — no model, no I/O.

This is deliberately not an agent. Deterministic work does not need one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from grace.rules.pack import RulePack

WindowStatus = Literal["not_open", "open", "overdue", "in_grace", "closed"]


@dataclass(frozen=True)
class Window:
    opens: date
    due: date
    grace_ends: date


def renewal_window(cert_end: date, pack: RulePack) -> Window:
    """The renewal window implied by a certification-period end date.

    Asserts the window is ordered. `load_pack` already rejects negative day
    counts, so this cannot trip for a validated pack — but `Window` is also
    constructible directly in tests and by Task 2's fixtures, and an inverted
    window is invisible to `window_status`.
    """
    window = Window(
        opens=cert_end - timedelta(days=pack.window_opens_days_before_end),
        due=cert_end,
        grace_ends=cert_end + timedelta(days=pack.grace_period_days_after_end),
    )
    if not window.opens <= window.due <= window.grace_ends:
        raise ValueError(
            f"Inverted renewal window: opens={window.opens} due={window.due} "
            f"grace_ends={window.grace_ends}"
        )
    return window


# How long after the due date a renewal is still merely late rather than in the
# formal grace period. Capped by the pack's own grace period below: for a program
# whose entire grace period is shorter than this, every post-due day is `overdue`.
OVERDUE_BAND_DAYS = 30


def window_status(today: date, window: Window) -> WindowStatus:
    """Where `today` falls relative to a renewal window.

    Boundaries are inclusive on the near side: the day the window opens is
    already `open`, and the last day of grace is still `in_grace`.

    `closed` is checked before the overdue band, so a program whose grace period
    is shorter than `OVERDUE_BAND_DAYS` reports a lapsed window as `closed`
    rather than as still-actionable `overdue`. Getting that order wrong tells a
    caseworker a dead case can still be filed.

    Note that both `overdue` and `in_grace` are still actionable — the renewal
    can be filed. They are distinguished so the caseworker briefing can say
    whether a case is merely late or inside the formal grace period.
    """
    if today < window.opens:
        return "not_open"
    if today <= window.due:
        return "open"
    if today > window.grace_ends:
        return "closed"
    overdue_ends = min(window.due + timedelta(days=OVERDUE_BAND_DAYS), window.grace_ends)
    if today <= overdue_ends:
        return "overdue"
    return "in_grace"
