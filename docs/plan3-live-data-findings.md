# Plan 3 — what the live `grace-cases` table actually contains

Measured on 2026-09-04 against the real table in `us-east-1`, before Task 3 was written, so
`lib/cases.ts` is built against reality rather than against the plan's fixtures. Every number here
came from a query, not from a guess.

---

## The table

`grace-cases`, 633 items. Keys `pk` (HASH) / `sk` (RANGE). One GSI, `escalation-queue`, keyed
`status` (HASH) / `escalated_at` (RANGE), `ProjectionType: ALL`.

Two `sk` prefixes exist today — `LEDGER#` (616 rows) and `ESCALATION#` (17). **No `DECISION#` row
exists yet**, so Task 5 introduces that prefix rather than joining one.

## Five things the plan's fixtures get wrong about real rows

1. **Timestamps are `+00:00`, never `Z`.** Real values look like
   `2026-09-03T14:17:01.231621+00:00` — `datetime.isoformat()` output, offset-suffixed and
   microsecond-precision. The plan's test fixtures use `2026-09-03T00:00:00Z`. Both are valid
   ISO 8601 and `new Date()` parses either, but **a parser that slices or regex-matches a trailing
   `Z` will silently fail on every real row**. Compare as `Date` values or as normalized strings,
   never by string-matching the suffix.

2. **`escalated_at` and `sk` carry the same value**, so `ESCALATION#<escalated_at>` is the sort key.
   The GSI range key is therefore escalation *time*, which is **not** the ordering the queue page
   wants — see 4.

3. **`d_trace_id` is DynamoDB type `NULL`, not a missing attribute and not an empty string.** The
   attribute is present on essentially every ledger row with value `{"NULL": true}`. A reader doing
   `item.d_trace_id?.S` gets `undefined` and must not treat that as "field absent"; a reader that
   assumes `.S` exists on every attribute will crash. The only two value types in the whole table are
   `S` and `NULL`. This is the deliberate consequence of Runtime not installing an in-process tracer
   provider (see CLAUDE.md) — it is honest, not broken, and the dashboard must render it as "not
   traced" rather than as an error.

4. **The GSI holds 17 rows for 3 households, and dedup must be newest-wins.** Counts are `c-010`: 6,
   `c-012`: 6, `c-011`: 5 — every sweep appends a fresh `ESCALATION#` row. A queue that lists rows
   as-returned shows the same family six times, which is a caseworker deciding the same case six
   times. `LastEvaluatedKey` was absent at 17 rows, but this table grows by 3 rows per daily sweep
   and a Query caps at 1MB, so **the reader must still paginate** — the same reasoning Plan 2 applied
   to `ledger()`.

5. **Real deadlines are not the plan's.** `c-010` → `2026-10-18`, `c-011` → `2026-10-22`,
   `c-012` → `2026-10-12`. The plan's ordering test asserts `["c-011", "c-012"]` from invented
   deadlines of `2026-10-05` and `2026-10-12`. With the real values, soonest-deadline-first is
   `c-012` (10-12), `c-010` (10-18), `c-011` (10-22) — the **reverse** of escalation-time order for
   the two that matter. That is what makes the ordering assertion worth having: it distinguishes
   "sorted by deadline" from "whatever the GSI returned".

## The finding that changes a requirement: a household name is still in the table

A scan of all 633 rows for every fixture surname found **`Mensah` in exactly 2 rows**, both for
`c-012`, both written before the `read_case` fix:

| Row | Field |
|---|---|
| `CASE#c-012` / `ESCALATION#2026-09-03T04:20:03.568119+00:00` | `reason` **and** `question` |
| `CASE#c-012` / `LEDGER#2026-09-03T04:20:01.414192+00:00#000002` | `d_question` |

The text is the referee's deliberation prose: *"Does the household size discrepancy allow the Mensah
Household to still qualify for SNAP benefits despite the income level of $1100?"* — exactly the chain
CLAUDE.md's PII finding documents (`read_case` → referee prose → `_deliberation_note` → escalation
reason), captured in DynamoDB as well as in CloudWatch.

**This was already known to be true of CloudWatch and is now confirmed of the durable store.** The
source is fixed and deployed (runtime version 2, `arn:aws:bedrock-agentcore:us-east-1:339712964409:runtime/grace_grace-oTyyvo8stE`),
so no new row can carry a name, and the fixtures are synthetic throughout — no real person is
involved. But it has two concrete consequences for Plan 3:

1. **Newest-wins dedup happens to hide it on `/queue`.** The newest escalation row for each of
   `c-010`, `c-011`, `c-012` is post-fix and carries no name — verified. So the queue page was clean
   *as a side effect* of a rule chosen for a different reason.
2. **`/case/[id]` renders the full ledger, so it would have displayed the name.** That page reads
   every `LEDGER#` row including the pre-fix one. Depending on a dedup rule for a PII property is
   exactly the "scrub each consumer" posture CLAUDE.md rejects in favour of capability absence.

### Resolved: the three values were stripped on 2026-09-04

On the maintainer's explicit instruction, the surname was removed from the three fields rather than
filtered at render. The rows themselves are kept — they are real evidence of a real sweep — and only
the identity phrase changed, `the Mensah Household` → `this household`, so the referee's argument
still reads as it did.

What was and was not touched, because the table *is* the demo's evidence:

- Both rows backed up verbatim to `/tmp/grace-c012-prefix-rows-backup.json` before any write.
- `UpdateItem` with `ConditionExpression="attribute_exists(pk)"`, setting only the named fields. No
  key attribute, no `status`, no `escalated_at`, no `deadline`, and no `renewal_submitted` row was
  modified. `pk`/`sk` are unchanged, so nothing moved in the GSI.
- The substitution asserted both that it changed something and that no `Mensah` survived, so a silent
  no-op could not report success.

Verified after: a scan of all **633** rows for every fixture surname and for `+1555` returns
**clean**; the escalation GSI still holds **17** rows across `c-010` (6), `c-012` (6), `c-011` (5);
`c-012`'s row still reads `PENDING_CASEWORKER` with deadline `2026-10-12`. The 9/3 counts are
untouched because they are derived from `renewal_submitted` rows and the GSI, neither of which this
edit reached.

Task 8's verification should re-run the scan and report the count. **Pre-fix CloudWatch log events
still contain the name and cannot be unwritten** — they age out with retention. So the honest claim is
now: fixed at the source, fixed in the running system, and fixed in durable storage; historical log
events remain.

## Reproducing the scan

```bash
.venv/bin/python - <<'PY'
import boto3, json, pathlib, yaml
ddb = boto3.client("dynamodb", region_name="us-east-1")
fx = yaml.safe_load(pathlib.Path("fixtures/households.yaml").read_text())
cases = fx["cases"] if isinstance(fx, dict) and "cases" in fx else fx
names = [c["household"]["display_name"] for c in
         (cases if isinstance(cases, list) else cases.values())]
hits = {}
for page in ddb.get_paginator("scan").paginate(TableName="grace-cases"):
    for item in page["Items"]:
        blob = json.dumps(item)
        for n in names:
            for tok in n.split():
                if len(tok) > 3 and tok != "Household" and tok in blob:
                    hits.setdefault(tok, []).append((item["pk"]["S"], item["sk"]["S"]))
print({k: len(v) for k, v in hits.items()} or "clean")
PY
```

## Access confirmed for later tasks

- **Cognito** (Task 4): `list-user-pools` works; two unrelated pools already exist in the account, so
  the API and permissions are proven. No Grace pool yet. See below for what was probed.
- **Amplify** (Task 7): `list-apps` returns `[]` — reachable, and genuinely new ground here.
- Caller identity is currently the account **root** principal. CLAUDE.md prefers the `grace-dev` IAM
  user; provisioning ran as root in Plan 2 as well, so this is a consistency note, not a blocker.

---

## Cognito, probed rather than assumed (Task 4)

Three throwaway pools were created and deleted on 2026-09-04 to check Task 4's draft before writing
it. All three were confirmed gone afterwards — `list_user_pools` shows only the two unrelated
projects. Five assumptions held and one gap was found.

| Probed | Result |
|---|---|
| `Schema` entry named `role` | Round-trips as **`custom:role`** in `SchemaAttributes`. The `custom:` prefix is Cognito's; do not write it into the schema name. |
| `ReadAttributes: ["email", "custom:role"]` | Accepted and echoed back verbatim, so the claim reaches the ID token. |
| `ReadAttributes` omitted entirely | Echoes back **empty**, which per the API docs means standard attributes only — `custom:role` would be unreadable and `verifySession` would refuse every legitimate caseworker. This is the defect already fixed in commit `0e07f04`, now confirmed against the live API rather than from the docs alone. |
| `admin_create_user` with an immutable custom attribute | Accepted; `custom:role` is set and `sub` is a UUID. **The user lands in `FORCE_CHANGE_PASSWORD` and cannot sign in.** |
| `admin_set_user_password(Permanent=True)` | Moves the user to **`CONFIRMED`**. So that call is required for an unattended demo, not a convenience — without it the seeded account exists and cannot be used. |
| Re-creating the same user / same domain | `UsernameExistsException`; `InvalidParameterException` with message `Domain already exists.` Both are already in the draft's `except` clauses. |

### The gap: `WriteAttributes`

Cognito **accepts `custom:role` in `WriteAttributes` even though the schema marks it
`Mutable: False`** — verified, the client was created and echoed the attribute back. Granting it would
let a signed-in caseworker call `UpdateUserAttributes` against the very claim that authorises them.
The immutable flag would still refuse the write, but that leaves one guard where there should be two.

Omitting `WriteAttributes` yields an empty list, which is the safe state, and nothing legitimate needs
it: the role is set once by `admin_create_user`, an admin API that this list does not constrain. So the
absence is deliberate and is now commented as such in the plan — the client cannot rewrite the claim
that authorises it, because it was never granted the ability to try. Same reasoning as layer 1 of the
escalation boundary.

### The ID token, minted and read rather than assumed

A fourth throwaway pool was created on 2026-09-04 with `ALLOW_ADMIN_USER_PASSWORD_AUTH` so a **real
Cognito ID token** could be minted without a browser and its claims read. Deleted afterwards. Every
claim `verifySession` depends on is present and has the value the draft expects:

```text
custom:role   caseworker      ← reaches the ID token with ReadAttributes set
token_use     id
aud           <client id>     ← equals the client id exactly
iss           https://cognito-idp.us-east-1.amazonaws.com/<pool>
sub           34881488-d081-70d2-61d6-fca70fe529b4   ← opaque UUID, hard rule 9
email         absent          ← nothing to drop, because nothing is sent
JWKS          .well-known/jwks.json → 2 keys, both RS256
```

**The access token is a genuinely different shape**, which is what makes the `token_use` check worth
having rather than defensive: `token_use: "access"`, **no `custom:role`, and no `aud` claim at all**.
So an access token presented as an ID token would authenticate a session with no authorisation basis —
and `jwtVerify`'s own `audience` check would also refuse it, giving two independent barriers.

### `jose` refuses all nine attacks, verified against 6.2.10

Every refusal the plan asserts was executed against the installed `jose` rather than trusted. Both
key-resolution paths were tested — `createLocalJWKSet` and the plan's hand-rolled resolver — because a
custom resolver is a different code path and could plausibly have bypassed the algorithm allowlist:

| Attack | Result |
|---|---|
| Wrong signing key | `ERR_JWS_SIGNATURE_VERIFICATION_FAILED` |
| Expired, via `currentDate` | `ERR_JWT_EXPIRED` |
| Wrong issuer / wrong audience | `ERR_JWT_CLAIM_VALIDATION_FAILED` |
| `alg: "none"` — the classic bypass | `ERR_JOSE_ALG_NOT_ALLOWED` |
| HS256 algorithm confusion, signing with the public key's `n` as an HMAC secret | `ERR_JOSE_ALG_NOT_ALLOWED` |
| Not a JWT at all | `ERR_JWS_INVALID` |
| `currentDate` honoured in the *other* direction (a valid token, verified at a later date) | `ERR_JWT_EXPIRED` |

`alg: none` and HS256 confusion also fail identically through the custom resolver, so the
`algorithms: ["RS256"]` allowlist is enforced by `jwtVerify` before any key is fetched.

One resolver quirk, benign: the `?? parsed.keys[0]` fallback means an **unknown `kid` still verifies**
if the signature is genuinely from the injected key. It refuses a wrong key regardless, so it cannot
accept a forgery — it is a test convenience, not a hole.

### The defect: `COGNITO_TEST_JWKS` had no environment gate

The draft read `process.env.COGNITO_TEST_JWKS` from production code. That variable **replaces the trust
anchor** — setting it on the deployed app substitutes an attacker-supplied key set for Cognito's
published one, and every forged token then verifies with a valid signature. Nothing else in the request
path would notice: the session looks correctly authenticated because, against that key set, it is.

Fixed to `process.env.NODE_ENV === "test" ? process.env.COGNITO_TEST_JWKS : undefined`. Verified that
vitest sets `NODE_ENV="test"` (and `VITEST="true"`), so one condition is enough, and a thirteenth test
was added that flips `NODE_ENV` to `"production"`, re-imports the module, and asserts the *same* token
stops verifying. It resets modules on both sides because `cachedKeys` is module-level and a resolver
cached under one environment would otherwise answer for the other. Task 4's sabotage step now includes
removing the guard and watching that test fail.

---

## Amplify: the plan was missing the one thing that makes SSR work (Task 7)

`CreateApp`'s `platform` enum is confirmed `['WEB', 'WEB_DYNAMIC', 'WEB_COMPUTE']` from the live API
model, so the plan's platform assertion is sound.

**The gap: `computeRoleArn` is a real parameter on both `CreateApp` and `CreateBranch`, and Task 7's
draft sets neither.** Amplify's SSR compute functions take their AWS credentials from that role and
from nothing else. With no role attached, the SSR runtime has no credentials, so every DynamoDB read in
`lib/cases.ts` and the `invoke_agent_runtime` call in `lib/decide.ts` fail with AccessDenied — **after
a green build and a successful deploy.** The app serves, the pages render their empty states, and the
build log says nothing. Same class as choosing the wrong platform: wrong rather than broken, which is
the harder kind to notice.

Confirmed from the Amplify documentation:

- The role needs a **custom trust policy with `amplify.amazonaws.com`** as the service principal, and
  attaching a role whose trust relationship is wrong is **refused with an error** — so this is
  verifiable at provision time rather than at request time.
- Credentials are available in the SSR runtime immediately, with no redeploy needed to change the role.
- App-level attachment is the default; the docs recommend branch-level *only* when a public repo uses
  auto-branch creation or PR previews. Grace's app has one branch and neither feature enabled, so
  app-level is the simpler correct choice — and keeping those two features off is what keeps it correct.

Least privilege for what the dashboard actually does, with the real ARNs:

```text
dynamodb:Query, dynamodb:GetItem
  arn:aws:dynamodb:us-east-1:339712964409:table/grace-cases
  arn:aws:dynamodb:us-east-1:339712964409:table/grace-cases/index/escalation-queue
dynamodb:PutItem
  arn:aws:dynamodb:us-east-1:339712964409:table/grace-cases
bedrock-agentcore:InvokeAgentRuntime
  arn:aws:bedrock-agentcore:us-east-1:339712964409:runtime/grace_grace-oTyyvo8stE
```

**The index needs its own ARN.** A policy granting `Query` on the table alone denies the GSI query
while the table query succeeds — so `/queue` would break and `/case/[id]` would work, which reads like
a bug in the queue page rather than a missing permission.

**No `Scan` and no `DeleteItem`.** `lib/cases.ts` queries rather than scans, and granting `Scan` would
let a bug read every ledger row in the table. Nothing in the app deletes, and a dashboard that can
delete a ledger row can destroy the audit trail the entire project rests on.
