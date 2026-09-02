"""Read tools. Free to call — they change nothing.

Every tool here takes NO arguments. The case is bound at construction time
from the authenticated session, so a model cannot ask for a different family's
record: there is no parameter to poison. This is layer 2 of the escalation
boundary (see CLAUDE.md) and it is the reason `make_read_tools` is a factory
rather than three module-level functions taking a `case_id`.

Worth knowing exactly how strands enforces that: an argument the tool spec does
not declare is **silently discarded** before the function is called, so an
injected `case_id` produces a normal, successful read of the *bound* case rather
than an error. The property holds, but it holds because the parameter does not
exist — not because anything validates it. Adding a `case_id` parameter "just
for tests" would therefore remove the protection with no visible failure.

Two things these tools deliberately do NOT do:

1. They do not report the household's phone number. The model never needs it —
   `send_family_message` reads it from the bound case itself — and a tool result
   ends up in the model transcript, and from there in whatever captures one.
2. They do not let an `InvalidRulePack` escape. strands converts a tool
   exception into an error tool-result the model reads, so a raw
   `InvalidRulePack` would put an absolute filesystem path into the prompt.
   These tools fail closed instead: they say the window cannot be verified and
   that a human must decide, which leaves escalation as the only move.
"""

from __future__ import annotations

from datetime import date

from strands import tool

from grace.authority import _most_recent, document_problems
from grace.cases.store import CaseStore
from grace.rules.clock import renewal_window, window_status
from grace.rules.pack import load_pack

# What a read tool says when it could not verify a fact it was asked for. The
# wording matters: it must not look like a fact ("window closed"), and it must
# point at the only safe next step, because a model reading a vague failure will
# otherwise guess. `evaluate` escalates independently on the same case, so this
# string is a courtesy to the transcript, never the enforcement.
_UNVERIFIABLE = (
    "The program rules for this case cannot be verified. "
    "Do not act on this case — escalate to a human caseworker."
)


def _reported(value: int | None, unit: str = "") -> str:
    """Render a family-reported figure for a human (and a model) to read.

    `None` means "not reported this cycle", which is the ordinary case for most
    renewals — not missing data. Rendering it as the literal `None` invites the
    opposite inference: that something failed to load and the case needs a
    human. `0` must still render as a figure, because a family whose income
    dropped to zero is the most eligibility-relevant case Grace will ever see.
    """
    if value is None:
        return "not reported this cycle"
    return f"{value}{unit}"


def make_read_tools(store: CaseStore, case_id: str, today: date) -> list:
    """Build read tools bound to one case and one sweep date.

    `case_id` and `today` are closed over, never accepted as arguments. `today`
    is bound for the same reason the tests pin it: a `date.today()` inside a
    tool would make the verdict depend on when the sweep happened to run.
    """

    @tool
    def read_case() -> str:
        """Read the household and program details for the current case.

        No arguments needed — identity is determined from the session.
        """
        c = store.get(case_id)
        return (
            f"Case {c.case_id}: {c.household.display_name}\n"
            f"Program: {c.program} ({c.state})\n"
            f"Household size on record: {c.household.size}, "
            f"reported: {_reported(c.reported_size)}\n"
            f"Monthly income on record: {c.household.monthly_income_cents} cents, "
            f"reported: {_reported(c.reported_income_cents, ' cents')}\n"
            f"Certification ends: {c.cert_end.isoformat()}\n"
            f"Preferred language: {c.household.language}\n"
            f"Source conflicts: {list(c.source_conflicts) or 'none'}"
        )

    @tool
    def check_window() -> str:
        """Check where today falls in the renewal window for the current case.

        No arguments needed — identity is determined from the session.
        """
        c = store.get(case_id)
        try:
            pack = load_pack(c.program, c.state)
            w = renewal_window(c.cert_end, pack)
        except Exception:
            # Fails closed on anything, not a chosen subset — the same
            # discipline CLAUDE.md mandates for evaluate()'s own callers, and
            # for the same reason: a pack that loads cleanly but carries an
            # out-of-range value (e.g. grace_period_days_after_end large
            # enough that cert_end + timedelta(...) overflows date) raises
            # OverflowError, which is neither InvalidRulePack nor ValueError.
            # A narrower except here would let that one escape as a raw
            # exception via a direct call, and via tool.stream() it would
            # surface to the model as a bare error instead of the escalate
            # instruction below.
            return _UNVERIFIABLE
        return (
            f"Window opens {w.opens.isoformat()}, due {w.due.isoformat()}, "
            f"grace ends {w.grace_ends.isoformat()}. "
            f"Status as of {today.isoformat()}: {window_status(today, w)}"
        )

    @tool
    def list_documents() -> str:
        """List documents on file and which the program requires.

        No arguments needed — identity is determined from the session.
        """
        c = store.get(case_id)
        try:
            pack = load_pack(c.program, c.state)
            lines = []
            for req in pack.required_documents:
                # `_most_recent`, not `{d.doc_id: d for d in c.documents}`: a
                # household that re-submits leaves the superseded copy on file,
                # and a dict comprehension is last-wins by record *order*, not
                # by which copy is newest. Reusing the gate's own selector is
                # the point — this tool is what a model reads before deciding
                # whether to act, so if it named a different copy than
                # `evaluate` does, the model would be reasoning from facts the
                # gate does not share.
                doc = _most_recent(c.documents, req.doc_id)
                if doc is None:
                    lines.append(f"- {req.doc_id}: MISSING (required)")
                    continue
                # The verdict is computed here and stated plainly, not left to
                # the model to derive from `received` plus `max_age_days`. An
                # earlier version reported both raw values and let the model
                # subtract; on a real sweep it got the arithmetic wrong on two
                # of nine clean cases and texted those families that a current
                # document had expired. `document_problems` is the same
                # function `evaluate` uses, so the tool and the gate cannot
                # disagree about staleness — the same reason `_most_recent` is
                # shared rather than reimplemented.
                problems = document_problems(doc, req, today)
                if not problems:
                    verdict = "CURRENT"
                elif problems == ("stale_by_age",):
                    verdict = f"STALE (older than the {req.max_age_days} days allowed)"
                elif problems == ("expired",):
                    verdict = "EXPIRED (past its own expiry date)"
                else:
                    verdict = (
                        f"STALE (older than the {req.max_age_days} days allowed) "
                        "and EXPIRED (past its own expiry date)"
                    )
                lines.append(
                    f"- {req.doc_id}: {verdict}"
                    f" — received {doc.received.isoformat()}"
                    f"{f', expires {doc.expires.isoformat()}' if doc.expires else ''}"
                )
        except Exception:
            # Fails closed on anything, not a chosen subset. One `try` around
            # both the pack load and the per-document verdict on purpose: a
            # pack with an out-of-range `max_age_days` loads cleanly through
            # `load_pack`'s own validation (which enforces no upper bound) and
            # then makes `document_problems`'s date arithmetic — the same
            # arithmetic `renewal_window` does — raise `OverflowError`. A `try`
            # that only wrapped `load_pack` would leave that call unguarded,
            # which is exactly the "narrowed to the exceptions I've seen so
            # far" pattern that caused `check_window`'s version of this bug
            # (Task 4) — see the module docstring's item 2. Confirmed live: a
            # pack with `max_age_days: 999999999` raised uncaught from this
            # function until the `try` covered the loop, not just the load.
            #
            # Without a pack, or without a verdict for every document, any
            # summary here would be an invented one. Say nothing rather than
            # imply the paperwork is complete.
            return _UNVERIFIABLE
        return "\n".join(lines)

    return [read_case, check_window, list_documents]
