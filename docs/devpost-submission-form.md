# Devpost submission form — every field, ready to paste

Companion to `docs/devpost-description.md` (which holds the standalone text description). This file
covers the **Project details** step's remaining fields: the Project Story in Devpost's own seven
headings, the Built-with tags, the links, and what the media slots need.

**Measured 2026-09-14, before writing.** Re-measure anything you quote if you record later:

| Figure | Measured value |
|---|---|
| Python tests | **945** |
| vitest | **232** across **11** files |
| trajectory evals | **23** (real Bedrock, costs money to run) |
| deployed runtime version | **9**, `READY`, updated 2026-09-12 |
| live table rows | **2,992** — moves constantly, never quote it |
| invariant 1 | `renewal_submitted` for exactly `c-001`–`c-009` ✅ |
| invariant 2 | no escalating household ever filed ✅ |
| scheduled sweeps since the caseload fix | 09-11, 09-12, 09-13 — all `SUCCEEDED`, composite names |
| rule-pack parameters | **13 total: 3 federally mandated, 10 `policy choice`** |

---

## Project Story

*Paste the whole block below into the "About the project" field. It uses Devpost's own headings.*

### Inspiration

When pandemic-era Medicaid protections ended in 2023, more than 25 million Americans lost coverage.
About **69% of them lost it to paperwork rather than eligibility** — roughly 17 million people who
still qualified, cut off because a letter went unanswered or one document never arrived.

The detail that turned this into a project: **federal law already gives those families 90 days to fix
it.** 42 CFR 435.916(a)(3)(iii) requires the agency to reconsider a household terminated for failing
to return a renewal form, if it arrives within 90 days — *without requiring a new application*.
Almost nobody knows the window exists, and it closes quietly.

The failure has no error message. A renewal simply does not happen, and a family finds out at a
pharmacy counter when a prescription is refused. That is a class of harm nobody intended and nobody
is watching, which is exactly the shape of problem an agent can hold.

### What it does

Grace watches every household's benefit-renewal clock against the encoded rules for its program,
files the renewals that are unambiguous, texts the family in their own language for the one missing
document, and wakes a human caseworker **only when eligibility is genuinely in doubt**.

Across twelve seeded synthetic households it handles **nine alone and escalates three**, each with a
typed reason a caseworker can act on: a missing proof of residency, a material income change, two
sources that disagree. Those counts are confirmed by reading DynamoDB, not by trusting a log line.

It is for caseworkers at community clinics, food banks, and school family-support offices, tracking
recertification windows for hundreds of households across programs with different clocks. They enrol a
household once; Grace watches the clocks from then on. Nobody opens an app to do this work, so it runs
as a background process and surfaces only at the decision.

### How we built it

A daily EventBridge schedule starts a Step Functions sweep that invokes Grace on **AgentCore Runtime**
once per household. Inside, all three Strands multi-agent patterns, each where it is actually the right
tool:

- A **Graph** is the deterministic spine — intake, document checks, decide. Deadline math is a *tool,
  not an agent*: deterministic work does not need a model.
- A three-agent **Swarm** runs only on ambiguous cases: an advocate, an adversarial verifier, and a
  referee, on three **different** Amazon Nova models. Two instances of one model agreeing proves
  nothing, and nothing should referee its own argument. The nine clean households never pay for it.
- **Agents-as-tools** drafts the family's message in a nested agent with no tools and no access to the
  case, so translation chatter never enters the eligibility reasoning — and the agent that writes the
  words cannot send them.

**AgentCore Memory** carries what each sweep concluded into the next one; a recertification cycle is
annual, so a fact is only useful if it survives eleven months. A remembered lesson reaches the
advocate — the agent whose job is to argue — and never the gate.

**The gate is the point.** `authority.py` is pure Python, no model and no I/O, wired in as a Strands
steering handler that runs before every state-changing call. Three layers, strongest first:

1. **Capability absence.** Privileged tools are not in the agent's tool list at all for a case that has
   not passed verification. There is nothing to disobey.
2. **Identity from the session, never the conversation.** Every household-scoped tool takes **zero
   arguments**. A prompt injection has no parameter to poison.
3. **A deterministic gate**, and any error during verification escalates. Fail closed.

Caseworkers sign in through Cognito to a Next.js dashboard on Amplify SSR: the sweep at a glance, the
escalation queue, one household's full audit trail, and an approve/deny control. Approving bypasses
nothing — it records the decision and **re-invokes** Grace so the gate re-evaluates the case facts. It
is deliberately not a "resume", because resuming a paused agent with any affirmative answer just
approves whatever it was blocked on.

### Challenges we ran into

**A green build proves nothing about a running system.** Five separate deploy defects survived
`SUCCEED` builds. Marking the AWS SDK as an external package made it absent at runtime, so every page
returned 500 with a *valid* session, failing on a module name nobody published. Amplify environment
variables never reach the SSR runtime at all. Each one had to be found by probing the deployed app at
request time with a real session, not by reading a build log.

**The worst bug never failed.** A household added through the intake form was invisible to the daily
sweep — and it was two defects wearing one symptom. The deployed container image was four days older
than the code that could read the new record shape, *and* the EventBridge schedule carried a hardcoded
list of twelve case ids, so the caseload was frozen at the moment the provisioning script last ran.
The schedule stayed green, every execution reported `SUCCEEDED`, and the 9/3 count stayed correct.
That is precisely what made it invisible. It was found by asking a question no dashboard answers: *is
the thing I deployed the thing I wrote?*

**Tests that could not fail.** A parametrized safety test ran on three escalating households, but two
of them never execute a gated action — escalating is the point — so the loop that did the checking
never ran, and the test passed having asserted nothing while still paying for real Bedrock calls.
Elsewhere, a test proving the escalation gate could not be bypassed passed *with the gate deleted*,
because a different branch produced the same outcome for every input. The habit that came out of this:
prove a new test fails against the code it was written to catch, and remove the other branches' alibis
before believing a safety assertion.

**A household surname reached CloudWatch.** Not through any logging call. `read_case` returned a
display name → the swarm's referee quoted it in its deliberation prose → that became the escalation
reason → the reason was the Lambda's return payload → Step Functions logged the payload. Span
redaction covers span content, and a Step Functions execution payload is not span content. The fix was
capability absence rather than filtering: `read_case` no longer returns the name at all, which closes
every downstream path at once. Then the already-written rows had to be scanned and stripped
separately, because fixing the source does not clean what was already written.

**A model filed a renewal it had been told not to file.** Told *"never submit a renewal when a
required document is missing"*, one Nova model read the case, saw the document was missing, filed
anyway, and then said *"I made the same mistake again."* Other models obeyed the identical prompt —
which is the point. Grace does not rely on a model choosing to obey.

### Accomplishments that we're proud of

**The safety claim is executed, not argued.** A real caseworker session approved the household missing
a proof of residency on the deployed system. Grace re-checked and **filed nothing** — confirmed from
DynamoDB, where `renewal_submitted` for that household is still zero while its decision rows exist. A
human said yes and the gate still said no.

**It runs unattended.** The daily schedule has fired every day since deploy, every execution
`SUCCEEDED`, and both invariants held through every one: renewals filed for exactly the nine clean
households, and no escalating household ever filed.

**Every rule-pack number names its authority — and says when it has none.** Three of thirteen
parameters are federally mandated and quoted. The other ten carry `authority: "policy choice"` with a
note on what the regulation does and does not establish. The sharpest entry is negative: SNAP's
immaterial-income band is a percentage, and 7 CFR 273.12(a)(1)(i)(A) sets a *dollar* threshold, so a
percentage band has no federal basis at all and the pack says so. An uncited number that decides
whether a family keeps coverage is indistinguishable from an invented one.

**No household identity exists anywhere a model or a log can reach.** The intake form refuses to
collect a name, phone, or address — with an allowlist, so it cannot be walked around with a field
name nobody anticipated. The dashboard labels document status as *asserted by* an opaque id, with
*"Grace does not verify it independently"* on screen, because a claim Grace never confirmed should be
labelled as one for the person about to act on it.

**We named the one AgentCore surface we did not ship.** Four, not five. Gateway is deferred with its
reason. Claiming five would be the single thing that turns a working entry into a dishonest one.

### What we learned

**"The API accepted my configuration" and "the control does what I think" are different claims.**
Enabling point-in-time recovery on a table silently failed one run in three while the script exited 0.
The fix was a read-back as the sole arbiter of success. The same shape appeared in IAM: omitting
`WriteAttributes` from a Cognito client does not withhold write access, it grants *everything* — and
probing the obvious attribute produced a refusal from a *different* guard, which would have confirmed
the wrong mechanism. To verify a control, perform the action it should prevent against a target where
only that control can refuse.

**A docstring asserting that some other layer performs a check is not evidence that it does.** A
comment vouched for session verification on every page. Grepping for the function found it in two
routes and zero pages: an unsigned, unparseable literal string in a cookie returned 200 with every
household record. The claim had been true when it was written, and stayed in place while the pages
were built around it.

**Fail-closed is not always the safe instinct — it depends on what the code is deciding.** A steering
handler's exception is swallowed by the SDK and the tool then runs *ungated*, so every fallible call
there must fail closed. But a hook's exception is re-raised and converts an already-approved tool call
into a failed one — so failing closed on an *observability* question would mean losing a renewal to
keep a trace ID. Failing closed on verification protects the family; failing closed on telemetry harms
them.

**When two surfaces disagree, first ask whether they are answering the same question.** The dashboard
reported "3 waiting on you" while the queue showed 2. Neither was wrong — one counted what Grace
escalated, the other what remained undecided. A reader cannot audit two right answers; they can only
stop trusting the page. The fix was not to pick a number but to make the disagreement unrepresentable:
one computed field both surfaces read.

**When an experiment appears to eliminate a hypothesis, list what else differs between the probe and
the real case.** Memory recall returned nothing for two days. The right hypothesis was tested with a
trivial probe that also returned nothing, so it was recorded as eliminated — and a whole verification
section was written blaming the service. Two things differed between probe and production and only one
had been varied.

### What's next for Grace

**The state integration, which is a legal instrument rather than code.**
`grace/cases/document_source.py` already defines the seam: a `DocumentSource` protocol whose
distinguishing method is `provenance()`, because every source must be able to say *how it knows*. The
real implementation deliberately **raises** rather than returning an empty result — to the gate, "no
documents" is the positive claim that a family has sent nothing, so a polite stub would escalate all
twelve households for reasons that are not true. It needs a per-state data-sharing agreement and
credentials issued under it. **AgentCore Gateway** is deferred for exactly this: an outbound call to a
system Grace does not own.

**Ex parte renewal modelling** is the lever that actually prevents the 69% — and it is something the
*state* does, from wage and tax data Grace cannot get. Building it now would mean simulating a
capability Grace does not have.

**Real SMS**, once an origination number and sender-ID registration exist. The channel already sits
behind an interface with a transcript implementation as the always-works path, so the demo never
depends on delivery.

**CloudWatch trace correlation**, if AgentCore Runtime ever installs an in-process tracer provider.
Every ledger row already carries a `trace_id` field; its value is null, honestly, because tracing
genuinely was not configured. The packages that would fill the gap are ones this project refuses,
because they would trade a verified safety property for a nicer screenshot.

---

## Built with

*Devpost allows 25 tags. These 18 are all genuinely in the request path or the deploy.*

```
amazon-bedrock
amazon-bedrock-agentcore
strands-agents
amazon-nova
aws-lambda
aws-step-functions
amazon-eventbridge
amazon-dynamodb
amazon-cognito
aws-amplify
amazon-cloudwatch
python
typescript
next.js
react
tailwindcss
boto3
podman
```

Do **not** add a tag for anything deferred (no `amazon-sns`, no Gateway, no OpenTelemetry).

## "Try it out" links

| Label | URL |
|---|---|
| Live dashboard | `https://grace.rosettacloud.app` |
| Source code (MIT) | `https://github.com/mohamedsorour1998/Grace` |

The dashboard is Cognito-gated and accounts are admin-created only, so a judge cannot self-register.
**Add the seeded caseworker credentials to the "Additional info" step**, not to the public page.

## Image gallery

3:2 ratio, JPG/PNG/GIF, 5 MB max, up to 15. Four is enough; put them in this order, because the first
is the gallery thumbnail:

1. **`/` — the sweep.** The headline `9 handled alone, 3 waiting on you.` and the twelve-row case
   table. This is the strongest single frame Grace has.
2. **`/queue`.** Exactly `c-010`, `c-011`, `c-012` with their typed reasons, soonest deadline first.
   Note what is absent: no names, no phone numbers, no addresses.
3. **`/case/c-010`.** The typed reason, the *documents sent to the state* panel with its
   *"asserted by … does not verify it independently"* provenance line, the Spanish
   `family_message_sent` row, and the ledger.
4. **`docs/architecture.png`.**

Every one of these is a real screenshot of the deployed app. Do not mock one up.

## Video demo link

**Blocking, and nothing in the repository produces it.** The script, shot list, timings, and
figure-provenance table are ready in `docs/demo-video-handout.md`. Requirements: ≤5:00, covers problem
→ who it's for → why it matters → working demo, uploaded **public** (not unlisted) to YouTube or
Vimeo.

Two claims the recording must not make, both easy to say by accident: that traces or Transaction
Search work (zero spans exist in the account), and that SMS is delivered (the channel writes a
transcript). The handout has a full "Things not to claim" section.

## Additional info step

- **AWS Builder ID: `mohamedsorour1998@gmail.com`** — the form asks for the *email* that created the
  Builder ID, not a handle. `@sorour` is the wrong value.
- Seeded caseworker credentials for judges — see `docs/dashboard-runbook.md`.
