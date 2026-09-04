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
app would build, serve, and be wrong. **`CreateDeployment` is not a build fallback**, which the
preflight originally assumed it was: a manual deployment *deploys artifacts and does not run a
buildspec* (`StartDeployment`'s own API docs say manually deployed apps are not connected to a Git
repository). A zip must already contain the Amplify Hosting deployment-specification bundle —
`.amplify-hosting/static/`, `.amplify-hosting/compute/default/` with a self-contained Node entry point
listening on **port 3000**, and a `deploy-manifest.json` carrying a catch-all route to `Compute` —
and Next.js emits none of that (measured: a real build produces `.next/`, with no `.amplify-hosting/`
and no `.next/standalone` unless `output: "standalone"` is set).

So Grace connects the repository instead. That is the supported SSR path, and the only one that runs
the buildspec's `typecheck`/`lint`/`test` before deploying. **It requires exactly one browser step** —
authorizing the AWS Amplify GitHub app — for which there is no API; `CreateApp`'s
`accessToken`/`oauthToken` are legacy personal-token fields that do not cover the GitHub App
installation. Two further traps from the AWS docs: framework detection happens on the *Add repository*
page, so a monorepo whose app root is not set to `web` silently stays `platform: WEB`; and
`baseDirectory` is `.next` for Next 14+ regardless of SSG or SSR, even though a local build may also
leave an `out/` directory that looks like the right answer.

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

## `lib/authorize.ts` (Task 2)

The dashboard's `authority.py`: a pure function from (session, case facts, attempted decision,
`nowMs`) to `Permit | Refusal`. Final state is **19 tests** in `__tests__/authorize.test.ts` (21
with Task 1's two smoke tests), all four `web/` gates green, Python unchanged at **622 passed**, and
`git diff 0e9de29 -- grace/{authority,steering,graph,swarm}.py` empty.

### The red step, and a parse error that was not one

Typed in verbatim, the plan's draft test **did not parse**:

```text
[PARSE_ERROR] Expected `}` but found `Identifier`
    ╭─[ __tests__/authorize.test.ts:79:34 ]
 79 │       expect(r.permitted, `${bad!r} must refuse`).toBe(false);
    │                                  ┬
    │                                  ╰── `}` expected
```

`${bad!r}` is Python `repr` syntax inside a JS template literal. Vitest reported `1 failed`, which
looks like a red step and is not one — **zero of the file's fourteen tests ran**. Fixed to
`JSON.stringify(bad)`, which also renders the trailing space in `"approve "` visibly. The genuine
red step, after that:

```text
Error: Cannot find package '@/lib/authorize' imported from …/__tests__/authorize.test.ts
```

Note the wording: Vitest 5 says *package*, the plan predicted *module*.

### Three tests asserted nothing on the outcome they guard

Every refusal-code check sat inside `if (!r.permitted) expect(r.code).toBe(...)`, and the whole body
of `carries the opaque sub, never a name` sat inside `if (r.permitted)`. Measured by rewriting
`authorize` to always refuse — 10 of 14 failed and **four passed**, three of them meaningfully:

```text
✓ refuses with no session at all              (correct for the wrong reason)
✓ carries the opaque sub, never a name        (body skipped entirely)
✓ is deterministic and mutates nothing        (refusals are also deterministic)
✓ imports nothing that performs I/O           (structural, unaffected)
```

`refusalOf` / `permitOf` narrow the union by **throwing** on the wrong variant, so every assertion
is now unconditional. This is Plan 1 Task 8's vacuity lesson in TypeScript form: a discriminated
union invites exactly the `if` that makes the check disappear.

### The purity guard had three holes, all measured

The draft compared the source against five literal strings. Each of these was added to
`authorize.ts` and **passed the guard**:

| Impurity added | Why the draft missed it |
|---|---|
| `import { readFileSync } from "fs";` | it forbade only `node:fs` |
| `const t = new Date().getTime();` | it forbade only `Date.now()` |
| `const f = globalThis.fetch;` | it forbade only `fetch(` |

Replaced with a positive check: enumerate the `import` statements actually present and require each
to be **type-only** (erased at compile time, so it cannot execute) and **relative** (a bare
specifier is a package, and no package here is I/O-free), plus word-boundary patterns for `Date`,
`process`, `fetch`, `require(`, dynamic `import(`, `globalThis`, `Math.random`, and `performance.`.
Re-verified: all six probes now caught, including the two the draft already covered. Same discipline
as Task 4's model-ID guard — discover what is there rather than denylisting spellings someone
remembered.

### Two reachable inputs walked past their own guard

**A non-finite `expiresAt`.** JSON cannot encode `NaN`, but it can encode an overflowing exponent,
and `JSON.parse('{"exp":1e400}').exp` is `Infinity`. Signed a token with that raw payload and
verified it with `jose` against a real generated RS256 key pair:

```text
jose ACCEPTED the token. exp = Infinity | isFinite: false
typeof exp === 'number': true   (Task 4's cognito guard passes it)
expiresAt = exp*1000 = Infinity
```

`Infinity <= nowMs` is `false`, so the expiry check would treat it as a session that never expires —
the fail-open direction on the one check that bounds a stolen cookie's lifetime. `authorize` now
refuses a non-finite `expiresAt` or `nowMs`. Task 4's draft in the plan was corrected in the same
pass, from `typeof payload.exp !== "number"` to `!Number.isFinite(payload.exp)`; both checks are
kept, because `authorize` is not reached only through `cognito.ts`.

**A non-string `note`.** Task 5's route builds `attempt` from `await request.json()` with a bare
`as` cast, so `note` is whatever was posted. `undefined.length` is `undefined` and
`undefined > 2000` is `false`, so the cap passes silently and a non-string reaches the decision row;
`null.length` throws `TypeError`, turning a refusal into a 500 out of the pure gate. Refused by type
rather than coerced — coercion would invent a note nobody wrote. The lesson generalises: a
signature's types are what a caller *promises*, and an `as` cast is where that promise stops being
checked.

### Step 5: the allowlist sabotage, and why its predicted output was wrong

Replaced `DECISIONS.has(attempt.decision)` with the denylist `attempt.decision === "escalate"`:

```text
FAIL  authorize — refusals > refuses any decision word that is not exactly approve or deny
AssertionError: "Approve" must refuse: expected true to be false // Object.is equality
❯ __tests__/authorize.test.ts:79:65
```

The guard fired, but it names **`"Approve"`**, not the `"yes"`/`"file"`/`"proceed"`/`"needs review"`
the plan's Step 5 predicted. Vitest stops at the first failed assertion, so the string reported is
just the first element of the array. Anyone checking the output against the plan's prediction would
have read a correct sabotage as a failed one. The plan is corrected, and the loop now ends with
`expect(checked).toBe(words.length)` so a loop that stopped early cannot report a pass. Allowlist
restored; green again.

### Every other new assertion was watched failing

One sabotage per added property, each failing exactly the test written for it and nothing else:

| Sabotage | Test that failed |
|---|---|
| drop the finite-expiry guard | `refuses an expiry that is not a finite number of milliseconds` |
| drop the non-string note guard | `refuses a note that is not a string` |
| `expiresAt <= nowMs` → `<` | `refuses an expired session, even one millisecond past` |
| note cap `>` → `>=` | `refuses a note longer than the cap` |
| role compare → `.trim().toLowerCase()` | `refuses a session without the caseworker role` |
| `status !== "escalated"` → `status === "acted"` | `refuses a case Grace handled itself` |
| permit carries `caseId` | `carries nothing beyond the four fields…` + `permits without filing anything` |
| check `facts` before `session` | `orders its checks so a refusal never leaks whether a case exists` |

The last one is worth keeping: session checks must precede fact checks, or an unauthenticated
caller learns which case IDs exist from the difference between `no_session` and `unknown_case`.

### Two properties recorded as tests rather than prose

`error` joins `acted` as not-decidable — a case whose sweep failed has no measured verdict to
approve. And `Object.keys(permit)` is asserted to be exactly
`["decidedBy", "decision", "note", "permitted"]`: a `Permit` is what `recordDecision` writes from, so
if a name or a whole session object rode along, hard rule 9's surface would widen without anyone
choosing to widen it. `permits without filing anything` states the "a permit is not a filing"
property against `c-010` directly, since it is the one most easily misread.

### Re-verified independently, against the shipped file

The claims above were re-measured rather than accepted. Nine sabotages applied to the committed
`authorize.ts`, each failing exactly one test: allowlist→denylist, `<=`→`<` on expiry, dropping the
finite-expiry guard, case-insensitive role compare, dropping the already-decided check, dropping the
not-escalated check, dropping the non-string-note guard, and an off-by-one note cap. A tenth — always
refuse — failed **15 of 19**, and the four survivors are legitimately independent of it: the
no-session refusal, the check-ordering test, and the two purity tests, which read the source rather
than call the function.

The purity guard was probed with six injections and caught all six, including two not tested during
implementation: a **value** import of a relative sibling (`import { X } from "./types"`), and
`process.env`. It also fires on the word `Date` in a **comment** — over-strict, in the safe direction,
and it fails loudly rather than silently.

Both files were diffed against the plan's embedded code blocks: **identical**, 12,125 bytes for the
test and byte-for-byte for the implementation. So the plan is a faithful record of what shipped, which
is what makes it re-runnable.

---

## `lib/env.ts` and `lib/cases.ts` (Task 3)

The dashboard's only DynamoDB reader. `authorize.ts` decides; this file measures the facts it decides
over. Final state is **26 new tests** (**58** with Tasks 1 and 2), all four `web/` gates green, and the
Python suite unchanged at **622 passed**.

### The red step

With `lib/env.ts` moved aside, Vitest 5 again says *package* where the plan predicted *module*:

```text
Error: Cannot find package '@/lib/env' imported from …/__tests__/cases.test.ts
 Test Files  1 failed | 2 passed (3)
      Tests  21 passed (21)
```

### Ten defects in the plan's draft

Five were already recorded in `docs/plan3-live-data-findings.md`; implementing it found five more.
Ordered by consequence, worst first.

| # | Defect | Consequence |
|---|---|---|
| 1 | `pending ? "escalated" : "acted"` | A case with **no escalation and no `renewal_submitted` row** reported as handled autonomously — a family counted among the nine while nothing was filed. Hard rule 6, inverted. Now `… : filed ? "acted" : "error"`. |
| 2 | Naive `sk.startsWith("DECISION#")` | Task 5's own `DECISION#<ts>#outcome` write counted as a decision: a phantom **deny attributed to nobody**, and `alreadyDecided` true from Grace's write — so the **first** human decision refuses itself as a duplicate. |
| 3 | `parseInt` chosen by a `.`-test | boto3 writes `Decimal` canonically, so `1e30` is `{"N": "1E+30"}` with no `.` — `parseInt("1E+30", 10)` is **1**. A number read back 1e30 too small, silently. One such row is live (`c-002`). |
| 4 | String `>` on timestamps | `Z` (0x5A) sorts above `.` (0x2E), so the **older** row wins a newest-wins dedup. Both `listQueue` and `readCase` have their own picker; both needed fixing. |
| 5 | `outcome` read off the human decision row | Task 5 never writes it there, so `Decision.outcome` was structurally always `null`. Joined on the shared `decided_at` instead. |
| 6 | Unbounded `do/while` in `queryAll` | A repeated `LastEvaluatedKey` **hangs** an SSR page rather than failing it. Capped at 100 pages and it throws; `readCase` turns that into `null`. |
| 7 | `str(row.program, "—")` | Structurally always the dash: no escalation row carries `program` (measured across all 18). A presentation dash in a data layer is a magic value. Returns `""`; Task 6 renders the dash. |
| 8 | `deadline` read only from an escalation row | Empty for **all nine acted cases**. The same fact is `d_cert_end` on the renewal row, verified equal to the fixture `cert_end`. |
| 9 | `as NodeJS.ProcessEnv` | **Does not compile.** Next 16 declares `NODE_ENV` required (`next/types/global.d.ts:23`) → `TS2352`. Reproduced with only the draft's own two lines. `readEnv` now takes a structural `EnvSource`. |
| 10 | `required()` returned the untrimmed value | `" grace-cases "` passes as present and the spaces reach the SDK, failing as a `ResourceNotFoundException` naming a table that looks right in the log. |

Two smaller ones: `listCases` merged `listQueue`'s rows, whose `filed: false` is **unmeasured** (the GSI
projects escalation rows only), mixing two reliabilities in the summary page's hard-rule-6 column — it
now reads all twelve from the ledger. And the draft's `readEnv` tests only exercised
`GRACE_TABLE_NAME`: deleting the `GRACE_RUNTIME_ARN` check left every one of them passing.

### 24 sabotages, all caught — but only after one survived

A throwaway harness applies one mutation at a time, runs vitest, and restores; it is deleted rather
than committed, since the record of which test caught what is the durable artifact. On the first pass
**23 of 24** failed the test written for them and one **survived**: replacing `instant()` with a string
comparison.

The test was vacuous. Its fixtures were an hour apart (`04:00:00Z` vs `05:00:00.000000+00:00`), and the
hour differs *before* the `Z`/`.` byte is ever compared, so string order and instant order agreed:

```text
MY FIXTURE   string says B newer? true   instant says B newer? true   AGREE -> test cannot fail
DISAGREEING  string says D newer? false  instant says D newer? true   DISAGREE -> test can fail
```

Fixed to stamps differing **within the same second**, with the test now asserting its own fixture
disagrees before asserting the behaviour — so a future edit cannot quietly make it vacuous again. A
second test was added for `readCase`'s own escalation picker, which had none. Both fail under the
sabotage now. This is the fourth consecutive task where the sabotage step found something the
implementation and a green suite did not.

### Read against the live table (Step 6)

**`node --experimental-strip-types` cannot run these modules** — Node's ESM resolver requires file
extensions and `cases.ts` imports `"./env"`, giving `ERR_MODULE_NOT_FOUND`. The plan's `npx tsx`
fallback has the same problem. Run it through **vitest**, which already resolves the `@/` alias: a
temporary `web/live-check.test.ts` and `web/vitest.live.mts` (the config must be inside `web/`, or
`vitest/config` does not resolve), with `disableConsoleIntercept: true` and `reporters: ["verbose"]` to
make the output visible. Both deleted afterwards.

```text
QUEUE: 3 rows (GSI holds 18)
  c-012  2026-10-12  filed=false  prog=""  A caseworker must decide. source_conflict: household size 5
  c-010  2026-10-18  filed=false  prog=""  missing_document: proof_of_residency is not on file (Grace h
  c-011  2026-10-22  filed=false  prog=""  material_income_change: Income moved 30.0%, above the 5.0% i

CASES: 12  acted=9 escalated=3 error=0 filed=9
  c-001 acted  prog=medicaid deadline=2026-10-15 filed=true   … through c-009
  c-010 escalated prog=- deadline=2026-10-18 filed=false
  c-011 escalated prog=- deadline=2026-10-22 filed=false
  c-012 escalated prog=- deadline=2026-10-12 filed=false

c-001: 52 ledger rows, chronological=true, trace_id null on 52 of 52
c-010: 65 ledger rows, chronological=true, trace_id null on 65 of 65
c-011: 41 ledger rows, chronological=true, trace_id null on 41 of 41
c-012: 42 ledger rows, chronological=true, trace_id null on 42 of 42

PII scan of everything the reader returned: CLEAN
```

**Three queued cases from 18 GSI rows**, in deadline order `c-012`, `c-010`, `c-011` — the assertion the
fake cannot make. **9 acted / 3 escalated / 0 error**, matching the deployed sweep and derived per case
from the ledger. `trace_id` is **null on all 200 rows read**, which is the honest reading of Runtime
never installing an in-process tracer provider, rendered as "not traced".

### The table grew, and the strip held

Re-measured during this task: **643 rows** (not 633) and the GSI holds **18** (not 17) — a sweep ran in
between, so both numbers are measurements with a date. A table-wide scan of all 643 rows for every
fixture surname and for `+1555` returns **clean**, confirming the 2026-09-04 strip held.

Also corrected in `docs/plan3-live-data-findings.md`: `d_trace_id` is `NULL` on **613 of 625** ledger
rows, and **12 rows on `c-003` lack the attribute entirely** — so a reader must tolerate absent as well
as `NULL`, which the findings doc's "essentially every row" could have been read as ruling out.

### Re-verified independently, against the shipped files

Everything above is the implementor's account. Re-run by the verifier on the committed code, with a
separately-written harness of 30 sabotages rather than the implementor's 24:

- **All four `web/` gates green plus 622 Python** — `tsc --noEmit` silent, `eslint .` clean output,
  `next build` compiled, and `git diff` against Task 1's commit shows `grace/`, `evals/`, `infra/`,
  and `fixtures/` untouched.
- **The live read re-measured through the modified code**: queue `c-012, c-010, c-011`; **12 cases,
  9 acted / 3 escalated / 0 error / 9 filed**; **625 ledger rows scanned, every case chronological,
  zero hits** across all twelve fixture surnames plus `+1555` and `Household`.
- **Three sabotages survived the suite as shipped**, each now covered by a test that was watched
  failing: `readEnv()` moved inside `readCase`'s `try` (every household reads back `null`, dashboard
  looks healthy), the per-case `MAX_PAGES` cap raised (`readCase` catches the throw, so an uncapped
  loop hangs the SSR page rather than failing it), and the `error` status reaching `authorize`'s
  `not_escalated` message.

**One measurement artifact worth recording.** Removing `MAX_PAGES` entirely does not produce a test
failure — the vitest worker dies with `SIGABRT` mid-file, and a JSON reporter records that as *zero*
failed assertions with the file's other tests simply absent. A harness that counts failed assertions
would score it SURVIVED. Raising the cap to a large finite number instead makes it fail as a normal
assertion. **When a sabotage can crash the runner rather than fail an assertion, "the suite went red"
and "a test caught it" are different signals** — check the reporter's own accounting, not just the
exit code.

---

## Running locally

Filled in by Task 6.

## Deploying

Filled in by Task 7.
