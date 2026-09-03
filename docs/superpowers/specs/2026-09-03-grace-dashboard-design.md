# Grace Plan 3 — Caseworker Dashboard Design

**Date:** 2026-09-03
**Status:** approved, ready for implementation planning
**Predecessors:** Plan 1 (local agent, 9 tasks) and Plan 2 (AgentCore deploy, 11 tasks) both complete
**Deadline pressure:** 11 days to 2026-09-14 17:00 PT, with the architecture diagram and the ≤5-minute demo video still outstanding alongside this

---

## 1. What this plan is

Plan 3 builds the surface a caseworker actually touches: a Next.js dashboard that shows what Grace
did, shows the three cases it refused to decide, and lets a human resolve one — with Grace acting on
that decision afterwards.

It is the half of the escalation boundary Plans 1 and 2 could not demonstrate. The gate provably
refuses; nothing so far shows what happens next.

**Nothing in `grace/` changes except by addition.** The decision path — `authority.py`,
`steering.py`, `graph.py`, `swarm.py` — stays byte-identical, as it did through all of Plan 2. That
is a checkable exit criterion, not an aspiration.

### 1.1 Decisions taken before design

| Decision | Chosen | Rejected |
|---|---|---|
| Scope | Dashboard **plus** caseworker approve/deny | read-only; docs-and-video only |
| How approval takes effect | **Never resume a paused graph** — record the decision, re-run the gate | resume with an allowlist; record-only, Grace never files |
| Auth | **Amazon Cognito** | Better Auth (no DynamoDB adapter, and this account has no RDS) |
| UI | **Next.js + shadcn/ui + Tailwind 4** | hand-rolled CSS |
| Rendering & hosting | **SSR on Amplify Hosting** (`WEB_COMPUTE`), server route handlers | static export + API Gateway/Lambda |

### 1.2 Why "never resume" is the safety-critical choice

Plan 1's Task 6 established, against the real tool executor, that resuming an interrupt with **any
truthy response approves the blocked tool**. `"Escalate."` (one trailing period), `"no, hold this
one"`, and `"needs review"` each resumed a paused graph and **filed a renewal for `c-010`** — a
household missing a required document. `run.py` guards the attended CLI with an exact-match
allowlist; Plan 2 removed the resume path from the deployed entrypoint entirely, which is *stronger*
because a path that cannot resume cannot be talked into filing.

A dashboard approve button is exactly the pressure that would reintroduce it. So it does not:

```text
POST /api/case/c-011/decide  {"decision": "approve", "note": "..."}
   │
   ├─→ Cognito session verified server-side          (no session → 401, nothing written)
   │
   ├─→ authorize(session, caseFacts, attempt)        pure, no I/O, returns Permit | Refusal
   │
   ├─→ write DECISION#<ts> row                        who, when, what, why — durable first
   │
   └─→ invoke_agent_runtime {case_id, caseworker_approved: true}
          │
          └─→ the authority gate re-evaluates THE CASE FACTS
                 still escalate?  → nothing filed, the refusal is recorded
                 now clean?       → files, and the ledger proves it
```

The gate remains the only thing that can permit a filing. A caseworker's approval is an *input* to
the decision, never a bypass of it. Concretely: approving `c-010` (missing `proof_of_residency`) must
still not file, because the document is still missing — and that is a test, not a hope.

This also means **no session persistence across processes**, no `MAX_RESUME_ROUNDS`, and no
`interruptResponse` anywhere in the dashboard's code path. Plan 2's structural test that asserts
that vocabulary is absent from `grace/entrypoint.py` stays valid and gains a sibling.

---

## 2. Architecture

```text
browser
  │  (no AWS credentials, ever)
  ▼
Amplify Hosting — platform WEB_COMPUTE (SSR)
  │
  ├── middleware: Cognito session required on every route
  │
  └── Next.js route handlers, server-side, under the Amplify app role
        │
        ├── reads  → DynamoDB grace-cases   (ledger, escalation-queue GSI, decisions)
        └── writes → DECISION# row, then invoke_agent_runtime on grace_grace-oTyyvo8stE
```

Two properties this shape exists for. **No AWS credential ever reaches the browser** — every read
and the one write happen in a route handler. And **the auth gate is server-side**, so it is testable
without a browser and cannot be bypassed by calling the API directly.

### 2.1 Pages

| Route | Shows |
|---|---|
| `/` | The sweep: 9 acted, 3 escalated, last execution time. The headline claim, rendered. |
| `/queue` | `PENDING_CASEWORKER` from the GSI, oldest deadline first — the actual work list |
| `/case/[id]` | One household: gate reason, deadline, full ledger, decision history, approve/deny |
| `/api/case/[id]/decide` | The one write. POST only, session-gated. |

### 2.2 New files

```text
web/
  app/
    layout.tsx                    shell, fonts, theme
    page.tsx                      sweep summary
    (routes)/queue/page.tsx       the escalation queue
    (routes)/case/[id]/page.tsx   one case
    api/case/[id]/decide/route.ts the write
  lib/
    authorize.ts                  PURE. session + facts → Permit | Refusal. No I/O.
    cases.ts                      reads grace-cases. The only DynamoDB reader.
    decide.ts                     the write path: row, then runtime invoke
    cognito.ts                    session verification
    types.ts                      shared shapes
  components/ui/                  shadcn primitives
  middleware.ts                   session required
  __tests__/                      vitest, offline
infra/
  provision_cognito.py            user pool, client, one caseworker
  provision_amplify.py            app (WEB_COMPUTE), branch, build spec
```

`lib/authorize.ts` is pure with no I/O, and `lib/cases.ts` measures the facts it decides over. That
split is deliberate and mirrors `grace/authority.py` (pure) against `grace/steering.py` (adapter) —
it means every refusal is testable with no AWS and no browser, and a route physically cannot hand
`authorize` a fact it did not measure. The pattern is knowledge reused from a prior project of the
maintainer's (see §7); no code is copied.

---

## 3. What is already verified

Checked against the live account on 2026-09-03, so the plan does not rest on assumption:

- **Cognito works here** — two pools already exist in this account (`astrolabe-paper-auth`,
  `rosettaclaw-live-auth`), so the API and permissions are proven.
- **Amplify supports SSR** — `CreateApp`'s `platform` enum is `WEB | WEB_DYNAMIC | WEB_COMPUTE`;
  `WEB_COMPUTE` is the SSR option. No Amplify apps exist yet, so this is new ground in this account.
- **Amplify can deploy without repo OAuth** — `CreateDeployment` / `StartDeployment` accept a zip.
  That matters because connecting a GitHub repo requires browser-based app authorization, which
  cannot be done unattended. Zip deploy is the fallback.
- **The stack versions exist**: Next 16.3.4, shadcn 4.20.1, Cognito via boto3.
- **The repo is public** (`github.com/mohamedsorour1998/Grace`, HTTP 200) **but 42 commits are
  unpushed**, including all of Plan 2. That is both a submission requirement and a prerequisite for
  git-based Amplify deploy, so it is Task 0.
- **Grace's deployed backend is healthy**: runtime `grace_grace-oTyyvo8stE` v2 READY, `grace-cases`
  ACTIVE with PITR, alarm OK, and a fourth consecutive deployed sweep returned 9 acted / 3
  escalated in 62s with hard rule 6 holding in DynamoDB.

### 3.1 Cognito, and the honest scope claim

Adding Cognito means AgentCore **Identity** stops being deferred: Plan 2's Appendix D researched the
`customJWTAuthorizer` with `customClaims: role = caseworker`, and this is where it lands. So Grace
can claim **four** surfaces — Runtime, Memory, Identity, harness — not three, and not five. Gateway
stays deferred with its written reason.

Two constraints carried from that research, both binding:

- **Never `GetWorkloadAccessTokenForUserId`.** It treats the user id as an opaque string with no
  verification, so an authenticated caseworker could obtain a token scoped to any household. The
  runtime role already carries an explicit `Deny` on it — verified `explicitDeny` by the IAM
  simulator — and that stays.
- **The JWT `sub` must be opaque.** Inbound JWT claims are logged to CloudTrail, which is outside
  every redaction Grace has. So no name and no email in `sub`, and the decision row records that
  opaque id — never a caseworker's name. Same rule as hard rule 9 for households.

**One documentation change this forces, and it must not be made early.** `CLAUDE.md:50` currently
reads *"Scope is three AgentCore surfaces, not five … Identity are deferred … Never describe Grace as
using five."* That is **true today** and becomes false only when Cognito is actually provisioned and
the dashboard is actually gated on it. So the count moves from three to four **in the task that ships
Identity, after it is verified working** — not in advance, and never to five. If Plan 3 is cut short
before Cognito lands, the three-surface claim stands unchanged and correct.

---

## 4. Data model additions

One new row kind in the existing `grace-cases` table. No new table.

```text
PK              SK                       purpose
CASE#c-011      DECISION#<iso8601>       a caseworker's decision, durable
```

Carrying `decided_by` (the opaque Cognito `sub`), `decided_at`, `decision` (`approve` | `deny`),
`note` (free text the caseworker typed), and `outcome` — what Grace did *afterwards*, written once
the re-invocation returns.

**The row is written before the runtime is invoked, not after.** If the invocation fails, the record
that a human decided still exists; the alternative loses the human's work on an infrastructure
error. This is the same ordering `action.py` uses for the opposite reason — there, the ledger row
comes *after* the tool returns, because a row claiming an unconfirmed action is worse than no row
(hard rule 6). Here the row claims only that a human decided, which is true the moment they did.

`note` is caseworker free text and reaches DynamoDB. It is escaped at render time, never trusted,
and never fed to a model — the same posture `authority.py` takes for `source_conflicts`.

---

## 5. Testing

Three layers, and the first is non-negotiable.

1. **Plan 1 and 2's 622 tests keep passing, unchanged.** If the dashboard requires editing the
   decision path, the design is wrong. Verified with `git diff` against the Plan 1 completion commit.
2. **`lib/authorize.ts` is table-tested exhaustively, offline** — no session, expired session, wrong
   role, unknown case, a case that is not escalated, a case already decided, and the permitted case.
   This is the dashboard's equivalent of `authority.py`, and it is tested first and hardest.
3. **The write path is proven to be gated**, by test rather than by inspection: a POST with no
   session must return 401 **and** write nothing. Both halves — a refusal that still wrote a row
   would be the whole point missed.

Then two end-to-end confirmations that cost real Bedrock, run once each:

- **Approving `c-011` (material income change) files nothing on its own** unless the gate agrees, and
  whatever happens is visible in the ledger and the decision row.
- **Approving `c-010` (missing document) must still not file.** The document is still missing. This
  is the test that proves the gate was not bypassed, and it is the one to run in the demo video.

Two lessons carried forward from the prior plans, both of which cost real time when they were
learned: **prove a new test fails against the code it targets** (Plan 2 found three vacuous tests I
had written), and **assert that a parametrized loop actually ran** (`assert checked == n`).

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| **Amplify SSR for Next 16 is new ground in this account** | `WEB_COMPUTE` verified as a platform value; zip deploy verified as a fallback to repo OAuth. Amplify is the **last** task, so a fight with the build spec cannot cost the dashboard. |
| A public page that can file renewals | The auth gate is a blocking requirement with its own tests. Nothing is deployed publicly until a session-less POST is proven to write nothing. |
| 11 days, with diagram and video outstanding | Dashboard tasks are ordered so a working local demo exists early; the live link is the only thing that gets cut. |
| Cognito hosted UI vs custom sign-in | Hosted UI first — it is one API call and no forms to build. A custom page is a later refinement, not a requirement. |
| Free text from a caseworker reaching storage | Escaped at render, never sent to a model, treated exactly like `source_conflicts`. |
| Scope creep into Gateway | Gateway stays deferred with its Plan 2 reason. Four surfaces, stated honestly. |

---

## 7. Newly-created-work disclosure

Per the hackathon rule, no code is copied from the maintainer's prior projects. Two were read for
approach only, and the reuse is disclosed here and in the README:

- **RosettaCloud** — a working shadcn 4 + Tailwind 4 + Next 15 configuration (`base-nova` style, RSC,
  lucide icons, `@theme` tokens in `globals.css`). It is a static export with no auth and no API
  routes, so only the UI-stack configuration is relevant.
- **TheAgentOrg/web** — app structure (`app/(routes)`, `app/api/*/route.ts`) and, more valuably, the
  discipline of a pure `authz.decide()` separated from the file that measures the facts. It uses
  NextAuth with a Postgres adapter, which Cognito replaces here.

Everything in `web/` is written for Grace.

---

## 8. Out of scope, with reasons

| Deferred | Why |
|---|---|
| **AgentCore Gateway** | Unchanged from Plan 2: the largest remaining chunk, and the most common deploy-day failure. The `target___tool` prefix fix stays tested in `steering.py` regardless. |
| **Real SMS** | The account is sandboxed — `MaxLimit: 1`, zero origination numbers, and Egypt sender-ID registration needs a letter of authorization, company registration, and a tax card. `TranscriptChannel` remains the always-works path; the demo never depends on SMS. |
| **Multi-tenant caseworkers** | One pool, one role claim. Per-office scoping is real product work and demonstrates nothing extra here. |
| **Live-updating UI** | Server-rendered on request. Polling or streaming adds moving parts for a sweep that runs daily. |
| **The reflection loop** (spec §3.8) | Still the originality differentiator, still additive. It needs deployed outcomes to reflect on, which now exist — a candidate if the dashboard lands early. |

---

## 9. Open items

1. **Amplify domain** — the default `*.amplifyapp.com` domain is fine; a custom domain is not worth
   DNS work on day 11.
2. **Whether to seed a second caseworker account** for the video, to show `decided_by` differing
   across rows. Cheap, and only if time allows.
