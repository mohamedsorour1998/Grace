# Audit Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the seven findings from the 2026-09-08 final audit, so that every claim Grace's dashboard and sweep make about a household is true of *this run* rather than of history.

**Architecture:** Three of the findings share one root cause — **a per-run claim answered from an all-time record.** A decision is permanent while an escalation recurs every sweep, so the queue never empties; `renewal_filed` reads the whole ledger, so a run that did nothing reports `acted`. Both fixes scope the question to an episode: the newest decision is compared against the newest escalation, and a filing is counted only from the moment this run started. The remaining findings are a latent exhaustiveness gap in the gate, a documented date that measurement contradicts, and two constraints that exist in nobody's test.

**Tech Stack:** Python 3.12 (`strands-agents`, `boto3`, pytest), TypeScript (Next.js 16, vitest 5, `@aws-sdk/client-dynamodb`), DynamoDB, Step Functions, EventBridge.

**Spec:** No separate spec. This plan implements the findings recorded in the audit section of the conversation of 2026-09-08, each of which was reproduced with a runnable check before being written down. The reproduction for each finding is included in its task as the failing test.

## Global Constraints

- **`grace/authority.py` stays pure.** No `strands`, no `boto3`, no file or network I/O. `test_authority_imports_only_pure_siblings` enforces this — Task 3 touches this file and must not add an import.
- **Amazon Nova only** in the request path; model IDs live in `grace/models.py` and are referenced by role.
- **Never put household identity anywhere a model or a log can reach it** — no name, phone, address, or email in a tool's returned text, a span attribute, a ledger row, or an escalation reason. Case ids only.
- **Never claim an action succeeded without tool confirmation.** This plan tightens that rule; no task may loosen it.
- **Escalating is always allowed.** `escalate_to_caseworker` is never gated.
- **Reflection and caseworker approval are advisory only.** They may make Grace *more* cautious, never satisfy a gate condition.
- **All household data is synthetic**; fixture phones use the reserved `+1555` range.
- **Do not delete from or overwrite the live `grace-cases` table.** Tasks 1–5 are local only. Task 6 reads AWS and writes nothing.
- **Five gates must pass before any commit:** `.venv/bin/python -m pytest` from the repo root, and `npm run typecheck`, `npm run lint`, `npm run test`, `npm run build` from `web/`. Lint must produce *clean output*, not merely exit 0.
- **Baseline to hold or grow:** **874 Python tests**, **211 vitest tests across 9 files**. `web/` is additive and must never reduce the Python count.
- **Prove every new guard by sabotage.** Break the line the test exists to protect, watch the named test fail, restore. A test nobody watched fail is not a test. Record the sabotage and the failing test name in the commit message.
- **The demo's two invariants must survive every task:** `renewal_submitted` exists for exactly `c-001`–`c-009`; no household in `c-010`/`c-011`/`c-012` ever has one.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `web/lib/cases.ts` | Add `parseInstant`, add `decidedSinceEscalation` to `CaseDetail`, rewrite `listQueue` to exclude decided cases | 1 |
| `web/lib/types.ts` | Add `decidedSinceEscalation` to the `CaseDetail` interface | 1 |
| `web/__tests__/queue-lifetime.test.ts` | **New.** The decision/escalation lifetime tests | 1 |
| `grace/run.py` | `renewal_filed` gains `since`; `sweep` passes its per-case run start | 2 |
| `grace/entrypoint.py` | `process_case` captures a run start and passes it | 2 |
| `tests/test_run_scoped_filing.py` | **New.** A stale filing must not report `acted` | 2 |
| `grace/authority.py` | Exhaustive dispatch over `document_problems` codes | 3 |
| `tests/test_authority.py` | An unknown problem code names itself | 3 |
| `tests/test_demo_dates.py` | **New.** Pin the measured degradation dates | 4 |
| `CLAUDE.md`, `grace/{graph,entrypoint,run,steering}.py` | Correct the 8/4 claim to the measured 6/6 | 4 |
| `tests/test_recorded_constraints.py` | **New.** Pin repeat filing and record-over-seed precedence | 5 |
| `README.md` | State the repeat-filing constraint | 5 |
| `infra/verify_deployed.py` | **New.** Compare live infrastructure against the provisioners | 6 |
| `tests/test_verify_deployed.py` | **New.** Its unit tests, against fakes | 6 |
| `docs/runbook-deploy.md` | Document `verify_deployed` and the seeding prerequisite | 5, 6 |

---

### Task 1: A decision belongs to an escalation episode, not to a case forever

**The finding.** Nothing anywhere writes anything other than `PENDING_CASEWORKER` — `grep -rn "PENDING_CASEWORKER" grace/ infra/ web/` matches only the two constant definitions. Live, `c-010` has **1 human approval and 16 escalation rows, every one `PENDING_CASEWORKER`**. So `/queue` renders "3 households need a decision" when one was decided days ago, and the write route answers `409 already_decided` if anyone tries. The case is permanently in the queue and permanently undecidable.

**The fix is not to resolve the escalation row.** The next sweep re-escalates `c-010` (the document is still missing) and writes a fresh `PENDING` row, so any status flip is undone within a day. The mismatch is one of *lifetime*: `alreadyDecided` means "a decision has ever existed" while an escalation recurs. Compare the two by instant instead — a decision counts only if it is newer than the newest escalation. A caseworker's decision then removes the case from the queue, and tomorrow's sweep, finding the situation unchanged, puts it back and makes it decidable again. That is the correct behaviour: the family still has no document, and a human should see that it is still outstanding.

**Files:**
- Modify: `web/lib/cases.ts` (add `parseInstant` beside `instant` at :132; add `decidedSinceEscalation` to `readCase`'s return at :419; rewrite `listQueue` at :196-236; change `readFacts` at :448)
- Modify: `web/lib/types.ts` (add one field to `CaseDetail`)
- Test: `web/__tests__/queue-lifetime.test.ts` (new)

**Interfaces:**
- Produces: `parseInstant(value: string): number` — milliseconds, or `Number.NEGATIVE_INFINITY` for anything unparseable.
- Produces: `CaseDetail.decidedSinceEscalation: boolean` — true when the newest human decision is strictly newer than the newest escalation row.
- Consumes: the existing `instant`, `str`, `queryAll`, `readEnv`, `PENDING`, `DECISION`, `ESCALATION` module-locals in `web/lib/cases.ts`.

- [ ] **Step 1: Write the failing tests**

Create `web/__tests__/queue-lifetime.test.ts`:

```typescript
/**
 * A DECISION IS SCOPED TO AN ESCALATION, NOT TO A CASE.
 *
 * Live, `c-010` carried one approval and sixteen `PENDING_CASEWORKER`
 * escalation rows: decided days earlier, still rendered as "needs a decision",
 * and refused as `already_decided` if anyone tried. Resolving the escalation
 * row would not fix it — the next sweep writes a fresh PENDING row, because the
 * document really is still missing. The lifetimes are what mismatch.
 */
import { describe, expect, it, vi } from "vitest";
import type { AttributeValue } from "@aws-sdk/client-dynamodb";

const ENV = {
  AWS_REGION: "us-east-1",
  GRACE_TABLE_NAME: "grace-cases",
  GRACE_ESCALATION_INDEX: "escalation-queue",
  GRACE_RUNTIME_ARN: "arn:aws:bedrock-agentcore:us-east-1:1:runtime/r",
  COGNITO_USER_POOL_ID: "us-east-1_x",
  COGNITO_CLIENT_ID: "c",
  COGNITO_DOMAIN: "https://d",
  DASHBOARD_URL: "https://d",
};

function S(v: string): AttributeValue { return { S: v }; }

/** An escalation row as the sweep writes it. */
function escalation(caseId: string, at: string): Record<string, AttributeValue> {
  return {
    pk: S(`CASE#${caseId}`), sk: S(`ESCALATION#${at}`), case_id: S(caseId),
    status: S("PENDING_CASEWORKER"), escalated_at: S(at),
    deadline: S("2026-10-18"), reason: S("missing_document: proof_of_residency"),
    question: S("missing_document: proof_of_residency"),
  };
}

/** A human decision row, which carries a `decision` attribute. */
function decision(caseId: string, at: string): Record<string, AttributeValue> {
  return {
    pk: S(`CASE#${caseId}`), sk: S(`DECISION#${at}`), case_id: S(caseId),
    decided_at: S(at), decided_by: S("2448a4e8-0000-4000-8000-000000000000"),
    decision: S("approve"), note: S(""),
  };
}

/** A client that answers the GSI query with `queue` and each case partition
 *  with `partitions[caseId]`. Mirrors what `queryAll` actually sends. */
function fakeClient(
  queue: Record<string, AttributeValue>[],
  partitions: Record<string, Record<string, AttributeValue>[]>,
) {
  return {
    send: vi.fn(async (command: { input: Record<string, unknown> }) => {
      const input = command.input;
      if (input.IndexName) return { Items: queue };
      const pk = (input.ExpressionAttributeValues as Record<string, AttributeValue>)[":pk"];
      const id = (pk?.S ?? "").replace("CASE#", "");
      return { Items: partitions[id] ?? [] };
    }),
  };
}

async function load() {
  vi.resetModules();
  for (const [k, v] of Object.entries(ENV)) process.env[k] = v;
  return import("@/lib/cases");
}

describe("a decision is scoped to the escalation it answers", () => {
  it("reports a case decided after its newest escalation as decided", async () => {
    const { readCase } = await load();
    const client = fakeClient([], {
      "c-010": [escalation("c-010", "2026-09-04T04:00:00.000000+00:00"),
                decision("c-010", "2026-09-04T05:00:00.000Z")],
    });
    const detail = await readCase("c-010", client as never);
    expect(detail).not.toBeNull();
    expect(detail!.decidedSinceEscalation).toBe(true);
  });

  it("reports a case re-escalated after its decision as undecided", async () => {
    const { readCase } = await load();
    // The next sweep found the document still missing and escalated again.
    const client = fakeClient([], {
      "c-010": [escalation("c-010", "2026-09-04T04:00:00.000000+00:00"),
                decision("c-010", "2026-09-04T05:00:00.000Z"),
                escalation("c-010", "2026-09-05T09:00:00.000000+00:00")],
    });
    const detail = await readCase("c-010", client as never);
    expect(detail!.decidedSinceEscalation).toBe(false);
  });

  it("reports a case with no decisions as undecided", async () => {
    const { readCase } = await load();
    const client = fakeClient([], {
      "c-011": [escalation("c-011", "2026-09-04T04:00:00.000000+00:00")],
    });
    const detail = await readCase("c-011", client as never);
    expect(detail!.decidedSinceEscalation).toBe(false);
  });

  it("compares instants, not strings, when the stamps differ inside one second", async () => {
    // `Z` (0x5A) sorts above `.` (0x2E), so a string comparison calls the
    // *older* row newer. The fixture must disagree under the two orderings or
    // it cannot tell a correct implementation from a broken one — assert that
    // first, exactly as `lib/cases.ts`'s existing ordering test does.
    const esc = "2026-09-04T05:00:01.500000+00:00";
    const dec = "2026-09-04T05:00:01Z";
    expect(dec > esc).toBe(true);                       // the string ordering
    expect(Date.parse(dec) < Date.parse(esc)).toBe(true); // the real ordering
    const { readCase } = await load();
    const client = fakeClient([], {
      "c-010": [escalation("c-010", esc), decision("c-010", dec)],
    });
    const detail = await readCase("c-010", client as never);
    // The decision is genuinely older, so the case still awaits one.
    expect(detail!.decidedSinceEscalation).toBe(false);
  });
});

describe("the queue shows only households still awaiting a decision", () => {
  it("drops a case whose newest escalation has been decided", async () => {
    const { listQueue } = await load();
    const client = fakeClient(
      [escalation("c-010", "2026-09-04T04:00:00.000000+00:00"),
       escalation("c-011", "2026-09-04T04:00:00.000000+00:00")],
      {
        "c-010": [escalation("c-010", "2026-09-04T04:00:00.000000+00:00"),
                  decision("c-010", "2026-09-04T05:00:00.000Z")],
        "c-011": [escalation("c-011", "2026-09-04T04:00:00.000000+00:00")],
      },
    );
    const queue = await listQueue(client as never);
    expect(queue.map(c => c.caseId)).toEqual(["c-011"]);
  });

  it("keeps a household whose case partition cannot be read", async () => {
    // Dropping an escalated household because a read failed is the silent
    // omission this project exists to prevent. Degrade to the GSI row instead.
    const { listQueue } = await load();
    const client = {
      send: vi.fn(async (command: { input: Record<string, unknown> }) => {
        if (command.input.IndexName) {
          return { Items: [escalation("c-012", "2026-09-04T04:00:00.000000+00:00")] };
        }
        throw new Error("partition unavailable");
      }),
    };
    const queue = await listQueue(client as never);
    expect(queue.map(c => c.caseId)).toEqual(["c-012"]);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npx vitest run __tests__/queue-lifetime.test.ts`
Expected: FAIL. The four `readCase` tests fail on `decidedSinceEscalation` being `undefined`; the first `listQueue` test fails with `["c-010","c-011"]` received.

- [ ] **Step 3: Add `parseInstant` and route `instant` through it**

In `web/lib/cases.ts`, replace the existing `instant` helper at :132-135 with:

```typescript
/** An ISO stamp as milliseconds, or `-Infinity` when it is unparseable.
 *
 *  Separate from `instant` because a decision's `decided_at` arrives as a
 *  `string` off a parsed row while an escalation's arrives as an
 *  `AttributeValue`, and both have to be compared on the same scale. Never
 *  compare these as strings: `Z` (0x5A) sorts above `.` (0x2E), so
 *  `"…T05:00:01Z" > "…T05:00:01.500000+00:00"` is `true` while the offset row
 *  is the *later* instant. Grace writes microsecond `+00:00` stamps from
 *  Python and `Z` stamps from Step Functions, so both spellings are live in
 *  this table. */
function parseInstant(value: string): number {
  const t = Date.parse(value);
  return Number.isFinite(t) ? t : Number.NEGATIVE_INFINITY;
}

function instant(v: AttributeValue | undefined): number {
  return parseInstant(str(v));
}
```

- [ ] **Step 4: Compute `decidedSinceEscalation` in `readCase`**

In `web/lib/cases.ts`, immediately after the `const pending = ...` line at :398, insert:

```typescript
  // **A decision answers one escalation, not the case forever.**
  //
  // Every sweep appends a fresh `PENDING_CASEWORKER` escalation row for a
  // household it still cannot settle, and nothing ever changes that status —
  // so "has this case ever been decided" is permanently true once a caseworker
  // acts, and the household is then stuck in the queue and refused as
  // `already_decided` if anyone tries again. Measured live: `c-010` held one
  // approval and sixteen PENDING rows.
  //
  // Scoping the question to the newest escalation makes both surfaces correct.
  // The caseworker's decision clears the case from `/queue`; the next sweep,
  // finding the document still missing, escalates again and the case becomes
  // decidable again — which is right, because the family's situation has not
  // changed and a human should see that it is still outstanding.
  const newestEscalation = escalation ? instant(escalation.escalated_at) : Number.NEGATIVE_INFINITY;
  const newestDecision = decisions.reduce(
    (max, d) => Math.max(max, parseInstant(d.decidedAt)),
    Number.NEGATIVE_INFINITY,
  );
  // Strictly greater. A decision stamped at the same instant as the escalation
  // it would answer cannot have been made in response to it.
  const decidedSinceEscalation = newestDecision > newestEscalation;
```

Then add `decidedSinceEscalation,` to the object `readCase` returns, beside `record`.

- [ ] **Step 5: Declare the field on `CaseDetail`**

In `web/lib/types.ts`, add to the `CaseDetail` interface:

```typescript
  /** Whether the newest human decision is newer than the newest escalation.
   *  `false` means this household is still waiting on a person — see the note
   *  in `lib/cases.ts` on why a decision is scoped to an escalation episode. */
  decidedSinceEscalation: boolean;
```

- [ ] **Step 6: Rewrite `listQueue` to read each candidate**

Replace the body of `listQueue` in `web/lib/cases.ts` (:196-236) with:

```typescript
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
    if (id === "") continue;
    const seen = newest.get(id);
    if (!seen || instant(row.escalated_at) > instant(seen.escalated_at)) newest.set(id, row);
  }

  // The GSI gives the candidates; only the case partition knows whether a
  // caseworker has already answered. The index projects escalation rows alone,
  // so it can see neither decisions nor `renewal_submitted` — which is also why
  // the summary below now comes from `readCase` rather than from the GSI row:
  // it measures `filed` and `program` instead of asserting them.
  //
  // One extra read per *escalated* household, which is the small set. Reading
  // every case here would make the queue cost the whole caseload.
  const candidates = [...newest.entries()];
  const details = await Promise.all(
    candidates.map(([id]) => readCase(id, client).catch(() => null)),
  );

  const queue: CaseSummary[] = [];
  candidates.forEach(([id, row], index) => {
    const detail = details[index];
    if (detail === null || detail === undefined) {
      // An unreadable partition must not remove a household from the queue.
      // Dropping an escalated family because a read failed is the silent
      // omission this project exists to prevent, so fall back to the GSI row —
      // degraded (no `program`, `filed` unmeasured) but present.
      queue.push({
        caseId: id, status: "escalated", program: "",
        deadline: str(row.deadline), reason: str(row.reason) || null, filed: false,
      });
      return;
    }
    if (detail.decidedSinceEscalation) return;
    queue.push(detail.summary);
  });

  return queue.sort(
    (a, b) => a.deadline.localeCompare(b.deadline) || a.caseId.localeCompare(b.caseId),
  );
}
```

- [ ] **Step 7: Scope `readFacts`'s `alreadyDecided` the same way**

In `web/lib/cases.ts`, change `readFacts`'s return (:454-458) so `alreadyDecided` uses the new field:

```typescript
  return {
    caseId,
    status: detail.summary.status,
    // Scoped to the newest escalation, not to the case's whole history. A
    // household re-escalated after a decision is decidable again, because the
    // sweep found the same problem unresolved and a human should answer for
    // the current escalation rather than be told they already did.
    alreadyDecided: detail.decidedSinceEscalation,
  };
```

- [ ] **Step 8: Run the new tests and the whole web suite**

Run: `cd web && npx vitest run __tests__/queue-lifetime.test.ts`
Expected: PASS, 6 tests.

Run: `cd web && npm run test`
Expected: PASS, **10 files**, at least **217 tests**. If any pre-existing test fails, it is asserting the old lifetime — read it before changing it and record in the commit message which assertion moved and why.

- [ ] **Step 9: Sabotage each new guard and watch it fail**

Run each, confirm the named test fails, then restore the file with `git checkout -- web/lib/cases.ts`:

| Sabotage | Must fail |
|---|---|
| `newestDecision > newestEscalation` → `decisions.length > 0` | `reports a case re-escalated after its decision as undecided` |
| `parseInstant(d.decidedAt)` → `d.decidedAt` compared as a string | `compares instants, not strings, when the stamps differ inside one second` |
| `if (detail.decidedSinceEscalation) return;` deleted | `drops a case whose newest escalation has been decided` |
| the `detail === null` fallback replaced with `return;` | `keeps a household whose case partition cannot be read` |

- [ ] **Step 10: Run the remaining gates and commit**

```bash
cd web && npm run typecheck && npm run lint && npm run build
cd .. && .venv/bin/python -m pytest -q -p no:warnings
git add web/lib/cases.ts web/lib/types.ts web/__tests__/queue-lifetime.test.ts
git commit -m "fix: a decision answers one escalation, not the case forever

Live, c-010 held one caseworker approval and sixteen PENDING_CASEWORKER
escalation rows. Nothing anywhere writes any other status, so /queue said
'3 households need a decision' about a case decided days earlier, and the
write route answered 409 already_decided to anyone who tried. Permanently
queued and permanently undecidable.

Resolving the escalation row would not have fixed it: the next sweep finds
the document still missing and writes a fresh PENDING row. The mismatch is
one of lifetime, so compare instants instead — a decision counts only if it
is newer than the newest escalation. The caseworker's answer clears the
case, tomorrow's sweep re-escalates it, and it becomes decidable again,
which is correct: the family still has no document.

listQueue now reads each candidate's partition rather than rendering GSI
rows, so program and filed are measured rather than asserted. An unreadable
partition keeps the household in the queue on the GSI row — dropping an
escalated family because a read failed is the silent omission this project
exists to prevent.

Four sabotages watched failing: an all-time decision test, a string stamp
comparison, the queue filter removed, and the unreadable-partition fallback
removed."
```

---

### Task 2: A filing is counted from the moment this run started

**The finding.** `renewal_filed` reads the whole ledger, so once a household has ever been filed, every later run reports `acted` regardless of what happened. Reproduced:

```
gate says needs a human: None
renewal_filed (reads the WHOLE ledger): True
-> a run in which Grace did nothing would report: acted, filed=True
```

Live, every clean household carries **12 `renewal_submitted` rows across 12 sweeps** — the agent really is filing each run, and the check cannot tell that from doing nothing. Two consequences. The demo's headline invariant is now permanently satisfied, so a broken gate that filed nothing would leave every check in the repo passing. And `web/lib/decide.ts` reports *"Grace re-checked, the gate cleared the case, and the renewal was filed"* from `status === "acted" && filed === true`, which an old row alone can produce.

**`outreach_sent` is deliberately left alone.** The two functions answer different questions. `renewal_filed` asks *did this run do the thing* and must be run-scoped. `outreach_sent` asks *has this family already been bothered*, and its whole purpose is to stop a caseworker asking twice — scoping it to one run would make Grace forget it had already texted them.

**Files:**
- Modify: `grace/run.py` (`renewal_filed` at :355-370; `sweep`'s per-case loop at :425-433 and :582)
- Modify: `grace/entrypoint.py` (`process_case` at :175-180 and :249)
- Test: `tests/test_run_scoped_filing.py` (new)

**Interfaces:**
- Produces: `renewal_filed(store: CaseStore, case_id: str, since: datetime | None = None) -> bool` — counts a `renewal_submitted` entry only when `entry.at >= since`. `since=None` keeps the all-time reading for any caller that has no run boundary.
- Consumes: `LedgerEntry.at`, a timezone-aware `datetime` enforced at construction; `grace.entrypoint.process_case`; `grace.run.sweep`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_run_scoped_filing.py`:

```python
"""A FILING IS A CLAIM ABOUT THIS RUN, NOT ABOUT HISTORY.

`renewal_filed` read the whole ledger, so a household filed once was reported
`acted` by every later run whatever happened in it. Live, each clean case
carried twelve `renewal_submitted` rows across twelve sweeps — the agent was
filing every run, and this check could not have told that from a gate that had
stopped working entirely. The demo's own invariant was therefore unfalsifiable.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from grace.cases.models import LedgerEntry
from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.entrypoint import process_case
from grace.run import renewal_filed

TODAY = date(2026, 10, 1)


def _store() -> InMemoryCaseStore:
    return InMemoryCaseStore(load_fixture_cases())


def _filing(case_id: str, at: datetime) -> LedgerEntry:
    return LedgerEntry(
        case_id=case_id, at=at, kind="renewal_submitted",
        detail={"program": "medicaid", "cert_end": "2026-11-30"},
    )


class _NeverFilesGraph:
    """A graph that completes without calling a single tool.

    This is the shape the check must be able to see: the run ends cleanly and
    nothing was filed. Before run-scoping, an old ledger row made it
    indistinguishable from a run that filed correctly.
    """

    class _Result:
        from strands.multiagent.base import Status
        status = Status.COMPLETED
        interrupts = ()
        results = {}

    def __call__(self, *_args, **_kwargs):
        return self._Result()


def test_a_filing_from_an_earlier_run_does_not_count_as_this_one():
    store = _store()
    run_started = datetime.now(timezone.utc)
    store.append_ledger(_filing("c-001", run_started - timedelta(days=1)))
    assert renewal_filed(store, "c-001") is True, "the all-time reading still works"
    assert renewal_filed(store, "c-001", since=run_started) is False


def test_a_filing_from_this_run_counts():
    store = _store()
    run_started = datetime.now(timezone.utc)
    store.append_ledger(_filing("c-001", run_started + timedelta(seconds=1)))
    assert renewal_filed(store, "c-001", since=run_started) is True


def test_a_filing_at_exactly_the_run_start_counts():
    # `>=`, not `>`. A row written in the same microsecond the run began is
    # this run's; excluding it would report a real filing as absent.
    store = _store()
    run_started = datetime.now(timezone.utc)
    store.append_ledger(_filing("c-001", run_started))
    assert renewal_filed(store, "c-001", since=run_started) is True


def test_a_clean_case_that_files_nothing_escalates_despite_an_old_filing(monkeypatch):
    """The headline. A clean household with a filing from yesterday and a run
    that does nothing today must escalate, not report `acted`.

    Before run-scoping this returned `acted` with `filed: True`, and
    `web/lib/decide.ts` would have told a caseworker "the renewal was filed"
    about a run that filed nothing.
    """
    store = _store()
    store.append_ledger(_filing("c-001", datetime.now(timezone.utc) - timedelta(days=1)))
    monkeypatch.setattr(
        "grace.entrypoint.build_case_graph",
        lambda *args, **kwargs: _NeverFilesGraph(),
    )
    outcome = process_case({"case_id": "c-001", "today": TODAY.isoformat()}, store=store)
    assert outcome["status"] == "escalated", outcome
    assert "no renewal was filed" in outcome["reason"]
    assert outcome.get("filed") is None, "an escalated outcome must not carry filed"


def test_a_clean_case_that_does_file_reports_acted(monkeypatch):
    """The other half, so the test above cannot pass by escalating everything —
    the Task 8 lesson about removing the other branches' alibis."""
    store = _store()

    class _FilesOnce(_NeverFilesGraph):
        def __call__(self, *args, **kwargs):
            store.append_ledger(_filing("c-001", datetime.now(timezone.utc)))
            return super().__call__(*args, **kwargs)

    monkeypatch.setattr(
        "grace.entrypoint.build_case_graph", lambda *a, **k: _FilesOnce()
    )
    outcome = process_case({"case_id": "c-001", "today": TODAY.isoformat()}, store=store)
    assert outcome["status"] == "acted", outcome
    assert outcome["filed"] is True


def test_outreach_is_deliberately_not_run_scoped():
    """`outreach_sent` answers a different question and must stay all-time.

    Its purpose is to tell a caseworker the family has already been asked, so
    they do not ask again. Scoping it to one run would make Grace forget every
    message it had ever sent, and duplicate requests are exactly the confusion
    that makes families give up on paperwork.
    """
    import inspect

    from grace.run import outreach_sent

    assert "since" not in inspect.signature(outreach_sent).parameters
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_run_scoped_filing.py -q -p no:warnings`
Expected: FAIL. `test_a_filing_from_an_earlier_run_does_not_count_as_this_one` fails with `TypeError: renewal_filed() got an unexpected keyword argument 'since'`.

- [ ] **Step 3: Add `since` to `renewal_filed`**

In `grace/run.py`, replace `renewal_filed` (:355-370) with:

```python
def renewal_filed(
    store: CaseStore, case_id: str, since: datetime | None = None
) -> bool:
    """Whether the ledger confirms a renewal was filed, optionally in this run.

    Public for the same reason as `gate_reason`: the deployed entrypoint must
    answer this question from the same source the local sweep does.

    The ledger, not the model transcript and not the absence of an interrupt,
    is the ground truth for what executed. `submit_renewal` writes
    `renewal_submitted` only after the store operation returns, so this is a
    confirmed action rather than a claimed one (hard rule 6).

    **`since` scopes the claim to one run, and callers with a run boundary must
    pass it.** Without it this reads the whole ledger, which made the answer
    monotonic: a household filed once reported `acted` from every later run
    whatever happened in it. Measured live, every clean case carried twelve
    `renewal_submitted` rows across twelve sweeps — the agent was filing each
    time, and this function could not have distinguished that from a gate that
    had stopped working, because both produce a row that already exists. A
    check that cannot fail is indistinguishable from a passing one.

    `>=` rather than `>`: a row written in the same microsecond the run began
    belongs to that run, and excluding it would report a real filing as absent.
    Both stamps come from `datetime.now(timezone.utc)` in the same process, so
    there is no clock skew between them.

    The default stays `None` so a caller with no run boundary — a verification
    script asking "was this household ever filed" — still gets a truthful
    all-time answer rather than being forced to invent a start time.
    """
    try:
        entries = store.ledger(case_id)
    except Exception:  # noqa: BLE001 — an unreadable ledger confirms nothing
        return False
    return any(
        e.kind == "renewal_submitted" and (since is None or e.at >= since)
        for e in entries
    )
```

Confirm `datetime` is imported in `grace/run.py`; if only `date` is imported from `datetime`, extend that import to `from datetime import date, datetime, timezone`.

- [ ] **Step 4: Pass the run boundary from `sweep`**

In `grace/run.py`, inside the per-case loop, immediately before `try:` at :433 (the line above `graph = build_case_graph(...)`), insert:

```python
        # Captured before the graph runs, so every `renewal_submitted` row this
        # case writes is at or after it. Read from the same clock the ledger
        # writes with — `datetime.now(timezone.utc)` in this process — so there
        # is no skew to allow for.
        run_started = datetime.now(timezone.utc)
```

Then change the classification at :582 from `elif _renewal_filed(store, case.case_id):` to:

```python
        elif _renewal_filed(store, case.case_id, since=run_started):
```

- [ ] **Step 5: Pass the run boundary from the deployed entrypoint**

In `grace/entrypoint.py`, add `datetime` and `timezone` to the `from datetime import date` line so it reads `from datetime import date, datetime, timezone`. Then immediately before `try:` at :177 (the line above `graph = build_case_graph(...)`), insert:

```python
    # See `renewal_filed`'s docstring: captured before the graph runs so that a
    # filing from an earlier sweep cannot be mistaken for this run's work. The
    # deployed table holds twelve filings per household, so without this the
    # outcome this function returns — which `web/lib/decide.ts` renders as
    # "the renewal was filed" — would be a claim about history.
    run_started = datetime.now(timezone.utc)
```

Then change :249 from `if renewal_filed(store, case_id):` to:

```python
    if renewal_filed(store, case_id, since=run_started):
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_run_scoped_filing.py -q -p no:warnings`
Expected: PASS, 6 tests.

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: PASS, at least **880 tests**. A pre-existing test that fails here is asserting the all-time reading — read it before changing it, and say in the commit message which one moved.

- [ ] **Step 7: Sabotage and watch it fail**

| Sabotage | Must fail |
|---|---|
| `since is None or e.at >= since` → `True` | `test_a_filing_from_an_earlier_run_does_not_count_as_this_one`, `test_a_clean_case_that_files_nothing_escalates_despite_an_old_filing` |
| `e.at >= since` → `e.at > since` | `test_a_filing_at_exactly_the_run_start_counts` |
| `run_started` in `entrypoint.py` moved to *after* the `graph(...)` call | `test_a_clean_case_that_does_file_reports_acted` |

Restore with `git checkout -- grace/run.py grace/entrypoint.py` between each.

- [ ] **Step 8: Verify the deployed sweep still reports 9/3 — this is a gate, not a formality**

This change makes the sweep's headline able to fail for the first time. Run one sweep on the schedule's own input and read the outcome:

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:339712964409:stateMachine:grace-sweep \
  --region us-east-1 --input '{"today":"2026-10-01"}' --query executionArn --output text
```

Poll `describe-execution` until `SUCCEEDED`, then count the outcomes.

Expected: **12 outcomes, 9 acted / 3 escalated**, escalating exactly `c-010`/`c-011`/`c-012`.

**If it is not 9/3, stop and report — do not revert.** A clean household reporting `escalated` means the model did not file on that run, which is the regression this change exists to expose. Reverting would restore the blindness rather than fix the cause. Note that this deployed check exercises the **currently deployed runtime image**, which will not contain this change until a redeploy; run it anyway as a before-measurement, and re-run after any redeploy.

- [ ] **Step 9: Confirm the two invariants from DynamoDB and commit**

```bash
.venv/bin/python -c "
import boto3
d = boto3.client('dynamodb', region_name='us-east-1')
key=None; filed=set()
while True:
    kw={'TableName':'grace-cases'}
    if key: kw['ExclusiveStartKey']=key
    p=d.scan(**kw)
    for it in p['Items']:
        if it.get('kind',{}).get('S')=='renewal_submitted':
            filed.add(it['pk']['S'].removeprefix('CASE#'))
    key=p.get('LastEvaluatedKey')
    if not key: break
print('filed == c-001..c-009:', sorted(filed)==[f'c-{i:03d}' for i in range(1,10)])
print('no escalating household filed:', not ({'c-010','c-011','c-012'} & filed))
"
```

Both must print `True`.

```bash
git add grace/run.py grace/entrypoint.py tests/test_run_scoped_filing.py
git commit -m "fix: a filing is a claim about this run, not about history

renewal_filed read the whole ledger, so a household filed once reported
'acted' from every later run whatever happened in it. Live, each clean case
carries twelve renewal_submitted rows across twelve sweeps — the agent is
filing every run, and this check could not have told that from a gate that
had stopped working, because both leave a row that already exists.

Two consequences. The demo's headline invariant was unfalsifiable: a broken
gate filing nothing would leave every check in this repo passing. And
web/lib/decide.ts renders 'Grace re-checked and the renewal was filed' from
status === 'acted' && filed === true, which an old row alone can produce.

sweep and process_case now capture a run start before building the graph and
pass it as 'since'. >= not >, because a row written in the same microsecond
the run began is that run's. The default stays None so a verification script
asking 'was this ever filed' still gets a truthful all-time answer.

outreach_sent is deliberately NOT scoped. It answers whether the family has
already been bothered, and scoping it to one run would make Grace forget
every message it had sent — a pinned test asserts it takes no 'since'.

Three sabotages watched failing, and a deployed sweep re-verified at
9 acted / 3 escalated with both DynamoDB invariants intact."
```

---

### Task 3: The gate names an unhandled document problem instead of crashing on it

**The finding.** `evaluate`'s per-problem rendering is `if problem == "stale_by_age": … else: …`, so any third code from `document_problems` is rendered as "expired" and dereferences `doc.expires`, which may be `None`. Reproduced by patching a third code in:

```
RAISED: AttributeError 'NoneType' object has no attribute 'isoformat'
```

This is a **diagnosability** fix, not a safety one — every caller of `evaluate` already catches `Exception` broadly and escalates, so the raise fails closed either way. What is wrong is the message: `'NoneType' object has no attribute 'isoformat'` in a caseworker's escalation reason names nothing, where `unhandled document problem 'unreadable'` names the bug. The `else` also silently mislabels an unknown condition as an expiry, which is the same "a branch's message stopped being true" shape the project has hit three times.

**Files:**
- Modify: `grace/authority.py` (:228-245)
- Test: `tests/test_authority.py` (add two tests)

**Interfaces:**
- Consumes: `document_problems(doc, required, today) -> tuple[str, ...]`, unchanged, still returning only `"stale_by_age"` and `"expired"`.
- Produces: no signature change. `evaluate` may now raise `ValueError` naming an unhandled code, which its callers already treat as an escalation.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_authority.py`:

```python
def test_an_unhandled_document_problem_names_itself():
    """`evaluate` renders each `document_problems` code explicitly.

    The rendering used to be `if stale_by_age … else …`, so a third code fell
    into the expiry branch, was mislabelled as an expiry, and dereferenced
    `doc.expires` — which is `None` for a document that never expires. The
    result was `AttributeError: 'NoneType' object has no attribute 'isoformat'`
    in a caseworker's escalation reason. Every caller catches broadly, so this
    always failed closed; what was missing was a message naming the cause.
    """
    import pytest

    import grace.authority as authority

    case = _clean_case()
    original = authority.document_problems
    authority.document_problems = lambda doc, required, today: ("unreadable",)
    try:
        with pytest.raises(ValueError, match="unreadable"):
            authority.evaluate(case, TODAY, MEDICAID)
    finally:
        authority.document_problems = original


def test_an_expired_code_without_an_expiry_date_is_refused_not_rendered():
    """The impossible pairing is refused rather than crashing on `None`.

    `document_problems` only emits `expired` when `expires` is set, so this
    combination cannot arise today — which is exactly why the branch needs to
    say so rather than assume it.
    """
    import pytest

    import grace.authority as authority

    case = _clean_case()
    original = authority.document_problems
    authority.document_problems = lambda doc, required, today: ("expired",)
    try:
        # `_clean_case`'s documents are built without `expires`, so the expiry
        # branch has nothing to render. Asserted rather than assumed — if a
        # future edit gives the fixture an expiry, this test would silently
        # stop exercising the branch it exists for.
        for document in case.documents:
            assert document.expires is None, "fixture must have no expiry set"
        with pytest.raises(ValueError, match="expired"):
            authority.evaluate(case, TODAY, MEDICAID)
    finally:
        authority.document_problems = original
```

`_clean_case()`, `TODAY`, and `MEDICAID` already exist at the top of `tests/test_authority.py` (`MEDICAID = load_pack("medicaid", "NY")` at :27). Add `import pytest` at module scope if it is not already imported there rather than inside each test.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_authority.py -k unhandled -q -p no:warnings`
Expected: FAIL with `AttributeError: 'NoneType' object has no attribute 'isoformat'` rather than the expected `ValueError`.

- [ ] **Step 3: Make the dispatch exhaustive**

In `grace/authority.py`, replace the `for problem in document_problems(...)` block (:228-245) with:

```python
        for problem in document_problems(doc, required, today):
            if problem == "stale_by_age":
                reasons.append(
                    GateReason(
                        code="stale_document",
                        detail=(
                            f"{required.doc_id} received {doc.received.isoformat()}, "
                            f"older than {required.max_age_days} days"
                        ),
                    )
                )
            elif problem == "expired" and doc.expires is not None:
                reasons.append(
                    GateReason(
                        code="stale_document",
                        detail=f"{required.doc_id} expired {doc.expires.isoformat()}",
                    )
                )
            else:
                # Exhaustive on purpose, with no `else` that guesses. The
                # previous `if/else` sent every unrecognised code down the
                # expiry branch, which mislabelled it and then crashed on
                # `doc.expires.isoformat()` when the document had no expiry —
                # reaching a caseworker as "'NoneType' object has no attribute
                # 'isoformat'", a sentence naming nothing.
                #
                # Raising is the right direction rather than a lapse: Task 3
                # established that `evaluate`'s callers catch `Exception`
                # broadly and escalate, so an unrenderable verdict already
                # became a human decision. This only changes what that human
                # reads. `document_problems` emits `expired` only when
                # `expires` is set, so the second half of this branch is
                # unreachable today and says so rather than assuming it.
                raise ValueError(
                    f"{required.doc_id}: unhandled document problem {problem!r}"
                )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_authority.py -q -p no:warnings`
Expected: PASS, every test in the module.

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: PASS, at least **882 tests**.

- [ ] **Step 5: Confirm the purity guard still holds and the split is unmoved**

```bash
.venv/bin/python -m pytest tests/test_authority.py -k imports_only_pure -q -p no:warnings
.venv/bin/python -c "
from datetime import date
from grace.cases.store import load_fixture_cases
from grace.rules.pack import load_pack
from grace.authority import evaluate
cs = load_fixture_cases()
acts = [c.case_id for c in cs if evaluate(c, date(2026,10,1), load_pack(c.program, c.state)).decision == 'act']
print(len(acts), 'act /', len(cs)-len(acts), 'escalate')
assert len(acts) == 9, acts
"
```

Expected: the purity test passes, and the script prints `9 act / 3 escalate`.

- [ ] **Step 6: Sabotage and commit**

Sabotage: change `raise ValueError(...)` back to the old expiry rendering. Confirm `test_an_unhandled_document_problem_names_itself` fails. Restore.

```bash
git add grace/authority.py tests/test_authority.py
git commit -m "fix: the gate names an unhandled document problem instead of crashing on it

evaluate rendered document_problems codes with if/else, so any third code
fell into the expiry branch, was mislabelled as an expiry, and dereferenced
doc.expires — None for a document that never expires. Reproduced by patching
a third code in: AttributeError: 'NoneType' object has no attribute
'isoformat', in a caseworker's escalation reason.

Diagnosability, not safety: every caller of evaluate already catches broadly
and escalates, so this failed closed either way. What was missing was a
message naming the cause. The expiry branch now states the precondition it
was assuming, and an unrecognised code raises naming itself.

authority.py stays pure — no new imports. 9 act / 3 escalate unmoved.
Sabotage: restoring the old rendering fails
test_an_unhandled_document_problem_names_itself."
```

---

### Task 4: The pinned-date warning says what measurement says

**The finding.** Five source comments and CLAUDE.md all assert that fixture `c-002` going `closed` on 2026-10-31 turns the demo into **8/4** on that date. Measured across all twelve fixtures:

| date | measured split |
|---|---|
| 2026-10-01 | 9 / 3 |
| 2026-10-16 | c-002 escalates — on **`stale_document`**, not a closed window |
| 2026-10-23 | c-004 escalates |
| 2026-10-27 | c-008 escalates |
| 2026-10-30 | **6 / 6** |
| 2026-10-31 | 6 / 6, and only now does the window close |

The named cause is real but arrives **15 days late**, and the magnitude is wrong. The conclusion — never read a live clock — is right and *more* urgent than documented, so this corrects the number and keeps the warning. It also turns the claim into an assertion, which is what should have carried it in the first place.

**Files:**
- Create: `tests/test_demo_dates.py`
- Modify: `CLAUDE.md` (:175-177), `grace/graph.py` (:134-135), `grace/entrypoint.py` (:53-54), `grace/run.py` (:92-94), `grace/steering.py` (:120-123)

**Interfaces:**
- Consumes: `load_fixture_cases`, `load_pack`, `evaluate`. No production code changes — comments only.
- Produces: `tests/test_demo_dates.py`, which fails if any fixture's degradation date moves.

- [ ] **Step 1: Write the test**

Create `tests/test_demo_dates.py`:

```python
"""WHAT A LIVE CLOCK ACTUALLY COSTS.

Five comments and CLAUDE.md asserted that c-002's window closing on 2026-10-31
turns the 9/3 demo into 8/4 on that date. Measured, c-002 escalates on
**2026-10-16** on `stale_document`, and the split is **6/6** by 2026-10-30 —
before the window closes at all. The named cause was real and 15 days late,
and the magnitude was wrong in the reassuring direction.

The conclusion the comments draw is right and more urgent than they said, so
this pins the measurement rather than the prose. Any fixture edit that moves a
degradation date fails here, where a comment would simply have gone quietly out
of date — which is how it went wrong the first time.
"""

from __future__ import annotations

from datetime import date

from grace.authority import evaluate
from grace.cases.store import load_fixture_cases
from grace.rules.pack import load_pack

PINNED = date(2026, 10, 1)


def _split(day: date) -> tuple[list[str], list[str]]:
    acted, escalated = [], []
    for case in load_fixture_cases():
        verdict = evaluate(case, day, load_pack(case.program, case.state))
        (acted if verdict.decision == "act" else escalated).append(case.case_id)
    return acted, escalated


def test_the_pinned_date_gives_the_demos_split():
    acted, escalated = _split(PINNED)
    assert len(acted) == 9, acted
    assert escalated == ["c-010", "c-011", "c-012"], escalated


def test_the_first_clean_case_degrades_on_the_sixteenth_of_october():
    """c-002, and on a stale document rather than a closed window."""
    acted_before, _ = _split(date(2026, 10, 15))
    assert "c-002" in acted_before

    verdict = evaluate(
        next(c for c in load_fixture_cases() if c.case_id == "c-002"),
        date(2026, 10, 16),
        load_pack("snap", "NY"),
    )
    assert verdict.decision == "escalate"
    assert [r.code for r in verdict.reasons] == ["stale_document"], verdict.reasons


def test_the_split_is_six_six_before_any_window_closes():
    """The number the comments said was 8/4, and the date they said it began.

    Both wrong: three clean households have already gone stale by 2026-10-30,
    and c-002's grace period ends on that day rather than on the 31st.
    """
    acted, escalated = _split(date(2026, 10, 30))
    assert (len(acted), len(escalated)) == (6, 6), (acted, escalated)

    acted, escalated = _split(date(2026, 10, 31))
    assert (len(acted), len(escalated)) == (6, 6), (acted, escalated)


def test_every_clean_case_degrades_on_the_measured_day():
    """The full table, so a fixture edit that moves any one of them is caught.

    Measured 2026-09-08 against `fixtures/households.yaml`; every one is a
    `stale_document`, none is a window closing.
    """
    expected = {
        "c-001": date(2026, 11, 20), "c-002": date(2026, 10, 16),
        "c-003": date(2026, 11, 25), "c-004": date(2026, 10, 23),
        "c-005": date(2026, 11, 28), "c-006": date(2026, 11, 1),
        "c-007": date(2026, 11, 30), "c-008": date(2026, 10, 27),
        "c-009": date(2026, 11, 29),
    }
    for case in load_fixture_cases():
        if case.case_id not in expected:
            continue
        pack = load_pack(case.program, case.state)
        day = expected[case.case_id]
        assert evaluate(case, day.fromordinal(day.toordinal() - 1), pack).decision == "act", (
            f"{case.case_id} should still be clean the day before {day}"
        )
        assert evaluate(case, day, pack).decision == "escalate", (
            f"{case.case_id} should escalate on {day}"
        )
```

- [ ] **Step 2: Run it to verify it passes against today's fixtures**

Run: `.venv/bin/python -m pytest tests/test_demo_dates.py -q -p no:warnings`
Expected: PASS, 4 tests. This test documents a measurement, so it passes immediately — Step 4's sabotage is what proves it can fail.

- [ ] **Step 3: Correct the five comments and CLAUDE.md**

Replace each with the measured version. In `CLAUDE.md` at :175-177:

```markdown
**Pin the date.** Every test module uses `TODAY = date(2026, 10, 1)`. A `date.today()` anywhere in
the sweep degrades the demo **from 2026-10-16**, when `c-002`'s `proof_of_income` goes stale — not
from 2026-10-31 when its SNAP window closes, which is 15 days later and was the number this file
carried for five plans. By 2026-10-30 the split is **6/6**, not 8/4. `tests/test_demo_dates.py` pins
every fixture's degradation date so this cannot drift again. Task 6's CLI takes `--today` defaulting
to the pinned value.
```

In `grace/run.py` at :92-94:

```python
# The date every fixture window is anchored to. A default of `date.today()`
# would degrade the 9-act/3-escalate demo from 2026-10-16, when c-002's
# proof_of_income goes stale — and the split is 6/6 by 2026-10-30, before
# c-002's SNAP grace period ends. `tests/test_demo_dates.py` pins the dates.
```

In `grace/entrypoint.py` at :53-54:

```python
# Never `date.today()`. A live clock degrades the 9-act/3-escalate demo from
# 2026-10-16 (c-002's proof_of_income goes stale) and reaches 6/6 by
# 2026-10-30. See `tests/test_demo_dates.py`.
```

In `grace/graph.py` at :134-135 and `grace/steering.py` at :120-123, apply the same correction, keeping each comment's surrounding sentence and its existing indentation. Leave `CLAUDE.md:426` and `grace/graph.py:189` alone — those describe the concurrent tool executor moving the split, which is a different and still-correct claim.

- [ ] **Step 4: Sabotage the new test and watch it fail**

Change `"c-002": date(2026, 10, 16)` to `date(2026, 10, 31)` in the expected table.
Run: `.venv/bin/python -m pytest tests/test_demo_dates.py -q -p no:warnings`
Expected: FAIL — `c-002 should still be clean the day before 2026-10-31`. Restore.

- [ ] **Step 5: Run the gates and commit**

```bash
.venv/bin/python -m pytest -q -p no:warnings
git add tests/test_demo_dates.py CLAUDE.md grace/graph.py grace/entrypoint.py grace/run.py grace/steering.py
git commit -m "docs: the pinned-date warning says what measurement says

Five comments and CLAUDE.md asserted that c-002's window closing on
2026-10-31 turns the demo into 8/4. Measured across all twelve fixtures,
c-002 escalates on 2026-10-16 on stale_document — 15 days earlier and for a
different reason — and the split is 6/6 by 2026-10-30, before any window
closes. The named cause was late and the magnitude was wrong in the
reassuring direction.

The conclusion those comments draw is right and more urgent than they said,
so this corrects the number and keeps the warning. tests/test_demo_dates.py
pins every fixture's degradation date, so the next fixture edit that moves
one fails a test rather than quietly outdating a comment — which is how this
went wrong the first time.

Sabotage: moving c-002's expected date to the 31st fails the test."
```

---

### Task 5: Two constraints that lived in nobody's test

**The findings.** Both are true statements about the system that no assertion holds, so either could be silently reversed.

**Repeat filing.** `evaluate` is pure and cannot see the ledger, so nothing asks "have I already filed this?" — the daily sweep files the same renewal every day, twelve times so far per clean household. Harmless today because `submit_renewal` writes a ledger row and there is no state integration behind it, and the README says so. It becomes a duplicate-submission bug the moment one is attached. **Deliberately not fixed here:** making `submit_renewal` idempotent would stop it writing a row on the second day, which Task 2's run-scoped check would then read as "nothing was filed" and escalate a clean household. The two changes conflict, and the one that matters now is Task 2.

**Record-over-seed precedence.** `open_cases()` lets a table `RECORD#v1` row win over the fixture seed. Until `infra/seed_cases.py` was run on 2026-09-07 none of `c-001`–`c-012` had record rows, so a caseworker could have submitted `c-001` through `/new` and silently replaced a fixture household's facts. Seeding closed it — `create_case` puts under `attribute_not_exists(sk)` — but nothing records that seeding is a prerequisite rather than a convenience.

**Files:**
- Create: `tests/test_recorded_constraints.py`
- Modify: `README.md` (the `submit_renewal` paragraph), `docs/runbook-deploy.md` (add a seeding prerequisite)

**Interfaces:**
- Consumes: `make_action_tools`, `InMemoryCaseStore`, `DynamoDBCaseStore.open_cases`. No production code changes.

- [ ] **Step 1: Write the tests**

Create `tests/test_recorded_constraints.py`:

```python
"""TWO TRUE STATEMENTS THAT NOTHING WAS HOLDING.

Neither is a defect today. Both are properties a future change could reverse
without any test noticing, and each has a named consequence when it does.
"""

from __future__ import annotations

from datetime import date

from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.tools.action import TranscriptChannel, make_action_tools

TODAY = date(2026, 10, 1)


def test_submit_renewal_files_every_time_it_is_called():
    """**Grace has no idempotency check, and this pins that it is a choice.**

    `evaluate` is pure and cannot read the ledger, so nothing asks whether this
    renewal has already been filed. The daily sweep therefore files the same
    renewal every day — twelve rows per clean household on the live table.

    Harmless today: `submit_renewal` writes a ledger row and there is no state
    integration behind it (the README says so outright). It becomes a
    duplicate-submission bug the moment one is attached.

    **Do not "fix" this by short-circuiting on an existing row.** Task 2 scoped
    `renewal_filed` to the current run, so a `submit_renewal` that declined to
    write a second row would make a clean household read as unfiled and
    escalate on day two. A real filing endpoint needs idempotency at the
    endpoint — a submission id the state system deduplicates on — not a ledger
    lookup here.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    tools = {t.tool_name: t for t in make_action_tools(store, "c-001", TranscriptChannel())}
    # `DecoratedFunctionTool._tool_func` is the undecorated callable. Verified:
    # `original_function` does not exist on this SDK version, and `stream()`
    # would drag the whole tool-execution path into a test about ledger rows.
    submit = tools["submit_renewal"]._tool_func

    submit()
    submit()

    filings = [e for e in store.ledger("c-001") if e.kind == "renewal_submitted"]
    assert len(filings) == 2, (
        "submit_renewal deduplicated. If that was deliberate, read this test's "
        "docstring: it breaks Task 2's run-scoped classification."
    )


def test_a_table_record_row_wins_over_the_fixture_seed():
    """**Why `infra/seed_cases.py` is a deploy prerequisite, not a convenience.**

    `open_cases()` unions the constructor seed with the table's record rows and
    lets the table win, so a household submitted through `/new` is swept even
    though no image was rebuilt. The same precedence means that until the
    twelve fixture ids had record rows, submitting `c-001` through the intake
    form would have created one and silently replaced that household's facts
    for the agent while the dashboard showed nothing wrong.

    Seeding closed it: `create_case` puts under `attribute_not_exists(sk)`, so
    a fixture id is now taken. Run `python -m infra.seed_cases --verify` before
    exposing intake against any new table.
    """
    import inspect

    from grace.cases.dynamo_store import DynamoDBCaseStore

    source = inspect.getsource(DynamoDBCaseStore.open_cases)
    seed_line = source.index("by_id: dict[str, Case] = dict(self._cases)")
    table_line = source.index("by_id[case_id] = record.from_item(item)")
    assert seed_line < table_line, (
        "the seed must be laid down first and the table's record row must "
        "overwrite it; reversing this makes a submitted case invisible"
    )


def test_a_directory_entry_without_a_record_row_raises_rather_than_skipping():
    """The other half of the same precedence, and the loud direction on purpose.

    `web/lib/create-case.ts` writes the directory row first and the record row
    second, so a partial write leaves an id the directory names and the table
    cannot load. Skipping it would drop a household from the sweep while every
    count still looked plausible.
    """
    import inspect

    from grace.cases.dynamo_store import DynamoDBCaseStore

    source = inspect.getsource(DynamoDBCaseStore.open_cases)
    assert "raise record.InvalidCaseRecord" in source
```

- [ ] **Step 2: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_recorded_constraints.py -q -p no:warnings`
Expected: PASS, 3 tests.

- [ ] **Step 3: Sabotage each and watch it fail**

| Sabotage | Must fail |
|---|---|
| in `grace/tools/action.py`, make `submit_renewal` return early when the ledger already holds a `renewal_submitted` row | `test_submit_renewal_files_every_time_it_is_called` |
| in `grace/cases/dynamo_store.py`, swap the two `by_id` lines so the seed overwrites the table | `test_a_table_record_row_wins_over_the_fixture_seed` |
| replace `raise record.InvalidCaseRecord(...)` with `continue` | `test_a_directory_entry_without_a_record_row_raises_rather_than_skipping` |

Restore between each.

- [ ] **Step 4: State the repeat-filing constraint in the README**

In `README.md`, extend the `submit_renewal` paragraph (it begins **"`submit_renewal` does not submit anything to a state system"**) with:

```markdown
One consequence worth naming: because the gate is pure and cannot read the ledger, nothing asks
whether this renewal has already been filed, so the daily sweep files each clean household again
every day — twelve ledger rows per household at the time of writing. That is inert while the tool
writes a ledger row and nothing else. A real filing endpoint would need idempotency **at the
endpoint** — a submission id the state system deduplicates on — rather than a ledger lookup here,
because Grace's own classification counts a filing only within the run that made it.
```

- [ ] **Step 5: Make seeding a runbook prerequisite**

In `docs/runbook-deploy.md`, add to the deploy sequence, before any step that exposes the dashboard:

```markdown
### Seed the case directory before exposing intake

```bash
.venv/bin/python -m infra.seed_cases
.venv/bin/python -m infra.seed_cases --verify
```

Both are required and `--verify` is the one that counts: "the put returned" and "the row is there and
decodes to the case I meant" are different claims. This is a **prerequisite, not a convenience**.
`DynamoDBCaseStore.open_cases()` lets a table record row win over the fixture seed, so until the
twelve fixture ids have record rows, submitting `c-001` through `/new` creates one and silently
replaces that household's facts for the agent while the dashboard shows nothing wrong. Once seeded,
`create_case`'s `attribute_not_exists(sk)` refuses the id.
```

- [ ] **Step 6: Run the gates and commit**

```bash
.venv/bin/python -m pytest -q -p no:warnings
git add tests/test_recorded_constraints.py README.md docs/runbook-deploy.md
git commit -m "test: pin two constraints that lived in nobody's test

Neither is a defect. Both are true statements a future change could reverse
with no test noticing.

Repeat filing: the gate is pure and cannot read the ledger, so nothing asks
whether a renewal has already been filed and the daily sweep files each
clean household every day — twelve rows each on the live table. Inert while
submit_renewal writes a ledger row and nothing else. Deliberately not fixed:
short-circuiting on an existing row would make Task 2's run-scoped check
read day two as unfiled and escalate a clean household, so idempotency
belongs at a real filing endpoint. The test says so where someone would
otherwise reach for the ledger lookup.

Record-over-seed precedence: open_cases lets a table record row win over the
fixture seed, which is what makes a submitted case visible to the agent —
and, until seed_cases.py was run on 2026-09-07, what would have let someone
submit c-001 through /new and silently replace that household's facts.
Seeding closed it; the runbook now says it is a prerequisite rather than a
convenience.

Three sabotages watched failing."
```

---

### Task 6: A drift check for the infrastructure that was changed by hand

**The finding.** The `grace-sweep` state machine definition, the EventBridge target input, and the Step Functions IAM policy were all edited live on 2026-09-07. All three currently match their provisioners — verified by comparison — but nothing asserts it, and a console edit or a half-applied provisioning run would leave the repository describing infrastructure that is not deployed. This is the same shape as the finding that started that day's work: the code said one thing and the running system did another, and nothing failed.

**Files:**
- Create: `infra/verify_deployed.py`
- Create: `tests/test_verify_deployed.py`
- Modify: `docs/runbook-deploy.md`

**Interfaces:**
- Produces: `check_drift(sfn, events, iam, account_id: str) -> list[str]` — a list of human-readable mismatch descriptions, empty when everything matches. Takes its three clients as parameters so the tests drive it with fakes.
- Produces: `main() -> int` — 0 when there is no drift, 1 otherwise.
- Consumes: `infra.provision_stepfunctions.definition`, `infra.provision_eventbridge.SWEEP_INPUT`, `infra.provision_iam._stepfunctions_policy`, `infra.naming`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_verify_deployed.py`:

```python
"""DOES THE RUNNING SYSTEM MATCH THE REPOSITORY?

The state machine definition, the schedule's input, and the Step Functions IAM
policy were each edited live on 2026-09-07. All three matched their
provisioners afterwards — and nothing asserted it. A console edit, or a
provisioning run that half-applied, would leave this repository describing
infrastructure that is not deployed, which is the shape of the defect that
started that day's work: the code said one thing, the running system did
another, and nothing failed.
"""

from __future__ import annotations

import json

from infra import naming, provision_eventbridge, provision_iam, provision_stepfunctions
from infra.verify_deployed import check_drift

ACCOUNT = "339712964409"
LAMBDA_ARN = f"arn:aws:lambda:{naming.REGION}:{ACCOUNT}:function:{naming.LAMBDA}"


class _FakeSfn:
    def __init__(self, definition):
        self._definition = definition

    def describe_state_machine(self, **_kwargs):
        return {"definition": json.dumps(self._definition)}


class _FakeEvents:
    def __init__(self, target_input):
        self._input = target_input

    def list_targets_by_rule(self, **_kwargs):
        return {"Targets": [{"Input": json.dumps(self._input)}]}


class _FakeIam:
    def __init__(self, policy):
        self._policy = policy

    def get_role_policy(self, **_kwargs):
        return {"PolicyDocument": self._policy}


def _matching():
    return (
        _FakeSfn(provision_stepfunctions.definition(ACCOUNT, LAMBDA_ARN)),
        _FakeEvents(provision_eventbridge.SWEEP_INPUT),
        _FakeIam(provision_iam._stepfunctions_policy(ACCOUNT)),
    )


def test_no_drift_when_everything_matches():
    sfn, events, iam = _matching()
    assert check_drift(sfn, events, iam, ACCOUNT) == []


def test_a_changed_state_machine_is_reported():
    _, events, iam = _matching()
    stale = provision_stepfunctions.definition(ACCOUNT, LAMBDA_ARN)
    stale["StartAt"] = "SweepCases"  # the pre-2026-09-07 shape
    drift = check_drift(_FakeSfn(stale), events, iam, ACCOUNT)
    assert len(drift) == 1
    assert "state machine" in drift[0].lower()


def test_a_frozen_case_list_on_the_schedule_is_reported():
    """The exact regression: a schedule that names its caseload again."""
    sfn, _, iam = _matching()
    frozen = {"case_ids": [f"c-{n:03d}" for n in range(1, 13)], "today": "2026-10-01"}
    drift = check_drift(sfn, _FakeEvents(frozen), iam, ACCOUNT)
    assert len(drift) == 1
    assert "schedule" in drift[0].lower()


def test_a_missing_iam_statement_is_reported():
    sfn, events, _ = _matching()
    reduced = provision_iam._stepfunctions_policy(ACCOUNT)
    reduced["Statement"] = [
        s for s in reduced["Statement"] if s.get("Sid") != "ReadTheCaseDirectory"
    ]
    drift = check_drift(sfn, events, _FakeIam(reduced), ACCOUNT)
    assert len(drift) == 1
    assert "policy" in drift[0].lower()


def test_every_mismatch_is_reported_not_just_the_first():
    """A checker that stops at the first difference hides the rest, and an
    operator then fixes one thing and re-runs into the next."""
    stale = provision_stepfunctions.definition(ACCOUNT, LAMBDA_ARN)
    stale["StartAt"] = "SweepCases"
    frozen = {"case_ids": ["c-001"], "today": "2026-10-01"}
    reduced = {"Version": "2012-10-17", "Statement": []}
    drift = check_drift(_FakeSfn(stale), _FakeEvents(frozen), _FakeIam(reduced), ACCOUNT)
    assert len(drift) == 3, drift
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_verify_deployed.py -q -p no:warnings`
Expected: FAIL with `ModuleNotFoundError: No module named 'infra.verify_deployed'`.

- [ ] **Step 3: Write the verifier**

Create `infra/verify_deployed.py`:

```python
"""Does the running system match this repository?

    .venv/bin/python -m infra.verify_deployed

Exit 0 when the deployed state machine, schedule input, and Step Functions IAM
policy are byte-equal to what the provisioners in this package produce; exit 1
with a list of the differences otherwise.

**Why this exists.** All three were edited live on 2026-09-07 to fix a sweep
that could not see a submitted household, and all three matched their
provisioners afterwards — but nothing asserted it. The defect that prompted
that work was precisely this shape: the repository described one system and
another was running, with no error anywhere, because a green schedule and a
`SUCCEEDED` execution look identical either way.

**Reads only.** This module issues no write of any kind. It is safe to run
against production at any time, which is the point — a drift check nobody dares
run is not a drift check.

**Every difference is reported, not just the first.** An operator who fixes one
mismatch and re-runs into the next learns the same lesson twice.
"""

from __future__ import annotations

import json
import sys

import boto3

from infra import naming, provision_eventbridge, provision_iam, provision_stepfunctions

SFN_ROLE = "grace-stepfunctions-role"
SFN_POLICY = "grace-stepfunctions-policy"


def check_drift(sfn, events, iam, account_id: str) -> list[str]:
    """Every way the deployed system differs from the provisioners."""
    drift: list[str] = []
    lambda_arn = f"arn:aws:lambda:{naming.REGION}:{account_id}:function:{naming.LAMBDA}"

    state_machine_arn = (
        f"arn:aws:states:{naming.REGION}:{account_id}:"
        f"stateMachine:{naming.STATE_MACHINE}"
    )
    live_definition = json.loads(
        sfn.describe_state_machine(stateMachineArn=state_machine_arn)["definition"]
    )
    expected_definition = provision_stepfunctions.definition(account_id, lambda_arn)
    if live_definition != expected_definition:
        drift.append(
            f"state machine {naming.STATE_MACHINE}: the deployed definition is not "
            f"what infra/provision_stepfunctions.py produces "
            f"(deployed starts at {live_definition.get('StartAt')!r}, "
            f"expected {expected_definition.get('StartAt')!r})"
        )

    targets = events.list_targets_by_rule(Rule=naming.SCHEDULE_RULE)["Targets"]
    live_input = json.loads(targets[0]["Input"]) if targets else None
    if live_input != provision_eventbridge.SWEEP_INPUT:
        drift.append(
            f"schedule {naming.SCHEDULE_RULE}: the target input is {live_input!r}, "
            f"expected {provision_eventbridge.SWEEP_INPUT!r}. A `case_ids` key here "
            f"means the caseload is frozen at provisioning time and a household "
            f"added since is never swept."
        )

    live_policy = iam.get_role_policy(
        RoleName=SFN_ROLE, PolicyName=SFN_POLICY
    )["PolicyDocument"]
    if live_policy != provision_iam._stepfunctions_policy(account_id):
        live_sids = sorted(s.get("Sid", "?") for s in live_policy.get("Statement", []))
        drift.append(
            f"policy {SFN_POLICY} on {SFN_ROLE}: the deployed document is not what "
            f"infra/provision_iam.py produces (deployed statements: {live_sids})"
        )

    return drift


def main() -> int:
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    drift = check_drift(
        boto3.client("stepfunctions", region_name=naming.REGION),
        boto3.client("events", region_name=naming.REGION),
        boto3.client("iam"),
        account_id,
    )
    if not drift:
        print("no drift: the deployed sweep matches this repository")
        return 0
    print(f"DRIFT in {len(drift)} place(s):")
    for item in drift:
        print(f"  - {item}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_verify_deployed.py -q -p no:warnings`
Expected: PASS, 5 tests.

- [ ] **Step 5: Run it against the live account**

Run: `.venv/bin/python -m infra.verify_deployed`
Expected: `no drift: the deployed sweep matches this repository`, exit 0.

If it reports drift, that is a real finding — record what differs before changing anything, because the deployed system may be the correct one.

- [ ] **Step 6: Document it in the runbook**

In `docs/runbook-deploy.md`, add after the deploy sequence:

```markdown
### After any deploy, and before recording anything

```bash
.venv/bin/python -m infra.verify_deployed
```

Compares the deployed state machine definition, the EventBridge target input, and the Step Functions
IAM policy against what `infra/` produces. Reads only — no writes of any kind — so it is safe to run
against production at any time. Exit 1 lists every difference rather than the first.

It exists because all three were edited live on 2026-09-07 and nothing asserted that they still
matched the repository afterwards. A `case_ids` key reappearing in the schedule's input is the
specific regression to watch for: it freezes the caseload at provisioning time, and every symptom
stays green.
```

- [ ] **Step 7: Run the gates and commit**

```bash
.venv/bin/python -m pytest -q -p no:warnings
git add infra/verify_deployed.py tests/test_verify_deployed.py docs/runbook-deploy.md
git commit -m "feat: a read-only drift check for the sweep's deployed infrastructure

The state machine definition, the schedule's target input, and the Step
Functions IAM policy were each edited live on 2026-09-07. All three matched
their provisioners afterwards and nothing asserted it — the same shape as
the defect that prompted that work, where the repository described one
system and another was running with no error anywhere.

verify_deployed compares all three and reports every difference rather than
the first, so an operator does not fix one and re-run into the next. It
issues no write, so it is safe against production at any time; a drift check
nobody dares run is not a drift check.

The schedule check names the specific regression: a case_ids key freezes the
caseload at provisioning time and every symptom stays green.

Five tests against fakes, including one that reproduces the frozen case list
and one asserting all three mismatches are reported together. Run live
against the account: no drift."
```

---

### Task 7: Grace does not file the same renewal twice

**Depends on Task 2.** Do not start this until Task 2 is committed.

**The finding.** The gate is pure and cannot read the ledger, so nothing asks whether this renewal has already been filed. The daily sweep files each clean household again every day — **12 `renewal_submitted` rows per household across 12 sweeps** on the live table, all carrying the same `d_cert_end` of `2026-10-15` for `c-001`. Inert while the tool writes a ledger row and nothing else; a duplicate-submission bug the moment a real endpoint is attached.

**Why the obvious fix breaks Task 2, and what to do instead.** Making `submit_renewal` return early *without writing a row* would make Task 2's run-scoped check read day two as "nothing was filed" and escalate a clean household. So the short-circuit still writes a ledger row — a **different kind**, `renewal_already_filed` — and `renewal_filed` counts both. The property Task 2 established survives exactly: a run that reached no outcome at all writes neither kind and still escalates, while a run that confirmed an existing filing wrote something and reports `acted`.

**The two invariants are preserved and improved.** `renewal_submitted` rows stop accumulating and freeze at their current count, and `web/lib/cases.ts`'s `FILED = "renewal_submitted"` is unchanged, so the dashboard's `filed` flag still reads exactly the rows that prove a filing.

**Files:**
- Modify: `grace/tools/action.py` (`make_action_tools`, the `submit_renewal` tool)
- Modify: `grace/run.py` (`renewal_filed`, to count both kinds)
- Modify: `tests/test_recorded_constraints.py` (Task 5's pinned constraint now describes the fix)
- Test: `tests/test_no_duplicate_filing.py` (new)

**Interfaces:**
- Produces: ledger kind `renewal_already_filed`, carrying the same `program` and `cert_end` detail keys as `renewal_submitted`.
- Produces: `grace.run.FILED_KINDS: frozenset[str]` — the kinds that mean a renewal is on file for this period.
- Consumes: `renewal_filed(store, case_id, since=...)` from Task 2, unchanged in signature.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_no_duplicate_filing.py`:

```python
"""GRACE FILES A RENEWAL ONCE PER CERTIFICATION PERIOD.

The gate is pure and cannot read the ledger, so nothing asked whether this
renewal had already been filed. The daily sweep filed each clean household
again every day — twelve rows each on the live table, all for the same
certification period. Inert while `submit_renewal` writes a ledger row and
nothing else; a duplicate submission the moment a real endpoint is attached.

**The short-circuit still writes a row**, of kind `renewal_already_filed`.
Returning early without one would make Task 2's run-scoped `renewal_filed`
read day two as "nothing was filed" and escalate a clean household — so the
run-scoped property and the no-duplicate property would be in direct conflict.
Two kinds, both counted, keeps both: a run that reached no outcome writes
neither and still escalates.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from grace.cases.models import LedgerEntry
from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.run import FILED_KINDS, renewal_filed
from grace.tools.action import TranscriptChannel, make_action_tools

TODAY = date(2026, 10, 1)


def _submit(store: InMemoryCaseStore, case_id: str = "c-001"):
    tools = {t.tool_name: t for t in make_action_tools(store, case_id, TranscriptChannel())}
    return tools["submit_renewal"]._tool_func


def _kinds(store: InMemoryCaseStore, case_id: str = "c-001") -> list[str]:
    return [e.kind for e in store.ledger(case_id)]


def test_the_first_call_files():
    store = InMemoryCaseStore(load_fixture_cases())
    result = _submit(store)()
    assert "filed" in result.lower()
    assert _kinds(store).count("renewal_submitted") == 1


def test_a_second_call_for_the_same_period_does_not_file_again():
    store = InMemoryCaseStore(load_fixture_cases())
    submit = _submit(store)
    submit()
    result = submit()
    assert _kinds(store).count("renewal_submitted") == 1, "filed twice"
    assert _kinds(store).count("renewal_already_filed") == 1
    assert "already" in result.lower()


def test_the_short_circuit_still_writes_a_row_so_the_run_is_not_reported_empty():
    """The property that keeps this compatible with run-scoped classification.

    A second-day run that short-circuited *silently* would leave the run with
    no filing row at all, and `renewal_filed(since=run_started)` would escalate
    a clean household. Both kinds count.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    submit = _submit(store)
    submit()

    run_started = datetime.now(timezone.utc)
    submit()
    assert renewal_filed(store, "c-001", since=run_started) is True


def test_a_run_that_calls_nothing_still_reports_no_filing():
    """The other half — the guard against this fix reintroducing blindness."""
    store = InMemoryCaseStore(load_fixture_cases())
    _submit(store)()
    run_started = datetime.now(timezone.utc) + timedelta(seconds=1)
    assert renewal_filed(store, "c-001", since=run_started) is False


def test_a_new_certification_period_files_again():
    """Dedup is keyed on the period, not on the household. A household whose
    certification has rolled over needs a new renewal, and refusing to file it
    would be the family-harming direction of this change."""
    from dataclasses import replace

    cases = load_fixture_cases()
    case = next(c for c in cases if c.case_id == "c-001")
    store = InMemoryCaseStore(cases)
    _submit(store)()

    # The next cycle: same household, a later certification end.
    rolled = replace(case, cert_end=date(2027, 10, 15))
    store_next = InMemoryCaseStore([rolled])
    for entry in store.ledger("c-001"):
        store_next.append_ledger(entry)
    _submit(store_next)()
    assert _kinds(store_next).count("renewal_submitted") == 2


def test_an_unreadable_ledger_files_rather_than_skipping():
    """**Fails toward filing, which is the opposite polarity to verification.**

    Everywhere Grace answers a *verification* question it fails closed, because
    an unverified case must reach a human. This is not that question. The gate
    has already cleared this case; the only thing in doubt is whether a
    duplicate exists. Declining to file on a failed read would mean a family's
    renewal silently never happens, while filing means a duplicate ledger row.
    """
    class _BlindStore(InMemoryCaseStore):
        def ledger(self, case_id: str):
            raise RuntimeError("ledger unavailable")

    store = _BlindStore(load_fixture_cases())
    written: list[str] = []
    store.append_ledger = lambda entry: written.append(entry.kind)  # type: ignore[method-assign]
    _submit(store)()
    assert written == ["renewal_submitted"]


def test_both_kinds_mean_a_renewal_is_on_file():
    assert FILED_KINDS == frozenset({"renewal_submitted", "renewal_already_filed"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_no_duplicate_filing.py -q -p no:warnings`
Expected: FAIL — `ImportError: cannot import name 'FILED_KINDS' from 'grace.run'`.

- [ ] **Step 3: Add the dedup check to `submit_renewal`**

In `grace/tools/action.py`, inside `make_action_tools` and directly above the `@tool def submit_renewal`, add:

```python
    def _already_filed_for(cert_end: str) -> bool:
        """Whether the ledger already holds a filing for this period.

        Reads the **whole** ledger on purpose. The question here is "does a
        filing exist for this certification period", which is a fact about the
        household rather than about this run — the opposite scoping to
        `grace.run.renewal_filed`, which asks whether *this run* reached an
        outcome. Both exist because they answer different questions.

        Keyed on `cert_end`, not on the case id: a household whose
        certification has rolled over needs a new renewal, and a check keyed on
        the household alone would refuse to file it forever.

        **Fails toward filing.** Everywhere Grace answers a *verification*
        question it fails closed, because an unverified case must reach a
        human. This is not that question — the gate has already cleared this
        case, and the only thing in doubt is whether a duplicate exists. An
        unreadable ledger that stopped the filing would mean a family's renewal
        silently never happens; one that allows it means a duplicate row.
        """
        try:
            entries = store.ledger(case_id)
        except Exception:  # noqa: BLE001 — see the docstring: file anyway
            return False
        return any(
            e.kind == "renewal_submitted" and e.detail.get("cert_end") == cert_end
            for e in entries
        )
```

Then replace the `submit_renewal` tool body with:

```python
    @tool
    def submit_renewal() -> str:
        """File the renewal for the current case.

        No arguments needed — identity is determined from the session. This
        tool only executes if the authority gate has already passed.
        """
        c = store.get(case_id)
        cert_end = c.cert_end.isoformat()
        if _already_filed_for(cert_end):
            # A row is still written, and that is load-bearing rather than
            # tidy. `grace.run.renewal_filed` counts filings *within this run*
            # to tell a working sweep from one that did nothing, so a silent
            # short-circuit would make the second day's run look empty and
            # escalate a clean household. Two kinds, both counted, keeps the
            # no-duplicate property and the run-scoped property together.
            _log("renewal_already_filed", program=c.program, cert_end=cert_end)
            return (
                f"Renewal for {c.case_id} ({c.program}) was already filed for the "
                f"period ending {cert_end}. Not filing it again."
            )
        _log("renewal_submitted", program=c.program, cert_end=cert_end)
        return f"Renewal filed for {c.case_id} ({c.program})."
```

- [ ] **Step 4: Count both kinds in `renewal_filed`**

In `grace/run.py`, above `renewal_filed`, add:

```python
# The ledger kinds that mean a renewal is on file for this certification
# period. `renewal_already_filed` is written by `submit_renewal` when it
# declines to file a duplicate — it must count here, or the second day's run
# looks like a run that did nothing and a clean household escalates.
FILED_KINDS = frozenset({"renewal_submitted", "renewal_already_filed"})
```

Then change the `any(...)` in `renewal_filed`'s body from `e.kind == "renewal_submitted"` to `e.kind in FILED_KINDS`, and add to its docstring, after the `since` paragraph:

```
    Counts `renewal_already_filed` as well as `renewal_submitted` (see
    `FILED_KINDS`). A sweep that finds an existing filing for the period
    declines to duplicate it and records that decision, which is an outcome —
    unlike a run that reached none at all, which still escalates.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_no_duplicate_filing.py -q -p no:warnings`
Expected: PASS, 7 tests.

Run: `.venv/bin/python -m pytest tests/test_run_scoped_filing.py -q -p no:warnings`
Expected: PASS, 6 tests — Task 2's property is unchanged by this.

- [ ] **Step 6: Rewrite Task 5's pinned constraint, which now describes the fix**

In `tests/test_recorded_constraints.py`, replace `test_submit_renewal_files_every_time_it_is_called` entirely with:

```python
def test_submit_renewal_does_not_duplicate_a_filing_for_the_same_period():
    """Kept from the audit, inverted by the fix.

    This test used to assert that Grace filed the same renewal every day —
    twelve rows per household on the live table — and to explain why that was
    left alone. `tests/test_no_duplicate_filing.py` now covers the behaviour
    properly; this remains as the constraint that a future change must not
    quietly reverse, in the file where the constraint was first recorded.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    tools = {t.tool_name: t for t in make_action_tools(store, "c-001", TranscriptChannel())}
    submit = tools["submit_renewal"]._tool_func
    submit()
    submit()
    filings = [e for e in store.ledger("c-001") if e.kind == "renewal_submitted"]
    assert len(filings) == 1, "the same renewal was filed twice"
```

- [ ] **Step 7: Correct the README paragraph Task 5 added**

In `README.md`, replace the paragraph beginning **"One consequence worth naming"** with:

```markdown
Grace files a renewal **once per certification period**. The gate itself is pure and cannot read the
ledger, so the check lives in `submit_renewal`: it looks for an existing `renewal_submitted` row
carrying the same `cert_end` and, finding one, records `renewal_already_filed` instead of filing
again. It still writes a row, deliberately — Grace's classification counts a filing only within the
run that made it, so a silent short-circuit would make the next day's sweep look like a run that did
nothing and escalate a household that is perfectly fine. If the ledger cannot be read it files
anyway: the gate has already cleared the case, and a renewal that silently never happens is worse
than a duplicate row.
```

- [ ] **Step 8: Verify the whole suite and the demo split**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: PASS, at least **894 tests**.

Run a local end-to-end sweep twice against one in-memory store and confirm the second reports the same 9/3 while filing nothing new:

```bash
.venv/bin/python -c "
from datetime import date, datetime, timezone
from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.tools.action import TranscriptChannel, make_action_tools
store = InMemoryCaseStore(load_fixture_cases())
for day in (1, 2):
    tools = {t.tool_name: t for t in make_action_tools(store, 'c-001', TranscriptChannel())}
    print(f'day {day}:', tools['submit_renewal']._tool_func())
kinds = [e.kind for e in store.ledger('c-001')]
print('renewal_submitted:', kinds.count('renewal_submitted'))
print('renewal_already_filed:', kinds.count('renewal_already_filed'))
assert kinds.count('renewal_submitted') == 1
"
```

Expected: one filing, one already-filed, and the second call's message says so.

- [ ] **Step 9: Sabotage and commit**

| Sabotage | Must fail |
|---|---|
| `_already_filed_for` returns `False` unconditionally | `test_a_second_call_for_the_same_period_does_not_file_again` |
| the `_log("renewal_already_filed", ...)` line deleted (silent short-circuit) | `test_the_short_circuit_still_writes_a_row_so_the_run_is_not_reported_empty` |
| `e.detail.get("cert_end") == cert_end` → `True` (key on the household, not the period) | `test_a_new_certification_period_files_again` |
| `except Exception: return False` → `return True` | `test_an_unreadable_ledger_files_rather_than_skipping` |
| `FILED_KINDS` reduced to `{"renewal_submitted"}` | `test_both_kinds_mean_a_renewal_is_on_file`, `test_the_short_circuit_still_writes_a_row_so_the_run_is_not_reported_empty` |

```bash
git add grace/tools/action.py grace/run.py tests/test_no_duplicate_filing.py tests/test_recorded_constraints.py README.md
git commit -m "fix: Grace files a renewal once per certification period

The gate is pure and cannot read the ledger, so nothing asked whether this
renewal had already been filed. The daily sweep filed each clean household
again every day — twelve renewal_submitted rows each on the live table, all
carrying the same cert_end. Inert while the tool writes a ledger row and
nothing else, and a duplicate submission the moment a real endpoint is
attached.

The obvious fix conflicts with run-scoped classification: a submit_renewal
that returned early WITHOUT writing a row would make the second day's run
look like a run that did nothing, and renewal_filed(since=run_started) would
escalate a clean household. So the short-circuit still writes a row, of kind
renewal_already_filed, and renewal_filed counts both. A run that reached no
outcome at all writes neither and still escalates — the property Task 2
established survives exactly.

Keyed on cert_end, not on the household: a certification that has rolled
over needs a new renewal, and keying on the case id alone would refuse to
file it forever. Fails toward filing on an unreadable ledger, which is the
opposite polarity to every verification path here and deliberate — the gate
has already cleared the case, so the only thing in doubt is duplication, and
a renewal that silently never happens is worse than a duplicate row.

Both demo invariants improve: renewal_submitted stops accumulating and
freezes at its current count, and web/lib/cases.ts's FILED constant is
unchanged, so the dashboard still reads exactly the rows that prove a filing.

Five sabotages watched failing, including the silent short-circuit that would
have reintroduced the conflict."
```

---

## Risks

| Risk | Mitigation |
|---|---|
| **Task 2 makes the demo's headline able to fail, six days before the deadline.** A clean household where the model does not file now escalates rather than reporting `acted`. | Empirically it does not happen — 12 filings across 12 sweeps on every clean case. Step 8 is an explicit gate: run a deployed sweep and confirm 9/3. **If it is not 9/3 that is a finding, not a reason to revert** — reverting restores the blindness rather than fixing the cause. Escalate to the maintainer with the failing case id. |
| **Task 1 changes what `/queue` shows during the demo.** After the fix `c-010` disappears from the queue, because it was approved on 2026-09-04. | This is the correct behaviour and it is *better* for the recording: the queue then shows the two households genuinely awaiting a person. But the video handout's shot list says the queue shows three — update `docs/demo-video-handout.md` in the same commit if it names a count, and re-check `/queue` against the deployed app before recording. |
| Task 2 needs a runtime redeploy to take effect in production. | The change is in `grace/`, which ships in the container image. Note it in the commit and redeploy (`agentcore deploy -y`) before the video; re-run Step 8's sweep afterwards. The dashboard changes in Task 1 ship via Amplify on push. |
| A pre-existing test asserts the old `alreadyDecided` or all-time `renewal_filed` semantics and fails. | Expected and fine. Read the test before changing it — it may be pinning something else — and record in the commit message which assertion moved and why. Never delete a test to make a suite pass. |
| Task 1's extra reads make `/queue` slower. | One extra read per *escalated* household, which is the small set by construction. If the escalated set ever approaches the caseload, the queue is not the bottleneck. |
| `authority.py` gains an import during Task 3 and breaks the purity guard. | No import is needed; `ValueError` is a builtin. `test_authority_imports_only_pure_siblings` is run explicitly in Task 3 Step 5. |

## Out of scope, and why

**A submission id a state endpoint would deduplicate on.** Task 7 stops Grace filing twice; it does not make the filing itself idempotent end to end, because there is no endpoint to be idempotent against. When one is attached it needs its own key, and the ledger check here is not a substitute for it.

**Resolving escalation rows.** Task 1 deliberately compares instants rather than mutating `status`. The next sweep writes a fresh `PENDING` row for a household it still cannot settle, so any status flip is undone within a day, and a mutation would also need `dynamodb:UpdateItem` on the compute role for no gain.

**Re-running the trajectory evals.** They cost real Bedrock calls and none of these tasks touch the gate's ordering, the swarm, or the tool list. Run them before submission if time allows; a failure in `test_an_escalating_case_is_never_filed` would be a real regression and must never be re-run for a pass.

**`swarm.py`, `memory.py`, and `observability.py`.** Not re-derived during the audit. Their recorded findings were internally consistent, but this plan makes no claim about them.
