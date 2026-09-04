import { describe, expect, it } from "vitest";
import { readEnv } from "@/lib/env";
import type { EnvSource } from "@/lib/env";
import { CASE_IDS, listCases, listQueue, readCase, readFacts } from "@/lib/cases";
import type { CaseDetail } from "@/lib/types";

const ENV = {
  AWS_REGION: "us-east-1",
  GRACE_TABLE_NAME: "grace-cases",
  GRACE_ESCALATION_INDEX: "escalation-queue",
  GRACE_RUNTIME_ARN: "arn:aws:bedrock-agentcore:us-east-1:1:runtime/grace",
} satisfies EnvSource;

// `lib/cases.ts` reads `process.env` through `readEnv()` on every call, so the
// suite sets the real variables once rather than mocking the module. Mocking it
// would leave the env-reading path — the one that throws on a blank name —
// untested from `cases.ts`'s side.
Object.assign(process.env, ENV);

/** The slice of DynamoDBClient these functions use, and it can fail the way the
 *  real service fails.
 *
 *  Plan 2's lesson: `FakeTable`'s original float check could never fire, which
 *  made the suite look like it covered a boundary it did not. So this fake
 *  enforces what the live table enforces — a Query needs a `KeyConditionExpression`,
 *  every `:name` placeholder must be defined, an `IndexName` must be one that
 *  exists, and `Scan` is not reachable through it at all (the SSR role holds no
 *  `dynamodb:Scan`, so a Scan would be AccessDenied in production).
 *
 *  Pages are consumed one per `send`, and each page's own `LastEvaluatedKey`
 *  drives the loop — so a fake with two pages genuinely iterates twice, and a
 *  reader that ignored `LastEvaluatedKey` would return only the first page. */
interface Page {
  Items?: Record<string, AttrValue>[];
  LastEvaluatedKey?: Record<string, AttrValue>;
}
type AttrValue = { S: string } | { N: string } | { BOOL: boolean } | { NULL: true };

const INDEXES = new Set(["escalation-queue"]);

class FakeDynamo {
  public sent: Record<string, unknown>[] = [];
  private index = 0;
  constructor(private pages: Page[]) {}

  async send(command: { input: Record<string, unknown> }): Promise<Page> {
    const input = command.input;
    this.sent.push(input);
    if (typeof input.KeyConditionExpression !== "string") {
      throw new Error("ValidationException: Query requires a KeyConditionExpression");
    }
    if (input.TableName !== "grace-cases") {
      throw new Error(`ResourceNotFoundException: ${String(input.TableName)}`);
    }
    if (input.IndexName !== undefined && !INDEXES.has(String(input.IndexName))) {
      throw new Error(`ValidationException: no index ${String(input.IndexName)}`);
    }
    const values = (input.ExpressionAttributeValues ?? {}) as Record<string, unknown>;
    for (const placeholder of String(input.KeyConditionExpression).match(/:\w+/g) ?? []) {
      if (!(placeholder in values)) {
        throw new Error(`ValidationException: undefined value ${placeholder}`);
      }
    }
    const page = this.pages[Math.min(this.index, this.pages.length - 1)];
    this.index += 1;
    return page ?? {};
  }
}

const S = (s: string): AttrValue => ({ S: s });

// Real rows are `datetime.isoformat()` output — `+00:00`, microsecond precision,
// never `Z`. Measured on the live table: `2026-09-03T23:39:22.314855+00:00`.
const AT = "2026-09-03T03:06:05.430742+00:00";

const ledgerRow = (at: string, kind: string, extra: Record<string, AttrValue> = {}, seq = "000001") => ({
  pk: S("CASE#c-011"), sk: S(`LEDGER#${at}#${seq}`),
  case_id: S("c-011"), at: S(at), kind: S(kind),
  // Present with value NULL on 613 of 625 live ledger rows, so it belongs in
  // the default shape rather than in one special-cased test.
  d_trace_id: { NULL: true } as AttrValue, ...extra,
});

const escalationRow = (
  caseId: string, escalatedAt: string, deadline: string, reason = "material_income_change",
) => ({
  pk: S(`CASE#${caseId}`), sk: S(`ESCALATION#${escalatedAt}`), case_id: S(caseId),
  status: S("PENDING_CASEWORKER"), escalated_at: S(escalatedAt),
  // Live escalation rows carry `question` as well as `reason`, and carry no
  // `program` at all.
  reason: S(reason), question: S(reason), deadline: S(deadline),
});

const decisionRow = (decidedAt: string, decision: string, note: string) => ({
  pk: S("CASE#c-011"), sk: S(`DECISION#${decidedAt}`), case_id: S("c-011"),
  decided_by: S("7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d"), decided_at: S(decidedAt),
  decision: S(decision), note: S(note),
});

/** Task 5's own outcome write. Same `DECISION#` prefix, no `decision`. */
const outcomeRow = (decidedAt: string, outcome: string) => ({
  pk: S("CASE#c-011"), sk: S(`DECISION#${decidedAt}#outcome`), case_id: S("c-011"),
  decided_at: S(decidedAt), outcome: S(outcome),
});

/** Narrow by throwing, never by `if`. A `CaseDetail | null` invites
 *  `detail?.ledger`, and every assertion after an optional chain on `null`
 *  silently compares `undefined` — the Task 2 vacuity lesson in its other
 *  TypeScript shape. */
function detailOf(d: CaseDetail | null): CaseDetail {
  if (d === null) throw new Error("expected a CaseDetail, got null");
  return d;
}

describe("readEnv", () => {
  it("throws naming the variable that is missing", () => {
    // A missing table name must fail at startup with a readable message, not
    // as `undefined` inside an SDK call three layers down. Same reasoning as
    // Plan 2's GRACE_STORE allowlist, which raises on blank.
    expect(() => readEnv({ AWS_REGION: "us-east-1" }))
      .toThrow(/GRACE_TABLE_NAME/);
  });

  it("names each required variable in turn, so none is unchecked", () => {
    // A loop, because the draft only ever tested the first one. Dropping
    // `GRACE_RUNTIME_ARN` from `readEnv` left every draft assertion passing.
    const names = ["GRACE_TABLE_NAME", "GRACE_ESCALATION_INDEX", "GRACE_RUNTIME_ARN"];
    for (const name of names) {
      const partial: Record<string, string | undefined> = { ...ENV };
      delete partial[name];
      expect(() => readEnv(partial), `${name} must be required`)
        .toThrow(new RegExp(name));
    }
    expect(names).toHaveLength(3);
  });

  it("rejects a variable that is set but blank, or only whitespace", () => {
    // `process.env.X ?? default` only defaults on absence. Plan 2 found a blank
    // GRACE_STORE bypassing its default and silently discarding a ledger.
    for (const blank of ["", "   ", "\t", "\n"]) {
      expect(() => readEnv({ ...ENV, GRACE_TABLE_NAME: blank }))
        .toThrow(/GRACE_TABLE_NAME/);
    }
  });

  it("trims a padded value instead of handing spaces to the SDK", () => {
    // Checking `value.trim()` and returning `value` accepts `" grace-cases "`
    // and fails later as a ResourceNotFoundException naming a table that looks
    // right in the log line.
    expect(readEnv({ ...ENV, GRACE_TABLE_NAME: "  grace-cases  " }).tableName)
      .toBe("grace-cases");
  });

  it("reads a complete environment", () => {
    const env = readEnv(ENV);
    expect(env.tableName).toBe("grace-cases");
    expect(env.escalationIndex).toBe("escalation-queue");
    expect(env.region).toBe("us-east-1");
  });

  it("defaults only the region, because only the region has a right answer", () => {
    const { AWS_REGION: _drop, ...rest } = ENV;
    expect(readEnv(rest).region).toBe("us-east-1");
  });
});

describe("listQueue", () => {
  it("queries the sparse GSI, not a table scan", async () => {
    const fake = new FakeDynamo([{ Items: [] }]);
    await listQueue(fake as never);
    const input = fake.sent[0]!;
    expect(input.IndexName).toBe("escalation-queue");
    expect(input.KeyConditionExpression).toContain("#s = :s");
    expect(JSON.stringify(input)).toContain("PENDING_CASEWORKER");
    // A Scan would read every ledger row in the table to find three cases.
    expect(JSON.stringify(input)).not.toContain("FilterExpression");
    expect(input.ExpressionAttributeNames).toEqual({ "#s": "status" });
  });

  it("orders by soonest deadline, because that is the caseworker's urgency", async () => {
    // Real deadlines, and one row in the real `+00:00` shape rather than `Z`.
    // Three rows so that GSI order, escalation-time order, and deadline order
    // all differ — which is what makes the assertion distinguish "sorted by
    // deadline" from "whatever the GSI returned".
    const rows = [
      escalationRow("c-010", "2026-09-03T04:20:03.568119+00:00", "2026-10-18", "missing_document"),
      escalationRow("c-011", "2026-09-03T05:00:01+00:00", "2026-10-22"),
      escalationRow("c-012", "2026-09-03T06:00:00+00:00", "2026-10-12", "source_conflict"),
    ];
    const fake = new FakeDynamo([{ Items: rows }]);
    const queue = await listQueue(fake as never);
    expect(queue.map(c => c.caseId)).toEqual(["c-012", "c-010", "c-011"]);
    // GSI order and escalation-time order are both c-010, c-011, c-012 here, so
    // this assertion fails on either — stated so a future edit cannot read the
    // expectation above as a coincidence.
    expect(rows.map(r => r.case_id)).toEqual([S("c-010"), S("c-011"), S("c-012")]);
  });

  it("collapses repeat escalations of one household to a single row", async () => {
    // Every sweep appends a fresh ESCALATION# row, so the GSI legitimately holds
    // 18 rows for 3 households. A queue listing the same family seven times is a
    // caseworker deciding the same case seven times.
    const fake = new FakeDynamo([{ Items: [
      escalationRow("c-010", "2026-09-01T00:00:00+00:00", "2026-10-18", "stale reason"),
      escalationRow("c-010", "2026-09-03T23:39:22.314855+00:00", "2026-10-18", "current reason"),
    ] }]);
    const queue = await listQueue(fake as never);
    expect(queue).toHaveLength(1);
    expect(queue[0]!.caseId).toBe("c-010");
    // The newest escalation wins: it carries the most recent reason. Asserting
    // the length alone would pass on first-wins, which is the wrong row.
    expect(queue[0]!.reason).toBe("current reason");
  });

  it("compares escalation times as instants, not as strings", async () => {
    // `Z` and `+00:00` are both valid ISO 8601 and both parse fine, but they do
    // not sort alike: `Z` (0x5A) is above `.` (0x2E), so a string `>` makes the
    // OLDER `Z` row beat the NEWER offset row. Plan 2 hit the same class of bug
    // in the sort key, where a non-UTC offset sorted bytewise against a UTC one.
    // Reachable here because Grace writes `+00:00` and nothing stops another
    // writer using `Z`.
    //
    // The two stamps must differ WITHIN THE SAME SECOND. An earlier version of
    // this test used 04:00 vs 05:00, where the hour differs before the `Z`/`.`
    // byte is ever reached — so both orderings agreed and the sabotage
    // (`instant` comparing strings) SURVIVED. Measured: `"…T04:00:00Z"` vs
    // `"…T05:00:00.000000+00:00"` agree, `"…T05:00:01Z"` vs
    // `"…T05:00:01.500000+00:00"` disagree. Pick the pair that disagrees.
    const olderZ = "2026-09-03T05:00:01Z";
    const newerOffset = "2026-09-03T05:00:01.500000+00:00";
    expect(olderZ > newerOffset, "the fixture must disagree, or this test cannot fail").toBe(true);
    expect(Date.parse(newerOffset) > Date.parse(olderZ)).toBe(true);

    const fake = new FakeDynamo([{ Items: [
      escalationRow("c-010", olderZ, "2026-10-18", "older, spelled with Z"),
      escalationRow("c-010", newerOffset, "2026-10-18", "newer, spelled with an offset"),
    ] }]);
    const queue = await listQueue(fake as never);
    expect(queue[0]!.reason).toBe("newer, spelled with an offset");
  });

  it("follows LastEvaluatedKey", async () => {
    // Truncation would silently drop households from a work queue.
    const fake = new FakeDynamo([
      { Items: [escalationRow("c-010", "2026-09-03T00:00:00+00:00", "2026-10-18")],
        LastEvaluatedKey: { pk: S("CASE#c-010"), sk: S("ESCALATION#2026-09-03T00:00:00+00:00") } },
      { Items: [escalationRow("c-011", "2026-09-03T00:00:00+00:00", "2026-10-22")] },
    ]);
    const queue = await listQueue(fake as never);
    expect(fake.sent).toHaveLength(2);
    expect(queue.map(c => c.caseId)).toEqual(["c-010", "c-011"]);
    // The second call must carry the first page's key, or the loop re-reads page
    // one forever and only terminates because the fake stops offering a key.
    expect(fake.sent[0]!.ExclusiveStartKey).toBeUndefined();
    expect(fake.sent[1]!.ExclusiveStartKey).toEqual({
      pk: S("CASE#c-010"), sk: S("ESCALATION#2026-09-03T00:00:00+00:00"),
    });
  });

  it("refuses to page forever rather than hanging the request", async () => {
    // An SSR page that hangs is worse than one that errors: Plan 1 Task 6 ran a
    // resume loop to 500 rounds before being killed. A service returning the
    // same LastEvaluatedKey indefinitely must terminate.
    const forever = new FakeDynamo([
      { Items: [escalationRow("c-010", "2026-09-03T00:00:00+00:00", "2026-10-18")],
        LastEvaluatedKey: { pk: S("CASE#c-010") } },
    ]);
    await expect(listQueue(forever as never)).rejects.toThrow(/did not terminate/);
    expect(forever.sent.length).toBeLessThanOrEqual(100);
  });

  it("reports no program rather than inventing one", async () => {
    // Live escalation rows carry no `program` attribute at all — measured across
    // all 18. The draft's `str(row.program, "—")` was structurally always the
    // placeholder, and a data layer returning a presentation dash gives callers
    // a magic value they cannot tell from real data. Task 6 already renders
    // `{summary.deadline || "—"}`.
    const fake = new FakeDynamo([{ Items: [
      escalationRow("c-011", "2026-09-03T05:00:01+00:00", "2026-10-22"),
    ] }]);
    const queue = await listQueue(fake as never);
    expect(queue[0]!.program).toBe("");
    expect(JSON.stringify(queue)).not.toContain("—");
  });

  it("does not claim a renewal was filed, because the GSI cannot see one", async () => {
    // Hard rule 6. The GSI projects escalation rows only, so `filed` here is
    // false by construction rather than by evidence — and it must never be true.
    const fake = new FakeDynamo([{ Items: [
      escalationRow("c-010", "2026-09-03T00:00:00+00:00", "2026-10-18", "missing_document"),
    ] }]);
    expect((await listQueue(fake as never))[0]!.filed).toBe(false);
  });
});

describe("readCase", () => {
  it("returns ledger rows in chronological order", async () => {
    // The evals read ledger position to assert reads precede actions; the
    // caseworker reads it as a story. Both need append order.
    const fake = new FakeDynamo([{ Items: [
      ledgerRow("2026-09-03T03:06:05.430742+00:00", "tool_call", { d_tool: S("read_case") }),
      ledgerRow("2026-09-03T03:06:06.356039+00:00", "tool_result", { d_status: S("success") }, "000002"),
      ledgerRow("2026-09-03T03:06:09.005890+00:00", "family_message_sent", { d_ref: S("recorded:1") }, "000003"),
    ] }]);
    const detail = detailOf(await readCase("c-011", fake as never));
    expect(detail.ledger.map(r => r.kind))
      .toEqual(["tool_call", "tool_result", "family_message_sent"]);
    // Order comes from the sort key, which `infra/naming.py` normalizes to UTC —
    // so the query must ask for ascending range order rather than re-sorting.
    expect(fake.sent[0]!.ScanIndexForward).toBe(true);
    expect(fake.sent[0]!.IndexName).toBeUndefined();
  });

  it("strips the d_ prefix from detail keys", async () => {
    const fake = new FakeDynamo([{ Items: [
      ledgerRow(AT, "tool_call", { d_tool: S("read_case") }),
    ] }]);
    const detail = detailOf(await readCase("c-011", fake as never));
    expect(detail.ledger[0]!.detail.tool).toBe("read_case");
    // NULL must read back as null, not the string "None" — Plan 2's finding.
    // `d_trace_id` is `{"NULL": true}` on 613 of 625 live ledger rows because
    // Runtime never installed an in-process tracer provider. "Not traced" is
    // honest; an error or a "None" string would not be.
    expect(detail.ledger[0]!.detail.trace_id).toBeNull();
    expect("trace_id" in detail.ledger[0]!.detail).toBe(true);
    // Row columns are not detail. `d_`-prefixing exists because `detail` is
    // caller-supplied and could otherwise overwrite `kind`, the field `sweep`
    // classifies a case from.
    for (const key of ["pk", "sk", "at", "kind", "case_id"]) {
      expect(Object.keys(detail.ledger[0]!.detail)).not.toContain(key);
    }
  });

  it("reads a number back at its magnitude, not truncated at the exponent", async () => {
    // boto3's serializer emits Decimal's canonical form, so a large value
    // arrives as `{"N": "1E+30"}` — no `.` in it. The draft chose parseInt on
    // exactly that test, and `parseInt("1E+30", 10)` is **1**: a number read
    // back a factor of 1e30 too small, with no error anywhere. One such row
    // exists live (c-002, the type round-trip row).
    const fake = new FakeDynamo([{ Items: [
      ledgerRow(AT, "tool_result", {
        d_big: { N: "1E+30" }, d_small: { N: "1.5E-9" }, d_zero: { N: "0" },
        d_i: { N: "42" }, d_f: { N: "1.1" }, d_b: { BOOL: true }, d_s: S("text"),
      }),
    ] }]);
    const { detail } = detailOf(await readCase("c-011", fake as never)).ledger[0]!;
    expect(detail.big).toBe(1e30);
    expect(detail.small).toBe(1.5e-9);
    // Zero is a real value a ledger can carry and must not read as null —
    // Plan 1 Task 2's reasoning about `0` never doubling as an absence marker.
    expect(detail.zero).toBe(0);
    expect(detail.i).toBe(42);
    expect(detail.f).toBe(1.1);
    expect(detail.b).toBe(true);
    expect(detail.s).toBe("text");
  });

  it("returns null for a case with no rows at all", async () => {
    const fake = new FakeDynamo([{ Items: [] }]);
    expect(await readCase("c-999", fake as never)).toBeNull();
  });

  it("returns null when the read throws, rather than guessing", async () => {
    const boom = { send: () => Promise.reject(new Error("ProvisionedThroughputExceededException")) };
    expect(await readCase("c-011", boom as never)).toBeNull();
  });

  it("reports acted only with a renewal_submitted row to prove it", async () => {
    // Hard rule 6 at the measurement boundary: "not escalated" is not the same
    // claim as "Grace filed the renewal". `program` and `deadline` come from
    // that row too — it is the only place either is recorded for an acted case.
    const fake = new FakeDynamo([{ Items: [
      ledgerRow(AT, "tool_call", { d_tool: S("submit_renewal") }),
      ledgerRow("2026-09-03T03:35:21.073130+00:00", "renewal_submitted",
        { d_program: S("medicaid"), d_cert_end: S("2026-10-15") }, "000008"),
    ] }]);
    const { summary } = detailOf(await readCase("c-001", fake as never));
    expect(summary.status).toBe("acted");
    expect(summary.filed).toBe(true);
    expect(summary.program).toBe("medicaid");
    expect(summary.deadline).toBe("2026-10-15");
    expect(summary.reason).toBeNull();
  });

  it("reports error, not acted, for a case that ran and reached no outcome", async () => {
    // The draft's `pending ? "escalated" : "acted"` reported a case with no
    // escalation and no renewal as handled autonomously — a family silently
    // counted in the 9 while nothing was filed for them. `error` is what
    // `authorize` already refuses as undecidable, which is why the variant
    // exists at all.
    const fake = new FakeDynamo([{ Items: [
      ledgerRow(AT, "tool_call", { d_tool: S("read_case") }),
      ledgerRow("2026-09-03T03:06:06.356039+00:00", "tool_result", { d_status: S("error") }, "000002"),
    ] }]);
    const { summary } = detailOf(await readCase("c-004", fake as never));
    expect(summary.status).toBe("error");
    expect(summary.filed).toBe(false);
  });

  it("prefers the newest escalation row for the reason it shows", async () => {
    const fake = new FakeDynamo([{ Items: [
      escalationRow("c-011", "2026-09-01T00:00:00+00:00", "2026-10-22", "stale reason"),
      escalationRow("c-011", "2026-09-03T14:16:49.361051+00:00", "2026-10-22", "current reason"),
      ledgerRow(AT, "escalated", { d_question: S("Does the household still qualify?") }),
    ] }]);
    const { summary } = detailOf(await readCase("c-011", fake as never));
    expect(summary.status).toBe("escalated");
    expect(summary.reason).toBe("current reason");
    expect(summary.deadline).toBe("2026-10-22");
  });

  it("picks the newest escalation by instant here too, not by spelling", async () => {
    // `readCase` has its own escalation picker, so the `Z`-vs-offset hazard has
    // to be tested twice — one fixed comparison does not fix the other, and the
    // consequence here is a caseworker reading a stale reason on `/case/[id]`
    // while `/queue` shows the current one.
    const fake = new FakeDynamo([{ Items: [
      escalationRow("c-011", "2026-09-03T05:00:01Z", "2026-10-22", "older, spelled with Z"),
      escalationRow("c-011", "2026-09-03T05:00:01.500000+00:00", "2026-10-22", "newer, spelled with an offset"),
    ] }]);
    expect(detailOf(await readCase("c-011", fake as never)).summary.reason)
      .toBe("newer, spelled with an offset");
  });

  it("does not count Grace's own outcome row as a human decision", async () => {
    // Task 5 writes the outcome to `DECISION#<ts>#outcome`, which also starts
    // with the prefix and carries no `decision` attribute. Counted as a
    // decision it puts a phantom row on the page — a denial attributed to
    // nobody, because `decided_by` is absent — and, worse, an outcome written
    // BEFORE any human decision would make `alreadyDecided` true so the first
    // real decision refuses itself as a duplicate.
    const fake = new FakeDynamo([{ Items: [
      escalationRow("c-011", "2026-09-03T00:00:00+00:00", "2026-10-22"),
      outcomeRow("2026-09-04T01:00:00+00:00", "Grace re-checked and did not file."),
    ] }]);
    const detail = detailOf(await readCase("c-011", fake as never));
    expect(detail.decisions).toHaveLength(0);
  });

  it("attaches an outcome to the decision it belongs to", async () => {
    // The draft read `outcome` off the human decision row, where Task 5 never
    // writes it — so `Decision.outcome` was structurally always null and the
    // page could never show what Grace did after an approval. Join on the
    // shared `decided_at`.
    const fake = new FakeDynamo([{ Items: [
      escalationRow("c-011", "2026-09-03T00:00:00+00:00", "2026-10-22"),
      decisionRow("2026-09-04T01:00:00+00:00", "approve", "Wage record is stale."),
      outcomeRow("2026-09-04T01:00:00+00:00", "Grace re-checked and did not file."),
    ] }]);
    const detail = detailOf(await readCase("c-011", fake as never));
    expect(detail.decisions).toHaveLength(1);
    expect(detail.decisions[0]!.decision).toBe("approve");
    expect(detail.decisions[0]!.note).toBe("Wage record is stale.");
    expect(detail.decisions[0]!.outcome).toBe("Grace re-checked and did not file.");
    expect(detail.decisions[0]!.decidedBy).toBe("7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d");
  });

  it("shows an unrecognised decision word as a deny, and still counts it", async () => {
    // Two properties in one row. Displaying an unknown word as an approval
    // would imply a human authorised a filing they did not — hard rule 5's
    // forbidden direction. And it must still COUNT, or `alreadyDecided` goes
    // false and the case becomes decidable a second time; an allowlist would be
    // the wrong shape here even though it is the right shape in `authorize`.
    const fake = new FakeDynamo([{ Items: [
      escalationRow("c-011", "2026-09-03T00:00:00+00:00", "2026-10-22"),
      decisionRow("2026-09-04T01:00:00+00:00", "Approve", "case-shifted"),
    ] }]);
    const detail = detailOf(await readCase("c-011", fake as never));
    expect(detail.decisions).toHaveLength(1);
    expect(detail.decisions[0]!.decision).toBe("deny");
  });

  it("never surfaces a household name or phone", async () => {
    // Hard rule 9. Even if a row somehow carried one, the reader must not hand
    // it to a page. ALL TWELVE fixture surnames, not the three the draft listed
    // — `c-010` and `c-011` are Fitzgerald and Yamamoto, the two households
    // most likely to carry a name in an escalation reason, and neither was in
    // the draft's pattern.
    const fake = new FakeDynamo([{ Items: [
      escalationRow("c-011", "2026-09-03T00:00:00+00:00", "2026-10-22",
        "material_income_change: Income moved 30.0%"),
      ledgerRow(AT, "escalated", { d_question: S("Does the household still qualify?") }),
      ledgerRow("2026-09-03T03:06:09.005890+00:00", "family_message_sent",
        { d_body: S("We need your proof of residency to complete your renewal.") }, "000002"),
    ] }]);
    const blob = JSON.stringify(detailOf(await readCase("c-011", fake as never)));
    const names = ["Rivera", "Okonkwo", "Nguyen", "Haddad", "Delacroix", "Torres", "Abebe",
      "Silva", "Kowalski", "Fitzgerald", "Yamamoto", "Mensah", "+1555", "Household"];
    for (const n of names) expect(blob, `${n} must not reach a page`).not.toContain(n);
    expect(names).toHaveLength(14);
  });

  it("catches a name fed in through the reason, the path that reached CloudWatch", async () => {
    // Companion to the guard above, and the reason it is not vacuous. The real
    // chain was `read_case` returning `display_name` → a referee quoting it →
    // `_deliberation_note` appending that prose to the escalation reason. Two
    // live rows still held "the Mensah Household" until 2026-09-04. Without
    // this, "no name in this row" is true of every input and proves nothing.
    const fake = new FakeDynamo([{ Items: [
      escalationRow("c-012", "2026-09-03T04:20:03.568119+00:00", "2026-10-12",
        "Does the household size discrepancy allow the Mensah Household to still qualify?"),
    ] }]);
    const blob = JSON.stringify(detailOf(await readCase("c-012", fake as never)));
    expect(blob).toContain("Mensah");
  });

  it("turns a non-terminating per-case read into null, not a hung page", async () => {
    // The other half of the page cap, and the half with the worse failure. In
    // `listQueue` an uncapped loop throws; here `readCase` catches, so without
    // the cap the loop never ends and `/case/[id]` hangs — Plan 1 Task 6's
    // resume loop again, on the SSR request path. A page that renders
    // not-found is recoverable; one that never responds is not.
    const forever = {
      sent: 0,
      async send() {
        this.sent += 1;
        return {
          Items: [ledgerRow(AT, "tool_call", { d_tool: S("read_case") })],
          LastEvaluatedKey: { pk: S("CASE#c-011") },
        };
      },
    };
    expect(await readCase("c-011", forever as never)).toBeNull();
    expect(forever.sent).toBeLessThanOrEqual(100);
    expect(forever.sent).toBeGreaterThan(1);
  });

  it("throws on a misconfigured environment instead of reporting no such case", async () => {
    // `readEnv()` sits OUTSIDE `readCase`'s try on purpose, and until this test
    // existed nothing proved it: moving it inside survives every other
    // assertion in this file. The consequence is specific — a missing
    // GRACE_TABLE_NAME would make all twelve households read back as `null`,
    // so `/` renders an empty caseload and `/case/[id]` renders not-found,
    // on a dashboard that is otherwise healthy and logs nothing. A
    // misconfiguration is not an unreadable case.
    const saved = process.env.GRACE_TABLE_NAME;
    delete process.env.GRACE_TABLE_NAME;
    try {
      const fake = new FakeDynamo([{ Items: [] }]);
      await expect(readCase("c-011", fake as never)).rejects.toThrow(/GRACE_TABLE_NAME/);
      // And it must fail before touching DynamoDB, not after a failed call.
      expect(fake.sent).toHaveLength(0);
    } finally {
      process.env.GRACE_TABLE_NAME = saved;
    }
  });

  it("paginates the per-case read too", async () => {
    // 72 rows for c-010 today and 3 more per daily sweep; a 1MB cap will bite.
    // Dropping the last page drops the NEWEST rows, which is exactly where
    // `renewal_submitted` lives — so a filed renewal would read as unfiled.
    const fake = new FakeDynamo([
      { Items: [ledgerRow(AT, "tool_call", { d_tool: S("submit_renewal") })],
        LastEvaluatedKey: { pk: S("CASE#c-001"), sk: S(`LEDGER#${AT}#000001`) } },
      { Items: [ledgerRow("2026-09-03T03:35:21.073130+00:00", "renewal_submitted",
        { d_program: S("medicaid"), d_cert_end: S("2026-10-15") }, "000008")] },
    ]);
    const { summary, ledger } = detailOf(await readCase("c-001", fake as never));
    expect(fake.sent).toHaveLength(2);
    expect(ledger).toHaveLength(2);
    expect(summary.filed).toBe(true);
  });
});

describe("listCases", () => {
  it("reads every case in the caseload and reports the split from the ledger", async () => {
    // The `/` page's 9-acted/3-escalated claim. Derived per case from the
    // ledger, never from `listQueue` plus an assumption, because `filed` is the
    // hard-rule-6 fact and only the ledger carries it.
    const pages: Page[] = CASE_IDS.map((id, n) => (n < 9
      ? { Items: [{ pk: S(`CASE#${id}`), sk: S(`LEDGER#${AT}#000008`), case_id: S(id),
          at: S(AT), kind: S("renewal_submitted"), d_program: S("medicaid"),
          d_cert_end: S("2026-10-15"), d_trace_id: { NULL: true } as AttrValue }] }
      : { Items: [escalationRow(id, "2026-09-03T00:00:00+00:00", "2026-10-18", "needs a human")] }));
    // Keyed by the pk each call asks for, since listCases reads concurrently and
    // a positional fake would hand the wrong page to the wrong case.
    const byCase = new Map(CASE_IDS.map((id, n) => [`CASE#${id}`, pages[n]!]));
    const fake = {
      sent: [] as Record<string, unknown>[],
      async send(command: { input: Record<string, unknown> }) {
        this.sent.push(command.input);
        const values = command.input.ExpressionAttributeValues as Record<string, { S: string }>;
        return byCase.get(values[":pk"]!.S) ?? { Items: [] };
      },
    };
    const cases = await listCases(fake as never);
    expect(cases).toHaveLength(12);
    expect(cases.map(c => c.caseId)).toEqual([...CASE_IDS]);
    expect(cases.filter(c => c.status === "acted")).toHaveLength(9);
    expect(cases.filter(c => c.status === "escalated")).toHaveLength(3);
    expect(cases.filter(c => c.filed)).toHaveLength(9);
    // No Scan: the SSR role holds none, so a Scan here is AccessDenied live.
    for (const input of fake.sent) expect(input.KeyConditionExpression).toBeDefined();
  });

  it("omits a case with no rows rather than inventing an empty one", async () => {
    const fake = new FakeDynamo([{ Items: [] }]);
    expect(await listCases(fake as never)).toEqual([]);
  });
});

describe("readFacts", () => {
  it("reports escalated and undecided for a queued case", async () => {
    const fake = new FakeDynamo([{ Items: [
      escalationRow("c-011", "2026-09-03T00:00:00+00:00", "2026-10-22"),
    ] }]);
    const facts = await readFacts("c-011", fake as never);
    expect(facts).toEqual({ caseId: "c-011", status: "escalated", alreadyDecided: false });
  });

  it("reports alreadyDecided once a human DECISION row exists", async () => {
    const fake = new FakeDynamo([{ Items: [
      escalationRow("c-011", "2026-09-03T00:00:00+00:00", "2026-10-22"),
      decisionRow("2026-09-04T01:00:00+00:00", "approve", "ok"),
    ] }]);
    expect((await readFacts("c-011", fake as never))?.alreadyDecided).toBe(true);
  });

  it("stays decidable when only Grace's outcome row exists", async () => {
    // The defect this task had to fix rather than leave for Task 5: with a
    // naive prefix test, Grace's own `DECISION#<ts>#outcome` write makes the
    // FIRST human decision on a case look like a duplicate of itself and be
    // refused. Reachable in the one direction that matters, because Task 5
    // writes an outcome even when the re-invocation fails.
    const fake = new FakeDynamo([{ Items: [
      escalationRow("c-011", "2026-09-03T00:00:00+00:00", "2026-10-22"),
      outcomeRow("2026-09-04T01:00:00+00:00", "Grace could not be re-run."),
    ] }]);
    const facts = await readFacts("c-011", fake as never);
    expect(facts?.alreadyDecided).toBe(false);
    expect(facts?.status).toBe("escalated");
  });

  it("returns null when the read throws, rather than guessing", async () => {
    // Fail closed. An unreadable case must not authorise a decision, and
    // `authorize` refuses on null facts.
    const boom = { send: () => Promise.reject(new Error("throttled")) };
    expect(await readFacts("c-011", boom as never)).toBeNull();
  });

  it("carries only the three fields authorize reads", async () => {
    // Same reasoning as `authorize`'s own key assertion: if a reason, a name, or
    // a whole CaseDetail rode along, hard rule 9's surface would widen without
    // anyone choosing to widen it.
    const fake = new FakeDynamo([{ Items: [
      escalationRow("c-011", "2026-09-03T00:00:00+00:00", "2026-10-22"),
    ] }]);
    const facts = await readFacts("c-011", fake as never);
    expect(Object.keys(facts!).sort()).toEqual(["alreadyDecided", "caseId", "status"]);
  });

  it("reports a case Grace filed as not decidable", async () => {
    // `authorize` refuses anything that is not `escalated`, so this is the
    // measurement that keeps a human from retroactively "approving" a filing.
    const fake = new FakeDynamo([{ Items: [
      ledgerRow(AT, "renewal_submitted", { d_program: S("medicaid"), d_cert_end: S("2026-10-15") }),
    ] }]);
    expect((await readFacts("c-001", fake as never))?.status).toBe("acted");
  });
});
