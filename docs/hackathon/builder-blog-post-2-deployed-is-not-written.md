# Agents for Humans: the defect that was invisible because nothing failed

`<replace this text by the COVER IMAGE — suggested: a green Step Functions execution graph beside the sentence "SUCCEEDED. 9 acted / 3 escalated. And one household was never visited.">`

---

I built a dashboard form that lets a caseworker add a household to my agent's caseload. I wrote the
code that makes a submitted household visible to the agent. I tested it. I shipped it.

It did not work in production for three days, and **nothing failed**.

The daily schedule stayed green. Every Step Functions execution reported `SUCCEEDED`. The headline
count — nine households handled autonomously, three escalated to a human — stayed exactly right,
because the twelve households it knew about were all still behaving correctly. A submitted household
was simply never visited. No error, no alarm, no log line.

This is a post about how I found that, and about the shape of bug it belongs to, because I think that
shape is the single most dangerous thing in a deployed agent.

Grace is a Strands Agents entry for the AWS Agents for Humans hackathon: it watches Medicaid and SNAP
renewal deadlines, files the renewals that are unambiguous, and escalates the rest to a human
caseworker with a typed reason. It runs unattended on Amazon Bedrock AgentCore Runtime, driven by
EventBridge and Step Functions.

---

## Two defects wearing one symptom

The symptom was singular: a household submitted through `/new` rendered on the dashboard and was never
swept. The cause was two independent defects, and **fixing either one alone leaves the household
invisible** — which is why I found the first, congratulated myself, and did not notice the problem was
still there.

**Defect one: the container was older than the code.** Making a submitted case visible to the agent
was a change to the case store — the agent's view of "which households exist" had to come from
DynamoDB rather than from a YAML file compiled into the image. I made that change and it was correct.
The deployed container was still an image built three days earlier, from code where the store could not
read the new record rows.

So the property was true of the repository and false of the running system. Every test passed. The
tests were testing the code, and the code was not what was running.

**Defect two: the orchestrator carried a frozen list.** After redeploying, the sweep still could not
have seen a new household — because the EventBridge rule's target input was this:

```json
{
  "case_ids": ["c-001", "c-002", "c-003", "c-004", "c-005", "c-006",
               "c-007", "c-008", "c-009", "c-010", "c-011", "c-012"],
  "today": "2026-10-01"
}
```

Twelve case ids, written into the schedule at the moment I last ran the provisioning script. The
caseload was not "whatever is in the table". It was "whatever was in the table in early September".

---

## Why neither defect could report itself

Here is the property that makes this class of bug worth a whole post.

**A frozen list cannot tell you it has gone stale.** There is no error condition. The Map state
iterates twelve items and gets twelve successes. The count is right. The invariant I check on every
sweep — a renewal filed for exactly the nine clean households and none of the three escalating ones —
is *also* right, because those twelve are all fine. Every signal I had built said the system was
healthy, and every one of them was telling the truth about the twelve households it knew about.

The same is true of the stale image. A container that is three days old does not announce it. It serves
requests, returns 200, and reports the outcome its own older code computes.

Both defects are in the class of **things a monitor cannot detect because they are absences**. My
CloudWatch alarm watches whether the escalation count drops below three — which was a good instinct,
because the failure I most fear is Grace *acting* when it should have escalated, and that produces no
error, no throttle, and no latency spike. But an alarm on the households it processes cannot see a
household it never processed.

---

## How I actually found it

Not by reading a log. By asking a question no dashboard answers:

> **Is the thing I deployed the thing I wrote?**

That decomposes into two concrete checks, both of which take about a minute:

```bash
# 1. Compare the running artifact's build time against the commit that added the behaviour.
aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id <id> \
  --query '{version:agentRuntimeVersion,built:lastUpdatedAt}'
git log -1 --format='%h %ad' -S'the_function_that_should_be_running' -- path/to/file.py

# 2. Read the orchestrator's actual input payload. Not the code that generates it — the deployed value.
aws events list-targets-by-rule --rule <rule> --query 'Targets[0].Input'
```

The first told me the image predated the store change. The second printed the twelve hardcoded ids.
Neither required a debugger, a trace, or a reproduction. Both required *suspecting the deployment
rather than the code*, which is a habit rather than a technique.

---

## The fix, and the guard that makes it hold

The state machine now begins by asking the table:

```
ListCases  (aws-sdk:dynamodb:query over the CASE_DIRECTORY partition)
   → CheckDirectoryComplete  (Choice)
        → DirectoryTruncated (Fail)   if the response carries LastEvaluatedKey
        → SweepCases (Map over the query's own Items)
```

and the scheduled event carries only `{"today": "2026-10-01"}`. The caseload is a property of the
table, not of when I last ran a script.

Two details in there are the actually interesting engineering.

**`CheckDirectoryComplete` fails the execution rather than sweeping a partial caseload.** A DynamoDB
Query caps at 1 MB and signals more with `LastEvaluatedKey`. If I ignored that, a large enough
directory would silently drop households — and every count would still add up, which is precisely the
failure mode I had just spent three days on. Failing loudly is the only honest option: a sweep that
processed 80% of families and reported success is worse than one that refused.

**There is deliberately no `ResultSelector` on `ListCases`.** My first version selected
`LastEvaluatedKey` out of the response so the Choice state could read it directly. That raises
`States.Runtime` when the path matches nothing — and `LastEvaluatedKey` is *absent on every healthy
run*. I would have built a guard that fails exactly the runs that are fine. The whole response lands in
the state instead, and `Choice` asks about the key with `IsPresent`, which tolerates absence.

That is a small thing with a general form worth carrying: **when you write a check for an abnormal
condition, confirm the check is inert under normal conditions.** Mine was not, and the way I found out
was that the first sweep after "fixing" it failed.

---

## The drift check, and why it must be safe to run

Three pieces of infrastructure got edited live that week: the state machine definition, the
EventBridge target input, and the Step Functions IAM policy. All three matched the code in `infra/`
afterwards. **Nothing asserted that they did.**

So there is now a read-only checker that compares the deployed state machine definition, the
schedule's target input, and the role policy against exactly what the provisioning modules produce:

```bash
$ python -m infra.verify_deployed
no drift: the state machine definition, the schedule's target input, and the Step Functions role
policy match this repository. The runtime container image version is NOT checked — compare it
separately before trusting that deployed code matches this repo.
```

Three design decisions in that tool, each of which came from a mistake:

1. **It issues no write of any kind.** A drift check nobody dares run against production is not a
   drift check. That sentence is the whole reason it is read-only.
2. **It reports every mismatch, not the first.** An operator who fixes one, re-runs, and hits the next
   learns the same lesson three times. The test that pins this asserts one finding *per resource*,
   not a count of three — because "three findings" would accept the same mismatch reported three
   times.
3. **Its success message names what it did not check.** The runtime image version is the very thing
   that was stale for three days, and it is not in scope. A tool that said "no drift" full stop would
   be making the exact overclaim it exists to catch.

There was also a defect in the checker's own test, which is a fitting way for this to end. Its
"everything matches" case asserted `check_drift(...) == []` — and that **passes against a function
whose body is `return []`**. Worse, the fakes swallowed their keyword arguments, so a wrong ARN or a
wrong role name would have passed all five tests, leaving the live run as the only thing that could
catch it. The fakes now record what they were *asked about*, and the test asserts all three resources
were consulted by name.

---

## The transferable part

**A caseload, a container image, and an orchestrator's input list are three different places your
system's idea of "what to process" can live.** They can disagree, they will not tell you when they do,
and every dashboard you own will stay green while they do.

So: a claim about your agent is only as deployed as the last of them. When someone tells me a feature
works, the question I now ask is not "did you test it" but **"is the thing you deployed the thing you
wrote?"** — and I have started asking it of my own work first.

Verified on the deployed system rather than argued, which is the only way I trust any of this now. A
household submitted through the form, existing solely as a row the form wrote and absent from the
fixture file, was invoked directly on the runtime and escalated naming both of its missing documents.
Then a sweep started with the schedule's own input returned **13 outcomes, 9 acted / 4 escalated**. Its
rows were removed and the same input returned **12 outcomes, 9 acted / 3 escalated**, with the two
invariants intact.

`<replace this text by a screenshot of the Step Functions execution graph showing ListCases → CheckDirectoryComplete → SweepCases>`

---

**Code:** [github.com/mohamedsorour1998/Grace](https://github.com/mohamedsorour1998/Grace) (MIT)
**Live:** [grace.rosettacloud.app](https://grace.rosettacloud.app)
**Built with:** Strands Agents SDK · Amazon Bedrock (Nova) · AgentCore Runtime & Memory · DynamoDB ·
Step Functions · Lambda · EventBridge · Cognito · Amplify

*All household data in this project is synthetic.*
