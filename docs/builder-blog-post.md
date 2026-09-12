# Agents for Humans Finalist: Grace

**App Category:** Good Neighbor
**Team:** Mohamed Sorour (`mohamedsorour1998@gmail.com`)

`<replace this text by the COVER IMAGE — suggested: the Grace dashboard headline "9 handled alone / 3 waiting on you" over the three unwinding figures (94M enrolled · 25M+ lost coverage · 69% procedural)>`

---

## My Vision

A family qualifies for Medicaid. They qualified last year, they qualify this year, and nothing about
their circumstances has changed.

Their certification period runs **12 months**. The renewal window opens **60 days** before it ends, and
a late filing is still accepted for **90 days** after — so there is a five-month stretch in which one
form keeps the coverage. A notice arrives in the mail during that window, in English, addressed to an
apartment they left four months ago.

Nobody makes a decision about that family. No caseworker reviews their income, no system finds them
ineligible. A letter goes unanswered, 150 days elapse, and the coverage stops — silently, with no error
message anywhere in the process.

That is not a rare edge case. When pandemic-era continuous coverage ended in March 2023, states
resumed annual eligibility checks against a record **94 million** enrolees. More than **25 million
Americans lost coverage** — and roughly **69% of those losses were procedural**, missing forms and
missed deadlines rather than anyone becoming ineligible.

**About 17 million Americans lost health insurance they were still entitled to, because of paperwork.**

And here is the part that turned this from a sad statistic into a thing I could build against.
**42 CFR 435.916(a)(3)(iii) gives that family 90 days.** If coverage is terminated for failing to
return a renewal form, the agency must reconsider — *"if the individual subsequently submits the
renewal form within 90 days after the date of termination … without requiring a new application."*
The loss is reversible for three months, by law, and almost nobody inside that window knows it. Grace
lives in those 90 days.

I built **Grace** to close that window.

Grace watches every household's renewal clock against the encoded rules for its program — 12 months and
a 5% immaterial income band for Medicaid in New York, 6 months and 10% for SNAP. It **files the
renewals that are unambiguous**, chases the one missing document by text, and wakes a human caseworker
**only** when eligibility is genuinely in doubt.

Across the twelve seeded households it handles **nine alone and escalates three**, each with a typed reason a
person can act on. Not "needs review", but the three reasons as they actually appear in the ledger:

```text
c-010  missing_document: proof_of_residency is not on file (Grace has already messaged the family.)
c-011  material_income_change: Income moved 30.0%, above the 5.0% immaterial band
c-012  source_conflict: household size 5 on application, 3 on most recent wage record
```

The hard part was never the filing. It was building an agent that **provably refuses** to file when it
should not.

---

## Why This Matters

The people holding this together are caseworkers at community clinics, food banks, and
school-district family-support offices — tracking recertification windows for hundreds of households,
across programs with different clocks, with notices written in languages the families do not read.

They do not need another dashboard telling them what they already know. They need the routine work
done, and the hard calls handed over with the reasoning already assembled. What exists today, and where
each stops:

| Category | What it does | Where it stops |
|---|---|---|
| **Eligibility screeners** (mRelief, Benefits.gov) | Tells a family whether they *might* qualify | The family still does every step of the paperwork |
| **State renewal portals** | A place to submit a renewal | Requires the family to know a deadline exists and act on it |
| **Case-management systems** (Apricot, CaseWorthy) | Tracks households and notes | Records what happened; takes no action on anyone's behalf |
| **Generic RPA / form bots** | Automates form submission | No judgement about *when not to submit* — it will file a wrong renewal as happily as a right one |
| **A chatbot over benefits policy** | Answers eligibility questions | Answering is not filing, and a wrong answer is invisible |
| **Grace** | **Files the unambiguous, escalates the rest with a reason** | Deliberately does not decide contested eligibility — that stays human |

The distinction is the last row's second half. Automating benefits filing is not hard; plenty of
tools submit forms. What is hard is an automation that **knows the boundary of its own competence** and
can prove where it is. A bot that files 100% of renewals is worse than the status quo, because now the
wrong filings carry an official-looking submission.

Grace's value is the refusal, not the automation.

---

## How I Built This

Grace runs on AWS on a daily schedule, unattended:

```
EventBridge (daily) → Step Functions → Lambda → AgentCore Runtime
                                                    │
                          AgentCore Memory (per-household facts) ┤
                          DynamoDB (the case ledger)             ┤
                          Family channel (SMS / transcript)      ┘

Caseworker → Cognito → Amplify SSR dashboard → DynamoDB (reads)
                                             └→ InvokeAgentRuntime (a decision)
```

Built with the **Strands Agents SDK**, **Amazon Bedrock** (Nova models only), and **Amazon Bedrock
AgentCore** Runtime, Memory, and Identity — four surfaces, not five. Gateway is deferred with a
written reason, because claiming a surface I did not ship is the one thing that turns a working entry
into a dishonest one.

`<replace this text by a screenshot of docs/architecture.png — the full architecture diagram>`

### The one design idea: an escalation boundary

An agent that can file a benefits renewal can also file a wrong one. So Grace's defining property is
that it acts alone on the routine and *provably* escalates the rest. Three layers, strongest first.

**1. Capability absence.** The tool that files a renewal is not registered in the agent's tool list
at all for a case that has not passed verification. *This beats any instruction, because there is
nothing to disobey.*

**2. Identity from the session, never the conversation.** Every household-scoped read tool takes
**zero arguments** — the case is bound at construction from the authenticated session. A prompt
injection cannot point Grace at a different family, because there is no parameter to poison.

**3. A deterministic gate.** `grace/authority.py` is pure Python — no model, no I/O, no network —
mapping case facts to *act* or *escalate*. Any error during verification escalates. Fail closed.

### Three choices worth explaining

**Deadline math is a tool, not an agent.** Early on I let a model compare a document's received date
against a freshness window; on a real sweep it got that wrong on **two of nine clean cases** and texted
those families about paperwork that was already in order. The comparison now happens in Python and the
tool reports `CURRENT` / `STALE` / `EXPIRED` outright. Never hand a model two dates and ask it which is
later.

**Three models deliberate, but only on ambiguous cases.** When a case turns on judgement — a 30%
income change, a size conflict between two documents — an advocate (Nova 2 Lite) argues the family
qualifies, a verifier (Nova Pro) adversarially checks each claim, and a referee (Nova Micro) decides
whether it is genuinely ambiguous. Three *different* models: two instances of the same model agreeing
proves nothing, and nothing should referee its own argument. The nine clean households never pay for
any of it.

**Grace stores no documents, because it is not the party that receives them.** The instinct is to add
an upload. The domain says otherwise: the family sends documents to the **state**, whose eligibility
system is the system of record — it attempts an *ex parte* renewal from wage data it already holds,
mails a form if that fails, requests specific documents if it still cannot verify, and a state worker
decides. The 69% procedural loss happens between the notice and the submission. Grace's users are
*navigators* — clinics, food banks, school family-support offices — and what a navigator actually knows
is *"I helped this family send their paystub on the 20th"*. **Status, not custody.**

So a document in Grace is an id and a date, and the interface says **"documents sent to the state"**
rather than "documents on file" — three words that had implied custody nobody has. Storing the file
would have put the maximum-PII payload in the system into an architecture whose whole rule is "no
household identity anywhere", and bought the gate nothing: it reads two dates and never opens a
document. What Grace has is a caseworker's **assertion**, so the case page names it as one — *"Document
status asserted by `2448a4e8-…` at intake on 2026-09-06. Grace tracks the deadline on it and does not
verify it independently."* An opaque id, never a name.

The seam where a real answer would arrive is code rather than a sentence in a README:
`grace/cases/document_source.py`, a protocol whose distinguishing method is `provenance()` — every
source must say *how it knows* — with the shipped implementation reading the record and the state one
**raising `NotImplementedError`**. Returning an empty tuple would not read as "unknown": the gate takes
`()` as "this family has sent nothing", so every household would escalate for reasons that were not
true, silently. What it needs is a per-state data-sharing agreement, which is a legal instrument, not a
sprint.

**Every number in a rule pack names its authority.** The packs encode a programme's clocks —
certification length, when the window opens, how late is still savable, what income movement is
immaterial — and those numbers decide whether a family keeps coverage. They cited nothing, which makes
an invented number indistinguishable from a researched one. Now each carries the provision that
establishes it, checked against the regulation text before it was written down, or the literal string
`policy choice` where the regulation gives a range and the value is mine.

Three of the thirteen are federally mandated: the 12-month Medicaid renewal cycle
(42 CFR 435.916(a)(1)), that 90-day reconsideration window, and SNAP's own 30-day equivalent
(7 CFR 273.14(e)(2)). The most interesting entry is a negative one. SNAP's immaterial-income band is
10% in my pack, and **7 CFR 273.12(a)(1)(i)(A) sets a dollar threshold, not a percentage** — "a change
of more than $100 in the amount of unearned income". A percentage band has no federal basis at all.
Writing `42 CFR ...` beside it would have looked like diligence and been a lie, so it says
`policy choice` and explains itself.

**And I got one of these wrong in the direction that is hardest to catch.** An earlier version of this
post, and the pack it describes, said SNAP had *no* reconsideration provision equivalent to Medicaid's
90 days — that a household missing recertification simply reapplies. That is false:
7 CFR 273.14(e)(2) gives 30 days after the period ends to complete the process and requires the agency
to reopen the case. I had checked the reporting rules in 273.12 and the certification periods in
273.10 and concluded from their silence. **A false negative claim is worse than a false citation**,
because nothing in the section you cited visibly contradicts it — there is no wrong number to notice,
only an absence you asserted. The honest contrast is narrower and more useful: Medicaid gives 90 days
with no new application and no proration, SNAP gives 30 with proration. Grace has *less* room on SNAP,
not *no* room, and the pack now says which.

### Milestones

The gate came first — a pure-Python authority module, table-tested exhaustively, before any agent
existed. Then the local sweep (twelve seeded households, 9 filed / 3 escalated), the AgentCore deployment on an
EventBridge schedule, and the Cognito-gated dashboard on Amplify SSR. Then caseworker intake, and making
the deployed sweep genuinely read its caseload from the table. Last, the safety claim executed against
live infrastructure.

**945 Python tests and 232 frontend tests**, plus 23 trajectory evals asserting the gate's ordering
holds against real Bedrock calls. Every guard was sabotaged and watched failing — that is the subject
of a companion post.

---

## Demo

`<replace this text by the YouTube embed — use the "Insert YouTube embed" feature. NOTE: this article's video must be UNDER 3 MINUTES, which is shorter than the hackathon's 5-minute submission video. Record a tighter cut.>`

The sweep at a glance → the escalation queue → one household's full audit trail → a caseworker
approving the household with a missing document, and Grace refusing to file anyway.

---

## What I Learned

### The insight that changed the architecture

Early in development, the model in the gated role **filed a renewal it had been explicitly instructed
not to file.**

That is not a prompt-engineering problem to solve with firmer wording. If the correctness of a benefits
filing depends on a model choosing to obey, the design is wrong regardless of which model you pick.
That single failure is why the gate is pure Python the model cannot argue with, and why privileged
tools are *absent* rather than *forbidden*.

### The bug that would have shipped

My Next.js middleware carried a careful docstring: it was *"a redirect convenience, and never the
security boundary"*, because `verifySession` *"still refuses on every page."*

The second half was false. Grepping for `verifySession` matched the auth callback and the write route.
**No page verified anything.** Measured against a real server:

```
no cookie                                  → 307 /login
Cookie: grace_session=totally.forged.token → 200, 45143 bytes,
        every case id, every escalation reason, the full headline
```

An unsigned, unparseable **literal sentence** was a complete authentication bypass for every read in
the application.

The sentence had been *true when written* — the write route was the only consumer then. Pages grew
around it and the comment kept vouching for them. **Comments do not fail when the code they describe
stops being true**, and this one suppressed suspicion: anyone reading it concluded the gate was
elsewhere.

### The test that passed with the safety removed

The headline test asserted that approving the household missing a document still escalates. It
passed. It also passed with the gate **deliberately bypassed** — the test fake filed nothing, so a
*different* branch escalated the case for every possible input. The claim was true of the run and
unproven by the test.

When several code paths converge on the same observable result, asserting that result says nothing
about which produced it. The fix was to arm the fixture so the other branch could not fire.

### The defect that was invisible because nothing failed

The dashboard lets a caseworker add a household. I wrote the code that makes a submitted case visible
to the agent, tested it, and shipped it. It did not work in production for three days.

Two reasons, and each alone was enough. The deployed container was an older image that could not read
the new record rows. And the EventBridge schedule carried a **hardcoded list of twelve case ids** — the
caseload was frozen at the moment I last ran the provisioning script.

Nothing failed. The schedule stayed green, every execution reported `SUCCEEDED`, and the count was
right, because the twelve it knew about were all still behaving. A submitted household simply was never
visited. I found it by asking a question no dashboard answers: *is the thing I deployed the thing I
wrote?*

**A caseload, a container image, and an orchestrator's input list are three different places your
system's idea of "what to process" can live.** A claim about your agent is only as deployed as the last
of them.

### The through-line

Six of the seven serious defects I found shared one shape: **something asserted a property it did not
verify.** A docstring. A comment. A test. A config. An API's acceptance of my input. A running system
standing in for the code I had written.

So every guard in Grace was sabotaged and watched failing — 51 sabotages in a single task. If you
cannot make a test fail, you do not have a test; you have a sentence that agrees with you. For a system
whose failure mode is a family losing health coverage with no error message, that seemed like the right
standard.

### What I would tell a judge to check

The claim I would stake the project on, executed on live infrastructure: a caseworker approved the
household missing `proof_of_residency`, and the outcome row reads *"Grace re-checked and did not file.
missing_document: proof_of_residency is not on file."* **Zero renewals filed.** A human said yes and
the gate still said no.

That guarantee is structural rather than a matter of trust: the gate's `evaluate()` has no parameter
an approval could occupy, so a mistaken edit would be a type error rather than a quietly looser
verdict.

`<replace this text by a screenshot of the case page showing the outcome "Grace re-checked and did not file">`

And three things that do **not** work, stated because a project that hides them is less trustworthy
than one that names them: CloudWatch trace correlation is unavailable (Runtime injects the OTEL
variables but installs no in-process tracer, so every ledger row carries a null trace ID); SMS is
sandboxed, so the family channel writes a transcript; and one household name reached CloudWatch before
I fixed it at the source by removing the field entirely.

---

**Code:** [github.com/mohamedsorour1998/Grace](https://github.com/mohamedsorour1998/Grace) (MIT)
**Live:** [grace.rosettacloud.app](https://grace.rosettacloud.app)
**Built with:** Strands Agents SDK · Amazon Bedrock (Nova) · AgentCore Runtime, Memory, Identity ·
DynamoDB · Step Functions · Lambda · EventBridge · Cognito · Amplify

*All household data is synthetic. Unwinding figures: [Medicaid.gov](https://www.medicaid.gov/resources-for-states/coronavirus-disease-2019-covid-19/archived-unwinding-and-returning-regular-operations-after-covid-19)
and [KFF](https://www.kff.org/medicaid/10-things-to-know-about-the-unwinding-of-the-medicaid-continuous-enrollment-provision/)
for enrolment and coverage losses; [JAMA Health Forum](https://jamanetwork.com/journals/jama-health-forum/fullarticle/2825467)
for the 69% procedural share.*
