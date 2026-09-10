"""The AgentCore Runtime handler. One case per invocation.

**One case per invocation, not a sweep.** The loop lives in Step Functions' Map
state. This matters beyond tidiness: Task 6 established that
`AuthorityGate._seen` is per-instance and in-memory, and that a fresh process
starts with it empty. One case per microVM session means `_seen` can never span
two households — the isolation is structural rather than conventional.

**Classification is `sweep`'s, imported, not re-derived.** Task 6 found the
alternative broken: classifying by "did an interrupt fire" reported an incomplete
household as handled — 10/2 instead of 9/3, no error, because on `c-010` the
model called `send_family_message` rather than `submit_renewal` and the gate
correctly allowed it. Classification therefore comes from two things that cannot
be argued with: `evaluate()` run directly on the case, and a ledger row in
`grace.run.FILED_KINDS` (hard rule 6). **That is two kinds, not one** —
`renewal_submitted` when `submit_renewal` files, and `renewal_already_filed`
when it finds a filing for the same certification period and declines to
duplicate it. Both mean a renewal is on file for this period; only a run that
produced neither escalates. Grep `FILED_KINDS`, not `renewal_submitted`.

`gate_reason`, `renewal_filed`, `outreach_sent`, and `deliberation_note` are
imported from `grace.run` rather than reimplemented. Task 7 recorded what a
second copy of the last one costs: its failure mode is printing the advocate's
unchecked argument to a caseworker as though a verifier had confirmed it.

**This path never resumes an interrupt, and that is stronger than the local
CLI.** Task 6 confirmed against the real executor that resuming with a truthy
response *approves* the blocked tool — "Escalate.", "no, hold this one", and
"needs review" all resumed and filed a renewal for a household missing a required
document. `run.py` guards that with an allowlist because a human is present to
answer. Here nobody is, so the graph is invoked exactly once and an interrupt
becomes an escalation row. A path with no resume cannot be talked into filing,
so `MAX_RESUME_ROUNDS` and `APPROVE_DECISIONS` are deliberately absent — and
`tests/test_entrypoint.py` asserts that structurally, by reading this module's
own source, because a call-count assertion is only as strong as the fake it
counts.

**Nothing here raises.** Every payload shape, every store failure, and every
model failure produces a `CaseOutcome` dict. Step Functions can branch on
`{"status": "error"}`; it cannot branch on a stack trace it never receives.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, TypedDict

from strands.multiagent.base import Status

from grace.graph import build_case_graph
from grace.household_memory import recall_facts, remember_outcome
from grace.ledger import _current_trace_id
from grace.observability import setup_telemetry
from grace.run import deliberation_note, gate_reason, outreach_sent, renewal_filed
from grace.store_factory import build_store
from grace.tools.action import Channel, TranscriptChannel

# Never `date.today()`. A live clock degrades the 9-act/3-escalate demo from
# 2026-10-16 (c-002's proof_of_income goes stale) and reaches 6/6 by
# 2026-10-30. See `tests/test_demo_dates.py`.
DEFAULT_TODAY = "2026-10-01"

# What a case is escalated with when the graph reports an interrupt but carries
# no interrupt objects. That should not happen; if it does, the case still has
# to land in exactly one bucket rather than silently vanishing from a report
# whose entire purpose is that every family is accounted for.
_UNEXPLAINED_INTERRUPT = (
    "The run paused without saying why. A caseworker must review this case."
)


class CaseOutcome(TypedDict, total=False):
    """What one case reports back to Step Functions.

    `status` is always one of `acted` / `escalated` / `error`, and exactly one —
    Task 6's partition rule, which is what makes the 9/3 claim arithmetic that
    adds up rather than three counts that each look plausible.

    `total=False` because the per-status fields differ: `acted` carries `filed`,
    `escalated` carries `reason`/`question`/`deadline`, `error` carries `detail`.
    Absence is the signal — an `escalated` outcome must not carry `filed: True`,
    and `tests/test_entrypoint.py` asserts that rather than trusting it.
    """

    status: str
    case_id: str
    filed: bool
    reason: str
    question: str
    deadline: str
    detail: str
    trace_id: str | None


def _reason_text(interrupt: object) -> str:
    """One interrupt's reason as the sentence a caseworker reads.

    The steering handler wraps the gate's text as
    `reason={"message": action.reason}`, so a bare `str()` yields a Python dict
    repr — in the caseworker's brief, which is also the demo's headline output.
    Unwrap the known key, and fall back to `str()` for any other shape rather
    than dropping a reason we cannot parse.
    """
    reason = getattr(interrupt, "reason", None)
    if isinstance(reason, dict):
        message = reason.get("message")
        if message is not None:
            return str(message)
    return "no reason given" if reason is None else str(reason)


def _remember(case_id: str, outcome: CaseOutcome) -> None:
    """Record what this run concluded, for the next cycle to read.

    **Called from the wrapper rather than from each terminal path**, and that is
    the point: `process_case` returns from eight places, and a call added at each
    one is covered only as well as whoever counted them. Wrapping makes the
    coverage structural — a ninth return added later is remembered without
    anyone remembering to remember it.

    Carries the case id, the status, and the gate's own reason. **Never a name, a
    phone number, or an address:** this text is written to a service and read
    back into a model's context on the next cycle, which is exactly the path a
    household surname once took to CloudWatch. The reason strings this reads come
    from `gate_reason`, which builds them from typed reason codes and document
    ids, so they are safe by construction rather than by filtering.

    Swallows everything. `remember_outcome` already fails open and returns
    `False` rather than raising, so this is a second belt for a defect in *this*
    module — and it sits inside a function whose module docstring promises
    "Nothing here raises", because Step Functions branches on
    `{"status": "error"}` and cannot branch on a stack trace it never receives.
    An observability write on the way out must never turn a decided case into an
    error.
    """
    try:
        status = outcome.get("status", "unknown")
        detail = outcome.get("reason") or outcome.get("detail") or ""
        summary = f"{status}: {detail}".strip().rstrip(":").strip()
        remember_outcome(case_id, summary)
    except Exception:  # noqa: BLE001 — see the docstring
        pass


def process_case(
    payload: dict[str, Any],
    store: Any = None,
    channel: Channel | None = None,
) -> CaseOutcome:
    """Process exactly one case, then record the outcome for the next cycle.

    A thin wrapper over `_process_case`, which holds every decision this module
    makes. The split exists so that remembering an outcome is not something eight
    separate `return` statements each have to do correctly — see `_remember`.

    Never raises, for any payload shape: `_process_case` guarantees that and
    `_remember` swallows everything.
    """
    outcome = _process_case(payload, store=store, channel=channel)
    case_id = outcome.get("case_id", "")
    if case_id:
        _remember(case_id, outcome)
    return outcome


def _process_case(
    payload: dict[str, Any],
    store: Any = None,
    channel: Channel | None = None,
) -> CaseOutcome:
    """Process exactly one case and report which bucket it landed in.

    Never raises, for any payload shape. `BedrockAgentCoreApp` passes payloads
    through **unchanged** (its own docstring), so a caller sending a JSON array
    arrives here as a `list` — on which `payload.get(...)` is an
    `AttributeError`. Validating the container type before reading it keeps the
    stated contract true for every input rather than only for dicts.
    """
    if not isinstance(payload, dict):
        return {"status": "error", "case_id": "",
                "detail": f"payload must be a JSON object, got {type(payload).__name__}"}

    case_id = payload.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        # The payload comes from Step Functions. A malformed one must produce a
        # reportable outcome, not an unhandled exception the state machine has
        # to interpret.
        return {"status": "error", "case_id": "",
                "detail": "payload must carry a non-empty string case_id"}
    case_id = case_id.strip()

    try:
        today = date.fromisoformat(str(payload.get("today") or DEFAULT_TODAY))
    except ValueError as exc:
        # Never fall back to a live clock — see DEFAULT_TODAY. A wrong `today`
        # evaluates every renewal window against the wrong day, with no error.
        return {"status": "error", "case_id": case_id,
                "detail": f"today must be an ISO date: {exc}"}

    # A caseworker's approval, when the dashboard re-invokes a case they decided.
    # **This does not reach the gate.** `evaluate()` runs on the case record
    # exactly as before and the tool list is unchanged, so approving a household
    # that is still missing a document still files nothing. The flag affects only
    # the wording of an escalation reason, so a caseworker can tell "Grace
    # re-checked and still refused" apart from "nothing happened".
    #
    # The guarantee is structural rather than behavioural: `evaluate`'s signature
    # is `(case, today, pack=None)`, so there is no parameter an approval could
    # occupy even by mistake — a wrong edit is a `TypeError` at the call site,
    # not a silently looser verdict.
    #
    # `is True`, not truthiness. The payload arrives from an HTTP body, where
    # `"false"`, `"no"`, `1`, and `[0]` are all truthy in Python; only the JSON
    # boolean `true` deserializes to `True`. Same allowlist-over-truthiness
    # polarity as `APPROVE_DECISIONS` — and the same reason: the unrecognised
    # value must be the safe one.
    #
    # Deliberately NOT a resume: Task 6 proved a truthy resume response
    # *approves* the blocked tool, so there is no interrupt to answer here.
    caseworker_approved = payload.get("caseworker_approved") is True

    # Built here rather than at import, and inside no `try`, because a store
    # that cannot be constructed is a misconfiguration this process cannot
    # recover from (`build_store` raises on an unrecognized `GRACE_STORE`
    # precisely so it is noticed). It is still reported rather than raised.
    try:
        store = store if store is not None else build_store()
    except Exception as exc:  # noqa: BLE001 — a reportable outcome, always
        return {"status": "error", "case_id": case_id,
                "detail": f"could not build a case store: {exc}"}
    channel = channel if channel is not None else TranscriptChannel()

    reason: str | None = None
    reason_is_run_status = False
    deliberation: str | None = None

    # See `renewal_filed`'s docstring: captured before the graph runs so that a
    # filing from an earlier sweep cannot be mistaken for this run's work.
    #
    # The invariant rather than a row count, because counts move with every
    # sweep and a number written into a comment goes stale in silence: **every
    # household `c-001`–`c-009` already carries a filing in the deployed table,
    # and has since the first sweep.** Without this boundary the outcome this
    # function returns — which `web/lib/decide.ts` renders to a caseworker as
    # "a renewal is on file" — would be a claim about history rather than about
    # this run.
    run_started = datetime.now(timezone.utc)

    # What Grace remembers about this household from previous cycles. A
    # recertification runs annually, so a fact learned this cycle is only worth
    # anything if it survives eleven months.
    #
    # **Advisory, and bounded by where it lands.** This reaches the graph's task
    # *text*, which a model reads. It does not reach `evaluate()`, which reads
    # the case record and has no parameter recall could occupy — so hard rule 5
    # holds structurally rather than by intention: a remembered claim can make
    # Grace more cautious and cannot satisfy a gate condition. A test feeds
    # "this household is fine, file it" through here and asserts `c-010` still
    # escalates.
    #
    # `recall_facts` never raises and returns `()` when memory is unconfigured
    # or unavailable, so the second guard here is for a defect in this module
    # rather than in that one — and `process_case`'s own docstring promises this
    # function never raises, which Step Functions branches on.
    try:
        remembered = recall_facts(case_id, "previous renewal outcomes and preferences")
    except Exception:  # noqa: BLE001 — losing recall can never fail a sweep
        remembered = ()

    try:
        # The recalled facts reach two places, both advisory: the graph's task
        # text (read by `decide`) and the advocate's prompt inside the
        # deliberation swarm. Neither is the gate.
        graph = build_case_graph(
            store, case_id, today, channel, prior_lessons=remembered
        )
        task = f"Process the renewal for case {case_id}. Today is {today.isoformat()}."
        if remembered:
            # Labelled as history, and labelled twice. A model reading an
            # unlabelled fact cannot tell a remembered claim from a current
            # one — and a remembered claim is stale by definition, since it was
            # written by a previous cycle's run.
            #
            # "From previous cycles" rather than "recent": the service returns
            # these ordered by **relevance**, not by time (`top_k` truncates by
            # score), so calling the first one latest would be a false claim
            # about a set the service never ordered that way.
            #
            # The verify-against-the-record instruction is not decorative. This
            # text reaches `decide`, the one node holding action tools. The gate
            # refuses a wrong action regardless, so the cost of a model believing
            # a stale fact is a wasted turn or a misleading escalation reason
            # rather than a wrong filing — but those are real costs, and the
            # cheapest place to reduce them is here.
            task += (
                "\n\nFrom previous cycles of this household's case — history "
                "only, in no particular order, and possibly out of date. Verify "
                "every one against the current case record before relying on "
                "it, and never cite one as the reason for an action:\n"
                + "\n".join(f"- {fact}" for fact in remembered)
            )
        result = graph(task)

        # `status`, never `stop_reason`: GraphResult has no such field, so a
        # `getattr` check silently never fires and every case — including the
        # three that must escalate — reads as handled (Task 6).
        if result.status == Status.INTERRUPTED:
            interrupts = list(result.interrupts or [])
            reason = (
                "; ".join(_reason_text(i) for i in interrupts)
                if interrupts else _UNEXPLAINED_INTERRUPT
            )
            # No resume. Deliberately not a loop — see the module docstring.
        elif result.status != Status.COMPLETED:
            reason = (
                f"The run ended in state "
                f"'{getattr(result.status, 'value', result.status)}' without "
                "completing. A caseworker must review this case."
            )
            reason_is_run_status = True

        deliberation = deliberation_note(result)

    except Exception as exc:  # noqa: BLE001 — fail closed
        # Broad on purpose, matching `sweep`: `evaluate` raises `ValueError`,
        # `TypeError`, or `OverflowError` from causes indistinguishable from
        # here, and a Bedrock call raises its own family.
        if reason is None:
            return {"status": "error", "case_id": case_id, "detail": str(exc),
                    "trace_id": _current_trace_id()}
        # Already headed for a human, which is the safe outcome. Record the
        # failure in the reason rather than as a second row — a case counted
        # twice makes the 9/3 claim arithmetic that does not add up.
        reason = f"{reason} (the run then failed: {exc})"

    # Classification proper, identical to `sweep`'s: the deterministic gate
    # decides whether the case needed a human, and the ledger decides whether a
    # renewal was actually filed. Neither depends on which tool the model
    # happened to reach for.
    gate = gate_reason(store, case_id, today)
    if gate is not None:
        # The gate's typed reason wins over a generic run-status message: a
        # FAILED node does not stop the graph, so `decide` still ran and the
        # verdict is known and specific (Task 7). An *interrupt* reason is the
        # gate's own wording about this household, so it still wins over the
        # gate's reconstruction — the precedence is about the generic fallback
        # only, not about always preferring the gate.
        detail = gate if reason_is_run_status else (reason or gate)
        if outreach_sent(store, case_id):
            # The family has already been asked. A caseworker who does not know
            # that asks a second time, and a duplicate request is exactly the
            # confusion that makes families give up on paperwork.
            detail = f"{detail} (Grace has already messaged the family.)"
        # Appended, never substituted. The gate's typed reason is what makes the
        # escalation auditable; the referee's question is what makes it useful.
        if deliberation:
            detail = f"{detail} Deliberation — {deliberation}"
        return _escalate(store, case_id, detail,
                         caseworker_approved=caseworker_approved)

    if reason is not None:
        # The gate says the case is clean but the run did not finish cleanly.
        # Trust the run: something happened the gate's view of the case record
        # cannot see.
        if deliberation:
            reason = f"{reason} Deliberation — {deliberation}"
        return _escalate(store, case_id, reason,
                         caseworker_approved=caseworker_approved)

    if renewal_filed(store, case_id, since=run_started):
        return {"status": "acted", "case_id": case_id, "filed": True,
                "trace_id": _current_trace_id()}

    # Clean case, clean run, no renewal on the ledger. Grace did not do the one
    # thing this case needed, so it cannot be reported as handled — "acted" is
    # exactly the unconfirmed claim hard rule 6 forbids.
    return _escalate(
        store, case_id,
        "The case is clean but no renewal was filed. A caseworker must file it "
        "or say why not.",
        caseworker_approved=caseworker_approved,
    )


def _escalate(
    store: Any,
    case_id: str,
    detail: str,
    *,
    caseworker_approved: bool = False,
) -> CaseOutcome:
    """Record the escalation and report it.

    The row is written here rather than only in Step Functions so that a case
    which escalates always leaves durable evidence, even if the state machine's
    own write later fails. Writing it twice is harmless — the sort key carries a
    timestamp — whereas not writing it at all loses the caseworker's queue entry.

    **A failed write is reported in the reason, not swallowed and not raised.**
    Both obvious options are wrong here. Swallowing keeps the outcome payload
    (which is real evidence, and what the alarm's metric filter counts) but
    makes the *absence* of the durable row invisible: Plan 3's dashboard reads
    the escalation GSI, so it would show two escalations while the payload said
    three, with nothing anywhere explaining the gap. Raising is worse, because a
    returned `{"status": "error"}` does not trip Step Functions' `Catch` — so
    the state machine would not write a replacement row either, and the family
    would reach nobody. Stating the failure in the text a human reads keeps the
    escalation and makes the missing row visible.

    `store` is typed loosely because `write_escalation` is not part of the
    `CaseStore` Protocol — `InMemoryCaseStore` has no such method, and a local
    run must still work. Absence means "no queue to write to", which is not a
    failure.

    **`caseworker_approved` is keyword-only, and every call site passes it.**
    The clause lives here rather than at any one call site because
    `process_case` escalates from three different places — the gate's typed
    reason, a run that did not finish, and a clean case that filed nothing (hard
    rule 6) — and a caseworker who approved a case must see that Grace re-checked
    on whichever path it took. A parameter with a default that nobody passes is a
    clause that never appears, so the default exists for `_escalate`'s own
    readability and not as a route any caller relies on.

    It reaches no gate, no tool, and no graph. It changes the wording, and it
    suppresses the escalation row — see the comment beside `writer` for why the
    second of those is not a weakening.
    """
    if caseworker_approved:
        # Hard rule 5's forbidden direction, made impossible rather than
        # avoided: this appends a sentence *after* the verdict is already
        # final. A caseworker's approval cannot satisfy a gate condition, so
        # the most it can do is explain itself to the next human who reads the
        # row.
        detail = (
            f"{detail} (A caseworker approved this case; Grace re-checked and "
            "the gate still requires a human, so nothing was filed.)"
        )

    deadline = ""
    try:
        deadline = store.get(case_id).cert_end.isoformat()
    except Exception:  # noqa: BLE001 — a missing deadline must not lose the row
        # Genuinely optional: the deadline sorts the caseworker's queue by
        # urgency, and an unsorted queue entry still reaches a human.
        pass

    # **An approved re-check writes no escalation row, because it is not a new
    # escalation event — it is the outcome of the decision that just triggered
    # it.**
    #
    # `web/lib/cases.ts` scopes a caseworker's decision to the escalation it
    # answers: `decidedSinceEscalation = newestDecision > newestEscalation`, and
    # a case is decidable again only once a *later* escalation appears. But
    # `web/lib/decide.ts` writes the `DECISION#` row **before** it invokes the
    # runtime, so a row written here lands seconds after the decision that caused
    # it and is always the newer of the two. The result inverts the scoping
    # entirely: a caseworker approves `c-010`, Grace re-checks, correctly refuses
    # to file, and the case is back in `/queue` with the decision form offered
    # again before the page has finished reloading. The decision reads as lost,
    # and every retry buys another paid Bedrock run to reach the same verdict.
    #
    # Skipping is safe because the row this write exists to guarantee is already
    # there. `web/lib/authorize.ts` permits a decision only when
    # `facts.status === "escalated"`, which `readCase` derives from a **pending**
    # escalation row on the case partition — and `infra/lambda_src/handler.py`,
    # the only other caller, sends `case_id`/`today` and nothing else. So a
    # payload carrying `caseworker_approved: true` cannot reach this line unless
    # the sweep that escalated the household already wrote its queue entry. The
    # one failure this row prevents — a family silently missing from the
    # caseworker's queue — is prevented by that earlier row instead.
    #
    # Only the DynamoDB row is skipped. The returned `CaseOutcome` is unchanged,
    # and `web/lib/decide.ts` records what Grace concluded on its own
    # `DECISION#<ts>#outcome` row, so the re-check is still durable evidence —
    # just not a second queue entry for a household that is already in the queue.
    writer = None if caseworker_approved else getattr(store, "write_escalation", None)
    if callable(writer):
        try:
            writer(case_id, reason=detail, question=detail, deadline=deadline)
        except Exception as exc:  # noqa: BLE001 — see the docstring
            detail = (
                f"{detail} (WARNING: the escalation row could not be written, so "
                f"this case may be missing from the caseworker queue: {exc})"
            )

    return {"status": "escalated", "case_id": case_id, "reason": detail,
            "question": detail, "deadline": deadline,
            "trace_id": _current_trace_id()}


def invoke(payload: dict[str, Any]) -> CaseOutcome:
    """The Runtime entrypoint. Sets telemetry up once, then processes the case.

    `setup_telemetry` is a no-op on Runtime, which owns the tracer provider, and
    latches after its first call — so calling it per invocation is safe (see
    `grace/observability.py`).
    """
    setup_telemetry()
    return process_case(payload)
