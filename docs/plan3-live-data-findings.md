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
  the API and permissions are proven. No Grace pool yet.
- **Amplify** (Task 7): `list-apps` returns `[]` — reachable, and genuinely new ground here.
- Caller identity is currently the account **root** principal. CLAUDE.md prefers the `grace-dev` IAM
  user; provisioning ran as root in Plan 2 as well, so this is a consistency note, not a blocker.
