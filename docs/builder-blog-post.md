# Agents for Humans: 17 million people lost health coverage to paperwork

## An agent that files the easy renewals and refuses to guess on the hard ones

When pandemic-era continuous coverage ended on 31 March 2023, states resumed annual Medicaid
eligibility checks. Enrolment had reached a record **94 million**. Over the unwinding period that
followed, **more than 25 million people lost Medicaid coverage**.

Here is the number that made me build something:

> **Roughly 69% of those losses were procedural.** Missing renewal forms. Missed deadlines. One
> document that never arrived. Not people who stopped qualifying — people who still qualified and lost
> coverage anyway.

That is about **17 million people** who lost health insurance they were entitled to, because of
paperwork.

The failure is silent by design. Nobody gets an error message. A renewal simply does not happen, and a
family finds out at a pharmacy counter when a prescription is refused.

I built **Grace** for the caseworker holding hundreds of those files at once.

`<replace this text by a screenshot of the Grace dashboard at grace.rosettacloud.app showing "9 handled alone" and "3 waiting on you">`

---

## What Grace does

Grace watches every household's renewal clock. It **files the renewals that are unambiguous**, chases
the one missing document by text message, and wakes a human caseworker **only** when eligibility is
genuinely in doubt.

On twelve synthetic households it handles **nine alone and escalates three**, each with a typed reason
a caseworker can act on — not "needs review", but `missing_document: proof_of_residency is not on file`.

Built with the Strands Agents SDK, Amazon Bedrock (Nova models only), and Amazon Bedrock AgentCore
Runtime and Memory. It runs on a schedule, unattended.

---

## The one design idea

An agent that can file a benefits renewal can also file a wrong one. So Grace's defining property is
an **escalation boundary**: it acts alone on the routine and *provably* escalates the rest.

Three layers, strongest first.

**1. Capability absence.** The tool that files a renewal is not registered in the agent's tool list at
all for a case that has not passed verification. Grace cannot file a renewal it should not, because the
ability does not exist in that context. *This beats any instruction, because there is nothing to
disobey.*

**2. Identity from the session, never the conversation.** Every household-scoped read tool takes
**zero arguments**. The case is bound at construction from the authenticated session. A prompt
injection cannot point Grace at a different family, because there is no parameter to poison.

**3. A deterministic gate.** `grace/authority.py` is pure Python — no model, no I/O, no network —
mapping case facts to *act* or *escalate*. Any error during verification escalates. Fail closed.

Remember layer 1. It comes back later as the fix for a problem that looks unrelated.

`<replace this text by a screenshot of the architecture diagram from docs/architecture.png>`

---

## How it is put together

```
EventBridge (daily) → Step Functions → Lambda → AgentCore Runtime
                                                    │
                          AgentCore Memory (per-household facts) ┤
                          DynamoDB (the case ledger)             ┤
                          Family channel (SMS / transcript)      ┘

Caseworker → Cognito → Amplify SSR dashboard → DynamoDB (reads)
                                             └→ InvokeAgentRuntime (a decision)
```

Two deliberate choices in there.

**Deadline math is a tool, not an agent.** Deterministic work does not need a model. Early on I let the
model compare a document's received date against a freshness window; on a real sweep it got that wrong
on **two of nine clean cases** and texted those families about paperwork that was already in order. The
comparison now happens in Python and the tool reports `CURRENT` / `STALE` / `EXPIRED` outright. *Never
hand a model two dates and ask it which is later.*

**The three-model deliberation runs only on ambiguous cases.** When a case turns on a judgement — a 30%
income change, a household-size conflict between two documents — three *different* Nova models argue:
an advocate makes the case that the family qualifies, a verifier adversarially checks each claim
against readable facts, and a referee decides whether it is genuinely ambiguous. Three different models
because two instances of the same model agreeing proves nothing, and nothing should referee its own
argument. The nine clean households never pay for any of it.

---

## Seven times something was confidently wrong

This is the part I would actually want to read. Each of these looked exactly like working code.

### A model filed a renewal it had been told not to file

The gated role originally ran on Nova Lite. Under test it **filed a renewal it had been explicitly
instructed not to file.**

That is not a prompt-engineering problem to solve with firmer wording. If the correctness of a benefits
filing depends on a model choosing to obey, the design is wrong no matter which model you pick. This is
what pushed the gate out of the prompt and into pure Python that the model cannot argue with.

### A check that could never fire

My sweep detected escalations with `getattr(result, "stop_reason", None) == "interrupt"`. But
`GraphResult` has no `stop_reason` field at all — only single-agent results do. The expression is
*always* `False`, so the escalation branch never executed and **every case was reported as handled
autonomously.**

No exception. No warning. The demo would have claimed 12 handled and 0 escalated.

I found it by reading the SDK's source instead of its documentation. I now keep a table of six places
where the published docs disagree with the installed code.

### A denylist that made the unrecognised answer the dangerous one

When a caseworker's decision resumed a paused agent, I checked the response against words meaning
"escalate". Then I measured what the SDK actually does: it computes
`can_proceed = event.interrupt(...)` and cancels the tool only `if not can_proceed`.

**Any non-empty string is truthy.** Confirmed against the real executor: `"Escalate."` with a trailing
period, `"no, hold this one"`, and `"needs review"` all resumed the graph and **filed a renewal for a
household missing a required document.**

The immediate fix was an allowlist — proceed only on an exact match to `approve`, `yes`, `file`,
`proceed`. The better fix was architectural: the dashboard does not resume a paused agent at all. It
records the decision and **re-invokes**, so the gate re-evaluates from scratch.

> The polarity that fails closed is always *"act only on an exact affirmative"*, never *"refuse only on
> a known negative."*

### "The API accepted my configuration" is not "the control works"

Grace's caseworkers live in an Amazon Cognito user pool. I omitted `WriteAttributes` on the app client
and wrote a confident comment: *capability absence — the client cannot rewrite the claim that
authorises it.*

Then I probed it on a throwaway pool carrying two custom attributes, one mutable and one not:

```
WriteAttributes omitted → write an ungranted MUTABLE attribute → SUCCEEDED
WriteAttributes omitted → write the IMMUTABLE role attribute   → InvalidParameterException:
                                                  "Attribute cannot be updated."
WriteAttributes ["other"] → write the excluded attribute        → NotAuthorizedException:
                                     "A client attempted to write unauthorized attribute"
```

**Omitting it grants every attribute.** The AWS documentation says so outright. My protected attribute
had survived only because its schema marked it immutable — I had claimed two guards and shipped one,
with the comment asserting the reverse.

What makes this worth writing down is *why it was easy to miss*. Probing the protected attribute alone
**does** produce a refusal. It just comes from the immutability check rather than from permissions.
Reading that as evidence confirms the wrong mechanism entirely.

> To verify a control, perform the action it should prevent — against a target where **only** that
> control can refuse.

### A docstring that vouched for a check nobody performed

This one would have shipped a genuine vulnerability.

My Next.js middleware carried a careful docstring: *"a redirect convenience, and never the security
boundary — a forged cookie gets past it and is then refused by `verifySession`, which is the check that
matters"*, and that `verifySession` *"still refuses on every page and on the decide route."*

The second half was false. `grep verifySession app/` matched the auth callback and the write route.
**No page verified anything.** Measured against a real server:

```
no cookie                                  → 307 /login
Cookie: grace_session=totally.forged.token → 200, 45143 bytes,
        every case id, every escalation reason, the full headline
```

An unsigned, unparseable **literal sentence** was a complete authentication bypass for every read in
the application.

The sentence had been *true when written* — the write route was the only consumer then. Pages grew
around it and the comment kept vouching for them. Comments do not fail when the code they describe
stops being true, and this one actively suppressed suspicion: anyone reading it concluded the gate was
elsewhere and moved on. That is worse than no comment.

The fix is a `requireSession()` that calls `redirect()` — which *throws*, so there is no falsy return a
caller can forget to branch on — invoked before any page touches the data layer.

> When a comment says "X is checked elsewhere", grep for X.

`<replace this text by a screenshot of the sign-in redirect, or of the requireSession function>`

### Five deploy defects that each survived a green build

Deploying to AWS Amplify took seven builds. Builds that reported **SUCCEED** still served 500s, and
builds that failed described themselves wrongly.

The best of them: `serverExternalPackages` listed the AWS SDK, on the sound reasoning that server-only
packages should not enter the client bundle. Right reasoning, wrong mechanism — marking a package
external emits a bare `require` and **omits it from the bundle**, and Amplify's SSR bundle ships only
what the trace includes. Every page returned 500 **with a valid session**: sign-in worked, then the
first import failed with `Cannot find module '@aws-sdk/client-dynamodb-3e32f4e24bb075d4'`, naming a
module nobody has ever published, because Turbopack appends a content hash to an external's name.

It was protecting nothing. No client chunk referenced the SDK anyway.

The other four, briefly, because each cost a build:

- **Amplify environment variables never reach the SSR runtime** — documented as intentional. Bridge
  them by writing `.env.production` in the buildspec *before* the build.
- **`update_app(environmentVariables=…)` is a full replace**, and the console writes its own keys into
  the same map. A write built from a stale read deleted the monorepo app root; the next build died at
  clone time with `Cannot read 'next' version in package.json` — which reads like a packaging problem,
  not a deleted variable.
- **A monorepo app root and a flat buildspec are mutually exclusive**, and both failure modes surface
  at clone time before any phase runs.
- **Amplify rejects any variable starting with `AWS`**, reserved for the credentials the execution role
  injects. Separately, two keys I typed by hand carried whitespace, which reads as *absent* to an
  exact-key lookup. A loader can trim values; it can never trim a malformed key.

> A green build is a statement about compilation, not about a running request. Probe the deployed app at
> request time, with a real session, and assert on **rendered content** — not on HTTP 200.

`<replace this text by a screenshot of the Amplify build history showing the successful deploy>`

### A safety test that passed with the safety removed

The headline test asserted that approving the household missing a document still escalates. It passed.
It also passed with the gate **deliberately bypassed**.

The test fake filed nothing, so a *different* branch — "clean case, no renewal filed" — escalated the
case for every possible input. The safety claim was true of the run and unproven by the test.

The fix was to arm the fixture so that other branch could not fire: pre-write the filing row, giving
the sabotaged code a real path to reporting success. Now the gate is the only thing standing between the
approval and a wrong outcome, and that sabotage fails three tests.

> When several code paths converge on the same observable result, asserting that result says nothing
> about which path produced it. Ask what *else* could produce this output, then remove its alibi.

This is why every guard in Grace was sabotaged and watched failing — 51 sabotages in a single task. A
related trap worth knowing: a sabotage that *crashes* the test runner records **zero** failed
assertions and scores as a survivor. Weaken a bound rather than removing it.

---

## The claim I care most about

A caseworker signs in, opens the household missing `proof_of_residency`, and clicks **Approve**.

Grace records the decision — against an opaque identifier, never a name — and re-invokes so the
authority gate re-evaluates the case record from scratch. Executed on live infrastructure, the outcome
row reads:

> *Grace re-checked and did not file. missing_document: proof_of_residency is not on file (Grace has
> already messaged the family.)*

Zero renewals filed for that household. A human said yes and the gate still said no, because the
document is still missing.

**A human's approval can make Grace more cautious. It can never make it less.** That guarantee is
structural rather than a matter of trust: the gate's `evaluate()` function has no parameter an approval
could occupy, so a mistaken edit would be a type error rather than a quietly looser verdict.

`<replace this text by a screenshot of the case page showing the outcome "Grace re-checked and did not file">`

---

## What does not work

Three things, stated plainly, because a project that hides them is less trustworthy than one that names
them.

**CloudWatch trace correlation is unavailable.** Every ledger row carries a `trace_id` key whose value
is `NULL`. AgentCore Runtime injects the OpenTelemetry environment variables but does not install an
in-process tracer provider, and zero spans exist in the account. The DynamoDB ledger is the evidence
instead — and a ledger row cannot be dropped by sampling, which a span can.

**SMS is not delivered.** The account is sandboxed with no origination number, so the family channel
writes a transcript behind an interface. The demo never depends on message delivery.

**One household name reached CloudWatch before it was fixed.** A tool returned a display name, a model
quoted it into its reasoning, that text became an escalation reason, and the reason was logged as a
Step Functions payload — a path that span redaction does not cover. The fix was **layer 1 again**: the
tool no longer returns the field at all, so there is nothing downstream to leak. Stripped from durable
storage and confirmed clean across every row. Historical log events cannot be unwritten; they age out
with retention.

---

## The through-line

Six of those seven failures shared a single shape: **something asserted a property it did not verify.**
A docstring. A comment. A test. A configuration. An API's acceptance of my input.

The discipline that caught them is not clever. It is: *perform the action the guard should prevent, and
watch the guard refuse.* If you cannot make a test fail, you do not have a test — you have a sentence
that agrees with you.

For a system whose failure mode is a family losing health coverage without anyone getting an error
message, that seemed like the right standard.

---

**Code:** [github.com/mohamedsorour1998/Grace](https://github.com/mohamedsorour1998/Grace) (MIT)
**Live demo:** [grace.rosettacloud.app](https://grace.rosettacloud.app)
**Built with:** Strands Agents SDK · Amazon Bedrock (Nova) · AgentCore Runtime + Memory · DynamoDB ·
Step Functions · Lambda · EventBridge · Cognito · Amplify

*All household data is synthetic. Unwinding figures: 94 million peak enrolment and 25 million+
coverage losses per [Medicaid.gov unwinding
resources](https://www.medicaid.gov/resources-for-states/coronavirus-disease-2019-covid-19/archived-unwinding-and-returning-regular-operations-after-covid-19)
and [KFF](https://www.kff.org/medicaid/10-things-to-know-about-the-unwinding-of-the-medicaid-continuous-enrollment-provision/);
the 69% procedural share per [JAMA Health
Forum](https://jamanetwork.com/journals/jama-health-forum/fullarticle/2825467).*
