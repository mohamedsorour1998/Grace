# Lessons: deploying the dashboard, sign-in, and case intake (Plans 3–4)

Moved verbatim from `CLAUDE.md` on 2026-10-31. Each lesson was learned on a real defect; paths and counts are as of when it was written.

### The dashboard is deployed, and five things had to be fixed after a *green* build

`https://grace.rosettacloud.app` — Amplify app `dbi97xicbjbv8`, `WEB_COMPUTE`, custom domain on
Route 53, compute role `grace-amplify-compute-role` at app and branch level. Verified live with a real
Cognito ID token: `/` renders 12 cases and the **"9 handled alone / 3 waiting on you"** headline,
`/queue` shows exactly `c-010 c-011 c-012` de-duplicated from the GSI's escalation rows, `/case/c-010`
shows its real `missing_document: proof_of_residency`. **Zero household identity** across ~151 KB of
deployed markup. A forged cookie 307s to `/login` with **zero** case ids; the write route returns **401
`no_session`**; `"Escalate."` is refused **400 `unknown_decision`**; an unknown case **404
`unknown_case`**; and a case Grace handled itself **409 `not_escalated`**. Task 8 then executed the
approval those refusals guard: **approving `c-010` wrote a `DECISION#` row and filed nothing** — see
`docs/evidence/dashboard-verification.md`.

Five deploy defects, and the pattern is what matters: **a green build proves nothing about a running
SSR app.** Builds 1–4 all reported SUCCEED or failed for reasons that misdescribed themselves.

1. **`serverExternalPackages` made the AWS SDK absent at runtime.** Every page returned 500 with a
   *valid* session — sign-in worked, then the first DynamoDB import failed with
   `Cannot find module '@aws-sdk/client-dynamodb-3e32f4e24bb075d4'`, naming a module nobody published
   (Turbopack appends a content hash to an external's name). Marking a package external emits a bare
   `require` and omits it from the bundle, and Amplify's SSR bundle ships only what the trace includes.
   It was protecting nothing: no chunk under `.next/static/` references the SDK, because `lib/cases.ts`
   and `lib/decide.ts` are imported only from server components and a route handler.
2. **Amplify environment variables never reach the SSR runtime** — documented as intentional. Without a
   bridge every read is `undefined` after a green deploy. `amplify.yml` writes them to
   `.env.production` before `next build`, verified end to end.
3. **`update_app(environmentVariables=...)` is a full replace, and the console writes its own keys into
   the same map.** Sending a map built from a stale read deleted `AMPLIFY_MONOREPO_APP_ROOT`,
   `AMPLIFY_DIFF_DEPLOY`, and the Node-22 pin; the next build died at clone time with
   `Cannot read 'next' version in package.json` — which reads like a packaging problem, not a deleted
   variable. **Read fresh and merge**, and preserve every `AMPLIFY_*` / `_LIVE_UPDATES` key you do not
   own.
4. **A monorepo app root and a flat buildspec are mutually exclusive.** Without
   `AMPLIFY_MONOREPO_APP_ROOT` the build cannot find Next; with it, a top-level `frontend:` spec is
   rejected as `Monorepo spec provided without "applications" key`. Both fail at clone time. `amplify.yml`
   is committed in the `applications:`/`appRoot: web` form, where `baseDirectory` is `.next` — the
   `web/` prefix belongs only to the single-app form.
5. **Amplify rejects any variable starting with the reserved `AWS` prefix** (`AWS_REGION` included), and
   two keys entered by hand carried whitespace (`GRACE_TABLE_NAME  `, `GRACE_ESCALATION _INDEX`), which
   read as absent to an exact-key lookup. `readEnv` trims *values* but cannot trim a malformed *key*.

**`create_branch` before the repository is connected blocks the connection permanently** — measured:
`Cannot connect your app to repository while manually deployed branch still exists. Please delete all
branches and try again.` And `except BadRequestException` around `create_branch` swallows a *rejected
compute role*, which all three of `CreateApp`/`CreateBranch`/`UpdateApp` report with that same code —
so a script could report success with no credentials in the SSR runtime. Match on the message, not the
code.

### What Task 8 established — follow these

**A caseworker's approval is an input to the gate, never a bypass — and the four load-bearing facts
sit in four different files.** Say all four or none: (1) `web/` contains no resume vocabulary, so no
paused graph is ever resumed with a truthy response; (2) the approval is recorded as a durable
`DECISION#` row and the runtime is **re-invoked**, so the gate re-evaluates *case facts*; (3)
`evaluate` has no parameter an approval could occupy; (4) `caseworker_approved` reaches only
`_escalate`'s wording. Drop any one and the claim becomes "we told it not to", which is what every
other agent says.

**The decision row is written *before* the invocation, and that inverts `action.py`'s ordering on
purpose.** `web/lib/decide.ts` issues `PutItemCommand` (line ~118) then `InvokeAgentRuntimeCommand`
(line ~173), so a runtime failure still leaves a durable record that a human decided. `action.py` is
the opposite — a ledger row must never claim an action that did not happen (hard rule 6). **The rule
that reconciles them: write the row first when the row records a *decision*, write it after when the row
records an *effect*.** A human's decision is a fact the moment they make it; a filing is only a fact
once the tool confirms it.

**Middleware checks *presence*; the route and every page verify the *token*.** `web/proxy.ts` only asks
whether the cookie exists — it runs on the edge and its own docstring says it is "never the security
boundary". `verifySession` refuses independently on all three pages and on the decide route, which is
why a forged cookie 307s to `/login` and the write route answers 401 rather than either one trusting
the redirect. Never move an authorisation decision into `proxy.ts`, and never remove `verifySession`
from a page because middleware "already covers it".

**The queue must be de-duplicated by case, because every sweep appends a fresh escalation row.** The
GSI held 19 rows for three households before Task 8 and 20 after; `/queue` renders exactly `c-010
c-011 c-012` either way. **The distinct case set is the claim; the row count is not.** A reader that
rendered rows would show the same family three times and make a caseworker think the backlog was
growing. Also: `listQueue`'s `filed` is `false` by construction — the GSI projects escalation rows
only, so it cannot see a `renewal_submitted` row.

**The end-to-end safety claim is executed, not argued: approving `c-010` filed nothing.** A real
caseworker session POSTed `{"decision":"approve"}` to the deployed route for the household missing
`proof_of_residency`; the response said `filed: false`, and DynamoDB confirms `renewal_submitted` for
`c-010` is still **0** while two `DECISION#` rows exist. **Confirm this from the store, never from the
route's own response** — hard rule 6 applies to a verification step as much as to the agent.

**`c-011`'s approval in the plan's Step 2 was deliberately not run, and skipping it was the right
call.** The plan invites approving `c-011` too, noting a filing there would be legitimate. But `c-011`
is one of the three households whose *un-filed* state is the demo's central evidence, and a filing
consumes that irreversibly to re-confirm a property `c-010` already proves. **When a plan step would
spend the demo's own evidence on something already established, report instead of executing** — and say
in the artifact that it was skipped and why, so the gap is visible rather than looking like an oversight.

**A refusal message is not a refusal — check the store.** After the `c-010` approval, a second decision
on the same case returned **409 `already_decided`**, and the table still held exactly **2** `DECISION#`
rows and **0** `renewal_submitted` rows, so the second attempt wrote nothing and invoked nothing. This
check is not ceremony: Grace's own outcome row shares the `DECISION#` prefix, so under a naive prefix
test it would make `alreadyDecided` true and the *first* human decision would refuse itself as a
duplicate. `readCase` discriminates on the presence of a `decision` attribute, which is what makes the
first decision succeed and the second fail.

**Measure the baseline before a write, or the delta is unattributable.** The table was scanned *first*
(651 rows, **zero** `DECISION#` rows), then again after (663 rows). All twelve new rows carry an `sk`
timestamp inside the probe window and all are on `c-010`: 2 decision, 9 ledger, 1 escalation — and the
9 ledger rows end in `family_message_sent`, never `renewal_submitted`. Row counts have now moved
633 → 643 → 651 → 663 in two days, so **never write a row count into documentation as a constant** —
write the invariants instead: `renewal_submitted` exists for exactly `c-001`–`c-009` and for none of
`c-010`/`c-011`/`c-012`.

**A PII scanner that matches nothing reports `NONE` too.** The table-wide scan (663 rows) and the
deployed-markup scan (~151 KB across five responses) both returned `NONE` for all twelve surnames,
`+1555`, and `@`. That is only meaningful because `web/__tests__` feeds a name in through `reason` —
the exact path that reached CloudWatch in Plan 2 — and asserts the scanner catches it.

**The Cognito hosted UI is styled, and the check that it *is* styled is not the obvious one.**
`infra/provision_cognito.py`'s `HOSTED_UI_CSS` carries the dashboard's palette from
`web/app/globals.css`, applied by `set_ui_customization` on every `provision` run (idempotent by
nature — it replaces whatever CSS is there). A test asserts all six colours and the four
`*-customizable` classes, and it was watched failing on a changed hex value; without it, a colour
changed in one place and not the other is invisible until someone opens both pages.

**A managed login v2 settings document made only of colours produces a page that reads as
unstyled, and the branding call returns 200 either way.** `pageBackground.image` and `form.logo`
both default to `enabled: False` in Cognito's own document, so colours alone left `#FAF9F7` behind
a white card — a 2% step, no wordmark, and a heading Cognito wrote. Three images now ship
(`branding_assets()`): a `PAGE_BACKGROUND` that is Grace's own case table ghosted back to a texture
— twelve rows, nine `muted` pills and three `escalate`, the demo's split drawn at ~9–18% opacity —
a `FORM_LOGO` carrying the dashboard masthead's exact lockup, and a `FAVICON_SVG`. All three are
generated from `PALETTE`, so the test that reads `web/app/globals.css` covers them too, and
`test_an_uploaded_image_is_also_switched_on` checks the asset and its `enabled` flag **as a pair** —
an asset stored with its switch off is the same silent failure as a six-digit colour.

**Three constraints on managed login assets, each measured off a rejection rather than the docs.**
The SVG sanitiser refuses `role` and `aria-label` on the root element (`element [svg#role] is not
allowed`) — so these SVGs carry no ARIA, which is why `PAGE_BACKGROUND` is decorative and the logo's
name is also the form's own heading. A `FORM_LOGO` must be between **1:1 and 4:1**: a 360×54 lockup
came back `Invalid file dimension`, and 360×96 was accepted. And `Assets` must be sent on the
**converge** path as well as the create — `branding_settings()` switches both components on
unconditionally, so an update that sent only `Settings` would leave the page asking for images that
were never uploaded, which is worse than the flat page it replaced. Verified served, not merely
stored: all three come back **200 and byte-identical** to what the module generates, and the theme
stylesheet's `--cognito-page-background-image` names the light-mode asset.

**Verifying it needs the linked stylesheet, not the HTML.** Grepping the sign-in page for the hex codes
finds **zero** matches and looks like a failure — the values only ever appear in
`https://d3oia8etllorh5.cloudfront.net/<pool>/<client>/<cssVersion>/assets/CSS/custom-css.css`, which
the page links third after bootstrap and Cognito's own stylesheet. Fetch that file. This is the classic
hosted UI (`ManagedLoginVersion: 1`); managed login v2 would allow real layout control but changes the
sign-in URL shape, so `hostedUiUrl`'s `/login` path and the whole OAuth round trip would need
re-testing for a cosmetic gain.

**Case intake exists as of Plan 4, and the shape of it is the point.** `/new` writes a `RECORD#v1` row
and `DynamoDBCaseStore` reads records from the table, so a submitted household is genuinely seen by the
agent. Before Plan 4, `build_store()` passed `load_fixture_cases()` into the store — records were baked
into the container image, so a form writing to DynamoDB would have rendered on the dashboard and been
**invisible to the sweep**. If you touch the store, keep that property: a case the dashboard shows and
the agent cannot see is the exact failure three plans were spent eliminating.

**Writing that code was not the same as shipping it, and for three days it was not shipped.** Plan 4
landed the store change on 2026-09-07; the deployed image was version 2 from 2026-09-03, and the
EventBridge target still named twelve ids. So the property above was true of the repository and false
of the running system — the honest phrasing while that was so is "the store reads records" rather than
"a submitted household is seen by the agent". Both are true now (runtime version 3, `ListCases` over
the directory), verified end to end on the deployed system. **The general form: a store change, a
container image, and an orchestrator's input list are three separate places a caseload lives, and a
claim about "the agent" is only as deployed as the last of them.**

**The intake form collects no household identity, and `web/lib/intake.ts` enforces it.** No name, phone,
address, or email — the validator refuses any identity-shaped field *and* any unrecognised field, which
is an allowlist rather than a denylist so it cannot be walked around with a field name nobody
anticipated. **Do not add an identity field to make the form feel complete**: `read_case` returning
`display_name` is how a surname reached CloudWatch, and the fix there was removing the field rather
than filtering its consumers.

**A new case's status is `new`, not `error`.** `error`'s message reads "Grace's last run on this case
reached no outcome — re-run the sweep", which is a false claim about a run that never happened. Adding
the variant made three `switch` statements in `case-table.tsx` fail to compile, which is those switches
working as designed: they carry no `default:` precisely so a new status is a compile error rather than
a silent inheritance of `acted`'s green.

**Sign-in is at `auth.rosettacloud.app`** — managed login v2 on Grace's own domain, ACM certificate in
`us-east-1` (CloudFront is global and reads certificates only from there, regardless of the pool's
region). A hand-built Next.js login page was considered and rejected: it would post the caseworker's
password to a Grace route handler, moving the credential through Grace's servers and turning a page
into a password-guessing surface. **Prefer the option that does not enlarge the credential surface.**
The `grace-caseworkers` prefix domain is kept as a working fallback, which is why `HOSTED_UI_CSS` is
still load-bearing rather than dead code.
