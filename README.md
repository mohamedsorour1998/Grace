# Grace

**Nobody should lose healthcare over a missed letter.**

During the Medicaid unwinding, **more than 25 million Americans lost coverage — and about 69% of those
losses were procedural**, not eligibility. Roughly **17 million Americans** lost health insurance they
still qualified for, because of paperwork.

Grace is an AI agent that watches every family's benefit-renewal deadline, files the renewals
that are unambiguous, chases the one missing document in the family's own language — and wakes
a human caseworker only when eligibility is genuinely in doubt.

Built with the [Strands Agents SDK](https://strandsagents.com) and Amazon Bedrock AgentCore
for the **AWS Agents for Humans Hackathon** (Good Neighbor track).

> **Status: deployed and running on AWS.** A scheduled sweep of the twelve seeded synthetic households reports
> **9 filed autonomously, 3 escalated to a human**, confirmed from DynamoDB rather than from a log
> line. A caseworker dashboard at **[grace.rosettacloud.app](https://grace.rosettacloud.app)** shows the
> queue and lets a human decide — and approving the household with a missing document still files
> nothing. See [deployed verification](docs/deployed-verification.md) and
> [dashboard verification](docs/dashboard-verification.md) for the evidence, including the parts that
> did not work. **The demo video is not yet recorded**; see
> [still outstanding](#still-outstanding-for-submission).

---

## The problem

When pandemic-era continuous coverage ended on **31 March 2023**, states resumed annual Medicaid
eligibility checks. Enrolment had reached a record **94 million**. Over the unwinding that followed,
**more than 25 million Americans lost Medicaid coverage.**

**About 69% of those losses were procedural** — missing forms, missed deadlines, one document that never
arrived. Not people who stopped qualifying. People who still qualified and lost coverage anyway.

> That is roughly **17 million Americans** who lost health insurance they were entitled to, because of
> paperwork.

The failure is silent by design. Nobody gets an error message. A renewal simply does not happen, and a
family finds out at a pharmacy counter when a prescription is refused.

The people holding this together are caseworkers at clinics, food banks, and school districts,
tracking recertification windows for hundreds of households across programs with different
clocks, in languages the notices are not written in.

**One sentence:** a family that still qualifies is stuck re-proving it, over and over, and
loses coverage when a single letter goes unanswered.

<sub>Sources: [Medicaid.gov unwinding resources](https://www.medicaid.gov/resources-for-states/coronavirus-disease-2019-covid-19/archived-unwinding-and-returning-regular-operations-after-covid-19)
and [KFF](https://www.kff.org/medicaid/10-things-to-know-about-the-unwinding-of-the-medicaid-continuous-enrollment-provision/)
for enrolment and coverage losses; [JAMA Health Forum](https://jamanetwork.com/journals/jama-health-forum/fullarticle/2825467)
for the procedural share. All household data in this repository is synthetic.</sub>


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

Plenty of software can file a form. What Grace does that a form-filler cannot is
**refuse** — and prove where the refusal comes from.

It has run unattended on AWS every day since deploy, handling nine of twelve households
alone and escalating three with typed reasons a caseworker can act on. The refusal is
enforced in code rather than requested in a prompt, and every claim in this README is
checkable against the ledger, the tests, or a live URL.

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

**Every number in a rule pack names its authority, and only two of them are mandated.**
`certification_period_months` for Medicaid is **42 CFR 435.916(a)(1)** — *"must be renewed
once every 12 months, and no more frequently than once every 12 months"* — and
`grace_period_days_after_end` is **42 CFR 435.916(a)(3)(iii)**, which requires the agency to
reconsider a household terminated for failing to return the form if it arrives *"within 90
days after the date of termination … without requiring a new application"*.

**That second provision is why Grace exists.** It is the window in which a procedural
termination is still reversible, and Grace's whole job is to act inside it.

The other ten parameters — the 60-day window, the immaterial-income bands, every document
freshness limit — carry `authority: "policy choice"` and a note saying what the regulation
does and does not establish. SNAP's is the sharpest example: 7 CFR 273.12(a)(1)(i)(A) sets a
**dollar** threshold, *"a change of more than $100 in the amount of unearned income"*, so a
percentage band has no federal basis at all and the pack says so rather than implying one.
An uncited number that decides whether a family keeps coverage is indistinguishable from an
invented one, and `load_pack` refuses a malformed `sources` block with the same
`InvalidRulePack` it raises for every other malformed field.

---

## How it works

```text
EventBridge (daily sweep) → Step Functions → Lambda → AgentCore Runtime
                                                          │
                            Memory (per-household facts)   ┤
                            DynamoDB (case ledger)         ┤
                            Family channel (SMS/transcript)┘

Caseworker → Cognito → Amplify SSR dashboard → DynamoDB (reads)
                                             └→ invoke_agent_runtime (a decision)
```

The dashboard **never** talks to the runtime from the browser: a decision is recorded server-side, then
the agent is re-invoked so the gate re-evaluates.

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

**Agents-as-tools** — context isolation for the outreach drafter. When a document is
missing, `decide` calls `draft_family_message`, which runs its own agent on Nova 2 Lite
with **no tools at all** and returns the message text. It is bound to nothing — no store,
no case id — so its arguments are *content* rather than identity: which document, by when,
in which language. Translation chatter never enters the eligibility reasoning's context,
and the drafter cannot send anything, because `send_family_message` stays on `decide`
behind the gate. On the deployed system it writes to `c-010` in Spanish.

### The ledger

Hooks append every node transition and tool call to a per-case ledger. In a benefits context
an audit trail is a requirement, not a feature — and it is also how you can see the autonomy
claim is real: nine cases handled alone, three escalated, each with a reason.

Trajectory evals read the ledger rather than the model transcript, because a transcript-based
eval would miss a tool that ran but was not logged.

### Learning, advisory only

An outcome-reflection loop — Grace writing a short lesson when a case closes, and feeding those
lessons into future deliberations — **is built**, and it was deferred until now for a reason worth
stating: it cannot be built honestly before a deployed sweep exists to reflect on. Eight sweeps
now exist, so it reflects on real outcomes rather than on invented ones.

Every terminal path records what the run concluded to AgentCore Memory, and the next cycle reads
those facts back. **Where they land is the whole design.** A lesson reaches two places, both
advisory: the graph's opening task text, and the **advocate's** prompt inside the deliberation
swarm — the agent whose job is to argue, for whom history is legitimate material. It reaches
neither the verifier, whose job is checking claims against readable facts, nor the referee, whose
job is concluding: a remembered claim is not a readable fact, and a conclusion drawn from last
year's case is not a conclusion about this one.

The gate never sees a lesson at all. `evaluate()` re-runs on the case record and **has no
parameter a lesson could occupy** — asserted against the function's signature rather than trusted
from the wiring, so a mistaken edit is a type error instead of a quietly looser verdict. A lesson
can make Grace more cautious. It cannot make it less.

---

## Models

Amazon Nova throughout — no third-party LLMs in the request path.

| Role | Model |
|---|---|
| Advocate | `global.amazon.nova-2-lite-v1:0` |
| Verifier, briefer | `us.amazon.nova-pro-v1:0` |
| Referee | `us.amazon.nova-micro-v1:0` |
| Document classifier | `global.amazon.nova-2-lite-v1:0` |
| Outreach drafter, steering judge | `global.amazon.nova-2-lite-v1:0` |

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

### Four AgentCore surfaces, not five

| Surface | State |
|---|---|
| **Runtime** | Shipped. Container on ARM64, IAM auth, deployed via the `agentcore` CLI and CDK. |
| **Memory** | Shipped and **in use**, with one half verified and one wired. `grace_household_memory`, 365-day expiry, one actor per household. Every sweep records what it concluded — **confirmed by listing the events back, not by the write returning**: two per household, carrying the typed reason and nothing that identifies anyone. Retrieval into `/facts/` is asynchronous and had not surfaced records at the time of writing, so `recall_facts` returns `()` and the read path is wired but unverified. It fails open by design, so an empty recall degrades an outreach message and can never change a verdict. |
| **Identity** | Shipped, and **narrowly**: a Cognito user pool (`grace-caseworkers` / `us-east-1_HXs3b0APR`) whose ID token is the dashboard's trust anchor. See the sentence below for what that does and does not mean. |
| **The deploy harness** | Shipped. `infra/provision_all.py` creates every resource idempotently; a guarded teardown exists. |
| **Gateway** | **Deferred.** The largest remaining chunk and the most common deploy-day failure — outbound auth differs per target type. The `target___tool` prefix bug stays fixed and tested in `grace/steering.py` regardless, so re-adding Gateway later cannot silently bypass the gate. |

One surface is deliberately absent and named as such. Claiming five would be the one thing that
turns a working entry into a dishonest one.

**What "Identity" means here, precisely, because two different things share the name.** What shipped
is a **Cognito user pool whose ID token is the dashboard's trust anchor**: `verifySession` checks the
signature against the pool's published JWKS, the issuer, the audience, the expiry, `token_use: "id"`,
and `custom:role === "caseworker"` — and a failure of any one of those produces `null` rather than a
lesser session. What did **not** ship is an **AgentCore Gateway JWT authorizer**
(`customJWTAuthorizer` with inbound claim rules); the runtime is still IAM-authorised. Both are
honest; conflating them is not. The findings that make the JWT path safe — an explicit `Deny` on
`GetWorkloadAccessTokenForUserId`, and a `sub` that is an opaque UUID rather than a name or an email —
remain enforced in the runtime role either way.

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

## The caseworker dashboard

**[grace.rosettacloud.app](https://grace.rosettacloud.app)** — Next.js on Amplify SSR
(`WEB_COMPUTE`), gated on Cognito. Accounts are **admin-created only**
(`AllowAdminCreateUserOnly: true`), so nobody on the internet can register and reach the decide
endpoint.

Three pages, all server-rendered: the sweep at a glance, the escalation queue, and one household's
full audit trail with an approve/deny control. Verified live with a real Cognito ID token — `/`
renders the twelve seeded households and the headline **"9 handled alone, 3 waiting on you"**, `/queue` shows exactly
`c-010 c-011 c-012`, and `/case/c-010` shows the gate's own reason,
`missing_document: proof_of_residency`.

The sign-in page is Cognito's classic hosted UI, styled with the dashboard's own palette
(`infra/provision_cognito.py`'s `HOSTED_UI_CSS`, values taken from `web/app/globals.css`) so that
signing in does not look like a different product than the app it guards. A test pins the two together,
because a colour changed in one place and not the other is invisible until someone views both pages.

### How a household gets into Grace

**A caseworker can add one at [`/new`](https://grace.rosettacloud.app/new).** Case id, program, state,
certification end date, income, household size, and which documents have been sent to the state. Grace
picks it up on its next sweep, reads the rules for that program, and either files the renewal or
escalates it with a reason.

**The form collects no household identity — no name, no phone number, no address — and that absence is
the design.** An intake form is exactly where identity feels natural to collect, and `read_case`
returning `display_name` is precisely how a household surname reached CloudWatch (see
[what did not work](#what-shipped-and-what-did-not)). Grace does not need to know who a family is in
order to know whether their paperwork is complete; a real deployment's identity stays in the system of
record that referred the household, keyed by case id. `web/lib/intake.ts` refuses any identity-shaped
field **and** any field it does not recognise — an allowlist, so the guard cannot be walked around with
a field name nobody anticipated.

**The outreach is in the family's language, and the deployed system shows it.** `c-010` —
the household missing `proof_of_residency` — reads Spanish, so the message in its audit trail
is Spanish, written by the drafter and sent unchanged. That is the one place this README's
second sentence is checkable rather than asserted, and `tests/test_demo_dates.py` holds the
property that the household Grace texts is not an English speaker, so a fixture edit cannot
quietly make the claim undemonstrable again.

**Grace stores no documents, and there is no upload.** A document in Grace is two facts — which kind
it is, and the date it was sent:

```yaml
- {id: "proof_of_income", received: "2026-09-20"}
```

That is the entire record. There is no S3 bucket and no file anywhere in the system. Grace reasons
about the *clock* on a document — has it been sent, is it still inside the rule pack's freshness
window, has it expired — and never about its contents.

**The family sends documents to the state, and the state's eligibility system is the system of
record.** That sequence is the domain fact the whole design rests on: the state attempts an *ex parte*
renewal from wage and tax data it already holds; if that fails it mails a renewal form; if it still
cannot verify something it requests specific documents; the family submits them to the **state** by
portal, mail, or in person; a state worker decides. **The 69% procedural loss happens between the
notice and the submission** — the letter never arrives, or the document never gets sent.

Grace is not any of those parties. Its users are **navigators** — community clinics, food banks, school
family-support offices — and what a navigator actually knows is *"I helped this family upload their
paystub on the 20th"*. That is **status, not custody**, and it is exactly what a `Document` records.
The interface used to say "documents on file", which implied Grace or the org held the paperwork.
Neither does. It now says **documents sent to the state**, and each entry reads *sent 2026-09-20*.

**So the claim is an assertion, and the dashboard says whose.** Every `RECORD#v1` row carries a
`created_by` — the caseworker's opaque Cognito `sub`, never a name or an email; both writers *refuse* a
subject that is not opaque rather than stripping one — and `/case/[id]` renders one line above the
documents:

> Document status asserted by `2448a4e8-…` at intake on 2026-09-06. Grace tracks the deadline on it
> and does not verify it independently.

Hard rule 6 says never claim an action succeeded without tool confirmation. That sentence is its
mirror: a claim Grace never confirmed and could not, labelled as one, for the person who is about to
act on it.

**The integration point is real code, not a promise.** `grace/cases/document_source.py` defines a
`DocumentSource` protocol whose distinguishing method is `provenance()` — every source must be able to
say *how it knows*. `AssertedDocumentSource` is what ships and returns the record's own documents.
`StateEligibilityDocumentSource` **raises `NotImplementedError`** and its docstring states what a real
one needs: a per-state data-sharing agreement, credentials issued under it, and a query for whether a
household has a current verification on file — a status and a date, never the file. It is deliberately
not wired in. A stub returning an empty tuple would not read as "unknown": to `evaluate`, `()` is the
positive claim that the family has sent nothing, so all twelve households would escalate for reasons
that are not true with no error anywhere. This is what **AgentCore Gateway** was deferred for — an
outbound call to a system Grace does not own.

**Two things deliberately not built, with the reasons.** *Document storage:* a proof of income carries
a name, an address, an employer, and often an SSN — the maximum-PII payload, in a system whose entire
architecture is "no household identity anywhere". It also buys the gate nothing, because
`document_problems` reads two dates and never opens a document. *Ex parte renewal modelling:* it is
the lever that actually prevents the 69%, and it is something the **state** does from data Grace cannot
get. Building it would mean simulating a capability Grace does not have.

Case records live in DynamoDB alongside the ledger, under a `RECORD#v1` sort key. That matters more
than it sounds: before Plan 4 the records were seeded from `fixtures/households.yaml` into the
container image at build time, so a form writing to DynamoDB would have rendered on the dashboard and
been **invisible to the agent**. A submitted case is only real if the sweep can see it.

**That took two fixes, and the second one is the interesting one.** Reading records was a store change,
shipped in the repository on 2026-09-07 — and the deployed container was still the image built on
2026-09-03, so for three days the property was true of the code and false of the running system. Fixed
by a redeploy (runtime version 3). But the sweep *still* could not have seen a new household, because
the EventBridge schedule carried a **hardcoded list of twelve case ids**: the caseload was frozen at the
moment the provisioning script last ran. The state machine now starts with a `ListCases` query over the
table's case-directory partition, and the scheduled event carries only `{"today": "2026-10-01"}`.

Neither defect ever failed. The schedule stayed green, every execution reported `SUCCEEDED`, and the
9/3 count stayed correct — which is precisely what made it invisible. It was found by asking a question
no dashboard answers: *is the thing I deployed the thing I wrote?*

Verified on the deployed system rather than argued. A household submitted through the form, existing
only as a row the form wrote and **absent from `fixtures/households.yaml`**, was invoked directly on the
runtime and escalated naming both of its missing documents; then a sweep started with the schedule's own
input returned **13 outcomes, 9 acted / 4 escalated**. Its rows were then removed and the same input
returned **12 outcomes, 9 acted / 3 escalated**, with `renewal_submitted` still present for exactly
`c-001`–`c-009` and the escalation queue still holding exactly `c-010`/`c-011`/`c-012`.

One guard worth naming: a DynamoDB Query caps at 1 MB, so `CheckDirectoryComplete` **fails the
execution** if the response carries `LastEvaluatedKey`. Sweeping a truncated directory would drop
households while every count still added up — the same silent shortfall the store's own reader refuses,
expressed at the orchestration layer.

A newly submitted case shows the status **`new`** rather than `error`. That distinction is deliberate:
`error` reads "Grace's last run on this case reached no outcome — re-run the sweep", which would be a
false claim about a run that never happened. `lib/cases.ts` reads the `RECORD#v1` row and reports `new`
only for a case with a record, no ledger rows, and no escalation — a sweep that ran and concluded
nothing leaves ledger rows behind and is still an `error`.

**`submit_renewal` does not submit anything to a state system, and nothing here claims it does.** It
writes a ledger row recording that Grace decided to file, and the escalation boundary — which is what
this project is about — is enforced entirely before that point. Wiring it to a real filing endpoint
needs the same per-state data-sharing agreement `StateEligibilityDocumentSource` documents, which is a
legal instrument rather than code.

Grace files a renewal **once per certification period**. The gate itself is pure and cannot read the
ledger, so the check lives in `submit_renewal`: it looks for an existing `renewal_submitted` row
carrying the same `cert_end` and, finding one, records `renewal_already_filed` instead of filing
again. It still writes a row, deliberately — Grace's classification counts a filing only within the
run that made it, so a silent short-circuit would make the next day's sweep look like a run that did
nothing and escalate a household that is perfectly fine. If the ledger cannot be read it files
anyway: the gate has already cleared the case, and a renewal that silently never happens is worse
than a duplicate row.

You can still add households by editing `fixtures/households.yaml` and re-running the sweep; the
twelve seeded ones arrive that way, and `infra/seed_cases.py` writes them into the table — which it
now has, so the case directory names all twelve and the sweep no longer depends on the fixture list
being compiled into the image. The seeder never overwrites: `create_case` puts each record under
`attribute_not_exists(sk)`, and `--verify` reads every row back and compares it to the fixture field by
field rather than trusting that the write returned.

## What shipped, and what did not

Recorded as decisions rather than omissions.

| Deferred | Why |
|---|---|
| **AgentCore Gateway** | Largest remaining chunk; outbound auth shape differs per target type, which is the most common deploy-day failure. The gate's `target___tool` prefix handling stays tested regardless. |
| **An AgentCore Gateway JWT authorizer** | Distinct from the Cognito pool that *did* ship. The runtime stays IAM-authorised; a `customJWTAuthorizer` belongs with Gateway, above. |
| **Real SMS** | Account is sandboxed: `MaxLimit: 1`, zero origination numbers, and sender-ID registration in the maintainer's country requires a letter of authorization, company registration, and a tax card. |

| **Skills / progressive disclosure** | A prompt-size optimization. Grace's prompts are not the bottleneck. |
| **`strands-agents-evals`** | The trajectory evals are ordinary pytest functions in `evals/` that read the ledger — the ground truth for what executed, which a transcript-based eval would miss. The package was not adopted because it depends on `strands-agents-tools`: 25 packages including `slack-bolt` and `pillow` that Grace never imports. |
| **Bedrock Guardrails** | Span redaction already covers the export path that matters, and every household is synthetic, so PII anonymization would protect nothing today. |
| **CloudWatch trace correlation** | Every ledger row carries a `trace_id` key whose value is `NULL`; Runtime injects the OTEL variables without installing an in-process tracer provider, so zero spans exist. The fix requires a package this project refuses. |

### Still outstanding for submission

**The ≤5-minute demo video does not exist.** It is a hard requirement and no part of the build produces
it. Nothing in this README should be read as saying the submission is complete.

| Required artifact | State |
|---|---|
| Project built with the required tools | Present — Strands Agents SDK, Amazon Bedrock (Nova only), AgentCore Runtime + Memory, deployed on AWS |
| Text description of features and functionality | This README, plus [docs/dashboard-verification.md](docs/dashboard-verification.md) and [docs/deployed-verification.md](docs/deployed-verification.md) for the evidence |
| Public code repository | Present — [`mohamedsorour1998/Grace`](https://github.com/mohamedsorour1998/Grace) |
| MIT license, visible in the About section | Present — [LICENSE](LICENSE), detected by GitHub as MIT |
| README | This file |
| Architecture diagram | [`docs/architecture.md`](docs/architecture.md) (Mermaid, renders on GitHub) and [`docs/architecture.png`](docs/architecture.png) |
| AWS Builder ID | **mohamedsorour1998@gmail.com** — the Devpost form asks for the email used to create the Builder ID |
| Live demo link *(optional, scores better)* | Present — **[grace.rosettacloud.app](https://grace.rosettacloud.app)** |
| **≤5-minute demo video** | **Not recorded. Outstanding.** The script, shot list, and figures are ready in [docs/demo-video-handout.md](docs/demo-video-handout.md) — must cover the problem, who it is for, why it matters, and a demonstration; uploaded publicly to YouTube or Vimeo |
| **builder.aws blog post** *(optional, bonus points)* | **Not published. Outstanding.** Drafted in [docs/builder-blog-post.md](docs/builder-blog-post.md) — must be public on builder.aws.com with "Agents for Humans" in the title |

**AWS Builder ID:** `mohamedsorour1998@gmail.com`

Both remaining artifacts are drafted rather than done. Each carries
`<replace this text by a screenshot of …>` markers where a capture belongs, so the writing is finished
and only the recording and the screenshots remain.

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
infra/                # provisioning: DynamoDB, IAM, Lambda, Step Functions, alarm, Cognito, Amplify
runtime_app.py        # BedrockAgentCoreApp wrapper, refuses to start unredacted
evals/                # trajectory evals proving gate ordering holds
web/                  # Next.js caseworker dashboard (Amplify SSR)
├── lib/authorize.ts   #   pure decision gate — is this case decidable, by this session?
├── lib/cases.ts       #   the only DynamoDB reader, paginated and de-duplicated
├── lib/cognito.ts     #   ID-token verification: signature, iss, aud, exp, token_use, role
├── lib/decide.ts      #   records the decision, then re-invokes so the gate re-runs
└── app/               #   sweep · queue · one case's audit trail
docs/                 # design specs, plans, deploy runbook, verification evidence
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
