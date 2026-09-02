# CLAUDE.md — Grace

Guidance for Claude Code and subagents working in this repository.

---

## What this is

**Grace** is an AI agent that keeps families from losing benefits over paperwork.

During Medicaid unwinding, the majority of disenrollments were **procedural** — people who
still qualified but missed a letter, a deadline, or one document. They did not become
ineligible. They lost coverage to paperwork.

Grace watches every household's Medicaid/SNAP recertification clock, files the renewals that
are unambiguous, chases the one missing document by SMS in the family's language, and wakes a
human caseworker **only** when eligibility is genuinely in doubt.

Built for the **AWS Agents for Humans Hackathon** (Good Neighbor track), deadline
**2026-09-14 17:00 PT**.

- Spec: `docs/superpowers/specs/2026-08-28-grace-design.md`
- Plan 1 (core, local): `docs/superpowers/plans/2026-08-28-grace-core.md`
- Plans 2 (AgentCore deploy) and 3 (dashboard) to follow.

---

## Current state

**Plan 1, Task 6 complete.** 285 tests passing (`.venv/bin/python -m pytest`, or
`.venv/bin/pytest` directly). `grace sweep` runs end to end against real Bedrock and reports
**9 acted / 3 escalated**, stable across consecutive runs including against a caseworker
answer that would have exploited the resume fail-open below. Task 7 — the deliberation swarm —
is next; `make_needs_deliberation` already exists and is tested, waiting to be called and
wired to a conditional edge.

| Task | State |
|---|---|
| 1 — rule packs + deadline math | **done** — `grace/rules/{pack,clock}.py`, 48 tests |
| 2 — case types, store, 12 fixtures | **done** — `grace/cases/{models,store}.py`, `fixtures/households.yaml`, 60 tests |
| 3 — the authority gate | **done** — `grace/authority.py`, 121 tests total. The task that matters most. |
| 4 — Nova model registry + tools | **done** — `grace/models.py`, `grace/tools/{read,action}.py`, 157 tests total |
| 5 — `AuthorityGate` + `LedgerHook` | **done** — `grace/{steering,ledger,vendored_actions}.py`, 212 tests total. Capability absence is now real enforcement, not shape. |
| 6 — Graph spine + `grace sweep` CLI | **done** — `grace/{graph,run}.py`, 285 tests total. The first runnable end-to-end path. **Read "What Task 6 established" below before touching the sweep or the swarm** |
| 7 — deliberation swarm | next — call `make_needs_deliberation(store, case_id, today)` (already written and tested) to get the conditional-edge predicate for a `deliberate` node |
| 8 — trajectory evals | not started |
| 9 — ledger/trace correlation | not started |

`pyproject.toml`, `LICENSE`, `.gitignore`, `.env.example`, `README.md` all exist and are
committed — **do not recreate them.** Dependencies are installed in `.venv`; no install step is
needed to run tests.

### What Task 1 established — follow these

**`load_pack` raises `InvalidRulePack` and nothing else.** One exception type for missing,
unreadable, malformed, mislabelled, and out-of-range packs, so a caller fails closed with a
single `except InvalidRulePack`. Task 4's `check_window` must wrap it; Task 3's `evaluate` must
not rely on `pack is None` alone, because a *partially* corrupt pack never reaches that check.
This also covers a non-string `program`/`state` now — see Task 2 below.

**Rule-pack input is untrusted.** `program`/`state` reach `load_pack` from case records and, in
Plan 2, from a Gateway payload. Path containment, a non-empty `required_documents`, and finite
numeric thresholds are all enforced there — an empty document list would make
`missing_document` unreachable, and a `NaN` threshold disables the income check silently because
every comparison against `NaN` is `False`. Do not relax these.

**`overdue` and `in_grace` are both actionable.** Grace *does* file a late renewal inside the
grace period — that is the procedural save it exists to make. Only `not_open` and `closed`
escalate on window grounds. The two are distinguished for the caseworker briefing, not for the
gate.

**Pin the date.** Every test module uses `TODAY = date(2026, 10, 1)`. Fixture `c-002` goes
`closed` on 2026-10-31, so a `date.today()` anywhere in the sweep turns the 9-act/3-escalate
demo into 8/4 on that date. Task 6's CLI takes `--today` defaulting to the pinned value.

### What Task 2 established — follow these

**`reported_income_cents`/`reported_size` are `int | None`, and `None` means "not reported."**
Not the household's on-file value, and never `0` — `0` is a real income a family can report
(a genuine loss of all income is the single most eligibility-relevant case Grace will see), so
it cannot double as an absence marker. **Task 3's `evaluate` must treat `None` as "no income or
size check applies" and short-circuit before comparing** — do not compute a percentage change
or an inequality against `None`. See the note at the top of Task 3 in the plan.

**`LedgerEntry.detail` is an immutable, type-checked mapping, not a plain `dict`.** Values are
restricted to JSON-safe scalars (`str | int | float | bool | None`) and the mapping itself is
frozen at construction. A caller holding a `ledger()` result cannot rewrite an audit entry —
Task 8's evals read the ledger as ground truth, so a mutable `detail` would let something
retroactively change what the eval sees. `LedgerEntry.at` must be a timezone-aware `datetime`;
a naive one raises at construction, before it can end up unsortable next to an aware one.

**Fixture and fail-closed loader conventions carry forward.** `load_fixture_cases` raises
`InvalidFixtureData` (parallel to `InvalidRulePack`) for a non-string field or a malformed
`source_conflicts`, rather than silently coercing. Quote every YAML scalar in new fixtures —
an unquoted `no`/`yes`/`on`/`off` parses as a boolean, and an unquoted phone number as an int.

### What Task 3 established — follow these

**Never select "the" document for a `doc_id` by record order.** A household can have multiple
copies of the same document on file (a re-submission leaving the old one in place). Selecting
by position — including a naive `{d.doc_id: d for d in documents}` dict comprehension, which
is last-wins by *order*, not by which copy is actually newest — makes the verdict depend on how
the record happened to load. This was a real, confirmed bug: two documents with an identical
`received` date and different `expires` produced opposite verdicts purely from tuple order.
Selection must be a deterministic function of the data (see `_most_recent` in
`grace/authority.py`), and a duplicate may only make a verdict *stricter*, never looser.

**Every failing condition gets its own reason — never `elif` between independent checks.** A
document can be both stale-by-age and past its own `expires` at the same time; both facts must
reach the caseworker brief. If you add a new condition to the gate, ask whether it can co-occur
with an existing one and use `if`, not `elif`, unless the conditions are genuinely mutually
exclusive.

**`evaluate` can still raise `ValueError` or `TypeError`, and that is correct — the caller must
catch broadly.** A structurally invalid `RulePack` (bypassing `load_pack`'s own validation via
direct construction) makes `renewal_window` raise, or a missing threshold raises a different
type from the same underlying cause. Converting either to a `GateResult` would be worse: a
`verification_error` result is indistinguishable from a normal escalation at the call site, so
a caller that logs and moves on would silently treat pack corruption as routine. **Task 5's
`steer_before_tool` and Task 6's sweep must wrap the `evaluate` call in `except Exception`, not
`except ValueError`** — catching only one exception type leaves the other to escape the
steering handler into the agent loop.

**`GateReason.detail` carries untrusted free text and is deliberately unescaped.**
`source_conflicts` is case-record text surfaced verbatim, including as a potential
prompt-injection vector into whatever agent eventually reads it (the caseworker briefer is
agents-as-tools, per the architecture). `authority.py` has no rendering context to know the
right escaping strategy — HTML for a UI, parameterization for DynamoDB, something else again
for a model prompt — so it does not escape at all. Whichever surface renders `detail` is
responsible for escaping it there. Do not add escaping logic to `authority.py`.

**Reason order is not a contract.** `GateResult.reasons` follows check order, which has already
changed once. Do not surface `reasons[0]` as "the" reason for a case (e.g. in a span attribute
or a briefing) without picking deliberately — compare on `.code`, or treat `reasons` as a set.

### What Task 4 established — follow these

**Read tools share `_most_recent` with the gate — never a second dict comprehension.**
`grace/tools/read.py`'s `list_documents` imports `_most_recent` from `grace.authority` directly,
rather than reimplementing document selection. `list_documents` is what a model reads before
deciding whether to call `submit_renewal`; `evaluate` is what actually permits it. If the two
selected different copies of the same `doc_id`, the model would reason from facts the gate does
not share, and the disagreement would be invisible — no error, tool says "current," gate says
"stale." A duplicated implementation can drift out of sync; an import cannot.

**A read tool that catches only `(InvalidRulePack, ValueError)` still fails open.** `load_pack`
can return a pack whose values are individually valid but whose date arithmetic overflows —
`grace_period_days_after_end: 999999999` loads cleanly, then `renewal_window` raises
`OverflowError`, a third exception type from the same underlying cause Task 3 already warned
about. `check_window` now catches `Exception` broadly, matching the discipline Task 3 already
mandates for `evaluate`'s callers. If you add a new read tool that calls `renewal_window`,
catch broadly there too — narrowing to "the exceptions I've seen so far" is how this bug
happened the first time.

**A `Channel`'s `send()` return value must be coerced to `str` before it reaches the ledger.**
`Channel` is a plain `Protocol`, not `@runtime_checkable`, so its `-> str` annotation is
enforced by nothing. Plan 2's real SNS implementation will naturally return a boto3 response
shape (a dict), which `LedgerEntry`'s scalar-only contract rejects — *after* the message has
already been sent. That is hard rule 6 inverted: the family was contacted and the audit trail
says nothing happened. `send_family_message` wraps the call in `str(...)` for exactly this
reason; any new action tool that logs a channel's return value must do the same.

**A no-argument tool spec is not enforced by rejection — it is enforced by absence.** Verified
against the real invocation path (`tool.stream()`, not a direct Python call): an injected
`{"case_id": "c-002"}` argument on a zero-argument tool is *silently discarded*, and the call
succeeds against the bound case. There is no error, no signal that an injection was attempted —
it looks identical to a normal call. CLAUDE.md's "no parameter to poison" phrasing is correct
about the outcome and imprecise about the mechanism: nothing validates and rejects an
injected argument, because the parameter simply does not exist for `strands` to bind it to.

**Closure-cell mutation is a real gap, and deliberately not defended against.** `case_id` and
`store` are ordinary Python closure variables; rewriting `read_case.__closure__[i].cell_contents`
redirects the tool (confirmed). This requires arbitrary code execution in the agent's own
process, which is a different threat class than prompt injection — an attacker who can rewrite
closure cells can equally reach `store` and `evaluate` directly, so no binding strategy in
`read.py` would close this. Do not attempt to harden the closure; the boundary that matters is
the process boundary, not the variable-binding strategy inside it.

**`_UNVERIFIABLE` was a courtesy string with nothing behind it, until Task 5.** Before Task 5,
no code outside `test_authority.py` called `evaluate` at all — a read tool returning
`_UNVERIFIABLE` did not, by itself, force anything. Task 5's `AuthorityGate` is what turns it
into enforcement: it evaluates independently on the same case before every action tool, so the
string is now a hint to the model, not the mechanism.

**Model-ID guards must scan the whole package, not a list of modules someone remembered.**
`pkgutil.walk_packages` over `grace/`, not a hardcoded tuple — confirmed a real Claude
inference-profile ID inlined into `read.py` passed all 157 tests before this fix, because the
non-Nova-vendor check ran against `models.py` only. Any new module (`grace/steering.py`,
`grace/ledger.py` from Task 5) is covered automatically because the test discovers modules from
disk, not because anyone remembered to add them.

### What Task 5 established — follow these

**A `SteeringHandler`'s exception is swallowed, not propagated — verify this before touching
`steer_before_tool` again.** `SteeringHandler.provide_tool_steering_guidance` (the SDK's own
dispatcher — read its source, do not take this on faith) wraps the call to `steer_before_tool`
in `except Exception: return`, logs at debug level, and leaves `cancel_tool` unset. Confirmed
empirically: a handler that raises produces `cancel_tool == False`, and the tool executes
*ungated*. This is why every fallible call inside `steer_before_tool` — including `evaluate`
itself, which can raise `ValueError`, `TypeError`, or `OverflowError` from a pack that loaded
cleanly — sits inside one `except Exception` that returns an `Interrupt`. A narrower `except`
here is not merely incomplete; it is silent fail-open on the one method whose entire purpose is
failing closed, and nothing in the agent loop will tell you it happened.

**`submit_renewal` and `send_family_message` are gated on different questions.** Filing needs a
fully clean `evaluate()` verdict. Outreach needs only that every reason is `missing_document` or
`stale_document` (`DOCUMENT_ONLY_CODES` in `grace/steering.py`) — a case that is *also* off on
income, size, or a source conflict must still escalate, because texting the family does not
resolve an eligibility question. If you add a new action tool, decide explicitly which of these
two questions it answers; do not assume "gated" means "needs the same verdict as filing."

**Two different `Interrupt` classes exist — do not confuse them.**
`strands.vended_plugins.steering.Interrupt` (what `steer_before_tool` returns; `type`/`reason`
only, no `.id`) is unrelated to `strands.interrupt.Interrupt` (the multi-agent resume type from
Appendix B.1, with `id`/`name`/`reason`/`response`, used to resume a paused `Graph`/`Swarm`).
They share a name and nothing else.

**The ledger is asymmetric between `Guide` and `Interrupt` — Task 8's evals must account for
this, not discover it.** On `Guide`, the SDK builds a synthetic error `ToolResult` and fires
`AfterToolCallEvent`, pairing `tool_call` with `tool_result` in the ledger. On `Interrupt`, the
SDK yields a `ToolInterruptEvent` and returns *before* the after-hook, so the ledger gets
`tool_call` with **no paired result**. An eval that reads an unpaired `tool_call` as "a tool ran
and was not logged" is backwards for an escalated case: it means the tool did not run. This is
SDK behavior, not a choice made here, and it is pinned in `tests/test_steering.py` rather than
worked around.

**`AuthorityGate._seen` is per-instance, in-memory, and does not survive a fresh process.** It
cannot drift from the ledger on the read path — both are driven off the same
`BeforeToolCallEvent`. **Task 6 tested the resumed-run case and it is fine in-process:**
`Graph._build_node_input` restores the node executor's `messages`, `state`, `_interrupt_state`,
and `_model_state` on resume but never touches the plugin registry, so the same `AuthorityGate`
instance is reused and `_seen` still holds every prior read (verified against a real graph
resume, and pinned by `test_the_gates_seen_set_survives_an_in_process_resume`). A resume in a
*new* process (Plan 2, via a session manager) would start with an empty `_seen` — still not
fail-open, since an empty `_seen` makes the gate stricter, but it would `Guide` once before
proceeding.

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



## The one idea that matters

Grace's defining property is an **escalation boundary**: it acts alone on the routine and
*provably* escalates the rest. Almost every design decision follows from that.

The boundary is enforced three ways, strongest first. When you touch this code, know which
layer you are in.

**1. Capability absence.** Privileged tools are not registered in the agent's tool list at
all. `submit_renewal` does not appear in `list_tools_sync()` for a case that has not passed
verification. Grace cannot file a renewal it should not, because the capability does not
exist in that context. This beats any instruction, because there is nothing to disobey.

**2. Identity from the session, never the conversation.** Every household-scoped read tool
takes **no arguments** — `properties={}`. The case is bound at construction from the
authenticated session. A prompt injection cannot redirect Grace to another family's record
because there is no parameter to poison. **Never add a `case_id` or `household_id` argument
to a tool.**

**3. A deterministic gate.** `grace/authority.py` is pure Python — no model, no I/O, no
`strands` import — mapping case facts to `act` or `escalate`. `grace/steering.py` adapts it
into a `SteeringHandler` that returns `Proceed` / `Guide` / `Interrupt` before every
state-changing tool call.

**Fail closed.** Any error during verification escalates. Never write
`except Exception: pass` around an eligibility, ownership, or document check. The reference
implementation Grace learned from fails *open* in three places; that is unacceptable when
the consequence is a family losing coverage.

---

## Hard rules

These are not stylistic preferences. Breaking one is a bug.

1. **Amazon Nova only.** No third-party LLMs in the request path. Model IDs live in
   `grace/models.py` and are referenced by role (`nova("verifier")`), never inlined.
2. **The advocate, verifier, and referee must run three different models.** Two instances of
   the same model agreeing proves nothing, and nothing should referee its own argument.
   `ADVOCATE` is Nova 2 Lite, `VERIFIER` is Nova Pro, `REFEREE` is Nova Micro. If you change
   one, keep all three distinct. **Nova Premier is Legacy and blocked by the provider** —
   `Converse` returns `ResourceNotFoundException`, and there is no `nova-2-pro`, so Nova Pro is
   the strongest model available. **Never use `nova-lite-v1:0` in a gated role**: under test it
   filed a renewal it had been explicitly told not to file. See Task 4.
3. **All household data is synthetic.** Real PII must never enter this repo. Fixture phone
   numbers use the reserved `+1555` range and names are obviously fictional; a test asserts
   both.
4. **`grace/authority.py` stays pure.** No `strands`, no `boto3`, no file or network I/O.
   Its exhaustive testability depends on this. A test greps for violations.
5. **Reflection lessons are advisory only.** They may make Grace *more* cautious. They may
   never satisfy a gate condition.
6. **Never claim an action succeeded without tool confirmation.** Grace must not tell a
   family their renewal was filed unless `submit_renewal` returned successfully. This is the
   specific failure this system must not have.
7. **Escalating is always allowed.** `escalate_to_caseworker` is never gated.
8. **Never remove the span-redaction token.** `OTEL_SEMCONV_STABILITY_OPT_IN` must keep the
   `gen_ai_unredacted_attributes=` suffix. Its value lists what to leave *unredacted*, so the
   empty value means "redact everything"; **absence of the token disables redaction entirely**
   and exports the full household record to CloudWatch. The trailing `=` is load-bearing.
9. **Never put household identity in a span attribute.** `trace_attributes` are exported
   verbatim — the rule-8 policy covers only the five `gen_ai.*` content attributes, not custom
   ones. `grace.case_id` yes; name, phone, or address never. Same rule as the JWT `sub`.

---

## Working here

### Environment

```bash
.venv/bin/python -m pytest          # run tests
.venv/bin/python -m grace.run sweep --auto escalate   # local end-to-end
```

Python 3.12 via `uv`. AWS: account `<AWS_ACCOUNT_ID>`, `us-east-1`, Bedrock Nova enabled.

### Dependencies — deliberately minimal

`strands-agents[otel]==1.54.0`, `boto3`, `pyyaml`, plus pytest for dev. That is all.

The `[otel]` extra declares exactly **one** package
(`opentelemetry-exporter-otlp-proto-http`), which costs 10 transitively — all first-party OTEL
or protobuf, 52 → 62 on the clean venv. **Do not add `aws-opentelemetry-distro`** — the AWS
docs call for it and for running under `opentelemetry-instrument`, but only for agents hosted
*outside* AgentCore Runtime. Runtime instruments itself.

**Do not install `strands-agents-tools`.** It pulls `slack-bolt`, `slack-sdk`,
`beautifulsoup4`, `pillow`, `sympy`, and `watchdog` — 30 packages Grace never imports. The
`graph`/`swarm`/`workflow` *tools* live there; Grace uses the `GraphBuilder` and `Swarm`
**classes** from the core SDK, because Grace's topology is fixed at build time rather than
chosen by a model.

`boto3` and `pyyaml` arrive transitively via `strands-agents` but are declared explicitly,
because Grace imports both directly.

### Verify the SDK, do not trust its docs

`strands-agents` moves fast and the published documentation is wrong in several places that
matter. Always introspect:

```bash
.venv/bin/python -c "import inspect; from strands.multiagent import GraphBuilder; print(inspect.signature(GraphBuilder.add_edge))"
```

Known doc errors, verified against 1.54.0:

| Docs say | Reality |
|---|---|
| `event.cancel_tool()` | Attribute assignment: `event.cancel_tool = "reason"` |
| `S3SessionManager(bucket_name=, region=)` | `S3SessionManager(session_id, bucket, prefix, region_name)` |
| `/users/{actorId}/facts` memory namespace | Working code uses `/facts/{actorId}` — must match the `namespaceTemplates` set at memory creation |
| `agentcore configure` / `launch` | Current CLI is `agentcore create` → `add` → `deploy` |
| `Interrupt` usable in `steer_after_model` | Type-invalid. `ModelSteeringAction = Proceed \| Guide`. Gate in `steer_before_tool` |
| Enabling `gen_ai_latest_experimental` protects span content | It does **not**. Redaction needs the separate `gen_ai_unredacted_attributes=` token; without it every prompt and tool result exports verbatim |

Also: `strands.experimental.steering` is the **old** import path. Use
`strands.vended_plugins.steering`, re-exported through `grace/vendored_actions.py` so a
future move is a one-file change.

### Two SDK behaviours that will surprise you

**Multi-agent interrupts use `result.status`, not `result.stop_reason`.** A Graph or Swarm
signals an escalation with `result.status == Status.INTERRUPTED` and carries
`result.interrupts`; only single-agent invocations use `stop_reason == "interrupt"`.
`GraphResult` has no `stop_reason` field at all — confirmed in Task 6, where the plan's
`getattr(result, "stop_reason", None)` check silently never fired. Respond with `interrupt.id`
(distinct from `interrupt.name`), and never send a null response — the server refuses it. **A
truthy response approves the blocked tool**; see "What Task 6 established". See Appendix B.1.

**Python Graph uses OR semantics.** A node fires when *any* incoming edge is satisfied
(TypeScript uses AND). `decide` has three incoming edges and firing on the first satisfied
one is intended — do not "fix" it. See Appendix A.1 of the plan.

**Python accumulates node state on revisit** unless `reset_on_revisit` is enabled. Grace
builds a fresh graph per case, so it does not bite today.

**Agents inside a Graph or Swarm must not have their own `session_manager`** — only the
orchestrator may. Python raises `ValueError` otherwise.

**AgentCore Gateway renames every tool to `<target>___<tool>`** (three underscores). The
authority gate must strip that prefix before matching against `ACTION_TOOLS`, or every
gateway-provided action tool silently bypasses the gate — the exact failure this design
exists to prevent. See Appendix C.1.

**Never use `GetWorkloadAccessTokenForUserId`.** It treats the user ID as an opaque string
with no verification, so an authenticated caseworker could pass any household ID and receive
a token scoped to that household. Use `GetWorkloadAccessTokenForJWT` (validates issuer,
signature, expiry) and explicitly `Deny` the `...ForUserId` action in the execution role.
This also rules out the `BedrockAgentCoreFullAccess` managed policy, which grants it.
See Appendix D.1.

**The JWT `sub` claim must be an opaque ID, never a name or email** — inbound JWT claims are
logged to CloudTrail, which is outside the guardrail's PII redaction. See Appendix D.4.

**`StrandsTelemetry()` hijacks the global tracer provider** — constructing it calls
`trace_api.set_tracer_provider(...)` as a side effect. On AgentCore Runtime that replaces a
working provider with a second one, so skip it there; gate on `AGENT_OBSERVABILITY_ENABLED`,
which Runtime sets. Also: exporters are opt-in, so traces are created and silently dropped
until `setup_console_exporter()` or `setup_otlp_exporter()` is called, and failed exporter
setup is logged rather than raised. See Appendix E.3.

**CloudWatch Transaction Search is a prerequisite, not an optimization** — without it AgentCore
spans are not searchable at all. One-time per account, up to ten minutes to take effect. Do it
well before recording the demo, not on the day. See Appendix E.5.

### Framework wiring cheat sheet

| Thing | Attaches via |
|---|---|
| `SteeringHandler`, `AgentSkills`, `ContextOffloader` | `plugins=[...]` |
| `HookProvider` | `hooks=[...]` |
| Conversation strategy | `context_manager="auto"` (verified present in 1.54.0) |
| Serial tool execution | `tool_executor=SequentialToolExecutor()` — required on any gated node, see Task 6 |

---

## Architecture

Per AWS guidance, the agent is a capability inside the system, not the backend. Grace follows
the **Deep Automation** pattern — its value is in backend process, not a chat UI:

```text
EventBridge (daily sweep) → Step Functions → Lambda → AgentCore Runtime
                                                          │
                            Gateway (rule packs, documents) ┤
                            Memory (per-household facts)    ┤
                            DynamoDB grace-cases (ledger)   ┤
                            SMS channel                     ┘

Next.js dashboard → API layer → invoke_agent_runtime   (never client → agent)
```

### Agent structure — all three multi-agent patterns, each where it fits

**Graph** (deterministic spine), conditional edges:

```text
intake → documents → eligibility(Swarm) → decide ─┬─(gate passes)→ act
                                                  └─(else)───────→ escalate
```

Deadline math is a **tool, not an agent**. Deterministic work does not need a model.

**Swarm** (genuine deliberation) — runs *only* on ambiguous cases. Three opposed roles:
advocate argues the family qualifies, verifier adversarially checks each claim against
readable facts, referee decides whether it is genuinely ambiguous. Every agent needs a
`description=`: Python's swarm builds a routing context from them, so omitting one makes the
swarm route blind.

**Agents-as-tools** (context isolation) — outreach drafter, policy retriever, caseworker
briefer. `callback_handler=None`, return `str(response)`.

Loop safety on the swarm is mandatory: an advocate and a verifier ping-pong forever
otherwise. `repetitive_handoff_detection_window` must be **less than** `max_iterations`, or
the iteration cap trips first and detection never fires.

### Ledger

Hooks append every node transition and tool call to a per-case ledger in DynamoDB. In a
benefits context an audit trail is a requirement, not a feature — and this ledger is also
the demo: nine cases handled alone, three escalated, each with a reason.

The ledger is the ground truth for what executed. Trajectory evals read from it rather than
the model transcript, because a transcript-based eval would miss a tool that ran but was not
logged.

### Observability — traces complement the ledger, they do not replace it

The ledger records *what Grace decided and did*, durably, in DynamoDB. Traces record *how long
it took and in what order*, sampled, in CloudWatch. A trace can be dropped by sampling; a
ledger entry cannot. **Never move an eval assertion from the ledger to a span.**

Every ledger entry carries the active OTEL trace ID (32 lowercase hex) so a DynamoDB row joins
to its CloudWatch trace. The interesting test is that the two agree: a tool in
`GraphResult.execution_order` with no ledger entry means a tool ran without being logged.

Attach `grace.case_id`, `grace.program`, `grace.window_status`, `grace.gate_decision`, and
`grace.gate_reason` via `trace_attributes=`. Filtering Transaction Search on
`grace.gate_decision = "escalate"` should return exactly three traces — that query is the
demo's central claim, executed rather than narrated. Note `Agent.__init__` silently drops
non-scalar values, so pass `gate.reason or ""`, never `None`.

Alarm on **escalation count `< 3`**, not on error rate. Acting when Grace should have escalated
produces no error, no throttle, and no latency spike — it looks like success.

See Appendix E of the plan for the full detail.

---

## Testing

Run `.venv/bin/python -m pytest` before claiming anything works.

`grace/authority.py` is table-tested exhaustively — one case per gate condition, plus a
multi-problem case, plus the fail-closed path. This is where a bug means a family loses
coverage, so it is tested first and hardest.

The fixture set encodes the demo: **12 households, 9 clean, 3 that must escalate** —
`c-010` missing a document, `c-011` a material income change, `c-012` a source conflict. If
a clean case escalates the gate is too strict; if one of those three acts it is too loose.
Either is a bug worth stopping for.

Trajectory evals (`evals/`) assert the gate ordering holds against real model runs:
`read_case`, `check_window`, and `list_documents` always precede any action.

---

## Hackathon constraints

- **Newly created work only.** No code from `OpenClaw`, `RosettaCloud`, `astrolabe`,
  `TheAgentOrg`, or `AWS-Resource-Optimizer-Agent`. Patterns and hard-won API knowledge are
  reused as *knowledge*; that reuse is disclosed in the README. Do not copy files in.
- **MIT license** at repo root, visible in the GitHub About section.
- Required at submission: public repo, README, architecture diagram, ≤5-minute demo video
  (problem → who it's for → why it matters → working demo), AWS Builder ID.
- A live demo link and AgentCore deployment both strengthen the Technical Implementation
  score.

### Known infrastructure limits

- **SMS is sandboxed.** `TEXT_MESSAGE_MONTHLY_SPEND_LIMIT` has `MaxLimit: 1` (~$1/month) and
  there are **zero origination numbers**. `the maintainer's verified test number` is a VERIFIED destination, but
  Egypt sender-ID registration requires a letter of authorization, company registration, and
  a tax card — not achievable in this timeline. Therefore the family channel sits behind a
  `Channel` interface with a transcript implementation as the always-works path. **The demo
  must never depend on SMS delivery.**
- Use the `grace-dev` IAM user (`GraceDevPolicy`) rather than root. No long-lived access
  keys have been created; the deployed runtime uses a scoped role.

---

## Style

Match the surrounding code. Comments explain *why*, not *what* — and in this codebase the
"why" is usually a safety property, so keep those comments. Conventional commits
(`feat:`, `test:`, `fix:`, `docs:`). Commit after each completed task.

When you finish a task from the plan, tick its checkboxes and run the full suite before
moving on.
