# Agents for Humans: a test you never watched fail is a sentence that agrees with you

`<replace this text by the COVER IMAGE — suggested: a green test run and a red one side by side, over the words "The same code. The safety deleted in one of them.">`

---

My headline safety test asserted the thing my whole project rests on: that when a caseworker approves a
household missing a required document, the agent still refuses to file. It passed.

It also passed with the authority gate **deliberately bypassed**.

Not because the assertion was wrong. Because the test fake filed nothing, so a *different* code path
escalated the case for every possible input. The claim was true of that run and completely unproven by
the test. I had a green check and no evidence.

Grace is a Strands Agents entry for the AWS Agents for Humans hackathon — it watches Medicaid and SNAP
renewal deadlines, files what is unambiguous, and escalates the rest to a human. Its failure mode is a
family losing health coverage with no error message anywhere. That is the kind of system where a test
that cannot fail is worse than no test at all, because it stops you looking.

This post is four such tests, and the method that found them.

---

## 1. The safety test with an alibi

The test: approve `c-010`, the household missing `proof_of_residency`, and assert the outcome is
`escalated` with nothing filed.

The problem: three independent branches produce `escalated` on that input.

1. The gate refuses because the document is missing. ← *the one I meant to test*
2. The run reaches no outcome at all, so hard rule 6 escalates rather than claim success.
3. The case is clean but no renewal is on the ledger, so it escalates.

The fake graph filed nothing, which routes every input down branch 3. Splice in the sabotage — force
the gate's verdict to `None` — and the test **still passes**, because branch 3 catches it.

The fix was not a better assertion. It was **arming the fixture so the other branches could not
fire**: write a `renewal_submitted` row before the run, so branch 3 has no path, and the gate becomes
the only thing standing between an approval and a filing. Now the sabotage fails three tests.

> **When several code paths converge on the same observable result, asserting that result says nothing
> about which one produced it.** Remove the other branches' alibis before you trust the assertion.

---

## 2. The trap that would have disarmed itself on a future date

Same test, second defect, and this one is my favourite because it was set to expire.

The ledger row that arms the trap was stamped `2026-10-01T12:00Z`. My classification counts a filing
only *within the current run* — so that row counted as "this run" **only because that timestamp is in
the future**. On 1 October it silently stops arming anything, and the test goes on passing forever.

Worse: the assertion that checked the trap was armed called the *all-time* reading of "has this
household ever been filed", which is a different question from the one the code under test asks. So it
was **structurally incapable of noticing its own disarming**.

The fix writes the row from the fake graph's `__call__`, so it lands during the run by construction
rather than by a date, and the arming assertion asks the same run-scoped question the production code
asks.

> **A fixture whose validity depends on today's date is a test with an expiry you did not write down.**
> If a test needs a row to be "recent", make the code produce it, not the calendar.

---

## 3. The comment that vouched for a check nobody performed

My Next.js middleware carried a careful, honest-sounding docstring. It said the middleware was *"a
redirect convenience, and never the security boundary"* — a forged cookie gets past it and is then
refused by `verifySession`, *"which is the check that matters"* — and that `verifySession` *"still
refuses on every page."*

The first half was true. The second half was false. Grepping for `verifySession` matched the auth
callback and the write route. **No page verified anything.**

Measured against a real server:

```
no cookie                                  → 307 /login
Cookie: grace_session=totally.forged.token → 200, 45143 bytes,
        every case id, every escalation reason, the full headline
```

An unsigned, unparseable **literal English sentence** was a complete authentication bypass for every
read in the application.

Here is what makes this a testing lesson rather than an auth one: the sentence had been *true when it
was written*. The write route was the only consumer then. Pages grew around it and the comment kept
vouching for them.

> **Comments do not fail when the code they describe stops being true.** And this one actively
> suppressed suspicion — anyone reading it concluded the gate was somewhere else. When a comment says
> "X is verified elsewhere", grep for X.

---

## 4. The test that passed against `return []`

I built a read-only drift checker that compares deployed AWS infrastructure against the code that
provisions it. Its happy-path test:

```python
def test_no_drift_when_everything_matches():
    sfn, events, iam = _matching()
    assert check_drift(sfn, events, iam, ACCOUNT) == []
```

That passes against a `check_drift` whose entire body is `return []`.

And the fakes ignored their keyword arguments, so a **wrong ARN, a wrong rule name, or a wrong role
name would have passed all five tests** — leaving a live run against production as the only thing that
could ever catch it.

The fixed fakes record what they were *asked about*, and the test asserts all three resources were
consulted by name. It now fails against the stub.

There is a matching one in the same tool. Its "report every mismatch, not just the first" test asserted
`len(drift) == 3` — which would happily accept **the same mismatch reported three times**. It asserts
one finding per *resource* now.

> **`== []` and `== 3` are the two shapes of vacuous assertion I now grep my own tests for.** An empty
> result and a count are both satisfied by code that did nothing.

---

## The method, which is embarrassingly simple

For every guard I care about:

1. Break the line the guard protects.
2. Run the tests.
3. **Confirm the test I expected to fail is the one that failed** — not merely that something did.
4. Restore.

Step 3 is the one that earns its keep. Twice a sabotage failed a *different* test than the one that was
supposed to catch it, which told me the named guard was untested and something else was accidentally
covering the case. That is a hole you cannot see any other way.

Two hard-won details:

**A crashing sabotage scores as a survivor.** Removing a page cap entirely killed the test worker with
`SIGABRT` mid-file, and the JSON reporter then recorded **zero** failed assertions, with the file's
other tests simply absent from the report. An assertion-counting harness reads that as "nothing
failed". So weaken a bound rather than deleting it — raise the cap to a large finite number and get an
ordinary failure — and never read a green count from a run that did not complete.

**A fake that carries its own copy of a contract can drift exactly like a comment.** I hit this while
wiring AgentCore Memory: the SDK's `create_event` takes `(text, role)` tuples, my code passed
`(role, text)`, and **my test pinned the wrong order** — so the test defended the defect. Every write
would have raised before the network, been swallowed by a fail-open `except`, and returned `False`
forever with a warning nobody reads. The fix was to make the fake validate *through the real SDK
method*, so there is no second copy of the contract to drift.

---

## Was it worth it?

Fifty-one sabotages in a single task, at one point. It is slow, and I would not do it to a CRUD
endpoint.

But this system's whole value proposition is a **refusal**: it acts alone on the routine and provably
escalates the rest. A refusal you cannot demonstrate is a promise. And the specific way this project
fails is silent — Grace acting when it should have escalated produces no error, no throttle, and no
latency spike. It looks *exactly* like success. Not one standard alarm I could have configured would
have caught any of the four defects above.

So the standard I settled on:

> **If you cannot make a test fail, you do not have a test. You have a sentence that agrees with you.**

Six of the seven serious defects I found in this project shared one shape — *something asserted a
property it did not verify.* A docstring. A comment. A test. A config. An API's acceptance of my input.
A running system standing in for the code I had written.

They all read as diligence right up to the moment you check.

`<replace this text by a screenshot of a sabotage run — the named test failing, with the rest of the suite passing>`

---

**Code:** [github.com/mohamedsorour1998/Grace](https://github.com/mohamedsorour1998/Grace) (MIT)
**Live:** [grace.rosettacloud.app](https://grace.rosettacloud.app)
**Built with:** Strands Agents SDK · Amazon Bedrock (Nova) · AgentCore Runtime & Memory · DynamoDB ·
Step Functions · Lambda · EventBridge · Cognito · Amplify

*All household data in this project is synthetic.*
