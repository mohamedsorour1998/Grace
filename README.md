# Grace

**Nobody should lose healthcare over a missed letter.**

Grace is an AI agent that watches every family's benefit-renewal deadline, files the renewals
that are unambiguous, chases the one missing document in the family's own language — and wakes
a human caseworker only when eligibility is genuinely in doubt.

Built with the [Strands Agents SDK](https://strandsagents.com) and Amazon Bedrock AgentCore
for the **AWS Agents for Humans Hackathon** (Good Neighbor track).

> **Status: in development.** Deadline 2026-09-14. This README describes what Grace is and
> how it works; sections marked _(Plan 2)_ or _(Plan 3)_ are not built yet.

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
                            Gateway (rule packs, documents) ┤
                            Memory (per-household facts)    ┤
                            DynamoDB (case ledger)          ┤
                            SMS channel                     ┘

Caseworker dashboard → API layer → invoke_agent_runtime
```

The agent is a capability inside the system, not the backend. Step Functions owns retries and
workflow durability so the agent doesn't have to, and the dashboard never talks to the runtime
directly.

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
| Advocate | Argues the family still qualifies | Nova Pro |
| Verifier | Adversarially checks every claim against readable facts | Nova **Premier** |
| Referee | Decides whether it is genuinely ambiguous, or concludes | Nova Pro |

The verifier deliberately runs a **different model** than the advocate. Two instances of the
same model agreeing proves nothing.

**Agents-as-tools** — context isolation for the outreach drafter, policy retriever, and
caseworker briefer, so translation and search chatter never pollute the eligibility
reasoning. _(Plan 2)_

### The ledger

Hooks append every node transition and tool call to a per-case ledger. In a benefits context
an audit trail is a requirement, not a feature — and it is also how you can see the autonomy
claim is real: nine cases handled alone, three escalated, each with a reason.

Trajectory evals read the ledger rather than the model transcript, because a transcript-based
eval would miss a tool that ran but was not logged.

### Learning, advisory only

When a case closes, Grace writes a short reflection on the decision and feeds recent lessons
into future eligibility deliberations. Lessons can only ever make Grace **more** cautious;
they can never satisfy a gate condition. _(Plan 2)_

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
model agreeing proves nothing, and nothing should referee its own argument.

One measured result shaped this design. Told *"never submit a renewal when a required document
is missing"*, `nova-lite-v1:0` read the case, saw the document was missing, and filed the
renewal anyway — then said *"I made the same mistake again."* Other Nova models escalated
correctly on the identical prompt, but that is the point: Grace does not rely on a model
choosing to obey. `submit_renewal` is not registered as a capability for a case that has not
passed verification, so there is nothing to disobey.

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

### Data

**All household data in this repository is synthetic.** Names are obviously fictional and
phone numbers use the reserved `+1555` range; a test asserts both. No real personal, health,
or financial data is used anywhere.

### Notifications

The family channel sits behind an interface with two implementations: real SMS via AWS End
User Messaging, and a transcript view. AWS SMS sandbox limits mean the transcript is the
always-works path, and the demo does not depend on SMS delivery.

---

## Repository layout

```text
grace/
├── models.py         # Nova model IDs — single source of truth
├── rules/            # rule packs (YAML) + deadline math, pure functions
├── cases/            # case types and store
├── authority.py      # THE GATE — pure logic, no model, no I/O
├── steering.py       # AuthorityGate(SteeringHandler) — the only adapter
├── ledger.py         # per-case audit trail
├── tools/            # read tools (free) and action tools (gated)
├── swarm.py          # three-agent deliberation
├── graph.py          # the spine
└── run.py            # local sweep CLI
evals/                # trajectory evals proving gate ordering holds
docs/superpowers/     # design spec and implementation plans
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
