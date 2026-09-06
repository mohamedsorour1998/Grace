# Plan 4 — a sign-in page on Grace's own domain, and real case intake

Two changes the demo needs, planned together because they share one property: **both look like small UI
work and are not.** The first moves an identity boundary; the second moves where case records live.

- **Task 1** replaces the `amazoncognito.com` hosted UI with **managed login v2 on
  `auth.rosettacloud.app`** — Grace's own domain, real branding control, and the password still never
  touches Grace's servers.
- **Task 2** moves case records from the container image into DynamoDB so a **submitted case is
  actually seen by the deployed agent**, then adds the intake form.

Written 2026-09-06. Submission deadline **2026-09-14 17:00 PT** — 8 days. Task 1 is low risk and
independently shippable. Task 2 touches Plan 1 code and needs a runtime redeploy.

---

## Preflight — what was measured before writing this

| Probe | Result |
|---|---|
| `dig rosettacloud.app A` | `3.175.86.72`, `.87`, `.74` — **the parent domain resolves**, which is a hard prerequisite for a Cognito custom domain |
| `auth.rosettacloud.app` in Route 53 | **free**, zone `Z08385903PVEGWMREU7F7` |
| Existing ACM cert in `us-east-1` | covers `rosettacloud.app` + `www.rosettacloud.app` only — **does not cover `auth.`**, so a new certificate is required |
| Current pool tier | `ESSENTIALS` — managed login v2 is available (Lite would not be) |
| Current domain | prefix `grace-caseworkers`, `ManagedLoginVersion: 1` (classic hosted UI) |
| How the deployed agent loads cases | `DynamoDBCaseStore(cases)` where `cases = load_fixture_cases()` — **case records are baked into the container image**; DynamoDB holds only the ledger |

**That last row is the whole reason Task 2 is not a form.** A dashboard that writes a new case to
DynamoDB today would show it on screen and the agent would never see it — the exact "looks like it
works" failure this project has spent three plans eliminating.

---

## Task 1 — managed login v2 on `auth.rosettacloud.app`

**Why this rather than a custom sign-in page in Next.js.** A hand-built login page would mean posting
the password to a Grace route handler and calling `InitiateAuth` server-side. That works, but it moves
the password through Grace's servers, turns the route into a password-guessing surface, and puts a
credential path inside the app that `verifySession` was designed to keep outside it. A custom **domain**
gets the same result — a sign-in page that looks like Grace's — while leaving the credential exchange
entirely with Cognito. **Prefer the option that does not enlarge the credential surface.**

**Files:** `infra/provision_cognito.py` (extend), `web/lib/cognito.ts` (URL builder only)

- [ ] **Step 1: Request the certificate**

ACM in **`us-east-1`** specifically — the certificate is attached to a CloudFront distribution, which
is global, and Cognito rejects a certificate from any other region even though the pool is in
`us-east-1` anyway.

```bash
aws acm request-certificate --region us-east-1 \
  --domain-name auth.rosettacloud.app \
  --validation-method DNS \
  --query 'CertificateArn' --output text
```

Then add the CNAME it asks for to zone `Z08385903PVEGWMREU7F7` and wait for `ISSUED`. Do not proceed
on `PENDING_VALIDATION` — `CreateUserPoolDomain` fails on an unissued certificate, and the error names
the certificate rather than the validation state.

- [ ] **Step 2: Create the custom domain with branding version 2**

```python
client.create_user_pool_domain(
    Domain="auth.rosettacloud.app",
    UserPoolId=POOL_ID,
    ManagedLoginVersion=2,          # 1 is the classic hosted UI; 2 is managed login
    CustomDomainConfig={"CertificateArn": CERT_ARN},
)
```

**Keep the existing `grace-caseworkers` prefix domain.** A pool may hold both, and keeping it means a
problem with the custom domain does not leave the demo with no working sign-in. One documented
consequence to be aware of and to *not* be alarmed by: with both present, Cognito serves
`/.well-known/openid-configuration` only for the custom domain. **This does not affect
`verifySession`** — the JWKS it fetches lives at
`https://cognito-idp.us-east-1.amazonaws.com/<pool>/.well-known/jwks.json`, on the API host, not on
either domain. Verify that claim rather than trusting this sentence.

- [ ] **Step 3: Point DNS at the alias target**

`create_user_pool_domain` returns a CloudFront alias target. Add an **A record, alias type**, for
`auth.rosettacloud.app` in the hosted zone pointing at it (hosted zone id for CloudFront aliases is the
fixed `Z2FDTNDATAQYW2`).

**A new custom domain takes up to an hour to propagate.** Budget for that; do not read an early
failure as a misconfiguration.

- [ ] **Step 4: Brand it**

Managed login v2 has a real branding editor and a `CreateManagedLoginBranding` API. Apply Grace's
palette — the same six values `HOSTED_UI_CSS` already carries, read from `web/app/globals.css`:
paper `#FAF9F7`, ink `#1C1F23`, muted `#6B7280`, rule `#E5E3DF`, escalate `#B4530A`, error `#9B2C2C`.

Unlike v1's CSS classes, v2 takes a settings document plus base64 image assets. **Do not delete
`HOSTED_UI_CSS`** — the prefix domain still serves v1 and remains the fallback.

- [ ] **Step 5: Update the URL builder, having verified its shape**

`web/lib/cognito.ts`'s `hostedUiUrl` builds `${domain}/login?...`. AWS's own custom-domain example uses
`/login` on a custom domain, so this may need no change at all — **but verify against the live domain
before assuming.** If v2 wants a different path, change it here and re-test the whole round trip.

The only other change is the `COGNITO_DOMAIN` environment variable on the Amplify app:
`https://auth.rosettacloud.app`. Read fresh and merge (Plan 3's finding — `update_app` is a full
replace and the console writes its own keys into that map).

- [ ] **Step 6: Add the callback URL, then verify end to end**

`UpdateUserPoolClient` is a **full replace** — resend every field. Add
`https://auth.rosettacloud.app/...` nowhere; the callback is still Grace's app. What changes is only
where the *sign-in page* lives.

Verify, and assert on rendered content rather than on HTTP 200:

```bash
curl -sI https://auth.rosettacloud.app/login?client_id=...   # 200, and served by the custom domain
# then the real round trip in a browser, signing in as caseworker-01
```

Confirm afterwards that a session minted through the new domain still passes `verifySession` — the
`iss` claim is the pool's API URL and does not change with the domain, so it should, but that is a
claim to check rather than assert.

- [ ] **Step 7: Tests, then commit**

Pin what a test can pin: the domain constant, `ManagedLoginVersion: 2`, the certificate's region, and
that the branding settings carry all six palette values. Sabotage each and watch it fail.

---

## Task 2 — case records in DynamoDB, and the intake form

**The blocker, restated because it determines the whole design.** `build_store()` calls
`DynamoDBCaseStore(cases)` with `cases` from `load_fixture_cases()`. `get()` and `open_cases()` read
that in-memory dict. So the deployed agent's view of "which households exist" is fixed at image build
time. Intake cannot be a dashboard-only feature.

**Files:** `grace/cases/dynamo_store.py` (extend), `infra/seed_cases.py` (new),
`web/app/api/case/new/route.ts` (new), `web/app/new/page.tsx` (new), `web/lib/intake.ts` (new)

### The one hard rule this feature runs into

**Hard rule 9: no household identity anywhere a model or a log can reach.** An intake form is exactly
where a name, phone number, and address would feel natural to collect — and `read_case` returning
`display_name` is precisely how a surname reached CloudWatch in Plan 2.

**So the form collects no identity at all.** Case id, program, state, certification end date, reported
income, household size, and which documents are on file. Nothing else. If that feels wrong, it is the
same instinct that caused the original leak: Grace does not need to know who the family is in order to
know whether their paperwork is complete. A real deployment's identity lives in the system of record
that referred the household, keyed by case id.

**Hard rule 3 also applies:** everything entered is synthetic. The form should say so on screen.

- [ ] **Step 1: A record row, alongside the ledger**

New sort key under the existing `CASE#<id>` partition, so a case's record and its ledger stay together:

```text
pk  CASE#c-013
sk  RECORD#v1        ← the case record itself
sk  LEDGER#<ts>#<n>  ← what Grace did (unchanged)
sk  ESCALATION#<ts>  ← unchanged
sk  DECISION#<ts>    ← unchanged
```

`RECORD#` is a new prefix. **`ledger()` already filters on the `LEDGER#` prefix** (Plan 2 finding), so
it will not pick these up — verify that rather than assuming it.

- [ ] **Step 2: Seed the twelve existing households**

`infra/seed_cases.py`, idempotent, writing each fixture household as a `RECORD#v1` row. Run it once
before changing the store, so the table is complete before anything reads from it.

- [ ] **Step 3: `DynamoDBCaseStore` reads records from the table**

`open_cases()` and `get()` query the table instead of the constructor dict. Keep the constructor
argument as a **fallback seed** rather than deleting it, so the in-memory store and every existing test
keep working unchanged.

Two things to get right, both learned already in this project:

- **Pagination.** A Query caps at 1MB and signals more with `LastEvaluatedKey`. `open_cases()` must
  page, with a cap that throws rather than truncates — truncation would silently hide households from
  the sweep, which is the failure this whole system exists to prevent.
- **A malformed record must not become a half-populated `Case`.** Parse strictly and raise, in the
  spirit of `InvalidFixtureData`. A case with a missing `cert_end` that defaults to today would file or
  escalate on a date nobody chose.

- [ ] **Step 4: `create_case`, refusing to overwrite**

```python
ConditionExpression="attribute_not_exists(sk)"
```

An intake that silently overwrites an existing household would destroy a case record and its history
would then belong to two different families. Refuse, and let the route return a typed conflict.

- [ ] **Step 5: The intake route, gated exactly like the decide route**

`POST /api/case/new`. Reuse the existing pattern rather than inventing one: `verifySession` first,
then a pure validation function in `web/lib/intake.ts` that returns a permit or a typed refusal, then
the write. Map refusal codes to HTTP status through a `Record<…, number>` so adding a code is a
compile error rather than a silent 400.

Validate: case id shape and uniqueness, program in the known set, state in the known set, a real ISO
date, income a finite non-negative integer in cents, household size a positive integer, and **no field
that could carry identity**. Reject a non-string where a string is expected rather than coercing —
coercion invents data nobody entered.

- [ ] **Step 6: The form**

`/new`, in Grace's design, with a visible note that all data is synthetic. On success, link to the new
case's page. The case will show no ledger until a sweep runs — **say that on screen**, or a judge will
read an empty audit trail as a broken feature.

- [ ] **Step 7: Redeploy the runtime, then prove the agent sees it**

This is the step that makes the feature real rather than cosmetic. Rebuild and deploy the AgentCore
runtime with the new store, then:

1. Submit a household through the form.
2. Invoke the deployed runtime for that case id directly.
3. Confirm from **DynamoDB** — not from the response — that Grace evaluated it and wrote a ledger.

**Then re-derive the demo's headline claim.** Adding a thirteenth household changes "9 handled alone,
3 waiting on you". Every document that quotes 12/9/3 must be re-measured: `README.md`,
`docs/dashboard-verification.md`, `docs/demo-video-handout.md`, `docs/builder-blog-post.md`,
`CLAUDE.md`. **Decide deliberately whether the demo ships with 12 households or 13** — a submitted case
is a better demo, but the 9/3 split is quoted in six places and in the video script.

- [ ] **Step 8: `CASE_IDS` is no longer a constant**

`web/lib/cases.ts` generates `c-001..c-012` from a length. Once cases are in the table, `listCases`
should read the real set — a query for `RECORD#` rows rather than a hardcoded range. Keep the page cap
and keep `Scan` ungranted.

- [ ] **Step 9: Gates, sabotage, commit**

All five gates. Sabotage every new guard and watch the specific test fail — including the
`attribute_not_exists` conflict, the pagination cap, and every identity field the validator rejects.

---

## Risks, stated up front

| Risk | Mitigation |
|---|---|
| Custom domain takes up to an hour to propagate | Keep the prefix domain; it stays a working fallback throughout |
| Managed login v2 changes the sign-in URL shape | Verify `/login` against the live domain before editing `hostedUiUrl`; the round trip is re-tested end to end |
| Task 2 needs a runtime redeploy | The current image is version 2 and working; redeploy only after the store change passes locally |
| A 13th household breaks the 9/3 claim in six documents | Decide the demo's caseload deliberately in Step 7, then re-measure every document rather than editing the number by hand |
| Intake invites collecting identity | The form has no identity fields at all, and the validator rejects them — hard rule 9 enforced by capability absence, not by discipline |

## Out of scope

Editing or deleting an existing case. Grace is not the system of record, and a dashboard that can
rewrite a case record can rewrite the audit trail this project rests on.
