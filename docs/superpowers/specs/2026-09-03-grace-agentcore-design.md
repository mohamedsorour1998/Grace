# Grace Plan 2 — AgentCore Deployment Design

**Date:** 2026-09-03
**Status:** approved, ready for implementation planning
**Predecessor:** Plan 1 complete — 360 unit tests, 23 trajectory evals, `grace sweep` reports 9 acted / 3 escalated locally
**Deadline pressure:** 11 days to 2026-09-14 17:00 PT, with Plan 3 (dashboard) and the demo video still ahead

---

## 1. What this plan is

Plan 2 deploys the **unmodified** `grace` package to AgentCore Runtime and builds real AWS
infrastructure around it.

The graph, the swarm, the authority gate, and the steering handler are not touched. That is the
central claim of this plan, and it is a checkable exit criterion: **Plan 1's 360 tests must still
pass, unchanged, when Plan 2 is done.** If deploying requires editing `grace/authority.py`,
`grace/steering.py`, `grace/graph.py`, or `grace/swarm.py`, the abstraction was wrong and the plan
stops to reconsider rather than editing them.

New code is additive and sits behind interfaces that already exist.

### 1.1 Scope decisions

Five decisions were made before design, and they bound everything below.

| Decision | Chosen | Rejected |
|---|---|---|
| AgentCore surfaces | Runtime + DynamoDB + Memory | Gateway, Identity — deferred with reasons (§8) |
| Sweep trigger | EventBridge → Step Functions → Lambda → Runtime | Lambda-only; manual invoke |
| Infrastructure | Idempotent `boto3` scripts under `infra/` | CDK; hand-run CLI runbook |
| Ledger storage | `DynamoDBCaseStore` as a second `CaseStore` implementation, env-selected | dual-write; replacing `InMemoryCaseStore` |
| Escalation in the cloud | Write a pending row to DynamoDB, do **not** resume | `waitForTaskToken`; auto-deny |

Scope discipline is the dominant constraint. The judging rubric rewards a working deployed demo
over breadth of surfaces used, so three surfaces that work beat five that half-work.

---

## 2. Architecture

```text
EventBridge (daily cron, 09:00 UTC)
      │
      ▼
Step Functions  grace-sweep
  Map over 12 case ids (maxConcurrency 3)
    ├── Retry: 2× on throttle / 5xx / Lambda.TooManyRequests
    └── Catch: States.ALL → write escalation row  (fail closed)
      │
      ▼
Lambda  grace-invoke-case   (one case per invocation)
      │  invoke_agent_runtime(runtimeSessionId=<uuid, 33+ chars>)
      ▼
AgentCore Runtime  grace          ← IAM auth; not directly invocable by anyone else
  └── grace/ package, unchanged
        ├── DynamoDBCaseStore            ──→ DynamoDB grace-cases
        ├── AgentCoreMemorySessionManager ──→ AgentCore Memory (orchestrator only)
        └── TranscriptChannel             ──→ transcript (SMS stays sandboxed)
      │
      ▼
CloudWatch: traces (Transaction Search, already ACTIVE) + alarm on escalated < 3
```

### 2.1 New Grace code

| New | Path | Behind |
|---|---|---|
| DynamoDB ledger store | `grace/cases/dynamo_store.py` | the existing `CaseStore` Protocol |
| Runtime entrypoint | `grace/entrypoint.py` | new — the `invoke_agent_runtime` handler |
| Memory wiring | `grace/memory.py` | attached to the orchestrator only |
| Telemetry setup | `grace/observability.py` | specified in Plan 1 Appendix E.3 |

Infrastructure lives in `infra/` as idempotent `boto3` scripts plus one shell script wrapping the
`agentcore` CLI. Re-running any of them is safe and is the intended recovery path.

---

## 3. Preflight — verified 2026-09-03, not assumed

Run against account `<AWS_ACCOUNT_ID>` / `us-east-1`.

**Already satisfied — do not redo:**

- **CloudWatch Transaction Search is `ACTIVE`.** `aws xray get-trace-segment-destination` returns
  `Destination: CloudWatchLogs, Status: ACTIVE`. CLAUDE.md warns this is a one-time account action
  taking up to ten minutes and must not be left to demo day. It is done.
- All three Nova inference profiles are `ACTIVE`: `global.amazon.nova-2-lite-v1:0`,
  `us.amazon.nova-pro-v1:0`, `us.amazon.nova-micro-v1:0`.
- `uv` 0.12.5, `node` v24.19.0, `npm` 11.17.0.

**Blockers, handled as Task 0 before any deployment work:**

- **The Docker daemon is not running.** The binary is installed (29.7.2) but `/var/run/docker.sock`
  does not exist. `agentcore deploy` builds a container image and pushes it to ECR, so this is a
  hard stop, not a warning.
- **The `agentcore` CLI is not installed.** Latest `@aws/agentcore` on npm is **0.28.1**; Plan 1's
  appendices were verified against **0.24.2**. Expect CLI drift and re-introspect `create` / `add` /
  `deploy` rather than trusting the recorded command shapes.
- **No Grace resources exist yet** — no `grace-cases` table, no Grace runtime, memory, or gateway.
  Only unrelated prior projects' resources are present, which does at least prove the account's
  AgentCore APIs are reachable.
- The shell is authenticated as **root**, not the `grace-dev` IAM user CLAUDE.md prefers.

### 3.1 `AgentCoreMemorySessionManager` is not in `strands-agents`

The spec's §3.7 assumed it was available from the SDK. It is not — `strands.session` ships only
`file`, `s3`, `repository`, and `snapshot` managers. It lives in **`bedrock-agentcore`**, which is
not installed.

Measured marginal cost against Grace's actual venv: **2 packages** — `bedrock-agentcore` and
`websockets`. Everything else it wants (boto3, botocore, pydantic, anyio, urllib3, …) is already
satisfied by `strands-agents`. That is smaller than the `[otel]` extra's 10 and it is first-party
AWS, so it is within the dependency rule — but it *is* a dependency change and must be recorded in
`pyproject.toml` deliberately, with this measurement as the justification.

Verified signatures (introspected against the real package, with `strands` present):

```python
AgentCoreMemorySessionManager(agentcore_memory_config, region_name=None, boto_session=None,
                              boto_client_config=None, *, converter=None, **kwargs)

AgentCoreMemoryConfig(*, memory_id: str, session_id: str, actor_id: str,
                      retrieval_config: dict[str, RetrievalConfig] | None = None,
                      batch_size: int = 1, flush_interval_seconds: float | None = None,
                      context_tag: str = "user_context", filter_restored_tool_context: bool = False,
                      default_metadata=None, metadata_provider=None,
                      persistence_mode: PersistenceMode = PersistenceMode.FULL,
                      async_mode: bool = False)

RetrievalConfig(*, top_k: int = 10, relevance_score: float = 0.2,
                strategy_id: str | None = None, initialization_query: str | None = None)
```

`memory_id`, `session_id`, **and** `actor_id` are all required.

### 3.2 `PersistenceMode` cannot express "write selectively"

Spec §3.7 records a known hazard: guardrail-blocked messages persisted to Memory poison later
turns, and says Grace "writes to memory selectively — blocked or errored turns are not persisted."

`PersistenceMode` has exactly two members, `FULL` and `NONE`. There is no per-turn selectivity.
So the spec named a capability that does not exist, and the hazard must be handled in Grace's own
code: Memory attaches only where the turns are safe to persist, and a turn that errored or was
blocked must not reach it. This plan's Memory task owns that decision explicitly rather than
inheriting a false assumption.

### 3.3 Hard rule 2 constrains where Memory can attach

Agents inside a Graph or Swarm must not have their own `session_manager` — Python raises
`ValueError`. Memory therefore attaches to the **orchestrator only**, never to `decide`, `intake`,
`documents`, or the three swarm agents.

Task 7 established that a `Swarm` node's session manager is the *public* `session_manager`
attribute, and that `test_no_node_has_its_own_session_manager` passed **vacuously** via
`getattr(..., None)` until it was made to recurse into `executor.nodes`. So the assertion guarding
this must be a real one that recurses, not a `getattr` that returns `None` for the wrong reason.

---

## 4. DynamoDB schema and `DynamoDBCaseStore`

One table. The ledger and the escalation queue are the same audit trail read two ways, so they
share it.

```text
Table: grace-cases          (PAY_PER_REQUEST, point-in-time recovery on)

  PK (S)          SK (S)                      purpose
  ─────────────────────────────────────────────────────────────
  CASE#c-011      LEDGER#<iso8601>#<seq>      one ledger entry
  CASE#c-011      ESCALATION#<iso8601>        pending caseworker decision
  CASE#c-011      CASE                        case record (Plan 3 reads this)

  GSI escalation-queue
    PK: status (S)   SK: escalated_at (S)     → "all PENDING_CASEWORKER, oldest first"
```

Three design points, each following from a Plan 1 finding rather than from preference.

**The sort key needs `#<seq>`, not a bare timestamp.** `LedgerEntry.at` is
`datetime.now(timezone.utc)`, and Task 9's correlation test showed one tool call writing `tool_call`
and `tool_result` in rapid succession. Two rows sharing a microsecond would collide on the sort key
and one would silently overwrite the other — losing an audit row, the one thing this table exists
not to do. A monotonic per-case sequence appended to the SK makes that impossible.

**`ledger()` must return entries in the same order `InMemoryCaseStore` does.** Task 8's evals assert
`read_case` precedes any action by reading ledger *position*, and Task 6's `sweep` classifies a case
by scanning for a `renewal_submitted` row. A different order from the DynamoDB implementation would
break every trajectory eval in a way that reads as a gate regression. Query with
`ScanIndexForward=True`, and pin the ordering in a test parametrized over **both** stores — that
shared test body is the real guard against drift, the same reasoning that makes `_most_recent` an
import rather than a reimplementation.

**`float` must become `Decimal` at the boundary.** `LedgerEntry.detail` allows
`str | int | float | bool | None`. DynamoDB has no float type and boto3's serializer *raises* on one
rather than coercing — so a float in `detail` would fail the write **after** the action already
happened. That is hard rule 6 inverted, the same failure `str(channel.send(...))` was added to
prevent in Task 4. Convert at the boundary and pin it with a test.

### 4.1 Error posture

Deliberately split, following the codebase's existing reasoning rather than one blanket rule:

- **Read failures** (`get`, `open_cases`, `ledger`) propagate. Tasks 3 and 4 established that a
  verification path fails closed — an unreadable case escalates, it is never assumed clean.
- **Ledger write failures** propagate. An action that happened with no audit row is worse than a
  visible error, and Step Functions' Catch converts it into an escalation row.

This is the **opposite** of Task 9's `_current_trace_id` decision, for the reason Task 9 stated: the
trace ID is observability and losing it harms nobody, while a ledger row is evidence.

### 4.2 Fixtures stay authoritative

The 12 households stay in `fixtures/households.yaml`. The table holds ledger and escalation rows,
not household records, so there is no second copy of case data to drift and hard rule 3 (synthetic
data only) needs no new enforcement surface.

---

## 5. Runtime entrypoint and the escalation contract

`grace/entrypoint.py` is the one genuinely new piece of logic, and it is where Plan 1's findings
either hold or quietly break.

**One case per invocation.** The entrypoint accepts `{"case_id": "c-011", "today": "2026-10-01"}`
and processes exactly that case; the sweep loop moves into Step Functions' Map state. This matters
beyond tidiness: Task 6 established that `AuthorityGate._seen` is per-instance and in-memory and
that a fresh process starts empty. One case per microVM session means `_seen` can never span two
households — isolation becomes structural rather than conventional.

**Classification reuses `sweep`'s, and is not re-derived.** Task 6 found the alternative broken:
classifying by "did an interrupt fire" reported an incomplete household as handled — 10/2 instead of
9/3, no error. Classification comes from the two things that cannot be argued with: `evaluate()` run
directly on the case, and the ledger's `renewal_submitted` row.

```text
{status: "acted",     case_id, filed: bool,                        trace_id}
{status: "escalated", case_id, reason, question, deadline,          trace_id}
{status: "error",     case_id, detail,                             trace_id}
```

Every case lands in **exactly one** of the three — Task 6's "counted twice or counted nowhere" rule,
which is what makes 9/3 arithmetic that adds up.

### 5.1 The deployed path cannot resume, and that is a safety improvement

When the graph returns `Status.INTERRUPTED`, the entrypoint writes the escalation row and returns
`escalated`. It does **not** resume. This removes `MAX_RESUME_ROUNDS` and the `APPROVE_DECISIONS`
allowlist from the deployed path entirely.

That is stronger than the local path, not weaker. Task 6 established that resuming with a truthy
response *approves* the blocked tool, and confirmed against the real executor that `"Escalate."`,
`"no, hold this one"`, and `"needs review"` all resumed and filed a renewal for `c-010`, a household
missing a required document. **A deployed path with no resume cannot be talked into filing.**

The resume path stays in `grace/run.py` for the local CLI, where a human is actually present.

### 5.2 The escalation `question`

Same source the local escalation row uses, and for the reasons Task 7 established the hard way:

- the gate's typed reason is preferred over the generic run-status fallback, tracked with an explicit
  flag rather than by re-comparing strings;
- the referee's conclusion is **appended, never substituted**;
- the referee is selected from `SwarmResult.results` by the `"referee"` key, never
  `node_history[-1]` — a positional fallback prints the advocate's unchecked argument to a
  caseworker as though a verifier had confirmed it.

The entrypoint **reuses `grace/run.py`'s existing helpers** rather than reimplementing them. A second
implementation of `_deliberation_note` would drift, and Task 7 already documented what its failure
mode costs.

### 5.3 Runtime constraints

- `runtimeSessionId` must be **33+ characters**: `f"grace-{case_id}-{uuid4()}"`.
- **Skip `StrandsTelemetry()` on Runtime.** Constructing it calls `set_tracer_provider` as a side
  effect and would replace the working provider Runtime already installed.
  `grace/observability.py` gates on `AGENT_OBSERVABILITY_ENABLED`, which Runtime sets.
- **Hard rule 8's redaction token must be verified in the deployed environment**, not assumed to
  carry over from `.env.example`. `OTEL_SEMCONV_STABILITY_OPT_IN` must keep the
  `gen_ai_unredacted_attributes=` suffix with its load-bearing trailing `=`; absence of the token
  disables redaction entirely and exports the full household record to CloudWatch.
- `trace_attributes` carry `grace.case_id`, `grace.program`, `grace.window_status`,
  `grace.gate_decision`, `grace.gate_reason` — and never a name, phone, or address (hard rule 9).
  `Agent.__init__` silently drops non-scalars, so pass `gate.reason or ""`, never `None`.

### 5.4 Deliberate deviation: IAM auth, not JWT

The deployed runtime uses IAM auth. Identity is deferred, so there is no Cognito pool and no
caseworker IdP; the runtime is invocable only by the Lambda's execution role. Shipping that honestly
is better than half-wiring a JWT authorizer whose `customClaims` gate nothing.

---

## 6. Step Functions, EventBridge, IAM

### 6.1 State machine

```text
Map (maxConcurrency 3, over 12 case ids)
  └── Task: grace-invoke-case
        Retry  ThrottlingException, ServiceException, Lambda.TooManyRequestsException
               2 attempts, 5s base, backoff 2.0
        Catch  States.ALL → WriteEscalationRow   (fail closed)
  ↓
Aggregate → {acted: N, escalated: N, errors: N}
```

`maxConcurrency: 3` is considered, not a default. Twelve concurrent cases each open a graph
invocation, and the two swarm-routed cases alone cost ~18–19 Bedrock invocations each; twelve at once
invites the throttling the retry policy then has to absorb. Three keeps the sweep to a few minutes
while staying clear of Nova's throttle ceiling.

**The Catch → escalation-row branch is the fail-closed rule expressed as infrastructure.** A Lambda
that times out or a runtime that dies produces no verdict, and "no verdict" must become "a human
looks at it," never "nothing happened." Task 7's timeout finding is the precedent: a graph node
timeout is fail-fast, raised out of the call, and produced a sweep *error* with no escalation row.
Same principle, one layer up.

The aggregate step makes the demo claim executable rather than narrated.

### 6.2 The one alarm that matters

**Alarm on `escalated < 3`.** Acting when Grace should have escalated produces no error, no throttle,
and no latency spike — it looks exactly like success. Standard `SystemErrors` / `Throttles` / p99
latency alarms are worth having as hygiene, but none of them would have caught any of the defects
found in Plan 1.

### 6.3 IAM — four roles, each narrow

| Role | Gets | Notably |
|---|---|---|
| Step Functions | `lambda:InvokeFunction` on the one function; `dynamodb:PutItem` on the table | |
| Lambda | `bedrock-agentcore:InvokeAgentRuntime` on the one runtime ARN | |
| Runtime execution | `bedrock:InvokeModel*` on the 3 Nova profiles; DynamoDB RW on the table; Memory RW on the one memory | **explicit `Deny` on `GetWorkloadAccessTokenForUserId`** |
| EventBridge | `states:StartExecution` on the one state machine | |

The explicit `Deny` carries even though Identity is deferred: an explicit Deny beats any Allow,
including one someone attaches later, and it costs a single policy statement now.
`BedrockAgentCoreFullAccess` is not used anywhere — the docs warn it grants that exact unsafe
action.

Tag every resource `Project=Grace`, `Environment=<env>` at creation. It makes Grace's spend separable
in Cost Explorer against a $50 credit budget.

---

## 7. Verification

Three gates. The first is non-negotiable.

1. **Plan 1's 360 tests pass, unchanged.** If deploying required editing the graph, swarm, gate, or
   steering handler, the abstraction was wrong. New tests are additive.
2. **`DynamoDBCaseStore` is tested by the same test body as `InMemoryCaseStore`**, parametrized over
   both: ordering, `float`→`Decimal`, `trace_id` presence, and `detail` immutability. A separate test
   file for the new store is how the two drift.
3. **A real deployed sweep reports 9 acted / 3 escalated**, with no `renewal_submitted` row in
   DynamoDB for any of the three escalating cases, and `grace.gate_decision = "escalate"` returning
   exactly three traces in Transaction Search. That query is the demo's central claim, executed
   rather than described.

Two Plan 1 test-design lessons carry forward explicitly, because review caught both rather than a
failing test:

- **Assert that a parametrized test's loop actually ran.** Task 8's headline ordering test passed
  having asserted nothing on the two most important cases, because its `for` body never executed for
  them. `assert ran_something`.
- **Never assert a fixed tool-call count against a real model run.** Task 9 measured
  `submit_renewal.call_count` as `1`, `2`, `2` across three real `c-001` runs — both values correct.
  Compare tallies against each other, not against a literal.

---

## 8. Out of scope, with reasons

Recorded so each reads as a decision rather than an omission. The README will say so plainly rather
than implying five AgentCore surfaces shipped when three did.

| Deferred | Why |
|---|---|
| **AgentCore Gateway** | The largest single chunk of remaining work, and the most common deploy-day failure (outbound auth shape differs per target type). The C.1 `target___tool` prefix bug stays fixed and tested in `grace/steering.py` regardless, so re-adding Gateway later cannot silently bypass the gate. |
| **AgentCore Identity / Cognito JWT** | No caseworker IdP exists. A JWT authorizer whose `customClaims` gate nothing is worse than honest IAM auth. Appendix D's findings (D.1's `Deny`, D.4's opaque `sub`) are preserved for when it lands. |
| **SNS / real SMS** | Account is sandboxed: `MaxLimit: 1`, zero origination numbers, and Egypt sender-ID registration needs a letter of authorization, company registration, and a tax card. `TranscriptChannel` remains the always-works path. The demo must never depend on SMS delivery. |
| **Reflection loop (spec §3.8)** | Genuinely the originality differentiator, and genuinely additive. It cannot ship before a deployed sweep exists to reflect on. Candidate for Plan 3 if the dashboard lands early. |
| **Skills / `SKILL.md` (spec §3.6)** | Progressive disclosure is a prompt-size optimization. Grace's prompts are not the bottleneck. |
| **Bedrock Guardrails (spec §3.9)** | Hard rule 8's span redaction already covers the export path that matters. PII `ANONYMIZE` on SSN is real value, but every household is synthetic, so it protects nothing today. |
| **`gen_ai_tool_definitions`** | Additive, and adds a sixth attribute path out. Enable only after §5.3's redaction check passes in the deployed environment. |

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Docker daemon down blocks `agentcore deploy` | Task 0 preflight, before any other work. Fail loudly and early rather than on demo day. |
| `agentcore` CLI drift (0.28.1 vs appendices' 0.24.2) | Re-introspect `create`/`add`/`deploy` before use; treat recorded command shapes as hints. |
| Bedrock latency on swarm cases | Already measured: one eval run in five timed out at `set_node_timeout(420.0)` under real latency. Step Functions' retry absorbs a transient; the Catch branch turns a persistent one into an escalation, not a silent gap. |
| Deployment eats the dashboard's time | Scope was cut to three surfaces for exactly this reason. Gateway/Identity are documented, not started. |
| Root credentials in use | Plan provisions with the existing session but scopes the *deployed* roles narrowly. Switching the local shell to `grace-dev` is noted, not blocking. |

---

## 10. Open items

1. **`grace-dev` vs root for provisioning.** Roles created for the deployed system are narrow either
   way; the question is only which identity runs `infra/`. Not blocking.
2. **Memory's selective-write policy** (§3.2) needs a concrete rule once the entrypoint exists —
   which turns reach Memory, given `PersistenceMode` cannot express it.
3. **EventBridge schedule time.** 09:00 UTC is a placeholder; the demo triggers manually regardless.
