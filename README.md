# Grace

**Nobody should lose healthcare over a missed letter.**

Grace is an AI agent that watches every family's benefit-renewal deadline, files the renewals
that are unambiguous, chases the one missing document in the family's own language — and wakes
a human caseworker only when eligibility is genuinely in doubt.

Built with the [Strands Agents SDK](https://strandsagents.com) and Amazon Bedrock AgentCore
for the **AWS Agents for Humans Hackathon** (Good Neighbor track).

> **Status: deployed and running on AWS.** A scheduled sweep of 12 synthetic households reports
> **9 filed autonomously, 3 escalated to a human**, confirmed from DynamoDB rather than from a log
> line. See [deployed verification](docs/deployed-verification.md) for the evidence, including the
> three things that did not work as planned.

---

## The problem

During Medicaid unwinding, the majority of disenrollments were **procedural** — people who
still qualified but missed a letter, a deadline, or one document. They did not become
ineligible. They lost coverage to paperwork.

The people holding this together are caseworkers at clinics, food banks, and school districts,
tracking recertification windows for hundreds of households across programs with different
clocks, in languages the notices are not written in.

**One sentence:** a family that still qualifies is stuck re-proving it, over and over, and
loses coverage when a single letter goes unanswered.

## Who it's for

Small organisations that hold a community together — a community clinic, a food bank, a school
district's family-support office. They enrol a household once; Grace watches the clocks from
then on.

## Why an agent, not an app

The work is: watch a clock, notice a gap, chase one document, and know when a human must
decide. Nobody opens an app to do that. It has to run in the background and surface only at
the decision.

---

## What makes Grace different

Most agents are a model with tools and a prompt asking it to be careful. Grace has an
**escalation boundary** — it acts alone on the routine and *provably* escalates the rest,
enforced in code rather than requested in a prompt.

Three layers, strongest first:

**1. Capability absence.** Privileged tools are not registered in the agent's tool list at
all. `submit_renewal` does not appear in `list_tools_sync()` for a case that has not passed
verification. Grace cannot file a renewal it should not, because the capability does not exist
in that context. This beats any instruction — there is nothing to disobey.

**2. Identity from the session, never the conversation.** Every household-scoped tool takes
**no arguments**. The case is bound at construction from the authenticated session. A prompt
injection cannot redirect Grace to another family's record, because there is no parameter to
poison.

**3. A deterministic gate.** `grace/authority.py` is pure Python — no model, no I/O — mapping
case facts to `act` or `escalate`. It is wired into the agent loop as a Strands
`SteeringHandler` that returns `Proceed`, `Guide`, or `Interrupt` before any state-changing
call. Steering was chosen over prompt instructions on evidence: AWS's own 600-run evaluation
measured **100% adherence for steering vs 82.5% for prompt instructions**, at 66% fewer input
tokens than an SOP-style prompt.

**Fail closed.** Any error during verification escalates. Grace never guesses when the
consequence is a family losing coverage.

Grace may act alone only if *all* of these hold:

1. The renewal window is verified from a rule pack — never inferred by a model.
2. Every required document is present, current, and unexpired.
3. Income has not moved outside the band the rule pack calls immaterial.
4. Household composition is unchanged.
5. No two sources disagree.

Anything else becomes a specific question for a human.

---

## How it works

```text
EventBridge (daily sweep) → Step Functions → Lambda → AgentCore Runtime
                                                          │
                            Memory (per-household facts)   ┤
                            DynamoDB (case ledger)         ┤
                            Family channel (SMS/transcript)┘

Caseworker dashboard → API layer → invoke_agent_runtime
```

**[Full architecture diagram →](docs/architecture.md)** — the whole system, plus the three claims it
supports and the evidence for each. Rendered as
[`docs/architecture.png`](docs/architecture.png) as well.

The agent is a capability inside the system, not the backend. Step Functions owns retries and
workflow durability so the agent doesn't have to, and the dashboard never talks to the runtime
directly. AgentCore Gateway would sit alongside Memory here for rule packs and document
retrieval; it is [deferred](#what-shipped-and-what-did-not), and rule packs are read from
version-controlled YAML in the meantime.

### The agent

All three Strands multi-agent patterns, each where it is actually the right tool:

**Graph** — the deterministic spine.

```text
intake → documents → eligibility(Swarm) → decide ─┬─(gate passes)→ act
                                                  └─(else)───────→ escalate
```

Deadline math is a **tool, not an agent**. Deterministic work does not need a model.

**Swarm** — genuine deliberation, on ambiguous cases only. Three opposed roles:

| Agent | Job | Model |
|---|---|---|
| Advocate | Argues the family still qualifies | Nova 2 Lite |
| Verifier | Adversarially checks every claim against readable facts | Nova Pro |
| Referee | Decides whether it is genuinely ambiguous, or concludes | Nova Micro |

All three run **different** models. Two instances of the same model agreeing proves nothing, and
nothing should referee its own argument.

**Agents-as-tools** — context isolation for the outreach drafter, policy retriever, and
caseworker briefer, so translation and search chatter never pollute the eligibility
reasoning.

### The ledger

Hooks append every node transition and tool call to a per-case ledger. In a benefits context
an audit trail is a requirement, not a feature — and it is also how you can see the autonomy
claim is real: nine cases handled alone, three escalated, each with a reason.

Trajectory evals read the ledger rather than the model transcript, because a transcript-based
eval would miss a tool that ran but was not logged.

### Learning, advisory only

An outcome-reflection loop — Grace writing a short lesson when a case closes and feeding recent
lessons into future deliberations — is **designed and deliberately not built**. Lessons could only
ever make Grace *more* cautious; they can never satisfy a gate condition. It is deferred because it
cannot be built honestly before a deployed sweep exists to reflect on, and that sweep only started
running at the end of this plan. See [out of scope](#what-shipped-and-what-did-not).

---

## Models

Amazon Nova throughout — no third-party LLMs in the request path.

| Role | Model |
|---|---|
| Advocate | `global.amazon.nova-2-lite-v1:0` |
| Verifier, briefer | `us.amazon.nova-pro-v1:0` |
| Referee | `us.amazon.nova-micro-v1:0` |
| Document classifier | `global.amazon.nova-2-lite-v1:0` |
| Outreach drafter, steering judge | `us.amazon.nova-2-lite-v1:0` |

The three deliberation roles run three *different* models on purpose — two instances of one
model agreeing proves nothing, and nothing should referee its own argument. Nova Pro is the
strongest available: `nova-premier-v1:0` is Legacy and `Converse` returns
`ResourceNotFoundException` for it, and there is no `nova-2-pro`.

One measured result shaped this design. Told *"never submit a renewal when a required document
is missing"*, `nova-lite-v1:0` read the case, saw the document was missing, and filed the
renewal anyway — then said *"I made the same mistake again."* Other Nova models escalated
correctly on the identical prompt, but that is the point: Grace does not rely on a model
choosing to obey. `submit_renewal` is not registered as a capability for a case that has not
passed verification, so there is nothing to disobey.

---

## Deployed on AWS

Grace runs on a schedule in `us-east-1`. A daily EventBridge rule starts a Step Functions sweep that
fans out over households (Map, `maxConcurrency` 3), invokes a Lambda per case, and each Lambda invokes
Grace on AgentCore Runtime.

```text
EventBridge (grace-daily-sweep)
  └→ Step Functions (grace-sweep)
       └→ Lambda (grace-invoke-case)
            └→ AgentCore Runtime (grace_grace-oTyyvo8stE)  →  Bedrock Nova
                 ├→ DynamoDB grace-cases       (ledger + escalation queue)
                 └→ AgentCore Memory           (per-household facts)
```

**A real execution reports 9 acted / 3 escalated in 61 seconds**, and the counts are confirmed from
DynamoDB rather than from the agent's own log line: `renewal_submitted` exists for exactly `c-001`
through `c-009` and for none of the three escalating households. The `escalation-queue` GSI holds
exactly `c-010`, `c-011`, and `c-012`, each with the gate's typed reason. Full output, including two
negative results, is in [docs/deployed-verification.md](docs/deployed-verification.md).

### Three AgentCore surfaces, not five

| Surface | State |
|---|---|
| **Runtime** | Shipped. Container on ARM64, IAM auth, deployed via the `agentcore` CLI and CDK. |
| **Memory** | Shipped. `grace_household_memory`, 365-day expiry, per-household actor scoping. |
| **The deploy harness** | Shipped. `infra/provision_all.py` creates every resource idempotently; a guarded teardown exists. |
| **Gateway** | **Deferred.** The largest remaining chunk and the most common deploy-day failure — outbound auth differs per target type. The `target___tool` prefix bug stays fixed and tested in `grace/steering.py` regardless, so re-adding Gateway later cannot silently bypass the gate. |
| **Identity / Cognito JWT** | **Deferred.** No caseworker IdP exists. A JWT authorizer whose claims gate nothing is worse than honest IAM auth. The findings that make it safe — an explicit `Deny` on `GetWorkloadAccessTokenForUserId`, an opaque `sub` — are already enforced in the runtime role. |

Two surfaces are deliberately absent and named as such. Claiming five would be the one thing that
turns a working entry into a dishonest one.

### Observability: the alarm is on escalation count, not error rate

`grace-escalations-below-expected` fires when the sweep escalates **fewer than 3** cases. That is the
interesting direction. Grace acting when it should have escalated produces no error, no throttle, and
no latency spike — it looks exactly like success, and an error-rate alarm would stay green through the
only failure that actually costs a family their coverage. Missing data is treated as breaching, because
a sweep that never ran is a failure rather than an absence of news.

The alarm is proven on real data: a sweep published `Sum=3.0` to `Grace/EscalatedCases` and the alarm
resolved to `OK` on that datapoint.

**What does not work: CloudWatch traces.** Every ledger row carries a `trace_id` field, but its value
is `NULL` in the deployed runtime and **zero traces exist in the account**. AgentCore Runtime injects
the OTEL environment variables and creates a log group, but does not install an in-process tracer
provider — and the packages that would fill that gap are ones this project deliberately refuses,
because they would trade a verified safety property for a nicer screenshot. So a Transaction Search
query on `grace.gate_decision = "escalate"` returns nothing. The DynamoDB escalation queue is the
evidence instead, which is the stronger artifact anyway: a trace can be dropped by sampling, a ledger
row cannot. The reasoning is recorded in full in
[docs/deployed-verification.md](docs/deployed-verification.md#4-transaction-search-returns-nothing-and-why).

---

## Running it

Requires Python 3.12+, AWS credentials, and Bedrock Nova access in `us-east-1`.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

.venv/bin/python -m pytest                            # tests
.venv/bin/python -m grace.run sweep --auto escalate   # local sweep
```

The sweep runs 12 synthetic households. Nine are filed autonomously; three escalate —
one missing a document, one with a material income change, one with conflicting sources.

Two suites, deliberately separate. `pytest` runs the fast unit suite — **622 tests**, no network.
`pytest evals/` runs trajectory evals against **real Bedrock** — about 65 model invocations across five
graph runs — and asserts the gate's ordering holds on real model behaviour: `read_case`, `check_window`,
and `list_documents` always precede any action. They are excluded from the default run because they cost
money and take minutes, not because they are optional.

The evals pass 23/23, and honestly: that took two runs. One assertion — that an escalating case does
*something* rather than nothing — is liveness, not safety, because the gate only ever permits or refuses
a tool call and never forces one. A model that deliberates and then answers in prose fails it while
behaving correctly. No safety eval has failed, including the one that blocks submission if it does. The
[verification doc](docs/deployed-verification.md#7-trajectory-evals-against-real-bedrock) records both
runs rather than only the good one.

To deploy the whole stack into a fresh account:

```bash
.venv/bin/python -m infra.provision_all      # idempotent; verified across three runs
```

### Data

**All household data in this repository is synthetic.** Names are obviously fictional and
phone numbers use the reserved `+1555` range; a test asserts both. No real personal, health,
or financial data is used anywhere.

Household identity is also kept away from the models: `read_case` returns no name and no phone
number. That is a fix rather than a precaution — it used to return the household's name, a referee
quoted it into its deliberation, and the text reached a CloudWatch log group inside a Step Functions
payload, a path that span redaction does not cover. The name is removed at the source, with a
regression test over all 12 fixtures, because a model that can read a name will eventually quote it
somewhere nobody is filtering. **The fix is deployed** — runtime version 2, and re-invoking the exact
case that leaked now returns an escalation with no name in the payload, confirmed across a full 9/3
sweep with zero household names anywhere in the output.

Fixing the source did not clean up what was already written, so that was checked separately: a scan of
all 633 rows in the DynamoDB table found the surname in three fields of two pre-fix rows, and those
values were stripped in place without touching any key, status, deadline, or `renewal_submitted` row.
The scan now returns clean. Log events written before the fix still contain the name and cannot be
unwritten; that, and the exact scope of the cleanup, are recorded in
[docs/deployed-verification.md](docs/deployed-verification.md#5-a-household-name-reached-cloudwatch--found-fixed-at-the-source-pre-fix-events-remain)
rather than quietly smoothed over.

### Notifications

The family channel sits behind an interface with two implementations: real SMS via AWS End
User Messaging, and a transcript view. **The AWS account's SMS is sandboxed** —
`TEXT_MESSAGE_MONTHLY_SPEND_LIMIT` has `MaxLimit: 1` (about $1/month) and there are **zero
origination numbers** — so `TranscriptChannel` is the deliberate always-works path and **the demo
never depends on SMS delivery**. The interface is the point: the gate decides whether a family may be
contacted, and swapping the transport does not touch that decision.

---

## What shipped, and what did not

Recorded as decisions rather than omissions.

| Deferred | Why |
|---|---|
| **AgentCore Gateway** | Largest remaining chunk; outbound auth shape differs per target type, which is the most common deploy-day failure. The gate's `target___tool` prefix handling stays tested regardless. |
| **AgentCore Identity / Cognito JWT** | No caseworker IdP exists. A JWT authorizer whose claims gate nothing is worse than honest IAM auth. |
| **Real SMS** | Account is sandboxed: `MaxLimit: 1`, zero origination numbers, and sender-ID registration in the maintainer's country requires a letter of authorization, company registration, and a tax card. |
| **Reflection loop** | Genuinely the originality differentiator, and genuinely additive. It cannot be built before a deployed sweep exists to reflect on. |
| **Skills / progressive disclosure** | A prompt-size optimization. Grace's prompts are not the bottleneck. |
| **Bedrock Guardrails** | Span redaction already covers the export path that matters, and every household is synthetic, so PII anonymization would protect nothing today. |
| **Caseworker dashboard** | Next up. The escalation queue it reads is already live and populated. |

---

## Repository layout

```text
grace/
├── models.py         # Nova model IDs — single source of truth
├── rules/            # rule packs (YAML) + deadline math, pure functions
├── cases/            # case types, in-memory store, DynamoDB store
├── authority.py      # THE GATE — pure logic, no model, no I/O
├── steering.py       # AuthorityGate(SteeringHandler) — the only adapter
├── ledger.py         # per-case audit trail
├── tools/            # read tools (free) and action tools (gated)
├── swarm.py          # three-agent deliberation
├── graph.py          # the spine
├── memory.py         # AgentCore Memory session manager
├── observability.py  # telemetry setup + span-redaction guard
├── entrypoint.py     # what AgentCore Runtime invokes
└── run.py            # local sweep CLI
infra/                # provisioning: DynamoDB, IAM, Lambda, Step Functions, alarm
runtime_app.py        # BedrockAgentCoreApp wrapper, refuses to start unredacted
evals/                # trajectory evals proving gate ordering holds
docs/                 # design specs, plans, deploy runbook, deployed verification
```

---

## Prior work and disclosure

Grace was newly created during the hackathon submission period. No code was copied from any
earlier project.

Design patterns and hard-won API knowledge were carried over from the author's previous
work — a gated read/action tool split and an outcome-reflection loop from a trading system,
a non-AI deterministic gatekeeper from a CI/CD pipeline, and AgentCore Runtime/Memory/Gateway
wiring from a learning platform. Those informed Grace's design; none of their code is in this
repository.

The Strands Agents SDK, Amazon Bedrock, and the AWS SDKs are used as third-party
dependencies under their own licenses.

---

## License

MIT — see [LICENSE](LICENSE).
