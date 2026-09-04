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
- Plan 2 (AgentCore deploy): `docs/superpowers/plans/2026-09-03-grace-agentcore.md` — **complete**
- Plan 3 (dashboard): `docs/superpowers/plans/2026-09-03-grace-dashboard.md` — **in progress**,
  spec `docs/superpowers/specs/2026-09-03-grace-dashboard-design.md`,
  runbook `docs/dashboard-runbook.md`

---

## Current state

**Plans 1 and 2 are both complete.** Plan 1's 9 tasks and Plan 2's 11 tasks are done. **622 unit tests
pass** (`.venv/bin/python -m pytest`), and 23 trajectory evals pass against real Bedrock
(`.venv/bin/python -m pytest evals/` — `testpaths = ["tests"]` excludes `evals/` from the fast suite).
`grace sweep` runs end to end locally and reports **9 acted / 3 escalated**.

**The evals needed two runs to reach 23/23, and that is expected for exactly one of them.**
`test_an_escalating_case_does_something_rather_than_nothing[c-011]` is liveness, not safety — the gate
only permits or refuses a tool call, never forces one, so a model that deliberates inside the swarm and
answers from `decide` in prose fails it while every safety property holds. Re-running is the correct
response to *that* failure and the wrong response to any safety failure. **Never re-run
`test_an_escalating_case_is_never_filed` to get a pass; a failure there is a real regression.**

**Grace is deployed and the full sweep runs on AWS.** EventBridge → Step Functions (Map,
`maxConcurrency` 3) → Lambda → Runtime `grace_grace-oTyyvo8stE`. Two consecutive executions
**SUCCEEDED in ~61s reporting 9 acted / 3 escalated**, verified from DynamoDB rather than from a log
line: `renewal_submitted` appears for exactly `c-001`–`c-009` and for none of `c-010`/`c-011`/`c-012`,
and the `escalation-queue` GSI holds exactly those three households with their typed gate reasons.
Hard rule 6 holds on deployed infrastructure. `docs/deployed-verification.md` is the pasted evidence.

**Scope is four AgentCore surfaces, not five** — Runtime, Memory, Identity, and the deploy harness.
Identity was un-deferred by Plan 3 Task 4, which ships the Cognito pool whose ID token is the trust
anchor for every dashboard read and the one write. **Gateway stays deferred** with its written reason
(spec §8, README). **Never describe Grace as using five.**

**And state the Identity claim narrowly, because two different things share the name.** What shipped is
a **Cognito user pool** (`grace-caseworkers` / `us-east-1_HXs3b0APR`) whose ID token `verifySession`
validates against the pool's published JWKS, issuer, audience, expiry, `token_use: "id"`, and
`custom:role === "caseworker"`. What did **not** ship is an **AgentCore Gateway JWT authorizer**
(`customJWTAuthorizer` with inbound claim rules) — the runtime is still IAM-authorised. Both statements
are honest; merging them is not. Appendix D's protections (an explicit `Deny` on
`GetWorkloadAccessTokenForUserId`, an opaque `sub` that is never a name or an email) remain enforced in
the runtime role either way.

**Two deployed facts that must not be overclaimed:**

1. Every ledger row carries a `trace_id` **key**, but its value is `NULL` in the deployed runtime, and
   **zero traces exist in the account**. Runtime injects the OTEL env vars without installing an
   in-process tracer provider. So a Transaction Search query on `grace.gate_decision` returns nothing,
   and the DynamoDB escalation queue is the demo's evidence instead. See "Runtime instruments itself is
   wrong" below.
2. A household name **did** reach CloudWatch before it was fixed (see the PII finding below). The fix
   is now deployed — runtime **version 2**, and a fresh invocation of `c-012` (the case that leaked)
   returns an escalation with no name in the payload, confirmed on a full 9/3 sweep with zero PII in
   the output. Fixing the source did not clean what was already written, so that was scanned
   separately: three fields across two pre-fix `c-012` rows in DynamoDB held the surname and were
   stripped in place on 2026-09-04 (no key, `status`, `deadline`, or `renewal_submitted` row touched;
   a 633-row scan now returns clean — `docs/plan3-live-data-findings.md`). **Pre-fix log events still
   contain the name** and cannot be unwritten; they age out with retention. So: fixed in the repo,
   fixed in the running system, fixed in durable storage, historical log events still carry it.

- Plan 2 spec: `docs/superpowers/specs/2026-09-03-grace-agentcore-design.md`
- Plan 2 tasks: `docs/superpowers/plans/2026-09-03-grace-agentcore.md`
- Deploy runbook (verified CLI flags): `docs/runbook-deploy.md`
- Deployed evidence: `docs/deployed-verification.md`

Plan 2 task state:


| Task | State |
|---|---|
| 0 — preflight | **done** — Podman not Docker, CLI 0.28.1, CDK already bootstrapped, Transaction Search already ACTIVE |
| 1 — naming + `grace-cases` table | **done** — `infra/{naming,provision_dynamodb}.py`, 367 tests. **Two real defects in the plan's draft; read "What Plan 2 established" below** |
| 2 — `DynamoDBCaseStore` + store factory | **done** — `grace/cases/dynamo_store.py`, `grace/store_factory.py`, 428 tests. **Six defects in the plan's draft, three of them vacuous tests** |
| 3 — observability | **done** — `grace/observability.py`, 438 tests at the time. **Found a hard-rule-8 hole: "token present" is not "content redacted"** |
| 4 — Runtime entrypoint | **done** — `grace/entrypoint.py`, `grace/run.py` (rename only). The deployed path invokes the graph **once** and never resumes |
| 5 — AgentCore Memory | **done** — `grace/memory.py`, `infra/provision_memory.py`, 544 tests. Memory `grace_household_memory-TCf1SS708O` ACTIVE, 365-day expiry |
| 6 — IAM roles | **done** (out of order — no code deps) — `infra/provision_iam.py`, 489 tests. `explicitDeny` on the unverified token path verified live. **The runtime role would have denied every Nova call — see below** |
| 7 — deploy to Runtime | **done** — `Dockerfile`, `runtime_app.py`, `agentcore/`, 556 tests. **Runtime `grace_grace-oTyyvo8stE` is READY and serving**; `c-010` escalated in 9.1s with real DynamoDB rows |
| 8 — Lambda/Step Functions/EventBridge | **done** — `infra/{lambda_src,provision_lambda,provision_stepfunctions,provision_eventbridge}.py`, 578 tests. **The deployed sweep reports 9 acted / 3 escalated** |
| 9 — escalation alarm + provisioning | **done** — `infra/{provision_alarm,provision_all,teardown}.py`, 621 tests. **The alarm went OK on a real sweep's Sum=3.0** |
| 10 — deployed verification + README | **done** — `docs/deployed-verification.md`, honest README scope. **A PII scan of the deployed log groups found a household name in CloudWatch — read the PII finding below before touching `read_case`** |

Plan 1 task state, for reference:

| Task | State |
|---|---|
| 1 — rule packs + deadline math | **done** — `grace/rules/{pack,clock}.py`, 48 tests |
| 2 — case types, store, 12 fixtures | **done** — `grace/cases/{models,store}.py`, `fixtures/households.yaml`, 60 tests |
| 3 — the authority gate | **done** — `grace/authority.py`, 121 tests total. The task that matters most. |
| 4 — Nova model registry + tools | **done** — `grace/models.py`, `grace/tools/{read,action}.py`, 157 tests total |
| 5 — `AuthorityGate` + `LedgerHook` | **done** — `grace/{steering,ledger,vendored_actions}.py`, 212 tests total. Capability absence is now real enforcement, not shape. |
| 6 — Graph spine + `grace sweep` CLI | **done** — `grace/{graph,run}.py`, 285 tests total. The first runnable end-to-end path. **Read "What Task 6 established" below before touching the sweep or the swarm** |
| 7 — deliberation swarm | **done** — `grace/swarm.py`, 351 tests total. **Read "What Task 7 established" — a swarm ends when a node does not hand off, two separate defects made it collapse to one model silently, and two more (a timeout margin, a verdict-extraction order dependency) were found in review of the first two fixes** |
| 8 — trajectory evals | **done** — `evals/{test_gate_trajectory.py,README.md}`, 23 evals against real Bedrock. **Read "What Task 8 established" — `strands-agents-evals` is never installed, and the headline test was vacuous on the two most important cases until review caught it** |
| 9 — ledger/trace correlation | **done** — `grace/ledger.py`, `tests/test_ledger_trace_correlation.py`, 360 tests total. **Read "What Task 9 established" — there are two ledger writers, not one, and this is the one place fail-closed is the wrong instinct** |

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

### What Task 8 established — follow these

**`strands-agents-evals` is never installed, and this decision predates Task 8.** It depends
on `strands-agents-tools` — the same package Task 1's dependency rule already forbids, 25
packages including `slack-bolt` and `pillow`. Trajectory evals are ordinary pytest functions in
`evals/test_gate_trajectory.py`, run explicitly via `.venv/bin/python -m pytest evals/`.
`pyproject.toml`'s `testpaths = ["tests"]` already excludes `evals/` from a bare
`.venv/bin/python -m pytest`, so this costs nothing in the fast suite. **Never add
`strands-agents-evals` to any dependency list, and never import `strands_evals`.**

**A parametrized test that never reaches its own assertion body still passes.** An early
version of the suite's headline ordering test ran on all three escalating fixtures — but
`c-011`/`c-012` never execute a gated action at all (escalating is the point), so the loop
that does the checking never ran for them, and the test passed having asserted nothing while
still paying for ~37 of the suite's ~65 Bedrock invocations. **When a test's `for`/`if`
structure can be skipped entirely for a given input, add an explicit assertion that it wasn't**
(`assert ran_something`) — a parametrized case that silently checks nothing is worse than one
that fails, because it looks identical to a passing check in every report.

**A per-invocation cache must cache failures too, or a flaky run costs 4× and can silently mix
results from different invocations.** `_RUNS[case_id] = _Run(case_id)` never completes the
assignment if construction raises — confirmed live when `decide` hit `set_node_timeout(420.0)`
on Bedrock latency (512.92s against a typical ~75s). Every other test touching the same
`case_id` then retried the full graph invocation from scratch, and different tests for the
same nominal case could end up asserting against genuinely different runs. Cache the exception
and re-raise it on every subsequent lookup for that key.

**A raising operation should still leave partial, real evidence readable.** The evals'
`_Run.__init__` used to set `self.ledger` only after `graph(...)` returned — so a timeout
mid-run discarded a ledger that `LedgerHook` had already partially written and that `store`
(constructed before the `graph()` call) still held. A safety claim that could have been
checked from that partial data instead read as an unrelated infrastructure failure. Read
whatever state is available in a `finally`, regardless of whether the operation that populates
it raised.

**`decide`'s ledger cannot, by itself, distinguish "the swarm deliberated and `decide` trusted
it" from "`decide` escalated blind."** Only `decide` is built with `hooks=[ledger]` (see Task
6/7) — the swarm's own reads on `c-011`/`c-012` never reach the case ledger, so both scenarios
produce the identical shape on `decide`'s rows. `SwarmResult.node_history` closes this at zero
extra cost: it is already returned inside the `GraphResult` a graph invocation produces, and
asserting it names all three roles (not just "non-empty") is a genuine regression guard against
the exact collapse `grace/swarm.py`'s own docstring documents from a real run. **Before
concluding a ledger-based test can't observe something about a nested multi-agent node, check
whether the in-process result object already carries it — a hook is not the only way to see
what happened.**

**A test's classification as "safety" vs. "liveness" must match what could make it fail, not
what it is trying to catch.** A test asserting an escalating case does *something* (outreach,
escalation, or a refused attempt) sounds like a safety property, but the gate never *forces*
a tool call — a model that reads everything and answers only in prose passes the gate's own
checks while failing this test. Label by what can make an assertion fail on a correctly-behaving
system, not by how important the property feels.

### What Task 9 established — follow these

**There are two ledger writers, not one, and the plan only wired the trace ID into one of
them.** `LedgerHook._append` (`grace/ledger.py`) writes `tool_call`/`tool_result`; but
`make_action_tools`'s own `_log` (`grace/tools/action.py`) independently writes
`renewal_submitted`, `family_message_sent`, and `escalated` — the rows that record what Grace
actually *did* rather than which tools it invoked, and the ones `sweep` classifies a case from
(it looks for `renewal_submitted`, per Task 6). Wiring only the hook left exactly those rows
with no `trace_id`, unjoinable to their CloudWatch trace, while every test that inspected hook
rows still passed. Both now share `_current_trace_id`. `test_every_ledger_writer_in_grace_records_a_trace_id`
walks `grace/` with `pkgutil` and fails on any *new* module that calls `append_ledger` without
it — the same discovery-from-disk discipline Task 4's model-ID guard established, for the same
reason: a hardcoded list of writers someone remembered is how this was missed the first time.
**Before adding a field to "every ledger entry", grep for `append_ledger` and count the call
sites.**

**`_current_trace_id` is the one place in this codebase where fail-closed is the wrong
instinct — and a `HookProvider`'s exception is *not* swallowed the way a `SteeringHandler`'s
is.** Verified directly against the SDK: `HookRegistry.invoke_callbacks` re-raises anything
that is not an `InterruptException` (its own docstring says so), and `ToolExecutor._stream`
catches it and substitutes a `status: "error"` tool result. Confirmed empirically with a
raising hook on a real tool call. So an exception escaping `_current_trace_id` would convert a
tool that had *already passed the gate* into a failed call — `submit_renewal` reporting an
error on a clean case, and a renewal that never gets filed. That is the inverse of Task 5's
finding about `steer_before_tool`, where an exception is swallowed and the tool runs *ungated*.
**The distinction is what the code is deciding, not which class it lives in:** failing closed
on a *verification* question protects the family; failing closed on an *observability* question
harms them, because nothing relies on the trace ID to decide anything. `get_span_context()` is
a method on an arbitrary `Span` implementation and a misconfigured or partially shut-down
provider can raise from it, so the `try` is real, not boilerplate. Lose the trace ID; keep the
ledger row.

**Never assert a fixed tool-call count against a real model run.** Measured across three real
`c-001` invocations, `submit_renewal`'s `call_count` was `1`, `2`, `2` — the `2` when the gate
`Guide`s a first attempt and the model retries, both correct behaviour. The plan's own draft
cited `submit_renewal: 2` as the observed value, and a test asserting that number would have
failed one run in three with nothing wrong. The correlation test compares `decide`'s
`tool_metrics` **against its own ledger rows** as two `Counter`s, which is a property of the
wiring rather than of the model's choices, plus an explicit `assert from_ledger` so an empty-vs-
empty comparison cannot pass vacuously (the Task 8 lesson).

**`is_valid` bounds-checks the trace ID, so `format(..., "032x")` cannot overflow 32
characters.** `SpanContext.is_valid` is precomputed at construction and already rejects a
trace ID outside the 128-bit range — verified: `2**128` gives `is_valid == False`, `2**128 - 1`
formats to exactly 32 hex chars. No separate length guard is needed.

### What Plan 2 established — follow these

**The container engine is Podman, not Docker.** Docker's binary is installed but its daemon is
not running and is not used. `podman machine start`, then
`export DOCKER_HOST="$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}'
podman-machine-default)"` — that export is mandatory for any Docker-API client, including the
`agentcore` CLI, because `podman-mac-helper` is not installed so `/var/run/docker.sock` does not
exist. The VM is native `linux/arm64`, which is what Runtime requires, so `--platform linux/arm64`
is a no-op rather than an emulated cross-build. Note the socket path lives under `$TMPDIR` and
changes if the machine is recreated — read it, never hardcode it.

**The `agentcore` CLI is 0.28.1 and the appendices were written against 0.24.2. Four command
shapes differ, all verified against `--help`:** `deploy` deploys **via CDK** (already bootstrapped
in this account, `CDKToolkit` from Feb 2026); `create` takes `--name`/`--project-name` rather than
a positional and **scaffolds a new project** rather than registering existing code; `add` is
subcommand-based (`agent`, `harness`, `memory`, `gateway`, …); and Grace needs
`add agent --type byo --code-location . --entrypoint runtime_app.py` — `--type create` would
generate a fresh agent and ignore `grace/` entirely. `docs/runbook-deploy.md` is authoritative over
the appendices wherever they disagree.

**The Runtime entrypoint is `BedrockAgentCoreApp`, not a bare handler.** Verified by scaffolding a
template agent and reading it: `app = BedrockAgentCoreApp()`, `@app.entrypoint` on
`invoke(payload, context)`, `app.run()` under `__main__`. It is a Starlette app on **port 8080**
(8000 MCP, 9000 A2A per the Runtime service contract). Its own docstring says payloads are passed
through **unchanged**, so the app must validate the payload shape itself. Grace's entrypoint is a
plain `def` returning a dict; the template's is an async generator only because it is a chat agent.
**Two template defaults Grace rejects:** `aws-opentelemetry-distro` and
`CMD ["opentelemetry-instrument", ...]` — both forbidden here because Runtime instruments itself.

**A sort key built from `isoformat()` is only correctly ordered if the datetime is UTC.**
`LedgerEntry` requires an *aware* datetime and nothing more, so any offset reaches the key builder,
and DynamoDB compares a range key **bytewise**: `2026-10-01T08:00:00-05:00` is one hour *after*
`2026-10-01T12:00:00+00:00` in real time but sorts *before* it as a string. That silently inverts
`ScanIndexForward=True` — the ordering Task 8's evals read to assert reads precede actions — with no
error anywhere. `infra/naming.py`'s `_utc_stamp` normalizes first. **The naive-datetime `raise` must
stay ahead of the conversion:** a naive `.astimezone()` silently assumes the local clock, which
looks correct on a UTC host and is wrong where the code was written. It also refuses
`utcoffset() is None` — verified reachable, not defensive padding: a `tzinfo` subclass may return no
offset, `LedgerEntry` *accepts* such a value, and `isoformat()` then emits no offset at all.

**A provisioning script that swallows a "not ready yet" error reports success while the control is
absent.** `provision_dynamodb` originally caught `ContinuousBackupsUnavailableException` and moved
on; measured across three runs on throwaway tables, point-in-time recovery came out
`ENABLED, ENABLED, DISABLED` — one run in three left the ledger table unrecoverable while the script
exited 0. The fix is a bounded retry on that error code only **plus a read-back that is the sole
arbiter of success**: "the API call returned" and "the control is on" are different claims, and only
the second one matters. Non-transient errors re-raise immediately rather than burning the retry
budget. **Raising is correct here even though Grace's observability paths deliberately fail open** —
`infra/` is a provisioning script, not the request path, so a loud failure blocks a deploy and the
operator re-runs, which is exactly what idempotence exists for.

**Prove a new test fails against the code it was written to catch.** Both Task 1 defects were
invisible to the plan's own tests: the sort-key tests used only UTC inputs, so they passed against
the buggy implementation. The fix was confirmed by reverting `naming.py` in a throwaway copy and
watching the two new tests fail there while the five original ones passed either way. This is Task
8's vacuity lesson applied forward — a test that cannot fail is indistinguishable from a passing one
in every report.

**Task 8's state machine definition is pre-verified.** It was validated against the real
`stepfunctions:validate_state_machine_definition` API (`result: OK`, zero diagnostics) including the
`States.Format` intrinsics, `$$.State.EnteredTime`, and the `dynamodb:putItem` parameters nested
inside the Map's `ItemProcessor`. If it stops validating, re-run the validator rather than guessing
— it reports the exact path of the offending field.

**`namespaces` is a legacy parameter on a memory strategy; use `namespaceTemplates`.** The live
`CreateMemory` model documents it as *"a legacy parameter, use `namespaceTemplates`"*. Both exist
and both accept the same list, which is what makes it dangerous — writing the legacy field succeeds,
and a retrieval namespace that does not match what was set at creation **retrieves nothing,
silently**. Any agreement check must read both spellings and assert the result is non-empty, or it
passes vacuously when the service echoes the other field.

**Ledger rows prefix every `detail` key with `d_`, and that is structural, not cosmetic.** A row
carries its own `pk`/`sk`/`case_id`/`at`/`kind` columns, and `detail` is caller-supplied. Merging
`detail` in unprefixed lets `detail={"kind": ...}` overwrite the row's own `kind` — the field
`sweep` classifies a case from (`renewal_submitted`). Verified: the naive merge destroys it silently.
`ledger()` also filters on the `LEDGER#` sort-key prefix, or an escalation row surfaces as a ledger
entry and fails on a missing `at`.

**`ledger()` paginates, because a DynamoDB Query caps at 1MB and signals more via
`LastEvaluatedKey`.** Truncation drops the *newest* rows, which is exactly where
`renewal_submitted` lives, so a single-page read would report a filed renewal as unfiled with no
error. The test fake pages at 3 rows so the loop genuinely iterates — a pagination loop that never
iterates in any test is not tested.

**`os.getenv(name, default)` only defaults on *absence*, not on an empty value.** `GRACE_STORE=`
(set but blank) bypassed the in-memory default and would have had a deployed runtime write its
ledger to memory and discard it at process exit, with the dashboard showing an empty ledger and
nothing saying why. Both stores now raise on an unrecognized value, including blank — the same
allowlist polarity as `APPROVE_DECISIONS`.

**Non-finite floats never reach the ledger.** DynamoDB rejects Infinity and NaN, and
`Decimal("Infinity")` serializes cleanly then raises out of the *read* path later. The sharper
reason is the Task 1 finding above: a NaN disables every comparison it appears in, so it reads back
as a number and behaves like nothing. `math.isfinite()` guards it at the boundary. Real DynamoDB
additionally rejects >38 significant digits, which is what catches `Decimal(1.1)`'s 52-digit binary
noise if anyone reintroduces it.

**A test fake must be able to fail the way the real service fails.** `FakeTable`'s original float
check could never fire, because the serializer stringifies every number before it gets there. It now
enforces the three things the live table enforces (non-finite, >38 digits, exponent overflow) and
pages its queries. A fake that only ever succeeds is worse than no fake, because it makes the suite
look like it covers the boundary.

**Parametrizing over two implementations proves nothing unless you check both ran.**
`tests/test_dynamo_store.py` records the store types a run actually exercised and asserts the set.
Verified by sabotage: making the fixture return the in-memory store for both parameters leaves 41
tests passing and fails only that guard.

**A test that asserts by *raising* inside code that catches `Exception` asserts nothing.**
`AssertionError` is an `Exception`. The Task 3 draft test proved the Runtime guard existed by
monkeypatching `StrandsTelemetry` to raise — but `setup_telemetry` wraps construction in
`except Exception`, so the raise was swallowed and logged, and the test passed with the guard
deleted. Verified both ways. Record the attempt in a sentinel list and assert the list is empty
instead; no `except` can undo an append.

**`StrandsTelemetry.__init__` sets the global tracer provider unconditionally** —
`_initialize_tracer()` calls `trace_api.set_tracer_provider` (`telemetry/config.py:114`). So
*constructing* it is the damage; there is no later call to intercept. A second construction logs
"Overriding of current TracerProvider is not allowed", leaves the first provider global, and orphans
the second with a console exporter attached to nothing. `setup_telemetry` therefore latches on a
module-level flag — set even on failure, so a broken exporter is not retried on every invocation
(Task 4 calls it per case).

**`strands` invokes Bedrock through `converse`/`converse_stream`, not `InvokeModel`.**
`strands/models/bedrock.py:1397` chooses between them. A runtime policy granting only
`bedrock:InvokeModel` leaves every Nova call `implicitDeny` — verified with the IAM simulator against
the real role — and the symptom is an AccessDenied on the first model call of the first deployed
sweep, long after every test has passed. Grant all four actions, still scoped to the three Nova
profiles.

**A Container-build runtime needs ECR pull, or it cannot start.** `ecr:GetAuthorizationToken` is
account-level and must be `Resource: "*"`; the layer-pull actions can be scoped to the account's
registry. Runtime also creates its own log group under `/aws/bedrock-agentcore/`, so the role needs
`CreateLogGroup` on that path. **`theagentorg-shared-agentcore-runtime-role` in this account is a
known-working reference** for what Runtime actually requires — check it before inventing
permissions, and add only what is missing.

**IAM policy shape is checkable without deploying: use `simulate-principal-policy`.** It distinguishes
`explicitDeny` from `implicitDeny`, which is exactly the difference between "this action is blocked
forever" and "this action is simply not granted yet". Grace's runtime role returns `explicitDeny` for
`GetWorkloadAccessTokenForUserId` and `implicitDeny` for the safe JWT path — so Appendix D.1 is
enforced and AgentCore Identity can still be added later without fighting the statement. The
simulator reflects the identity policy only: it says nothing about resource policies, SCPs, or
whether a real `Converse` call succeeds.

**`aws:SourceAccount` in a Lambda trust policy is undocumented, and a condition on an unpopulated key
would make a role permanently unassumable.** Probed with a throwaway role rather than assumed: a
wrong account value was refused with "The role defined for the function cannot be assumed by
Lambda", and the identical call succeeded once corrected — so Lambda does populate the key and the
condition is genuinely evaluated. Both halves of that probe mattered; success alone would not
distinguish "satisfied" from "ignored".

**Renaming a function whose name is also a local variable inside a caller breaks the caller.**
`sweep` binds `gate_reason = _gate_reason(...)`. Rewriting that call site to the promoted public name
gives `gate_reason = gate_reason(...)`, and Python then treats the name as local for the whole
function — `UnboundLocalError` on every case with a non-clean verdict, i.e. all three demo
escalations, from a change intended as a no-op. The aliases (`_gate_reason = gate_reason`) make every
existing call site correct, so **promote the definition and leave the call sites alone.**

**The deployed entrypoint invokes the graph exactly once and carries no resume vocabulary.** Two
assertions, because one is not enough: a call count on a counting fake, *and* an AST check that
`interruptResponse` / `APPROVE_DECISIONS` / `MAX_RESUME_ROUNDS` appear nowhere in the module. A
call-count assertion is only as good as the fake it counts. Verified by sabotage — adding a bounded
resume loop fails four tests, and an *unbounded* one hangs the suite, reproducing Task 6's finding 3
live.

**A failed escalation-row write is recorded in the reason, not swallowed and not raised.** Swallowing
keeps the outcome payload but makes the *missing* durable row invisible — Plan 3's dashboard reads
the escalation GSI, so it would show two escalations while the payload claimed three. Raising is
worse: a returned `{"status": "error"}` does **not** trip Step Functions' `Catch`, so no replacement
row gets written by anyone. Appending the failure to the reason keeps the family escalating and makes
the gap explicit.

**`PersistenceMode` is `FULL`/`NONE` only, so "write to memory selectively" (spec §3.7) is not
expressible as configuration.** It is handled by *scope*: `build_session_manager` is a factory the
caller attaches to the orchestrator, and it is never wired into `build_case_graph` — so the nodes
carrying raw tool output and model errors have nothing to persist through. Pass `FULL` explicitly
rather than by default, because `NONE` leaves retrieval working while writing nothing, which is
indistinguishable from `FULL` in any test that only reads.

**A `Swarm` carrying its own `session_manager` is accepted as a graph node — an SDK gap, not a Grace
bug.** `Graph._validate_node_executor` guards only `isinstance(executor, Agent)`;
`Swarm._validate_swarm` guards only each *member's* `_session_manager`. Neither covers a Swarm
holding its own, and a Swarm inside a Graph is exactly Grace's topology. Grace never attaches one
there, so this is asserted structurally in `tests/test_memory.py` rather than defended against in
`graph.py`. A second test pins the gap so it fails (and can be deleted) if a future SDK closes it.

**Memory strategy names reject hyphens** (`[a-zA-Z][a-zA-Z0-9_]{0,47}`) — `household-facts` was
refused live, which matters because every other Grace resource is `grace-something`. Also verified:
`ListMemories` paginates, `CreateMemory` returns `CREATING` (use the `memory_created` waiter — real
creation took 2m31s), `ValidationException` must not be treated as "already exists" (it would report
success while silently dropping an invalid strategy), and an id prefix match needs anchoring because
this account holds `name` and `name_v2` pairs.


**"Runtime instruments itself" is wrong in a specific and consequential way — verified on the
deployed runtime.** Runtime injects the OTEL environment variables and creates a log group, but it
does **not** install an in-process tracer provider. So `setup_telemetry()` correctly skips (it gates
on `AGENT_OBSERVABILITY_ENABLED`, which Runtime sets), nothing else fills the gap,
`_current_trace_id()` returns `None`, and every deployed ledger row carries `trace_id: NULL`.
Measured after a successful deployed invocation: **zero traces in the account**, no `aws/spans` log
group, and no other deployed project in this account exports spans either — so this is not something
Grace broke. Transaction Search is ACTIVE, so the destination exists; nothing is producing spans to
send to it.

**Do not "fix" this by adding `aws-opentelemetry-distro`, switching the CMD to
`opentelemetry-instrument`, or removing the `AGENT_OBSERVABILITY_ENABLED` guard.** Task 9 of Plan 1
established that losing the trace ID must never cost a ledger row, and the deployed rows are all
present with correct sequence numbers and UTC-normalized sort keys. `trace_id: NULL` is *honest* —
tracing genuinely was not configured for that run. The consequence to be honest about instead: a
Transaction Search query on `grace.gate_decision` returns nothing until spans exist, so that claim
cannot be made in the README as written.

**`DOCKER_CONTAINER=1` is required in the Dockerfile.** `BedrockAgentCoreApp.run()` binds `0.0.0.0`
only if `/.dockerenv` exists *or* `DOCKER_CONTAINER` is set, else `127.0.0.1`. Podman creates no
`/.dockerenv`, so without it the server binds loopback inside the container, `/ping` returns nothing,
and the container still reports healthy. The CLI's own template sets this variable — treat it as the
explicit portable signal and `/.dockerenv` as a Docker-only fallback.


**`invoke_agent_runtime` is not idempotent, and boto3's default client retries it five times.**
Measured with a black-hole socket (accepts, never replies, so the accept count *is* the number of HTTP
attempts): default config **5**, `{"mode": "standard", "max_attempts": 1}` still **2**, and only
`{"total_max_attempts": 1}` gives **1**. `ReadTimeoutError` maps to `GENERAL_CONNECTION_ERROR`, so a
slow runtime looks like a dropped connection — and each retry re-runs the whole graph against the same
case, which could file one renewal five times. Reachable, not theoretical: the default `read_timeout`
is 60s and Plan 1 measured a real run at 512s. `infra/lambda_src/handler.py` sets
`total_max_attempts=1` and `read_timeout=870`, so the **Lambda's** 900s deadline binds first — that
ordering matters, because a Lambda timeout trips Step Functions' `Catch` while a returned error does
not.

**Step Functions' `Catch` cannot see a *returned* `{"status": "error"}`.** Grace's entrypoint and
Lambda handler never raise, so a politely-reported failure looked to Step Functions like a successful
task. The two failure paths were exactly inverted: a killed Lambda got an escalation row, a handled
failure got none — the family who disappeared was the one whose failure was handled *better*. Fixed
with a `CheckOutcome` Choice state routing `status == "error"` to `RecordEscalation`. **When a
component reports failure in its payload rather than by raising, the orchestrator needs an explicit
branch — error handling that only catches exceptions catches the wrong half.**

**A CloudWatch metric filter on Step Functions logs needs `$.type` anchoring and cannot read a
top-level field that does not exist.** Measured against a real sweep's events:
`{ $.status = "escalated" }` matches **0** (there is no top-level `status`; the outcome payload is an
embedded JSON *string* at `$.details.output`), the unanchored
`{ $.details.output = "*escalated*" }` matches **14** (the same outcome counted once per event type
the case passes through), and only
`{ $.type = "TaskStateExited" && $.details.output = "*\"status\":\"escalated\"*" }` matches **3**.
Against a `Threshold: 3` the unanchored version would keep the alarm permanently quiet — the exact
failure an escalation-count alarm exists to avoid. Check a pattern with `logs:test_metric_filter`
(caps at 50 messages per call, so batch); **`filter_log_events` returns 0 for every pattern, including
the empty one, for minutes after a run**, so it cannot be used to validate one promptly.


**A household name reached CloudWatch, and hard rule 8's redaction does not cover the path it took.**
Found by scanning real log events across a 24-hour window: `"Mensah"` appeared **16 times in 302
events** in `/aws/vendedlogs/states/grace-sweep-Logs`, and **zero times in 360 events** in the
runtime's own log group `/aws/bedrock-agentcore/runtimes/grace_grace-oTyyvo8stE-DEFAULT` — so the
carrier is the Step Functions payload, not the agent's stdout. Zero `+1555` hits anywhere. No Grace
code logged it. The chain was
`read_case` handing `display_name` to every model → the **referee quoting it in its deliberation
prose** → `_deliberation_note` appending that conclusion to the escalation reason → the reason being
the Lambda's return payload → Step Functions logging the payload. Span redaction protects `gen_ai.*`
span content; a Step Functions execution payload is not span content.

**The fix is capability absence, not filtering:** `read_case` no longer returns `display_name` at
all. Nothing needed it — `authority.py` never reads it, the action tools never use it, and the
outreach SMS does not address the family by name. Removing it at the source closes every downstream
path at once (model prose, escalation reasons, Step Functions logs, the ledger, future spans) rather
than scrubbing each consumer, which is the same reasoning as layer 1 of the escalation boundary.
**Never put a household's name, phone, or address into a tool's returned text** — a model will
eventually quote it somewhere you are not redacting, and hard rule 9's "never in a span attribute" is
necessary but not sufficient.

**Two consequences to keep straight.** First, the fix is in the repository but **not in the deployed
image**: runtime `grace_grace-oTyyvo8stE` is version 1, built `2026-09-03T03:04:55Z`, from code where
`read_case` still leaked. Pre-fix log events cannot be unwritten and remain until retention expires,
so "no household identity reaches CloudWatch" is true of the repo and needs a redeploy to be true of
the running system. Second, **two existing tests used the surname as their marker** for "the bound
case was read" (`test_read_case_returns_the_bound_case_only` and, more importantly,
`test_an_injected_identity_argument_cannot_redirect_a_read` — the load-bearing layer-2 test). Both now
discriminate on `case_id`, which tests the identical property with an opaque value. If you add a test
that needs to prove *which* case was read, use `case_id`; never reintroduce a name as a fixture marker.

### What Plan 3 established — follow these

Plan 3 adds a Next.js caseworker dashboard in `web/`, deployed to Amplify with Cognito auth. It also
un-defers **AgentCore Identity**, which Task 4 has now shipped — so the honest surface count is
**four**, and the "Scope is four AgentCore surfaces" paragraph above has been updated to say so. The
README still needs the same correction when Task 8 revisits it.

Task state:

| Task | State |
|---|---|
| 0 — preflight | **done** — eight of the plan's dependency pins were wrong; corrected from real registry data |
| 1 — scaffold `web/` | **done** — commit `e3ea347`, four gates green (`typecheck`, `lint`, `test`, `build`), Python suite unchanged at 622. **Two more version pins and three config defects; read below** |
| 2 — `lib/authorize.ts` | **done** — `web/lib/authorize.ts`, 19 tests (21 with Task 1's smoke), four gates green, Python unchanged at 622. **Four defects in the plan's draft, three of them vacuous or dead tests; read below** |
| 3 — `lib/cases.ts` | **done** — `web/lib/{env,cases}.ts`, **61 vitest tests** (39 cases + 20 authorize + 2 smoke), four gates green, Python unchanged at 622. **Ten defects in the plan's draft, one of which reported an unfiled case as `acted`; three more found in independent verification. Read below** |
| 4 — Cognito + session verification | **done** — `infra/provision_cognito.py`, `web/lib/cognito.ts`, `web/proxy.ts`, login page + callback route. **9 Python tests (631 total), 78 vitest**, five gates green. Pool `us-east-1_HXs3b0APR` live, one seeded caseworker, `custom:role` verified on the user. **Nine defects in the plan's draft, two of which broke a gate and two of which were inverted security claims; read below** |
| 5 — `lib/decide.ts` + the write route | **done** — `web/lib/decide.ts`, `web/app/api/case/[id]/decide/route.ts`, `grace/entrypoint.py` (flag only), 16 Python tests (**647 total**), **122 vitest**, five gates green. **The draft decoded a streaming response as bytes, which would have reported every approve as a failure after it succeeded; read below** |
| 6 — pages | **done** — `web/app/{page,queue,case/[id]}/`, `web/components/`, `web/lib/session.ts`, **154 vitest**, Python unchanged at 648, five gates green. **The pages had no session verification at all — a forged cookie returned 200 with every case id. Read below** |
| 7 — Amplify | not started — **the draft sets no SSR compute role, so every read fails after a green deploy; read below** |
| 8 — verification + docs | not started |

**`web/` runs its own toolchain, and `npm run build` is the gate that matters** — it is what Amplify
runs, so a green `typecheck` and `test` with a failing `build` is not a deployable app. Four commands,
all from `web/`: `npm run typecheck`, `npm run lint`, `npm run test`, `npm run build`. The Python suite
is separate and must stay at its current count; `web/` is additive and may never change it.

**TypeScript is pinned to 6.0.3, not 7.x, and this is not a stale pin.** TypeScript 7 is the native Go
port and `typescript-eslint` **refuses to load under it** — an explicit
`throw new Error("typescript-eslint does not support TS 7.0.")` guarded by `if (versionMajor >= 7)` in
its own `dist/index.js`. `eslint-config-next@16.3.4` depends on `typescript-eslint@^8.46.0`, so
pinning TS 7 means `npm run lint` cannot run at all. `tsc --noEmit` and `next build` pass identically
under 6.0.3, so nothing is lost. Upstream tracking issue: `typescript-eslint#10940`. **Do not bump
`typescript` past 6 until that closes.**

**`@aws-sdk/util-dynamodb` is on 3.996.9 while its sibling clients are on 3.1125.0, and that is
correct.** That package never entered the `3.1xxx` series — `latest` is 3.996.9 across 568 published
versions. Its peer range is `@aws-sdk/client-dynamodb: ^3.1111.0`, which 3.1125.0 satisfies, so the
pair is compatible. A "consistency" bump to 3.1125.0 installs a version that does not exist.

**`eslint-config-next` exports an array, not a factory.** `import next from "eslint-config-next"` then
`[...next()]` throws `next is not a function`; its own `dist/index.d.ts` reads
`declare const config: Linter.Config[]; export = config`. Spread the value: `[...next, ...]`.

**Every `.mjs` config must assign to a named `const` before exporting.** `eslint-config-next` enables
`import/no-anonymous-default-export`, so a bare `export default { ... }` in `postcss.config.mjs` or
`export default [ ... ]` in `eslint.config.mjs` makes the config lint *itself*. Clean lint output, not
merely a zero exit code, is the bar.

**`vitest.config.mts` uses `import.meta.dirname`, never `__dirname`.** Vitest 5 warns on every run
that `__dirname` is unsupported by `configLoader: "native"`, which is planned to become Vite's
default.

**`next build` rewrites `tsconfig.json` in place** — it forces `jsx` from `"preserve"` to
`"react-jsx"` (Next 16 uses the React automatic runtime) and appends `.next/dev/types/**/*.ts` to
`include`. Next's version is the committed one; reverting it just makes the next build rewrite it.
`*.tsbuildinfo` is gitignored because `incremental: true` writes an absolute-path-keyed cache on every
typecheck.

**The static-export guard is real, and was watched failing.** `__tests__/smoke.test.ts` asserts
`next.config.ts` does not set `output`; adding `output: "export"` fails it with
`expected 'export' to be undefined`. A static export has **no route handlers and no middleware**, so
it would silently delete the Cognito gate and the decide endpoint — the app would build, serve, and be
wrong. **Never add `output: "export"` to `web/next.config.ts`.**

**`web/` carries no resume vocabulary and no `NEXT_PUBLIC_` variables.** `interruptResponse`,
`APPROVE_DECISIONS`, and `MAX_RESUME_ROUNDS` appear nowhere — Plan 3's approve/deny path records the
decision and **re-invokes so the gate re-evaluates**, it never resumes a paused graph (any truthy
resume response approves the blocked tool; see Task 6 of Plan 1). No `NEXT_PUBLIC_` variable exists
either, because anything so prefixed is inlined into the client bundle, and every value this app holds
is either a credential or a household-scoped identifier.

**A TypeScript type is a promise a caller makes, not a fact `authorize` may assume — and an `as`
cast is where the promise stops being checked.** Task 5's decide route builds its `DecisionAttempt`
from `await request.json()` with a bare `body.decision as "approve"`, so both fields arrive as
whatever a client posted, and `DecisionAttempt.note: string` is erased at compile time. Two
consequences, both measured and both now guarded in `web/lib/authorize.ts`: a **non-string `note`**
passes the length cap silently (`undefined > 2000` is `false`) or throws `TypeError` if it is `null`,
turning a refusal into a 500 out of the pure gate; and a **non-finite `expiresAt`** defeats the
expiry check entirely, because `Infinity <= nowMs` is `false`. The second is reachable, not
theoretical: `JSON.parse('{"exp":1e400}').exp` is `Infinity`, `typeof` it is `"number"`, and `jose`
**verifies such a token** — confirmed against a real RS256 key pair. So `cognito.ts` must use
`Number.isFinite(payload.exp)`, not `typeof payload.exp !== "number"`. Refuse the type; never
coerce it, because coercion invents a note nobody wrote.

**A discriminated union invites the `if` that deletes the assertion.** Plan 3's Task 2 draft wrote
every refusal-code check as `if (!r.permitted) expect(r.code).toBe(...)`, and one whole test body as
`if (r.permitted) { ... }`. Measured by rewriting `authorize` to always refuse: **10 of 14 tests
failed and 4 passed**, including `carries the opaque sub, never a name`, whose body never ran.
Narrow by **throwing** on the wrong variant (`refusalOf`/`permitOf`) so every assertion is
unconditional — the Task 8 vacuity lesson, in the shape TypeScript encourages.

**A purity guard that greps for literal spellings is a denylist, and leaked three ways.** The draft
forbade `node:fs`, `Date.now()`, `fetch(`, `@aws-sdk`, and `process.env`; `from "fs"`,
`new Date().getTime()`, and `globalThis.fetch` were each added to `authorize.ts` and **passed**.
The guard now enumerates the `import` statements actually present and requires each to be type-only
and relative, which is a positive check — the same discovery-from-disk discipline as Task 4's
model-ID `pkgutil` walk, for the same reason.

**Session checks must precede fact checks.** Otherwise the difference between `no_session` and
`unknown_case` tells an unauthenticated caller which case IDs exist. Pinned by a test that compares
the two refusal codes for real and null facts.

**An env var that swaps out a trust anchor must never be readable in production.** Task 4's draft read
`process.env.COGNITO_TEST_JWKS` from `verifySession`'s key resolver with no environment guard — so
setting that one variable on the deployed app replaces Cognito's published key set with an
attacker-supplied one, and every forged token then verifies with a *valid signature*. Nothing
downstream would notice, because against that key set the session genuinely is authentic. Gated on
`process.env.NODE_ENV === "test"` (vitest sets `NODE_ENV="test"` and `VITEST="true"` — measured), with
a test that flips `NODE_ENV` to `"production"`, re-imports the module, and asserts the *same* token
stops verifying. `vi.resetModules()` on both sides, because `cachedKeys` is module-level and a
resolver cached under one environment would answer for the other.

**Cognito's real ID token was minted and read, and `jose`'s refusals were executed.** A throwaway pool
with `ALLOW_ADMIN_USER_PASSWORD_AUTH` produced a genuine token: `custom:role` **is** present with
`ReadAttributes` set, `token_use` is exactly `"id"`, `aud` equals the client id, `sub` is an opaque
UUID, and there is no `email`. The **access token is a different shape** — `token_use: "access"`, no
`custom:role`, and **no `aud` at all** — so the `token_use` check guards a real distinction rather than
being defensive. Separately, `jose` 6.2.10 was verified to refuse a wrong key, an expired token via
`currentDate` (in both directions), a wrong issuer, a wrong audience, a non-JWT, **`alg: "none"`**, and
**HS256 algorithm confusion** signing with the public key's `n` as an HMAC secret — the last two with
`ERR_JOSE_ALG_NOT_ALLOWED`, through both `createLocalJWKSet` and a hand-rolled resolver, so the
`algorithms: ["RS256"]` allowlist binds before any key is fetched.

**Never escape a string before handing it to JSX — React already escapes text children, and doing it
twice is visible to the user.** Task 6's draft ran a caseworker's note through an HTML-escaping helper
and then rendered `{escapeNote(d.note)}`. Measured with `renderToStaticMarkup`: a note reading
`The family's wage record is stale.` renders as `The family&#39;s wage record is stale.` on screen,
while React alone turns `<img src=x onerror="alert(1)">` into `&lt;img ...` with no live tag. The
draft's own "leaves ordinary prose alone" test used a fixture with **no apostrophe**, so it passed
against the bug. Replaced with `noteIsInert`, a **check** rather than a transform — it answers "would
this note be safe if someone reached for `dangerouslySetInnerHTML`?", which is the only path markup
could take. Nothing rewrites the caseworker's words. (`authority.py`'s "whoever renders it escapes it"
still holds; here the renderer *is* React.)

**A hard-rule-9 name assertion must list every fixture surname, and must be proven able to fail.**
Task 6's draft matched `Mensah|Rivera|Okonkwo` — three of twelve — and the two households most likely
to carry a name in an escalation reason are `c-010` and `c-011`, **Fitzgerald** and **Yamamoto**,
neither of which was in the pattern. All twelve are listed now, plus `+1555` and `Household`, and a
companion test feeds a name in through `reason` (the exact path that reached CloudWatch) and asserts
the guard catches it — otherwise "no name in this row" is true of every input and proves nothing.

**Fixture data in a plan must match the live table, or the instruction and the code contradict each
other.** Plan 3's Task 3 carried a findings block saying to use the real deadlines and a fixture below
it still using invented ones, leaving the implementor to guess. The ordering test now uses the real
`c-010`/`c-011`/`c-012` deadlines **plus a third row**, so deadline order, escalation-time order, and
GSI order all differ on the same input — which is what makes the assertion distinguish "sorted by
deadline" from "whatever the GSI returned".

**"Not escalated" is not the same claim as "Grace filed the renewal" — hard rule 6 at the read
boundary.** Task 3's draft classified a case as `pending ? "escalated" : "acted"`, so a household with
no pending escalation and no `renewal_submitted` row was reported **acted**: silently counted among the
nine while nothing had been filed for it. The shipped reader requires the ledger row that proves a
filing (`pending ? "escalated" : filed ? "acted" : "error"`), which also makes `CaseStatus`'s `error`
variant reachable — `authorize` already refuses it as undecidable, so it was otherwise a shipped,
tested, unreachable guard. **`listQueue`'s `filed` is `false` by construction, not by measurement**: the
`escalation-queue` GSI projects escalation rows only, so that query cannot see a renewal row. Never
render `filed` from `listQueue`; `listCases` reads the ledger for all twelve cases instead.

**A fix that makes a dead branch reachable is not finished until you read what that branch says.**
Requiring evidence for `acted` (above) made `CaseStatus`'s `error` variant reachable for the first
time — and `authorize` was refusing `acted` and `error` with the same code and the same sentence,
"Grace handled this case itself; there is nothing to decide." Harmless while unreachable; false the
moment it was not, because `error` means nothing was filed **and** nothing escalated. That is the
unconfirmed-success claim hard rule 6 exists to forbid, aimed at the person who could still save the
family: a caseworker told Grace handled it moves on. Split into a `case_incomplete` code reading
"Grace's last run on this case reached no outcome. Re-run the sweep before deciding." Both still
refuse — only the wording changed, and the polarity was never wrong. **When a change widens the set of
inputs a branch can see, re-read that branch's message as well as its logic.**

**Three guards in `lib/cases.ts` survived the implementor's own 24-sabotage sweep, and two were
documented-but-untested.** Its header comment says `readEnv()` sits outside `readCase`'s `try` "on
purpose"; moving it inside passed all 58 tests, and the consequence is that a missing
`GRACE_TABLE_NAME` makes all twelve households read back `null` — `/` renders an empty caseload,
`/case/[id]` renders not-found, and nothing logs. Separately, `MAX_PAGES` was pinned only through
`listQueue`, which throws; `readCase` **catches**, so an uncapped loop there hangs the SSR request
instead of failing it (Plan 1 Task 6's resume loop, on the request path). **A comment explaining why
a line is where it is does not test that it stays there** — if a sentence in a docstring states a
safety property, there should be an assertion with the same content.

**A sabotage that crashes the test runner scores as SURVIVED on an assertion-counting harness.**
Removing `MAX_PAGES` entirely kills the vitest worker with `SIGABRT` mid-file; the JSON reporter then
records **zero** failed assertions, with the file's other tests simply absent from the report.
Raising the cap to a large finite number instead produces an ordinary failure. So a sabotage harness
must distinguish "no test failed" from "the run did not complete" — check the reporter's own
accounting and the number of tests that reported at all, not the exit code, and never read a green
assertion count from a run that aborted.

**An ISO timestamp compared as a string inverts, and the obvious fixture cannot catch it.** `Z` (0x5A)
sorts above `.` (0x2E), so `"…T05:00:01Z" > "…T05:00:01.500000+00:00"` is `true` while the offset row is
the *later* instant — the **older** row wins a newest-wins dedup. Grace writes `+00:00` microsecond
stamps and nothing stops another writer using `Z`. Compare with `Date.parse`, in **both** places that
pick a newest escalation (`listQueue` and `readCase` each have their own picker; fixing one does not fix
the other). **The stamps in such a test must differ within the same second** — a fixture an hour apart
agrees under both orderings, because the hour differs before the `Z`/`.` byte is reached, and a
string-comparison sabotage against it *survived*. The test now asserts its own fixture disagrees before
asserting the behaviour.

**`parseInt` on a DynamoDB `N` chosen by a `.`-test reads a large number back as `1`.** boto3
serializes `Decimal` in canonical form, so `1e30` arrives as `{"N": "1E+30"}` — no `.` in it — and
`parseInt("1E+30", 10)` is **1**, a value a factor of 1e30 too small with no error anywhere. Use
`Number()`. One such row exists live (`c-002`, the type round-trip row, which carries the table's only
`BOOL` and `N` values).

**`as NodeJS.ProcessEnv` does not compile under Next 16.** It declares `NODE_ENV` as a **required**
property (`next/types/global.d.ts:23`), so `{ AWS_REGION: "x" } as NodeJS.ProcessEnv` fails with
`TS2352`. `readEnv` takes `EnvSource = Readonly<Record<string, string | undefined>>` — what it actually
needs, and `process.env` is assignable to it. Casting through `unknown` would reintroduce Task 2's
"the promise stops being checked" hole.

**A placeholder belongs to the renderer, never to the data layer.** The draft's `str(row.program, "—")`
was structurally always a dash, because no escalation row carries `program` at all (measured across all
18; `d_program` exists only on `renewal_submitted` rows). A presentation dash inside `lib/cases.ts` is a
magic value a caller cannot tell from real data — return `""` and let Task 6's `{summary.deadline || "—"}`
decide. Same division of labour as `authority.py` leaving escaping to whoever renders `detail`.

**Grace's own outcome row must not count as a human decision.** Task 5 writes
`sk: DECISION#<ts>#outcome`, which shares the prefix `readCase` collects decisions by and carries no
`decision` attribute. Under a naive prefix test it becomes a phantom **deny attributed to nobody**
(`decided_by` is absent), and an outcome written before any human decision makes `alreadyDecided` true —
so the **first** caseworker decision on a case refuses itself as a duplicate. `readCase` discriminates
on the presence of `decision` and joins the outcome to its decision by their shared `decided_at`; the
draft read `outcome` off the human row, where it is never written, making `Decision.outcome`
structurally always `null`. An **unrecognised** decision word must still *count* as a decision (or the
case becomes decidable twice) while *displaying* as a deny — the opposite polarity to `authorize`'s
allowlist, and for the same reason: never imply a human authorised a filing they did not.

**The live table is 643 rows and the GSI holds 18, not 633/17** — a sweep ran between
`docs/plan3-live-data-findings.md` and Task 3. Treat both as measurements with a date, not constants.
Also: `d_trace_id` is **not** universally present — 613 of 625 ledger rows carry it as `{"NULL": true}`
and **12 rows on `c-003` lack the attribute entirely**, so a reader must handle absent *and* `NULL`.

**A `queryAll` on an SSR request path needs a page cap, and it must throw rather than truncate.**
Plan 1 Task 6 ran an uncapped resume loop to 500 rounds; here a service repeating a `LastEvaluatedKey`
hangs a page instead of failing it, which is worse. Truncation is worse still — a DynamoDB Query drops
the *newest* rows, exactly where `renewal_submitted` lives, so a filed renewal reads as unfiled.
`readCase` turns a throw into `null` (fail closed) and would turn a truncation into a confident wrong
answer.

**`node --experimental-strip-types` cannot run `web/`'s modules**, so a live-data check goes through
vitest. Node's ESM resolver requires file extensions and `lib/cases.ts` imports `"./env"`; the plan's
`npx tsx` fallback has the same problem. A temporary test file plus a `vitest.*.mts` config **inside
`web/`** (outside it, `vitest/config` does not resolve) works, and `disableConsoleIntercept: true` with
`reporters: ["verbose"]` is what makes the output visible. Delete both afterwards.

**The JS SDK retries `InvokeAgentRuntime` three times by default, and the knob is not boto3's.**
Plan 2 established the hazard — the call is **not idempotent**, so each attempt re-runs the whole graph
against the same case and could file one renewal more than once. Task 5's draft built
`new BedrockAgentCoreClient({ region })` with no retry config, reintroducing it in a different SDK.
Measured against a black-hole socket (accepts, never replies, so the accept count *is* the attempt
count): **default 3, `maxAttempts: 1` exactly 1** — and confirmed off the client's own resolved config,
where `maxAttempts` is a provider function. **Do not carry Plan 2's boto3 finding across verbatim:**
there `max_attempts: 1` still gave 2 and only `total_max_attempts` gave 1; here `maxAttempts: 1` is
sufficient and `total_max_attempts` does not exist. Same hazard, different knob.

**`requestTimeout` alone does not bound a JS SDK call — it warns and hangs.** Measured:
`@smithy/node-http-handler - [WARN] a request has exceeded the configured requestTimeout. Init client
requestHandler with throwOnRequestTimeout=true to turn this into an error.` The promise stays pending,
which in an SSR route holds the caseworker's browser open with no error to report. Always pass
`throwOnRequestTimeout: true` alongside it.

**An extra payload key is harmless on the deployed runtime, verified live rather than by reading.**
`process_case` validates the container type and reads `case_id`/`today` by key with no allowlist.
Invoking `grace_grace-oTyyvo8stE` with `caseworker_approved: true` added returned 200 in 10.2s and
`c-010` **still escalated** on `missing_document` — so the approval flag changes no verdict, and it
changes nothing on the deployed version 2, which does not know the key yet.

**A Next middleware matcher's negative lookahead matches a *prefix*, not a path segment.** Task 4's
draft `"/((?!login|api/auth|...).*)"` left `/loginx` and `/api/authorize` **ungated** — measured.
Neither route exists today, which is exactly what makes it the kind of bug that ships later: someone
adds `/api/authorize` and it arrives unguarded. Anchor each alternative on a segment boundary
(`login$|login/`, `api/auth$|api/auth/`, `favicon\.ico$`) and verify the regex with a table of paths
rather than eyeballing it. This is a *redirect convenience* rather than the security boundary —
`verifySession` refuses independently on every page and on the decide route, and `proxy.ts` says so in
its own docstring — but a guard with a hole in it invites someone to start trusting it. **The `node -e`
table the plan uses proves it once and guards nothing after**, so `__tests__/cognito.test.ts` now reads
`config.matcher[0]` off the module and drives the same paths through it; sabotaging the anchors back to
bare prefixes fails two tests.

**`Number.isFinite` is not a type predicate, so the expiry guard did not compile.** Its declaration is
`isFinite(number: unknown): boolean` — a plain `boolean` — so `if (!Number.isFinite(payload.exp)) return null;`
leaves `payload.exp` typed `number | undefined` and `payload.exp * 1000` fails with
`TS18048: 'payload.exp' is possibly 'undefined'`. The plan's `cognito.ts` did not pass `tsc`. Fixed with
a one-line `isFiniteNumber(value): value is number` wrapper, which keeps `Number.isFinite` the **only**
runtime check — adding `typeof exp === "number"` alongside would re-admit the hazard, since
`typeof Infinity` is `"number"`, and `payload.exp!` is the non-null assertion Task 2 called "where the
promise stops being checked". **And the guard was shipped untested:** the plan calls it load-bearing and
has no assertion for it, so a test now hand-builds `exp: 1e400` and watches it refused. Measured, all
three parts: `JSON.parse('{"exp":1e400}').exp` is `Infinity`, `jose` **verifies** that token, and
`Infinity * 1000 <= Date.now()` is `false` — a permanent session. The token must be signed with
`CompactSign` over raw bytes because `SignJWT` refuses it first (`"exp" claim must be a finite number`),
which is the point: the hazard is a token an attacker crafts, not one `jose`'s builder would emit.

**`process.env` accepts a `defineProperty` descriptor only with all three flags set.** Node throws
`'process.env' only accepts a configurable, writable, and enumerable data descriptor` on any subset —
measured on all four. The `NODE_ENV`-flipping test in Task 4's draft set `configurable` alone, so it
threw on the way *in*, and the identical call in its own `finally` threw again and replaced the original
error. Worse than the error: with the write refused, `NODE_ENV` stayed `"test"`, so the assertion would
have been checking the injected-keys path against itself. The test now sets all three flags, **asserts
`process.env.NODE_ENV` actually changed** before trusting the refusal, and re-verifies the same token
after restoring — a one-way guard that left the module permanently refusing would otherwise look
identical to a working one.

**A Next page that reads configuration must be `force-dynamic`, or `next build` runs it.** `/login`
calls `hostedUiUrl`, which throws on a missing `COGNITO_DOMAIN` — and Next prerenders a page with no
dynamic API by default, so the build failed outright: `Error: COGNITO_DOMAIN is not set.` …
`Export encountered an error on /login/page, exiting the build`, exit 1. `npm run build` is the gate
Amplify runs, so this was a shipped-broken deploy. The quieter half is why `force-dynamic` is the right
fix rather than supplying the variable at build time: with it present, the redirect URL — client id and
`DASHBOARD_URL` included — bakes into the bundle, so rotating the app client keeps sending caseworkers
to the old one until someone rebuilds. A sign-in redirect is request-time configuration.

**The test-only JWKS resolver must select by `kid` and throw on a miss.** With `?? keys[0]`, a token
naming an unknown `kid` verifies against whichever key is first. Reproduced with a two-key harness:
strict resolver → right key `ACCEPTED`, wrong key same kid `ERR_JWS_SIGNATURE_VERIFICATION_FAILED`,
unknown kid `no key for kid other-kid`; loose resolver → unknown kid **`ACCEPTED`**. Grace's own pool
publishes **two** RS256 signing keys (verified live), so "checks `kid`" and "ignores `kid`" are
genuinely different verifiers and the loose one makes the suite unable to tell them apart — test
vacuity, not a production bypass, since this branch is gated on `NODE_ENV === "test"`.

**Two live facts that make the verifier's allowlists exact rather than defensive.** Read off Grace's
pool: `jwks_uri` is exactly `${issuer}/.well-known/jwks.json`, and
`id_token_signing_alg_values_supported` is `["RS256"]` and nothing else — so `algorithms: ["RS256"]`
restricts nothing legitimate. Also confirmed on the provisioned pool: tier `ESSENTIALS`,
`ManagedLoginVersion: 1` (classic hosted UI, which is what `hostedUiUrl`'s `/login` path targets),
seeded user `CONFIRMED` (so `admin_set_user_password(Permanent=True)` is required, not a convenience),
and the converge branch's full replace preserved `ReadAttributes`/`WriteAttributes`/flows/scopes/
callbacks on a second run.

**`ListUserPoolClients` paginates too, and the draft read one page.** The pool lookup above it pages
correctly; the client lookup did not. A missed page creates a *second* `grace-dashboard` client, and
`verifySession` checks `aud` against the one id in the environment — so a token minted by the other
client is refused **with a valid signature**, which reads as "auth is broken" rather than "there are
two clients". Confirmed off the service model: `NextToken` on both input and output,
`can_paginate: True`.

**Next 16.3.4 deprecates the `middleware.ts` file convention in favour of `proxy.ts`**, and Grace has
made the rename: the file is `web/proxy.ts` and the exported function is `proxy`. It was a `warnOnce`
on every build, and clean output rather than a zero exit code is this project's bar.
`PROXY_FILENAME = "proxy"` is present in `next/dist/lib/constants.js`, so the new convention is
genuinely supported here rather than aspirational; the build still labels the compiled output
`ƒ Proxy (Middleware)`. **Never ship both files — that is a hard error, not a warning.** Done before
Task 5 deliberately, so its `route-guard.test.ts` is written against the current name rather than
renamed afterwards.

**Two of Task 4's plan defects were inverted security claims — a comment asserting the opposite of the
measured behaviour.** Both were caught by probing the control rather than reading the API's response,
and both had been recorded in `docs/plan3-live-data-findings.md` as *confirmed* findings while being
backwards:

1. **Omitting `WriteAttributes` does not withhold write access — it grants everything.** The draft
   called the omission "capability absence: the client cannot rewrite the claim that authorises it."
   Measured on a throwaway pool, a signed-in user's `UpdateUserAttributes` against an ungranted
   **mutable** custom attribute **succeeded**. `custom:role` survived only because the schema marks it
   `Mutable: False` — one guard, where the comment claimed two. The AWS docs say so outright: omitting
   read/write permissions grants *all* attributes. Fixed to `"WriteAttributes": ["email"]`, and
   re-verified against the **shipped constant**: a write to `custom:role` is now refused
   `NotAuthorizedException: A client attempted to write unauthorized attribute` — an *authorisation*
   refusal, and the previously-succeeding mutable write is refused too.
2. **`UpdateUserPoolClient` is a full replace, not a patch.** An update naming only `ClientName` left
   `ReadAttributes`, `CallbackURLs`, and `AllowedOAuthFlows` **absent** afterwards. So the converge
   branch must resend every field it intends to keep; a later "tidy this into a two-key delta" edit
   would silently delete the OAuth flows and the `custom:role` read permission, and every sign-in would
   then fail closed with nothing failing at provision time. It also rejects `GenerateSecret` with
   botocore's `ParamValidationError` — **not** a `ClientError`, so no `except ClientError` catches it.

**Why the first one was easy to get wrong, which is the transferable part.** Probing `custom:role`
alone *does* produce a refusal — `InvalidParameterException: Attribute cannot be updated.` — but that
is the **immutability** guard, not an authorisation one. Reading it as evidence that omission withholds
permission confirms the wrong mechanism. **To verify a control, perform the action it should prevent
against a target where only that control can refuse.** Same shape as Plan 2's point-in-time-recovery
finding: "the API accepted my configuration" and "the control does what I think" are different claims.

**An index needs its own ARN in an IAM policy, and the failure is invisible to every page but one.**
Measured with `simulate-principal-policy` on throwaway roles: granting `dynamodb:Query` on
`table/grace-cases` alone yields `allowed` on the table and **`implicitDeny` on
`table/grace-cases/index/escalation-queue`**. `readCase` reads the table and `listQueue` reads the
index, so a table-only grant leaves `/case/c-010` rendering perfectly while **`/queue` comes back
empty** — the one page the product exists for, blank for exactly the three households who need a human,
after a green deploy. Task 7's compute role must name both ARNs. `implicitDeny` rather than
`explicitDeny`, so it can be added later without fighting a statement.

**A streaming response body decoded as bytes fails *after* the work already succeeded, which is the
worst shape a bug can have here.** `InvokeAgentRuntimeResponse.response` is typed
`StreamingBlobTypes` (confirmed in the SDK's own `models_0.d.ts`) and arrives as a Node
`IncomingMessage` carrying `sdkStreamMixin` — not a `Uint8Array`. Task 5's draft called
`new TextDecoder().decode(response.response)`, which throws
`TypeError: The "list" argument must be an instance of ... ArrayBufferView`. That throw lands in
`lib/decide.ts`'s own `catch`, so **every** approve would have reported "Grace could not be re-run"
while the invocation had in fact run to completion: a caseworker told Grace failed on a case Grace had
just decided, no error anywhere, and **no test that mocks the client would notice**, because a mock
returns whatever shape the test author imagined. `transformToString()` is the supported read.
**When a client returns a stream, assert against the real client's types, not against your fake's.**

**Grace's outcome row must not look like a human decision, and `lib/cases.ts` already depends on
exactly how it differs.** Task 5 writes `DECISION#<ts>#outcome` with **no `decision` attribute**, and
`readCase` discriminates on that attribute's presence, joining the outcome to its human row by their
shared `decided_at`. Under a naive prefix test the outcome row becomes a phantom deny attributed to
nobody (`decided_by` is absent), and an outcome written before any human decision makes
`alreadyDecided` true — so the **first** caseworker decision on a case refuses itself as a duplicate.
Do not change either field without reading `readCase`'s `DECISION#` branch.

**The decision row is written *before* the invocation, which is deliberately the opposite of the
ledger's discipline.** Both are right because they claim different things: a ledger row claims *Grace
did something*, true only once a tool returned (hard rule 6); a decision row claims *a human decided*,
true the moment they clicked. Losing that to an infrastructure error would discard the caseworker's
work and leave the case silently unresolved. So `recordDecision` throwing means "nothing happened",
and the route can honestly return 503 saying so.

**`is True` / `=== true`, never truthiness, on any flag that crosses a JSON boundary.** An HTTP body
can carry `"false"`, `1`, or `[0]`, all of which are truthy in Python and JavaScript; only the JSON
boolean deserialises to a real `true`. `grace/entrypoint.py` reads
`payload.get("caseworker_approved") is True` and `lib/decide.ts` reads `body.filed === true`. Same
allowlist-over-truthiness polarity as `APPROVE_DECISIONS`, for the same reason: **the unrecognised
value must be the safe one.** Both are pinned by a test that fails under a `bool()`/truthy rewrite.

**Map refusal codes to HTTP status through a `Record<RefusalCode, number>`, not a ternary chain.**
The `Record` makes adding a code in `lib/authorize.ts` a **compile error** at the route instead of a
silent fall-through to 400 — and it immediately caught `case_incomplete`, which Task 3 added after
Task 5's draft was written and which is a server-side "re-run the sweep" (409), not a client mistake.
A companion runtime test asserts the map is exhaustive, because a `Record`'s keys are erased at
compile time.

**A test that mocks the data layer but not the session tests only the first refusal branch.**
Task 5's draft `route-guard.test.ts` mocked `readFacts` and left the session unmocked, so all four
tests refused at `no_session` and every later branch — role, status, already-decided, the decision
allowlist, the note cap — was unreachable. Its decision-word test then papered over this with
`expect([400, 401]).toContain(status)`, meaning **a route that returned 401 to every authenticated
caseworker would have passed it**. The fix mints a real RS256 token so the route's own cookie parsing
and the real `verifySession` stay in the path, and asserts each code exactly. **A range of acceptable
status codes in an auth test is a smell: it usually means the test cannot tell which branch it reached.**

**A "flag cannot loosen the verdict" test needs a fixture where the verdict could actually loosen.**
Task 5's headline test asserted `c-010` still escalates when approved — but `FakeGraph` files nothing,
so hard rule 6's third branch ("clean case, no renewal filed") escalates it for *every* input.
Measured: with the plan's own sabotage `if caseworker_approved: gate = None` spliced in, `c-010` still
came back `escalated` and every assertion held. The claim was true of the run and unproven by the test.
Arming the store with a `renewal_submitted` row first makes `renewal_filed` return `True`, which gives
the sabotaged code a path to `acted` — so the gate becomes the only thing between an approval and a
household missing a document being reported as filed. That sabotage now fails three tests. **When a
test asserts an outcome that several independent branches all produce, it is not testing the branch you
mean; remove the other branches' alibis first.**

**`vi.fn()` narrows to its first implementation's literal type.** A fake seeded with
`status: "escalated"` makes every later `mockImplementation` returning `"acted"` or `"error"` a
*typecheck* failure — one of the five gates, not a runtime surprise. Type the mock's return explicitly.

**An Amplify manual (zip) deployment does not build the app, and Task 7's draft assumed it did.**
Amplify runs a buildspec **only for Git-connected apps** — `StartDeployment`'s own API documentation
says "Starts a deployment for a manually deployed app. Manually deployed apps are not connected to a Git
repository." So the draft's `zip … web` + `create-deployment` sequence would have uploaded TypeScript
source, deployed nothing runnable, and never executed the `buildSpec` that runs `typecheck`/`lint`/`test`.
A zip must instead contain a **pre-built** Amplify Hosting deployment-specification bundle:
`.amplify-hosting/static/`, `.amplify-hosting/compute/default/` with a self-contained Node entry point
listening on **port 3000**, and a `deploy-manifest.json` whose `routes` end in a catch-all to `Compute`.
**Next.js emits none of it** — measured, a real `npm run build` produces `.next/` with no
`.amplify-hosting/` and no `.next/standalone` absent `output: "standalone"`. So Grace connects the
repository (public, `github.com/mohamedsorour1998/Grace`), which is the supported SSR path.
**That needs exactly one browser step — authorizing the AWS Amplify GitHub app — and there is no API
for it**; `CreateApp`'s `accessToken`/`oauthToken` are legacy personal-token fields that do not cover the
GitHub App installation. It is the only manual step in this project.

**Three Amplify traps that produce a green deploy serving nothing.** First, **framework detection
happens on the *Add repository* page**: a monorepo whose app root is not set to `web` leaves
`platform: WEB` silently, and `WEB` has no route handlers and no middleware — the Cognito gate and the
decide endpoint would simply not exist. Verify `platform` is `WEB_COMPUTE` with `get-app` *before*
building, not after. Second, **`baseDirectory` is `.next` for Next 14+ regardless of SSG or SSR**;
`next export` was removed, and a local build may still leave an `out/` directory that looks like the
right answer but fails with `cannot find required-server-files.json`. Third, **Amplify's build image
ships Node 18**, which Next 16 and `jose` 6 do not support — set the Node version in the build settings
rather than discovering it as a build failure.

**The pages had no session verification at all, and the surrounding documentation asserted they did.**
This is the most serious defect the plan produced, found in Task 6. `proxy.ts`'s own docstring said it
was "a redirect convenience, and **never the security boundary** — a forged cookie gets past it and is
then refused by `verifySession`, which is the check that matters", and that `verifySession` "still
refuses on every page and on the decide route." The second half was true of the decide route and
**false of every page**: `grep verifySession` across `app/` matched only `api/auth/callback` and
`api/case/[id]/decide`. No page read a cookie.

Measured against a real `next start` on the live table, before the fix:

```text
curl localhost:3111/                                    -> 307 /login       (no cookie)
curl -H 'Cookie: grace_session=totally.forged.token' /  -> 200, 45143 bytes,
        all twelve case ids, all three typed escalation reasons, the 9/3 headline
```

An unsigned, unparseable **literal sentence** was a complete authentication bypass for every read in
the application. `web/lib/session.ts`'s `requireSession()` closes it, and all three pages call it
**before** touching `lib/cases.ts`, so an unauthenticated request performs no DynamoDB read either.
Re-verified after the fix on the same server: `307 /login` and **zero case ids** for both `/` and
`/queue`.

**Two lessons, and the second is the general one.** A `redirect()` that throws is the right shape for
this guard — there is no falsy return a caller can forget to branch on, the same reasoning as
`verifySession` having no middle value. And: **a docstring asserting that some other layer performs a
check is not evidence that it does.** The claim was written when it was true of the only consumer that
existed, and stayed in place while the pages were added around it. When a comment says "X is verified
elsewhere", grep for X.

**A PII scanner must be shown to detect PII before its "NONE" means anything.** Task 6's live render
check self-tests the scanner against `Yamamoto`, `Mensah`, and `+1555` and prints those detections
first, then reports `NONE` on the real markup. Without that, "no household identity in the markup" is
equally true of a scanner that matches nothing. Same discipline as the twelve-surname guard having a
companion test that feeds a name in through `reason`.


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
8. **Never remove the span-redaction token, and never give it a non-empty value.**
   `OTEL_SEMCONV_STABILITY_OPT_IN` must keep the `gen_ai_unredacted_attributes=` suffix **with
   nothing after the `=`**. The value is an *allowlist of attributes to leave unredacted*, so the
   empty value means "redact everything" and the trailing `=` is what makes it empty rather than
   absent. Two distinct failures, both verified against the real `Tracer`: **absence** of the token
   disables redaction entirely (`_redaction_enabled=False`), and a **non-empty** value carves holes
   in it — `gen_ai_unredacted_attributes=gen_ai.input.messages;gen_ai.output.messages` reports
   redaction "enabled" while exporting the full household record. So "the token is present" is not
   the same claim as "content is redacted"; `grace/observability.py`'s
   `redaction_is_configured` checks emptiness, not presence.
9. **Never put household identity anywhere a model or a log can reach it.** Not in a span
   attribute — `trace_attributes` are exported verbatim, and the rule-8 policy covers only the five
   `gen_ai.*` content attributes, not custom ones. **And not in a tool's returned text**, which is
   the wider version of this rule, learned the hard way: `read_case` used to return
   `display_name`, a referee quoted it into its deliberation, that text became an escalation reason,
   and the reason reached CloudWatch as a Step Functions payload — a path span redaction does not
   cover. `grace.case_id` yes; name, phone, or address never, in any surface a model reads or a
   service logs. Same rule as the JWT `sub`.

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
