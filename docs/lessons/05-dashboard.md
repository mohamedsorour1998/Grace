# Lessons: building the caseworker dashboard (Plan 3)

Moved verbatim from `CLAUDE.md` on 2026-10-31. Each lesson was learned on a real defect; paths and counts are as of when it was written.

### What Plan 3 established — follow these

Plan 3 adds a Next.js caseworker dashboard in `web/`, deployed to Amplify with Cognito auth. It also
un-defers **AgentCore Identity**, which Task 4 shipped — so the honest surface count is **four**, and
both the "Scope is four AgentCore surfaces" paragraph above and the README now say so. **Plan 3 is
complete**; `docs/evidence/dashboard-verification.md` is the pasted evidence.

**"Identity shipped" means one specific thing, and the narrow version is the only honest one.** What
shipped is a **Cognito user pool whose ID token is the dashboard's trust anchor** — `verifySession`
checks signature, issuer, audience, expiry, `token_use: "id"`, and `custom:role === "caseworker"`, and
any single failure yields `null` rather than a lesser session. What did **not** ship is an **AgentCore
Gateway JWT authorizer** (`customJWTAuthorizer` with inbound claim rules); the runtime is still
IAM-authorised. Never let the two collapse into "Grace uses AgentCore Identity" without the qualifier.

Task state:

| Task | State |
|---|---|
| 0 — preflight | **done** — eight of the plan's dependency pins were wrong; corrected from real registry data |
| 1 — scaffold `web/` | **done** — commit `e3ea347`, four gates green (`typecheck`, `lint`, `test`, `build`), Python suite unchanged at 622. **Two more version pins and three config defects; read below** |
| 2 — `lib/authorize.ts` | **done** — `web/lib/authorize.ts`, 19 tests (21 with Task 1's smoke), four gates green, Python unchanged at 622. **Four defects in the plan's draft, three of them vacuous or dead tests; read below** |
| 3 — `lib/cases.ts` | **done** — `web/lib/{env,cases}.ts`, **61 vitest tests** (39 cases + 20 authorize + 2 smoke), four gates green, Python unchanged at 622. **Ten defects in the plan's draft, one of which reported an unfiled case as `acted`; three more found in independent verification. Read below** |
| 4 — Cognito + session verification | **done** — `infra/provision_cognito.py`, `web/lib/cognito.ts`, `web/proxy.ts`, login page + callback route. **9 Python tests (631 total), 78 vitest**, five gates green. Pool `us-east-1_HXs3b0APR` live, one seeded caseworker, `custom:role` verified on the user. **Nine defects in the plan's draft, two of which broke a gate and two of which were inverted security claims; read below** |
| 5 — `lib/decide.ts` + the write route | **done** — `web/lib/decide.ts`, `web/app/api/case/[id]/decide/route.ts`, `grace/entrypoint.py` (flag only), 16 Python tests (**647 total**), **122 vitest**, five gates green. **The draft decoded a streaming response as bytes, which would have reported every approve as a failure after it succeeded; read below** |
| 6 — pages | **done** — `web/app/{page,queue,case/[id]}/`, `web/components/`, `web/lib/session.ts`, **154 vitest**, Python unchanged at 648, five gates green. **The pages had no session verification at all — a forged cookie returned 200 with every case id. Read below** |
| 7 — Amplify | **done** — `infra/provision_amplify.py`, **715 Python tests**, app `dbi97xicbjbv8` live at `https://grace.rosettacloud.app` serving real data. **Thirteen defects in the plan's draft; four of my own tests initially survived sabotage. Read below** |
| 8 — verification + docs | **done** — `docs/evidence/dashboard-verification.md`, four-surface README with the AWS Builder ID field, dashboard tier added to `docs/architecture.md`. **715 Python / 157 vitest, four gates green.** The end-to-end approval was executed against the live system: **approving `c-010` filed nothing.** Read below |

**`web/` runs its own toolchain, and `npm run build` is the gate that matters** — it is what Amplify
runs, so a green `typecheck` and `test` with a failing `build` is not a deployable app. Four commands,
all from `web/`: `npm run typecheck`, `npm run lint`, `npm run test`, `npm run build`. The Python suite
is separate and must stay at its current count; `web/` is additive and may never change it.

**TypeScript is pinned to 6.0.3, not 7.x, and this is not a stale pin.** TypeScript 7 is the native Go
port and `typescript-eslint` **refuses to load under it** — an explicit
`throw new Error("typescript-eslint does not support TS 7.0.")` guarded by `if (versionMajor >= 7)` in
its own `dist/index.js`. `eslint-config-next@16.3.4` depends on `typescript-eslint@^8.46.0`, so
pinning TS 7 means `npm run lint` cannot run at all. `tsc --noEmit` and `next build` pass identically
under 6.0.3, so nothing is lost. Upstream tracking issue: `typescript-eslint#10940`. **Do not bump
`typescript` past 6 until that closes.**

**`@aws-sdk/util-dynamodb` is on 3.996.9 while its sibling clients are on 3.1125.0, and that is
correct.** That package never entered the `3.1xxx` series — `latest` is 3.996.9 across 568 published
versions. Its peer range is `@aws-sdk/client-dynamodb: ^3.1111.0`, which 3.1125.0 satisfies, so the
pair is compatible. A "consistency" bump to 3.1125.0 installs a version that does not exist.

**`eslint-config-next` exports an array, not a factory.** `import next from "eslint-config-next"` then
`[...next()]` throws `next is not a function`; its own `dist/index.d.ts` reads
`declare const config: Linter.Config[]; export = config`. Spread the value: `[...next, ...]`.

**Every `.mjs` config must assign to a named `const` before exporting.** `eslint-config-next` enables
`import/no-anonymous-default-export`, so a bare `export default { ... }` in `postcss.config.mjs` or
`export default [ ... ]` in `eslint.config.mjs` makes the config lint *itself*. Clean lint output, not
merely a zero exit code, is the bar.

**`vitest.config.mts` uses `import.meta.dirname`, never `__dirname`.** Vitest 5 warns on every run
that `__dirname` is unsupported by `configLoader: "native"`, which is planned to become Vite's
default.

**`next build` rewrites `tsconfig.json` in place** — it forces `jsx` from `"preserve"` to
`"react-jsx"` (Next 16 uses the React automatic runtime) and appends `.next/dev/types/**/*.ts` to
`include`. Next's version is the committed one; reverting it just makes the next build rewrite it.
`*.tsbuildinfo` is gitignored because `incremental: true` writes an absolute-path-keyed cache on every
typecheck.

**The static-export guard is real, and was watched failing.** `__tests__/smoke.test.ts` asserts
`next.config.ts` does not set `output`; adding `output: "export"` fails it with
`expected 'export' to be undefined`. A static export has **no route handlers and no middleware**, so
it would silently delete the Cognito gate and the decide endpoint — the app would build, serve, and be
wrong. **Never add `output: "export"` to `web/next.config.ts`.**

**`web/` carries no resume vocabulary and no `NEXT_PUBLIC_` variables.** `interruptResponse`,
`APPROVE_DECISIONS`, and `MAX_RESUME_ROUNDS` appear nowhere — Plan 3's approve/deny path records the
decision and **re-invokes so the gate re-evaluates**, it never resumes a paused graph (any truthy
resume response approves the blocked tool; see Task 6 of Plan 1). No `NEXT_PUBLIC_` variable exists
either, because anything so prefixed is inlined into the client bundle, and every value this app holds
is either a credential or a household-scoped identifier.

**A TypeScript type is a promise a caller makes, not a fact `authorize` may assume — and an `as`
cast is where the promise stops being checked.** Task 5's decide route builds its `DecisionAttempt`
from `await request.json()` with a bare `body.decision as "approve"`, so both fields arrive as
whatever a client posted, and `DecisionAttempt.note: string` is erased at compile time. Two
consequences, both measured and both now guarded in `web/lib/authorize.ts`: a **non-string `note`**
passes the length cap silently (`undefined > 2000` is `false`) or throws `TypeError` if it is `null`,
turning a refusal into a 500 out of the pure gate; and a **non-finite `expiresAt`** defeats the
expiry check entirely, because `Infinity <= nowMs` is `false`. The second is reachable, not
theoretical: `JSON.parse('{"exp":1e400}').exp` is `Infinity`, `typeof` it is `"number"`, and `jose`
**verifies such a token** — confirmed against a real RS256 key pair. So `cognito.ts` must use
`Number.isFinite(payload.exp)`, not `typeof payload.exp !== "number"`. Refuse the type; never
coerce it, because coercion invents a note nobody wrote.

**A discriminated union invites the `if` that deletes the assertion.** Plan 3's Task 2 draft wrote
every refusal-code check as `if (!r.permitted) expect(r.code).toBe(...)`, and one whole test body as
`if (r.permitted) { ... }`. Measured by rewriting `authorize` to always refuse: **10 of 14 tests
failed and 4 passed**, including `carries the opaque sub, never a name`, whose body never ran.
Narrow by **throwing** on the wrong variant (`refusalOf`/`permitOf`) so every assertion is
unconditional — the Task 8 vacuity lesson, in the shape TypeScript encourages.

**A purity guard that greps for literal spellings is a denylist, and leaked three ways.** The draft
forbade `node:fs`, `Date.now()`, `fetch(`, `@aws-sdk`, and `process.env`; `from "fs"`,
`new Date().getTime()`, and `globalThis.fetch` were each added to `authorize.ts` and **passed**.
The guard now enumerates the `import` statements actually present and requires each to be type-only
and relative, which is a positive check — the same discovery-from-disk discipline as Task 4's
model-ID `pkgutil` walk, for the same reason.

**Session checks must precede fact checks.** Otherwise the difference between `no_session` and
`unknown_case` tells an unauthenticated caller which case IDs exist. Pinned by a test that compares
the two refusal codes for real and null facts.

**An env var that swaps out a trust anchor must never be readable in production.** Task 4's draft read
`process.env.COGNITO_TEST_JWKS` from `verifySession`'s key resolver with no environment guard — so
setting that one variable on the deployed app replaces Cognito's published key set with an
attacker-supplied one, and every forged token then verifies with a *valid signature*. Nothing
downstream would notice, because against that key set the session genuinely is authentic. Gated on
`process.env.NODE_ENV === "test"` (vitest sets `NODE_ENV="test"` and `VITEST="true"` — measured), with
a test that flips `NODE_ENV` to `"production"`, re-imports the module, and asserts the *same* token
stops verifying. `vi.resetModules()` on both sides, because `cachedKeys` is module-level and a
resolver cached under one environment would answer for the other.

**Cognito's real ID token was minted and read, and `jose`'s refusals were executed.** A throwaway pool
with `ALLOW_ADMIN_USER_PASSWORD_AUTH` produced a genuine token: `custom:role` **is** present with
`ReadAttributes` set, `token_use` is exactly `"id"`, `aud` equals the client id, `sub` is an opaque
UUID, and there is no `email`. The **access token is a different shape** — `token_use: "access"`, no
`custom:role`, and **no `aud` at all** — so the `token_use` check guards a real distinction rather than
being defensive. Separately, `jose` 6.2.10 was verified to refuse a wrong key, an expired token via
`currentDate` (in both directions), a wrong issuer, a wrong audience, a non-JWT, **`alg: "none"`**, and
**HS256 algorithm confusion** signing with the public key's `n` as an HMAC secret — the last two with
`ERR_JOSE_ALG_NOT_ALLOWED`, through both `createLocalJWKSet` and a hand-rolled resolver, so the
`algorithms: ["RS256"]` allowlist binds before any key is fetched.

**Never escape a string before handing it to JSX — React already escapes text children, and doing it
twice is visible to the user.** Task 6's draft ran a caseworker's note through an HTML-escaping helper
and then rendered `{escapeNote(d.note)}`. Measured with `renderToStaticMarkup`: a note reading
`The family's wage record is stale.` renders as `The family&#39;s wage record is stale.` on screen,
while React alone turns `<img src=x onerror="alert(1)">` into `&lt;img ...` with no live tag. The
draft's own "leaves ordinary prose alone" test used a fixture with **no apostrophe**, so it passed
against the bug. Replaced with `noteIsInert`, a **check** rather than a transform — it answers "would
this note be safe if someone reached for `dangerouslySetInnerHTML`?", which is the only path markup
could take. Nothing rewrites the caseworker's words. (`authority.py`'s "whoever renders it escapes it"
still holds; here the renderer *is* React.)

**A hard-rule-9 name assertion must list every fixture surname, and must be proven able to fail.**
Task 6's draft matched `Mensah|Rivera|Okonkwo` — three of twelve — and the two households most likely
to carry a name in an escalation reason are `c-010` and `c-011`, **Fitzgerald** and **Yamamoto**,
neither of which was in the pattern. All twelve are listed now, plus `+1555` and `Household`, and a
companion test feeds a name in through `reason` (the exact path that reached CloudWatch) and asserts
the guard catches it — otherwise "no name in this row" is true of every input and proves nothing.

**Fixture data in a plan must match the live table, or the instruction and the code contradict each
other.** Plan 3's Task 3 carried a findings block saying to use the real deadlines and a fixture below
it still using invented ones, leaving the implementor to guess. The ordering test now uses the real
`c-010`/`c-011`/`c-012` deadlines **plus a third row**, so deadline order, escalation-time order, and
GSI order all differ on the same input — which is what makes the assertion distinguish "sorted by
deadline" from "whatever the GSI returned".

**"Not escalated" is not the same claim as "Grace filed the renewal" — hard rule 6 at the read
boundary.** Task 3's draft classified a case as `pending ? "escalated" : "acted"`, so a household with
no pending escalation and no `renewal_submitted` row was reported **acted**: silently counted among the
nine while nothing had been filed for it. The shipped reader requires the ledger row that proves a
filing (`pending ? "escalated" : filed ? "acted" : "error"`), which also makes `CaseStatus`'s `error`
variant reachable — `authorize` already refuses it as undecidable, so it was otherwise a shipped,
tested, unreachable guard. **`listQueue`'s `filed` is `false` by construction, not by measurement**: the
`escalation-queue` GSI projects escalation rows only, so that query cannot see a renewal row. Never
render `filed` from `listQueue`; `listCases` reads the ledger for all twelve cases instead.

**A fix that makes a dead branch reachable is not finished until you read what that branch says.**
Requiring evidence for `acted` (above) made `CaseStatus`'s `error` variant reachable for the first
time — and `authorize` was refusing `acted` and `error` with the same code and the same sentence,
"Grace handled this case itself; there is nothing to decide." Harmless while unreachable; false the
moment it was not, because `error` means nothing was filed **and** nothing escalated. That is the
unconfirmed-success claim hard rule 6 exists to forbid, aimed at the person who could still save the
family: a caseworker told Grace handled it moves on. Split into a `case_incomplete` code reading
"Grace's last run on this case reached no outcome. Re-run the sweep before deciding." Both still
refuse — only the wording changed, and the polarity was never wrong. **When a change widens the set of
inputs a branch can see, re-read that branch's message as well as its logic.**

**Three guards in `lib/cases.ts` survived the implementor's own 24-sabotage sweep, and two were
documented-but-untested.** Its header comment says `readEnv()` sits outside `readCase`'s `try` "on
purpose"; moving it inside passed all 58 tests, and the consequence is that a missing
`GRACE_TABLE_NAME` makes all twelve households read back `null` — `/` renders an empty caseload,
`/case/[id]` renders not-found, and nothing logs. Separately, `MAX_PAGES` was pinned only through
`listQueue`, which throws; `readCase` **catches**, so an uncapped loop there hangs the SSR request
instead of failing it (Plan 1 Task 6's resume loop, on the request path). **A comment explaining why
a line is where it is does not test that it stays there** — if a sentence in a docstring states a
safety property, there should be an assertion with the same content.

**A sabotage that crashes the test runner scores as SURVIVED on an assertion-counting harness.**
Removing `MAX_PAGES` entirely kills the vitest worker with `SIGABRT` mid-file; the JSON reporter then
records **zero** failed assertions, with the file's other tests simply absent from the report.
Raising the cap to a large finite number instead produces an ordinary failure. So a sabotage harness
must distinguish "no test failed" from "the run did not complete" — check the reporter's own
accounting and the number of tests that reported at all, not the exit code, and never read a green
assertion count from a run that aborted.

**An ISO timestamp compared as a string inverts, and the obvious fixture cannot catch it.** `Z` (0x5A)
sorts above `.` (0x2E), so `"…T05:00:01Z" > "…T05:00:01.500000+00:00"` is `true` while the offset row is
the *later* instant — the **older** row wins a newest-wins dedup. Grace writes `+00:00` microsecond
stamps and nothing stops another writer using `Z`. Compare with `Date.parse`, in **both** places that
pick a newest escalation (`listQueue` and `readCase` each have their own picker; fixing one does not fix
the other). **The stamps in such a test must differ within the same second** — a fixture an hour apart
agrees under both orderings, because the hour differs before the `Z`/`.` byte is reached, and a
string-comparison sabotage against it *survived*. The test now asserts its own fixture disagrees before
asserting the behaviour.

**`parseInt` on a DynamoDB `N` chosen by a `.`-test reads a large number back as `1`.** boto3
serializes `Decimal` in canonical form, so `1e30` arrives as `{"N": "1E+30"}` — no `.` in it — and
`parseInt("1E+30", 10)` is **1**, a value a factor of 1e30 too small with no error anywhere. Use
`Number()`. One such row exists live (`c-002`, the type round-trip row, which carries the table's only
`BOOL` and `N` values).

**`as NodeJS.ProcessEnv` does not compile under Next 16.** It declares `NODE_ENV` as a **required**
property (`next/types/global.d.ts:23`), so `{ AWS_REGION: "x" } as NodeJS.ProcessEnv` fails with
`TS2352`. `readEnv` takes `EnvSource = Readonly<Record<string, string | undefined>>` — what it actually
needs, and `process.env` is assignable to it. Casting through `unknown` would reintroduce Task 2's
"the promise stops being checked" hole.

**A placeholder belongs to the renderer, never to the data layer.** The draft's `str(row.program, "—")`
was structurally always a dash, because no escalation row carries `program` at all (measured across all
18; `d_program` exists only on `renewal_submitted` rows). A presentation dash inside `lib/cases.ts` is a
magic value a caller cannot tell from real data — return `""` and let Task 6's `{summary.deadline || "—"}`
decide. Same division of labour as `authority.py` leaving escaping to whoever renders `detail`.

**Grace's own outcome row must not count as a human decision.** Task 5 writes
`sk: DECISION#<ts>#outcome`, which shares the prefix `readCase` collects decisions by and carries no
`decision` attribute. Under a naive prefix test it becomes a phantom **deny attributed to nobody**
(`decided_by` is absent), and an outcome written before any human decision makes `alreadyDecided` true —
so the **first** caseworker decision on a case refuses itself as a duplicate. `readCase` discriminates
on the presence of `decision` and joins the outcome to its decision by their shared `decided_at`; the
draft read `outcome` off the human row, where it is never written, making `Decision.outcome`
structurally always `null`. An **unrecognised** decision word must still *count* as a decision (or the
case becomes decidable twice) while *displaying* as a deny — the opposite polarity to `authorize`'s
allowlist, and for the same reason: never imply a human authorised a filing they did not.

**The live table is 643 rows and the GSI holds 18, not 633/17** — a sweep ran between
`docs/evidence/live-data-findings.md` and Task 3. Treat both as measurements with a date, not constants.
Also: `d_trace_id` is **not** universally present — 613 of 625 ledger rows carry it as `{"NULL": true}`
and **12 rows on `c-003` lack the attribute entirely**, so a reader must handle absent *and* `NULL`.

**A `queryAll` on an SSR request path needs a page cap, and it must throw rather than truncate.**
Plan 1 Task 6 ran an uncapped resume loop to 500 rounds; here a service repeating a `LastEvaluatedKey`
hangs a page instead of failing it, which is worse. Truncation is worse still — a DynamoDB Query drops
the *newest* rows, exactly where `renewal_submitted` lives, so a filed renewal reads as unfiled.
`readCase` turns a throw into `null` (fail closed) and would turn a truncation into a confident wrong
answer.

**`node --experimental-strip-types` cannot run `web/`'s modules**, so a live-data check goes through
vitest. Node's ESM resolver requires file extensions and `lib/cases.ts` imports `"./env"`; the plan's
`npx tsx` fallback has the same problem. A temporary test file plus a `vitest.*.mts` config **inside
`web/`** (outside it, `vitest/config` does not resolve) works, and `disableConsoleIntercept: true` with
`reporters: ["verbose"]` is what makes the output visible. Delete both afterwards.

**The JS SDK retries `InvokeAgentRuntime` three times by default, and the knob is not boto3's.**
Plan 2 established the hazard — the call is **not idempotent**, so each attempt re-runs the whole graph
against the same case and could file one renewal more than once. Task 5's draft built
`new BedrockAgentCoreClient({ region })` with no retry config, reintroducing it in a different SDK.
Measured against a black-hole socket (accepts, never replies, so the accept count *is* the attempt
count): **default 3, `maxAttempts: 1` exactly 1** — and confirmed off the client's own resolved config,
where `maxAttempts` is a provider function. **Do not carry Plan 2's boto3 finding across verbatim:**
there `max_attempts: 1` still gave 2 and only `total_max_attempts` gave 1; here `maxAttempts: 1` is
sufficient and `total_max_attempts` does not exist. Same hazard, different knob.

**`requestTimeout` alone does not bound a JS SDK call — it warns and hangs.** Measured:
`@smithy/node-http-handler - [WARN] a request has exceeded the configured requestTimeout. Init client
requestHandler with throwOnRequestTimeout=true to turn this into an error.` The promise stays pending,
which in an SSR route holds the caseworker's browser open with no error to report. Always pass
`throwOnRequestTimeout: true` alongside it.

**An extra payload key is harmless on the deployed runtime, verified live rather than by reading.**
`process_case` validates the container type and reads `case_id`/`today` by key with no allowlist.
Invoking `grace_grace-oTyyvo8stE` with `caseworker_approved: true` added returned 200 in 10.2s and
`c-010` **still escalated** on `missing_document` — so the approval flag changes no verdict, and it
changes nothing on the deployed version 2, which does not know the key yet.

**A Next middleware matcher's negative lookahead matches a *prefix*, not a path segment.** Task 4's
draft `"/((?!login|api/auth|...).*)"` left `/loginx` and `/api/authorize` **ungated** — measured.
Neither route exists today, which is exactly what makes it the kind of bug that ships later: someone
adds `/api/authorize` and it arrives unguarded. Anchor each alternative on a segment boundary
(`login$|login/`, `api/auth$|api/auth/`, `favicon\.ico$`) and verify the regex with a table of paths
rather than eyeballing it. This is a *redirect convenience* rather than the security boundary —
`verifySession` refuses independently on every page and on the decide route, and `proxy.ts` says so in
its own docstring — but a guard with a hole in it invites someone to start trusting it. **The `node -e`
table the plan uses proves it once and guards nothing after**, so `__tests__/cognito.test.ts` now reads
`config.matcher[0]` off the module and drives the same paths through it; sabotaging the anchors back to
bare prefixes fails two tests.

**`Number.isFinite` is not a type predicate, so the expiry guard did not compile.** Its declaration is
`isFinite(number: unknown): boolean` — a plain `boolean` — so `if (!Number.isFinite(payload.exp)) return null;`
leaves `payload.exp` typed `number | undefined` and `payload.exp * 1000` fails with
`TS18048: 'payload.exp' is possibly 'undefined'`. The plan's `cognito.ts` did not pass `tsc`. Fixed with
a one-line `isFiniteNumber(value): value is number` wrapper, which keeps `Number.isFinite` the **only**
runtime check — adding `typeof exp === "number"` alongside would re-admit the hazard, since
`typeof Infinity` is `"number"`, and `payload.exp!` is the non-null assertion Task 2 called "where the
promise stops being checked". **And the guard was shipped untested:** the plan calls it load-bearing and
has no assertion for it, so a test now hand-builds `exp: 1e400` and watches it refused. Measured, all
three parts: `JSON.parse('{"exp":1e400}').exp` is `Infinity`, `jose` **verifies** that token, and
`Infinity * 1000 <= Date.now()` is `false` — a permanent session. The token must be signed with
`CompactSign` over raw bytes because `SignJWT` refuses it first (`"exp" claim must be a finite number`),
which is the point: the hazard is a token an attacker crafts, not one `jose`'s builder would emit.

**`process.env` accepts a `defineProperty` descriptor only with all three flags set.** Node throws
`'process.env' only accepts a configurable, writable, and enumerable data descriptor` on any subset —
measured on all four. The `NODE_ENV`-flipping test in Task 4's draft set `configurable` alone, so it
threw on the way *in*, and the identical call in its own `finally` threw again and replaced the original
error. Worse than the error: with the write refused, `NODE_ENV` stayed `"test"`, so the assertion would
have been checking the injected-keys path against itself. The test now sets all three flags, **asserts
`process.env.NODE_ENV` actually changed** before trusting the refusal, and re-verifies the same token
after restoring — a one-way guard that left the module permanently refusing would otherwise look
identical to a working one.

**A Next page that reads configuration must be `force-dynamic`, or `next build` runs it.** `/login`
calls `hostedUiUrl`, which throws on a missing `COGNITO_DOMAIN` — and Next prerenders a page with no
dynamic API by default, so the build failed outright: `Error: COGNITO_DOMAIN is not set.` …
`Export encountered an error on /login/page, exiting the build`, exit 1. `npm run build` is the gate
Amplify runs, so this was a shipped-broken deploy. The quieter half is why `force-dynamic` is the right
fix rather than supplying the variable at build time: with it present, the redirect URL — client id and
`DASHBOARD_URL` included — bakes into the bundle, so rotating the app client keeps sending caseworkers
to the old one until someone rebuilds. A sign-in redirect is request-time configuration.

**The test-only JWKS resolver must select by `kid` and throw on a miss.** With `?? keys[0]`, a token
naming an unknown `kid` verifies against whichever key is first. Reproduced with a two-key harness:
strict resolver → right key `ACCEPTED`, wrong key same kid `ERR_JWS_SIGNATURE_VERIFICATION_FAILED`,
unknown kid `no key for kid other-kid`; loose resolver → unknown kid **`ACCEPTED`**. Grace's own pool
publishes **two** RS256 signing keys (verified live), so "checks `kid`" and "ignores `kid`" are
genuinely different verifiers and the loose one makes the suite unable to tell them apart — test
vacuity, not a production bypass, since this branch is gated on `NODE_ENV === "test"`.

**Two live facts that make the verifier's allowlists exact rather than defensive.** Read off Grace's
pool: `jwks_uri` is exactly `${issuer}/.well-known/jwks.json`, and
`id_token_signing_alg_values_supported` is `["RS256"]` and nothing else — so `algorithms: ["RS256"]`
restricts nothing legitimate. Also confirmed on the provisioned pool: tier `ESSENTIALS`,
`ManagedLoginVersion: 1` (classic hosted UI, which is what `hostedUiUrl`'s `/login` path targets),
seeded user `CONFIRMED` (so `admin_set_user_password(Permanent=True)` is required, not a convenience),
and the converge branch's full replace preserved `ReadAttributes`/`WriteAttributes`/flows/scopes/
callbacks on a second run.

**`ListUserPoolClients` paginates too, and the draft read one page.** The pool lookup above it pages
correctly; the client lookup did not. A missed page creates a *second* `grace-dashboard` client, and
`verifySession` checks `aud` against the one id in the environment — so a token minted by the other
client is refused **with a valid signature**, which reads as "auth is broken" rather than "there are
two clients". Confirmed off the service model: `NextToken` on both input and output,
`can_paginate: True`.

**Next 16.3.4 deprecates the `middleware.ts` file convention in favour of `proxy.ts`**, and Grace has
made the rename: the file is `web/proxy.ts` and the exported function is `proxy`. It was a `warnOnce`
on every build, and clean output rather than a zero exit code is this project's bar.
`PROXY_FILENAME = "proxy"` is present in `next/dist/lib/constants.js`, so the new convention is
genuinely supported here rather than aspirational; the build still labels the compiled output
`ƒ Proxy (Middleware)`. **Never ship both files — that is a hard error, not a warning.** Done before
Task 5 deliberately, so its `route-guard.test.ts` is written against the current name rather than
renamed afterwards.

**Two of Task 4's plan defects were inverted security claims — a comment asserting the opposite of the
measured behaviour.** Both were caught by probing the control rather than reading the API's response,
and both had been recorded in `docs/evidence/live-data-findings.md` as *confirmed* findings while being
backwards:

1. **Omitting `WriteAttributes` does not withhold write access — it grants everything.** The draft
   called the omission "capability absence: the client cannot rewrite the claim that authorises it."
   Measured on a throwaway pool, a signed-in user's `UpdateUserAttributes` against an ungranted
   **mutable** custom attribute **succeeded**. `custom:role` survived only because the schema marks it
   `Mutable: False` — one guard, where the comment claimed two. The AWS docs say so outright: omitting
   read/write permissions grants *all* attributes. Fixed to `"WriteAttributes": ["email"]`, and
   re-verified against the **shipped constant**: a write to `custom:role` is now refused
   `NotAuthorizedException: A client attempted to write unauthorized attribute` — an *authorisation*
   refusal, and the previously-succeeding mutable write is refused too.
2. **`UpdateUserPoolClient` is a full replace, not a patch.** An update naming only `ClientName` left
   `ReadAttributes`, `CallbackURLs`, and `AllowedOAuthFlows` **absent** afterwards. So the converge
   branch must resend every field it intends to keep; a later "tidy this into a two-key delta" edit
   would silently delete the OAuth flows and the `custom:role` read permission, and every sign-in would
   then fail closed with nothing failing at provision time. It also rejects `GenerateSecret` with
   botocore's `ParamValidationError` — **not** a `ClientError`, so no `except ClientError` catches it.

**Why the first one was easy to get wrong, which is the transferable part.** Probing `custom:role`
alone *does* produce a refusal — `InvalidParameterException: Attribute cannot be updated.` — but that
is the **immutability** guard, not an authorisation one. Reading it as evidence that omission withholds
permission confirms the wrong mechanism. **To verify a control, perform the action it should prevent
against a target where only that control can refuse.** Same shape as Plan 2's point-in-time-recovery
finding: "the API accepted my configuration" and "the control does what I think" are different claims.

**An index needs its own ARN in an IAM policy, and the failure is invisible to every page but one.**
Measured with `simulate-principal-policy` on throwaway roles: granting `dynamodb:Query` on
`table/grace-cases` alone yields `allowed` on the table and **`implicitDeny` on
`table/grace-cases/index/escalation-queue`**. `readCase` reads the table and `listQueue` reads the
index, so a table-only grant leaves `/case/c-010` rendering perfectly while **`/queue` comes back
empty** — the one page the product exists for, blank for exactly the three households who need a human,
after a green deploy. Task 7's compute role must name both ARNs. `implicitDeny` rather than
`explicitDeny`, so it can be added later without fighting a statement.

**A streaming response body decoded as bytes fails *after* the work already succeeded, which is the
worst shape a bug can have here.** `InvokeAgentRuntimeResponse.response` is typed
`StreamingBlobTypes` (confirmed in the SDK's own `models_0.d.ts`) and arrives as a Node
`IncomingMessage` carrying `sdkStreamMixin` — not a `Uint8Array`. Task 5's draft called
`new TextDecoder().decode(response.response)`, which throws
`TypeError: The "list" argument must be an instance of ... ArrayBufferView`. That throw lands in
`lib/decide.ts`'s own `catch`, so **every** approve would have reported "Grace could not be re-run"
while the invocation had in fact run to completion: a caseworker told Grace failed on a case Grace had
just decided, no error anywhere, and **no test that mocks the client would notice**, because a mock
returns whatever shape the test author imagined. `transformToString()` is the supported read.
**When a client returns a stream, assert against the real client's types, not against your fake's.**

**Grace's outcome row must not look like a human decision, and `lib/cases.ts` already depends on
exactly how it differs.** Task 5 writes `DECISION#<ts>#outcome` with **no `decision` attribute**, and
`readCase` discriminates on that attribute's presence, joining the outcome to its human row by their
shared `decided_at`. Under a naive prefix test the outcome row becomes a phantom deny attributed to
nobody (`decided_by` is absent), and an outcome written before any human decision makes
`alreadyDecided` true — so the **first** caseworker decision on a case refuses itself as a duplicate.
Do not change either field without reading `readCase`'s `DECISION#` branch.

**The decision row is written *before* the invocation, which is deliberately the opposite of the
ledger's discipline.** Both are right because they claim different things: a ledger row claims *Grace
did something*, true only once a tool returned (hard rule 6); a decision row claims *a human decided*,
true the moment they clicked. Losing that to an infrastructure error would discard the caseworker's
work and leave the case silently unresolved. So `recordDecision` throwing means "nothing happened",
and the route can honestly return 503 saying so.

**`is True` / `=== true`, never truthiness, on any flag that crosses a JSON boundary.** An HTTP body
can carry `"false"`, `1`, or `[0]`, all of which are truthy in Python and JavaScript; only the JSON
boolean deserialises to a real `true`. `grace/entrypoint.py` reads
`payload.get("caseworker_approved") is True` and `lib/decide.ts` reads `body.filed === true`. Same
allowlist-over-truthiness polarity as `APPROVE_DECISIONS`, for the same reason: **the unrecognised
value must be the safe one.** Both are pinned by a test that fails under a `bool()`/truthy rewrite.

**Map refusal codes to HTTP status through a `Record<RefusalCode, number>`, not a ternary chain.**
The `Record` makes adding a code in `lib/authorize.ts` a **compile error** at the route instead of a
silent fall-through to 400 — and it immediately caught `case_incomplete`, which Task 3 added after
Task 5's draft was written and which is a server-side "re-run the sweep" (409), not a client mistake.
A companion runtime test asserts the map is exhaustive, because a `Record`'s keys are erased at
compile time.

**A test that mocks the data layer but not the session tests only the first refusal branch.**
Task 5's draft `route-guard.test.ts` mocked `readFacts` and left the session unmocked, so all four
tests refused at `no_session` and every later branch — role, status, already-decided, the decision
allowlist, the note cap — was unreachable. Its decision-word test then papered over this with
`expect([400, 401]).toContain(status)`, meaning **a route that returned 401 to every authenticated
caseworker would have passed it**. The fix mints a real RS256 token so the route's own cookie parsing
and the real `verifySession` stay in the path, and asserts each code exactly. **A range of acceptable
status codes in an auth test is a smell: it usually means the test cannot tell which branch it reached.**

**A "flag cannot loosen the verdict" test needs a fixture where the verdict could actually loosen.**
Task 5's headline test asserted `c-010` still escalates when approved — but `FakeGraph` files nothing,
so hard rule 6's third branch ("clean case, no renewal filed") escalates it for *every* input.
Measured: with the plan's own sabotage `if caseworker_approved: gate = None` spliced in, `c-010` still
came back `escalated` and every assertion held. The claim was true of the run and unproven by the test.
Arming the store with a `renewal_submitted` row first makes `renewal_filed` return `True`, which gives
the sabotaged code a path to `acted` — so the gate becomes the only thing between an approval and a
household missing a document being reported as filed. That sabotage now fails three tests. **When a
test asserts an outcome that several independent branches all produce, it is not testing the branch you
mean; remove the other branches' alibis first.**

**`vi.fn()` narrows to its first implementation's literal type.** A fake seeded with
`status: "escalated"` makes every later `mockImplementation` returning `"acted"` or `"error"` a
*typecheck* failure — one of the five gates, not a runtime surprise. Type the mock's return explicitly.

**An Amplify manual (zip) deployment does not build the app, and Task 7's draft assumed it did.**
Amplify runs a buildspec **only for Git-connected apps** — `StartDeployment`'s own API documentation
says "Starts a deployment for a manually deployed app. Manually deployed apps are not connected to a Git
repository." So the draft's `zip … web` + `create-deployment` sequence would have uploaded TypeScript
source, deployed nothing runnable, and never executed the `buildSpec` that runs `typecheck`/`lint`/`test`.
A zip must instead contain a **pre-built** Amplify Hosting deployment-specification bundle:
`.amplify-hosting/static/`, `.amplify-hosting/compute/default/` with a self-contained Node entry point
listening on **port 3000**, and a `deploy-manifest.json` whose `routes` end in a catch-all to `Compute`.
**Next.js emits none of it** — measured, a real `npm run build` produces `.next/` with no
`.amplify-hosting/` and no `.next/standalone` absent `output: "standalone"`. So Grace connects the
repository (public, `github.com/mohamedsorour1998/Grace`), which is the supported SSR path.
**That needs exactly one browser step — authorizing the AWS Amplify GitHub app — and there is no API
for it**; `CreateApp`'s `accessToken`/`oauthToken` are legacy personal-token fields that do not cover the
GitHub App installation. It is the only manual step in this project.

**Three Amplify traps that produce a green deploy serving nothing.** First, **framework detection
happens on the *Add repository* page**: a monorepo whose app root is not set to `web` leaves
`platform: WEB` silently, and `WEB` has no route handlers and no middleware — the Cognito gate and the
decide endpoint would simply not exist. Verify `platform` is `WEB_COMPUTE` with `get-app` *before*
building, not after. Second, **`baseDirectory` is `.next` for Next 14+ regardless of SSG or SSR**;
`next export` was removed, and a local build may still leave an `out/` directory that looks like the
right answer but fails with `cannot find required-server-files.json`. Third, **Amplify's build image
ships Node 18**, which Next 16 and `jose` 6 do not support — set the Node version in the build settings
rather than discovering it as a build failure.

**The pages had no session verification at all, and the surrounding documentation asserted they did.**
This is the most serious defect the plan produced, found in Task 6. `proxy.ts`'s own docstring said it
was "a redirect convenience, and **never the security boundary** — a forged cookie gets past it and is
then refused by `verifySession`, which is the check that matters", and that `verifySession` "still
refuses on every page and on the decide route." The second half was true of the decide route and
**false of every page**: `grep verifySession` across `app/` matched only `api/auth/callback` and
`api/case/[id]/decide`. No page read a cookie.

Measured against a real `next start` on the live table, before the fix:

```text
curl localhost:3111/                                    -> 307 /login       (no cookie)
curl -H 'Cookie: grace_session=totally.forged.token' /  -> 200, 45143 bytes,
        all twelve case ids, all three typed escalation reasons, the 9/3 headline
```

An unsigned, unparseable **literal sentence** was a complete authentication bypass for every read in
the application. `web/lib/session.ts`'s `requireSession()` closes it, and all three pages call it
**before** touching `lib/cases.ts`, so an unauthenticated request performs no DynamoDB read either.
Re-verified after the fix on the same server: `307 /login` and **zero case ids** for both `/` and
`/queue`.

**Two lessons, and the second is the general one.** A `redirect()` that throws is the right shape for
this guard — there is no falsy return a caller can forget to branch on, the same reasoning as
`verifySession` having no middle value. And: **a docstring asserting that some other layer performs a
check is not evidence that it does.** The claim was written when it was true of the only consumer that
existed, and stayed in place while the pages were added around it. When a comment says "X is verified
elsewhere", grep for X.

**A PII scanner must be shown to detect PII before its "NONE" means anything.** Task 6's live render
check self-tests the scanner against `Yamamoto`, `Mensah`, and `+1555` and prints those detections
first, then reports `NONE` on the real markup. Without that, "no household identity in the markup" is
equally true of a scanner that matches nothing. Same discipline as the twelve-surname guard having a
companion test that feeds a name in through `reason`.
