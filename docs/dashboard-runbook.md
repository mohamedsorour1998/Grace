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

## Scaffolding `web/` (Task 1)

All four gates pass — `npm run typecheck`, `lint`, `test`, `build`. **`build` is the one that
matters**, because it is what Amplify runs.

### Two pinned versions had to change

| Package | Plan's pin | Installed | Why |
|---|---|---|---|
| `@aws-sdk/util-dynamodb` | 3.1125.0 | **3.996.9** | 3.1125.0 does not exist. This package never entered the `3.1xxx` series at all — its `latest` is 3.996.9 across 568 published versions. Its peer range is `@aws-sdk/client-dynamodb: ^3.1111.0`, which 3.1125.0 satisfies, so the pair is compatible. |
| `typescript` | 7.0.2 | **6.0.3** | TypeScript 7 is the native Go port, and `typescript-eslint` **refuses to load under it** — an explicit `throw new Error("typescript-eslint does not support TS 7.0.")` guarded by `if (versionMajor >= 7)` in its own `dist/index.js`. `eslint-config-next@16.3.4` depends on `typescript-eslint@^8.46.0`, so `npm run lint` could not run at all. |

**Task 0 validated the `tsconfig.json` options against `tsc@7.0.2` and that finding still holds — it
was simply the wrong compiler to pin.** `tsc --noEmit` and `next build` both pass identically under
6.0.3, so nothing was lost by downgrading; what was gained is a working linter. The upstream tracking
issue for TS >= 7.1 support is `typescript-eslint#10940`. Revisit the pin when that closes; until
then, TS 7 and `eslint-config-next` cannot both be present.

### Three defects in the plan's own config

1. **`eslint.config.mjs` spread a call, not an array.** The plan's `import next from
   "eslint-config-next"; export default [...next()]` throws `next is not a function`.
   `eslint-config-next@16.3.4`'s own `dist/index.d.ts` reads
   `declare const config: Linter.Config[]; export = config` — it exports the array directly. Fixed to
   `[...next, ...]`.
2. **The plan's two `.mjs` configs lint themselves.** `eslint-config-next` enables
   `import/no-anonymous-default-export`, which warns on both `export default [...]` in
   `eslint.config.mjs` and `export default { plugins: ... }` in `postcss.config.mjs`. Both now assign
   to a named `config` first. Lint output is clean, not merely error-free.
3. **`vitest.config.mts` used `__dirname`.** Vitest 5 / Vite warns on every run that `__dirname` is
   unsupported by `configLoader: "native"`, which is planned to become the default. Changed to
   `import.meta.dirname`.

### One file Next rewrote on its own

`next build` **edits `tsconfig.json` in place**: it forces `jsx` from the plan's `"preserve"` to
`"react-jsx"` (Next 16 uses the React automatic runtime and calls this a mandatory change) and appends
`.next/dev/types/**/*.ts` to `include`. Next's version is the one committed — reverting it just makes
the next build rewrite it again.

`*.tsbuildinfo` was added to `.gitignore`: `incremental: true` writes it on every typecheck, and it is
an absolute-path-keyed cache.

### The smoke test was watched failing

`__tests__/smoke.test.ts` asserts `next.config.ts` does not set `output`. Adding `output: "export"`
to the config makes it fail with `expected 'export' to be undefined`, confirmed and then reverted. A
static export has no route handlers and no middleware, so it would silently delete the Cognito gate
and the decide endpoint — the app would build, serve, and be wrong.

Staged file count after `git add web/`: **14**, so `node_modules/` and `.next/` are correctly ignored.
(15 before `*.tsbuildinfo` was ignored — `tsconfig.tsbuildinfo` was the fifteenth.)
Python suite still **622 passed** — `web/` is additive.

---

## Running locally

Filled in by Task 6.

## Deploying

Filled in by Task 7.
