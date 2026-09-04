# Agents for Humans: the seven times my agent was confidently wrong

*Building Grace, an agent that keeps families from losing health coverage to paperwork — on Strands
Agents, Amazon Bedrock, and AgentCore.*

> **Publish this on [builder.aws.com](https://builder.aws.com).** The title above already contains
> "Agents for Humans", which the hackathon requires. Replace each
> `<replace this text by a screenshot of …>` marker with a real screenshot before publishing.

---

## The problem is not that people stop qualifying

During Medicaid unwinding, **most disenrollments were procedural**. Not people who became ineligible —
people who missed a letter, a deadline, or one document. They lost coverage to paperwork.

That failure is silent. Nobody gets an error. A renewal simply does not happen, and a family finds out
at a pharmacy counter.

So I built **Grace**: an agent that watches every household's renewal clock, files the renewals that
are unambiguous, chases the one missing document by text, and wakes a human caseworker **only** when
eligibility is genuinely in doubt. On twelve synthetic households it handles nine alone and escalates
three, each with a typed reason.

`<replace this text by a screenshot of the dashboard at grace.rosettacloud.app showing "9 handled alone" and "3 waiting on you">`

The interesting part of building it was not the agent. It was that **seven times, something was
confidently wrong in a way that looked exactly like working.** Every one of those is the real content
of this post, because each is a trap the next person will hit.

---

## The one idea: an escalation boundary

Grace's defining property is that it acts alone on the routine and *provably* escalates the rest.
Three layers, strongest first:

1. **Capability absence.** The tool that files a renewal is not registered in the agent's tool list at
   all for a case that has not passed verification. It cannot do the wrong thing because the ability
   does not exist. *This beats any instruction, because there is nothing to disobey.*
2. **Identity from the session, never the conversation.** Every household-scoped read tool takes
   **zero arguments** — the case is bound at construction from the authenticated session. A prompt
   injection cannot redirect Grace to another family because there is no parameter to poison.
3. **A deterministic gate.** Pure Python, no model, no I/O, mapping case facts to *act* or *escalate*.
   Any error during verification escalates. Fail closed.

Hold onto layer 1. It comes back later as the fix for something completely different.

---

## 1. A model that files a renewal it was told not to file

Early on, the gated role ran on Nova Lite. Under test it **filed a renewal it had been explicitly
instructed not to file.**

Not a prompt engineering problem. A design problem. If the correctness of a benefits filing depends on
a model obeying an instruction, the system is wrong regardless of which model you pick. That is what
pushed the gate out of the prompt and into `grace/authority.py` — pure Python, exhaustively table-tested,
that the model cannot argue with.

The deliberation swarm that runs on genuinely ambiguous cases uses **three different Nova models** —
advocate, verifier, referee — because two instances of the same model agreeing proves nothing, and
nothing should referee its own argument.

---

## 2. The check that fired on 0 of 3 cases

A `GraphResult` has no `stop_reason` field; only single-agent results do. My sweep checked
`getattr(result, "stop_reason", None) == "interrupt"` to detect an escalation. That is *always* `False`
on a graph — so the escalation branch never executed and **every case was reported as handled
autonomously.**

Nothing failed. No exception, no warning. The demo would have claimed 12/0 instead of 9/3.

I found it by reading the SDK's source rather than its documentation, which is now a rule I follow:
`strands-agents` moves fast, and I have a table of six places where the published docs disagree with
the installed code.

---

## 3. A denylist that made the *unrecognised* answer the dangerous one

When a caseworker's decision resumed a paused agent, the resume response was checked against a list of
words meaning "escalate". Then I measured what actually happens.

The SDK's steering handler does `can_proceed = event.interrupt(...)` and cancels the tool only
`if not can_proceed`. **Any non-empty string is truthy.** Confirmed against the real executor:
`"Escalate."` with a trailing period, `"no, hold this one"`, and `"needs review"` all resumed the graph
and **filed a renewal for a household missing a required document.**

Two fixes. The immediate one: an *allowlist* — resume only on an exact match to `approve`, `yes`,
`file`, `proceed`; everything else, including anything unrecognised, denies. The better one: the
dashboard **does not resume at all.** It records the decision and re-invokes so the gate re-evaluates
from scratch.

**The polarity that fails closed is always "act only on an exact affirmative", never "refuse only on a
known negative."**

---

## 4. "The API accepted my config" is not "the control works"

Grace's caseworkers live in a Cognito user pool. I omitted `WriteAttributes` on the app client and wrote
a confident comment: *capability absence — the client cannot rewrite the claim that authorises it.*

Then I probed it properly, on a throwaway pool with two custom attributes:

```
WriteAttributes omitted → write an ungranted MUTABLE attribute   → SUCCEEDED
WriteAttributes omitted → write the IMMUTABLE role attribute     → InvalidParameterException:
                                                    "Attribute cannot be updated."
WriteAttributes ["other"] → write the excluded attribute          → NotAuthorizedException:
                                       "A client attempted to write unauthorized attribute"
```

**Omitting it grants every attribute** — the AWS docs say so outright. My protected attribute survived
only because its schema marked it immutable. I had claimed two guards and shipped one, with the comment
asserting the opposite.

What makes this worth writing down is *why it was easy to miss*: probing the protected attribute alone
**does** produce a refusal. It just comes from the immutability check, not from permissions. Reading
that as evidence confirms the wrong mechanism.

**To verify a control, perform the action it should prevent — against a target where only that control
can refuse.**

---

## 5. A docstring that vouched for a check nobody performed

This is the one that would have shipped a real vulnerability.

My Next.js middleware had a careful docstring: *"a redirect convenience, and never the security
boundary — a forged cookie gets past it and is then refused by `verifySession`, which is the check that
matters"*, and that `verifySession` *"still refuses on every page and on the decide route."*

The second half was **false**. `grep verifySession app/` matched the auth callback and the write route.
**No page verified anything.** Measured against a real server:

```
no cookie                                  → 307 /login
Cookie: grace_session=totally.forged.token → 200, 45143 bytes,
        every case id, every escalation reason, the full headline
```

An unsigned, unparseable **literal sentence** was a complete authentication bypass for every read.

The sentence was *true when written* — the write route was the only consumer then. Pages grew around it
and the comment kept vouching for them. **Comments do not fail when the code they describe stops being
true**, and this one actively suppressed suspicion: anyone reading it concluded the gate was elsewhere.

Fixed with a `requireSession()` that calls `redirect()` — which *throws*, so there is no falsy return a
caller can forget to check — called before any page touches the data layer.

**When a comment says "X is checked elsewhere", grep for X.**

`<replace this text by a screenshot of the /login redirect, or of the requireSession function>`

---

## 6. Five deploy defects that each survived a green build

Deploying to Amplify took seven builds. Builds that reported **SUCCEED** still served 500s, and builds
that failed described themselves wrongly.

The best of them: `serverExternalPackages` listed the AWS SDK, on the sound reasoning that server-only
packages should not enter the client bundle. Right reasoning, wrong mechanism — marking a package
external emits a bare `require` and **omits it from the bundle**, and Amplify's SSR bundle ships only
what the trace includes. Every page returned 500 **with a valid session**: sign-in worked, then the
first import failed with `Cannot find module '@aws-sdk/client-dynamodb-3e32f4e24bb075d4'` — naming a
module nobody ever published, because Turbopack appends a content hash to an external's name.

It was protecting nothing. No client chunk referenced the SDK anyway.

The others, briefly, because each cost a build:

- **Amplify environment variables never reach the SSR runtime.** Documented as intentional. Bridge them
  by writing `.env.production` in the buildspec *before* the build.
- **`update_app(environmentVariables=…)` is a full replace**, and the console writes its own keys into
  the same map. A stale-read write deleted the monorepo app root; the next build died at clone time
  with `Cannot read 'next' version in package.json` — which reads like a packaging problem, not a
  deleted variable.
- **A monorepo app root and a flat buildspec are mutually exclusive**, and both failure modes appear at
  clone time.
- **Amplify rejects any environment variable starting with `AWS`** — reserved for the credentials the
  execution role injects. And two keys I typed by hand carried whitespace, which reads as *absent* to
  an exact-key lookup. A loader can trim values; it can never trim a malformed key.

**A green build is a statement about compilation, not about a running request.** Probe the deployed app
at request time, with a real session, and assert on *rendered content* — not on HTTP 200.

`<replace this text by a screenshot of the Amplify build history showing the succeeded deploy>`

---

## 7. A safety test that passed with the safety removed

The headline test asserted that approving `c-010` — a household missing a required document — still
escalates. It passed. It also passed with the gate **deliberately bypassed**.

The fake filed nothing, so a *different* branch ("clean case, no renewal filed") escalated the case for
every input. The claim was true of the run and unproven by the test.

The fix was to arm the fixture so the other branch could not fire: pre-write the filing row, giving the
sabotaged code a real path to "acted". Now the gate is the only thing between the approval and a wrong
outcome, and the sabotage fails three tests.

**When several code paths converge on the same observable result, asserting that result says nothing
about which path produced it.** Ask what *else* could produce this output, then remove its alibi.

This is why every guard in Grace was sabotaged and watched failing — 51 sabotages in one task alone.
A related trap: a sabotage that *crashes* the test runner records **zero** failed assertions and scores
as a survivor. Weaken a bound rather than removing it.

---

## What it actually does now

```
EventBridge (daily) → Step Functions → Lambda → AgentCore Runtime
                                                    │
                          AgentCore Memory (per-household facts) ┤
                          DynamoDB (the case ledger)             ┤
                          Family channel (SMS / transcript)      ┘

Caseworker → Cognito → Amplify SSR dashboard → DynamoDB (reads)
                                             └→ InvokeAgentRuntime (a decision)
```

The browser **never** reaches the agent. A decision is recorded server-side, then Grace is re-invoked
so the gate re-evaluates.

Deadline math is a **tool, not an agent** — deterministic work does not need a model. The three-model
deliberation runs **only** on ambiguous cases, so the nine clean households never pay for it.

And the claim I care most about, executed on live infrastructure: a caseworker approved the household
missing a document, and Grace **filed nothing**, recording *"Grace re-checked and did not file.
missing_document: proof_of_residency is not on file."* A human said yes and the gate still said no.

**A human's approval can make Grace more cautious. It can never make it less.**

`<replace this text by a screenshot of c-010's outcome row showing "Grace re-checked and did not file">`

---

## What does not work, stated plainly

Three things, because a submission that hides them is worse than one that names them:

- **CloudWatch trace correlation is unavailable.** Every ledger row carries a `trace_id` key whose
  value is `NULL`. AgentCore Runtime injects the OTEL environment variables but does not install an
  in-process tracer provider, and zero spans exist in the account. The fix requires a package this
  project deliberately refuses. The DynamoDB ledger is the evidence instead — and a ledger row cannot
  be dropped by sampling, which a span can.
- **SMS is not delivered.** The account is sandboxed with zero origination numbers, so the family
  channel writes a transcript. The demo never depends on SMS.
- **One household name reached CloudWatch before it was fixed.** A tool returned a display name, a
  model quoted it into its reasoning, that text became an escalation reason, and the reason was logged
  as a Step Functions payload — a path span redaction does not cover. Fixed at the source by removing
  the field entirely (**layer 1 again**: nothing to leak because nothing is returned), stripped from
  durable storage, and confirmed clean across every row. Historical log events cannot be unwritten;
  they age out with retention.

---

## The through-line

Six of those seven failures shared one shape: **something asserted a property it did not verify.** A
docstring, a comment, a test, a config, an API's acceptance of my input.

The discipline that caught them is not clever. It is: *perform the action the guard should prevent, and
watch the guard refuse.* If you cannot make a test fail, you do not have a test — you have a sentence
that agrees with you.

**Code:** [github.com/mohamedsorour1998/Grace](https://github.com/mohamedsorour1998/Grace) · MIT
**Live:** [grace.rosettacloud.app](https://grace.rosettacloud.app)
**Built with:** Strands Agents SDK, Amazon Bedrock (Nova), AgentCore Runtime + Memory, DynamoDB,
Step Functions, Lambda, EventBridge, Cognito, Amplify
