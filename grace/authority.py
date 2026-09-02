"""The authority gate.

Grace may act alone only when every condition below holds. Anything else
escalates to a human with a specific, typed reason.

Three properties this module deliberately has:

1. No model. The decision is deterministic Python, so it cannot be argued
   with, prompt-injected, or talked around.
2. No I/O. It is a pure function from (case, date, pack) to a decision,
   which is why it can be exhaustively table-tested. In particular the rule
   pack is *passed in*, already loaded and validated: putting `load_pack`
   here would add file I/O and would move the `InvalidRulePack` fail-closed
   decision away from the caller, which is the only layer that can retry,
   log, or brief a caseworker about it.
3. Fails closed. A missing rule pack or an unreadable fact escalates. It
   never defaults to "act".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from grace.cases.models import Case, Document
from grace.rules.clock import renewal_window, window_status
from grace.rules.pack import RulePack

Decision = Literal["act", "escalate"]

# The tools that change state in the world. Everything not in this set is a
# read and may be called freely.
#
# Never add `escalate_to_caseworker` here. `steering.py`'s ALWAYS_ALLOWED
# check runs first and would still let it through, but only because that
# check exists specifically to guarantee this — adding it to ACTION_TOOLS
# means it also needs a PREREQUISITES entry (grace/steering.py), and until
# someone adds one it would fail closed on the "no gate policy" path, which
# blocks escalation exactly when a human is most needed (hard rule 7).
ACTION_TOOLS: frozenset[str] = frozenset(
    {"submit_renewal", "send_family_message", "close_case"}
)


@dataclass(frozen=True)
class GateReason:
    """One specific, typed reason the gate escalated.

    `detail` may contain untrusted free text — `source_conflicts` is a
    caseworker-facing string taken verbatim from case data, and this is where
    it ends up. Never rendered here, and never escaped here either: it is
    consumed by more than one downstream surface (a caseworker UI, DynamoDB,
    an agents-as-tools briefer prompt), each of which needs a different
    escaping strategy. Escape at whichever render boundary consumes this,
    not in this module — `authority.py` has no rendering context to know
    which escaping is correct.
    """

    code: str
    detail: str


@dataclass(frozen=True)
class GateResult:
    decision: Decision
    reasons: tuple[GateReason, ...] = ()

    @property
    def escalated(self) -> bool:
        return self.decision == "escalate"


def _pct_change(baseline: int, reported: int | None) -> float:
    """Absolute percentage change. A drop counts as much as a rise.

    `reported is None` means the family reported no figure this cycle, so there
    is nothing to compare and the answer is "no change". `None` is handled here
    as well as at the call site because arithmetic against `None` raises, and a
    helper that can only return a number is easier to reason about than one that
    depends on its caller having already checked.

    A zero baseline has no defined percentage change, so any non-zero reported
    figure is reported as a full change rather than as a division error — going
    from no income to some income is exactly the kind of fact a human must see.

    `baseline` is assumed non-negative — a negative on-file income is invalid
    data, not a real percentage change, and is checked for explicitly by the
    caller before this is ever reached (see `evaluate`). `abs()` on only the
    numerator would otherwise let a negative baseline flip the sign of the
    result, making an arbitrarily large reported change compare as *negative*
    and never cross a non-negative threshold — an income check that silently
    stops checking, rather than one that fails closed.
    """
    if reported is None:
        return 0.0
    if baseline == 0:
        return 0.0 if reported == 0 else 100.0
    return abs(reported - baseline) / abs(baseline) * 100.0


def _most_recent(documents: tuple[Document, ...], doc_id: str) -> Document | None:
    """The freshest copy of a document on file, or `None` if there is none.

    A household that re-submits a document leaves the superseded copy in the
    record. Picking by position would make the verdict depend on record order,
    so the same facts could act or escalate depending on how they were loaded —
    a real bug caught in review: two copies with an identical `received` date
    but different `expires` produced opposite verdicts depending purely on
    which one happened to come first in the tuple, because `max()` on a tie
    returns the first maximal element and the key ignored `expires` entirely.

    The tie-break is total and deliberately conservative: newest `received`
    first, and among an exact `received` tie, the *earliest* expiry — so a
    duplicate can only ever make the verdict stricter, never looser, and the
    result no longer depends on tuple order.
    """
    copies = [d for d in documents if d.doc_id == doc_id]
    if not copies:
        return None
    return min(copies, key=lambda d: (-d.received.toordinal(), d.expires or date.max))


def evaluate(case: Case, today: date, pack: RulePack | None = None) -> GateResult:
    """Decide whether Grace may act on this case alone.

    Collects *every* failing condition rather than short-circuiting: the
    caseworker brief needs the full picture, not the first problem found.

    `pack` defaults to `None`, the fail-closed value, so a caller that could not
    load a pack — or forgot to pass one — gets an escalation rather than an
    unchecked renewal.

    `reasons` order follows the order the checks below run, but that order is
    not a contract — do not treat `reasons[0]` as "the primary reason" in a
    caller. Compare against `GateReason.code`, or treat `reasons` as an
    unordered collection.
    """
    if pack is None:
        return GateResult(
            decision="escalate",
            reasons=(
                GateReason(
                    code="verification_error",
                    detail=f"No rule pack available for {case.program}/{case.state}",
                ),
            ),
        )

    reasons: list[GateReason] = []

    # 1. The renewal window must be open, verified from the pack. Note that
    #    `overdue` and `in_grace` are both actionable: filing a late renewal
    #    inside the grace period is the procedural save Grace exists to make.
    window = renewal_window(case.cert_end, pack)
    status = window_status(today, window)
    if status == "not_open":
        reasons.append(
            GateReason(
                code="window_not_open",
                detail=f"Window opens {window.opens.isoformat()}",
            )
        )
    elif status == "closed":
        reasons.append(
            GateReason(
                code="window_closed",
                detail=f"Grace period ended {window.grace_ends.isoformat()}",
            )
        )

    # 2. Every required document present, current, and unexpired. Driven by the
    #    pack's list, not the household's — a document the pack does not ask for
    #    can never cause an escalation, however old it is.
    for required in pack.required_documents:
        doc = _most_recent(case.documents, required.doc_id)
        if doc is None:
            reasons.append(
                GateReason(
                    code="missing_document",
                    detail=f"{required.doc_id} is not on file",
                )
            )
            continue
        # Both boundaries are inclusive: a document exactly at its maximum age,
        # or expiring today, is still current. Escalating on the last valid day
        # would fail a clean case for a document that is in fact acceptable.
        #
        # A document can be both older than max_age_days AND past its own
        # expiry date at once — these are checked independently (`if`, not
        # `elif`) so both reasons reach the caseworker brief. Silently
        # dropping one would contradict evaluate()'s own contract of
        # reporting every failing condition, not just the first one found.
        if doc.received + timedelta(days=required.max_age_days) < today:
            reasons.append(
                GateReason(
                    code="stale_document",
                    detail=(
                        f"{required.doc_id} received {doc.received.isoformat()}, "
                        f"older than {required.max_age_days} days"
                    ),
                )
            )
        if doc.expires is not None and doc.expires < today:
            reasons.append(
                GateReason(
                    code="stale_document",
                    detail=f"{required.doc_id} expired {doc.expires.isoformat()}",
                )
            )

    # 3. Income unchanged outside the band the pack calls immaterial.
    #
    #    `reported_income_cents is None` means the family reported no figure
    #    this cycle, which is the ordinary case and not a discrepancy — most
    #    renewals report nothing. It must be checked with `is None` and never
    #    for falsiness: `0` is a real reported income, and a family whose income
    #    dropped to zero is the most eligibility-relevant case Grace will ever
    #    see. Treating that as "nothing reported" would renew them silently at
    #    their old income level.
    if case.reported_income_cents is not None:
        if case.household.monthly_income_cents < 0:
            # A negative on-file income is corrupt data, not a real income —
            # comparing against it would be meaningless (and, before this
            # check existed, silently disabled the income check entirely by
            # flipping the sign of the percentage). Escalate rather than
            # compute a comparison that has no defined meaning.
            reasons.append(
                GateReason(
                    code="verification_error",
                    detail=(
                        f"On-file income is negative "
                        f"({case.household.monthly_income_cents} cents)"
                    ),
                )
            )
        else:
            change = _pct_change(case.household.monthly_income_cents, case.reported_income_cents)
            # Strictly `>`: a change of exactly the immaterial percentage is
            # inside the band, so the threshold itself does not escalate.
            if change > pack.income_change_immaterial_pct:
                reasons.append(
                    GateReason(
                        code="material_income_change",
                        detail=(
                            f"Income moved {change:.1f}%, above the "
                            f"{pack.income_change_immaterial_pct}% immaterial band"
                        ),
                    )
                )

    # 4. Household composition unchanged. Any change affects the benefit
    #    amount, so a human decides. Same `is None` discipline as income above:
    #    a reported size of 0 is a reported value, not an absence.
    if case.reported_size is not None and case.reported_size != case.household.size:
        reasons.append(
            GateReason(
                code="household_size_change",
                detail=(
                    f"Size reported as {case.reported_size}, "
                    f"on record as {case.household.size}"
                ),
            )
        )

    # 5. No conflict between sources.
    for conflict in case.source_conflicts:
        reasons.append(GateReason(code="source_conflict", detail=conflict))

    if reasons:
        return GateResult(decision="escalate", reasons=tuple(reasons))
    return GateResult(decision="act")
