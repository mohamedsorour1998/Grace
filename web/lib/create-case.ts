/**
 * THE SECOND WRITE IN THIS APPLICATION, and the first that creates anything.
 *
 * A submitted case has to become a row the **deployed agent** reads, not just
 * one the dashboard renders. Until Plan 4 the agent's view of which households
 * exist came from `fixtures/households.yaml` baked into its container image, so
 * a case written here would have shown on screen and been invisible to Grace —
 * the "looks like it works" failure this project has spent three plans
 * eliminating. `grace/cases/dynamo_store.py` now reads records from the table,
 * and this writes them in the shape `grace/cases/record.py` parses.
 *
 * **Two rows, and the order is a safety property.** The directory row is
 * written first, then the record under `attribute_not_exists(sk)`. Two puts
 * cannot be made atomic without `TransactWriteItems`, whose permissions neither
 * the runtime role nor this app's compute role holds, so one of the two partial
 * failures has to be chosen:
 *
 * - Record first, directory second: a household the table can answer `get()`
 *   for and that `open_cases()` never lists — silently absent from the sweep.
 * - Directory first, record second: an id `open_cases()` names and cannot load,
 *   which raises with the case id in the message.
 *
 * A visible failure beats a silent omission, and the retry is safe because the
 * directory write carries only a case id and is idempotent. The Python store
 * writes them in the same order, for the same reason.
 *
 * **The conditional put is the uniqueness check.** Not a read-then-write: two
 * submissions of the same id would both read absent and both write, and the
 * second would overwrite a case record whose ledger, escalation, and decision
 * history live in that same partition. That history would then belong to two
 * different families with nothing in the audit trail saying so.
 */

import { DynamoDBClient, PutItemCommand } from "@aws-sdk/client-dynamodb";
import { readEnv } from "./env";
import { toDirectoryItem, toRecordItem, type IntakePermit } from "./intake";

export interface CreateOutcome {
  created: true;
  caseId: string;
}

/** Thrown when the case id is already taken. A distinct type because a taken id
 *  is a 409 the caseworker can fix by choosing another, while any other write
 *  failure is a 503 they cannot — reporting an outage as "that id exists" would
 *  have them inventing new ids until the incident ended. */
export class CaseAlreadyExists extends Error {
  constructor(public readonly caseId: string) {
    super(`A case record already exists for ${caseId}.`);
    this.name = "CaseAlreadyExists";
  }
}

export async function createCase(
  permit: IntakePermit,
  client: DynamoDBClient = new DynamoDBClient({ region: readEnv().region }),
): Promise<CreateOutcome> {
  const env = readEnv();
  const { caseId } = permit.record;

  await client.send(new PutItemCommand({
    TableName: env.tableName,
    Item: toDirectoryItem(caseId),
  }));

  try {
    await client.send(new PutItemCommand({
      TableName: env.tableName,
      Item: toRecordItem(permit.record, new Date()),
      ConditionExpression: "attribute_not_exists(sk)",
    }));
  } catch (error) {
    // Matched on `name`, which every modelled SDK error carries, rather than on
    // `instanceof` — a bundler that ends up with two copies of the client would
    // make the class identity check fail and report a taken id as an outage.
    if (error instanceof Error && error.name === "ConditionalCheckFailedException") {
      throw new CaseAlreadyExists(caseId);
    }
    throw error;
  }

  return { created: true, caseId };
}
