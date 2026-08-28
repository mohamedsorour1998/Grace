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

**Plan 1, Task 1 in progress.** Nothing under `grace/` exists yet — the repo holds docs,
scaffold, and configuration only.

| Task | State |
|---|---|
| 1 — rule packs + deadline math | in progress |
| 2 — case types, store, 12 fixtures | not started |
| 3 — the authority gate (20 tests) | not started |
| 4 — Nova model registry + tools | not started |
| 5 — `AuthorityGate` + `LedgerHook` | not started |
| 6 — Graph spine + `grace sweep` CLI | not started |
| 7 — deliberation swarm | not started |
| 8 — trajectory evals | not started |
| 9 — ledger/trace correlation | not started |

`pyproject.toml`, `LICENSE`, `.gitignore`, `.env.example`, `README.md` all exist and are
committed — **do not recreate them.** Dependencies are installed in `.venv`; no install step is
needed to run tests.

**Capability absence is not implemented yet.** It arrives in Task 4 (tool construction) and
Task 5 (the steering gate). Until then, nothing enforces the boundary — do not write code that
assumes it is already there.

---

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
`GraphResult` has no `stop_reason` field at all. Respond with `interrupt.id` (distinct from
`interrupt.name`), and never send a null response — the server refuses it. See Appendix B.1.

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
