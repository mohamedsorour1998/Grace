# Devpost text description

Paste the block below into the submission form's description field, verbatim. It is written to be
read on its own — the rules say judges may choose to judge based solely on the text description,
images, and video.

Every claim in it is one the repository can back. If anything changes before submission, change it
here too: a description that outruns the code is the one thing that turns a working entry into a
dishonest one.

---

When pandemic-era Medicaid protections ended in 2023, more than 25 million Americans lost coverage —
and about 69% of them lost it to paperwork rather than eligibility. Roughly 17 million people who
still qualified lost health insurance because a letter went unanswered or one document never arrived.
Federal law gives them 90 days to fix it (42 CFR 435.916(a)(3)(iii)). Almost nobody knows that.

**Grace** is an AI agent, built with the **Strands Agents SDK** and deployed on **Amazon Bedrock
AgentCore**, that works inside that window. It watches every household's renewal deadline against the
encoded rules for its program, files the renewals that are unambiguous, texts the family in their own
language for the one missing document, and wakes a human caseworker only when eligibility is genuinely
in doubt.

**Who it's for.** Caseworkers at community clinics, food banks, and school family-support offices,
tracking recertification windows for hundreds of households across programs with different clocks.
They enrol a household once; Grace watches the clocks from then on. Nobody opens an app to do this
work, so it runs in the background and surfaces only at the decision.

**How it works.** A daily EventBridge schedule starts a Step Functions sweep that invokes Grace on
AgentCore Runtime once per household. Inside, a Strands **Graph** runs intake and document checks and
routes only ambiguous cases to a three-agent **Swarm** — an advocate, an adversarial verifier, and a
referee on three *different* Amazon Nova models, because two instances of one model agreeing proves
nothing and nothing should referee its own argument. **Agents-as-tools** drafts the family's message in
a nested agent with no tools and no access to the case, so translation never enters the eligibility
reasoning. A hook appends every tool call to a DynamoDB ledger — the audit trail and the evidence.

The gate is the point. `authority.py` is pure Python, no model and no I/O, wired in as a Strands
steering handler that runs before every state-changing call. Three layers: privileged tools are
**absent** from the tool list for a case that has not passed verification, so there is nothing to
disobey; every household-scoped tool takes **zero arguments**, so a prompt injection has no parameter
to poison; and any error during verification escalates.

Caseworkers sign in through Cognito to a dashboard: the sweep at a glance, the escalation queue, one
household's audit trail, and an approve/deny control. Approving bypasses nothing — it records the
decision and re-invokes Grace so the gate re-evaluates the facts. Executed live on the household
missing a document: a human said yes and Grace filed nothing.

**What's real.** It has run unattended every day since deploy. Across twelve seeded synthetic
households it handles nine alone and escalates three, each with a typed reason — confirmed by reading
DynamoDB, not a log line. Every rule-pack number names its authority, saying `policy choice` where the
regulation gives a range rather than a value. No household identity exists anywhere a model or a log
can reach, and the intake form refuses to collect any.

**What it does not do**, stated because a project that hides this is less trustworthy. SMS is
sandboxed here, so the family channel writes a transcript. Filing writes a ledger row: a real state
endpoint needs a data-sharing agreement, not code. Memory records every outcome — verified by reading
the events back — while retrieval is wired and not yet verified. CloudWatch trace correlation does not
work, and the repository explains why.

Live demo: **grace.rosettacloud.app** · Code: **github.com/mohamedsorour1998/Grace** (MIT)
