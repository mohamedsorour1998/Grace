# Dashboard verification

Evidence that the caseworker dashboard's claims are real, gathered against account `339712964409` /
`us-east-1` on **2026-09-04**. Command output is pasted verbatim, including the parts that did not turn
out the way the plan expected.

This is Plan 3's counterpart to [`deployed-verification.md`](deployed-verification.md), which covers the
unattended sweep. Read this one as the answer to "can a human actually act on an escalation, and is the
gate still in charge when they do?"

The headline result is a **negative** one, and it is the point of the whole plan: a caseworker approved
`c-010`, and Grace filed nothing, because the document is still missing.

**Row counts are measurements with a date, not constants.** The `grace-cases` table has moved
633 → 643 → 651 → 663 over two days of sweeps and probes. The two properties that do not move are the
ones asserted below: `renewal_submitted` exists for exactly the nine clean households, and for none of
the three escalating ones.

---

## 1. The decision path is still untouched

Three plans and two deployed surfaces later, the four files that decide whether a family keeps their
coverage are byte-identical to the commit where Plan 1 finished (`0e9de29`).

```console
$ git diff --stat 0e9de29 -- grace/authority.py grace/steering.py grace/graph.py grace/swarm.py
$
```

**Empty output.** The dashboard reads the gate's verdicts and re-invokes the agent; it does not touch
the gate, the steering handler, the graph spine, or the deliberation swarm. Everything Plan 3 added is
additive: `web/`, `infra/provision_cognito.py`, `infra/provision_amplify.py`, and one wording-only
parameter in `grace/entrypoint.py`.

---

## 2. The deployed dashboard, with a real Cognito ID token

`https://grace.rosettacloud.app` — Amplify app `dbi97xicbjbv8`.

```console
$ aws amplify get-app --app-id dbi97xicbjbv8 --region us-east-1 \
    --query 'app.{name:name,platform:platform,defaultDomain:defaultDomain}'
{
    "name": "grace-dashboard",
    "platform": "WEB_COMPUTE",
    "defaultDomain": "dbi97xicbjbv8.amplifyapp.com"
}
```

`WEB_COMPUTE`, not `WEB`. On `WEB` the platform serves static output only: there would be no route
handlers and no middleware, so the Cognito gate and the decide endpoint would simply not exist while
the build still went green.

The token below is a genuine ID token from the `grace-caseworkers` pool, carrying
`token_use: "id"`, `custom:role: "caseworker"`, an opaque UUID `sub`, and **no email**.

```console
$ curl -s -o /dev/null -w 'status=%{http_code}\n' -H "Cookie: grace_session=$TOK" \
    https://grace.rosettacloud.app/
status=200      45142 bytes

$ ... /queue
status=200      19104 bytes

$ ... /case/c-010
status=200      80758 bytes
```

What the deployed markup actually contains:

```console
$ # the headline on /
<span class="text-acted">9<!-- --> handled alone</span><span class="text-muted">, </span>
<span class="text-escalate">3<!-- --> waiting on you</span><span class="text-muted">.</span>

$ # case ids on /
c-001 c-002 c-003 c-004 c-005 c-006 c-007 c-008 c-009 c-010 c-011 c-012
count distinct: 12

$ # case ids on /queue
c-010 c-011 c-012

$ # /case/c-010
missing_document ... proof_of_residency
```

**Twelve households on `/`, nine acted and three escalated, and exactly the three escalating households
on `/queue`** — de-duplicated by the reader from 20 `ESCALATION#` rows in the GSI, because each sweep
appends a fresh row and the *distinct case set* is the claim rather than the row count.

`9 handled alone` is not read from a status field. `listCases` requires the ledger row that proves a
filing: a household with no pending escalation **and** no `renewal_submitted` row renders as `error`,
not as `acted`. "Not escalated" is not the same claim as "Grace filed it."

---

## 3. The approval that files nothing — the whole safety argument, executed

This is the claim the dashboard exists to support. `c-010` is the household missing
`proof_of_residency`. A caseworker signs in and approves it anyway.

**Baseline, measured first**, so that any row afterwards is attributable:

```console
total rows: 651
DECISION# rows: 0
renewal_submitted cases: ['c-001', 'c-002', 'c-003', 'c-004', 'c-005', 'c-006', 'c-007', 'c-008', 'c-009']
escalation-row cases: ['c-010', 'c-011', 'c-012']
sk prefixes: {'LEDGER': 632, 'ESCALATION': 19}
```

The approval, through the app's own route:

```console
$ curl -X POST -H "Cookie: grace_session=$TOK" -H 'Content-Type: application/json' \
    -d '{"decision":"approve","note":"Task 8 verification: caseworker approves; the gate must still refuse because the document is absent."}' \
    https://grace.rosettacloud.app/api/case/c-010/decide

{"recorded":true,"caseId":"c-010","decision":"approve",
 "graceOutcome":"Grace re-checked and did not file. missing_document: proof_of_residency is not on file (Grace has already messaged the family.)",
 "filed":false}
status=200                                                             9.2s
```

**That response is the agent's own claim, so it is confirmed from the store** — hard rule 6 applied to
the verification itself:

```console
$ aws dynamodb query --table-name grace-cases --region us-east-1 \
    --key-condition-expression 'pk = :p' \
    --expression-attribute-values '{":p":{"S":"CASE#c-010"}}' \
    --query 'Items[?starts_with(sk.S, `DECISION#`)].[sk.S,decision.S,decided_by.S,outcome.S]' --output table

| DECISION#2026-09-04T04:06:23.393Z         | approve | 2448a4e8-c021-70f6-382c-e8acbb6cc956 | None |
| DECISION#2026-09-04T04:06:23.393Z#outcome | None    | None                                 | Grace re-checked and did not file. missing_document: proof_of_residency is not on file (Grace has already messaged the family.) |

$ ... --query 'length(Items[?kind.S==`renewal_submitted`])'
0
```

**Zero `renewal_submitted` rows for `c-010`.** A human approved, Grace re-checked, the gate refused
again, and the document is still missing.

Three details in those two rows are load-bearing:

- **`decided_by` is the opaque Cognito `sub`**, never a name or an email. Same rule as the JWT `sub`.
- **The human's row and Grace's outcome row are separate**, joined by their shared `decided_at`
  timestamp. The outcome row carries no `decision` attribute, which is how `readCase` tells Grace's own
  row from a human's — under a naive `DECISION#` prefix test the outcome row would surface as a phantom
  deny attributed to nobody.
- **The decision row is written *before* the invocation.** If the runtime call fails, the record that a
  human decided still exists. This inverts `action.py`'s ordering, where a ledger row must never claim
  an action that did not happen; here the durable fact *is* the human's decision, not the filing.

The re-invocation's own ledger shows what Grace did instead of filing:

```console
c-010 LEDGER#2026-09-04T04:06:28.258017+00:00#000001 | tool_call
c-010 LEDGER#2026-09-04T04:06:28.293330+00:00#000002 | tool_result
c-010 LEDGER#2026-09-04T04:06:28.993737+00:00#000003 | tool_call
c-010 LEDGER#2026-09-04T04:06:29.000310+00:00#000004 | tool_result
c-010 LEDGER#2026-09-04T04:06:29.813524+00:00#000005 | tool_call
c-010 LEDGER#2026-09-04T04:06:29.821074+00:00#000006 | tool_result
c-010 LEDGER#2026-09-04T04:06:30.856401+00:00#000007 | tool_call
c-010 LEDGER#2026-09-04T04:06:30.863616+00:00#000008 | family_message_sent
c-010 LEDGER#2026-09-04T04:06:30.869737+00:00#000009 | tool_result
c-010 ESCALATION#2026-09-04T04:06:32.078174+00:00
```

Reads, then `family_message_sent`, then a fresh escalation row. Never `renewal_submitted`.

### Why an approval cannot reach the gate even by mistake

The guarantee is **structural**, not a checked condition:

```console
$ .venv/bin/python -c "import inspect; from grace.authority import evaluate; print(inspect.signature(evaluate))"
evaluate(case: 'Case', today: 'date', pack: 'RulePack | None' = None) -> 'GateResult'
```

`evaluate` has **no parameter an approval could occupy**. `caseworker_approved` reaches
`grace/entrypoint.py`'s `_escalate` and does exactly one thing — appends a sentence to the reason text a
human reads, *after* the verdict is already final. It reaches no gate, no tool, and no graph.

**And the dashboard never resumes a paused graph.** Resuming with any truthy response *approves* the
blocked tool — Plan 1 measured `"needs review"` resuming a graph and filing a renewal for a household
missing a required document. So `web/` carries no resume vocabulary at all;
`interruptResponse`, `APPROVE_DECISIONS`, and `MAX_RESUME_ROUNDS` appear only inside guard tests that
assert their absence. An approval becomes a durable row plus a fresh invocation, so the gate
re-evaluates the **case facts**, which have not changed.

### `c-011` was deliberately not approved

The plan's Step 2 asks for a second approval on `c-011` (material income change), noting that Grace
filing there would be a legitimate outcome. **It was not run.** `c-011` is one of the three households
whose un-filed state is the demo's central evidence, and a filing would consume it irreversibly for a
claim already proven by `c-010`. The safety property under test — an approval is an input to the gate,
never a bypass — is established by the case where the gate *must* refuse.

---

## 4. Every refusal, and each one is a real refusal

The dashboard's gate is `verifySession` on every page and on the write route. Middleware only checks
that a cookie is *present*; it never verifies it, and it says so in its own docstring.

```console
$ curl ... https://grace.rosettacloud.app/           # no cookie at all
status=307 redirect=https://grace.rosettacloud.app/login

$ curl -H "Cookie: grace_session=<forged RS256 with custom:role=caseworker>" ... /
status=307 redirect=https://grace.rosettacloud.app/login
case ids leaked in forged response: (none)
```

A forged cookie carrying the right claims is refused because the **signature** does not verify against
the pool's published JWKS. It leaks **zero** case ids — the redirect happens before any read.

The write route, which is the one that can change something:

```console
$ POST /api/case/c-010/decide   forged cookie
{"error":"no_session","message":"Sign in to decide a case."}                     status=401

$ POST /api/case/c-010/decide   valid session, {"decision":"Escalate."}
{"error":"unknown_decision","message":"Choose approve or deny."}                status=400

$ POST /api/case/c-099/decide   valid session, unknown case
{"error":"unknown_case","message":"No such case."}                              status=404

$ POST /api/case/c-001/decide   valid session, a case Grace handled itself
{"error":"not_escalated","message":"Grace handled this case itself; there is nothing to decide."}
                                                                                status=409
```

Two of these are sharper than they look:

- **`"Escalate."` is refused, not interpreted.** `authorize` uses an **allowlist** — only an exact
  `"approve"` or `"deny"` is a decision. Plan 1 measured the opposite polarity failing: against a
  denylist of escalate-words, `"Escalate."` with one trailing period *resumed a graph and filed a
  renewal*. The unrecognised answer must be the safe one.
- **The refusal order puts session checks before fact checks.** A forged cookie on a nonexistent case
  gets `no_session`, not `unknown_case` — otherwise the difference between the two codes would tell an
  unauthenticated caller which case ids exist.

All four refused, and the table proves they refused: **zero `DECISION#` rows existed before the one
approval in §3**, and afterwards exactly the two rows that approval wrote.

---

## 5. No household identity on any new surface

Hard rule 9, checked on all three surfaces Plan 3 added — the deployed markup, the decision rows, and
the table as a whole. All twelve fixture surnames, the reserved phone range, and `@` for emails:

```console
=== c-010's rows, including the new decision rows ===
rows scanned: 84
PII in c-010's rows: NONE
emails ('@') in c-010's rows: False
phone '+1555' in c-010's rows: False

=== every row in the table ===
total rows scanned: 663
PII anywhere in table: NONE
'+1555' anywhere in table: False
'@' anywhere in table: False

=== the deployed markup ===
/tmp/g_root.html         45080 bytes  names=NONE  +1555=False  'Household '=False
/tmp/g_queue.html        19092 bytes  names=NONE  +1555=False  'Household '=False
/tmp/g_c010.html         80744 bytes  names=NONE  +1555=False  'Household '=False
/tmp/g_forged.html        6411 bytes  names=NONE  +1555=False  'Household '=False
total bytes scanned: 151333
```

The caseworker's free-text note is the one new path by which text a human typed reaches a stored row.
It is **not** transformed on the way to the screen: React escapes text children already, and escaping
first would render `The family's wage record is stale.` as `The family&#39;s wage record is stale.` to
the caseworker. `noteIsInert` is a *check*, not a rewrite — nothing edits the caseworker's words.

**A scanner that matches nothing reports `NONE` too**, so the guard in `web/__tests__` feeds a name in
through `reason` — the exact path that reached CloudWatch in Plan 2 — and asserts the scanner catches
it. All twelve surnames are listed, including `Fitzgerald` and `Yamamoto`, the two households most
likely to carry a name in an escalation reason.

### What this does not clean up

**Log events written before Plan 2's fix still contain one household surname** and cannot be unwritten;
they age out with the log group's retention. The fix is deployed (runtime **version 2**), durable
storage was scanned and stripped, and the scan above returns clean — but "no household identity reaches
CloudWatch" is a claim about the running system and about new events, not about history.

---

## 6. The row delta, fully attributed

Between the baseline in §3 and the final scan, the table went **651 → 663**. Every one of the twelve new
rows is accounted for, all on `c-010`, all written by the single approval:

```console
by sk prefix: {'LEDGER': 641, 'ESCALATION': 20, 'DECISION': 2}
  (baseline:  {'LEDGER': 632, 'ESCALATION': 19, 'DECISION': 0} = 651)

rows with an sk timestamp inside the probe window (2026-09-04T04:0x UTC): 12
  by case:   {'c-010': 12}
  by prefix: {'DECISION': 2, 'ESCALATION': 1, 'LEDGER': 9}
```

Two decision rows, nine ledger rows from the re-invocation, one fresh escalation row. **No pre-existing
row was modified or deleted**, and nothing was written to any of the other eleven households.

The invariants after the write are the same as before it:

```console
renewal_submitted cases: ['c-001' ... 'c-009']  count: 9
  c-010/011/012 filed?: NONE — boundary holds
escalation cases: ['c-010', 'c-011', 'c-012']
```

---

## 7. Supporting deployed state

```console
$ aws cognito-idp describe-user-pool --user-pool-id us-east-1_HXs3b0APR --region us-east-1
{
    "Name": "grace-caseworkers",
    "AllowAdminCreateUserOnly": true,
    "Id": "us-east-1_HXs3b0APR"
}
```

**Self-signup is off.** Nobody on the internet can register an account and reach the decide endpoint;
caseworkers are admin-created.

```console
$ aws bedrock-agentcore-control list-agent-runtimes --region us-east-1
{ "name": "grace_grace", "version": "2", "status": "READY" }

$ aws cloudwatch describe-alarms --alarm-names grace-escalations-below-expected --region us-east-1
{ "state": "OK", "threshold": 3.0, "op": "LessThanThreshold", "missing": "breaching" }
```

The alarm fires when Grace escalates **fewer** than three cases — the direction that costs a family
their coverage. Acting when it should have escalated produces no error, no throttle, and no latency
spike, so an error-rate alarm would stay green through exactly that failure.

### The unattended sweep still runs on its own schedule

```text
state machine: grace-sweep
last 3 executions: all SUCCEEDED (2026-09-03 07:46, 12:00, 17:16 +03:00)
most recent outcome: {'acted': 9, 'escalated': 3}
```

**One of those three was EventBridge-scheduled rather than manual**, identified by its composite
execution name (`2e23be00-cb47-53d3-47fb-cf8046b73b78_114eec7a-…`), which is the shape EventBridge
generates; manual invocations get plain UUIDs. The daily automation genuinely fires on its own.

---

## 8. What does not work, carried forward

**CloudWatch traces still do not exist.** Re-verified today, after the dashboard shipped:

```console
$ aws logs describe-log-groups --region us-east-1 --log-group-name-prefix "aws/spans" --query 'logGroups'
[]

$ aws xray get-trace-summaries --region us-east-1 --start-time <-3h> --end-time <now> \
    --query '{TraceCount: length(TraceSummaries)}'
{
    "TraceCount": 0
}

$ # d_trace_id on the ledger rows the approval just wrote
[ { "NULL": true }, { "NULL": true }, { "NULL": true }, ... ]
```

Every ledger row carries the `trace_id` **key** with the value `NULL`, including the nine rows written
minutes ago. AgentCore Runtime injects the OTEL environment variables and creates a log group but does
not install an in-process tracer provider, and the packages that would fill that gap are ones this
project deliberately refuses. **So a Transaction Search query on `grace.gate_decision = "escalate"`
returns nothing, and that claim is not made anywhere.** The DynamoDB escalation queue is the evidence
instead — the stronger artifact anyway, since a trace can be dropped by sampling and a ledger row
cannot. Full reasoning in
[`deployed-verification.md` §4](deployed-verification.md#4-transaction-search-returns-nothing-and-why).

**SMS is still sandboxed.** `TEXT_MESSAGE_MONTHLY_SPEND_LIMIT` has `MaxLimit: 1` and there are zero
origination numbers, so `TranscriptChannel` is the always-works path and no demo depends on SMS
delivery. Note that `family_message_sent` in §3's ledger is a transcript write, not a delivered text.

**AgentCore Gateway is still deferred**, so the honest surface count is four, not five: Runtime, Memory,
Identity, and the deploy harness.

**AgentCore Identity means one specific thing here.** What shipped is a **Cognito user pool whose ID
token is the dashboard's trust anchor**. What did **not** ship is an **AgentCore Gateway JWT authorizer**
(`customJWTAuthorizer` with inbound claim rules) — the runtime is still IAM-authorised. Both are honest;
conflating them is not.

---

## 9. The gates

```console
$ .venv/bin/python -m pytest
715 passed, 2 warnings in 26.93s

$ cd web && npm run typecheck        # tsc --noEmit, clean
$ npm run lint                        # eslint ., clean
$ npm run test
 Test Files  7 passed (7)
      Tests  157 passed (157)
$ npm run build
✓ Compiled successfully in 572ms
Route (app)
┌ ƒ /
├ ○ /_not-found
├ ƒ /api/auth/callback
├ ƒ /api/case/[id]/decide
├ ƒ /case/[id]
├ ƒ /login
└ ƒ /queue
ƒ Proxy (Middleware)
```

**715 Python tests, 157 vitest tests across 7 files, four green gates.** Five of the seven routes are
`ƒ` (server-rendered on demand) and middleware is present — the shape a `WEB_COMPUTE` app must have and
a static export could not.

`npm run build` is the gate that matters, because it is what Amplify runs. A green `typecheck` and
`test` with a failing `build` is not a deployable app.

The 23 trajectory evals were **not** re-run — they cost real Bedrock invocations and nothing in Plan 3
touches the decision path (§1). Confirmed they still collect:

```console
$ .venv/bin/python -m pytest evals/ --co -q
evals/test_gate_trajectory.py: 23
```

---

## 10. Still outstanding for submission

**The ≤5-minute demo video does not exist.** No task in Plan 3 produces it, and it is a hard submission
requirement. Nothing in this document should be read as saying the submission is complete.

The **AWS Builder ID** is recorded in the README as a `TODO`, not a value — it is an account identifier
that cannot be derived from the repository or the AWS API.

Present and verified: public repo (`github.com/mohamedsorour1998/Grace`, MIT), README, architecture
diagram ([`architecture.md`](architecture.md) as Mermaid plus
[`architecture.png`](architecture.png)), and a live deployment.
