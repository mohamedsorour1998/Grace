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
import type { CaseDetail, CaseStatus, CaseSummary, Decision, LedgerRow } from "./types";

const LEDGER = "LEDGER#";
const ESCALATION = "ESCALATION#";
const DECISION = "DECISION#";
const PENDING = "PENDING_CASEWORKER";
const FILED = "renewal_submitted";

/** The caseload, as a constant rather than as a discovered set.
 *
 *  There is no index over "every case", and the SSR role deliberately holds no
 *  `dynamodb:Scan` — a bug with Scan could read all 643 ledger rows, and the
 *  audit trail is the one thing this project rests on. So enumeration has to
 *  come from somewhere, and a named constant that is visibly wrong when the
 *  caseload changes is better than a permission that is invisibly dangerous. */
export const CASE_IDS: readonly string[] = Array.from(
  { length: 12 },
  (_, n) => `c-${String(n + 1).padStart(3, "0")}`,
);

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

/** Order two ISO timestamps by the instant they name, not by their spelling.
 *
 *  Real rows are `datetime.isoformat()` output — `2026-09-03T23:39:22.314855+00:00`,
 *  offset-suffixed and microsecond-precision, never `Z`. A string `>` on mixed
 *  spellings inverts: `"...T05:00:01Z" > "...T05:00:01.5+00:00"` is `true`
 *  because `Z` (0x5A) sorts above `.` (0x2E), so the *earlier* row would win a
 *  newest-wins comparison. Plan 2 found the same class of bug in the sort key
 *  itself, where a non-UTC offset sorted bytewise against a UTC one.
 *
 *  An unparseable timestamp sorts as older than everything, so a corrupt row
 *  cannot displace a good one as "newest". */
function instant(v: AttributeValue | undefined): number {
  const t = Date.parse(str(v));
  return Number.isFinite(t) ? t : Number.NEGATIVE_INFINITY;
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

  return [...newest.values()]
    .map((row): CaseSummary => ({
      caseId: str(row.case_id),
      status: "escalated",
      // No escalation row carries a program: measured across all 18 live rows,
      // whose only attributes are pk/sk/case_id/status/escalated_at/deadline/
      // reason/question. `d_program` exists solely on `renewal_submitted`
      // ledger rows, which an escalated case by definition does not have — so
      // for these three households the program is genuinely not in the table,
      // and `""` says so. `listCases` fills it in for the nine that filed.
      program: "",
      deadline: str(row.deadline),
      reason: str(row.reason) || null,
      // NOT MEASURED, and false by construction rather than by evidence: the
      // GSI projects escalation rows only, so this query cannot see whether a
      // `renewal_submitted` row exists. Hard rule 6 says it must not — and a
      // page that wants to *check* that must use `listCases`, which reads the
      // ledger. Do not render this field from `listQueue`.
      filed: false,
    }))
    .sort((a, b) => a.deadline.localeCompare(b.deadline) || a.caseId.localeCompare(b.caseId));
}

/** Every case the ledger knows about, for the sweep summary. */
export async function listCases(client: DynamoDBClient = defaultClient()): Promise<CaseSummary[]> {
  // Read each case rather than merging `listQueue` with a per-case pass. One
  // source means `filed`, `program`, and `deadline` are measured the same way
  // for all twelve, and the 9-acted/3-escalated split on `/` is derived from
  // the ledger — which is what hard rule 6 is actually about. Concurrent
  // because twelve sequential round trips is twelve times the page latency for
  // no benefit; the reads are independent.
  const details = await Promise.all(CASE_IDS.map(id => readCase(id, client)));
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
  let filed = false;
  let program = "";
  let certEnd = "";

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
    }
  }

  for (const d of decisions) {
    const outcome = outcomes.get(d.decidedAt);
    if (outcome !== undefined && outcome !== "") d.outcome = outcome;
  }

  const pending = escalation !== undefined && str(escalation.status) === PENDING;
  // `acted` is a claim that Grace filed, so it needs the ledger row that proves
  // it. Neither pending nor filed is an `error`: something ran and reached no
  // outcome, and `authorize` refuses that as undecidable.
  const status: CaseStatus = pending ? "escalated" : filed ? "acted" : "error";
  return {
    summary: {
      caseId,
      status,
      program,
      // The escalation row's `deadline` and a renewal row's `d_cert_end` are the
      // same fact — the certification end date — recorded by whichever path the
      // case took. Verified equal to the fixture `cert_end` for every case.
      // Without the fallback, all nine acted cases render a dash on `/`.
      deadline: escalation ? str(escalation.deadline) : certEnd,
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
