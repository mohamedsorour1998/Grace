# Grace Caseworker Dashboard Implementation Plan (Plan 3 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the surface a caseworker touches — a Next.js dashboard that shows what Grace did, lists the three cases it refused to decide, and lets a human resolve one, with Grace acting on that decision afterwards through the same authority gate.

**Architecture:** Next.js 16 App Router with **server** route handlers on Amplify Hosting (`WEB_COMPUTE`). The browser never holds AWS credentials: every DynamoDB read and the single write run server-side under the Amplify app role, behind a Cognito session check. Authorization is a **pure function with no I/O** (`lib/authorize.ts`) separated from the file that measures the facts it decides over (`lib/cases.ts`) — the same split as `grace/authority.py` against `grace/steering.py`.

**Tech Stack:** Next.js 16.3.4, React 19, TypeScript 5, Tailwind 4, shadcn/ui 4 (`base-nova`, lucide), vitest, Amazon Cognito, Amplify Hosting, `@aws-sdk/client-dynamodb` + `@aws-sdk/client-bedrock-agentcore`, and `boto3` for `infra/` provisioning.

**Spec:** `docs/superpowers/specs/2026-09-03-grace-dashboard-design.md` — read it before Task 1. Plans 1 and 2 (`docs/superpowers/plans/2026-08-28-grace-core.md`, `docs/superpowers/plans/2026-09-03-grace-agentcore.md`) carry the findings this plan rests on, and `CLAUDE.md`'s hard rules are binding throughout.

---

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the spec and CLAUDE.md.

- **Plans 1 and 2's 622 Python tests must pass, unchanged, at every commit.** Run `.venv/bin/python -m pytest`. The dashboard is additive; if it requires editing the decision path, the design is wrong — **stop and report**.
- **`grace/authority.py`, `grace/steering.py`, `grace/graph.py`, `grace/swarm.py` stay byte-identical** to the Plan 1 completion commit `0e9de29`. Verified with `git diff --stat 0e9de29 -- <those four>` returning empty.
- **Approving never resumes a paused graph.** Plan 1's Task 6 proved any truthy resume response *approves* the blocked tool — `"needs review"` filed a renewal for `c-010`, a household missing a required document. The words `interruptResponse`, `APPROVE_DECISIONS`, and `MAX_RESUME_ROUNDS` must appear nowhere in `web/`.
- **The authority gate stays the only thing that can permit a filing.** A caseworker's approval is an input to the gate's decision, never a bypass of it.
- **No AWS credential ever reaches the browser.** All reads and the write happen in server route handlers. No `NEXT_PUBLIC_` variable may carry a key, a table name, or a runtime ARN.
- **Never put household identity in any surface a model or a log can reach** (hard rule 9, as widened in Plan 2). `case_id` yes; name, phone, address never. The dashboard renders `case_id` and the gate's typed reason.
- **The Cognito JWT `sub` must be opaque** — never a name or email. Inbound JWT claims are logged to CloudTrail, which is outside every redaction Grace has. Decision rows record that opaque `sub`.
- **Never `GetWorkloadAccessTokenForUserId`.** The runtime role's explicit `Deny` on it stays; it is verified `explicitDeny` by the IAM simulator.
- **All household data is synthetic.** Phone numbers use the reserved `+1555` range.
- **Caseworker free text is untrusted.** Escape at render, never send it to a model — the same posture `authority.py` takes for `source_conflicts`.
- **Region `us-east-1`, account `<AWS_ACCOUNT_ID>`.** Resource names are `grace-*`.
- **Deployed backend, already live — do not recreate:** runtime `grace_grace-oTyyvo8stE` (v2, READY), table `grace-cases` (ACTIVE, PITR on, GSI `escalation-queue`), memory `grace_household_memory-TCf1SS708O`, Lambda `grace-invoke-case`, state machine `grace-sweep`, rule `grace-daily-sweep`, alarm `grace-escalations-below-expected`.
- **Verify SDKs and CLIs by introspection, not documentation.** Both prior plans found the docs wrong in ways that mattered.
- **Conventional commits** (`feat:`, `test:`, `fix:`, `docs:`, `chore:`). Commit after each task and tick its checkboxes.

---

## File Structure

**The dashboard** — one responsibility per file, so a reviewer can reject one without unpicking the rest:

```text
web/
  package.json                      pinned deps; scripts dev/build/lint/typecheck/test
  next.config.ts                    SSR (NO output:"export" — that has no route handlers)
  tsconfig.json                     strict, @/* path alias
  postcss.config.mjs                @tailwindcss/postcss
  components.json                   shadcn: base-nova, rsc true, lucide
  middleware.ts                     Cognito session required on every route
  app/
    globals.css                     Tailwind 4 + @theme tokens
    layout.tsx                      shell, nav, fonts
    page.tsx                        the sweep: 9 acted / 3 escalated
    queue/page.tsx                  PENDING_CASEWORKER, soonest deadline first
    case/[id]/page.tsx              one household: reason, deadline, ledger, decisions, form
    login/page.tsx                  redirect to the Cognito hosted UI
    api/auth/callback/route.ts      OAuth code -> session cookie
    api/case/[id]/decide/route.ts   THE ONE WRITE. POST only, session-gated.
  lib/
    types.ts                        CaseSummary, LedgerRow, Decision, SessionIdentity, CaseFacts
    authorize.ts                    PURE. no imports that do I/O. -> Permit | Refusal
    cases.ts                        the only DynamoDB reader
    decide.ts                       the write path: decision row, then runtime invoke
    cognito.ts                      session verification (JWT signature, issuer, expiry, role)
    env.ts                          server-only config, fails loudly if a name is missing
  components/
    ui/                             shadcn primitives (button, card, badge, table, ...)
    case-table.tsx                  the sweep/queue table
    decision-form.tsx              approve/deny + note
  __tests__/
    authorize.test.ts               exhaustive, offline — the hardest-tested file
    decide.test.ts                  the write path against fakes
    cognito.test.ts                 session verification, including the refusals
    route-guard.test.ts             a session-less POST returns 401 AND writes nothing
```

**Infrastructure** — idempotent `boto3`, matching Plan 2's `infra/` convention:

```text
infra/
  provision_cognito.py              user pool, app client, hosted-UI domain, one caseworker
  provision_amplify.py              Amplify app (WEB_COMPUTE), branch, build spec, env vars
tests/
  test_infra_cognito.py             policy/shape assertions, offline
  test_infra_amplify.py             build spec + platform assertions, offline
```

**Docs:**

```text
docs/architecture.md                the diagram source (Mermaid) + the rendered asset
docs/dashboard-runbook.md           how to run locally and how it was deployed
```

### Why these boundaries

`lib/authorize.ts` is **pure** — given a session, the case's facts, and the attempted decision, it
returns a `Permit` or a `Refusal` and touches nothing. `lib/cases.ts` measures the facts it decides
over. That split is the whole reason every refusal is testable with no AWS and no browser, and it
means a route physically cannot hand `authorize` a fact it did not measure. It mirrors
`grace/authority.py` (pure, no `boto3`, no `strands`) against `grace/steering.py` (the adapter), which
is already this codebase's native discipline.

`lib/env.ts` exists so a missing table name fails at startup with a readable message rather than as
an `undefined` in an SDK call three layers down — the same reasoning as Plan 2's `GRACE_STORE`
allowlist, which raises on an unrecognized value including blank.

`api/case/[id]/decide/route.ts` is the only write in the application. One file, so "what can this
dashboard change?" has a one-file answer.

---
## Task 0: Preflight — verify the ground before building on it

Both prior plans found the ground different from the documentation. This task writes no application
code and creates no AWS resources.

**Files:**
- Create: `docs/dashboard-runbook.md` (the preflight section; later tasks append)

**Interfaces:**
- Consumes: nothing
- Produces: a verified environment and a recorded set of real API shapes. No code artifacts.

- [x] **Step 1: Confirm the repo is public and fully pushed**

```bash
git log origin/main..HEAD --oneline | wc -l          # must be 0
curl -s -o /dev/null -w "%{http_code}\n" https://github.com/mohamedsorour1998/Grace
```

Expected: `0` unpushed commits and `200`. Already done before this plan was written — 44 commits
including all of Plan 2 were pushed, and `docs/deployed-verification.md` is publicly visible. Recorded
here because Amplify's git-based deploy depends on it and because a public repo is a submission
requirement.

- [x] **Step 2: Confirm the deployed backend the dashboard reads from**

```bash
export AWS_PAGER=""
aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id grace_grace-oTyyvo8stE \
  --region us-east-1 --query '{v:agentRuntimeVersion,s:status}'
aws dynamodb describe-table --table-name grace-cases --region us-east-1 \
  --query '{status:Table.TableStatus,gsi:Table.GlobalSecondaryIndexes[0].IndexName}'
aws dynamodb query --table-name grace-cases --region us-east-1 --index-name escalation-queue \
  --key-condition-expression '#s = :s' \
  --expression-attribute-names '{"#s":"status"}' \
  --expression-attribute-values '{":s":{"S":"PENDING_CASEWORKER"}}' \
  --query 'length(Items)'
```

Expected: runtime `{"v":"2","s":"READY"}`, table `ACTIVE` with GSI `escalation-queue`, and a non-zero
count of pending rows. **If the queue is empty the dashboard has nothing to show** — run a sweep
first (Plan 2's `provision_stepfunctions.CASE_IDS`), because a dashboard built against an empty table
cannot be distinguished from a broken reader.

- [x] **Step 3: Confirm the Node toolchain and the exact package versions**

```bash
node --version    # v24.19.0 observed
npm --version     # 11.17.0 observed
npm view next version           # 16.3.4 observed
npm view shadcn version         # 4.20.1 observed
npm view @aws-sdk/client-dynamodb version
npm view @aws-sdk/client-bedrock-agentcore version
```

Record the observed versions in the runbook and **pin them** in `package.json`. Both prior plans were
bitten by version drift between research and execution.

- [x] **Step 4: Confirm Cognito and Amplify are usable in this account**

```bash
aws cognito-idp list-user-pools --max-results 20 --region us-east-1 \
  --query 'UserPools[].Name'
aws amplify list-apps --region us-east-1 --query 'apps[].name'
```

Expected: two existing pools (`astrolabe-paper-auth`, `rosettaclaw-live-auth`) — which prove the
Cognito API and the permissions work — and an **empty** Amplify list, so Amplify is new ground here.

Then confirm the SSR platform value exists, because a static platform has no route handlers and would
silently break the whole design:

```bash
.venv/bin/python -c "
import boto3
c = boto3.client('amplify', region_name='us-east-1')
sh = c.meta.service_model.operation_model('CreateApp').input_shape
p = sh.members['platform']
print('platform enum:', c.meta.service_model.shape_for(p.name).enum)
print('has CreateDeployment (zip fallback):',
      'CreateDeployment' in c.meta.service_model.operation_names)
"
```

Expected: `['WEB', 'WEB_DYNAMIC', 'WEB_COMPUTE']` and `True`. **`WEB_COMPUTE` is the SSR platform.**
`CreateDeployment` matters as the fallback: connecting a GitHub repo needs browser-based app
authorization, which cannot be done unattended, so a zip deploy is the escape hatch.

- [x] **Step 5: Confirm the Python suite baseline**

Run: `.venv/bin/python -m pytest`
Expected: **622 passed**. Every later task is measured against this; if it is not 622, something is
wrong before Plan 3 began.

- [x] **Step 6: Write the preflight section of the runbook**

Create `docs/dashboard-runbook.md` with a `## Preflight` section recording: the observed versions, the
Cognito and Amplify findings, the `WEB_COMPUTE` platform value, the zip-deploy fallback, and the
pending-queue count. Later tasks append the local-run and deploy sequences.

- [x] **Step 7: Commit**

```bash
git add docs/dashboard-runbook.md
git commit -m "docs: Plan 3 preflight — verified versions, Cognito, and Amplify SSR platform"
```

---

## Task 1: Scaffold `web/` with the UI stack, and prove it builds

Nothing here talks to AWS. The deliverable is a Next.js app that type-checks, lints, tests, and
builds — so that every later task starts from a green baseline rather than debugging tooling and
logic at once.

**Files:**
- Create: `web/package.json`, `web/next.config.ts`, `web/tsconfig.json`, `web/postcss.config.mjs`, `web/components.json`, `web/app/globals.css`, `web/app/layout.tsx`, `web/app/page.tsx`, `web/lib/types.ts`, `web/vitest.config.mts`
- Test: `web/__tests__/smoke.test.ts`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `web/lib/types.ts` exporting exactly these shapes, used by every later task:
    ```ts
    export type CaseStatus = "acted" | "escalated" | "error";
    export interface CaseSummary { caseId: string; status: CaseStatus; program: string; deadline: string; reason: string | null; filed: boolean; }
    export interface LedgerRow { at: string; kind: string; detail: Record<string, string | number | boolean | null>; }
    export interface Decision { decidedAt: string; decidedBy: string; decision: "approve" | "deny"; note: string; outcome: string | null; }
    export interface CaseDetail { summary: CaseSummary; ledger: LedgerRow[]; decisions: Decision[]; }
    export interface SessionIdentity { sub: string; role: string; expiresAt: number; }
    ```
  - npm scripts `dev`, `build`, `lint`, `typecheck`, `test`

- [x] **Step 1: Create the app with pinned dependencies**

`web/package.json` — versions are the ones Task 0 observed; pin them exactly rather than using `^`:

```json
{
  "name": "grace-dashboard",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "eslint .",
    "typecheck": "tsc --noEmit",
    "test": "vitest run"
  },
  "dependencies": {
    "@aws-sdk/client-bedrock-agentcore": "3.1125.0",
    "@aws-sdk/client-dynamodb": "3.1125.0",
    "@aws-sdk/util-dynamodb": "3.996.9",
    "class-variance-authority": "0.7.1",
    "clsx": "2.1.1",
    "jose": "6.2.10",
    "lucide-react": "1.30.0",
    "next": "16.3.4",
    "react": "19.2.8",
    "react-dom": "19.2.8",
    "tailwind-merge": "3.6.0"
  },
  "devDependencies": {
    "@tailwindcss/postcss": "4.3.3",
    "@types/node": "24.13.3",
    "@types/react": "19.2.18",
    "@types/react-dom": "19.2.5",
    "eslint": "9.39.5",
    "eslint-config-next": "16.3.4",
    "tailwindcss": "4.3.3",
    "typescript": "6.0.3",
    "vitest": "5.0.0"
  }
}
```

**If `npm install` reports that a pinned version does not exist, use the version Task 0 observed and
record the change in the runbook — do not silently switch to a range.** `jose` is for verifying the
Cognito JWT signature in Task 4; it has no AWS dependency and runs in the Next runtime.

Then:

```bash
cd web && npm install --no-audit --no-fund
```

- [x] **Step 2: Configure Next for SSR, not static export**

`web/next.config.ts`:

```ts
import type { NextConfig } from "next";

// NO `output: "export"`. A static export has no route handlers and no
// middleware, so the Cognito gate and the decide endpoint could not exist —
// the browser would have to hold AWS credentials to read anything. Amplify
// hosts this on the WEB_COMPUTE platform for that reason.
const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Server-only packages must not be bundled into the client graph.
  serverExternalPackages: [
    "@aws-sdk/client-dynamodb",
    "@aws-sdk/client-bedrock-agentcore",
  ],
};

export default nextConfig;
```

`web/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "ES2022"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```

`noUncheckedIndexedAccess` is deliberate: this app indexes into DynamoDB attribute maps constantly,
and it forces those accesses to be checked rather than assumed present.

- [x] **Step 3: Tailwind 4 and the shadcn config**

`web/postcss.config.mjs`:

```js
// Named, not an anonymous object literal: the Next lint config enables
// `import/no-anonymous-default-export`, which warns on a bare `export default {}`
// in a `.mjs` config — the config lints itself.
const config = { plugins: { "@tailwindcss/postcss": {} } };

export default config;
```

`web/components.json` — the shape RosettaCloud's working setup uses (`base-nova`, RSC on, lucide):

```json
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "base-nova",
  "rsc": true,
  "tsx": true,
  "tailwind": { "config": "", "css": "app/globals.css", "baseColor": "neutral", "cssVariables": true, "prefix": "" },
  "iconLibrary": "lucide",
  "aliases": { "components": "@/components", "utils": "@/lib/utils", "ui": "@/components/ui", "lib": "@/lib", "hooks": "@/hooks" }
}
```

`web/app/globals.css`:

```css
@import "tailwindcss";

/* Grace's palette. Muted and administrative on purpose: this is a work queue a
   caseworker looks at all day, not a marketing page. The one saturated colour is
   reserved for "a human must decide", so escalation is the thing the eye finds. */
@theme {
  --color-paper: #FAF9F7;
  --color-ink: #1C1F23;
  --color-muted: #6B7280;
  --color-rule: #E5E3DF;
  --color-acted: #2F6F4E;
  --color-escalate: #B4530A;
  --color-error: #9B2C2C;
}

body { background-color: var(--color-paper); color: var(--color-ink); }
```

- [x] **Step 4: The shared types, the shell, and a placeholder page**

`web/lib/types.ts` — exactly the interfaces listed under **Produces** above. Write them verbatim;
later tasks import them by name.

`web/app/layout.tsx`:

```tsx
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Grace — caseworker queue",
  description: "Renewals Grace filed, and the cases it refused to decide.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <header className="border-b border-[var(--color-rule)] px-6 py-4">
          <span className="font-semibold">Grace</span>
          <span className="ml-2 text-sm text-[var(--color-muted)]">caseworker queue</span>
        </header>
        <main className="px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
```

`web/app/page.tsx` — a placeholder that Task 5 replaces:

```tsx
export default function Home() {
  return <p className="text-[var(--color-muted)]">Loading the sweep…</p>;
}
```

- [x] **Step 5: A smoke test that can actually fail**

`web/vitest.config.mts`:

```ts
import { defineConfig } from "vitest/config";
import path from "node:path";

// `import.meta.dirname`, not `__dirname`: Vitest 5 warns that `__dirname` in a
// config file is unsupported by `configLoader: "native"`, which is planned to
// become Vite's default.
export default defineConfig({
  test: { environment: "node", include: ["__tests__/**/*.test.ts"] },
  resolve: { alias: { "@": path.resolve(import.meta.dirname, ".") } },
});
```

`web/__tests__/smoke.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { CaseSummary } from "@/lib/types";

describe("the scaffold", () => {
  it("resolves the @/ alias and the shared types", () => {
    // A type-only import cannot fail at runtime, so construct a value: this
    // fails if `CaseSummary`'s fields are renamed, which is what later tasks
    // depend on.
    const c: CaseSummary = {
      caseId: "c-011", status: "escalated", program: "medicaid",
      deadline: "2026-10-20", reason: "material_income_change", filed: false,
    };
    expect(c.caseId).toBe("c-011");
    expect(c.filed).toBe(false);
  });

  it("does not ship a static export config", async () => {
    // `output: "export"` would silently remove route handlers and middleware,
    // taking the Cognito gate and the decide endpoint with them. Assert the
    // config does not set it.
    const mod = await import("@/next.config");
    expect((mod.default as Record<string, unknown>).output).toBeUndefined();
  });
});
```

- [x] **Step 6: Prove the whole toolchain is green**

```bash
cd web
npm run typecheck
npm run lint
npm run test
npm run build
```

Expected: all four pass. **`npm run build` is the one that matters** — it is what Amplify runs, and a
build that only works locally is not a deployment.

If `eslint` has no config, create `web/eslint.config.mjs`:

```js
import next from "eslint-config-next";

// `...next`, not `...next()`. `eslint-config-next@16.3.4` exports
// `Linter.Config[]` — an array, per its own `dist/index.d.ts`
// (`declare const config: Linter.Config[]; export = config`). Spreading a call
// throws `next is not a function`.
//
// Named, not anonymous, for the same `import/no-anonymous-default-export`
// reason as `postcss.config.mjs`.
const config = [...next, { ignores: [".next/**", "node_modules/**"] }];

export default config;
```

- [x] **Step 7: Confirm the Python suite is untouched**

Run: `.venv/bin/python -m pytest`
Expected: **622 passed**. Adding `web/` must not affect it. If the count changed, something outside
`web/` was edited.

- [x] **Step 8: Commit**

```bash
git add web/ .gitignore
git commit -m "feat: scaffold the dashboard with Next 16, Tailwind 4, and shadcn"
```

Check `git status` before committing: `node_modules/` and `.next/` are gitignored, so the staged file
count should be roughly twenty, not thousands. If it is thousands, stop and fix the ignore rules.

---
## Task 2: `lib/authorize.ts` — the pure decision, tested hardest

This is the dashboard's `authority.py`: given a session, a case's facts, and an attempted decision, it
returns a `Permit` or a `Refusal` and **touches nothing**. No AWS, no `fetch`, no clock read passed
implicitly. Everything it needs arrives as an argument, which is what makes it exhaustively testable.

**Files:**
- Create: `web/lib/authorize.ts`
- Test: `web/__tests__/authorize.test.ts`

**Interfaces:**
- Consumes: `web/lib/types.ts` (`SessionIdentity`, `CaseStatus`)
- Produces:
  ```ts
  export const CASEWORKER_ROLE = "caseworker";
  export type DecisionKind = "approve" | "deny";
  export interface CaseFacts { caseId: string; status: CaseStatus; alreadyDecided: boolean; }
  export interface DecisionAttempt { decision: DecisionKind; note: string; }
  export type RefusalCode =
    | "no_session" | "session_expired" | "wrong_role"
    | "unknown_case" | "not_escalated" | "already_decided"
    | "unknown_decision" | "note_too_long";
  export interface Refusal { permitted: false; code: RefusalCode; message: string; }
  export interface Permit { permitted: true; decidedBy: string; decision: DecisionKind; note: string; }
  export type Authorisation = Permit | Refusal;
  export const MAX_NOTE_LENGTH = 2000;
  export function authorize(
    session: SessionIdentity | null,
    facts: CaseFacts | null,
    attempt: DecisionAttempt,
    nowMs: number,
  ): Authorisation;
  ```

**The signature's types are what a caller promises, not what `authorize` may assume.** Task 5's
route builds `attempt` from `await request.json()` with a bare `body.decision as "approve"` cast,
so `decision` and `note` arrive as whatever a client posted. `DecisionAttempt` says `note: string`
and `authorize` still checks `typeof attempt.note !== "string"` — an assertion in the type system
is erased at runtime, which is exactly the boundary a `as` cast crosses. Likewise `expiresAt:
number` admits `Infinity`. Both are checked; see the notes in the corrected draft below.

- [x] **Step 1: Write the failing test, exhaustively**

`web/__tests__/authorize.test.ts`:

> **Corrected after implementation.** The draft below is the shipped file. Four defects were
> found in the original draft and are fixed here, each with a comment in place so it is not
> reintroduced:
> 1. The draft's allowlist loop contained `${bad!r}` — Python `repr` syntax inside a JS template
>    literal. The whole file failed to parse (`[PARSE_ERROR] Expected `}` but found `Identifier``),
>    so **zero** of its fourteen tests ran. Use `JSON.stringify(bad)`, which also shows the
>    trailing space in `"approve "`.
> 2. Every refusal-code assertion sat inside `if (!r.permitted) expect(r.code)...`, whose body does
>    not run on a permit — so the code check silently vanished on exactly the outcome it guards.
>    Measured: with `authorize` rewritten to always refuse, three tests still passed, including
>    `carries the opaque sub, never a name`, whose entire body is inside `if (r.permitted)`.
>    `refusalOf`/`permitOf` narrow by throwing, so every assertion is unconditional.
> 3. The purity guard checked five literal spellings and left three holes, all three measured
>    passing against it: `from "fs"` (it forbade only `node:fs`), `new Date().getTime()` (only
>    `Date.now()`), and `globalThis.fetch` (only `fetch(`). It now enumerates the imports that are
>    actually present and requires each to be type-only and relative — a positive check, not a
>    denylist of spellings someone remembered.
> 4. Two reachable inputs bypassed their own guard: a non-finite `expiresAt` (`exp: 1e400` in a JWT
>    parses to `Infinity`, `jose` verifies such a token, and `Infinity <= nowMs` is `false`, so the
>    session never expires) and a non-string `note` (`.length` is `undefined`, and
>    `undefined > 2000` is `false`, so the cap passes silently; `null` throws instead).

```ts
import { describe, expect, it } from "vitest";
import { authorize, CASEWORKER_ROLE, MAX_NOTE_LENGTH } from "@/lib/authorize";
import type { Authorisation, CaseFacts, DecisionAttempt, Permit, Refusal } from "@/lib/authorize";
import type { SessionIdentity } from "@/lib/types";

const NOW = 1_788_400_000_000;
const session = (over: Partial<SessionIdentity> = {}): SessionIdentity => ({
  sub: "7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d",
  role: CASEWORKER_ROLE,
  expiresAt: NOW + 60_000,
  ...over,
});
const escalated = (over: Partial<CaseFacts> = {}): CaseFacts => ({
  caseId: "c-011",
  status: "escalated",
  alreadyDecided: false,
  ...over,
});
const approve: DecisionAttempt = { decision: "approve", note: "Wage record is stale." };

// The plan's draft asserted refusal codes inside `if (!r.permitted) { ... }`.
// That body does not run on a permit, so the code assertion silently vanishes
// on exactly the outcome it was written to catch. These two helpers narrow by
// *throwing*, so every assertion below is unconditional — the Task 8 vacuity
// lesson applied to a TypeScript discriminated union.
function refusalOf(r: Authorisation): Refusal {
  if (r.permitted) throw new Error(`expected a refusal, got a permit: ${JSON.stringify(r)}`);
  return r;
}
function permitOf(r: Authorisation): Permit {
  if (!r.permitted) throw new Error(`expected a permit, got ${r.code}: ${r.message}`);
  return r;
}

describe("authorize — refusals", () => {
  it("refuses with no session at all", () => {
    expect(refusalOf(authorize(null, escalated(), approve, NOW)).code).toBe("no_session");
  });

  it("refuses an expired session, even one millisecond past", () => {
    // Boundary, not a round number: `<=` written as `<` honours a
    // just-expired session, and that is the direction that fails open.
    expect(refusalOf(authorize(session({ expiresAt: NOW }), escalated(), approve, NOW)).code)
      .toBe("session_expired");
    expect(refusalOf(authorize(session({ expiresAt: NOW - 1 }), escalated(), approve, NOW)).code)
      .toBe("session_expired");
  });

  it("refuses an expiry that is not a finite number of milliseconds", () => {
    // Reachable, not defensive padding: `exp: 1e400` in a JWT payload parses to
    // `Infinity`, `jose` verifies such a token happily (measured), and Task 4's
    // `typeof payload.exp !== "number"` check passes it through — `Infinity` is
    // a number. `Infinity <= nowMs` is `false`, so without this guard the
    // session never expires. Plan 2's NaN finding in the other direction: a
    // non-finite number reads back as a number and behaves like nothing.
    for (const bad of [Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, Number.NaN]) {
      expect(refusalOf(authorize(session({ expiresAt: bad }), escalated(), approve, NOW)).code)
        .toBe("session_expired");
    }
    // The same must hold for a clock that arrives unusable.
    expect(refusalOf(authorize(session(), escalated(), approve, Number.NaN)).code)
      .toBe("session_expired");
  });

  it("refuses a session without the caseworker role", () => {
    // Exact match, so near-misses refuse too. `"Caseworker"` and `"caseworker "`
    // are not this role — the same allowlist polarity as the decision word.
    for (const role of ["viewer", "Caseworker", "CASEWORKER", "caseworker ", ""]) {
      expect(refusalOf(authorize(session({ role }), escalated(), approve, NOW)).code)
        .toBe("wrong_role");
    }
  });

  it("refuses a case that does not exist", () => {
    // `null` facts collapse "no such case" and "unreadable case" into one
    // answer on purpose — see the note in lib/cases.ts.
    expect(refusalOf(authorize(session(), null, approve, NOW)).code).toBe("unknown_case");
  });

  it("refuses a case Grace handled itself", () => {
    // Deciding an `acted` case would let a human retroactively "approve"
    // something already filed, which the audit trail would then imply they
    // authorised. `error` is not decidable either: a case whose sweep failed has
    // no measured verdict to approve.
    for (const status of ["acted", "error"] as const) {
      expect(refusalOf(authorize(session(), escalated({ status }), approve, NOW)).code)
        .toBe("not_escalated");
    }
  });

  it("refuses a second decision on the same case", () => {
    expect(refusalOf(authorize(session(), escalated({ alreadyDecided: true }), approve, NOW)).code)
      .toBe("already_decided");
  });

  it("refuses any decision word that is not exactly approve or deny", () => {
    // An ALLOWLIST, not a denylist. Plan 1's Task 6 proved a denylist makes the
    // UNRECOGNISED answer the dangerous one: "Escalate.", "no, hold this one",
    // and "needs review" all resumed a graph and filed a renewal for a
    // household missing a document. Anything unrecognised must refuse.
    const words = ["Approve", "APPROVE", "approve ", " approve", "approved", "yes", "file",
      "proceed", "needs review", "no, hold this one", "", "escalate", "Escalate.", "deny "];
    let checked = 0;
    for (const bad of words) {
      const r = authorize(session(), escalated(), { decision: bad as "approve", note: "x" }, NOW);
      expect(refusalOf(r).code, `${JSON.stringify(bad)} must refuse`).toBe("unknown_decision");
      checked += 1;
    }
    // A loop that never ran would assert nothing while reporting a pass.
    expect(checked).toBe(words.length);
  });

  it("refuses a note longer than the cap", () => {
    expect(refusalOf(authorize(session(), escalated(),
      { decision: "deny", note: "x".repeat(MAX_NOTE_LENGTH + 1) }, NOW)).code)
      .toBe("note_too_long");
    // The boundary itself is allowed; an off-by-one here refuses a legitimate note.
    expect(permitOf(authorize(session(), escalated(),
      { decision: "deny", note: "x".repeat(MAX_NOTE_LENGTH) }, NOW)).note.length)
      .toBe(MAX_NOTE_LENGTH);
  });

  it("refuses a note that is not a string", () => {
    // `.length` on a non-string is `undefined`, and `undefined > MAX_NOTE_LENGTH`
    // is `false` — so the cap passes silently and a non-string reaches the
    // decision row. `null` is worse: `.length` throws, and an exception out of
    // the pure gate is a 500 rather than a refusal. Refuse the type; coercing
    // would invent a note nobody wrote.
    for (const bad of [null, undefined, 42, {}, [], { length: 99999 }]) {
      expect(refusalOf(authorize(session(), escalated(),
        { decision: "deny", note: bad as unknown as string }, NOW)).code)
        .toBe("note_too_long");
    }
  });

  it("orders its checks so a refusal never leaks whether a case exists", () => {
    // An unauthenticated or wrong-role caller must not learn the difference
    // between a case that exists and one that does not. Session checks come
    // first, so both inputs give the same answer.
    expect(refusalOf(authorize(null, escalated(), approve, NOW)).code)
      .toBe(refusalOf(authorize(null, null, approve, NOW)).code);
    expect(refusalOf(authorize(session({ role: "viewer" }), escalated(), approve, NOW)).code)
      .toBe(refusalOf(authorize(session({ role: "viewer" }), null, approve, NOW)).code);
  });
});

describe("authorize — permits", () => {
  it("permits an approve from a valid caseworker on an escalated case", () => {
    const p = permitOf(authorize(session(), escalated(), approve, NOW));
    expect(p.decision).toBe("approve");
    expect(p.decidedBy).toBe(session().sub);
    expect(p.note).toBe("Wage record is stale.");
  });

  it("permits a session expiring one millisecond from now", () => {
    expect(permitOf(authorize(session({ expiresAt: NOW + 1 }), escalated(), approve, NOW)).decision)
      .toBe("approve");
  });

  it("permits a deny just as readily", () => {
    expect(permitOf(authorize(session(), escalated(), { decision: "deny", note: "" }, NOW)).decision)
      .toBe("deny");
  });

  it("carries the opaque sub, never a name", () => {
    // Hard rule 9's reasoning applied to the caseworker: the JWT `sub` is
    // logged to CloudTrail, which is outside every redaction Grace has.
    const p = permitOf(authorize(session(), escalated(), approve, NOW));
    expect(p.decidedBy).toMatch(/^[0-9a-f-]{36}$/);
    expect(p.decidedBy).not.toMatch(/@/);
  });

  it("carries nothing beyond the four fields a decision row needs", () => {
    // A permit is what `recordDecision` writes from. If `caseId`, a name, or a
    // whole session object rode along, hard rule 9's surface would widen without
    // anyone choosing to widen it.
    const p = permitOf(authorize(session(), escalated(), approve, NOW));
    expect(Object.keys(p).sort()).toEqual(["decidedBy", "decision", "note", "permitted"]);
  });

  it("permits without filing anything — a permit is not a filing", () => {
    // Stated as a test because it is the property most easily misread. Approving
    // `c-010`, a household missing a required document, is permitted here; the
    // authority gate re-evaluates the case record afterwards and still refuses
    // to file. This function authorises writing a decision row and re-invoking
    // the runtime, nothing more, which is why its result carries no verdict.
    const p = permitOf(authorize(session(), escalated({ caseId: "c-010" }), approve, NOW));
    expect(Object.keys(p)).not.toContain("filed");
    expect(Object.keys(p)).not.toContain("caseId");
  });
});

describe("authorize — purity", () => {
  it("is deterministic and mutates nothing it is given", () => {
    const s = session(); const f = escalated();
    const before = JSON.stringify({ s, f, approve });
    const a = authorize(s, f, approve, NOW);
    const b = authorize(s, f, approve, NOW);
    expect(JSON.stringify({ s, f, approve })).toBe(before);
    expect(a).toEqual(b);
  });

  it("imports nothing that performs I/O, and reads no clock", async () => {
    // Structural, so the purity survives a future edit. `authority.py` is
    // guarded the same way with a pkgutil walk; this is the TypeScript
    // equivalent, and it is why every refusal above needs no AWS.
    //
    // The plan's draft checked five literal spellings, which left three holes —
    // all three measured passing against it: `from "fs"` (it only forbade
    // `node:fs`), `new Date().getTime()` (it only forbade `Date.now()`), and
    // `globalThis.fetch` (it only forbade `fetch(`). A denylist of spellings
    // someone remembered is the same mistake Task 4's model-ID guard fixed by
    // discovering modules from disk. So: enumerate the imports that ARE there
    // and require every one to be type-only and relative.
    const { readFileSync } = await import("node:fs");
    const src = readFileSync(new URL("../lib/authorize.ts", import.meta.url), "utf8");

    const imports = [...src.matchAll(/^\s*import\s+([^;]+?)\s+from\s+["']([^"']+)["']/gm)]
      .map(m => ({ clause: m[1] ?? "", specifier: m[2] ?? "" }));
    expect(imports.length, "authorize.ts should import something, or this guard is vacuous")
      .toBeGreaterThan(0);
    for (const { clause, specifier } of imports) {
      // Type-only: erased at compile time, so it cannot execute I/O even if the
      // module it names would.
      expect(clause, `${specifier} must be imported as \`import type\``).toMatch(/^type\b/);
      // Relative: a bare specifier is a package, and no package in this
      // dependency tree is I/O-free.
      expect(specifier, `${specifier} must be a relative sibling`).toMatch(/^\.\.?\//);
    }

    // Anything that reaches outside the arguments, whatever its spelling.
    const forbidden: [RegExp, string][] = [
      [/@aws-sdk/, "an AWS SDK client"],
      [/\bfetch\b/, "fetch"],
      [/\brequire\s*\(/, "require()"],
      [/\bimport\s*\(/, "a dynamic import"],
      [/\bprocess\b/, "process"],
      [/\bDate\b/, "a clock read (Date)"],
      [/\bperformance\s*\./, "a clock read (performance)"],
      [/\bMath\.random\b/, "randomness"],
      [/\bglobalThis\b/, "globalThis"],
    ];
    for (const [pattern, what] of forbidden) {
      expect(pattern.test(src), `authorize.ts must not reference ${what}`).toBe(false);
    }
  });
});
```

- [x] **Step 2: Run it to verify it fails**

Run: `cd web && npm run test`
Expected: FAIL — `Cannot find package '@/lib/authorize'` (Vitest 5's wording; the draft said
"Cannot find module"). Watch for the *right* failure: the draft's own parse error also fails,
and a parse error is not a red step — it means none of the tests ran at all.

- [x] **Step 3: Write `web/lib/authorize.ts`**

```ts
/**
 * WHO MAY DECIDE WHAT. Pure, and deliberately so.
 *
 * This is the dashboard's `grace/authority.py`: it maps a session, a case's
 * facts, and an attempted decision onto `Permit` or `Refusal`, and it touches
 * nothing. No AWS client, no HTTP, no clock read — the current time arrives as
 * an argument. `lib/cases.ts` measures the facts; this file decides over them,
 * and `__tests__/authorize.test.ts` proves the import graph stays clean.
 *
 * Two properties worth stating outright, both inherited from Plan 1:
 *
 * **The decision word is an allowlist.** Task 6 proved a denylist makes the
 * *unrecognised* answer the dangerous one: "Escalate.", "no, hold this one",
 * and "needs review" each resumed a paused graph and filed a renewal for a
 * household missing a required document. Only an exact `"approve"` or `"deny"`
 * is honoured here; everything else refuses.
 *
 * **A permit is not a filing.** Permitting an approve only authorises writing
 * the decision row and re-invoking the runtime. The authority gate then
 * re-evaluates the case facts and may still refuse to file — approving `c-010`
 * must not file, because the document is still missing.
 */

import type { CaseStatus, SessionIdentity } from "./types";

export const CASEWORKER_ROLE = "caseworker";

/** A caseworker's note is free text stored verbatim; bound it so a single
 *  request cannot write an unbounded item to DynamoDB. */
export const MAX_NOTE_LENGTH = 2000;

export type DecisionKind = "approve" | "deny";

/** Exactly the two words honoured. A `Set` of literals, not a regex: a regex
 *  invites `/approve/i` and case-insensitivity is how "Approve " gets in. */
const DECISIONS: ReadonlySet<string> = new Set<DecisionKind>(["approve", "deny"]);

export interface CaseFacts {
  caseId: string;
  status: CaseStatus;
  alreadyDecided: boolean;
}

export interface DecisionAttempt {
  decision: DecisionKind;
  note: string;
}

export type RefusalCode =
  | "no_session"
  | "session_expired"
  | "wrong_role"
  | "unknown_case"
  | "not_escalated"
  | "already_decided"
  | "unknown_decision"
  | "note_too_long";

export interface Refusal {
  permitted: false;
  code: RefusalCode;
  message: string;
}

export interface Permit {
  permitted: true;
  decidedBy: string;
  decision: DecisionKind;
  note: string;
}

export type Authorisation = Permit | Refusal;

function refuse(code: RefusalCode, message: string): Refusal {
  return { permitted: false, code, message };
}

export function authorize(
  session: SessionIdentity | null,
  facts: CaseFacts | null,
  attempt: DecisionAttempt,
  nowMs: number,
): Authorisation {
  if (session === null) {
    return refuse("no_session", "Sign in to decide a case.");
  }
  // Refuse anything that is not a finite number of milliseconds. `expiresAt`
  // arrives from a decoded JWT claim, and `NaN <= nowMs` is `false` — so a
  // malformed expiry would slip past the comparison below and be treated as a
  // session that never expires. Same reasoning as Plan 2's NaN finding: a NaN
  // reads back as a number and behaves like nothing.
  if (!Number.isFinite(session.expiresAt) || !Number.isFinite(nowMs)) {
    return refuse("session_expired", "Your session expired. Sign in again.");
  }
  // `<=` and not `<`: a session expiring exactly now is expired. Written the
  // other way, a just-expired session is honoured, which is the fail-open
  // direction.
  if (session.expiresAt <= nowMs) {
    return refuse("session_expired", "Your session expired. Sign in again.");
  }
  if (session.role !== CASEWORKER_ROLE) {
    return refuse("wrong_role", "This account may not decide cases.");
  }
  // `null` covers both "no such case" and "the case could not be read".
  // `lib/cases.ts` collapses them at the measurement, so this function cannot
  // tell them apart even if a later edit wanted to.
  if (facts === null) {
    return refuse("unknown_case", "No such case.");
  }
  if (facts.status !== "escalated") {
    return refuse(
      "not_escalated",
      "Grace handled this case itself; there is nothing to decide.",
    );
  }
  if (facts.alreadyDecided) {
    return refuse("already_decided", "A caseworker has already decided this case.");
  }
  if (!DECISIONS.has(attempt.decision)) {
    return refuse("unknown_decision", "Choose approve or deny.");
  }
  // A route handler builds `attempt` from a JSON body, so `note` can arrive as
  // anything a client sends. `.length` on a non-string is `undefined`, and
  // `undefined > MAX_NOTE_LENGTH` is `false` — the cap would pass silently and
  // an object would reach the decision row. Refuse the type, do not coerce it:
  // coercion invents a note nobody wrote.
  if (typeof attempt.note !== "string") {
    return refuse("note_too_long", `Keep the note under ${MAX_NOTE_LENGTH} characters.`);
  }
  if (attempt.note.length > MAX_NOTE_LENGTH) {
    return refuse("note_too_long", `Keep the note under ${MAX_NOTE_LENGTH} characters.`);
  }
  return {
    permitted: true,
    decidedBy: session.sub,
    decision: attempt.decision,
    note: attempt.note,
  };
}
```

- [x] **Step 4: Run the tests**

Run: `cd web && npm run test && npm run typecheck`
Expected: PASS, **19** `authorize.test.ts` tests (21 including Task 1's two smoke tests). The
draft said 14 — that was the count before the vacuity and reachability fixes above added five.

- [x] **Step 5: Prove the allowlist test is not vacuous**

Temporarily replace the `DECISIONS.has(...)` check with a denylist —
`if (attempt.decision === "escalate") { ... }` — and re-run. The
`refuses any decision word that is not exactly approve or deny` test **must fail**.

**It names `"Approve"`, not one of the four strings the draft predicted.** The loop asserts in
order and Vitest stops the test at the first failed assertion, so the string reported is simply
the first in the array — capitalisation, not one of Plan 1's measured words. The draft's
prediction of `"yes"`/`"file"`/`"proceed"`/`"needs review"` would have looked like a failed
sabotage to anyone checking the output against it, when in fact the guard fired correctly. What
matters is that all of them get through the denylist; the test proves that by asserting each,
which is why the loop ends with `expect(checked).toBe(words.length)`.

Measured output:

```text
AssertionError: "Approve" must refuse: expected true to be false // Object.is equality
❯ __tests__/authorize.test.ts:79:65
```

Restore the allowlist and confirm green again.

Record in your report that you did this and what failed. A guard nobody has watched fail is a guard
nobody has tested.

- [x] **Step 6: Commit**

```bash
git add web/lib/authorize.ts web/__tests__/authorize.test.ts
git commit -m "feat: the dashboard's pure authorisation decision"
```

---

## Task 3: `lib/cases.ts` — the only DynamoDB reader

Everything the pages render comes from here, and nothing else in `web/` touches DynamoDB. Reads run
server-side; the browser never sees a credential or a table name.

**Files:**
- Create: `web/lib/env.ts`, `web/lib/cases.ts`
- Test: `web/__tests__/cases.test.ts`

**Interfaces:**
- Consumes: `web/lib/types.ts`, `web/lib/authorize.ts` (`CaseFacts`)
- Produces:
  ```ts
  // env.ts
  export interface Env { region: string; tableName: string; escalationIndex: string; runtimeArn: string; }
  export function readEnv(source?: NodeJS.ProcessEnv): Env;   // throws on a missing name
  // cases.ts — `client` is injectable so every test runs offline
  export function listCases(client?: DynamoDBClient): Promise<CaseSummary[]>;
  export function listQueue(client?: DynamoDBClient): Promise<CaseSummary[]>;
  export function readCase(caseId: string, client?: DynamoDBClient): Promise<CaseDetail | null>;
  export function readFacts(caseId: string, client?: DynamoDBClient): Promise<CaseFacts | null>;
  ```

- [ ] **Step 1: Write the failing test**

**Read `docs/plan3-live-data-findings.md` before you write a single fixture.** The real table was
measured before this task was written, and five things about it differ from the draft below. Fixtures
that disagree with reality produce a parser that passes its tests and fails on the live table, which
is the one failure mode Step 6 exists to catch late and this note exists to prevent early:

1. **Timestamps are `+00:00`, never `Z`** — real values are
   `2026-09-03T14:17:01.231621+00:00`, i.e. `datetime.isoformat()` with an offset and microseconds.
   The fixtures below use `Z`. Both parse as dates, but **anything that string-matches or slices a
   trailing `Z` breaks on every real row.** Prefer at least one fixture in the real shape.
2. **`d_trace_id` is DynamoDB type `NULL`** on essentially every ledger row — present, not absent,
   with value `{"NULL": true}`. The only two value types in the whole table are `S` and `NULL`.
3. **The GSI holds 17 rows for 3 households** (`c-010` 6, `c-012` 6, `c-011` 5). Dedup is not
   hypothetical.
4. **Real deadlines are `c-010` → `2026-10-18`, `c-011` → `2026-10-22`, `c-012` → `2026-10-12`.** The
   fixtures below now use these (the draft invented `2026-10-05`), and the ordering test carries a
   third row so that deadline order, escalation-time order, and GSI order all differ — which is what
   makes it distinguish "sorted by deadline" from "whatever the GSI returned". Soonest-first is
   `c-012`, `c-010`, `c-011`.
5. **Escalation rows carry `question` as well as `reason`**, and no `program` attribute exists on
   them at all — so `str(escalation?.program, "—")` in Step 4 always yields the placeholder. Either
   read the program from somewhere real or drop the field; do not ship a column that is structurally
   always a dash.

`web/__tests__/cases.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { readEnv } from "@/lib/env";
import { listQueue, readCase, readFacts } from "@/lib/cases";

/** The slice of DynamoDBClient these functions use. Records every command so a
 *  test can assert the query shape, not just the parsed result. */
class FakeDynamo {
  public sent: Array<Record<string, unknown>> = [];
  constructor(private pages: Array<Record<string, unknown>>) {}
  async send(command: { input: Record<string, unknown> }): Promise<unknown> {
    this.sent.push(command.input);
    return this.pages[Math.min(this.sent.length - 1, this.pages.length - 1)];
  }
}

const S = (s: string) => ({ S: s });

const ledgerRow = (at: string, kind: string, extra: Record<string, unknown> = {}) => ({
  pk: S("CASE#c-011"), sk: S(`LEDGER#${at}#000001`),
  case_id: S("c-011"), at: S(at), kind: S(kind), ...extra,
});

describe("readEnv", () => {
  it("throws naming the variable that is missing", () => {
    // A missing table name must fail at startup with a readable message, not
    // as `undefined` inside an SDK call three layers down. Same reasoning as
    // Plan 2's GRACE_STORE allowlist, which raises on blank.
    expect(() => readEnv({ AWS_REGION: "us-east-1" } as NodeJS.ProcessEnv))
      .toThrow(/GRACE_TABLE_NAME/);
  });

  it("rejects a variable that is set but blank", () => {
    // `process.env.X ?? default` only defaults on absence. Plan 2 found a blank
    // GRACE_STORE bypassing its default and silently discarding a ledger.
    expect(() => readEnv({
      AWS_REGION: "us-east-1", GRACE_TABLE_NAME: "",
      GRACE_ESCALATION_INDEX: "escalation-queue", GRACE_RUNTIME_ARN: "arn:x",
    } as NodeJS.ProcessEnv)).toThrow(/GRACE_TABLE_NAME/);
  });

  it("reads a complete environment", () => {
    const env = readEnv({
      AWS_REGION: "us-east-1", GRACE_TABLE_NAME: "grace-cases",
      GRACE_ESCALATION_INDEX: "escalation-queue",
      GRACE_RUNTIME_ARN: "arn:aws:bedrock-agentcore:us-east-1:1:runtime/grace",
    } as NodeJS.ProcessEnv);
    expect(env.tableName).toBe("grace-cases");
  });
});

describe("listQueue", () => {
  it("queries the sparse GSI, not a table scan", async () => {
    const fake = new FakeDynamo([{ Items: [] }]);
    await listQueue(fake as never);
    const input = fake.sent[0]!;
    expect(input.IndexName).toBe("escalation-queue");
    expect(input.KeyConditionExpression).toContain("#s = :s");
    expect(JSON.stringify(input)).toContain("PENDING_CASEWORKER");
    // A Scan would read every ledger row in the table to find three cases.
    expect(JSON.stringify(input)).not.toContain("FilterExpression");
  });

  it("orders by soonest deadline, because that is the caseworker's urgency", async () => {
    // Real deadlines, and one row in the real `+00:00` shape rather than `Z`.
    // Escalation time here is c-012 first, deadline order is c-012 first too —
    // so a third row (c-010, escalated earliest, deadline in between) is what
    // makes the two orderings disagree and the assertion meaningful.
    const rows = [
      { pk: S("CASE#c-010"), sk: S("ESCALATION#2026-09-03T04:20:03.568119+00:00"), case_id: S("c-010"),
        status: S("PENDING_CASEWORKER"), escalated_at: S("2026-09-03T04:20:03.568119+00:00"),
        reason: S("missing_document"), deadline: S("2026-10-18") },
      { pk: S("CASE#c-011"), sk: S("ESCALATION#2026-09-03T05:00:01Z"), case_id: S("c-011"),
        status: S("PENDING_CASEWORKER"), escalated_at: S("2026-09-03T05:00:01Z"),
        reason: S("material_income_change"), deadline: S("2026-10-22") },
      { pk: S("CASE#c-012"), sk: S("ESCALATION#2026-09-03T06:00:00Z"), case_id: S("c-012"),
        status: S("PENDING_CASEWORKER"), escalated_at: S("2026-09-03T06:00:00Z"),
        reason: S("source_conflict"), deadline: S("2026-10-12") },
    ];
    const fake = new FakeDynamo([{ Items: rows }]);
    const queue = await listQueue(fake as never);
    // Deadline order, not GSI order and not escalation-time order — all three
    // differ on this input, which is the point.
    expect(queue.map(c => c.caseId)).toEqual(["c-012", "c-010", "c-011"]);
  });

  it("collapses repeat escalations of one household to a single row", async () => {
    // Every sweep appends a fresh ESCALATION# row, so the GSI legitimately holds
    // 17 rows for 3 households. A queue listing the same family five times is a
    // caseworker deciding the same case five times.
    const dup = (ts: string) => ({
      pk: S("CASE#c-010"), sk: S(`ESCALATION#${ts}`), case_id: S("c-010"),
      status: S("PENDING_CASEWORKER"), escalated_at: S(ts),
      reason: S("missing_document"), deadline: S("2026-10-18"),
    });
    const fake = new FakeDynamo([{ Items: [dup("2026-09-01T00:00:00Z"), dup("2026-09-03T00:00:00Z")] }]);
    const queue = await listQueue(fake as never);
    expect(queue).toHaveLength(1);
    // The newest escalation wins: it carries the most recent reason.
    expect(queue[0]!.caseId).toBe("c-010");
  });

  it("follows LastEvaluatedKey", async () => {
    // Truncation would silently drop households from a work queue.
    const one = (id: string) => ({
      pk: S(`CASE#${id}`), sk: S("ESCALATION#2026-09-03T00:00:00Z"), case_id: S(id),
      status: S("PENDING_CASEWORKER"), escalated_at: S("2026-09-03T00:00:00Z"),
      reason: S("r"), deadline: S("2026-10-01"),
    });
    const fake = new FakeDynamo([
      { Items: [one("c-010")], LastEvaluatedKey: { pk: S("CASE#c-010") } },
      { Items: [one("c-011")] },
    ]);
    const queue = await listQueue(fake as never);
    expect(fake.sent).toHaveLength(2);
    expect(queue.map(c => c.caseId).sort()).toEqual(["c-010", "c-011"]);
  });
});

describe("readCase", () => {
  it("returns ledger rows in chronological order", async () => {
    // The evals read ledger position to assert reads precede actions; the
    // caseworker reads it as a story. Both need append order.
    const fake = new FakeDynamo([{ Items: [
      ledgerRow("2026-09-03T03:06:05Z", "tool_call", { d_tool: S("read_case") }),
      ledgerRow("2026-09-03T03:06:06Z", "tool_result", { d_status: S("success") }),
      ledgerRow("2026-09-03T03:06:09Z", "family_message_sent", { d_ref: S("recorded:1") }),
    ] }]);
    const detail = await readCase("c-011", fake as never);
    expect(detail?.ledger.map(r => r.kind))
      .toEqual(["tool_call", "tool_result", "family_message_sent"]);
  });

  it("strips the d_ prefix from detail keys", async () => {
    const fake = new FakeDynamo([{ Items: [
      ledgerRow("2026-09-03T03:06:05Z", "tool_call", { d_tool: S("read_case"), d_trace_id: { NULL: true } }),
    ] }]);
    const detail = await readCase("c-011", fake as never);
    expect(detail?.ledger[0]!.detail.tool).toBe("read_case");
    // NULL must read back as null, not the string "None" — Plan 2's finding.
    expect(detail?.ledger[0]!.detail.trace_id).toBeNull();
  });

  it("returns null for a case with no rows at all", async () => {
    const fake = new FakeDynamo([{ Items: [] }]);
    expect(await readCase("c-999", fake as never)).toBeNull();
  });

  it("never surfaces a household name or phone", async () => {
    // Hard rule 9. Even if a row somehow carried one, the reader must not
    // hand it to a page. Belt and braces on top of read_case's own fix.
    const fake = new FakeDynamo([{ Items: [
      ledgerRow("2026-09-03T03:06:05Z", "escalated", {
        d_question: S("Does the household still qualify?"),
      }),
    ] }]);
    const detail = await readCase("c-011", fake as never);
    const blob = JSON.stringify(detail);
    for (const n of ["Mensah", "Rivera", "Okonkwo", "+1555"]) {
      expect(blob).not.toContain(n);
    }
  });
});

describe("readFacts", () => {
  it("reports escalated and undecided for a queued case", async () => {
    const fake = new FakeDynamo([{ Items: [
      { pk: S("CASE#c-011"), sk: S("ESCALATION#2026-09-03T00:00:00Z"), case_id: S("c-011"),
        status: S("PENDING_CASEWORKER"), escalated_at: S("2026-09-03T00:00:00Z"),
        reason: S("material_income_change"), deadline: S("2026-10-22") },
    ] }]);
    const facts = await readFacts("c-011", fake as never);
    expect(facts).toEqual({ caseId: "c-011", status: "escalated", alreadyDecided: false });
  });

  it("reports alreadyDecided once a DECISION row exists", async () => {
    const fake = new FakeDynamo([{ Items: [
      { pk: S("CASE#c-011"), sk: S("ESCALATION#2026-09-03T00:00:00Z"), case_id: S("c-011"),
        status: S("PENDING_CASEWORKER"), escalated_at: S("2026-09-03T00:00:00Z"),
        reason: S("r"), deadline: S("2026-10-22") },
      { pk: S("CASE#c-011"), sk: S("DECISION#2026-09-03T01:00:00Z"), case_id: S("c-011"),
        decided_by: S("7f3a91"), decided_at: S("2026-09-03T01:00:00Z"),
        decision: S("approve"), note: S("ok") },
    ] }]);
    const facts = await readFacts("c-011", fake as never);
    expect(facts?.alreadyDecided).toBe(true);
  });

  it("returns null when the read throws, rather than guessing", async () => {
    // Fail closed. An unreadable case must not authorise a decision, and
    // `authorize` refuses on null facts.
    const boom = { send: () => Promise.reject(new Error("throttled")) };
    expect(await readFacts("c-011", boom as never)).toBeNull();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npm run test`
Expected: FAIL — `Cannot find module '@/lib/env'`.

- [ ] **Step 3: Write `web/lib/env.ts`**

```ts
/**
 * Server-only configuration, read once and validated loudly.
 *
 * A missing or blank name fails here with the variable's name in the message,
 * rather than surfacing as `undefined` inside an SDK call three layers down.
 * Plan 2 learned this twice: `os.getenv(name, default)` defaults only on
 * *absence*, so a blank `GRACE_STORE` bypassed its default and would have had a
 * deployed runtime write its ledger to memory and discard it at exit.
 *
 * None of these are `NEXT_PUBLIC_`. A table name or runtime ARN in the client
 * bundle is not a secret exactly, but it is a map of the backend, and nothing
 * in the browser needs it.
 */

export interface Env {
  region: string;
  tableName: string;
  escalationIndex: string;
  runtimeArn: string;
}

function required(source: NodeJS.ProcessEnv, name: string): string {
  const value = source[name];
  if (value === undefined || value.trim() === "") {
    throw new Error(
      `${name} is not set. The dashboard reads Grace's deployed resources and ` +
      `cannot guess their names.`,
    );
  }
  return value;
}

export function readEnv(source: NodeJS.ProcessEnv = process.env): Env {
  return {
    region: source.AWS_REGION?.trim() || "us-east-1",
    tableName: required(source, "GRACE_TABLE_NAME"),
    escalationIndex: required(source, "GRACE_ESCALATION_INDEX"),
    runtimeArn: required(source, "GRACE_RUNTIME_ARN"),
  };
}
```

- [ ] **Step 4: Write `web/lib/cases.ts`**

```ts
/**
 * THE ONLY DYNAMODB READER. Everything the pages render comes from here.
 *
 * `lib/authorize.ts` decides; this file measures the facts it decides over.
 * The split means every refusal is testable with no AWS, and a route
 * physically cannot hand `authorize` a fact this file did not measure — the
 * same discipline as `grace/authority.py` (pure) against `grace/steering.py`
 * (the adapter).
 *
 * Three behaviours that look like details and are not:
 *
 * **Queries paginate.** A DynamoDB Query caps at 1MB and signals more with
 * `LastEvaluatedKey`. Plan 2 hit this three separate times — `ledger()`,
 * `ListMemories`, and the runtime lookup — and in each case truncation was
 * silent. Here a dropped page removes a household from a work queue.
 *
 * **The queue is de-duplicated by case.** Every sweep appends a fresh
 * `ESCALATION#` row, so the GSI legitimately holds 17 rows for 3 households.
 * A caseworker must see three.
 *
 * **A failed read returns `null`, never a guess.** `authorize` refuses on null
 * facts, so an unreadable case cannot be decided. That is the fail-closed
 * direction Tasks 3 and 4 of Plan 1 established for the gate itself.
 */

import { DynamoDBClient, QueryCommand } from "@aws-sdk/client-dynamodb";
import type { AttributeValue } from "@aws-sdk/client-dynamodb";
import { readEnv } from "./env";
import type { CaseFacts } from "./authorize";
import type { CaseDetail, CaseSummary, Decision, LedgerRow } from "./types";

const LEDGER = "LEDGER#";
const ESCALATION = "ESCALATION#";
const DECISION = "DECISION#";
const PENDING = "PENDING_CASEWORKER";

let shared: DynamoDBClient | undefined;
function defaultClient(): DynamoDBClient {
  shared ??= new DynamoDBClient({ region: readEnv().region });
  return shared;
}

/** Read one attribute as a plain value. `NULL` becomes `null`, never the string
 *  "None" — Plan 2's round-trip finding. */
function plain(v: AttributeValue | undefined): string | number | boolean | null {
  if (v === undefined) return null;
  if (v.NULL) return null;
  if (v.S !== undefined) return v.S;
  if (v.BOOL !== undefined) return v.BOOL;
  if (v.N !== undefined) return v.N.includes(".") ? Number.parseFloat(v.N) : Number.parseInt(v.N, 10);
  return null;
}

function str(v: AttributeValue | undefined, fallback = ""): string {
  const p = plain(v);
  return typeof p === "string" ? p : fallback;
}

async function queryAll(
  client: DynamoDBClient,
  input: Record<string, unknown>,
): Promise<Record<string, AttributeValue>[]> {
  const items: Record<string, AttributeValue>[] = [];
  let startKey: Record<string, AttributeValue> | undefined;
  do {
    const page = (await client.send(
      new QueryCommand({ ...input, ExclusiveStartKey: startKey } as never),
    )) as { Items?: Record<string, AttributeValue>[]; LastEvaluatedKey?: Record<string, AttributeValue> };
    items.push(...(page.Items ?? []));
    startKey = page.LastEvaluatedKey;
  } while (startKey);
  return items;
}

/** The caseworker's work list: one row per household, soonest deadline first. */
export async function listQueue(client: DynamoDBClient = defaultClient()): Promise<CaseSummary[]> {
  const env = readEnv();
  const rows = await queryAll(client, {
    TableName: env.tableName,
    IndexName: env.escalationIndex,
    KeyConditionExpression: "#s = :s",
    ExpressionAttributeNames: { "#s": "status" },
    ExpressionAttributeValues: { ":s": { S: PENDING } },
  });

  // Newest escalation per case wins — it carries the current reason.
  const newest = new Map<string, Record<string, AttributeValue>>();
  for (const row of rows) {
    const id = str(row.case_id);
    const seen = newest.get(id);
    if (!seen || str(row.escalated_at) > str(seen.escalated_at)) newest.set(id, row);
  }

  return [...newest.values()]
    .map((row): CaseSummary => ({
      caseId: str(row.case_id),
      status: "escalated",
      program: str(row.program, "—"),
      deadline: str(row.deadline),
      reason: str(row.reason) || null,
      filed: false,
    }))
    .sort((a, b) => a.deadline.localeCompare(b.deadline) || a.caseId.localeCompare(b.caseId));
}

/** Every case the ledger knows about, for the sweep summary. */
export async function listCases(client: DynamoDBClient = defaultClient()): Promise<CaseSummary[]> {
  const queue = await listQueue(client);
  const escalated = new Set(queue.map(c => c.caseId));
  const summaries: CaseSummary[] = [...queue];
  // The twelve fixture cases are the caseload; anything not escalated and
  // carrying a renewal_submitted row was handled autonomously.
  for (let n = 1; n <= 12; n += 1) {
    const id = `c-${String(n).padStart(3, "0")}`;
    if (escalated.has(id)) continue;
    const detail = await readCase(id, client);
    if (detail === null) continue;
    summaries.push(detail.summary);
  }
  return summaries.sort((a, b) => a.caseId.localeCompare(b.caseId));
}

/** One household: its ledger, its decisions, and what Grace concluded. */
export async function readCase(
  caseId: string,
  client: DynamoDBClient = defaultClient(),
): Promise<CaseDetail | null> {
  const env = readEnv();
  let rows: Record<string, AttributeValue>[];
  try {
    rows = await queryAll(client, {
      TableName: env.tableName,
      KeyConditionExpression: "pk = :pk",
      ExpressionAttributeValues: { ":pk": { S: `CASE#${caseId}` } },
      ScanIndexForward: true,
    });
  } catch {
    // Fail closed: an unreadable case is not a decidable one.
    return null;
  }
  if (rows.length === 0) return null;

  const ledger: LedgerRow[] = [];
  const decisions: Decision[] = [];
  let escalation: Record<string, AttributeValue> | undefined;
  let filed = false;

  for (const row of rows) {
    const sk = str(row.sk);
    if (sk.startsWith(LEDGER)) {
      const detail: LedgerRow["detail"] = {};
      for (const [key, value] of Object.entries(row)) {
        if (key.startsWith("d_")) detail[key.slice(2)] = plain(value);
      }
      const kind = str(row.kind);
      if (kind === "renewal_submitted") filed = true;
      ledger.push({ at: str(row.at), kind, detail });
    } else if (sk.startsWith(DECISION)) {
      // `startsWith(DECISION)` is NOT sufficient on its own — Task 5 writes
      // Grace's outcome to `DECISION#<ts>#outcome`, which also starts with the
      // prefix. Counting that row as a decision makes `alreadyDecided` true
      // from Grace's own write rather than from a human's, so the *first*
      // caseworker decision would refuse itself as a second one. Distinguish
      // the two shapes here (an outcome row carries no `decision` attribute) or
      // give the outcome its own prefix in Task 5. Either way, `readFacts` must
      // count only rows a human wrote.
      decisions.push({
        decidedAt: str(row.decided_at),
        decidedBy: str(row.decided_by),
        decision: str(row.decision) === "approve" ? "approve" : "deny",
        note: str(row.note),
        outcome: str(row.outcome) || null,
      });
    } else if (sk.startsWith(ESCALATION)) {
      if (!escalation || str(row.escalated_at) > str(escalation.escalated_at)) escalation = row;
    }
  }

  const pending = escalation !== undefined && str(escalation.status) === PENDING;
  return {
    summary: {
      caseId,
      status: pending ? "escalated" : "acted",
      program: str(escalation?.program, "—"),
      deadline: str(escalation?.deadline),
      reason: escalation ? str(escalation.reason) || null : null,
      filed,
    },
    ledger,
    decisions,
  };
}

/** Exactly what `authorize` needs, and nothing else. */
export async function readFacts(
  caseId: string,
  client: DynamoDBClient = defaultClient(),
): Promise<CaseFacts | null> {
  const detail = await readCase(caseId, client);
  if (detail === null) return null;
  return {
    caseId,
    status: detail.summary.status,
    alreadyDecided: detail.decisions.length > 0,
  };
}
```

- [ ] **Step 5: Run the tests**

Run: `cd web && npm run test && npm run typecheck`
Expected: PASS — 3 `readEnv`, 4 `listQueue`, 4 `readCase`, 3 `readFacts`.

- [ ] **Step 6: Read the real table once, to prove the parser matches reality**

```bash
cd web
GRACE_TABLE_NAME=grace-cases GRACE_ESCALATION_INDEX=escalation-queue \
GRACE_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-east-1:339712964409:runtime/grace_grace-oTyyvo8stE \
AWS_REGION=us-east-1 \
npx tsx -e '
import { listQueue, readCase } from "./lib/cases";
const q = await listQueue();
console.log("queue:", q.map(c => `${c.caseId} ${c.deadline} ${c.reason}`));
const d = await readCase("c-011");
console.log("c-011 ledger kinds:", d?.ledger.map(r => r.kind));
console.log("c-011 filed:", d?.summary.filed);
'
```

Expected: exactly **three** queued cases (`c-010`, `c-011`, `c-012`) despite the GSI holding **17**
rows, and `c-011` showing `filed: false`. **A queue longer than three means the de-duplication is
wrong** — that is the assertion this step exists for, and the fake cannot make it.

**Also check what the reader hands the pages for `c-012`.** Two pre-fix rows in the live table used to
contain a household surname in `reason`/`question`/`d_question` — the referee's deliberation prose,
from before `read_case` stopped returning `display_name`. **Those three values were stripped on
2026-09-04** (see `docs/plan3-live-data-findings.md` for exactly what was and was not touched), so a
table-wide scan of all 633 rows for every fixture surname and for `+1555` now returns clean. Do not
build a render-time name filter on the strength of that history; the fix is at the source, which is
where CLAUDE.md requires it. Task 8 re-runs the scan as a check rather than as a hope.

If `tsx` is unavailable, `npx tsx` will fetch it; otherwise write the snippet to a temporary `.mts`
file and run it with `node --experimental-strip-types`.

- [ ] **Step 7: Confirm the Python suite is untouched**

Run: `.venv/bin/python -m pytest`
Expected: **622 passed**.

- [ ] **Step 8: Commit**

```bash
git add web/lib/env.ts web/lib/cases.ts web/__tests__/cases.test.ts
git commit -m "feat: the dashboard's only DynamoDB reader, paginated and de-duplicated"
```

---
## Task 4: Cognito — the pool, and server-side session verification

Two halves: an idempotent `boto3` script that provisions the pool, and a TypeScript verifier that
turns a cookie into a `SessionIdentity` or into `null`. The verifier is where a mistake becomes an
authentication bypass, so it is tested like the gate.

**Files:**
- Create: `infra/provision_cognito.py`, `web/lib/cognito.ts`, `web/middleware.ts`, `web/app/login/page.tsx`, `web/app/api/auth/callback/route.ts`
- Test: `tests/test_infra_cognito.py`, `web/__tests__/cognito.test.ts`

**Interfaces:**
- Consumes: `infra.naming`, `web/lib/types.ts` (`SessionIdentity`), `web/lib/authorize.ts` (`CASEWORKER_ROLE`)
- Produces:
  ```text
  # infra/provision_cognito.py
  POOL_NAME = "grace-caseworkers"; CLIENT_NAME = "grace-dashboard"
  ROLE_CLAIM = "custom:role"; ROLE_VALUE = "caseworker"
  def pool_spec() -> dict
  def provision(client=None, callback_urls: list[str] | None = None) -> dict
      # -> {"pool_id", "client_id", "domain", "issuer"}
  ```
  ```ts
  // web/lib/cognito.ts
  export const SESSION_COOKIE = "grace_session";
  export function verifySession(idToken: string | undefined, nowMs?: number): Promise<SessionIdentity | null>;
  export function hostedUiUrl(redirectUri: string): string;
  export function exchangeCode(code: string, redirectUri: string): Promise<string | null>;
  ```

### Why the role lives in a custom claim

Plan 2's Appendix D researched `customJWTAuthorizer` with
`customClaims: [{ inboundTokenClaimName: "role", ... }]` — a claim rule enforced *in the authorizer*
rather than in application code. The same idea applies here one layer up: `custom:role` is set on the
user at creation, Cognito puts it in the ID token, and `verifySession` refuses any token without
exactly `caseworker`. A user who signs in successfully but lacks the claim gets a session of `null`,
not a session with reduced powers — there is no such thing here.

**The `sub` is the identity that reaches storage, and it must stay opaque.** Cognito's `sub` is a UUID;
the email is deliberately *not* read into `SessionIdentity` and never written to a decision row.
Appendix D.4's reason: inbound JWT claims are logged to CloudTrail, which is outside every redaction
Grace has.

- [ ] **Step 1: Write the failing Python test**

`tests/test_infra_cognito.py`:

```python
"""The pool's shape, asserted offline.

A user pool is easy to create with a permissive password policy and no MFA
consideration, and nothing about the running system says so afterwards. These
assertions are cheap and they pin the choices.
"""

from __future__ import annotations

from infra import provision_cognito


def test_the_pool_is_named_for_grace():
    """So `list-user-pools` output can be filtered, and so teardown cannot
    match another project's pool. This account already holds
    `astrolabe-paper-auth` and `rosettaclaw-live-auth`."""
    assert provision_cognito.POOL_NAME.startswith("grace")


def test_the_password_policy_is_not_the_default():
    """Cognito's default minimum is 8 with no symbol requirement. A benefits
    dashboard that can file renewals deserves better, and it costs nothing."""
    policy = provision_cognito.pool_spec()["Policies"]["PasswordPolicy"]
    assert policy["MinimumLength"] >= 12
    assert policy["RequireNumbers"] is True
    assert policy["RequireSymbols"] is True
    assert policy["RequireUppercase"] is True


def test_the_role_claim_is_declared_in_the_schema():
    """A custom attribute must exist in the pool's schema before a user can
    carry it. Setting `custom:role` on a user without declaring it fails at
    user-creation time, which is a confusing place to learn this."""
    names = {a["Name"] for a in provision_cognito.pool_spec()["Schema"]}
    assert "role" in names, names


def test_self_signup_is_disabled():
    """Anyone able to sign themselves up could reach the decide endpoint. The
    pool is admin-create-only: a caseworker account is issued, not requested."""
    cfg = provision_cognito.pool_spec()["AdminCreateUserConfig"]
    assert cfg["AllowAdminCreateUserOnly"] is True


def test_the_client_has_no_secret():
    """A public client. The dashboard runs the code exchange server-side, but a
    generated secret would then have to live in an Amplify env var for no gain —
    and a client secret in a build environment is a credential in a log waiting
    to happen."""
    assert provision_cognito.CLIENT_SPEC["GenerateSecret"] is False


def test_the_client_uses_the_authorization_code_flow():
    """Not implicit. The implicit flow returns the token in the URL fragment,
    which lands in browser history and any referrer; the code flow keeps it in a
    server-side exchange."""
    spec = provision_cognito.CLIENT_SPEC
    assert spec["AllowedOAuthFlows"] == ["code"]
    assert "implicit" not in spec["AllowedOAuthFlows"]
    assert spec["AllowedOAuthFlowsUserPoolClient"] is True


def test_the_scopes_do_not_include_anything_write_shaped():
    """openid gives the `sub`; profile is not needed and would carry name and
    email into a token that CloudTrail logs."""
    assert set(provision_cognito.CLIENT_SPEC["AllowedOAuthScopes"]) == {"openid"}


def test_the_role_attribute_is_explicitly_readable():
    """**The one that makes sign-in work at all.**

    Verified against the live API documentation: when `ReadAttributes` is
    omitted, the client may read only `email_verified`,
    `phone_number_verified`, and the pool's *standard* attributes — a custom
    attribute is not among them. So without naming `custom:role` here it never
    reaches the ID token, `verifySession` refuses every legitimate caseworker,
    and the symptom reads as "auth is broken" rather than "one attribute is
    unreadable". It fails closed, which is the right direction and still means
    nobody can sign in.
    """
    assert provision_cognito.ROLE_CLAIM in provision_cognito.CLIENT_SPEC["ReadAttributes"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_infra_cognito.py -v`
Expected: FAIL — `ImportError: cannot import name 'provision_cognito' from 'infra'`.

- [ ] **Step 3: Write `infra/provision_cognito.py`**

**Five of this draft's assumptions were probed on throwaway pools on 2026-09-04 and all five hold** —
do not re-derive them, and do not "simplify" any of them away:

| Probed | Result |
|---|---|
| `Schema` name `role` → claim name | Round-trips as `custom:role` in `SchemaAttributes`. The `custom:` prefix is Cognito's, not something to write yourself. |
| `ReadAttributes: ["email", "custom:role"]` | Accepted and echoed back verbatim. Omitting it leaves `ReadAttributes` **empty**, which per the API docs means standard attributes only — so `custom:role` would be unreadable and `verifySession` would refuse every caseworker. |
| `admin_create_user` with an immutable custom attribute | Accepted. The user lands in **`FORCE_CHANGE_PASSWORD`**, which cannot sign in — so the `admin_set_user_password(Permanent=True)` call below is **required**, not a convenience. After it, status is `CONFIRMED`. |
| Re-creating the same user | `UsernameExistsException`, exactly as the `except` expects. |
| Re-creating the same domain on the same pool | `InvalidParameterException` with message `Domain already exists.` — the code the `except` already allows. `AliasExistsException` was not observed, but leave it listed; it is the documented code for a domain taken by *another* pool. |

```python
"""The caseworker user pool. Idempotent: re-running is the recovery path.

Cognito rather than a self-hosted auth library, for a reason that is not
fashion: Better Auth ships adapters for drizzle/kysely/memory/mongodb/prisma and
**no DynamoDB**, and this account has no RDS — so self-hosting auth would have
meant either a SQLite file that cannot survive a hosted deployment or a 0.1.0
community adapter holding the authentication layer of a benefits dashboard.
Cognito is a managed directory, so the question disappears.

This also un-defers AgentCore **Identity** from Plan 2, which is why Grace can
honestly claim four surfaces rather than three. Not five: Gateway stays deferred
with its written reason.
"""

from __future__ import annotations

import secrets

import boto3
from botocore.exceptions import ClientError

from infra import naming

POOL_NAME = "grace-caseworkers"
CLIENT_NAME = "grace-dashboard"
DOMAIN_PREFIX = "grace-caseworkers"

# The claim `verifySession` requires. Declared in the pool schema, set on the
# user at creation, and asserted in the ID token — a user who signs in without
# exactly this value gets no session at all, not a lesser one.
ROLE_CLAIM = "custom:role"
ROLE_VALUE = "caseworker"

# A seeded account for the demo. The username is opaque on purpose: Cognito puts
# `sub` (a UUID) in the token and that is what reaches a decision row, but a
# username that looked like a person would invite someone to read it as one.
SEED_USERNAME = "caseworker-01"


CLIENT_SPEC: dict = {
    "ClientName": CLIENT_NAME,
    # Public client. The code exchange happens server-side in a route handler,
    # so a secret buys nothing — and a client secret in an Amplify build
    # environment is a credential one `echo` away from a log.
    "GenerateSecret": False,
    # The authorization-code flow, never implicit: implicit returns the token in
    # the URL fragment, which lands in browser history and any referrer header.
    "AllowedOAuthFlows": ["code"],
    "AllowedOAuthFlowsUserPoolClient": True,
    # `openid` alone. `profile` would carry name and email into a token that
    # CloudTrail logs, and nothing here needs either (Appendix D.4).
    "AllowedOAuthScopes": ["openid"],
    "SupportedIdentityProviders": ["COGNITO"],
    "ExplicitAuthFlows": ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    # **`ReadAttributes` must name `custom:role` explicitly.** Verified against
    # the live API docs: when `ReadAttributes` is omitted, the client can read
    # only `email_verified`, `phone_number_verified`, and the pool's *standard*
    # attributes — a custom attribute is not among them. So leaving this out
    # would keep `custom:role` out of the ID token, `verifySession` would refuse
    # every legitimate caseworker, and the failure would look like "auth is
    # broken" rather than "one attribute is unreadable". Fails closed, which is
    # the right direction and still unusable.
    "ReadAttributes": ["email", ROLE_CLAIM],
    # **`WriteAttributes` is deliberately absent, and must stay absent.** Probed
    # on a throwaway pool: Cognito *accepts* `custom:role` in `WriteAttributes`
    # even though the schema marks it `Mutable: False`. Granting it would let a
    # signed-in caseworker call `UpdateUserAttributes` against their own role —
    # the immutable flag would still refuse, but that is one guard where there
    # should be two. The role is set once by `admin_create_user`, which is an
    # admin API and not bound by this list, so nothing legitimate needs write
    # access. Capability absence again: the client cannot rewrite the claim that
    # authorises it, because it was never granted the ability to try.
    # An hour. Long enough for a caseworker's session, short enough that a
    # leaked token expires before it is useful.
    "IdTokenValidity": 60,
    "AccessTokenValidity": 60,
    "TokenValidityUnits": {"IdToken": "minutes", "AccessToken": "minutes"},
}


def pool_spec() -> dict:
    """The pool's configuration, as data so it is testable without AWS."""
    return {
        "PoolName": POOL_NAME,
        "Policies": {
            "PasswordPolicy": {
                # Cognito's default is 8 with no symbol requirement. This
                # account can file benefit renewals.
                "MinimumLength": 12,
                "RequireUppercase": True,
                "RequireLowercase": True,
                "RequireNumbers": True,
                "RequireSymbols": True,
            }
        },
        # Admin-create-only. Anyone who could sign themselves up would reach the
        # decide endpoint; a caseworker account is issued, not requested.
        "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
        "Schema": [
            {
                "Name": "role",
                "AttributeDataType": "String",
                "Mutable": False,
                "Required": False,
                "StringAttributeConstraints": {"MinLength": "1", "MaxLength": "32"},
            }
        ],
        "UserPoolTags": naming.TAGS,
    }


def provision(client=None, callback_urls: list[str] | None = None) -> dict:
    """Create the pool, client, domain, and one caseworker. Idempotent.

    Returns the four values the dashboard needs as environment variables.
    """
    client = client or boto3.client("cognito-idp", region_name=naming.REGION)
    callback_urls = callback_urls or ["http://localhost:3000/api/auth/callback"]

    # Find an existing Grace pool before creating one. `ListUserPools`
    # paginates, and this account holds other projects' pools — Plan 2 hit the
    # single-page version of this bug three separate times.
    pool_id: str | None = None
    token: str | None = None
    while True:
        kwargs = {"MaxResults": 60}
        if token:
            kwargs["NextToken"] = token
        page = client.list_user_pools(**kwargs)
        for pool in page.get("UserPools", []):
            if pool["Name"] == POOL_NAME:
                pool_id = pool["Id"]
                break
        token = page.get("NextToken")
        if pool_id or not token:
            break

    if pool_id is None:
        pool_id = client.create_user_pool(**pool_spec())["UserPool"]["Id"]

    # The app client, likewise found-or-created.
    client_id: str | None = None
    for existing in client.list_user_pool_clients(
        UserPoolId=pool_id, MaxResults=60
    ).get("UserPoolClients", []):
        if existing["ClientName"] == CLIENT_NAME:
            client_id = existing["ClientId"]
            break
    spec = {**CLIENT_SPEC, "UserPoolId": pool_id, "CallbackURLs": callback_urls,
            "LogoutURLs": [u.replace("/api/auth/callback", "/login") for u in callback_urls]}
    if client_id is None:
        client_id = client.create_user_pool_client(**spec)["UserPoolClient"]["ClientId"]
    else:
        # Converge: a re-run must apply the intended callback URLs, not leave
        # whatever a previous run wrote.
        client.update_user_pool_client(**{**spec, "ClientId": client_id})

    # The hosted UI domain. One API call, and it saves building sign-in forms.
    try:
        client.create_user_pool_domain(Domain=DOMAIN_PREFIX, UserPoolId=pool_id)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {
            "InvalidParameterException",  # already exists on this pool
            "AliasExistsException",
        }:
            raise

    # One caseworker, with the role claim. A generated password printed once.
    try:
        password = f"Gr{secrets.token_urlsafe(16)}!7"
        client.admin_create_user(
            UserPoolId=pool_id,
            Username=SEED_USERNAME,
            MessageAction="SUPPRESS",
            UserAttributes=[{"Name": ROLE_CLAIM, "Value": ROLE_VALUE}],
            TemporaryPassword=password,
        )
        client.admin_set_user_password(
            UserPoolId=pool_id, Username=SEED_USERNAME,
            Password=password, Permanent=True,
        )
        print(f"seeded {SEED_USERNAME} with password: {password}")
        print("record it now — it is not recoverable")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "UsernameExistsException":
            raise

    return {
        "pool_id": pool_id,
        "client_id": client_id,
        "domain": f"https://{DOMAIN_PREFIX}.auth.{naming.REGION}.amazoncognito.com",
        "issuer": f"https://cognito-idp.{naming.REGION}.amazonaws.com/{pool_id}",
    }


if __name__ == "__main__":
    for key, value in provision().items():
        print(f"{key}: {value}")
```

- [ ] **Step 4: Run the Python tests and provision for real**

```bash
.venv/bin/python -m pytest tests/test_infra_cognito.py -v
export AWS_PAGER=""
.venv/bin/python -m infra.provision_cognito
.venv/bin/python -m infra.provision_cognito   # idempotence: same ids, no new user
```

Expected: 7 tests pass; both runs print the same `pool_id` and `client_id`. **Record the seeded
password from the first run** — it is printed once and is not recoverable. Then verify the claim
actually landed on the user, because a missing custom attribute is silent until sign-in:

```bash
POOL=$(.venv/bin/python -c "from infra.provision_cognito import provision; print(provision()['pool_id'])")
aws cognito-idp admin-get-user --user-pool-id "$POOL" --username caseworker-01 \
  --region us-east-1 --query 'UserAttributes[?Name==`custom:role`]'
```

Expected: `[{"Name": "custom:role", "Value": "caseworker"}]`.

- [ ] **Step 5: Write the failing session-verification test**

`web/__tests__/cognito.test.ts`:

```ts
import { describe, expect, it, beforeAll } from "vitest";
import { SignJWT, exportJWK, generateKeyPair } from "jose";
import { verifySession } from "@/lib/cognito";
import { CASEWORKER_ROLE } from "@/lib/authorize";

const NOW = 1_788_400_000_000;
const ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TESTPOOL";
const CLIENT_ID = "test-client-id";

let sign: (claims: Record<string, unknown>, opts?: { alg?: string; expSec?: number }) => Promise<string>;

beforeAll(async () => {
  const { privateKey, publicKey } = await generateKeyPair("RS256");
  const jwk = { ...(await exportJWK(publicKey)), kid: "test-kid", alg: "RS256", use: "sig" };
  process.env.COGNITO_ISSUER = ISSUER;
  process.env.COGNITO_CLIENT_ID = CLIENT_ID;
  // Inject the key set so the verifier never reaches the network in tests.
  process.env.COGNITO_TEST_JWKS = JSON.stringify({ keys: [jwk] });
  sign = async (claims, opts = {}) =>
    new SignJWT({ token_use: "id", aud: CLIENT_ID, iss: ISSUER, ...claims })
      .setProtectedHeader({ alg: opts.alg ?? "RS256", kid: "test-kid" })
      .setIssuedAt(Math.floor(NOW / 1000) - 10)
      .setExpirationTime(Math.floor(NOW / 1000) + (opts.expSec ?? 3600))
      .sign(privateKey);
});

describe("verifySession — refusals", () => {
  it("returns null for a missing cookie", async () => {
    expect(await verifySession(undefined, NOW)).toBeNull();
  });

  it("returns null for a token that is not a JWT at all", async () => {
    expect(await verifySession("not-a-token", NOW)).toBeNull();
  });

  it("returns null for a token signed by the wrong key", async () => {
    const { privateKey } = await generateKeyPair("RS256");
    const forged = await new SignJWT({
      token_use: "id", aud: CLIENT_ID, iss: ISSUER,
      sub: "attacker", "custom:role": CASEWORKER_ROLE,
    })
      .setProtectedHeader({ alg: "RS256", kid: "test-kid" })
      .setExpirationTime(Math.floor(NOW / 1000) + 3600)
      .sign(privateKey);
    expect(await verifySession(forged, NOW)).toBeNull();
  });

  it("returns null for an expired token", async () => {
    const token = await sign({ sub: "s", "custom:role": CASEWORKER_ROLE }, { expSec: -60 });
    expect(await verifySession(token, NOW)).toBeNull();
  });

  it("returns null for the wrong issuer", async () => {
    const token = await sign({ sub: "s", "custom:role": CASEWORKER_ROLE, iss: "https://evil.example" });
    expect(await verifySession(token, NOW)).toBeNull();
  });

  it("returns null for the wrong audience", async () => {
    const token = await sign({ sub: "s", "custom:role": CASEWORKER_ROLE, aud: "another-client" });
    expect(await verifySession(token, NOW)).toBeNull();
  });

  it("returns null for an access token used as an ID token", async () => {
    // `token_use` distinguishes them. An access token carries no role claim, so
    // accepting one would authenticate a session with no authorisation basis.
    const token = await sign({ sub: "s", "custom:role": CASEWORKER_ROLE, token_use: "access" });
    expect(await verifySession(token, NOW)).toBeNull();
  });

  it("returns null when the role claim is absent", async () => {
    const token = await sign({ sub: "s" });
    expect(await verifySession(token, NOW)).toBeNull();
  });

  it("returns null when the role claim is close but not exact", async () => {
    for (const role of ["Caseworker", "caseworker ", "case-worker", "admin", ""]) {
      const token = await sign({ sub: "s", "custom:role": role });
      expect(await verifySession(token, NOW), role).toBeNull();
    }
  });

  it("returns null for an unsigned (alg: none) token", async () => {
    // The classic JWT bypass. `jose` should refuse it, but assert rather than
    // assume — this is the one that turns a verifier into a decoder.
    const unsigned = `${Buffer.from(JSON.stringify({ alg: "none", kid: "test-kid" })).toString("base64url")}.${
      Buffer.from(JSON.stringify({
        sub: "attacker", "custom:role": CASEWORKER_ROLE, iss: ISSUER,
        aud: CLIENT_ID, token_use: "id", exp: Math.floor(NOW / 1000) + 3600,
      })).toString("base64url")}.`;
    expect(await verifySession(unsigned, NOW)).toBeNull();
  });
});

describe("verifySession — the one acceptance", () => {
  it("accepts a correctly signed caseworker ID token", async () => {
    const token = await sign({ sub: "7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d", "custom:role": CASEWORKER_ROLE });
    const session = await verifySession(token, NOW);
    expect(session).not.toBeNull();
    expect(session!.sub).toBe("7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d");
    expect(session!.role).toBe(CASEWORKER_ROLE);
    expect(session!.expiresAt).toBeGreaterThan(NOW);
  });

  it("carries no email or name into the session", async () => {
    // Hard rule 9's reasoning for the caseworker: the token's claims are logged
    // to CloudTrail. `sub` is opaque; email is not, and nothing needs it.
    const token = await sign({
      sub: "7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d",
      "custom:role": CASEWORKER_ROLE,
      email: "someone@example.com", name: "A Person",
    });
    const session = await verifySession(token, NOW);
    expect(JSON.stringify(session)).not.toContain("@example.com");
    expect(JSON.stringify(session)).not.toContain("A Person");
    expect(Object.keys(session!).sort()).toEqual(["expiresAt", "role", "sub"]);
  });
});
```

- [ ] **Step 6: Write `web/lib/cognito.ts`**

```ts
/**
 * TURNING A COOKIE INTO AN IDENTITY, OR INTO NOTHING.
 *
 * `verifySession` returns a `SessionIdentity` or `null`. There is no middle
 * value — a token that fails any check produces `null`, and `authorize` refuses
 * on a null session. That is deliberate: a partially-trusted session is a thing
 * nobody can reason about.
 *
 * Every check here is one an attacker would otherwise skip: the signature
 * against Cognito's published keys, the issuer, the audience, the expiry, that
 * it is an **ID** token and not an access token, and that the role claim is
 * exactly `caseworker`. `jose` performs the cryptography; the value this file
 * adds is refusing everything else.
 *
 * Only `sub`, `role`, and the expiry reach `SessionIdentity`. Email and name are
 * deliberately dropped: inbound JWT claims are logged to CloudTrail, which is
 * outside every redaction Grace has (Plan 2, Appendix D.4), and a decision row
 * records the opaque `sub`.
 */

import { createRemoteJWKSet, jwtVerify, type JWTPayload } from "jose";
import { CASEWORKER_ROLE } from "./authorize";
import type { SessionIdentity } from "./types";

export const SESSION_COOKIE = "grace_session";

const ROLE_CLAIM = "custom:role";

function issuer(): string {
  const value = process.env.COGNITO_ISSUER;
  if (!value) throw new Error("COGNITO_ISSUER is not set.");
  return value;
}

function clientId(): string {
  const value = process.env.COGNITO_CLIENT_ID;
  if (!value) throw new Error("COGNITO_CLIENT_ID is not set.");
  return value;
}

type KeyResolver = Parameters<typeof jwtVerify>[1];

let cachedKeys: KeyResolver | undefined;
function keys(): KeyResolver {
  // Tests inject a key set so no test reaches the network. The `NODE_ENV`
  // guard is the load-bearing part: without it, setting COGNITO_TEST_JWKS on
  // the deployed app replaces Cognito's real key set with an attacker-supplied
  // one, and every forged token verifies. An env var that swaps out the trust
  // anchor must never be readable in production. Verified: vitest sets
  // NODE_ENV="test" (and VITEST="true"); `next build`/`next start` set
  // "production".
  const injected =
    process.env.NODE_ENV === "test" ? process.env.COGNITO_TEST_JWKS : undefined;
  if (injected) {
    const parsed = JSON.parse(injected) as { keys: Record<string, unknown>[] };
    // `createLocalJWKSet(parsed)` is the supported equivalent and was verified
    // to refuse a wrong key, alg:"none", and HS256 confusion identically; a
    // resolver is used here only to keep the shape parallel to the remote one.
    return (async (header: { kid?: string }) => {
      const { importJWK } = await import("jose");
      const jwk = parsed.keys.find(k => k.kid === header.kid) ?? parsed.keys[0];
      return importJWK(jwk as never, "RS256");
    }) as unknown as KeyResolver;
  }
  cachedKeys ??= createRemoteJWKSet(
    new URL(`${issuer()}/.well-known/jwks.json`),
  ) as unknown as KeyResolver;
  return cachedKeys;
}

export async function verifySession(
  idToken: string | undefined,
  nowMs: number = Date.now(),
): Promise<SessionIdentity | null> {
  if (!idToken) return null;

  let payload: JWTPayload;
  try {
    ({ payload } = await jwtVerify(idToken, keys(), {
      issuer: issuer(),
      audience: clientId(),
      // `jose` refuses `alg: "none"` and anything not listed here.
      algorithms: ["RS256"],
      currentDate: new Date(nowMs),
    }));
  } catch {
    // Any cryptographic or claim failure is the same answer: no session.
    return null;
  }

  // An access token has no role claim, so accepting one would authenticate a
  // session with no authorisation basis behind it.
  if (payload.token_use !== "id") return null;

  const role = payload[ROLE_CLAIM];
  // Exact match. `"Caseworker"` and `"caseworker "` are not this role — the same
  // allowlist discipline `authorize` applies to the decision word.
  if (role !== CASEWORKER_ROLE) return null;

  const sub = payload.sub;
  if (typeof sub !== "string" || sub === "") return null;
  // `Number.isFinite`, not `typeof === "number"`: `exp: 1e400` in a JWT payload
  // parses to `Infinity`, which IS a number, and `jose` verifies such a token —
  // both measured during Task 2. `Infinity` then becomes an `expiresAt` that no
  // `<=` comparison can ever call expired. `authorize` refuses a non-finite
  // expiry independently (defence in depth, since this file is not its only
  // caller), but the token should not get this far.
  if (!Number.isFinite(payload.exp)) return null;

  // Only these three. Email and name are dropped on purpose.
  return { sub, role: CASEWORKER_ROLE, expiresAt: payload.exp * 1000 };
}

/** Where to send a signed-out visitor. */
export function hostedUiUrl(redirectUri: string): string {
  const domain = process.env.COGNITO_DOMAIN;
  if (!domain) throw new Error("COGNITO_DOMAIN is not set.");
  const params = new URLSearchParams({
    client_id: clientId(),
    response_type: "code",
    scope: "openid",
    redirect_uri: redirectUri,
  });
  return `${domain}/login?${params.toString()}`;
}

/** Exchange an authorization code for an ID token. Server-side only. */
export async function exchangeCode(
  code: string,
  redirectUri: string,
): Promise<string | null> {
  const domain = process.env.COGNITO_DOMAIN;
  if (!domain) throw new Error("COGNITO_DOMAIN is not set.");
  const response = await fetch(`${domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: clientId(),
      code,
      redirect_uri: redirectUri,
    }),
  });
  if (!response.ok) return null;
  const body = (await response.json()) as { id_token?: string };
  return body.id_token ?? null;
}
```

- [ ] **Step 7: Write the middleware, the login page, and the callback**

`web/middleware.ts`:

```ts
import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/cognito";

/**
 * Every route requires a session cookie, except the two that establish one.
 *
 * This is a *presence* check, not verification: middleware runs on the edge
 * runtime where the JWKS fetch and `jose` verification belong badly. Each page
 * and the decide route verify the token properly server-side. So middleware is
 * a redirect convenience, and **never the security boundary** — a forged cookie
 * gets past it and is then refused by `verifySession`, which is the check that
 * matters. `__tests__/route-guard.test.ts` proves the write path refuses on its
 * own, with no middleware involved.
 */
export function middleware(request: NextRequest) {
  if (request.cookies.get(SESSION_COOKIE)) return NextResponse.next();
  const login = new URL("/login", request.url);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/((?!login|api/auth|_next/static|_next/image|favicon.ico).*)"],
};
```

`web/app/login/page.tsx`:

```tsx
import { redirect } from "next/navigation";
import { hostedUiUrl } from "@/lib/cognito";

export default function Login() {
  const base = process.env.DASHBOARD_URL ?? "http://localhost:3000";
  redirect(hostedUiUrl(`${base}/api/auth/callback`));
}
```

`web/app/api/auth/callback/route.ts`:

```ts
import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, exchangeCode, verifySession } from "@/lib/cognito";

export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  const base = process.env.DASHBOARD_URL ?? request.nextUrl.origin;
  if (!code) return NextResponse.redirect(new URL("/login", base));

  const idToken = await exchangeCode(code, `${base}/api/auth/callback`);
  // Verify the token we just received before trusting it into a cookie. The
  // exchange succeeding is not the same claim as the token being usable.
  if (!idToken || (await verifySession(idToken)) === null) {
    return NextResponse.redirect(new URL("/login", base));
  }

  const response = NextResponse.redirect(new URL("/queue", base));
  response.cookies.set(SESSION_COOKIE, idToken, {
    httpOnly: true,   // no script can read it
    secure: true,     // https only
    sameSite: "lax",  // survives the OAuth redirect, refuses cross-site POSTs
    path: "/",
    maxAge: 60 * 60,
  });
  return response;
}
```

- [ ] **Step 8: Run every test**

```bash
cd web && npm run test && npm run typecheck && npm run build
cd .. && .venv/bin/python -m pytest
```

Expected: the 13 `cognito.test.ts` assertions pass, the build succeeds, and Python is **622 passed**.

Add this thirteenth test, which pins the guard that makes the injection mechanism safe:

```ts
it("ignores an injected key set outside a test environment", async () => {
  // COGNITO_TEST_JWKS swaps out the trust anchor. If production code reads it,
  // setting it on the deployed app makes every forged token verify. Proving the
  // guard means proving the *same* token stops verifying when NODE_ENV changes.
  const token = await sign({ sub: "s", "custom:role": CASEWORKER_ROLE });
  expect(await verifySession(token, NOW)).not.toBeNull();

  const original = process.env.NODE_ENV;
  try {
    // A network fetch to the fake issuer cannot succeed, so the only way this
    // returns a session is by reading the injected keys it must now ignore.
    Object.defineProperty(process.env, "NODE_ENV", { value: "production", configurable: true });
    vi.resetModules();
    const { verifySession: prod } = await import("@/lib/cognito");
    expect(await prod(token, NOW)).toBeNull();
  } finally {
    Object.defineProperty(process.env, "NODE_ENV", { value: original, configurable: true });
    vi.resetModules();
  }
});
```

`vi.resetModules()` matters both times: `cachedKeys` is module-level, so a stale remote resolver
cached under one `NODE_ENV` would answer for the other. Import `vi` from `vitest`.

- [ ] **Step 9: Prove the verifier's refusals are real**

Delete the `payload.token_use !== "id"` check and re-run: the access-token test **must fail**. Then
loosen the role comparison to `String(role).toLowerCase().trim() === CASEWORKER_ROLE` and re-run: the
close-but-not-exact test **must fail** on `"Caseworker"`. Then drop the `NODE_ENV === "test"` guard
back to a bare `process.env.COGNITO_TEST_JWKS` and re-run: the injected-key-set test **must fail**.
Restore all three.

Report what failed. A verifier whose refusals have never been watched to fail is a decoder.

- [ ] **Step 10: Commit**

```bash
git add infra/provision_cognito.py tests/test_infra_cognito.py \
        web/lib/cognito.ts web/middleware.ts web/app/login web/app/api/auth
git commit -m "feat: Cognito caseworker pool and server-side session verification"
```

---
## Task 5: `lib/decide.ts` and the one write route

The application's only write. A caseworker's decision becomes a durable row, and then Grace
re-evaluates the case **through the authority gate**, which may still refuse to file.

**Files:**
- Create: `web/lib/decide.ts`, `web/app/api/case/[id]/decide/route.ts`
- Modify: `grace/entrypoint.py` — accept an optional `caseworker_approved` flag (additive; see Step 1)
- Test: `web/__tests__/decide.test.ts`, `web/__tests__/route-guard.test.ts`, `tests/test_entrypoint_approval.py`

**Interfaces:**
- Consumes: `web/lib/authorize.ts` (`authorize`, `Permit`), `web/lib/cases.ts` (`readFacts`), `web/lib/cognito.ts` (`verifySession`, `SESSION_COOKIE`), `web/lib/env.ts`
- Produces:
  ```ts
  export interface DecisionOutcome { recorded: true; caseId: string; decision: "approve" | "deny"; graceOutcome: string; filed: boolean; }
  export function recordDecision(permit: Permit, caseId: string, clients?: { dynamo?: DynamoDBClient; runtime?: BedrockAgentCoreClient }): Promise<DecisionOutcome>;
  ```
  ```text
  # grace/entrypoint.py, additive
  def process_case(payload: dict, store=None, channel=None) -> CaseOutcome
      # payload may now carry "caseworker_approved": bool
  ```

### The ordering, and why it is the opposite of `action.py`

The decision row is written **before** the runtime is invoked. `grace/tools/action.py` does the
opposite — it sends first and logs after — because a ledger row claiming an unconfirmed action is
worse than no row (hard rule 6). Both are right, because they claim different things: the ledger row
claims *Grace did something*, which is only true once the tool returned; the decision row claims *a
human decided*, which is true the moment they clicked. Losing that to an infrastructure error would
discard the human's work and leave the case silently unresolved.

### What `caseworker_approved` may and may not do

It reaches `process_case` and is recorded. It **does not** touch `evaluate()`, the gate, or the tool
list. Its only effect is on the *wording* Grace uses when a case is still escalated, and on the
`outcome` written back to the decision row. Approving `c-010` re-runs the identical gate against the
identical facts — the document is still missing, so nothing is filed. That is Task 5's headline test
and the one to record for the demo video.

- [ ] **Step 1: Add the flag to `grace/entrypoint.py`, additively**

`grace/entrypoint.py` is in Plan 2's territory but not in the four protected decision-path files, and
this change adds a field without altering classification. In `process_case`, after `today` is parsed:

```python
    # A caseworker's approval, when the dashboard re-invokes a case they decided.
    # **This does not reach the gate.** `evaluate()` runs on the case record
    # exactly as before, and the tool list is unchanged — approving a household
    # that is still missing a document still files nothing. The flag only affects
    # the wording of an escalation reason and the `outcome` the dashboard records,
    # so a caseworker can see that Grace re-checked and still refused.
    #
    # Deliberately NOT a resume: Plan 1's Task 6 proved a truthy resume response
    # approves the blocked tool, so there is no interrupt to answer here.
    caseworker_approved = payload.get("caseworker_approved") is True
```

Then append one clause when the flag is set. **`process_case` has three separate `_escalate` call
sites, not one** — the gate-reason branch, the "gate is clean but the run did not finish" branch, and
the "clean case, clean run, but no renewal was filed" branch. A single edit at "where the escalation
reason is assembled" therefore covers one of three, and the two it misses include the hard-rule-6
branch. Verify by reading `grace/entrypoint.py` and counting `_escalate(` yourself before editing.

The clause belongs where every path passes through, which is `_escalate` itself:

```python
        if caseworker_approved:
            detail = (
                f"{detail} (A caseworker approved this case; Grace re-checked and "
                "the gate still requires a human, so nothing was filed.)"
            )
```

If you thread it through `_escalate`, that function's signature gains a keyword-only
`caseworker_approved: bool = False` and every call site passes it explicitly — an added parameter with
a default that nobody passes is a clause that never appears. Add a test per call site, and assert the
clause is absent when the flag is false, or a test that only ever sets it true cannot tell the
difference between "appended on approval" and "appended always".

**Change nothing else.** Do not pass the flag to `build_case_graph`, `evaluate`, or any tool.

- [ ] **Step 2: Write the failing Python test**

`tests/test_entrypoint_approval.py`:

```python
"""A caseworker's approval is an input to the gate's decision, never a bypass.

The dashboard's whole safety argument rests on this file: `c-010` is missing
`proof_of_residency`, and approving it must still file nothing.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date

from strands.multiagent.base import Status

from grace import entrypoint
from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.tools.action import TranscriptChannel

TODAY = "2026-10-01"


class FakeGraph:
    def __init__(self, status=Status.COMPLETED):
        self._status = status
        self.calls = 0

    def __call__(self, task):
        self.calls += 1
        return self

    @property
    def status(self):
        return self._status

    @property
    def interrupts(self):
        return []

    @property
    def results(self):
        return {}


def _store():
    return InMemoryCaseStore(load_fixture_cases())


def test_approving_a_case_missing_a_document_still_does_not_file(monkeypatch):
    """The headline safety property, and the one to show in the demo.

    `c-010` is missing `proof_of_residency`. A caseworker approving it changes
    nothing about that fact, so `evaluate()` still says escalate and no renewal
    is filed.
    """
    store = _store()
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: FakeGraph())
    out = entrypoint.process_case(
        {"case_id": "c-010", "today": TODAY, "caseworker_approved": True},
        store=store, channel=TranscriptChannel(),
    )
    assert out["status"] == "escalated"
    assert out.get("filed") is not True
    assert not any(e.kind == "renewal_submitted" for e in store.ledger("c-010"))


def test_the_approval_is_visible_in_the_reason(monkeypatch):
    """So a caseworker can tell "Grace re-checked and still refused" apart from
    "nothing happened"."""
    store = _store()
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: FakeGraph())
    out = entrypoint.process_case(
        {"case_id": "c-010", "today": TODAY, "caseworker_approved": True},
        store=store, channel=TranscriptChannel(),
    )
    assert "caseworker approved" in out["reason"].lower()
    assert "missing_document" in out["reason"]


def test_the_flag_does_not_change_the_verdict_for_any_fixture(monkeypatch):
    """Structural: across all twelve households, approving changes no case's
    status. If it ever does, the flag has reached the gate."""
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: FakeGraph())
    checked = 0
    for n in range(1, 13):
        case_id = f"c-{n:03d}"
        plain = entrypoint.process_case(
            {"case_id": case_id, "today": TODAY},
            store=_store(), channel=TranscriptChannel())
        approved = entrypoint.process_case(
            {"case_id": case_id, "today": TODAY, "caseworker_approved": True},
            store=_store(), channel=TranscriptChannel())
        assert plain["status"] == approved["status"], case_id
        checked += 1
    assert checked == 12, "the loop must actually run for every fixture"


def test_a_non_boolean_flag_is_not_treated_as_approval():
    """`payload.get(...) is True`, not truthiness. The payload arrives from an
    HTTP body; `"false"`, `"no"`, and `1` are all truthy in Python, and this is
    the same allowlist-over-truthiness discipline the resume path taught."""
    for value in ["true", "false", 1, 0, "yes", [], {}, None]:
        out = entrypoint.process_case(
            {"case_id": "c-010", "today": TODAY, "caseworker_approved": value},
            store=_store(), channel=TranscriptChannel())
        # Whatever happens, nothing files and no case is mis-marked.
        assert out.get("filed") is not True, value


def test_the_flag_never_reaches_the_gate_or_the_graph():
    """Structural, so a later edit cannot quietly wire it through.

    `evaluate` decides from the case record alone. If `caseworker_approved`
    appeared in a call to `build_case_graph`, `evaluate`, or `gate_reason`, the
    gate would be taking a caseworker's word for a fact it is supposed to check.
    """
    tree = ast.parse(inspect.getsource(entrypoint))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if callee not in {"build_case_graph", "evaluate", "gate_reason"}:
            continue
        rendered = ast.dump(node)
        assert "caseworker_approved" not in rendered, (
            f"{callee} must not receive the approval flag"
        )


def test_the_deployed_path_still_carries_no_resume_vocabulary():
    """Plan 2's guard, re-asserted here because this task is exactly the
    pressure that would reintroduce a resume."""
    source = inspect.getsource(entrypoint)
    for forbidden in ("interruptResponse", "APPROVE_DECISIONS", "MAX_RESUME_ROUNDS"):
        assert forbidden not in source, forbidden
```

- [ ] **Step 3: Run the Python tests**

```bash
.venv/bin/python -m pytest tests/test_entrypoint_approval.py -v
.venv/bin/python -m pytest
```

Expected: 6 new tests pass and the suite is **628 passed** (622 + 6). Report the real number.

- [ ] **Step 4: Write the failing TypeScript tests**

`web/__tests__/decide.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { recordDecision } from "@/lib/decide";
import type { Permit } from "@/lib/authorize";

class FakeDynamo {
  public puts: Array<Record<string, unknown>> = [];
  constructor(private failOnPut = false) {}
  async send(command: { input: Record<string, unknown> }): Promise<unknown> {
    if (this.failOnPut) throw new Error("dynamodb refused the write");
    this.puts.push(command.input);
    return {};
  }
}

class FakeRuntime {
  public invocations: Array<Record<string, unknown>> = [];
  constructor(private body: Record<string, unknown> = { status: "escalated", case_id: "c-010" }) {}
  async send(command: { input: Record<string, unknown> }): Promise<unknown> {
    this.invocations.push(command.input);
    return { response: new TextEncoder().encode(JSON.stringify(this.body)) };
  }
}

const permit = (over: Partial<Permit> = {}): Permit => ({
  permitted: true,
  decidedBy: "7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d",
  decision: "approve",
  note: "Wage record is stale.",
  ...over,
});

function withEnv<T>(run: () => Promise<T>): Promise<T> {
  process.env.GRACE_TABLE_NAME = "grace-cases";
  process.env.GRACE_ESCALATION_INDEX = "escalation-queue";
  process.env.GRACE_RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-east-1:1:runtime/grace";
  process.env.AWS_REGION = "us-east-1";
  return run();
}

describe("recordDecision", () => {
  it("builds a runtime client that cannot retry a non-idempotent invocation", () =>
    withEnv(async () => {
      // Not tuning — a safety property, and asserted because a config value
      // nobody checks can be deleted silently. `InvokeAgentRuntime` re-runs the
      // whole graph per attempt, so a retry could file one renewal twice.
      // Measured against a black-hole socket: the JS SDK default is **3**
      // attempts, `maxAttempts: 1` is exactly 1. (boto3 differs: `max_attempts:
      // 1` still gave 2 there, only `total_max_attempts` gave 1.)
      //
      // No client is injected here, so the module builds its own — which is the
      // path the deployed route takes and the only path this config is on.
      const { BedrockAgentCoreClient } = await import("@aws-sdk/client-bedrock-agentcore");
      const built: unknown[] = [];
      const spy = vi.spyOn(BedrockAgentCoreClient.prototype, "send")
        .mockImplementation(async function (this: unknown) {
          built.push(this);
          return { response: new TextEncoder().encode(JSON.stringify({ status: "escalated" })) };
        } as never);
      try {
        await recordDecision(permit(), "c-010", { dynamo: new FakeDynamo() as never });
        expect(built.length).toBe(1);
        const client = built[0] as { config: { maxAttempts: unknown } };
        // `maxAttempts` is a provider on a resolved client config.
        const attempts = typeof client.config.maxAttempts === "function"
          ? await (client.config.maxAttempts as () => Promise<number>)()
          : client.config.maxAttempts;
        expect(attempts).toBe(1);
      } finally {
        spy.mockRestore();
      }
    }));

  it("writes the decision row BEFORE invoking the runtime", () =>
    withEnv(async () => {
      // The opposite ordering to action.py, and deliberately so: the row claims
      // "a human decided", which is true the moment they did. Losing it to an
      // invocation failure would discard the caseworker's work.
      const order: string[] = [];
      const dynamo = { send: async () => { order.push("row"); return {}; } };
      const runtime = {
        send: async () => {
          order.push("invoke");
          return { response: new TextEncoder().encode(JSON.stringify({ status: "escalated" })) };
        },
      };
      await recordDecision(permit(), "c-010",
        { dynamo: dynamo as never, runtime: runtime as never });
      expect(order).toEqual(["row", "invoke"]);
    }));

  it("records the opaque sub, never a name", () =>
    withEnv(async () => {
      const dynamo = new FakeDynamo();
      await recordDecision(permit(), "c-010",
        { dynamo: dynamo as never, runtime: new FakeRuntime() as never });
      const item = dynamo.puts[0]!.Item as Record<string, { S?: string }>;
      expect(item.decided_by!.S).toBe("7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d");
      expect(JSON.stringify(item)).not.toContain("@");
    }));

  it("keys the row so it cannot overwrite a ledger row or an earlier decision", () =>
    withEnv(async () => {
      const dynamo = new FakeDynamo();
      await recordDecision(permit(), "c-011",
        { dynamo: dynamo as never, runtime: new FakeRuntime() as never });
      const item = dynamo.puts[0]!.Item as Record<string, { S?: string }>;
      expect(item.pk!.S).toBe("CASE#c-011");
      expect(item.sk!.S).toMatch(/^DECISION#\d{4}-\d{2}-\d{2}T/);
      // UTC, so the sort key orders correctly bytewise — Plan 2's finding that a
      // non-UTC offset sorts a later instant before an earlier one.
      expect(item.sk!.S).toMatch(/\+00:00$|Z$/);
    }));

  it("sends caseworker_approved: true only for an approve", () =>
    withEnv(async () => {
      const approved = new FakeRuntime();
      await recordDecision(permit({ decision: "approve" }), "c-010",
        { dynamo: new FakeDynamo() as never, runtime: approved as never });
      const payload = JSON.parse(new TextDecoder().decode(
        approved.invocations[0]!.payload as Uint8Array));
      expect(payload.caseworker_approved).toBe(true);
      expect(payload.case_id).toBe("c-010");
    }));

  it("does not re-invoke Grace at all for a deny", () =>
    withEnv(async () => {
      // A deny means "leave it escalated". There is nothing for Grace to do, and
      // an invocation would cost real Bedrock for no decision.
      const runtime = new FakeRuntime();
      const out = await recordDecision(permit({ decision: "deny" }), "c-010",
        { dynamo: new FakeDynamo() as never, runtime: runtime as never });
      expect(runtime.invocations).toHaveLength(0);
      expect(out.filed).toBe(false);
      expect(out.graceOutcome).toContain("not re-run");
    }));

  it("reports filed: false when Grace escalates again", () =>
    withEnv(async () => {
      const out = await recordDecision(permit(), "c-010", {
        dynamo: new FakeDynamo() as never,
        runtime: new FakeRuntime({ status: "escalated", case_id: "c-010",
          reason: "missing_document: proof_of_residency is not on file" }) as never,
      });
      expect(out.filed).toBe(false);
      expect(out.graceOutcome).toContain("missing_document");
    }));

  it("reports filed: true only when Grace says it acted and filed", () =>
    withEnv(async () => {
      const out = await recordDecision(permit(), "c-011", {
        dynamo: new FakeDynamo() as never,
        runtime: new FakeRuntime({ status: "acted", case_id: "c-011", filed: true }) as never,
      });
      expect(out.filed).toBe(true);
    }));

  it("does not claim a filing when the runtime says acted without filed", () =>
    withEnv(async () => {
      // Hard rule 6 at this boundary: only a confirmed filing is reported.
      const out = await recordDecision(permit(), "c-011", {
        dynamo: new FakeDynamo() as never,
        runtime: new FakeRuntime({ status: "acted", case_id: "c-011" }) as never,
      });
      expect(out.filed).toBe(false);
    }));

  it("propagates a failed row write instead of invoking Grace", () =>
    withEnv(async () => {
      // If the human's decision could not be recorded, do not act on it — the
      // audit trail would then have Grace filing with no record of who asked.
      const runtime = new FakeRuntime();
      await expect(recordDecision(permit(), "c-010",
        { dynamo: new FakeDynamo(true) as never, runtime: runtime as never }))
        .rejects.toThrow(/refused the write/);
      expect(runtime.invocations).toHaveLength(0);
    }));

  it("survives a runtime failure with the decision still recorded", () =>
    withEnv(async () => {
      const dynamo = new FakeDynamo();
      const broken = { send: () => Promise.reject(new Error("runtime unavailable")) };
      const out = await recordDecision(permit(), "c-010",
        { dynamo: dynamo as never, runtime: broken as never });
      expect(dynamo.puts).toHaveLength(2);   // the decision, then the outcome
      expect(out.filed).toBe(false);
      expect(out.graceOutcome).toContain("runtime unavailable");
    }));

  it("carries no resume vocabulary", async () => {
    const src = await import("node:fs").then(fs =>
      fs.readFileSync(new URL("../lib/decide.ts", import.meta.url), "utf8"));
    for (const forbidden of ["interruptResponse", "APPROVE_DECISIONS", "MAX_RESUME_ROUNDS"]) {
      expect(src, forbidden).not.toContain(forbidden);
    }
  });
});
```

`web/__tests__/route-guard.test.ts`:

```ts
import { describe, expect, it, vi, beforeEach } from "vitest";

/** The write route must refuse without a session AND write nothing. Both halves
 *  matter: a refusal that still wrote a row would be the whole point missed. */

const writes: unknown[] = [];
vi.mock("@/lib/decide", () => ({
  recordDecision: vi.fn(async () => {
    writes.push("wrote");
    return { recorded: true, caseId: "c-010", decision: "approve", graceOutcome: "", filed: false };
  }),
}));
vi.mock("@/lib/cases", () => ({
  readFacts: vi.fn(async () => ({ caseId: "c-010", status: "escalated", alreadyDecided: false })),
}));

beforeEach(() => { writes.length = 0; });

async function post(body: unknown, cookie?: string) {
  const { POST } = await import("@/app/api/case/[id]/decide/route");
  const headers = new Headers({ "content-type": "application/json" });
  if (cookie) headers.set("cookie", `grace_session=${cookie}`);
  const request = new Request("http://localhost:3000/api/case/c-010/decide", {
    method: "POST", headers, body: JSON.stringify(body),
  });
  return POST(request as never, { params: Promise.resolve({ id: "c-010" }) } as never);
}

describe("the decide route", () => {
  it("returns 401 and writes NOTHING without a session cookie", async () => {
    const response = await post({ decision: "approve", note: "" });
    expect(response.status).toBe(401);
    expect(writes, "a refused request must not write").toHaveLength(0);
  });

  it("returns 401 and writes nothing for a forged cookie", async () => {
    // Middleware only checks presence; verification happens here. A forged
    // cookie gets past the redirect and must still be refused.
    const response = await post({ decision: "approve", note: "" }, "not-a-real-jwt");
    expect(response.status).toBe(401);
    expect(writes).toHaveLength(0);
  });

  it("returns 400 and writes nothing for an unrecognised decision word", async () => {
    const response = await post({ decision: "needs review", note: "" }, "not-a-real-jwt");
    expect([400, 401]).toContain(response.status);
    expect(writes).toHaveLength(0);
  });

  it("rejects a GET", async () => {
    const mod = await import("@/app/api/case/[id]/decide/route");
    expect((mod as Record<string, unknown>).GET).toBeUndefined();
  });
});
```

- [ ] **Step 5: Run them to verify they fail**

Run: `cd web && npm run test`
Expected: FAIL — `Cannot find module '@/lib/decide'`.

- [ ] **Step 6: Write `web/lib/decide.ts`**

```ts
/**
 * THE ONLY WRITE IN THIS APPLICATION.
 *
 * A caseworker's decision becomes a durable row, and then — for an approve —
 * Grace re-evaluates the case. **The re-evaluation goes through the authority
 * gate**, which may refuse again: approving a household that is still missing a
 * document files nothing, because the document is still missing.
 *
 * No interrupt is resumed here, and the words that would do it appear nowhere in
 * this file. Plan 1's Task 6 proved that resuming with any truthy response
 * *approves the blocked tool* — "needs review" filed a renewal for `c-010`. The
 * deployed entrypoint has no resume path at all, and this is the request that
 * would have been tempted to add one.
 *
 * **The row is written before the invocation**, which is the opposite of
 * `grace/tools/action.py`. Both are right, because they claim different things: a
 * ledger row claims *Grace did something*, true only once a tool returned (hard
 * rule 6); this row claims *a human decided*, true the moment they clicked.
 * Losing that to an infrastructure error would discard the caseworker's work and
 * leave the case silently unresolved.
 */

import { DynamoDBClient, PutItemCommand } from "@aws-sdk/client-dynamodb";
import {
  BedrockAgentCoreClient,
  InvokeAgentRuntimeCommand,
} from "@aws-sdk/client-bedrock-agentcore";
import { readEnv } from "./env";
import type { Permit } from "./authorize";

export interface DecisionOutcome {
  recorded: true;
  caseId: string;
  decision: "approve" | "deny";
  graceOutcome: string;
  filed: boolean;
}

interface Clients {
  dynamo?: DynamoDBClient;
  runtime?: BedrockAgentCoreClient;
}

/** UTC, always. Plan 2 established that a non-UTC offset sorts a later instant
 *  *before* an earlier one when DynamoDB compares the sort key bytewise. */
function utcStamp(): string {
  return new Date().toISOString();
}

export async function recordDecision(
  permit: Permit,
  caseId: string,
  clients: Clients = {},
): Promise<DecisionOutcome> {
  const env = readEnv();
  const dynamo = clients.dynamo ?? new DynamoDBClient({ region: env.region });
  const decidedAt = utcStamp();

  // 1. The durable record that a human decided. If this throws, the error
  //    propagates and Grace is never invoked: acting on a decision we could not
  //    record would put a filing in the ledger with no record of who asked for
  //    it.
  await dynamo.send(new PutItemCommand({
    TableName: env.tableName,
    Item: {
      pk: { S: `CASE#${caseId}` },
      sk: { S: `DECISION#${decidedAt}` },
      case_id: { S: caseId },
      decided_at: { S: decidedAt },
      // The opaque Cognito `sub`. Never an email or a name — those claims are
      // logged to CloudTrail, outside every redaction Grace has.
      decided_by: { S: permit.decidedBy },
      decision: { S: permit.decision },
      note: { S: permit.note },
    },
    // Two caseworkers deciding the same case in the same millisecond is
    // vanishingly unlikely, but a lost decision is worse than a rejected one.
    ConditionExpression: "attribute_not_exists(sk)",
  }));

  // 2. A deny means "leave it escalated". There is nothing for Grace to do, and
  //    an invocation would cost real Bedrock to reach the same conclusion.
  if (permit.decision === "deny") {
    const graceOutcome = "Denied by a caseworker; Grace was not re-run.";
    await writeOutcome(dynamo, env.tableName, caseId, decidedAt, graceOutcome);
    return { recorded: true, caseId, decision: "deny", graceOutcome, filed: false };
  }

  // 3. An approve re-invokes Grace with the flag. The gate re-evaluates the case
  //    record; the flag affects only wording, never the verdict.
  //
  // `maxAttempts: 1` is a safety property, not tuning. `InvokeAgentRuntime` is
  // NOT idempotent — each attempt re-runs the whole graph against the same case,
  // so a retried invocation could file one renewal more than once. Measured
  // against a black-hole socket (accepts, never replies, so the accept count IS
  // the number of HTTP attempts): the **JS SDK default makes 3 attempts**;
  // `maxAttempts: 1` makes exactly 1. Note this differs from boto3, where
  // `max_attempts: 1` still gave 2 and only `total_max_attempts` gave 1 (Plan 2)
  // — do not carry that finding across verbatim, the knobs are not the same.
  //
  // `throwOnRequestTimeout` is the second half. Without it the SDK logs
  // "a request has exceeded the configured requestTimeout" and **hangs** rather
  // than throwing — measured. A hung request handler in an SSR route holds the
  // caseworker's browser open with no error to report. 870s mirrors the Lambda's
  // budget from Plan 2 and clears the 512s a real run has been measured at.
  const runtime = clients.runtime ?? new BedrockAgentCoreClient({
    region: env.region,
    maxAttempts: 1,
    requestHandler: { requestTimeout: 870_000, throwOnRequestTimeout: true },
  });
  let graceOutcome: string;
  let filed = false;
  try {
    const response = (await runtime.send(new InvokeAgentRuntimeCommand({
      agentRuntimeArn: env.runtimeArn,
      // 33+ characters, per the Runtime constraint.
      runtimeSessionId: `grace-decide-${caseId}-${crypto.randomUUID()}`,
      payload: new TextEncoder().encode(JSON.stringify({
        case_id: caseId,
        today: "2026-10-01",
        caseworker_approved: true,
      })),
    }))) as { response?: Uint8Array };

    const body = JSON.parse(new TextDecoder().decode(response.response ?? new Uint8Array()));
    // Hard rule 6 at this boundary: only a confirmed filing is reported. An
    // `acted` status without `filed: true` is not a filing.
    filed = body.status === "acted" && body.filed === true;
    graceOutcome = filed
      ? "Grace re-checked, the gate cleared the case, and the renewal was filed."
      : `Grace re-checked and did not file. ${body.reason ?? body.detail ?? body.status}`;
  } catch (error) {
    // The decision is already recorded, so say what happened rather than losing
    // it. Same reasoning as Plan 2's failed-escalation-write handling: the gap
    // is stated, not swallowed.
    graceOutcome = `The decision was recorded, but Grace could not be re-run: ${
      error instanceof Error ? error.message : String(error)}`;
  }

  await writeOutcome(dynamo, env.tableName, caseId, decidedAt, graceOutcome);
  return { recorded: true, caseId, decision: "approve", graceOutcome, filed };
}

/** Record what Grace did afterwards, on its own row so the decision row itself
 *  is never rewritten — an audit trail that can be edited is not one.
 *
 *  **The sort key must not be `DECISION#<ts>#outcome`.** `lib/cases.ts` collects
 *  decisions with `sk.startsWith("DECISION#")`, so an outcome row under that
 *  prefix is counted as a second decision and `alreadyDecided` goes true from
 *  Grace's own write — meaning the first human decision on a case would be
 *  refused as a duplicate of itself. Use a distinct prefix (`OUTCOME#<ts>`) and
 *  have `readCase` attach it to the decision it belongs to, or keep the prefix
 *  and make `readCase` discriminate on the presence of a `decision` attribute.
 *  Whichever you choose, add a test that a decision followed by its outcome
 *  leaves `alreadyDecided` true for exactly one human decision. */
async function writeOutcome(
  dynamo: DynamoDBClient,
  tableName: string,
  caseId: string,
  decidedAt: string,
  outcome: string,
): Promise<void> {
  await dynamo.send(new PutItemCommand({
    TableName: tableName,
    Item: {
      pk: { S: `CASE#${caseId}` },
      sk: { S: `DECISION#${decidedAt}#outcome` },
      case_id: { S: caseId },
      decided_at: { S: decidedAt },
      outcome: { S: outcome },
    },
  }));
}
```

- [ ] **Step 7: Write the route**

`web/app/api/case/[id]/decide/route.ts`:

```ts
/**
 * The write endpoint. POST only, session-gated, and it refuses before it
 * measures anything it does not need.
 *
 * There is no GET export on purpose: a decision must not be reachable by
 * following a link, which is also why the session cookie is `sameSite: "lax"`.
 */

import { NextResponse } from "next/server";
import { authorize } from "@/lib/authorize";
import { readFacts } from "@/lib/cases";
import { recordDecision } from "@/lib/decide";
import { SESSION_COOKIE, verifySession } from "@/lib/cognito";

export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await context.params;

  // Verify the session here, not in middleware. Middleware checks only that a
  // cookie exists, on the edge runtime; this is the check that matters, and
  // `__tests__/route-guard.test.ts` proves it refuses on its own.
  const cookie = request.headers
    .get("cookie")
    ?.split(";")
    .map(c => c.trim())
    .find(c => c.startsWith(`${SESSION_COOKIE}=`))
    ?.slice(SESSION_COOKIE.length + 1);
  const session = await verifySession(cookie);

  let body: { decision?: unknown; note?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    body = {};
  }

  const attempt = {
    // Not coerced and not trimmed: `authorize` compares against an allowlist of
    // exactly "approve" and "deny", and trimming here would quietly accept
    // "approve ". Anything else refuses.
    decision: body.decision as "approve" | "deny",
    note: typeof body.note === "string" ? body.note : "",
  };

  // Facts are measured only once the session is known — an unauthenticated
  // caller learns nothing about which cases exist.
  const facts = session === null ? null : await readFacts(id);
  const decision = authorize(session, facts, attempt, Date.now());

  if (!decision.permitted) {
    const status =
      decision.code === "no_session" || decision.code === "session_expired"
        ? 401
        : decision.code === "wrong_role"
          ? 403
          : decision.code === "unknown_case"
            ? 404
            : 400;
    return NextResponse.json(
      { error: decision.code, message: decision.message },
      { status },
    );
  }

  try {
    const outcome = await recordDecision(decision, id);
    return NextResponse.json(outcome, { status: 200 });
  } catch (error) {
    // The decision could not be recorded, so Grace was not re-run. Say so.
    return NextResponse.json(
      {
        error: "not_recorded",
        message: `The decision was not recorded, so nothing was changed: ${
          error instanceof Error ? error.message : String(error)}`,
      },
      { status: 503 },
    );
  }
}
```

- [ ] **Step 8: Run everything**

```bash
cd web && npm run test && npm run typecheck && npm run build
cd .. && .venv/bin/python -m pytest
```

Expected: 11 `decide.test.ts` + 4 `route-guard.test.ts` pass, the build succeeds, Python is
**628 passed**.

- [ ] **Step 9: Prove the two guards that matter are not vacuous**

**The session guard.** In `route.ts`, temporarily replace `const session = await verifySession(cookie)`
with a hardcoded session object. The `returns 401 and writes NOTHING` test **must fail**. Restore it.

**The gate is not bypassed.** In `grace/entrypoint.py`, temporarily make the flag force a clean
verdict — e.g. `if caseworker_approved: gate = None` before the classification. Then
`test_approving_a_case_missing_a_document_still_does_not_file` **must fail**, because `c-010` would
file. Restore it, and re-run to confirm green.

Report both failures. These two are the plan's safety argument; a guard nobody has watched fail is
not a guard.

- [ ] **Step 10: Commit**

```bash
git add web/lib/decide.ts web/app/api/case web/__tests__/decide.test.ts \
        web/__tests__/route-guard.test.ts grace/entrypoint.py tests/test_entrypoint_approval.py
git commit -m "feat: the caseworker decision path, which never bypasses the gate"
```

---
## Task 6: The pages a caseworker actually reads

Server components rendering what the previous tasks measure. The escalation queue is the point: three
households, each with a typed reason and a deadline.

**Files:**
- Create: `web/components/ui/{button,card,badge,table}.tsx` (via the shadcn CLI), `web/lib/utils.ts`, `web/components/case-table.tsx`, `web/components/decision-form.tsx`, `web/app/queue/page.tsx`, `web/app/case/[id]/page.tsx`
- Modify: `web/app/page.tsx` (replace the Task 1 placeholder)
- Test: `web/__tests__/render.test.ts`

**Interfaces:**
- Consumes: `web/lib/cases.ts` (`listCases`, `listQueue`, `readCase`), `web/lib/types.ts`
- Produces: the four routes. No new exported functions other than the components.

- [ ] **Step 1: Add the shadcn primitives**

```bash
cd web
npx shadcn@4.20.1 add button card badge table --yes
```

This writes `components/ui/*.tsx` and `lib/utils.ts` (the `cn` helper). If the CLI asks to overwrite
`app/globals.css`, **decline** — Task 1's `@theme` block is Grace's palette and the CLI's default
would replace it. If the CLI cannot run non-interactively, write the four primitives by hand from the
shadcn docs; they are small, and `cn` is `twMerge(clsx(...))`.

- [ ] **Step 2: Write the failing render test**

`web/__tests__/render.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { formatCaseRow, statusTone, noteIsInert } from "@/components/case-table";
import type { CaseSummary } from "@/lib/types";

// Every fixture surname, so the guard cannot pass by naming the wrong three.
// The plan's draft listed Mensah/Rivera/Okonkwo — and the two households most
// likely to carry a name in a reason are c-010 and c-011, Fitzgerald and
// Yamamoto, neither of which was in it. Keep this in sync with
// fixtures/households.yaml; Task 8 re-derives it from the file.
const FIXTURE_NAMES = [
  "Rivera", "Okonkwo", "Nguyen", "Haddad", "Delacroix", "Torres",
  "Abebe", "Silva", "Kowalski", "Fitzgerald", "Yamamoto", "Mensah",
];
const IDENTITY = new RegExp(`${FIXTURE_NAMES.join("|")}|\\+1555|Household`, "i");

const escalated: CaseSummary = {
  caseId: "c-011", status: "escalated", program: "medicaid",
  deadline: "2026-10-22", reason: "material_income_change: Income moved 30.0%", filed: false,
};

describe("the case row", () => {
  it("shows the case id and never a household name", () => {
    // Hard rule 9. The row is the one place a name would look natural to add.
    const row = formatCaseRow(escalated);
    expect(row.title).toBe("c-011");
    expect(JSON.stringify(row)).not.toMatch(IDENTITY);
  });

  it("passes a name through only if one is in the input, and there is nowhere for one to be", () => {
    // Proves the guard above can fail rather than being true of every input:
    // feeding a name in via `reason` — the exact path that put one into
    // CloudWatch — must be caught. `CaseSummary` has no name field, so `reason`
    // is the only carrier, which is why this is the shape the test uses.
    const leaked: CaseSummary = { ...escalated, reason: "source_conflict: The Yamamoto Household disagrees" };
    expect(JSON.stringify(formatCaseRow(leaked))).toMatch(IDENTITY);
  });

  it("surfaces the gate's typed reason, not a generic label", () => {
    // "Needs review" tells a caseworker nothing. The reason code is the whole
    // value of the escalation.
    expect(formatCaseRow(escalated).detail).toContain("material_income_change");
  });

  it("reads an absent reason as handled rather than as an empty escalation", () => {
    const acted: CaseSummary = { ...escalated, status: "acted", reason: null, filed: true };
    expect(formatCaseRow(acted).detail).toMatch(/filed/i);
  });

  it("does not claim a filing the ledger did not confirm", () => {
    // hard rule 6 at the render boundary: `acted` with `filed: false` is the
    // "clean case, no renewal" outcome, and must not read as success.
    const odd: CaseSummary = { ...escalated, status: "acted", reason: null, filed: false };
    expect(formatCaseRow(odd).detail).not.toMatch(/\bfiled\b/i);
  });

  it("gives escalation its own tone, so the eye finds it", () => {
    expect(statusTone("escalated")).not.toBe(statusTone("acted"));
    expect(statusTone("error")).not.toBe(statusTone("acted"));
  });
});

describe("the caseworker's note", () => {
  it("renders markup as text rather than as an element", async () => {
    // The note is free text a human typed and DynamoDB stored verbatim. React
    // escapes text children itself, so the assertion is about the rendered
    // output, not about a helper: verified that renderToStaticMarkup turns
    // `<img src=x onerror="alert(1)">` into `&lt;img ...` with no live tag.
    const { renderToStaticMarkup } = await import("react-dom/server");
    const { createElement } = await import("react");
    const html = renderToStaticMarkup(
      createElement("p", null, '<img src=x onerror="alert(1)">'));
    expect(html).not.toMatch(/<img\s/);
    expect(html).toContain("&lt;img");
  });

  it("does not double-escape ordinary prose", () => {
    // The apostrophe is the whole point of this test. An escape helper applied
    // before JSX turns `the family's record` into `the family&#39;s record` on
    // screen, and the plan's original fixture had no apostrophe in it, so it
    // passed against the buggy version. `noteIsInert` is a check, not a
    // transform — nothing rewrites the caseworker's words.
    const note = "The family's wage record is stale; they re-filed.";
    expect(noteIsInert(note)).toBe(true);
    expect(noteIsInert('<img src=x onerror="alert(1)">')).toBe(false);
  });
});
```

- [ ] **Step 3: Write the shared row helpers**

`web/components/case-table.tsx` — the pure helpers live here alongside the component so the test can
import them without rendering React:

```tsx
import type { CaseStatus, CaseSummary } from "@/lib/types";

export interface CaseRow {
  title: string;
  detail: string;
  deadline: string;
  status: CaseStatus;
}

/** What one row says. `case_id` and the gate's typed reason — never a household
 *  name, which is the thing that would look natural to add here and is the exact
 *  path that put a name into CloudWatch (hard rule 9). */
export function formatCaseRow(c: CaseSummary): CaseRow {
  const detail =
    c.reason ??
    (c.filed
      ? "Handled autonomously — renewal filed."
      : "Handled without a renewal on the ledger.");
  return { title: c.caseId, detail, deadline: c.deadline, status: c.status };
}

/** Escalation gets the one saturated colour in the palette. A caseworker scans
 *  this list for work, and the work is the escalations. */
export function statusTone(status: CaseStatus): string {
  switch (status) {
    case "escalated":
      return "text-[var(--color-escalate)]";
    case "error":
      return "text-[var(--color-error)]";
    default:
      return "text-[var(--color-acted)]";
  }
}

/** A caseworker's note is untrusted free text, and this asserts what protects it
 *  rather than adding a second layer. React escapes text children itself —
 *  verified: `<img src=x onerror="alert(1)">` renders as `&lt;img ...` with no
 *  live tag through `renderToStaticMarkup`. Escaping again before handing the
 *  string to JSX double-escapes it: a note reading `the family's record` would
 *  display as `the family&#39;s record` to the caseworker.
 *
 *  So this function exists to be *checked*, not applied. It answers "would this
 *  note be safe if someone reached for `dangerouslySetInnerHTML`?", which is the
 *  only way markup could reach the page. Callers render `note` directly. */
export function noteIsInert(note: string): boolean {
  return !/[<>]/.test(note);
}

export function CaseTable({ cases }: { cases: CaseSummary[] }) {
  const rows = cases.map(formatCaseRow);
  return (
    <table className="w-full text-left text-sm">
      <thead className="border-b border-[var(--color-rule)] text-[var(--color-muted)]">
        <tr>
          <th className="py-2 font-medium">Case</th>
          <th className="py-2 font-medium">What Grace concluded</th>
          <th className="py-2 font-medium">Deadline</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(row => (
          <tr key={row.title} className="border-b border-[var(--color-rule)]">
            <td className="py-3 font-mono">
              <a className="underline" href={`/case/${row.title}`}>{row.title}</a>
            </td>
            <td className={`py-3 ${statusTone(row.status)}`}>{row.detail}</td>
            <td className="py-3 tabular-nums text-[var(--color-muted)]">{row.deadline || "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 4: Write the three pages**

`web/app/page.tsx` — the sweep, which is the headline claim rendered:

```tsx
import { listCases } from "@/lib/cases";
import { CaseTable } from "@/components/case-table";

export const dynamic = "force-dynamic";   // always read the live table

export default async function Home() {
  const cases = await listCases();
  const escalated = cases.filter(c => c.status === "escalated").length;
  const acted = cases.length - escalated;
  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Today&apos;s sweep</h1>
        <p className="mt-1 text-[var(--color-muted)]">
          <strong className="text-[var(--color-acted)]">{acted} handled alone</strong>
          {" · "}
          <strong className="text-[var(--color-escalate)]">{escalated} need a human</strong>
        </p>
      </div>
      <CaseTable cases={cases} />
    </section>
  );
}
```

`web/app/queue/page.tsx`:

```tsx
import { listQueue } from "@/lib/cases";
import { CaseTable } from "@/components/case-table";

export const dynamic = "force-dynamic";

export default async function Queue() {
  const queue = await listQueue();
  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Waiting on a caseworker</h1>
        <p className="mt-1 text-[var(--color-muted)]">
          {queue.length === 0
            ? "Nothing is waiting. Grace handled every case in the last sweep."
            : "Soonest deadline first."}
        </p>
      </div>
      <CaseTable cases={queue} />
    </section>
  );
}
```

`web/app/case/[id]/page.tsx`:

```tsx
import { notFound } from "next/navigation";
import { readCase } from "@/lib/cases";
import { statusTone } from "@/components/case-table";
import { DecisionForm } from "@/components/decision-form";

export const dynamic = "force-dynamic";

export default async function Case({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const detail = await readCase(id);
  if (detail === null) notFound();

  const { summary, ledger, decisions } = detail;
  return (
    <section className="space-y-8">
      <div>
        <h1 className="font-mono text-xl font-semibold">{summary.caseId}</h1>
        <p className={`mt-1 ${statusTone(summary.status)}`}>
          {summary.reason ?? (summary.filed ? "Renewal filed." : "No renewal on the ledger.")}
        </p>
        <p className="mt-1 text-sm text-[var(--color-muted)]">
          {summary.program} · certification ends {summary.deadline || "—"}
        </p>
      </div>

      {summary.status === "escalated" && decisions.length === 0 && (
        <DecisionForm caseId={summary.caseId} />
      )}

      {decisions.length > 0 && (
        <div>
          <h2 className="mb-2 font-medium">Caseworker decisions</h2>
          <ul className="space-y-3 text-sm">
            {decisions.map(d => (
              <li key={d.decidedAt} className="border-l-2 border-[var(--color-rule)] pl-3">
                <p>
                  <strong>{d.decision}</strong>
                  <span className="ml-2 text-[var(--color-muted)]">
                    {d.decidedAt} · by {d.decidedBy}
                  </span>
                </p>
                {/* Rendered directly: React escapes text children, and escaping again
                    would show `&#39;` to the caseworker. See `noteIsInert`. */}
                {d.note && <p className="mt-1">{d.note}</p>}
                {d.outcome && (
                  <p className="mt-1 text-[var(--color-muted)]">{d.outcome}</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <h2 className="mb-2 font-medium">Audit trail</h2>
        <p className="mb-3 text-sm text-[var(--color-muted)]">
          Every tool Grace called on this case, in order. This is the record, not a
          summary of it.
        </p>
        <ul className="space-y-1 font-mono text-xs">
          {ledger.map((row, i) => (
            <li key={`${row.at}-${i}`} className="flex gap-3">
              <span className="text-[var(--color-muted)]">{row.at}</span>
              <span>{row.kind}</span>
              <span className="text-[var(--color-muted)]">
                {Object.entries(row.detail)
                  .filter(([k]) => k !== "trace_id")
                  .map(([k, v]) => `${k}=${String(v)}`)
                  .join(" ")}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
```

`web/components/decision-form.tsx` — a client component, the only one:

```tsx
"use client";

import { useState } from "react";

export function DecisionForm({ caseId }: { caseId: string }) {
  const [note, setNote] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function decide(decision: "approve" | "deny") {
    setBusy(true);
    setResult(null);
    const response = await fetch(`/api/case/${caseId}/decide`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, note }),
    });
    const body = await response.json();
    // Report exactly what happened, including a refusal. "Approved" when Grace
    // then refused to file would be the dashboard telling a comfortable lie.
    setResult(response.ok ? body.graceOutcome : `${body.error}: ${body.message}`);
    setBusy(false);
  }

  return (
    <div className="rounded border border-[var(--color-rule)] p-4">
      <h2 className="font-medium">Your decision</h2>
      <p className="mt-1 text-sm text-[var(--color-muted)]">
        Approving asks Grace to re-check the case. It files only if the gate clears
        it — if the household is still missing a document, nothing is filed.
      </p>
      <textarea
        className="mt-3 w-full rounded border border-[var(--color-rule)] p-2 text-sm"
        rows={3}
        maxLength={2000}
        placeholder="What did you check?"
        value={note}
        onChange={e => setNote(e.target.value)}
      />
      <div className="mt-3 flex gap-2">
        <button
          className="rounded bg-[var(--color-acted)] px-3 py-1.5 text-sm text-white disabled:opacity-50"
          disabled={busy}
          onClick={() => decide("approve")}
        >
          Approve
        </button>
        <button
          className="rounded border border-[var(--color-rule)] px-3 py-1.5 text-sm disabled:opacity-50"
          disabled={busy}
          onClick={() => decide("deny")}
        >
          Keep escalated
        </button>
      </div>
      {result && <p className="mt-3 text-sm">{result}</p>}
    </div>
  );
}
```

- [ ] **Step 5: Run the tests and the build**

```bash
cd web && npm run test && npm run typecheck && npm run lint && npm run build
```

Expected: 7 `render.test.ts` assertions pass and the build succeeds.

- [ ] **Step 6: Look at it against the real table**

```bash
cd web
GRACE_TABLE_NAME=grace-cases GRACE_ESCALATION_INDEX=escalation-queue \
GRACE_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-east-1:339712964409:runtime/grace_grace-oTyyvo8stE \
COGNITO_ISSUER=... COGNITO_CLIENT_ID=... COGNITO_DOMAIN=... \
AWS_REGION=us-east-1 npm run dev
```

Sign in with the seeded caseworker, then check three things by eye: `/` shows **9 handled alone, 3
need a human**; `/queue` lists exactly **three** cases with typed reasons and deadlines; and
`/case/c-011` shows a ledger with rows in order. **A queue longer than three means the
de-duplication regressed** — the GSI holds a row per sweep.

Record what you saw in `docs/dashboard-runbook.md` under a `## Running locally` section, including the
environment variables.

- [ ] **Step 7: Confirm Python is untouched**

Run: `.venv/bin/python -m pytest`
Expected: **628 passed**.

- [ ] **Step 8: Commit**

```bash
git add web/ docs/dashboard-runbook.md
git commit -m "feat: the sweep, the queue, and one case's audit trail"
```

---

## Task 7: Deploy to Amplify

The last task, and the only one whose failure is an AWS error rather than a failing test. Everything
before it works locally, so if Amplify fights, the demo still exists.

**Files:**
- Create: `infra/provision_amplify.py`, `web/amplify.yml`
- Modify: `docs/dashboard-runbook.md` (append the deploy sequence)
- Test: `tests/test_infra_amplify.py`

**Interfaces:**
- Consumes: `infra.naming`, `infra.provision_cognito.provision`
- Produces: `infra.provision_amplify.build_spec() -> str`, `infra.provision_amplify.provision(...) -> dict`

- [ ] **Step 1: Write the failing test**

`tests/test_infra_amplify.py`:

```python
"""The Amplify app's shape, asserted offline.

The platform value is the assertion that matters: `WEB` is a static host with no
route handlers and no middleware, so deploying onto it would silently remove the
Cognito gate and the decide endpoint — the app would build, serve, and be wrong.
"""

from __future__ import annotations

import yaml

from infra import provision_amplify


def test_the_platform_is_the_ssr_one():
    """WEB_COMPUTE, verified against the live enum
    ['WEB', 'WEB_DYNAMIC', 'WEB_COMPUTE']. WEB is static."""
    assert provision_amplify.PLATFORM == "WEB_COMPUTE"


def test_the_app_is_named_for_grace():
    assert provision_amplify.APP_NAME.startswith("grace")


def test_the_build_spec_is_valid_yaml_and_builds_web():
    spec = yaml.safe_load(provision_amplify.build_spec())
    frontend = spec["frontend"]
    assert "cd web" in " ".join(frontend["phases"]["preBuild"]["commands"])
    assert "npm run build" in " ".join(frontend["phases"]["build"]["commands"])
    # `.next` is the SSR output. A `baseDirectory` of `out` would mean a static
    # export, which is the same mistake as the wrong platform.
    assert frontend["artifacts"]["baseDirectory"] == "web/.next"


def test_the_build_spec_runs_the_tests_before_building():
    """A deploy that skips the tests can ship a broken auth gate. The gate's
    tests are the reason this is not merely tidy."""
    commands = " ".join(provision_amplify.build_spec_commands())
    assert "npm run test" in commands
    assert "npm run typecheck" in commands


def test_no_secret_is_in_the_environment_variables():
    """Amplify env vars are visible in the console and in build logs. Resource
    names are fine; a credential is not — the app role supplies those."""
    env = provision_amplify.ENVIRONMENT_VARIABLES
    joined = " ".join(f"{k}={v}" for k, v in env.items()).lower()
    for forbidden in ["secret", "password", "aws_access_key", "private"]:
        assert forbidden not in joined, forbidden


def test_no_public_variable_carries_backend_detail():
    """A NEXT_PUBLIC_ variable is compiled into the client bundle. The table
    name and runtime ARN are a map of the backend and nothing in the browser
    needs them."""
    for name in provision_amplify.ENVIRONMENT_VARIABLES:
        assert not name.startswith("NEXT_PUBLIC_"), name
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_infra_amplify.py -v`
Expected: FAIL — `ImportError: cannot import name 'provision_amplify' from 'infra'`.

- [ ] **Step 3: Write `infra/provision_amplify.py`**

**This draft is missing the SSR compute role, and without it the whole task fails silently in the
worst way.** Verified against the live API and the Amplify documentation on 2026-09-04:

- `CreateApp`'s `platform` enum really is `['WEB', 'WEB_DYNAMIC', 'WEB_COMPUTE']`, so the platform
  assertion is sound.
- **But `computeRoleArn` is a real parameter on both `CreateApp` and `CreateBranch`, and this draft
  sets neither.** Amplify's SSR compute functions get their AWS credentials from that role and from
  nothing else. With no role attached there are no credentials in the SSR runtime, so `lib/cases.ts`'s
  every DynamoDB call and `lib/decide.ts`'s `invoke_agent_runtime` fail with AccessDenied — **after a
  green build and a successful deploy.** The app would serve, the pages would render their empty and
  error states, and nothing in the build log would say why. That is the same class of failure as the
  wrong platform value: wrong rather than broken.
- The role needs a **custom trust policy naming `amplify.amazonaws.com`** as the service principal, and
  this was **probed both ways** on 2026-09-04 rather than taken from the docs. A role trusting
  `lambda.amazonaws.com` is refused at `CreateApp` with
  `BadRequestException: The compute role provided cannot be assumed by Amplify.`; the identical call
  with `amplify.amazonaws.com` is **accepted and echoes `computeRoleArn` back** on the app. Both halves
  mattered: a refusal alone would not prove the correct principal works, and an acceptance alone would
  not prove the parameter is validated rather than ignored. Practical consequence: a wrong role fails
  during provisioning, not after a green deploy — so `provision_amplify` must not swallow a
  `BadRequestException` here (Plan 2's "a provisioning script that swallows a not-ready error reports
  success while the control is absent").
- Grant least privilege, scoped to what the dashboard actually does: `dynamodb:Query` and
  `dynamodb:GetItem` on the `grace-cases` table **and its `escalation-queue` index** (an index needs
  its own ARN — `table/grace-cases/index/escalation-queue` — or the GSI query is denied while the table
  query succeeds), `dynamodb:PutItem` on the table for the decision row, and
  `bedrock-agentcore:InvokeAgentRuntime` on the Grace runtime ARN. **No `Scan`**: `lib/cases.ts`
  queries, and granting `Scan` would let a bug read every ledger row in the table.
- Attach it **app-level** here. The docs recommend branch-level instead when a public repo uses
  auto-branch creation or PR previews — this app has one branch and neither feature, so app-level is
  the simpler correct choice. Keep `enableAutoBranchCreation` and PR previews off, which is also why
  that recommendation does not bite.

So Task 7 grows an `infra/provision_amplify_role.py` (or a function in this module) that creates the
role idempotently, and `provision` passes `computeRoleArn=` to `create_app`/`update_app`. Add a test
asserting the trust policy's principal is `amplify.amazonaws.com` and that the policy grants no
`dynamodb:Scan` and no `dynamodb:DeleteItem` — the second because nothing in this app deletes, and a
dashboard that can delete a ledger row can destroy the audit trail the whole project rests on.

```python
"""The Amplify app that hosts the dashboard. Idempotent.

**`WEB_COMPUTE`, not `WEB`.** The live enum is
`['WEB', 'WEB_DYNAMIC', 'WEB_COMPUTE']`, and `WEB` is a static host: no route
handlers, no middleware. Deploying onto it would remove the Cognito gate and the
decide endpoint while still building and serving successfully — an app that is
wrong rather than broken, which is the worse failure.

Repo-connected deploys need a GitHub app authorization performed in a browser,
which cannot be done unattended. `CreateDeployment` / `StartDeployment` accept a
zip instead, and that is the documented fallback in the runbook.
"""

from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

from infra import naming

APP_NAME = "grace-dashboard"
BRANCH = "main"
PLATFORM = "WEB_COMPUTE"

# Resource names only. Amplify environment variables are visible in the console
# and in build logs, so a credential here would be a credential in a log. The
# app's service role supplies AWS access; these just say which resources.
ENVIRONMENT_VARIABLES: dict[str, str] = {
    "AWS_REGION": naming.REGION,
    "GRACE_TABLE_NAME": naming.TABLE,
    "GRACE_ESCALATION_INDEX": naming.ESCALATION_GSI,
    # Filled in by `provision` from the deployed runtime and the Cognito pool.
    "GRACE_RUNTIME_ARN": "",
    "COGNITO_ISSUER": "",
    "COGNITO_CLIENT_ID": "",
    "COGNITO_DOMAIN": "",
    "DASHBOARD_URL": "",
}


def build_spec_commands() -> list[str]:
    """Every command the build runs, flattened — so a test can assert the tests
    are among them."""
    return [
        "cd web",
        "npm ci",
        "npm run typecheck",
        "npm run lint",
        "npm run test",
        "npm run build",
    ]


def build_spec() -> str:
    """Amplify's buildspec. The tests run **before** the build, deliberately: a
    deploy that skips them can ship a broken authorisation gate, and this app can
    file benefit renewals."""
    return """version: 1
frontend:
  phases:
    preBuild:
      commands:
        - cd web
        - npm ci
    build:
      commands:
        - npm run typecheck
        - npm run lint
        - npm run test
        - npm run build
  artifacts:
    baseDirectory: web/.next
    files:
      - '**/*'
  cache:
    paths:
      - web/node_modules/**/*
"""


def provision(client=None, runtime_arn: str | None = None, cognito: dict | None = None) -> dict:
    """Create or update the Amplify app and its branch. Returns app id and URL."""
    client = client or boto3.client("amplify", region_name=naming.REGION)

    if runtime_arn is None:
        control = boto3.client("bedrock-agentcore-control", region_name=naming.REGION)
        matches = []
        # Paginated: this account holds 16 runtimes and Grace is not on page 1.
        for page in control.get_paginator("list_agent_runtimes").paginate():
            for runtime in page.get("agentRuntimes", []):
                if str(runtime["agentRuntimeName"]).split("_")[0] == naming.RUNTIME:
                    matches.append(runtime)
        ready = [r for r in matches if r.get("status") == "READY"]
        if not ready:
            raise RuntimeError("no READY Grace runtime found; deploy it first")
        runtime_arn = str(ready[0]["agentRuntimeArn"])

    if cognito is None:
        from infra import provision_cognito

        cognito = provision_cognito.provision()

    app_id: str | None = None
    for page in client.get_paginator("list_apps").paginate():
        for app in page.get("apps", []):
            if app["name"] == APP_NAME:
                app_id = app["appId"]
                break

    env = {
        **ENVIRONMENT_VARIABLES,
        "GRACE_RUNTIME_ARN": runtime_arn,
        "COGNITO_ISSUER": cognito["issuer"],
        "COGNITO_CLIENT_ID": cognito["client_id"],
        "COGNITO_DOMAIN": cognito["domain"],
    }

    if app_id is None:
        created = client.create_app(
            name=APP_NAME,
            platform=PLATFORM,
            buildSpec=build_spec(),
            environmentVariables=env,
            tags=naming.TAGS,
        )
        app_id = created["app"]["appId"]
    else:
        client.update_app(
            appId=app_id, platform=PLATFORM, buildSpec=build_spec(),
            environmentVariables=env,
        )

    try:
        client.create_branch(appId=app_id, branchName=BRANCH, stage="PRODUCTION")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "BadRequestException":
            raise

    url = f"https://{BRANCH}.{app_id}.amplifyapp.com"
    # The callback URL is only knowable once the app exists, so Cognito is
    # updated afterwards. Without this the hosted UI refuses the redirect.
    from infra import provision_cognito

    provision_cognito.provision(callback_urls=[
        f"{url}/api/auth/callback",
        "http://localhost:3000/api/auth/callback",
    ])
    client.update_app(appId=app_id, environmentVariables={**env, "DASHBOARD_URL": url})

    return {"app_id": app_id, "url": url, "branch": BRANCH}


if __name__ == "__main__":
    for key, value in provision().items():
        print(f"{key}: {value}")
```

- [ ] **Step 4: Write `web/amplify.yml` and run the Python tests**

Write `build_spec()`'s output to `web/amplify.yml` so the spec is reviewable in the repo as well as
set on the app. Then:

```bash
.venv/bin/python -m pytest tests/test_infra_amplify.py -v
.venv/bin/python -m pytest
```

Expected: 6 new tests pass; the suite is **634 passed**. Report the real number.

- [ ] **Step 5: Provision and deploy**

```bash
export AWS_PAGER=""
.venv/bin/python -m infra.provision_amplify
.venv/bin/python -m infra.provision_amplify   # idempotence: same app id
```

Then deploy. Try the repo-connected path first, and fall back to zip if it needs a browser:

```bash
APP=$(aws amplify list-apps --region us-east-1 \
  --query "apps[?name=='grace-dashboard'].appId | [0]" --output text)
aws amplify start-job --app-id "$APP" --branch-name main --job-type RELEASE \
  --region us-east-1 2>&1 | head -20
```

If that reports no connected repository, use the zip path:

```bash
cd /Users/sorour/sorour/AgentsforHumansHackathon
zip -rq /tmp/grace-dashboard.zip web -x "web/node_modules/*" "web/.next/*"
aws amplify create-deployment --app-id "$APP" --branch-name main --region us-east-1
# upload to the returned zipUploadUrl with curl --upload-file, then:
aws amplify start-deployment --app-id "$APP" --branch-name main \
  --job-id <returned> --region us-east-1
```

Then watch it:

```bash
aws amplify list-jobs --app-id "$APP" --branch-name main --region us-east-1 \
  --query 'jobSummaries[0].{status:status,reason:jobId}'
```

Expected: `SUCCEED`. **If the build fails, read the build log before changing anything** — the most
likely causes are a missing environment variable (the build runs `npm run test`, which needs none, but
the runtime needs all of them) and the platform being wrong.

- [ ] **Step 6: Prove the deployed auth gate actually holds**

The single most important check in this task:

```bash
URL="https://main.${APP}.amplifyapp.com"
echo "=== an unauthenticated page must redirect, not render ==="
curl -s -o /dev/null -w "%{http_code} -> %{redirect_url}\n" "$URL/queue"
echo "=== an unauthenticated POST must be refused, and write nothing ==="
curl -s -X POST "$URL/api/case/c-010/decide" \
  -H 'Content-Type: application/json' -d '{"decision":"approve","note":"probe"}' \
  -w "\nHTTP %{http_code}\n"
```

Expected: the page returns **307/302 to `/login`**, and the POST returns **401**. Then confirm the
probe wrote nothing:

```bash
aws dynamodb query --table-name grace-cases --region us-east-1 \
  --key-condition-expression 'pk = :p AND begins_with(sk, :s)' \
  --expression-attribute-values '{":p":{"S":"CASE#c-010"},":s":{"S":"DECISION#"}}' \
  --query 'Items[].note.S'
```

Expected: no item containing `probe`. **If a decision row appeared, stop and do not proceed** — an
unauthenticated caller reached the write path, which is the one outcome this whole design exists to
prevent.

- [ ] **Step 7: Record the deploy and commit**

Append to `docs/dashboard-runbook.md`: the app id, the URL, which deploy path worked, the exact
environment variables set, and the two curl probes with their observed responses.

```bash
git add infra/provision_amplify.py tests/test_infra_amplify.py web/amplify.yml \
        docs/dashboard-runbook.md
git commit -m "feat: host the dashboard on Amplify SSR with a proven auth gate"
```

---

## Task 8: Verify, and tell the truth about it

Nothing new is built. What exists is proven end to end and described accurately — including the parts
that do not work.

**Files:**
- Create: `docs/dashboard-verification.md`
- Modify: `README.md`, `CLAUDE.md`, `docs/architecture.md`

- [ ] **Step 1: Confirm the decision path is still untouched**

```bash
git diff --stat 0e9de29 -- grace/authority.py grace/steering.py grace/graph.py grace/swarm.py
```

Expected: **empty**. Three plans, one deployed system, and the four files that decide whether a family
keeps their coverage are byte-identical to when Plan 1 finished. If they differ, stop and explain
before writing a word of documentation.

- [ ] **Step 2: The end-to-end approval, on the real deployed system**

Sign in as the caseworker and approve **`c-010`** — the household missing `proof_of_residency`.

```bash
aws dynamodb query --table-name grace-cases --region us-east-1 \
  --key-condition-expression 'pk = :p' \
  --expression-attribute-values '{":p":{"S":"CASE#c-010"}}' \
  --query 'Items[?starts_with(sk.S, `DECISION#`)].[sk.S,decision.S,outcome.S]' --output table

aws dynamodb query --table-name grace-cases --region us-east-1 \
  --key-condition-expression 'pk = :p' \
  --expression-attribute-values '{":p":{"S":"CASE#c-010"}}' \
  --query 'length(Items[?kind.S==`renewal_submitted`])'
```

Expected: a `DECISION#` row recording the approval, an outcome saying Grace re-checked and did not
file, and **`0` renewal_submitted rows**. That is the whole safety argument, executed: a human
approved, Grace re-checked, the gate still refused, and the document is still missing.

Then approve **`c-011`** (material income change) and record whatever happens — including if Grace
files. That is a legitimate outcome: the gate escalated on an ambiguity a human resolved, and the
reason is recorded.

- [ ] **Step 3: Confirm no PII reached the new surfaces**

```bash
.venv/bin/python - <<'PY'
import boto3, json
d = boto3.client("dynamodb", region_name="us-east-1")
rows, tok = [], None
while True:
    kw = {"TableName": "grace-cases",
          "KeyConditionExpression": "pk = :p",
          "ExpressionAttributeValues": {":p": {"S": "CASE#c-010"}}}
    if tok: kw["ExclusiveStartKey"] = tok
    r = d.query(**kw); rows += r["Items"]; tok = r.get("LastEvaluatedKey")
    if not tok: break
blob = json.dumps(rows)
names = ["Abebe","Delacroix","Fitzgerald","Haddad","Kowalski","Mensah",
         "Nguyen","Okonkwo","Rivera","Silva","Torres","Yamamoto"]
print("PII in c-010's rows:", [n for n in names if n in blob] or "NONE")
print("emails in decision rows:", "@" in blob)
PY
```

Expected: `NONE` and `False`. The decision rows carry the opaque Cognito `sub`, never an email.

- [ ] **Step 4: Write `docs/dashboard-verification.md`**

Paste real output, not paraphrase: the empty decision-path diff, the two curl probes with their status
codes, the `c-010` approval showing zero `renewal_submitted` rows, the `c-011` approval, the PII scan,
and the final test count.

- [ ] **Step 5: Update the README honestly**

- **Four AgentCore surfaces now, not three and not five** — Runtime, Memory, Identity, harness.
  Gateway stays deferred with its reason. Update the count only because Cognito actually shipped.
- The dashboard's live URL, and that it is gated on Cognito with an admin-created account.
- **The claim that matters:** a caseworker can approve a case and Grace will re-check it, and
  approving `c-010` still files nothing because the document is still missing. Say it with the
  evidence.
- Carry forward the honest caveats: `trace_id` is `NULL` in the deployed runtime, pre-fix log events
  still contain one household name until retention expires, and SMS is sandboxed.

- [ ] **Step 6: Update `CLAUDE.md`**

Move the surface count from three to four **now that Identity has shipped** — the one place the
earlier instruction said not to change in advance. Add a "What Plan 3 established" section covering at
minimum: middleware checks presence while the route verifies the token; a caseworker's approval is an
input to the gate and never a bypass; the decision row is written before the invocation and why that
inverts `action.py`; the queue must be de-duplicated by case because each sweep appends a row; and
`WEB_COMPUTE` versus `WEB`.

- [ ] **Step 7: Write the architecture diagram**

`docs/architecture.md` with a Mermaid diagram covering the whole system — EventBridge through Step
Functions, Lambda, Runtime, the swarm, DynamoDB, and the dashboard with Cognito. Mermaid renders on
GitHub, so the repo satisfies the "architecture diagram" requirement without a binary asset; export a
PNG as well if the submission form needs an image.

- [ ] **Step 8: Run everything one last time**

```bash
.venv/bin/python -m pytest              # expect 634
cd web && npm run test && npm run build
```

- [ ] **Step 9: Tick every checkbox in this plan, then commit**

```bash
git add -A
git commit -m "docs: Plan 3 complete — dashboard verified, four surfaces, honest README"
git push origin main
```

---

## Self-Review

**Spec coverage.** Every section of `2026-09-03-grace-dashboard-design.md` maps to a task:

| Spec section | Task |
|---|---|
| §1.1 decisions | Bounded by Global Constraints |
| §1.2 never resume | Task 5 (`decide.ts`, the entrypoint flag, and the two sabotage proofs) |
| §2 architecture | Tasks 1 (SSR config), 4 (middleware), 7 (`WEB_COMPUTE`) |
| §2.1 pages | Task 6 |
| §2.2 new files | Tasks 1–7, file-for-file |
| §3 verified ground | Task 0 |
| §3.1 Cognito & the honest count | Task 4 provisions it; Task 8 Step 6 moves the count |
| §4 data model | Task 5 (`DECISION#` row, written before the invocation) |
| §5 testing, all three layers | Task 2 (pure, exhaustive), Task 5 (write path gated), Task 8 (end to end) |
| §6 risks | Task 0 (platform + zip fallback), Task 7 Step 6 (the gate), Task 5 (free text) |
| §7 disclosure | Task 8 Step 5 |
| §8 out of scope | Task 8 Step 5 |

**Placeholder scan.** No "TBD", no "add error handling", no "similar to Task N". Every code step
carries real code. Task 7's zip-deploy path leaves the upload URL to be substituted at runtime, which
is a value that does not exist until the API returns it — not a placeholder.

**Type consistency.** Checked across tasks: `CaseSummary` / `LedgerRow` / `Decision` / `CaseDetail` /
`SessionIdentity` (T1 → T3, T6), `CaseFacts` / `Permit` / `Refusal` / `authorize` (T2 → T3, T5),
`readEnv` / `Env` (T3 → T5), `listCases` / `listQueue` / `readCase` / `readFacts` (T3 → T5, T6),
`verifySession` / `SESSION_COOKIE` / `hostedUiUrl` / `exchangeCode` (T4 → T5), `recordDecision` /
`DecisionOutcome` (T5 → route), `formatCaseRow` / `statusTone` / `noteIsInert` (T6 → its test).
`CASEWORKER_ROLE` is defined once in T2 and imported by T4 rather than restated.

**Test-count arithmetic.** Python: 622 → 628 (T5's six) → 634 (T7's six), plus T4's seven Cognito
tests inside that range. TypeScript: 2 (T1) → 16 (T2) → 30 (T3) → 42 (T4) → 57 (T5) → 64 (T6). Every
task says "report the real number", because every estimate in Plans 1 and 2 proved stale once written.
