# Lessons: the graph spine and the deliberation swarm (Plan 1, Tasks 6–7)

Moved verbatim from `CLAUDE.md` on 2026-10-31. Each lesson was learned on a real defect; paths and counts are as of when it was written.

### What Task 6 established — follow these

**`GraphResult` has no `stop_reason` field.** Only single-agent `AgentResult` does. The plan's
sweep checked `getattr(result, "stop_reason", None) == "interrupt"`, which is always `False` on
a graph, so its escalation branch never executed and every case fell through to "handled
autonomously". Use `result.status == Status.INTERRUPTED` with `result.interrupts`
(`from strands.multiagent.base import Status`). This was caught by reading the SDK; nothing
about the failure was visible at runtime.

**Resuming an interrupt with a truthy response *approves* the blocked tool — and a denylist of
"escalate" words is itself fail-open.** The SDK's `SteeringHandler._handle_tool_steering_action`
does `can_proceed = event.interrupt(...)` and cancels the tool only `if not can_proceed`. Any
non-empty string is truthy, so a denylist approach (deny only on an exact match to a word
meaning "escalate") makes the *unrecognized* answer the dangerous one: confirmed against the
real executor, `"Escalate."` (one trailing period), `"no, hold this one"` (contains "no" but is
not equal to it), and `"needs review"` all resumed and filed a renewal for `c-010`, a household
missing a required document. `APPROVE_DECISIONS` in `grace/run.py` is an **allowlist** instead —
only an exact match to `{"approve", "yes", "file", "proceed"}` resumes the graph; everything
else, including anything unrecognized, denies. Re-verified end to end with a real Bedrock sweep
using `auto_decide="Escalate."`: 9/3, and none of the three escalating cases carry a
`renewal_submitted` row. **If you add a resume path anywhere, the polarity that fails closed is
always "resume only on an exact match to an affirmative," never "deny only on an exact match to
a negative."**

**A resume loop needs its own iteration cap — `set_max_node_executions` does not bound it.**
That setting bounds nodes *within* one graph invocation; a resume calls the graph again, so a
case that interrupts on every resume loops with no bound at all, and each round is a paid
Bedrock call. Confirmed by running one case to 500 resumes before hard-killing it.
`MAX_RESUME_ROUNDS` in `grace/run.py` caps this; exhausting it escalates with a reason saying
so, rather than spinning.

**Never derive a graph edge condition from a model's summary of a narrower question than the
one the edge is deciding.** A first version of the deliberation predicate matched substrings in
the `documents` node's free text — but `documents` only ever calls `list_documents`, so its
prose can never mention income, household size, or a source conflict. Measured against the real
fixtures: that version fired on `c-010` (a missing document, needing no deliberation — the
swarm exists to argue about ambiguous eligibility, not to conclude "the document isn't on
file") and stayed silent on `c-011`/`c-012`, the two cases a deliberation swarm exists for.
Widening the `documents` prompt to also relay income/conflict data would recreate the
`document_problems` bug one function up — asking a model to compare two numbers and describe
the difference in prose, when the comparison already has a deterministic answer.
**`make_needs_deliberation(store, case_id, today)`** replaces the free function: a factory
matching every other per-case component in this file, which re-runs `evaluate()` directly and
routes to the swarm exactly when a reason code is `material_income_change`,
`household_size_change`, or `source_conflict` — never for `missing_document`/`stale_document`/
window reasons, and never for a clean verdict. Verified against all twelve fixtures that this
matches `evaluate()`'s own reason codes on every case, not just the three named in the demo.
**There is no free function named `needs_deliberation` — call the factory to get a bound
predicate, and pass that as the edge condition.**

**Never classify a sweep outcome by whether an interrupt fired.** An interrupt means "the model
tried something the gate refused", which is not the same question as "did this case need a
human". Observed on a real run: on `c-010` (missing `proof_of_residency`) the model called
`send_family_message` rather than `submit_renewal`; the gate *correctly* allowed it, no
interrupt fired, and an incomplete household was reported as handled — 10/2 instead of 9/3, no
error. `sweep` now classifies from two things that cannot be argued with: `evaluate()` run
directly on the case (did it need a human) and the **ledger** (`renewal_submitted` — was a
renewal actually filed, hard rule 6). An interrupt still supplies the caseworker's wording and
still forces an escalation, but it is no longer the only thing that can produce one.

**Deadline math is a tool, not an agent — and that includes document freshness.**
`list_documents` used to report `received` plus `max_age_days` and leave the subtraction to the
model. On a real sweep the model got it wrong on **two of the nine clean cases**, reported
current documents as expired, and texted those families about paperwork that was in order.
`document_problems(doc, required, today)` in `grace/authority.py` now computes the verdict, both
`evaluate` and `list_documents` call it, and the tool states `CURRENT`/`STALE`/`EXPIRED`
outright. Shared for the same reason `_most_recent` is shared: a duplicated implementation
drifts, an import cannot. **Never hand a model two dates and ask it to compare them.**

**A fail-closed `try` must wrap every call that can raise, not just the one you already know
about.** `list_documents`'s `try` was widened once to add `Exception` around `load_pack`
(Task 4), then reused for `document_problems` when Task 6 introduced it — but
`document_problems` does the same date arithmetic `renewal_window` does, and the `try` block's
boundary had not moved to cover the loop that calls it. An out-of-range `max_age_days` raised
`OverflowError` from *inside* the loop, past the `except` that closed before it. Confirmed live
with a repro pack before and after the fix. When you extend a function that already has a
fail-closed `try`, check whether the new code is inside that `try`'s literal indentation —
"this function already fails closed" is not the same claim as "every line in this function is
covered by the `except`."

**The `decide` node must use `SequentialToolExecutor()`.** The default executor is concurrent,
and this model routinely requests `read_case`, `check_window`, `list_documents`, and
`submit_renewal` in a single turn. Run concurrently, `submit_renewal` reaches the gate before
the reads register in `_seen`, so the gate `Guide`s a correctly-ordered call and whether the
model retries is luck — the same clean case filed on one run and not the next, moving the split
to 8/4 with no error. Sequential execution also stops at the first interrupt instead of running
the rest of the batch, which is what a gate blocking an action should do.

**Only `decide` gets action tools, and only `decide` gets the gate.** `intake` and `documents`
receive `read_tools` alone, so no prompt reaching them can file anything — capability absence
(layer 1) applied per node, which is stronger than the gate. A second `AuthorityGate` on a read
node would also keep its own `_seen`, giving two gates that disagree about what happened.

**Every case must land in exactly one of `acted`/`escalated`/`errors`.** A case counted twice,
or counted nowhere, makes "nine handled alone, three escalated" arithmetic that does not add up
while each individual count still looks plausible. A case that escalates and then fails on
resume records the failure *in its escalation reason*, not as a second row.

**`grace/authority.py` gained `document_problems` and an import of `RequiredDocument`.** The
purity rule still holds — no `strands`, no `boto3`, no I/O — and
`test_authority_imports_only_pure_siblings` whitelists the addition.

### What Task 7 established — follow these

**A Swarm ends when a node finishes its turn without calling `handoff_to_agent`, and that made
a three-model deliberation collapse to one model — silently, reporting `COMPLETED`.** Measured
on real `c-011` runs through the graph, `node_history` came back `['advocate']`,
`['advocate']`, `['advocate', 'referee']`. Status was `COMPLETED` every time; nothing in the
result distinguishes a collapse from a real deliberation except `node_history`. Two independent
causes, both fixed, both needed:

1. *The prompts described when to hand off, not that they must.* Each debater's prompt now
   names its own successor (`agent_name="verifier"` / `agent_name="referee"`) and makes the
   handoff mandatory including when the advocate cannot make the case at all.
2. *`Graph._build_node_input` prepends every upstream node's output to a nested Swarm's task*,
   so the advocate opened by reading `documents` saying "all required documents are present and
   current", believed it, and concluded there was nothing to argue. Reproduced deterministically
   outside the graph by passing that same ContentBlock list: 2 of 3 runs collapsed with it, 0 of
   4 without. The advocate is now told up front that a deterministic check already found a
   question, that a document summary cannot settle an income/size/conflict question, and that
   "the case looks fine" is not a conclusion it may reach alone.

**The referee will hand back if you leave it any room to.** "Do not hand off further — you
conclude" was not enough: on a real run the referee handed to the advocate, the swarm cycled
`a→v→r→a→v→r→a→v`, hit `Max handoffs reached: 8`, and reported FAILED — eight paid calls to
produce nothing, when the first three had already produced a conclusion. The prompt now says
`NEVER call handoff_to_agent` and closes the escape hatch it used ("if the argument is
incomplete, that is itself a reason to answer AMBIGUOUS"). `max_handoffs`/`max_iterations` are
6, not the plan's 8: three turns plus one retry round.

**`repetitive_handoff_min_unique_agents=2` can never fire on a two-agent ping-pong.** The SDK
stops the swarm only when `unique_nodes < min_unique_agents`, so the plan's `window=4,
min_unique=2` evaluates `2 < 2` on `[advocate, verifier, advocate, verifier]` — `False`,
continue. Detection was configured, passed a `> 0` assertion, and could not trigger. It is 3
now. **A test asserting a loop-safety setting is present is not a test that it fires** — drive
`SwarmState.should_continue` with the real values instead.

**`Swarm.nodes` is a `dict[str, SwarmNode]`.** The plan's `{n.name for n in swarm.nodes}` raises
`AttributeError: 'str' object has no attribute 'name'`. Use `swarm.nodes.keys()`, and
`swarm.nodes[role].executor` to reach the `Agent`.

**Every swarm agent needs `description=`.** `Swarm._build_node_input` gates the
"Agent description:" line on it, so an agent without one is offered to the others as a bare
name with no stated role. Nothing crashes; routing just gets worse invisibly.

**A `Swarm` node breaks any test that assumes every graph node is an `Agent`.** It has no
`.model`, no `.tool_names`, and no `_session_manager` (its session manager is the *public*
`session_manager`). Three Task 6 tests raised `AttributeError` on it and a fourth —
`test_no_node_has_its_own_session_manager` — passed **vacuously** via `getattr(..., None)`.
Recurse into `executor.nodes` rather than skipping: the three models inside the swarm are
exactly the ones hard rule 2 is about.

**A FAILED node does not stop the graph, so a FAILED status must not displace the gate's
reason.** `decide` still ran after the swarm failed (verified against the SDK and directly),
and `evaluate()` had a specific verdict the whole time — but the row read "The run ended in
state 'failed'" and dropped `material_income_change: Income moved 30.0%`, the one fact the
caseworker needed. `sweep` now prefers the gate's typed reason over the generic run-status
fallback, tracked with an explicit flag rather than by re-comparing strings.

**The referee's conclusion is appended to the escalation row, never substituted, and is read by
key.** `_deliberation_note` in `grace/run.py` searches the referee's prose for `AMBIGUOUS:`/
`CLEAR:` — which Task 6 established is wrong for an *edge condition*, and is acceptable here
only because the classification is already final before the note is read: the case escalated
because `evaluate()` said so. Confirmed on a real run where the referee concluded **CLEAR** for
`c-011` and the case escalated anyway. The referee is selected from `SwarmResult.results` by the
`"referee"` key, never `node_history[-1]` — a positional fallback would print the advocate's
unchecked argument to a caseworker as though a verifier had confirmed it.

**The swarm's `execution_timeout` must bind before the graph's `node_timeout` — and the margin
that matters is the sum of both swarm budgets, not the execution budget alone.** The graph
applies `node_timeout` to a nested Swarm as a whole and a graph node timeout is *fail-fast*: it
raises out of the graph call, `decide` never runs, and `sweep` records an **error** (exit 1, no
escalation row). The swarm hitting its own budget reports FAILED instead, the graph marks the
node failed without raising, and `decide` still escalates. Same wall clock, opposite outcome for
the family — so the swarm's budget must be the smaller number. **An earlier fix set the graph's
node timeout to 330s, reasoning it only had to clear the swarm's 300s `execution_timeout` — that
is the wrong margin.** `SwarmState.should_continue` checks `execution_timeout` only at the top
of its loop, *before* a node starts, so a node beginning at 299s still runs to completion, up to
its own `node_timeout` (90s). The true worst case is `execution_timeout + node_timeout = 390s`,
and 330s does not clear it — reproduced at 1/30 scale with a sleeping fake model, no Bedrock
cost: with the graph timeout between the swarm's `execution_timeout` alone and the true sum, the
graph's timeout fired first and `decide` never ran, exactly the fail-fast outcome this setting
exists to avoid. Fixed to `set_node_timeout(420.0)`. **If you change any of the three numbers
(swarm `execution_timeout`, swarm `node_timeout`, graph `node_timeout`), re-derive the inequality
as `swarm.execution_timeout + swarm.node_timeout < graph.node_timeout` — do not eyeball it
against `execution_timeout` alone.**

**The referee's `AMBIGUOUS:`/`CLEAR:` extraction must not depend on which marker is listed
first in `_REFEREE_VERDICTS`.** An earlier version of `_deliberation_note` iterated the tuple
and returned on the first marker *found in tuple order*, not the first the referee actually
concluded. Confirmed live: reordering `_REFEREE_VERDICTS = ("AMBIGUOUS:", "CLEAR:")` to
`("CLEAR:", "AMBIGUOUS:")` changed nothing about a referee's real output but silently reported a
CLEAR verdict on a case the referee had called AMBIGUOUS — and every test at the time still
passed. That is hard rule 5's forbidden direction: a deliberation step making Grace *less*
cautious. Neither "first in tuple order" nor "earliest position in the text" is a safe fix — a
referee reasoning "I first considered CLEAR: ..., but ultimately AMBIGUOUS: ..." states CLEAR
first and means AMBIGUOUS either way. The fix anchors to a marker that **starts its own line**
(the referee's prompt says to answer that way), checked across every line rather than only the
first, because an unclosed `<thinking>` tag can leave reasoning text ahead of the real answer. A
marker with no line-start anchor anywhere now honestly returns "the deliberation did not state a
conclusion" rather than guessing — the case still escalates on the gate's own reason regardless,
since this function only supplies wording. **Never search a model's output for a set of
mutually-exclusive markers by iterating a collection and returning the first match — the
collection's order is not a property of the model's answer.**
