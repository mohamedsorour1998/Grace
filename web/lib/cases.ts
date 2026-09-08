/**
 * THE ONLY DYNAMODB READER. Everything the pages render comes from here.
 *
 * `lib/authorize.ts` decides; this file measures the facts it decides over.
 * The split means every refusal is testable with no AWS, and a route
 * physically cannot hand `authorize` a fact this file did not measure — the
 * same discipline as `grace/authority.py` (pure) against `grace/steering.py`
 * (the adapter).
 *
 * Five behaviours that look like details and are not:
 *
 * **Queries paginate, with a cap.** A DynamoDB Query caps at 1MB and signals
 * more with `LastEvaluatedKey`. Plan 2 hit this three separate times —
 * `ledger()`, `ListMemories`, and the runtime lookup — and in each case
 * truncation was silent. Here a dropped page removes a household from a work
 * queue. The page cap is Plan 1 Task 6's lesson in a different loop: an
 * unbounded `while` on the SSR request path hangs the page rather than failing
 * it, so exhausting the cap throws and `readCase` fails closed.
 *
 * **The queue is de-duplicated by case, newest wins, compared as time.** Every
 * sweep appends a fresh `ESCALATION#` row, so the GSI legitimately holds 18
 * rows for 3 households. A caseworker must see three.
 *
 * **A failed read returns `null`, never a guess.** `authorize` refuses on null
 * facts, so an unreadable case cannot be decided. That is the fail-closed
 * direction Tasks 3 and 4 of Plan 1 established for the gate itself.
 *
 * **`acted` requires evidence, and a case with neither is an `error`.** Hard
 * rule 6 in the other direction: "not escalated" is not the same claim as
 * "Grace filed the renewal". A case with no pending escalation and no
 * `renewal_submitted` row is reported `error`, which `authorize` already
 * refuses as undecidable — the variant is otherwise unreachable, which would
 * make a shipped and tested guard dead code.
 *
 * **Placeholders belong to the renderer, not here.** An unknown program or
 * deadline reads back as `""`, never `"—"`. A presentation dash inside the data
 * layer is a magic value a caller cannot tell from real data, and Task 6
 * already writes `{summary.deadline || "—"}`. Same division of labour as
 * `authority.py` leaving escaping to whichever surface renders `detail`.
 */

import { DynamoDBClient, QueryCommand } from "@aws-sdk/client-dynamodb";
import type { AttributeValue, QueryCommandInput } from "@aws-sdk/client-dynamodb";
import { readEnv } from "./env";
import type { CaseFacts } from "./authorize";
import type {
  CaseDetail,
  CaseRecordFacts,
  CaseStatus,
  CaseSummary,
  Decision,
  LedgerRow,
  RecordDocument,
} from "./types";

const LEDGER = "LEDGER#";
const ESCALATION = "ESCALATION#";
const DECISION = "DECISION#";
const RECORD = "RECORD#";
const PENDING = "PENDING_CASEWORKER";
const FILED = "renewal_submitted";

/** The twelve households `fixtures/households.yaml` seeds, as a constant.
 *
 *  Still a constant, and still for the original reason: there is no index over
 *  "every case" and the SSR role deliberately holds no `dynamodb:Scan`, because
 *  a bug with Scan could read the whole audit trail. What changed in Plan 4 is
 *  that this is no longer the *whole* caseload — a case submitted through
 *  `/new` is discovered from the directory partition below. This list survives
 *  as the floor: the twelve the demo's evidence rests on are listed even before
 *  seeding has run, so a dashboard cannot come up showing an empty caseload
 *  because one provisioning step was skipped. */
export const SEEDED_CASE_IDS: readonly string[] = Array.from(
  { length: 12 },
  (_, n) => `c-${String(n + 1).padStart(3, "0")}`,
);

/** The partition that enumerates every case record. See `infra/naming.py`. */
const CASE_DIRECTORY_PK = "CASE_DIRECTORY";

/** Refuse to spin. 643 rows live in the whole table today and the largest
 *  single case holds 72, so any real query finishes in one page; a hundred is
 *  unreachable by data and reachable only by a service returning the same
 *  `LastEvaluatedKey` forever. Throwing beats truncating, because `readCase`
 *  turns a throw into `null` and a truncation into a confident wrong answer. */
const MAX_PAGES = 100;

let shared: DynamoDBClient | undefined;
function defaultClient(): DynamoDBClient {
  shared ??= new DynamoDBClient({ region: readEnv().region });
  return shared;
}

/** Read one attribute as a plain value. `NULL` becomes `null`, never the string
 *  "None" — Plan 2's round-trip finding, and the reason it matters here is that
 *  `d_trace_id` is `{"NULL": true}` on 613 of 625 live ledger rows. Runtime
 *  never installed an in-process tracer provider, so "not traced" is the honest
 *  reading and the dashboard must render it as such rather than as an error. */
function plain(v: AttributeValue | undefined): string | number | boolean | null {
  if (v === undefined) return null;
  if (v.NULL) return null;
  if (v.S !== undefined) return v.S;
  if (v.BOOL !== undefined) return v.BOOL;
  // `Number()`, not `parseInt`/`parseFloat` chosen by a `.` test. Python writes
  // these through boto3's serializer, which emits `Decimal`'s canonical form —
  // measured: `1e30` arrives as `{"N": "1E+30"}` and `-1e21` as `{"N": "-1E+21"}`,
  // neither of which contains a `.`. `parseInt("1E+30", 10)` is **1**, so a
  // large number would read back as a small one with no error anywhere.
  if (v.N !== undefined) {
    const n = Number(v.N);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function str(v: AttributeValue | undefined, fallback = ""): string {
  const p = plain(v);
  return typeof p === "string" ? p : fallback;
}

/** An ISO stamp as milliseconds, or `-Infinity` when it is unparseable.
 *
 *  Order two ISO timestamps by the instant they name, never by their
 *  spelling: `Z` (0x5A) sorts above `.` (0x2E), so
 *  `"…T05:00:01Z" > "…T05:00:01.500000+00:00"` is `true` as a string
 *  comparison while the offset row is the *later* instant. This is reachable,
 *  not theoretical, because two different writers use two different spellings
 *  on the same table: Grace's Python side (`infra/naming.py`,
 *  `grace/ledger.py`) writes microsecond, offset-suffixed stamps —
 *  `2026-09-03T23:39:22.314855+00:00` — for every escalation and ledger row,
 *  while `web/lib/decide.ts`'s `utcStamp()` (`new Date().toISOString()`)
 *  writes the `Z` spelling for `decided_at` on every human decision row. A
 *  decision's stamp and an escalation's stamp must therefore be compared on
 *  the same scale rather than by whichever format each happened to use. Plan
 *  2 found the same class of bug in the sort key itself, where a non-UTC
 *  offset sorted bytewise against a UTC one.
 *
 *  An unparseable timestamp sorts as older than everything, so a corrupt row
 *  cannot displace a good one as "newest". */
function parseInstant(value: string): number {
  const t = Date.parse(value);
  return Number.isFinite(t) ? t : Number.NEGATIVE_INFINITY;
}

/** The `AttributeValue` wrapper over `parseInstant`, for a stamp read
 *  straight off a DynamoDB row rather than off an already-parsed field like
 *  `Decision.decidedAt`. */
function instant(v: AttributeValue | undefined): number {
  return parseInstant(str(v));
}

/** The `RECORD#v1` row, as the provenance the case page renders.
 *
 *  **A malformed document entry is skipped, and that is the opposite of the
 *  Python reader's posture on purpose.** `grace/cases/record.py` raises, because
 *  a document the *gate* cannot parse must never be silently dropped — a
 *  dropped entry moves a household from `escalate` to `act`. Nothing here
 *  reaches the gate: this is a read-only render of what a caseworker asserted,
 *  and the sweep decides from its own read of the same row. So a garbled entry
 *  costs one missing line on a page, while throwing would cost the whole case
 *  page (`readCase` catches and returns `null`, so `/case/c-010` would 404 for a
 *  household that is perfectly readable to Grace). The dangerous direction here
 *  is losing the page, not losing a line.
 *
 *  `received` becomes `sent`, which is the whole vocabulary change: the family
 *  sends documents to the state, and Grace holds none of them. */
function recordFacts(row: Record<string, AttributeValue>): CaseRecordFacts {
  const documents: RecordDocument[] = [];
  for (const entry of row.documents?.L ?? []) {
    const fields = entry.M;
    if (fields === undefined) continue;
    const id = str(fields.id);
    const sent = str(fields.received);
    if (id === "" || sent === "") continue;
    documents.push({ id, sent, expires: str(fields.expires) || null });
  }
  return {
    // `""` when nobody asserted, which is the seeded twelve. Never a
    // placeholder that looks like an id — the renderer decides what to say about
    // an absence, the same division of labour as the deadline dash.
    createdBy: str(row.created_by),
    createdAt: str(row.created_at),
    documents,
  };
}

async function queryAll(
  client: DynamoDBClient,
  input: QueryCommandInput,
): Promise<Record<string, AttributeValue>[]> {
  const items: Record<string, AttributeValue>[] = [];
  let startKey: Record<string, AttributeValue> | undefined;
  let pages = 0;
  do {
    if (pages >= MAX_PAGES) {
      throw new Error(
        `Query on ${input.TableName} did not terminate within ${MAX_PAGES} pages.`,
      );
    }
    const page = await client.send(
      new QueryCommand(startKey === undefined ? input : { ...input, ExclusiveStartKey: startKey }),
    );
    pages += 1;
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

/** Every case id, from the directory partition and the seeded twelve.
 *
 *  A **union**, so neither source can shrink the caseload: a household seeded
 *  into the table but somehow missing from the directory is still listed, and a
 *  case submitted through `/new` appears without anyone editing a constant.
 *  `grace/cases/dynamo_store.py`'s `open_cases()` unions the same two sources
 *  for the same reason, so the dashboard and the agent enumerate alike.
 *
 *  **A read failure propagates rather than falling back to the twelve.** That
 *  is deliberate and it is the same call `readEnv()` sitting outside `readCase`'s
 *  `try` already makes: a page that quietly rendered a shorter caseload would
 *  look healthy while hiding households, and a caseworker cannot tell a caseload
 *  of twelve from a caseload of thirteen with one silently dropped. A page that
 *  errors is recoverable; a page that under-reports is not. */
export async function listCaseIds(
  client: DynamoDBClient = defaultClient(),
): Promise<string[]> {
  const env = readEnv();
  const rows = await queryAll(client, {
    TableName: env.tableName,
    KeyConditionExpression: "pk = :pk",
    ExpressionAttributeValues: { ":pk": { S: CASE_DIRECTORY_PK } },
    ScanIndexForward: true,
  });
  const ids = new Set<string>(SEEDED_CASE_IDS);
  for (const row of rows) {
    const id = str(row.case_id);
    if (id !== "") ids.add(id);
  }
  return [...ids].sort();
}

/** Every case the ledger knows about, for the sweep summary. */
export async function listCases(client: DynamoDBClient = defaultClient()): Promise<CaseSummary[]> {
  // Read each case rather than merging `listQueue` with a per-case pass. One
  // source means `filed`, `program`, and `deadline` are measured the same way
  // for all twelve, and the 9-acted/3-escalated split on `/` is derived from
  // the ledger — which is what hard rule 6 is actually about. Concurrent
  // because twelve sequential round trips is twelve times the page latency for
  // no benefit; the reads are independent.
  const details = await Promise.all(
    (await listCaseIds(client)).map(id => readCase(id, client)),
  );
  return details
    .filter((d): d is CaseDetail => d !== null)
    .map(d => d.summary)
    .sort((a, b) => a.caseId.localeCompare(b.caseId));
}

/** One household: its ledger, its decisions, and what Grace concluded. */
export async function readCase(
  caseId: string,
  client: DynamoDBClient = defaultClient(),
): Promise<CaseDetail | null> {
  // Outside the `try` on purpose. A missing environment variable is a
  // misconfiguration, not an unreadable case, and collapsing it to `null` would
  // report every household as "no such case" on a dashboard that looks healthy.
  const env = readEnv();
  let rows: Record<string, AttributeValue>[];
  try {
    rows = await queryAll(client, {
      TableName: env.tableName,
      KeyConditionExpression: "pk = :pk",
      ExpressionAttributeValues: { ":pk": { S: `CASE#${caseId}` } },
      // Sort-key order is chronological because `infra/naming.py` normalizes
      // every stamp to UTC before building it, so DynamoDB's bytewise range
      // comparison is a time comparison. That is why the ledger needs no
      // client-side sort — and why the test asserts this flag rather than
      // shuffling its fixture.
      ScanIndexForward: true,
    });
  } catch {
    // Fail closed: an unreadable case is not a decidable one.
    return null;
  }
  if (rows.length === 0) return null;

  const ledger: LedgerRow[] = [];
  const decisions: Decision[] = [];
  const outcomes = new Map<string, string>();
  let escalation: Record<string, AttributeValue> | undefined;
  let record: CaseRecordFacts | null = null;
  let filed = false;
  let program = "";
  let certEnd = "";
  let recordProgram = "";
  let recordCertEnd = "";

  for (const row of rows) {
    const sk = str(row.sk);
    if (sk.startsWith(LEDGER)) {
      const detail: LedgerRow["detail"] = {};
      for (const [key, value] of Object.entries(row)) {
        if (key.startsWith("d_")) detail[key.slice(2)] = plain(value);
      }
      const kind = str(row.kind);
      if (kind === FILED) {
        filed = true;
        // The only real source for either field. An escalated case has no
        // `renewal_submitted` row, so it has no program in the table at all.
        program = str(row.d_program) || program;
        certEnd = str(row.d_cert_end) || certEnd;
      }
      ledger.push({ at: str(row.at), kind, detail });
    } else if (sk.startsWith(DECISION)) {
      // `startsWith(DECISION)` is NOT sufficient on its own. Task 5 writes
      // Grace's own outcome to `DECISION#<ts>#outcome`, which also starts with
      // the prefix and carries no `decision` attribute. Counted as a decision
      // it would put a phantom second row on the page — a denial attributed to
      // nobody, because `decided_by` is absent and the `decision` fallback is
      // "deny" — next to the approval a caseworker actually made. An audit
      // trail that invents a decision is worse than one that omits an outcome.
      //
      // So discriminate on the presence of `decision`, and attach the outcome
      // to the human row it belongs to by its shared `decided_at`. The draft
      // read `outcome` off the human row, where it is never written, which made
      // `Decision.outcome` structurally always `null`.
      const decision = str(row.decision);
      if (decision === "") {
        const at = str(row.decided_at);
        if (at !== "") outcomes.set(at, str(row.outcome));
        continue;
      }
      decisions.push({
        decidedAt: str(row.decided_at),
        decidedBy: str(row.decided_by),
        // An allowlist would be the wrong shape here: an unrecognised word must
        // still *count* as a decision, or `alreadyDecided` goes false and the
        // case becomes decidable a second time. Falling back to "deny" is the
        // cautious display — showing an approval no human made would imply they
        // authorised a filing, which is hard rule 5's forbidden direction.
        decision: decision === "approve" ? "approve" : "deny",
        note: str(row.note),
        outcome: null,
      });
    } else if (sk.startsWith(ESCALATION)) {
      if (!escalation || instant(row.escalated_at) > instant(escalation.escalated_at)) {
        escalation = row;
      }
    } else if (sk.startsWith(RECORD)) {
      // One row per case, versioned in the sort key (`RECORD#v1`) so a future v2
      // shape is a new row an old reader does not match rather than a changed row
      // it misparses. `startsWith` rather than an equality test for that reason,
      // and the last one wins — v2 sorts after v1.
      record = recordFacts(row);
      // The record row is a second, independent source for both of these, and
      // Plan 4 put it in the table after the comments below were written. It is
      // a *fallback*, never an override: a `renewal_submitted` ledger row is
      // evidence of what Grace actually filed under, while a record row is what
      // was submitted, and where they disagree the evidence wins.
      recordProgram = str(row.program);
      recordCertEnd = str(row.cert_end);
    }
  }

  for (const d of decisions) {
    const outcome = outcomes.get(d.decidedAt);
    if (outcome !== undefined && outcome !== "") d.outcome = outcome;
  }

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

  const pending = escalation !== undefined && str(escalation.status) === PENDING;
  // A submitted case that no sweep has looked at: a record row and nothing else.
  // `new` was declared in `lib/types.ts`, rendered in `case-table.tsx`, and
  // documented in the README as what such a case shows — and nothing produced
  // it, because `readCase` never read the record row. The consequence was not
  // cosmetic: a case created through `/new` fell into `error`, whose message is
  // "Grace's last run on this case reached no outcome — re-run the sweep", a
  // false claim about a run that never happened. Requiring an *absent* ledger as
  // well as an absent escalation keeps the two apart: a sweep that ran and
  // reached no outcome leaves ledger rows behind and is still an `error`.
  const unswept = record !== null && ledger.length === 0 && escalation === undefined;
  // `acted` is a claim that Grace filed, so it needs the ledger row that proves
  // it. Neither pending nor filed nor unswept is an `error`: something ran and
  // reached no outcome, and `authorize` refuses that as undecidable.
  const status: CaseStatus = pending
    ? "escalated"
    : filed
      ? "acted"
      : unswept
        ? "new"
        : "error";
  return {
    record,
    decidedSinceEscalation,
    summary: {
      caseId,
      status,
      // Evidence first, then the record. `d_program` exists only on a
      // `renewal_submitted` ledger row, so before the record row was read here
      // an escalated case had no program at all and `/case/c-010` rendered a
      // dash — which the comment beside `listQueue` still describes as "genuinely
      // not in the table". That was true until Plan 4 put a `RECORD#v1` row
      // there. `listQueue` reads the GSI and still cannot see it; this reads the
      // whole partition and can.
      program: program || recordProgram,
      // The escalation row's `deadline`, a renewal row's `d_cert_end`, and the
      // record row's `cert_end` are the same fact — the certification end date —
      // recorded by whichever path the case took. Verified equal to the fixture
      // `cert_end` for every case. Without the fallbacks, all nine acted cases
      // rendered a dash on `/`, and a newly submitted case renders one for the
      // deadline that is the entire reason Grace exists.
      deadline: escalation ? str(escalation.deadline) : certEnd || recordCertEnd,
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
    // Scoped to the newest escalation, not to the case's whole history. A
    // household re-escalated after a decision is decidable again, because the
    // sweep found the same problem unresolved and a human should answer for
    // the current escalation rather than be told they already did.
    alreadyDecided: detail.decidedSinceEscalation,
  };
}
