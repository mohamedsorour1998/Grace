# Lessons: the live caseload, the audits, Memory, and the rule packs (Plans 4–7)

Moved verbatim from `CLAUDE.md` on 2026-10-31. Each lesson was learned on a real defect; paths and counts are as of when it was written.

## State of the project as of 2026-09-14

**Seven plans are complete.** Plan 1's 9 tasks, Plan 2's 11, Plan 3's 9, Plan 4 (sign-in + case
intake), Plan 5 (document provenance), Plan 6 (the 2026-09-08 audit fixes) and Plan 7 (the grand-prize
plan, `docs/superpowers/plans/2026-09-10-grace-grand-prize.md`, which closed four overclaims and cited
the regulations) are done. **Python tests pass** plus
**vitest tests across 10 files** in `web/`, and 23 trajectory evals pass against real Bedrock
(`.venv/bin/python -m pytest evals/` — `testpaths = ["tests"]` excludes `evals/` from the fast suite).
`grace sweep` runs end to end locally and reports **9 acted / 3 escalated**. **Re-measure these counts
before quoting them anywhere** — they have moved every plan, and the two DynamoDB invariants are the
claims that do not move.

**One hard submission requirement is still outstanding: the ≤5-minute demo video.** No task in any plan
produces it. Everything else required — public repo, README, architecture diagram, AWS Builder ID
(`mohamedsorour1998@gmail.com` — the Devpost form asks for the **email** used to create the Builder ID,
not the handle) — exists. Do not describe the submission as complete.

**The evals needed two runs to reach 23/23, and that is expected for exactly one of them.**
`test_an_escalating_case_does_something_rather_than_nothing[c-011]` is liveness, not safety — the gate
only permits or refuses a tool call, never forces one, so a model that deliberates inside the swarm and
answers from `decide` in prose fails it while every safety property holds. Re-running is the correct
response to *that* failure and the wrong response to any safety failure. **Never re-run
`test_an_escalating_case_is_never_filed` to get a pass; a failure there is a real regression.**

**Grace is deployed and the full sweep runs on AWS.** EventBridge → Step Functions (Map,
`maxConcurrency` 3) → Lambda → Runtime `grace_grace-oTyyvo8stE`. Two consecutive executions
**SUCCEEDED in ~61s reporting 9 acted / 3 escalated**, verified from DynamoDB rather than from a log
line: `renewal_submitted` appears for exactly `c-001`–`c-009` and for none of `c-010`/`c-011`/`c-012`,
and the `escalation-queue` GSI holds exactly those three households with their typed gate reasons.
Hard rule 6 holds on deployed infrastructure. `docs/evidence/deployed-verification.md` is the pasted evidence.

**The runtime is version 3 as of 2026-09-07, and the sweep reads its caseload from the table.** Both
changes exist for one reason: a household submitted through `/new` was invisible to the daily sweep,
and it was **two defects wearing one symptom** — fix either alone and the household is still never
visited. (1) The deployed image was version 2, built 2026-09-03, from code where `DynamoDBCaseStore`
could not read `RECORD#v1` rows; that landed in `0bc380c` on 2026-09-07. (2) EventBridge's target
carried a **hardcoded list of twelve case ids**, so even a runtime that could see the case would never
be handed it. The state machine now starts at `ListCases`, an `aws-sdk:dynamodb:query` over the
`CASE_DIRECTORY` partition, and `SWEEP_INPUT` is `{"today": "2026-10-01"}` and nothing else.
`infra/seed_cases.py` has been run against the live table, so the directory names all twelve.

**Neither defect failed.** Green schedule, `SUCCEEDED` executions, a correct 9/3 count — the caseload
was simply frozen at the moment `provision_eventbridge` last ran. That is the shape to watch for: when
a list of things to process is written at provisioning time, the system cannot tell you it has gone
stale. Proven on the deployed system rather than argued — the runtime was invoked on `c-900`, a case
existing **only** as a row the dashboard form wrote (it is not in `fixtures/households.yaml`), and it
escalated naming both missing documents; then the schedule's own input with no case list swept **13
outcomes, 9 acted / 4 escalated**. The probe's 519 rows were then deleted under an assertion that every
key was its own partition or its single directory row, and a final sweep on the same input returned
**12 outcomes, 9 acted / 3 escalated**, with both invariants intact.

`CheckDirectoryComplete` fails the execution if the Query returns `LastEvaluatedKey`: a truncated
directory would drop families while every count still added up. **No `ResultSelector` on `ListCases`**
— a `.$` path matching nothing raises `States.Runtime`, so selecting a key that is absent on every
healthy run would fail exactly the runs that are fine. `Choice` with `IsPresent` tolerates absence.

**Six defects were found by audit on 2026-09-08 and all seven fixes are deployed.** Three of them
shared one root cause worth naming: **a per-run claim answered from an all-time record.** A caseworker's
decision was permanent while an escalation recurs every sweep, so a decided household sat in `/queue`
forever and answered `409 already_decided` to anyone who tried again. And `renewal_filed` read the whole
ledger, so a run in which Grace did nothing still reported `acted` — which made the demo's own headline
invariant unfalsifiable: a gate that broke and filed nothing would have left every check in this repo
passing. Both are now scoped to an episode: a decision counts only if it is newer than the newest
escalation, and a filing counts only from the moment the run started.

**The two fixes that touch filing are coupled, and the coupling is the design.** `submit_renewal` now
declines to file a renewal already on file for the same certification period — but the short-circuit
**still writes a ledger row**, of kind `renewal_already_filed`, and `grace/run.py`'s `FILED_KINDS`
counts both. A *silent* short-circuit would make the second day's run look like a run that reached no
outcome, and a clean household would escalate. A run that genuinely reached no outcome writes neither
kind and still escalates, which is the property that must survive any edit here. `web/lib/cases.ts`'s
`FILED = "renewal_submitted"` is deliberately unchanged, so the dashboard still reads exactly the rows
that prove a real filing. Verified together on runtime **version 4**: one deployed sweep returned
9 acted / 3 escalated with **zero** new `renewal_submitted` rows and **nine** `renewal_already_filed`
rows — had the short-circuit been silent, all nine would have escalated.

**Two defensible numbers for one question are worse than one wrong number — and the fix is to make
the disagreement unrepresentable.** `/` reported "3 waiting on you" while `/queue` reported 2. Neither
was wrong: `/` counted every household Grace escalated, `/queue` counted the ones still undecided, and
they diverged the moment a caseworker answered something. A reader cannot audit two right answers;
they can only stop trusting the page. The fix was **not** to pick a number. `CaseSummary.awaitingDecision`
is computed once in `web/lib/cases.ts` (escalated **and** unanswered since the newest escalation),
`summarise` counts it, and `listQueue` filters on it — so both surfaces read one field and cannot drift
apart again. `escalated` is kept beside `awaiting` rather than replaced, because they answer genuinely
different questions: `escalated` is Grace's verdict about the sweep and stays 3 whatever a human does;
`awaiting` is the working state. **When two surfaces disagree, first ask whether they are answering the
same question — if they are not, name both; if they are, give them one field.**

**The demo headline survives that change untouched, and that was a design constraint rather than luck.**
With nothing answered, `awaiting == escalated`, so `/` renders `9 handled alone, 3 waiting on you.` byte
for byte — the sentence the README, the video handout, and the article all quote. The "you've answered"
clause appears only once a human has decided something. Adding the field to `CaseSummary` also made both
test fixtures a **compile error**, which is the same discipline as `case-table.tsx`'s `switch`
statements carrying no `default:`.

**`infra/verify_deployed.py` is the read-only drift check** that the deployed state machine, the
EventBridge target input, and the Step Functions IAM policy still match `infra/`. It exists because all
three were edited live on 2026-09-07 and nothing asserted they still agreed. It issues no write, so it
is safe against production at any time — a drift check nobody dares run is not a drift check.

**Scope is four AgentCore surfaces, not five** — Runtime, Memory, Identity, and the deploy harness.
Identity was un-deferred by Plan 3 Task 4, which ships the Cognito pool whose ID token is the trust
anchor for every dashboard read and the one write. **Gateway stays deferred** with its written reason
(spec §8, README). **Never describe Grace as using five.**

**And state the Identity claim narrowly, because two different things share the name.** What shipped is
a **Cognito user pool** (`grace-caseworkers` / `us-east-1_HXs3b0APR`) whose ID token `verifySession`
validates against the pool's published JWKS, issuer, audience, expiry, `token_use: "id"`, and
`custom:role === "caseworker"`. What did **not** ship is an **AgentCore Gateway JWT authorizer**
(`customJWTAuthorizer` with inbound claim rules) — the runtime is still IAM-authorised. Both statements
are honest; merging them is not. Appendix D's protections (an explicit `Deny` on
`GetWorkloadAccessTokenForUserId`, an opaque `sub` that is never a name or an email) remain enforced in
the runtime role either way.

**Two deployed facts that must not be overclaimed:**

1. Every ledger row carries a `trace_id` **key**, but its value is `NULL` in the deployed runtime, and
   **zero traces exist in the account**. Runtime injects the OTEL env vars without installing an
   in-process tracer provider. So a Transaction Search query on `grace.gate_decision` returns nothing,
   and the DynamoDB escalation queue is the demo's evidence instead. See "Runtime instruments itself is
   wrong" below.
2. A household name **did** reach CloudWatch before it was fixed (see the PII finding below). The fix
   is now deployed — runtime **version 2**, and a fresh invocation of `c-012` (the case that leaked)
   returns an escalation with no name in the payload, confirmed on a full 9/3 sweep with zero PII in
   the output. Fixing the source did not clean what was already written, so that was scanned
   separately: three fields across two pre-fix `c-012` rows in DynamoDB held the surname and were
   stripped in place on 2026-09-04 (no key, `status`, `deadline`, or `renewal_submitted` row touched;
   a 633-row scan now returns clean — `docs/evidence/live-data-findings.md`). **Pre-fix log events still
   contain the name** and cannot be unwritten; they age out with retention. So: fixed in the repo,
   fixed in the running system, fixed in durable storage, historical log events still carry it.

- Plan 2 spec: `docs/superpowers/specs/2026-09-03-grace-agentcore-design.md`
- Plan 2 tasks: `docs/superpowers/plans/2026-09-03-grace-agentcore.md`
- Deploy runbook (verified CLI flags): `docs/runbooks/deploy.md`
- Deployed evidence: `docs/evidence/deployed-verification.md`

Plan 2 task state:


| Task | State |
|---|---|
| 0 — preflight | **done** — Podman not Docker, CLI 0.28.1, CDK already bootstrapped, Transaction Search already ACTIVE |
| 1 — naming + `grace-cases` table | **done** — `infra/{naming,provision_dynamodb}.py`, 367 tests. **Two real defects in the plan's draft; read "What Plan 2 established" below** |
| 2 — `DynamoDBCaseStore` + store factory | **done** — `grace/cases/dynamo_store.py`, `grace/store_factory.py`, 428 tests. **Six defects in the plan's draft, three of them vacuous tests** |
| 3 — observability | **done** — `grace/observability.py`, 438 tests at the time. **Found a hard-rule-8 hole: "token present" is not "content redacted"** |
| 4 — Runtime entrypoint | **done** — `grace/entrypoint.py`, `grace/run.py` (rename only). The deployed path invokes the graph **once** and never resumes |
| 5 — AgentCore Memory | **done** — `grace/memory.py`, `infra/provision_memory.py`, 544 tests. Memory `grace_household_memory-TCf1SS708O` ACTIVE, 365-day expiry |
| 6 — IAM roles | **done** (out of order — no code deps) — `infra/provision_iam.py`, 489 tests. `explicitDeny` on the unverified token path verified live. **The runtime role would have denied every Nova call — see below** |
| 7 — deploy to Runtime | **done** — `Dockerfile`, `runtime_app.py`, `agentcore/`, 556 tests. **Runtime `grace_grace-oTyyvo8stE` is READY and serving**; `c-010` escalated in 9.1s with real DynamoDB rows |
| 8 — Lambda/Step Functions/EventBridge | **done** — `infra/{lambda_src,provision_lambda,provision_stepfunctions,provision_eventbridge}.py`, 578 tests. **The deployed sweep reports 9 acted / 3 escalated** |
| 9 — escalation alarm + provisioning | **done** — `infra/{provision_alarm,provision_all,teardown}.py`, 621 tests. **The alarm went OK on a real sweep's Sum=3.0** |
| 10 — deployed verification + README | **done** — `docs/evidence/deployed-verification.md`, honest README scope. **A PII scan of the deployed log groups found a household name in CloudWatch — read the PII finding below before touching `read_case`** |

Plan 1 task state, for reference:

| Task | State |
|---|---|
| 1 — rule packs + deadline math | **done** — `grace/rules/{pack,clock}.py`, 48 tests |
| 2 — case types, store, 12 fixtures | **done** — `grace/cases/{models,store}.py`, `fixtures/households.yaml`, 60 tests |
| 3 — the authority gate | **done** — `grace/authority.py`, 121 tests total. The task that matters most. |
| 4 — Nova model registry + tools | **done** — `grace/models.py`, `grace/tools/{read,action}.py`, 157 tests total |
| 5 — `AuthorityGate` + `LedgerHook` | **done** — `grace/{steering,ledger,vendored_actions}.py`, 212 tests total. Capability absence is now real enforcement, not shape. |
| 6 — Graph spine + `grace sweep` CLI | **done** — `grace/{graph,run}.py`, 285 tests total. The first runnable end-to-end path. **Read "What Task 6 established" below before touching the sweep or the swarm** |
| 7 — deliberation swarm | **done** — `grace/swarm.py`, 351 tests total. **Read "What Task 7 established" — a swarm ends when a node does not hand off, two separate defects made it collapse to one model silently, and two more (a timeout margin, a verdict-extraction order dependency) were found in review of the first two fixes** |
| 8 — trajectory evals | **done** — `evals/{test_gate_trajectory.py,README.md}`, 23 evals against real Bedrock. **Read "What Task 8 established" — `strands-agents-evals` is never installed, and the headline test was vacuous on the two most important cases until review caught it** |
| 9 — ledger/trace correlation | **done** — `grace/ledger.py`, `tests/test_ledger_trace_correlation.py`, 360 tests total. **Read "What Task 9 established" — there are two ledger writers, not one, and this is the one place fail-closed is the wrong instinct** |

`pyproject.toml`, `LICENSE`, `.gitignore`, `.env.example`, `README.md` all exist and are
committed — **do not recreate them.** Dependencies are installed in `.venv`; no install step is
needed to run tests.

## The schedule and the caseload

**Cite the EventBridge-scheduled execution precisely, and the claim has grown.** A schedule-triggered
execution is identified by its **composite execution name** (`<uuid>_<uuid>`, the shape EventBridge
generates) where manual invocations get a plain UUID.

**Re-measured 2026-09-07: the schedule has fired at 12:00 on four consecutive days** — 03, 04, 05 and
06 September — every one `SUCCEEDED`, with no human involved after the deploy. The table grew
643 → 1033 rows across those runs (~120–140 ledger rows per sweep), and **both invariants held through
every one**: `renewal_submitted` for exactly `c-001`–`c-009`, and no escalating household ever filed.

That is the strongest available version of the deployment claim, and it is worth stating as "it has run
unattended every day since deploy" rather than the older, weaker "one of the last three executions was
scheduled". Re-measure before quoting it — the count moves daily, the two booleans do not.

**Those four scheduled runs swept a frozen list, and that does not weaken the claim — say it
precisely.** Every one of them was a real unattended end-to-end run of EventBridge → Step Functions →
Lambda → Runtime → DynamoDB, and both invariants held through all four. What they did *not* prove is
that the sweep picks up a household added after provisioning, because until 2026-09-07 the caseload
came from the event rather than from the table. The first execution to sweep from the directory ran
2026-09-07 05:55 and returned 9 acted / 3 escalated. **So "it has run unattended every day since
deploy" is true, and "a case you add today is swept tomorrow" became true on 2026-09-07** — the next
scheduled fire is the first to demonstrate both at once, and it is worth re-checking before the video
rather than asserting it.

## Overclaims, Memory, and rule-pack citations

**Four overclaims were closed on 2026-09-10 and the way each was found is the transferable part.**
`README.md` listed AgentCore Memory as "Shipped" and named agents-as-tools as one of three multi-agent
patterns. `build_session_manager` had zero callers; no `@tool` wrapped an `Agent`; and `models.py`
defined `outreach` and `judge` roles that nothing called. All were found by grepping the **request
path** for the thing the documentation claimed, rather than by reading the documentation — the same
"is the thing I deployed the thing I wrote?" question that found the frozen caseload.

**`AgentCoreMemorySessionManager` cannot attach to a Graph.** `create_multi_agent` raises
`NotImplementedError("MultiAgent is not implemented for this repository")`, verified against the
installed package. `GraphBuilder.set_session_manager` *does* exist, so this is syntactically inviting
and would raise on the first sweep. `grace/memory.py`'s docstring reaches the right conclusion by the
wrong route — it cites a `ValueError` about agents inside a Graph, which is a different guard.
`grace/household_memory.py` uses the data-plane client (`create_event` / `retrieve_memories`) instead.

**`MemoryClient.create_event` takes `(text, role)` tuples, not `(role, text)`.** Its body does
`text, role = msg` then `MessageRole(role.upper())`, and its own docstring says so. The reverse order
raises `ValueError` **before the network**, is swallowed by a fail-open `except`, and returns `False`
forever with a warning nobody reads. A plan draft had it backwards *and its test pinned the wrong
order*, so the test defended the defect. The fake now validates through the real SDK method
(`object.__new__` skips `__init__`, so no boto3 client and no credentials) — **a fake that carries its
own copy of a contract drifts exactly like a comment.**

**A role defined in `models.py` and called by nothing cannot reveal a permission it does not have.**
`OUTREACH` carried a `us.` prefix while the deployed runtime policy grants `global.*`, so the drafter
worked in every local test and was **AccessDenied in production** the moment Task 1 first called it.
`judge` carried the identical wrong prefix and is still uncalled. A test now asserts every role's model
id is one the deployed policy actually grants, and
`test_every_model_role_is_referenced_by_some_module` walks the package from disk and fails on any
unreferenced role — `judge` exempted by name, with the exemption to be deleted along with the role if
LLM steering never ships.

**A SEMANTIC memory strategy extracts facts from a *conversation*, so a lone `ASSISTANT` note
extracts nothing.** `remember_outcome` wrote one statement per outcome, the events were well-formed,
the strategies were `ACTIVE` with the right namespaces — and `retrieve_memories` returned `()` for two
days. The fix is to write the **pair**: a `USER` turn asking what happened to this case, and the
`ASSISTANT` turn answering. Same information; a shape the extractor can attribute a fact to.
Extraction then takes ~1-2 minutes, so recall immediately after a sweep may legitimately be empty.

**And the way I nearly missed it is the transferable part: I changed one variable and read the result
as conclusive.** The right hypothesis — "an ASSISTANT-only note may not be extractable" — was tested
with a *trivial* `USER`+`ASSISTANT` pair ("hello"/"hi"), which returned `()` too, so I recorded the
hypothesis as eliminated and wrote a whole verification section attributing the gap to the service.
Two things differed between probe and production (message shape **and** content substance) and I
varied only one. **When an experiment appears to eliminate a hypothesis, list what else differs
between the probe and the real case before believing it.**

**An unset `GRACE_MEMORY_ID` and a genuinely empty memory produce byte-identical output.** After the
fix, `recall_facts` returned `()` while the raw API showed records present — the code was right and my
shell had no id, so it took the degraded path it is designed for. An hour went to a defect that did
not exist. `tests/test_household_memory.py` pins the ambiguity as a known property and asserts the
deployed manifest names an id at all, because a wired read path with nothing configured is the same
silent no-op one layer up. **Before diagnosing "no facts", check that the reader was told where to
look.**

**Three rule-pack numbers are federally mandated and the other ten are not, and saying so is the
point.** `42 CFR 435.916(a)(1)` mandates the 12-month Medicaid renewal cycle; `(a)(3)(iii)` gives a
household **90 days** to submit a late renewal form after a procedural termination "without requiring a
new application" — the window Grace exists to work inside. And `7 CFR 273.14(e)(2)` gives a SNAP
household **30 days** from a notice of expiration to file for recertification. Everything else says
`policy choice` with its reasoning. The sharpest entry is negative: SNAP's immaterial-income band is a
*percentage* and `7 CFR 273.12(a)(1)(i)(A)` sets a *dollar* threshold, so a percentage band has no
federal basis at all.

**And the third one was found by a reviewer catching a false negative claim, which is the harder
defect to see.** The SNAP pack originally said "SNAP has no reconsideration provision equivalent to
42 CFR 435.916(a)(3)(iii), so a household that misses recertification generally has to reapply" and
marked its 30-day grace period `policy choice`. Verified against the regulation text: 273.14(e)(2)
does mandate a 30-day filing window, so the pack was **denying a statutory protection a household
actually has** — and understating Grace's own legal grounding while doing it. **A wrong citation is
worse than none, and a wrong claim that something is *uncited* is worse still**: it reads as
diligence, so nobody re-checks it.
