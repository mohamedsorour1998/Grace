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
