"""Local sweep: the runnable deliverable.

Processes every open case, files what it can, and reports what it could not.
The escalation list is the point — it is what a caseworker actually reads.

Three fail-open bugs live in this file's interrupt loop if it is written the
obvious way. All three were found by reading the SDK rather than by observing
a failure, because none of them produces an error:

1. **`GraphResult` has no `stop_reason` field.** Only single-agent
   `AgentResult` does. `getattr(result, "stop_reason", None) == "interrupt"` is
   therefore always `False` on a graph, so an escalation-detection loop written
   that way never runs and every case — including the three that must escalate
   — is reported as handled autonomously. A 12/0 sweep, with no error and no
   exception. Multi-agent results signal an interrupt with
   `status == Status.INTERRUPTED` and carry `result.interrupts`.

2. **Resuming with a truthy response *approves* the blocked tool.** The SDK's
   `SteeringHandler._handle_tool_steering_action` does
   `can_proceed = event.interrupt(...)` and cancels the tool only
   `if not can_proceed`. Any non-empty string is truthy, so resuming an
   interrupt with a caseworker's free-text answer files the renewal the gate
   just blocked, unless that exact answer is caught first. The obvious fix —
   a denylist of words meaning "escalate" — is itself fail-open: a denylist
   makes an *unrecognized* answer the dangerous one, and confirmed against the
   real tool executor, "Escalate." (one trailing period), "no, hold this one"
   (contains "no" but is not equal to it), and "needs review" all resumed and
   filed a renewal for a household missing a required document. `APPROVE_
   DECISIONS` below is an allowlist instead: only an exact match to an
   affirmative word resumes the graph. Everything else, including anything
   unrecognized, ends the case here — the unrecognized answer is now the safe
   one, which is the correct default for free text a human typed under time
   pressure.

3. **An unbounded resume loop pays for paid inference forever.** A case that
   interrupts on every resume (a caseworker answer the model does not accept,
   or a case that is simply stuck) loops with no cap — `set_max_node_
   executions(12)` on the graph bounds nodes *within* one invocation, not
   resumes across invocations, so it does not help here. `MAX_RESUME_ROUNDS`
   below caps it; exhausting it escalates with a reason saying so, rather than
   spinning.

**Why the report is not built from `Status.INTERRUPTED` alone.** An interrupt
means "the model tried something the gate refused." That is not the same
question as "did this case need a human," and using it as the answer makes the
9/3 split depend on which tool the model happened to reach for. Observed on a
real run: on `c-010` (missing `proof_of_residency`) the model called
`send_family_message` instead of `submit_renewal`. The gate *correctly* allowed
it — chasing one missing document by SMS is exactly what Grace exists to do —
so no interrupt fired, and a household with an incomplete file was reported as
handled autonomously. The same run reported 10/2; a second run reported 10/2
with a different case set. A demo claim that moves with model temperature is
not a claim.

So each case is classified from two sources that cannot be argued with:

- `evaluate()` — the deterministic gate, the same function the steering handler
  calls, run directly on the case. It decides whether the case *needed* a human.
- the ledger — what actually executed. `renewal_submitted` is the only evidence
  that a renewal was filed (hard rule 6: never claim an action without tool
  confirmation).

An interrupt still forces an escalation and supplies the caseworker's reason,
but it is no longer the only thing that can produce one.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date

from strands.multiagent.base import Status

from grace.authority import evaluate
from grace.cases.store import CaseStore, InMemoryCaseStore, load_fixture_cases
from grace.graph import build_case_graph
from grace.rules.pack import load_pack
from grace.tools.action import Channel, TranscriptChannel

# The date every fixture window is anchored to. A default of `date.today()`
# would turn the 9-act/3-escalate demo into 8/4 from 2026-10-31, when fixture
# c-002's SNAP grace period ends.
DEFAULT_TODAY = "2026-10-01"

# Caseworker decisions that mean "yes, file this after all" — the only
# answers that resume the graph. Everything else, including anything
# unrecognized, denies and ends the case here.
#
# This is an allowlist, not a denylist, on purpose: a resume carries the
# decision back into the SDK as a *boolean approval* (see the module
# docstring), so the polarity that fails closed is "resume only on an exact
# match to an affirmative word", never "deny only on an exact match to a
# negative one." A denylist makes the *unrecognized* answer the dangerous
# one — a caseworker who types "Escalate." (one trailing period), "no, hold
# this one" (contains "no" but isn't equal to it), or "needs review" would
# resume and file the renewal the gate just blocked, confirmed against the
# real executor. An allowlist makes the unrecognized answer the safe one:
# anything that isn't exactly "approve" ends the case, which is the correct
# default when the input is free text a human typed under time pressure.
APPROVE_DECISIONS = frozenset({"approve", "yes", "file", "proceed"})

# A resumed graph can interrupt again — a caseworker answer the model does
# not accept, or a case that is genuinely stuck. `set_max_node_executions(12)`
# on the graph bounds nodes *within* one invocation; it does not bound resumes
# across invocations, so nothing else stops this loop. Each round is a paid
# Bedrock call, so exhausting this escalates rather than spinning.
MAX_RESUME_ROUNDS = 3

# What a case is escalated with when the graph reports an interrupt but carries
# no interrupt objects. That should not happen; if it does, the case still has
# to land in exactly one bucket rather than silently vanishing from a report
# whose entire purpose is that every family is accounted for.
_UNEXPLAINED_INTERRUPT = (
    "The run paused without saying why. A caseworker must review this case."
)


@dataclass(frozen=True)
class SweepReport:
    acted: tuple[str, ...] = ()
    escalated: tuple[tuple[str, str], ...] = ()
    errors: tuple[tuple[str, str], ...] = ()

    def summary(self) -> str:
        lines = [
            f"Swept {len(self.acted) + len(self.escalated) + len(self.errors)} cases.",
            f"  Handled autonomously: {len(self.acted)}",
            f"  Escalated to a human: {len(self.escalated)}",
        ]
        if self.errors:
            lines.append(f"  Errors (escalated):   {len(self.errors)}")
        for case_id, reason in self.escalated:
            lines.append(f"\n  [{case_id}] {reason}")
        for case_id, err in self.errors:
            lines.append(f"\n  [{case_id}] ERROR: {err}")
        return "\n".join(lines)


def _reason_text(interrupt: object) -> str:
    """Render one interrupt's reason as the sentence a caseworker reads.

    The steering handler wraps the gate's text:
    `event.interrupt(name=..., reason={"message": action.reason})`. A bare
    `str()` on that yields `{'message': '...'}` — a Python dict repr in the
    caseworker brief, which is also the demo's headline output. Unwrap the
    known key, and fall back to `str()` for any other shape rather than
    dropping a reason we cannot parse.
    """
    reason = getattr(interrupt, "reason", None)
    if isinstance(reason, dict):
        message = reason.get("message")
        if message is not None:
            return str(message)
    if reason is None:
        return "no reason given"
    return str(reason)


def _gate_reason(store: CaseStore, case_id: str, today: date) -> str | None:
    """Why this case needs a human, or `None` if it does not.

    Runs the same deterministic `evaluate` the steering handler runs, directly
    on the case. This is what makes the 9/3 split a property of the data rather
    than of what the model chose to do: an interrupt tells you the model tried
    something and was refused, which is a different question from whether the
    case needed a human at all.

    `except Exception`, per Task 3's standing instruction to `evaluate`'s
    callers: a pack that passes `load_pack`'s validation can still raise
    `ValueError`, `TypeError`, or `OverflowError` from the date arithmetic
    inside `evaluate`. Any of those means the case could not be verified, and an
    unverifiable case escalates.
    """
    try:
        case = store.get(case_id)
        pack = load_pack(case.program, case.state)
        verdict = evaluate(case, today, pack)
    except Exception as exc:  # noqa: BLE001 — deliberate: fail closed
        return f"Verification error: could not verify case {case_id} ({exc})."
    if verdict.decision == "act":
        return None
    # Every reason, not `reasons[0]` — reason order is not a contract (Task 3)
    # and a case can fail several conditions at once.
    return "; ".join(f"{r.code}: {r.detail}" for r in verdict.reasons)


def _renewal_filed(store: CaseStore, case_id: str) -> bool:
    """Whether the ledger confirms a renewal was actually filed.

    The ledger, not the model transcript and not the absence of an interrupt,
    is the ground truth for what executed. `submit_renewal` writes
    `renewal_submitted` only after the store operation returns, so this is a
    confirmed action rather than a claimed one (hard rule 6).
    """
    try:
        return any(e.kind == "renewal_submitted" for e in store.ledger(case_id))
    except Exception:  # noqa: BLE001 — an unreadable ledger confirms nothing
        return False


def _outreach_sent(store: CaseStore, case_id: str) -> bool:
    """Whether the ledger confirms the family was messaged.

    Same ledger-as-ground-truth discipline as `_renewal_filed`. Surfaced in the
    escalation reason so a caseworker picking up the case knows the family has
    already been asked for the document and does not ask a second time.
    """
    try:
        return any(e.kind == "family_message_sent" for e in store.ledger(case_id))
    except Exception:  # noqa: BLE001 — an unreadable ledger confirms nothing
        return False


def sweep(
    store: CaseStore,
    today: date,
    channel: Channel,
    auto_decide: str | None = None,
) -> SweepReport:
    """Run every open case through the graph.

    Every case lands in exactly one of `acted`, `escalated`, or `errors`. That
    partition is the report's whole claim — a case counted twice, or counted
    nowhere, makes "nine handled alone, three escalated" arithmetic that does
    not add up while each individual count still looks plausible.

    Args:
        auto_decide: When set, answers every interrupt with this string
            instead of prompting. Use "escalate" for unattended runs.
    """
    acted: list[str] = []
    escalated: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []

    for case in store.open_cases():
        # The reason a human is needed, once known. `None` means "not yet
        # established"; a string means the case is escalated and this is what
        # the caseworker reads. Collected before the buckets are written so a
        # case that interrupts, resumes, interrupts again, and then fails still
        # produces exactly one row — twelve families, twelve outcomes.
        reason: str | None = None
        try:
            graph = build_case_graph(store, case.case_id, today, channel)
            result = graph(
                f"Process the renewal for case {case.case_id}. "
                f"Today is {today.isoformat()}."
            )

            # `status`, never `stop_reason`: GraphResult has no such field, so a
            # `getattr` check silently never fires. See the module docstring.
            resume_rounds = 0
            while result.status == Status.INTERRUPTED:
                interrupts = list(result.interrupts or [])
                if not interrupts:
                    # Fail closed. An interrupt with nothing to explain it is
                    # still a paused run, and a paused run is not a filed
                    # renewal (hard rule 6).
                    reason = reason or _UNEXPLAINED_INTERRUPT
                    break

                # First interrupt wins the caseworker's reason: it is the one
                # the gate raised on the untouched case, before any human
                # answer changed the picture.
                reason = reason or "; ".join(_reason_text(i) for i in interrupts)

                if resume_rounds >= MAX_RESUME_ROUNDS:
                    # A resume that keeps interrupting is not converging, and
                    # each round is a paid Bedrock call — stop paying for it.
                    reason = (
                        f"{reason} (did not settle after {MAX_RESUME_ROUNDS} "
                        "caseworker responses.)"
                    )
                    break

                if auto_decide is None:
                    print(f"\n[{case.case_id}] {reason}")
                    # A blank answer means escalate, not `None`: the SDK
                    # refuses a null interrupt response, and "I did not decide"
                    # is a decision to leave it to a human.
                    answer = input("  Caseworker decision: ").strip() or "escalate"
                else:
                    answer = auto_decide

                if answer.strip().lower() not in APPROVE_DECISIONS:
                    # Do not resume. The SDK reads the resume response as a
                    # truthy approval, so anything other than an exact,
                    # recognized "yes" would *approve* the tool the gate just
                    # blocked — including an unrecognized answer, which is
                    # exactly why this checks membership in an allowlist of
                    # affirmatives rather than absence from a denylist.
                    break

                resume_rounds += 1
                # One response block per interrupt, keyed by `interrupt.id` and
                # never by `interrupt.name`: the SDK matches responses to
                # interrupts by id, so an unanswered interrupt is not resumed,
                # it stays paused.
                result = graph(
                    [
                        {
                            "interruptResponse": {
                                "interruptId": i.id,
                                "response": answer,
                            }
                        }
                        for i in interrupts
                    ]
                )

            if reason is None and result.status != Status.COMPLETED:
                # FAILED, or any status that is neither completed nor an
                # interrupt. Not an error the sweep raised, and definitely not a
                # filed renewal — a human gets it, because `acted` is a claim
                # that Grace handled the case and nothing here confirms that.
                reason = (
                    f"The run ended in state "
                    f"'{getattr(result.status, 'value', result.status)}' "
                    "without completing. A caseworker must review this case."
                )

        except Exception as exc:  # noqa: BLE001 — fail closed, keep sweeping
            # Broad on purpose. `evaluate` raises `ValueError`, `TypeError`, or
            # `OverflowError` from causes that all look the same from here, a
            # Bedrock call raises its own family, and one unloadable case must
            # not abandon the other eleven families. Narrowing this is how a
            # sweep ends early and reports success for cases it never reached.
            if reason is None:
                errors.append((case.case_id, str(exc)))
                continue
            # Already in a human's hands, which is the safe outcome. Record the
            # resume failure in the reason rather than as a second row.
            escalated.append(
                (case.case_id, f"{reason} (the run then failed while resuming: {exc})")
            )
            continue

        # Classification proper. The deterministic gate decides whether the case
        # needed a human, and the ledger decides whether a renewal was actually
        # filed. Neither depends on which tool the model happened to choose —
        # see the module docstring on why `Status.INTERRUPTED` alone is not
        # enough.
        gate_reason = _gate_reason(store, case.case_id, today)
        if gate_reason is not None:
            detail = reason or gate_reason
            if _outreach_sent(store, case.case_id):
                # The family has already been asked. A caseworker picking this
                # up needs to know that, or they ask a second time — and a
                # duplicate request is exactly the confusion that makes families
                # give up on paperwork.
                detail = f"{detail} (Grace has already messaged the family.)"
            escalated.append((case.case_id, detail))
        elif reason is not None:
            # The gate says the case is clean but the run did not finish
            # cleanly. Trust the run: something happened that the gate's view
            # of the case record cannot see.
            escalated.append((case.case_id, reason))
        elif _renewal_filed(store, case.case_id):
            acted.append(case.case_id)
        else:
            # Clean case, clean run, and no renewal on the ledger. Grace did not
            # do the one thing this case needed, so it cannot be reported as
            # handled — hard rule 6 is about never claiming an unconfirmed
            # action, and "handled autonomously" is exactly such a claim.
            escalated.append(
                (
                    case.case_id,
                    "The case is clean but no renewal was filed. A caseworker "
                    "must file it or say why not.",
                )
            )

    return SweepReport(
        acted=tuple(acted), escalated=tuple(escalated), errors=tuple(errors)
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="grace", description="Grace benefit-renewal sweep")
    parser.add_argument("command", choices=["sweep"], help="what to run")
    parser.add_argument(
        "--today",
        default=DEFAULT_TODAY,
        help=f"date to evaluate windows against (ISO, default {DEFAULT_TODAY})",
    )
    parser.add_argument(
        "--auto",
        metavar="DECISION",
        default=None,
        help="answer every escalation with DECISION instead of prompting",
    )
    args = parser.parse_args()

    # `parser.error` exits non-zero rather than falling back to `date.today()`:
    # a silent fallback evaluates every renewal window against the wrong day.
    try:
        today = date.fromisoformat(args.today)
    except ValueError as exc:
        parser.error(f"--today must be an ISO date (YYYY-MM-DD): {exc}")

    store = InMemoryCaseStore(load_fixture_cases())
    channel = TranscriptChannel()
    report = sweep(store, today, channel, auto_decide=args.auto)
    print(report.summary())

    if channel.sent:
        print(f"\nMessages to families ({len(channel.sent)}):")
        for phone, body in channel.sent:
            print(f"  -> {phone}: {body}")

    # Escalations are a success — three of them is the intended outcome. An
    # *error* is an unswept family, so it must not exit 0: this runs from a cron
    # (EventBridge, then Step Functions) where a zero exit means "the sweep was
    # fine" and nothing looks at stdout.
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
