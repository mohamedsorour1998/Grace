# Grace dashboard runbook

How the caseworker dashboard is built, run, and deployed. Every command here was executed against
account `<AWS_ACCOUNT_ID>` / `us-east-1`. Where this file disagrees with the plan, this file is right —
it records what actually happened.

---

## Preflight (2026-09-03)

### The repo

44 commits — all of Plan 2 — were pushed to `github.com/mohamedsorour1998/Grace` before Plan 3 began.
`git log origin/main..HEAD` returns 0 and the repo answers HTTP 200, so `docs/deployed-verification.md`
is publicly visible. This matters twice: a public repo is a submission requirement, and Amplify's
git-based deploy needs a reachable remote.

### The backend the dashboard reads

| Resource | Observed |
|---|---|
| Runtime `grace_grace-oTyyvo8stE` | version **2**, `READY` |
| Table `grace-cases` | `ACTIVE`, GSI `escalation-queue` present |
| Pending escalation rows | **17 rows / 3 distinct households** |

**The 17-vs-3 gap is the finding, not a discrepancy.** Every sweep appends a fresh `ESCALATION#` row,
so the GSI legitimately holds one row per case per sweep. A queue page that lists 17 entries would have
a caseworker deciding the same family repeatedly — `lib/cases.ts` must de-duplicate by case and keep
the newest row. Task 3 asserts this, and Task 6's manual check is "the queue shows exactly three".

### Package versions — the plan's first pins were wrong

Observed with `npm view <pkg> version`, and the plan was corrected to match. Recording both numbers,
because the drift is the point: research and execution were three hours apart.

| Package | Plan's first guess | Actual |
|---|---|---|
| `@aws-sdk/client-dynamodb` | 3.735.0 | **3.1125.0** |
| `@aws-sdk/client-bedrock-agentcore` | 3.735.0 | **3.1125.0** |
| `@aws-sdk/util-dynamodb` | 3.735.0 | **3.1125.0** |
| `jose` | 6.1.0 | **6.2.10** |
| `vitest` | 4.1.11 | **5.0.0** |
| `typescript` | 5.9.3 | **7.0.2** |
| `tailwindcss`, `@tailwindcss/postcss` | 4.1.18 | **4.3.3** |
| `next`, `eslint-config-next` | 16.3.4 | 16.3.4 ✓ |
| `shadcn` | 4.20.1 | 4.20.1 ✓ |
| `react`, `react-dom` | 19.2.8 | 19.2.8 ✓ |

**TypeScript 7 is a major version jump**, so the plan's `tsconfig.json` was checked against it
directly rather than assumed: a throwaway project with the exact compiler options — including
`moduleResolution: "bundler"`, `noUncheckedIndexedAccess`, and `isolatedModules` — type-checks clean
under `tsc@7.0.2`.

### Cognito and Amplify

```text
existing user pools : astrolabe-paper-auth, rosettaclaw-live-auth
existing amplify apps: (none)
CreateApp platform  : ['WEB', 'WEB_DYNAMIC', 'WEB_COMPUTE']
CreateDeployment    : available
```

Two pools already exist, which proves the Cognito API and this account's permissions work. Amplify is
**new ground here** — no apps exist yet.

**`WEB_COMPUTE` is the SSR platform.** `WEB` is a static host with no route handlers and no
middleware, so deploying onto it would silently remove the Cognito gate and the decide endpoint: the
app would build, serve, and be wrong. `CreateDeployment` matters as the fallback — connecting a GitHub
repo needs browser-based app authorization, which cannot be done unattended, so a zip deploy is the
escape hatch.

### Baseline

`.venv/bin/python -m pytest` → **622 passed**. Every later task is measured against this.

---

## Running locally

Filled in by Task 6.

## Deploying

Filled in by Task 7.
