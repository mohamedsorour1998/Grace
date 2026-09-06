import { describe, expect, it } from "vitest";
import { CaseAlreadyExists, createCase } from "@/lib/create-case";
import { validateIntake, type IntakePermit } from "@/lib/intake";
import type { EnvSource } from "@/lib/env";
import type { SessionIdentity } from "@/lib/types";

/**
 * THE WRITE PATH, WHICH NOTHING TESTED.
 *
 * `lib/create-case.ts` shipped with no test file at all — `grep createCase
 * __tests__/` matched nothing — while its own docstring asserts three
 * properties: that the directory row is written before the record, that the
 * conditional put is the uniqueness check, and that a conditional failure is a
 * different outcome from any other write error. All three are safety arguments,
 * and a docstring asserting a property is not a test of it.
 *
 * The immediate reason for the file is narrower: `createdBy` is the caseworker's
 * opaque `sub`, the permit has carried it since Plan 4, and until now it stopped
 * there — `toRecordItem(permit.record, new Date())` never saw it, so every
 * `RECORD#v1` row was written with no record of who asserted its document
 * status. The provenance line on the case page is only as good as this wire.
 */

const ENV = {
  AWS_REGION: "us-east-1",
  GRACE_TABLE_NAME: "grace-cases",
  GRACE_ESCALATION_INDEX: "escalation-queue",
  GRACE_RUNTIME_ARN: "arn:aws:bedrock-agentcore:us-east-1:1:runtime/grace",
} satisfies EnvSource;
Object.assign(process.env, ENV);

const SUB = "2448a4e8-c021-70f6-382c-e8acbb6cc956";
const NOW = 1_788_400_000_000;

const session: SessionIdentity = { sub: SUB, role: "caseworker", expiresAt: NOW + 3_600_000 };

/** A permit built through the real validator, never hand-rolled.
 *
 *  Hand-building one would let this file assert against a shape `validateIntake`
 *  does not actually produce — the Plan 3 lesson about mocked clients hiding real
 *  response shapes, applied to a value rather than to a response. */
function permit(over: Record<string, unknown> = {}, sub = SUB): IntakePermit {
  const decision = validateIntake({ ...session, sub }, {
    case_id: "c-013",
    program: "medicaid",
    state: "NY",
    cert_end: "2026-12-31",
    language: "en",
    monthly_income_cents: 240_000,
    size: 3,
    documents: [{ id: "proof_of_income", received: "2026-09-01", expires: null }],
    ...over,
  }, NOW);
  if (!decision.permitted) throw new Error(`expected a permit, got ${decision.code}`);
  return decision;
}

interface Put {
  TableName?: string;
  Item?: Record<string, { S?: string; N?: string; L?: unknown[]; NULL?: boolean }>;
  ConditionExpression?: string;
}

/** Records every put, and can fail the way the real service fails. */
class FakeDynamo {
  public puts: Put[] = [];
  constructor(private failRecordWith?: Error) {}
  async send(command: { input: Put }): Promise<unknown> {
    this.puts.push(command.input);
    if (command.input.ConditionExpression !== undefined && this.failRecordWith) {
      throw this.failRecordWith;
    }
    return {};
  }
}

function conditionalFailure(): Error {
  const error = new Error("The conditional request failed");
  // Matched on `name`, not `instanceof` — a bundler ending up with two copies of
  // the client would make a class check fail and report a taken id as an outage.
  error.name = "ConditionalCheckFailedException";
  return error;
}

describe("createCase", () => {
  it("writes the caseworker's opaque id onto the record row", async () => {
    // THE assertion. The permit has carried `createdBy` since Plan 4 and it never
    // reached the row: `toRecordItem` was called with two arguments. Every case
    // submitted through `/new` was stored with no record of who asserted that its
    // documents had been sent, and the case page therefore had nothing to show.
    const fake = new FakeDynamo();
    await createCase(permit(), fake as never);
    const record = fake.puts.find(p => p.ConditionExpression !== undefined);
    expect(record?.Item?.created_by).toEqual({ S: SUB });
    expect(record?.Item?.sk).toEqual({ S: "RECORD#v1" });
  });

  it("carries no household identity onto either row", async () => {
    // Hard rule 9 at the write boundary. `validateIntake` refuses an
    // identity-shaped field and a subject that is not opaque, so this is the
    // end-to-end confirmation that nothing reassembles one downstream.
    const fake = new FakeDynamo();
    await createCase(permit(), fake as never);
    const written = JSON.stringify(fake.puts);
    expect(written).not.toMatch(/@/);
    for (const name of ["Yamamoto", "Fitzgerald", "Mensah"]) {
      expect(written).not.toContain(name);
    }
    expect(written).toContain(SUB);
  });

  it("writes the directory row before the record, and only the record conditionally", async () => {
    // The module's own docstring calls the ordering a safety property. Two puts
    // cannot be made atomic without `TransactWriteItems`, whose permissions this
    // app's compute role does not hold, so one partial failure has to be chosen:
    // record-first leaves a household `open_cases()` never lists — silently absent
    // from the sweep — while directory-first leaves an id that raises with the
    // case id in the message. A visible failure beats a silent omission.
    const fake = new FakeDynamo();
    await createCase(permit(), fake as never);
    expect(fake.puts).toHaveLength(2);
    expect(fake.puts[0]?.Item?.pk).toEqual({ S: "CASE_DIRECTORY" });
    expect(fake.puts[0]?.ConditionExpression).toBeUndefined();
    expect(fake.puts[1]?.Item?.pk).toEqual({ S: "CASE#c-013" });
    expect(fake.puts[1]?.ConditionExpression).toBe("attribute_not_exists(sk)");
  });

  it("reports a taken case id as a conflict, never as an outage", async () => {
    // A distinct type because the two need different answers: a taken id is a 409
    // the caseworker fixes by choosing another, while any other write failure is a
    // 503 they cannot. Reporting an outage as "that id exists" would have them
    // inventing ids until the incident ended.
    const fake = new FakeDynamo(conditionalFailure());
    await expect(createCase(permit(), fake as never)).rejects.toBeInstanceOf(CaseAlreadyExists);
  });

  it("lets any other write failure through as itself", async () => {
    const fake = new FakeDynamo(new Error("ProvisionedThroughputExceededException"));
    await expect(createCase(permit(), fake as never)).rejects.toThrow(/Provisioned/);
  });
});
