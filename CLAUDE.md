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

It was built for the AWS Agents for Humans Hackathon and submitted on 2026-09-14 (tag `v0.1.0`,
record in `docs/hackathon/README.md`); it is now maintained as an open-source project. Every
design and plan is in `docs/superpowers/` as a dated snapshot; `docs/README.md` indexes the rest.

---

## Current state

**Deployed, and running unattended every day since 2026-09-03.** EventBridge → Step Functions
(`ListCases` over the `CASE_DIRECTORY` partition, then a Map with `maxConcurrency` 3) → Lambda →
AgentCore Runtime `grace_grace-oTyyvo8stE` (version 4). Because the caseload is read from the
table, a household added through the dashboard's `/new` is swept the next day. Memory is
`grace_household_memory-TCf1SS708O`. The dashboard is `https://grace.rosettacloud.app` (Amplify app
`dbi97xicbjbv8`); sign-in is `auth.rosettacloud.app` (Cognito pool `us-east-1_HXs3b0APR`).

**Two invariants are the claims that do not move**: `renewal_submitted` exists for exactly
`c-001`–`c-009` and for none of `c-010`/`c-011`/`c-012`, and the `escalation-queue` GSI holds
exactly those three households with their typed gate reasons. Verify them from DynamoDB, never
from a log line or a route's response. `infra/verify_deployed.py` is the read-only drift check.

**Counts move every change; re-measure before quoting.** On 2026-10-31: 960 Python tests pass
with `-m "not live"` and 2 more are `live`; vitest 234 in 11 files; 23 trajectory evals collect
(`.venv/bin/python -m pytest evals/`, real Bedrock). Never re-run
`test_an_escalating_case_is_never_filed` to get a pass — a failure there is a real regression;
`test_an_escalating_case_does_something_rather_than_nothing[c-011]` is liveness and may need one.

**Scope is four AgentCore surfaces, not five** — Runtime, Memory, Identity, and the deploy
harness; Gateway is deferred. State Identity narrowly: what shipped is a Cognito user pool whose
ID token `verifySession` validates, **not** an AgentCore Gateway JWT authorizer — the runtime is
still IAM-authorised.

**Two deployed facts must not be overclaimed.** Every ledger row's `trace_id` is `NULL` and zero
traces exist in the account, so no Transaction Search query returns anything. And a household
name reached CloudWatch before it was fixed: fixed in the repo, the running system, and DynamoDB,
but pre-fix log events still carry it until retention expires. Details:
`docs/lessons/07-caseload-audits-and-memory.md`.

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

Local A2A server on :9000, no AWS needed for `/ping`, the card, or a malformed request:
`OTEL_SEMCONV_STABILITY_OPT_IN='gen_ai_latest_experimental,gen_ai_unredacted_attributes=' GRACE_STORE=memory .venv/bin/python -m a2a_app`.

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

**`bedrock-agentcore` is pinned exactly, to 1.23.0.** 1.23.1 and later import
`strands.experimental.bidi`, which strands-agents 1.54.0 does not have, and declare
`strands-agents>=1.56.0` only under their own `strands-agents` extra — which Grace does not
install, so no resolver sees the conflict. Measured 2026-10-31: the lock had resolved 1.24.1 and
seven test modules could not import `grace.memory`. The image installs from `pyproject.toml`, not
from `uv.lock`, so a range would let a rebuild ship a container that dies at import.
`tests/test_dependency_pins.py` guards both halves.

**The `a2a` extra is the one dependency added after the hackathon**, for the A2A server alone
(`a2a_app.py`, `Dockerfile.a2a`); the HTTP runtime's image does not install it. It is
`bedrock-agentcore[a2a]==1.23.0` plus `a2a-sdk[http-server]==0.3.26`, the last 0.3.x (2026-04-09)
and the range that extra accepts. Measured 2026-11-01, it adds **8** packages to the dev venv
(64 → 72): `a2a-sdk`, `annotated-doc`, `fastapi`, `google-api-core`, `google-auth`, `proto-plus`,
`pyasn1`, `pyasn1-modules`. Three of those had releases younger than two weeks, so
`[tool.uv] constraint-dependencies` holds `fastapi<=0.143.3`, `google-api-core<=2.42.0` and
`pyasn1<=0.6.4` (each the newest release on or before 2026-10-17), and the A2A image installs from
`uv.lock` so the holds reach the container.

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
the **Deep Automation** pattern — its value is in backend process, not a chat UI. As deployed:

```text
EventBridge (daily) → Step Functions (ListCases → Map) → Lambda → AgentCore Runtime
                                                                     │
                                Memory (per-household facts)          ┤
                                DynamoDB grace-cases (records,        ┤
                                  ledger, escalations, decisions)     ┤
                                SMS channel (a transcript; see limits) ┘

Next.js dashboard (Amplify) → route handler → invoke_agent_runtime   (never client → agent)
```

AgentCore Gateway is deferred (`README.md` says why); `docs/architecture.md` has the full picture.

**A second entrypoint, `a2a_app.py`, serves the same case run over A2A** on port 9000
(`Dockerfile.a2a`, built from `uv.lock`). `grace/a2a.py` accepts exactly `case_id` and `today` —
an A2A caller is not a caseworker, so `caseworker_approved` is refused like any unknown key —
runs `process_case` on a worker thread so `/ping` stays live, and answers with one agent message.
It makes the same two import-time refusals as `runtime_app.py`: redaction first, then region. CI
builds, smoke-tests, and scans its arm64 image; it is not deployed to Runtime.

### Agent structure — three multi-agent patterns, each where it fits

**Graph** (deterministic spine), conditional edges:

```text
intake → documents → deliberate(Swarm) → decide
          └── the swarm runs only when evaluate() reports an income, size, or
              source-conflict reason; decide alone holds action tools and the gate
```

Deadline math is a **tool, not an agent**. Deterministic work does not need a model.

**Swarm** (genuine deliberation) — runs *only* on ambiguous cases. Three opposed roles:
advocate argues the family qualifies, verifier adversarially checks each claim against
readable facts, referee decides whether it is genuinely ambiguous. Every agent needs a
`description=`: Python's swarm builds a routing context from them, so omitting one makes the
swarm route blind.

**Agents-as-tools** (context isolation) — the outreach drafter (`grace/tools/outreach.py`), an
`Agent` wrapped in a `@tool` that drafts the family's message and can neither send nor act.

Loop safety on the swarm is mandatory: an advocate and a verifier ping-pong forever
otherwise. `repetitive_handoff_detection_window` must be **less than** `max_iterations`, or
the iteration cap trips first and detection never fires.

### Ledger

A `LedgerHook` on `decide` appends every tool call and its result, and the action tools append
what Grace did (`renewal_submitted`, `renewal_already_filed`, `family_message_sent`,
`escalated`), to a per-case ledger in DynamoDB. The swarm's own reads are not recorded. In a
benefits context an audit trail is a requirement, not a feature — and this ledger is also
the demo: nine cases handled alone, three escalated, each with a reason.

The ledger is the ground truth for what executed. Trajectory evals read from it rather than
the model transcript, because a transcript-based eval would miss a tool that ran but was not
logged.

### Observability — traces complement the ledger, they do not replace it

The ledger records *what Grace decided and did*, durably, in DynamoDB. Traces record *how long
it took and in what order*, sampled, in CloudWatch. A trace can be dropped by sampling; a
ledger entry cannot. **Never move an eval assertion from the ledger to a span.**

Every ledger entry has a `trace_id` field for the active OTEL trace ID (32 lowercase hex), so that
a DynamoDB row can join to its CloudWatch trace; on the deployed runtime the value is `NULL`
(below). The interesting test is that the two agree: a tool in `GraphResult.execution_order`
with no ledger entry means a tool ran without being logged.

The original design also attached `grace.*` attributes via `trace_attributes=` and named a
Transaction Search query on `grace.gate_decision` as the demo's evidence. Neither exists:
Runtime installs no in-process tracer provider, so there are no spans to filter
(`docs/lessons/04-agentcore-deploy.md`). The DynamoDB escalation queue is the evidence instead.

Alarm on **escalation count `< 3`**, not on error rate. Acting when Grace should have escalated
produces no error, no throttle, and no latency spike — it looks like success.

---

## Testing

Run `.venv/bin/python -m pytest` before claiming anything works.

Tests that call real AWS services carry `@pytest.mark.live`. `pytest -m "not live"` is what CI
runs, with no credentials; `--strict-markers` makes a misspelt marker a collection error.

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

## Lessons

Every lesson below was learned on a real defect, and each file keeps them verbatim:

- `docs/lessons/01-gate-and-tools.md` — the gate, the case store, the tools (Plan 1, Tasks 1–5)
- `docs/lessons/02-graph-and-swarm.md` — the graph spine and the swarm (Tasks 6–7)
- `docs/lessons/03-evals-and-ledger.md` — trajectory evals and trace correlation (Tasks 8–9)
- `docs/lessons/04-agentcore-deploy.md` — Runtime, IAM, Memory, Step Functions, the PII finding (Plan 2)
- `docs/lessons/05-dashboard.md` — the Next.js dashboard, auth, and the decide route (Plan 3)
- `docs/lessons/06-dashboard-deploy-and-sign-in.md` — Amplify, hosted UI, intake, sign-in (Plans 3–4)
- `docs/lessons/07-caseload-audits-and-memory.md` — the live caseload, audits, Memory, rule packs (Plans 4–7)

Ten rules that cut across them — read the source before arguing with one:

1. **Never hand a model two dates and ask it to compare them.** Deadline and freshness math is a tool. (02)
2. **The unrecognised value must be the safe one.** Allowlists, exact-match approvals, `is True`. (02, 05)
3. **A test that cannot fail proves nothing.** Watch it fail; assert a loop ran; sabotage the guard. (03, 04)
4. **Fail closed on verification, open on observability.** Lose the trace id, keep the ledger row. (01, 03)
5. **Deployed is not written.** The store, the image, and the orchestrator's input are three places a caseload lives. (07)
6. **Answer a per-run claim from the run, not from an all-time record.** (07)
7. **When two surfaces disagree, ask whether they answer the same question.** (07)
8. **"The API accepted it" is not "the control works."** Probe by doing what it should prevent. (04, 05)
9. **Never put household identity in a tool's returned text.** A model will quote it somewhere unredacted. (04)
10. **A docstring saying a check happens elsewhere is not a check.** Grep for it. (05)

---

## Infrastructure

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
