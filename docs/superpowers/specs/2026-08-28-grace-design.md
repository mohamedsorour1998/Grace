# Grace — Design Spec

**Date:** 2026-08-28
**Track:** Good Neighbor Agents (AWS Agents for Humans Hackathon)
**Deadline:** 2026-09-14 17:00 PT (17 days from spec date)
**SDK:** `strands-agents==1.54.0` (verified by introspection, not documentation)

---

## 1. Problem

During Medicaid unwinding, the majority of disenrollments were **procedural** — people who
still qualified but missed a letter, a deadline, or one document. They did not become
ineligible. They lost coverage to paperwork.

The people holding this together are caseworkers at clinics, food banks, and school
districts, tracking recertification windows for hundreds of households across programs with
different clocks (Medicaid, SNAP), in languages the notices are not written in.

**One sentence:** A family that still qualifies is stuck re-proving it, over and over, and
loses coverage when a single letter goes unanswered.

### Why an agent, not an app

The work is: watch a clock, notice a gap, chase one document, and know when a human must
decide. Nobody opens an app to do that. It has to run in the background and surface only at
the decision. That is the hackathon brief's definition of a strong entry, and it is a real
description of this problem rather than a retrofit.

### Non-goals

- Not a benefits *eligibility determination* system. Grace never decides who qualifies;
  it decides what is unambiguous enough to file and what a caseworker must judge.
- Not a case management system replacement.
- Not a chatbot. There is no conversational surface for families beyond document chase.

---

## 2. Scope (17 days)

**In:** Medicaid + SNAP, one state's rule pack, one workflow end to end
(sweep → detect → verify → act-or-escalate → learn).

**Out:** WIC, school meals, multi-state rule packs, real PII, real caseworker accounts.

100% synthetic households. Stated plainly in README and video. Per hackathon FAQ, synthetic
data is the recommended posture and reviewers flag anything resembling real PII.

---

## 3. Architecture

### 3.1 System shape

Per AWS guidance ("We Need To Talk About AI Agent Architectures", Morgan Willis), the agent
is a capability inside the system, not the backend. Grace uses the **Deep Automation Agent
Pattern** — the agent's value is in backend process, not a chat UI:

```text
EventBridge (daily sweep)
      │
      ▼
Step Functions  ── orchestrates the sweep, owns retries + workflow state
      │
      ▼
Lambda  ── per-household invocation
      │
      ▼
AgentCore Runtime (VPC network mode)  ── the Grace harness
      │
      ├─→ AgentCore Gateway ──→ rule-pack API + document store (MCP tools)
      ├─→ AgentCore Memory  ──→ per-household long-term facts
      ├─→ DynamoDB grace-cases ──→ ledger + case state
      └─→ SMS channel ──→ family document chase

Next.js dashboard ──→ API layer ──→ invoke_agent_runtime
                                    (never client → agent directly)
```

Step Functions owns retries, branching, and durability so the agent does not have to.
The dashboard never talks to AgentCore directly.

### 3.2 The authority gate (the core of the design)

Grace's defining property: **it acts alone on the routine and provably escalates the rest.**
That boundary is enforced three ways, in order of strength.

**(a) Capability absence — strongest.** Privileged tools are not registered in the agent's
tool list at all. `submit_renewal` does not appear in `list_tools_sync()` for a case that has
not passed verification. Grace cannot file a renewal it should not, because the capability
does not exist in that context. (Pattern verified in `ai-agent-guardrails`: a lambda present
on disk but never registered in CDK.)

**(b) Identity from the token, never the conversation.** Household lookup tools take **no
household-ID argument** — `properties={}, required=[]`, described as "identity is determined
from the session." A Gateway interceptor decodes the JWT and injects the verified household
ID server-side. A prompt injection cannot redirect Grace to another family's case because
there is no parameter to poison. The reference implementation measured this as the difference
between one breach and zero.

**(c) Deterministic steering gate.** `AuthorityGate(SteeringHandler)` overrides
`steer_before_tool`. Plain Python, no model in the decision:

```python
async def steer_before_tool(self, *, agent, tool_use, **kwargs) -> ToolSteeringAction:
    if tool_use["name"] not in ACTION_TOOLS:
        return Proceed(reason="Not a state-changing action")
    ledger = self.steering_context.data.get("ledger", {})
    calls = ledger.get("tool_calls", [])
    # every prerequisite must have completed with status == "success"
    ...
    return Interrupt(reason=...)   # ambiguous → human decides
```

Verified in 1.54.0: `ToolSteeringAction = Proceed | Guide | Interrupt`;
`ModelSteeringAction = Proceed | Guide` only. `Interrupt` is **not valid** in
`steer_after_model` ("the model has already responded"), so the gate must live in
`steer_before_tool`. All three actions take a required `reason: str`.

Why steering rather than a plain module: Clare Liguori's 600-run evaluation measured
**steering at 100% pass rate vs 82.5% for prompt instructions and 80.8% for graph
workflows**, at 66% fewer input tokens than the SOP approach. It is also the only mechanism
that yields a first-class `Interrupt`.

**Gate conditions** — Grace may act alone only if all hold:

1. Renewal window verified from a rule pack, not inferred by a model.
2. Every required document present and unexpired.
3. Household composition and income unchanged outside a de-minimis band the rule pack
   declares immaterial.
4. No conflict between sources.
5. Action is on the allowlist.

Any miss → `Interrupt` with a typed reason. **Fail closed:** a verification *error* escalates.
(The reference repo swallows exceptions in ownership checks and fails open; for benefits
eligibility that is unacceptable.)

### 3.3 Escalation → human → resume

`Interrupt` surfaces as `result.stop_reason == "interrupt"`. The dashboard renders
`interrupt.reason`; the caseworker's decision resumes the run:

```python
result = agent(task)
while result.stop_reason == "interrupt":
    for it in result.interrupts:
        decision = await caseworker_decision(it.reason)   # dashboard
        result = agent([{"interruptResponse": {"interruptId": it.id, "response": decision}}])
```

`event.interrupt(name, reason=None, response=None)` verified on `BeforeToolCallEvent`.
Interrupt-capable events: `BeforeToolsEvent`, `BeforeToolCallEvent`, `BeforeNodeCallEvent`.

### 3.4 Multi-agent structure

All three Strands patterns, each where it is the right tool.

**Graph (deterministic spine)** — `GraphBuilder`, conditional edges:

```text
intake → documents → eligibility(Swarm) → decide ─┬─(gate passes)→ act
                                                  └─(else)───────→ escalate
```

Deadline math is a **tool, not an agent**. Deterministic work does not need a model.

Conditional edges: `add_edge(from, to, condition: Callable[[GraphState], bool])`.
Read upstream verdicts via `str(state.results["node_id"])` — `NodeResult.__str__` delegates
safely, whereas `.result.message["content"][0]["text"]` breaks when the node returned a
`MultiAgentResult` or an `Exception`. Not exercised by any reference repo; verify locally.

**Swarm (genuine deliberation)** — runs *only* on ambiguous cases. Three opposed roles:

| Agent | Job | Model |
|---|---|---|
| Advocate | Argues the family still qualifies | `us.amazon.nova-pro-v1:0` |
| Verifier | Adversarially checks each claim; must cite a rule line | `us.amazon.nova-premier-v1:0` |
| Rules referee | Breaks ties, or declares genuine ambiguity → escalate | `us.amazon.nova-pro-v1:0` |

**The verifier runs on a different model than the advocate**, deliberately: the course's
model-provider guidance notes a different model for verification avoids same-model bias. Two
instances of the same model agreeing proves nothing.

Safety params are mandatory — an advocate/verifier pair ping-pongs forever otherwise:
`max_handoffs=10, max_iterations=10, execution_timeout=300.0, node_timeout=120.0,
repetitive_handoff_detection_window=6, repetitive_handoff_min_unique_agents=2`.
`handoff_to_agent` is auto-injected.

**Agents-as-tools (context isolation)** — `@tool` wrapping an `Agent`,
`callback_handler=None`, return `str(response)`:

- **Outreach drafter** — family SMS in their language at the right reading level.
- **Policy retriever** — rule-pack search via Gateway; noisy output stays out of the
  orchestrator's window.
- **Caseworker briefer** — turns an escalation into a one-screen brief: the question, what
  Grace checked, the two candidate readings, the deadline.

### 3.5 Ledger (hooks)

A `HookProvider` writes every node transition and action-tool call to
DynamoDB `grace-cases`. In a benefits context an audit trail is a requirement, not a feature —
and this ledger *is* the demo: 10 households swept, 9 handled alone, 1 escalated with a
specific question.

`LedgerProvider` supplies the gate's prerequisite view. Schema verified — `data["ledger"]`
has `session_start`, `tool_calls`, `conversation_history`, `session_metadata`. Each
`tool_calls` entry is two-phase: pre-call `status="pending"`, post-call `status` from the
result. **Always test `status == "success"`**, never truthiness, or in-flight calls read as
passed. `result` is a *list* of content blocks.

Note: `event.cancel_tool` is an **attribute assignment**, not a call
(`event.cancel_tool = "reason"`) — the AWS doc showing `cancel_tool()` is wrong. The string
is fed back to the model as the tool result, so write it as an instruction.

### 3.6 Skills

One `SKILL.md` per program under `skills/<name>/` — Medicaid renewal, SNAP recert, document
chase. Frontmatter is exactly two keys (verified across 32 skill files): `name`,
`description`. The `description` is all the model sees until activation, so it is written as a
routing signal. Progressive disclosure instead of one bloated prompt.

### 3.7 Memory

`AgentCoreMemorySessionManager` with `actor_id` = **household**. Namespaces:

```python
retrieval_config={
    "/facts/{actorId}":                    RetrievalConfig(top_k=10, relevance_score=0.3),
    "/preferences/{actorId}":              RetrievalConfig(top_k=5,  relevance_score=0.5),
    "/summaries/{actorId}/{sessionId}":    RetrievalConfig(top_k=3,  relevance_score=0.5),
}
```

Verified against working code in `RosettaCloud/Backend/agents/agent.py:250`. Note this is
`/facts/{actorId}`, **not** the `/users/{actorId}/facts` form in the AWS blog post — the
namespace must match the `namespaceTemplates` set at memory-creation time
(`agentcore add memory --strategies ...`). `RetrievalConfig` imports from
`...memory.integrations.strands.config`, a *different module* than the session manager.

Why it matters here: a recert cycle is annual. "Income verified via pay stubs last cycle" and
"prefers Arabic, evenings" must survive the eleven months between contacts.

**Known hazard:** guardrail-blocked messages persisted to Memory poison later turns. The
reference repo documents this and does not fix it. Grace writes to memory selectively —
blocked or errored turns are not persisted.

### 3.8 Reflection loop (advisory only)

On case close: compute the outcome, write 2–4 sentences (was the call right, what held,
one lesson), store as `REFLECTION#{household}`, inject recent lessons into the eligibility
swarm. Adapted from `astrolabe/docs/trade-reflection-memory.md`.

**Lessons can only make Grace more cautious, never less. They cannot override the gate.**

This is the originality differentiator — an outcome-feedback loop is rare in hackathon
entries.

### 3.9 Guardrails

Bedrock Guardrails via `BedrockModel(guardrail_id=..., guardrail_version=...,
guardrail_trace="enabled")` — a pinned numeric version, not `DRAFT`.

Policy: PII `ANONYMIZE` on `US_SOCIAL_SECURITY_NUMBER` (benefits records carry SSNs),
`PROMPT_ATTACK` at input-only, denied topics for legal/medical advice.

Plus an LLM steering handler with a bounded retry budget (`max_retries=2`, then `Proceed`)
enforcing: **never tell a family their renewal was filed unless a tool confirms it.** That is
the specific failure this system must not have.

### 3.10 Context management

`context_manager="auto"` — verified present in 1.54.0 as
`Optional[Literal['auto','agentic']]`. (Reference repos disagreed: deployable paths used
`conversation_manager="auto"` with a comment that `context_manager` was "not yet on PyPI".
It has landed. Both parameters exist.)

---

## 4. Models — Amazon Nova only

All agents run on Amazon Nova via Bedrock. No third-party LLMs in the request path. Every
profile below verified `ACTIVE` in account <AWS_ACCOUNT_ID> / us-east-1.

| Role | Model | Why |
|---|---|---|
| Advocate | `us.amazon.nova-pro-v1:0` | Reasoning for the eligibility argument |
| **Verifier** | `us.amazon.nova-premier-v1:0` | **Different model than the advocate** — preserves the anti-bias property |
| Rules referee | `us.amazon.nova-pro-v1:0` | Tie-break against cited regulation |
| Document classifier | `global.amazon.nova-2-lite-v1:0` | High volume; `global.` for throttle resilience |
| Outreach drafter | `us.amazon.nova-2-lite-v1:0` | Short multilingual SMS |
| Caseworker briefer | `us.amazon.nova-pro-v1:0` | Must be genuinely clear to a human |
| Steering judge | `us.amazon.nova-2-lite-v1:0` | Bounded retry, cheap |

Rationale beyond cost: an AWS hackathon judged by AWS engineers, running entirely on
AWS-native models — same posture as RosettaCloud. Nova pricing keeps the whole build inside
the $50 promotional credits.

---

## 5. AgentCore surfaces

Each has a real job; none is decorative.

| Surface | Job |
|---|---|
| **Runtime** | Hosts the harness. VPC network mode; session-per-case microVM isolation — required, since one family's data must never leak into another's. |
| **Gateway** | Rule-pack API + document store as MCP tools with semantic search. Also the JWT interceptor that injects household identity. |
| **Memory** | Per-household long-term facts across the annual gap. |
| **Identity** | Caseworker OAuth/JWT inbound auth. Not optional when the payload is family benefit data. |
| **Harness** | Loop, tools, hooks, steering, skills, context management. |

Deploy: `agentcore create` → `add` → `deploy` (**not** the older `configure`/`launch`, which
`AWS-Resource-Optimizer-Agent` used via the deprecated `bedrock-agentcore-starter-toolkit`;
that package shadows the current npm CLI and should be uninstalled). `agentcore deploy`
requires a `pyproject.toml` in the agent folder — `uv init --bare` + `uv add`.
`runtimeSessionId` must be **33+ characters**; case session IDs are UUID-based.

Observability: `strands-agents[otel]` + `aws-opentelemetry-distro`, traces to CloudWatch.

---

## 6. Testing

**TDD for the gate.** `authority.py` is pure logic with no model in it — table-driven tests
over the five conditions plus the fail-closed error path. This is the part where a bug
means a family loses coverage, so it is tested first and hardest.

**Trajectory evals** — `TrajectoryEvaluator` proves gate ordering holds: verification
precedes action, always. Turns "we built a gate" into "we can show it holds."

**Chaos** — `ChaosPlugin` with `Timeout` / `NetworkError` on the document store. A benefits
agent that silently fails is worse than none; the test asserts it escalates rather than
guesses. (Documentation-only in the reference repos and the snippet has defects — will verify
against the installed package.)

**Note:** `strands-agents-evals` is a separate, git-unpinned package; `experimental.redteam`
APIs drift. Verify before relying on them.

---

## 7. Delivery plan

| Days | Work |
|---|---|
| 1–2 | Repo, MIT license, rule packs, synthetic households, `authority.py` + tests |
| 3–5 | Graph spine, tools, ledger hooks, `AuthorityGate` steering, local end-to-end |
| 6–8 | Swarm, agents-as-tools, skills, memory |
| 9–11 | AgentCore deploy (Runtime + Memory + Identity), Step Functions sweep, Gateway |
| 12–14 | Dashboard, escalation queue, SMS, guardrails |
| 15–16 | Evals, architecture diagram, README, demo video |
| 17 | Submit early (3–4 h before 17:00 PT) |

Bonus: builder.aws.com post with "Agents for Humans" in the title (+0.2 each, max +0.6).

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| **SMS cannot send** — account is `SANDBOX`, `TEXT_MESSAGE_MONTHLY_SPEND_LIMIT` MaxLimit `1`, zero origination numbers. Destination `the maintainer's verified test number` is VERIFIED but there is no number to send *from*. | Channel interface with two impls: SNS (real, verified number) and dashboard transcript (always works). Demo never depends on SMS. Origination provisioning is billable — pending user decision. |
| Conditional-edge API unexercised by any reference repo | Verify against installed 1.54.0 before building on it; fall back to a single decide-node returning a typed verdict |
| Reference repos pin 1.29–1.43; installed is 1.54.0 | Steering/skills/context surfaces moved most in that range — introspect, never trust docs |
| Scope creep across programs | Two programs, one state, one workflow. Locked. |
| Newly-created-work rule | No code reused from OpenClaw, RosettaCloud, astrolabe, TheAgentOrg, or AWS-Resource-Optimizer. Patterns reused as knowledge; disclosed in README. |

---

## 9. Judging alignment

- **Technical Implementation** — Graph + Swarm + agents-as-tools + steering + hooks + skills +
  memory + evals; AgentCore Runtime/Gateway/Memory/Identity/harness; live demo link.
- **Design** — complete product: dashboard, escalation queue, real notifications.
- **Potential Impact** — people lose coverage over paperwork, not eligibility. Specific,
  documented, and the solution addresses that exact failure.
- **Creativity** — escalation-boundary framing, advocate/verifier on different models,
  advisory-only reflection loop, capability-absence security. Outside the four examples the
  brief names.
- **Presentation** — the ledger makes autonomy legible: 9 handled alone, 1 escalated.

---

## 10. Open items

1. **SMS origination identity** — toll-free (~$2/mo, US→EG delivery uncertain) vs sender ID
   (often correct for Egypt) vs dashboard-transcript only. Billable; awaiting decision.
2. **`grace-dev` access keys** — user + scoped `GraceDevPolicy` created and attached. Keys
   deliberately *not* created; recommendation is to keep local work on existing credentials
   and let the deployed runtime use a scoped role.
3. **Real design-partner org** — synthetic now; if a clinic/food bank materializes before
   Sept 14, add as design partner (materially stronger Impact score).
