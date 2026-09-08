/**
 * `/` AND `/queue` CANNOT DISAGREE ABOUT "WAITING ON YOU".
 *
 * They did. `/` counted every escalated household and `/queue` counted the ones
 * still undecided, so the moment a caseworker answered a case the two pages
 * reported **3** and **2** for the same phrase. Neither number was wrong, which
 * is what made it worse than a bug: a reader cannot audit two defensible
 * answers, they can only stop trusting the page.
 *
 * The fix is structural rather than arithmetic. `CaseSummary.awaitingDecision`
 * is computed once in `lib/cases.ts`, `summarise` counts it, and `listQueue`
 * filters on it — so the two surfaces read one field and the disagreement is
 * unrepresentable. These tests assert the equality directly, because a test
 * that only checked `summarise` in isolation would pass with the two sides
 * drifting apart again.
 */
import { describe, expect, it, vi } from "vitest";
import type { AttributeValue } from "@aws-sdk/client-dynamodb";
import { summarise } from "@/components/case-table";

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

function escalation(caseId: string, at: string): Record<string, AttributeValue> {
  return {
    pk: S(`CASE#${caseId}`), sk: S(`ESCALATION#${at}`), case_id: S(caseId),
    status: S("PENDING_CASEWORKER"), escalated_at: S(at),
    deadline: S("2026-10-18"), reason: S("missing_document: proof_of_residency"),
    question: S("missing_document: proof_of_residency"),
  };
}

function decision(caseId: string, at: string): Record<string, AttributeValue> {
  return {
    pk: S(`CASE#${caseId}`), sk: S(`DECISION#${at}`), case_id: S(caseId),
    decided_at: S(at), decided_by: S("2448a4e8-0000-4000-8000-000000000000"),
    decision: S("approve"), note: S(""),
  };
}

/** A `renewal_submitted` ledger row — the evidence `acted` requires. */
function filing(caseId: string, at: string): Record<string, AttributeValue> {
  return {
    pk: S(`CASE#${caseId}`), sk: S(`LEDGER#${at}#000001`), case_id: S(caseId),
    at: S(at), kind: S("renewal_submitted"),
    d_program: S("medicaid"), d_cert_end: S("2026-10-18"),
  };
}

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

/** Three escalated households; `c-010` answered after its newest escalation.
 *  This is the live shape that produced 3-against-2. */
const ESCALATED_AT = "2026-09-08T04:00:00.000000+00:00";
const DECIDED_AT = "2026-09-08T05:00:00.000Z";

function caseload(decided: boolean) {
  const partitions: Record<string, Record<string, AttributeValue>[]> = {};
  for (let n = 1; n <= 9; n += 1) {
    const id = `c-00${n}`;
    partitions[id] = [filing(id, "2026-09-08T03:00:00.000000+00:00")];
  }
  for (const id of ["c-010", "c-011", "c-012"]) {
    partitions[id] = [escalation(id, ESCALATED_AT)];
  }
  if (decided) partitions["c-010"]!.push(decision("c-010", DECIDED_AT));
  const queue = ["c-010", "c-011", "c-012"].map(id => escalation(id, ESCALATED_AT));
  return { queue, partitions };
}

describe("the two pages report one number for one question", () => {
  it("agrees before any decision — the demo's own 9/3 state", async () => {
    const { listCases, listQueue } = await load();
    const { queue, partitions } = caseload(false);

    const sweep = summarise(await listCases(fakeClient(queue, partitions) as never));
    const waiting = await listQueue(fakeClient(queue, partitions) as never);

    expect(sweep.acted).toBe(9);
    expect(sweep.escalated).toBe(3);
    expect(sweep.awaiting).toBe(3);
    expect(sweep.answered).toBe(0);
    expect(waiting).toHaveLength(sweep.awaiting);
  });

  it("still agrees after a decision — the case that used to read 3 against 2", async () => {
    const { listCases, listQueue } = await load();
    const { queue, partitions } = caseload(true);

    const sweep = summarise(await listCases(fakeClient(queue, partitions) as never));
    const waiting = await listQueue(fakeClient(queue, partitions) as never);

    // Grace's verdict is unchanged: it escalated three and still would.
    expect(sweep.escalated).toBe(3);
    // The working state moved, and both surfaces moved together.
    expect(sweep.awaiting).toBe(2);
    expect(sweep.answered).toBe(1);
    expect(waiting).toHaveLength(sweep.awaiting);
    expect(waiting.map(c => c.caseId)).toEqual(["c-011", "c-012"]);
  });

  it("keeps every household in exactly one bucket", async () => {
    // Plan 1 Task 6's partition rule. A case counted twice, or counted nowhere,
    // makes a total that still looks plausible.
    const { listCases } = await load();
    const { queue, partitions } = caseload(true);
    const sweep = summarise(await listCases(fakeClient(queue, partitions) as never));

    expect(sweep.acted + sweep.escalated + sweep.incomplete).toBe(sweep.total);
    expect(sweep.awaiting + sweep.answered).toBe(sweep.escalated);
  });
});

describe("awaitingDecision is the field both surfaces read", () => {
  it("is false for a household answered since its newest escalation", async () => {
    const { readCase } = await load();
    const { queue, partitions } = caseload(true);
    const detail = await readCase("c-010", fakeClient(queue, partitions) as never);
    expect(detail!.summary.status).toBe("escalated");
    expect(detail!.summary.awaitingDecision).toBe(false);
  });

  it("is true again once a later sweep re-escalates the same household", async () => {
    // The whole lifecycle: answered, cleared, and back when the situation
    // persists. The document really is still missing.
    const { readCase } = await load();
    const { queue, partitions } = caseload(true);
    partitions["c-010"]!.push(escalation("c-010", "2026-09-09T04:00:00.000000+00:00"));
    const detail = await readCase("c-010", fakeClient(queue, partitions) as never);
    expect(detail!.summary.awaitingDecision).toBe(true);
  });

  it("is never true for a household Grace handled itself", async () => {
    const { readCase } = await load();
    const { queue, partitions } = caseload(false);
    const detail = await readCase("c-001", fakeClient(queue, partitions) as never);
    expect(detail!.summary.status).toBe("acted");
    expect(detail!.summary.awaitingDecision).toBe(false);
  });

  it("is true on a degraded row whose partition could not be read", async () => {
    // Fail toward showing the household. One wrongly listed costs a caseworker
    // a click; one wrongly hidden costs a family its coverage.
    const { listQueue } = await load();
    const client = {
      send: vi.fn(async (command: { input: Record<string, unknown> }) => {
        if (command.input.IndexName) {
          return { Items: [escalation("c-012", ESCALATED_AT)] };
        }
        throw new Error("partition unavailable");
      }),
    };
    const waiting = await listQueue(client as never);
    expect(waiting).toHaveLength(1);
    expect(waiting[0]!.awaitingDecision).toBe(true);
  });
});
