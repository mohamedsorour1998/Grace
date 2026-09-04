/**
 * THE ONLY WRITE IN THIS APPLICATION.
 *
 * A caseworker's decision becomes a durable row, and then — for an approve —
 * Grace re-evaluates the case. **The re-evaluation goes through the authority
 * gate**, which may refuse again: approving a household that is still missing a
 * document files nothing, because the document is still missing. Verified on the
 * Python side by `tests/test_entrypoint_approval.py`, structurally rather than
 * behaviourally — `evaluate(case, today, pack=None)` has no parameter an
 * approval could occupy.
 *
 * No interrupt is resumed here, and the words that would do it appear nowhere in
 * this file. Plan 1's Task 6 proved that resuming with any truthy response
 * *approves the blocked tool* — "needs review" filed a renewal for `c-010`. The
 * deployed entrypoint has no resume path at all, and this is the request that
 * would have been tempted to add one.
 *
 * **The row is written before the invocation**, which is the opposite of
 * `grace/tools/action.py`. Both are right, because they claim different things: a
 * ledger row claims *Grace did something*, true only once a tool returned (hard
 * rule 6); this row claims *a human decided*, true the moment they clicked.
 * Losing that to an infrastructure error would discard the caseworker's work and
 * leave the case silently unresolved.
 *
 * **The response body is a stream, not a `Uint8Array`.** Measured against the
 * real client: `InvokeAgentRuntimeResponse.response` is typed `StreamingBlobTypes`
 * and arrives as a Node `IncomingMessage` carrying `sdkStreamMixin`, so
 * `new TextDecoder().decode(response.response)` — which the plan's draft did —
 * throws `TypeError: The "list" argument must be an instance of ... ArrayBufferView`.
 * That would have been caught by this module's own `catch`, turning **every**
 * approve into "Grace could not be re-run" while the invocation had in fact
 * already run to completion. Nothing would have errored, no test that mocks the
 * client would have noticed, and the caseworker would be told Grace failed on a
 * case Grace had actually just decided. `transformToString()` is the supported
 * read.
 */

import { DynamoDBClient, PutItemCommand } from "@aws-sdk/client-dynamodb";
import {
  BedrockAgentCoreClient,
  InvokeAgentRuntimeCommand,
} from "@aws-sdk/client-bedrock-agentcore";
import { readEnv } from "./env";
import type { Permit } from "./authorize";

export interface DecisionOutcome {
  recorded: true;
  caseId: string;
  decision: "approve" | "deny";
  graceOutcome: string;
  filed: boolean;
}

interface Clients {
  dynamo?: DynamoDBClient;
  runtime?: BedrockAgentCoreClient;
}

/** The date every Grace surface pins to. Never a live clock.
 *
 *  `grace/entrypoint.py`'s `DEFAULT_TODAY` is the same value and for the same
 *  reason: fixture `c-002`'s SNAP grace period ends 2026-10-30, so a real
 *  `date.today()` turns the 9-act/3-escalate demo into 8/4 from 2026-10-31. The
 *  entrypoint would default to this anyway if the key were absent; it is sent
 *  explicitly so the dashboard and the sweep cannot disagree about which day
 *  they are evaluating, which is the kind of difference that produces two
 *  correct-looking answers. */
const PINNED_TODAY = "2026-10-01";

/** Refuse to hang. 870s mirrors the Lambda's budget from Plan 2 and clears the
 *  512s a real run has been measured at. */
const INVOKE_TIMEOUT_MS = 870_000;

/** UTC, always. Plan 2 established that a non-UTC offset sorts a later instant
 *  *before* an earlier one when DynamoDB compares the sort key bytewise.
 *  `toISOString()` is UTC by definition and always emits the `Z` suffix, so no
 *  normalisation step is needed here the way `infra/naming.py` needs one — that
 *  function receives an arbitrary aware datetime, this one reads the clock. */
function utcStamp(): string {
  return new Date().toISOString();
}

/** Read the runtime's JSON body.
 *
 *  `response` is a streaming blob. The SDK attaches `transformToString` to it
 *  (`sdkStreamMixin`), which is the only supported way to collect it — see the
 *  module docstring for what decoding it as bytes actually does. Kept narrow and
 *  typed off the value rather than cast, so a shape that carries neither a
 *  stream nor a string is a stated error rather than a silent empty read. */
async function bodyText(response: unknown): Promise<string> {
  if (response === null || response === undefined) return "";
  if (typeof response === "string") return response;
  const stream = response as { transformToString?: () => Promise<string> };
  if (typeof stream.transformToString === "function") {
    return stream.transformToString();
  }
  // A `Uint8Array` is not what this API returns, but a test fake or a future SDK
  // change could supply one, and decoding it is unambiguous.
  if (response instanceof Uint8Array) return new TextDecoder().decode(response);
  throw new Error(
    `the runtime response was neither a stream nor text (got ${typeof response})`,
  );
}

export async function recordDecision(
  permit: Permit,
  caseId: string,
  clients: Clients = {},
): Promise<DecisionOutcome> {
  const env = readEnv();
  const dynamo = clients.dynamo ?? new DynamoDBClient({ region: env.region });
  const decidedAt = utcStamp();

  // 1. The durable record that a human decided. If this throws, the error
  //    propagates and Grace is never invoked: acting on a decision we could not
  //    record would put a filing in the ledger with no record of who asked for
  //    it.
  await dynamo.send(new PutItemCommand({
    TableName: env.tableName,
    Item: {
      pk: { S: `CASE#${caseId}` },
      sk: { S: `DECISION#${decidedAt}` },
      case_id: { S: caseId },
      decided_at: { S: decidedAt },
      // The opaque Cognito `sub`. Never an email or a name — those claims are
      // logged to CloudTrail, outside every redaction Grace has (hard rule 9),
      // and `verifySession` already drops them before a `Permit` exists.
      decided_by: { S: permit.decidedBy },
      decision: { S: permit.decision },
      note: { S: permit.note },
    },
    // Two caseworkers deciding the same case in the same millisecond is
    // vanishingly unlikely, but a lost decision is worse than a rejected one.
    ConditionExpression: "attribute_not_exists(sk)",
  }));

  // 2. A deny means "leave it escalated". There is nothing for Grace to do, and
  //    an invocation would cost real Bedrock to reach the same conclusion.
  if (permit.decision === "deny") {
    const graceOutcome = "Denied by a caseworker; Grace was not re-run.";
    await writeOutcome(dynamo, env.tableName, caseId, decidedAt, graceOutcome);
    return { recorded: true, caseId, decision: "deny", graceOutcome, filed: false };
  }

  // 3. An approve re-invokes Grace with the flag. The gate re-evaluates the case
  //    record; the flag affects only wording, never the verdict.
  //
  // `maxAttempts: 1` is a safety property, not tuning. `InvokeAgentRuntime` is
  // NOT idempotent — each attempt re-runs the whole graph against the same case,
  // so a retried invocation could file one renewal more than once. Measured
  // against a black-hole socket (accepts, never replies, so the accept count IS
  // the number of HTTP attempts): the **JS SDK default makes 3 attempts**;
  // `maxAttempts: 1` makes exactly 1. Note this differs from boto3, where
  // `max_attempts: 1` still gave 2 and only `total_max_attempts` gave 1 (Plan 2)
  // — do not carry that finding across verbatim, the knobs are not the same and
  // `total_max_attempts` does not exist here.
  //
  // `throwOnRequestTimeout` is the second half. Without it the SDK logs
  // "a request has exceeded the configured requestTimeout" and **hangs** rather
  // than throwing — measured. A hung request handler in an SSR route holds the
  // caseworker's browser open with no error to report.
  const runtime = clients.runtime ?? new BedrockAgentCoreClient({
    region: env.region,
    maxAttempts: 1,
    requestHandler: {
      requestTimeout: INVOKE_TIMEOUT_MS,
      throwOnRequestTimeout: true,
    },
  });
  let graceOutcome: string;
  let filed = false;
  try {
    const response = await runtime.send(new InvokeAgentRuntimeCommand({
      agentRuntimeArn: env.runtimeArn,
      // 33+ characters, per the Runtime constraint. A UUID alone is 36, so the
      // prefix is for readability in a log rather than for the length.
      runtimeSessionId: `grace-decide-${caseId}-${crypto.randomUUID()}`,
      payload: new TextEncoder().encode(JSON.stringify({
        case_id: caseId,
        today: PINNED_TODAY,
        caseworker_approved: true,
      })),
    }));

    const body = JSON.parse(await bodyText(response.response)) as {
      status?: unknown;
      filed?: unknown;
      reason?: unknown;
      detail?: unknown;
    };
    // Hard rule 6 at this boundary: only a confirmed filing is reported. An
    // `acted` status without `filed: true` is not a filing, and `=== true`
    // rather than truthiness for the same reason the Python side uses `is True`
    // — this value crosses a JSON boundary, so `"false"` would otherwise pass.
    filed = body.status === "acted" && body.filed === true;
    graceOutcome = filed
      ? "Grace re-checked, the gate cleared the case, and the renewal was filed."
      : `Grace re-checked and did not file. ${describe(body)}`;
  } catch (error) {
    // The decision is already recorded, so say what happened rather than losing
    // it. Same reasoning as Plan 2's failed-escalation-write handling: the gap
    // is stated, not swallowed.
    graceOutcome = `The decision was recorded, but Grace could not be re-run: ${
      error instanceof Error ? error.message : String(error)}`;
  }

  await writeOutcome(dynamo, env.tableName, caseId, decidedAt, graceOutcome);
  return { recorded: true, caseId, decision: "approve", graceOutcome, filed };
}

/** What Grace said, as a sentence, without ever claiming a filing.
 *
 *  Separated out because the draft's inline
 *  `${body.reason ?? body.detail ?? body.status}` renders `undefined` as the
 *  string "undefined" when all three are absent — a response shape a caseworker
 *  would read as a bug in the dashboard rather than as a runtime that answered
 *  nothing. It also silently stringifies a non-string `reason`, so an object
 *  would arrive as "[object Object]". */
function describe(body: {
  status?: unknown;
  reason?: unknown;
  detail?: unknown;
}): string {
  for (const value of [body.reason, body.detail, body.status]) {
    if (typeof value === "string" && value.trim() !== "") return value;
  }
  return "Grace returned no reason; re-run the sweep and check the case ledger.";
}

/** Record what Grace did afterwards, on its own row so the decision row itself
 *  is never rewritten — an audit trail that can be edited is not one.
 *
 *  **The sort key is `DECISION#<ts>#outcome`, and `lib/cases.ts` already handles
 *  it.** That reader collects decisions by the `DECISION#` prefix, so this row
 *  shares it — and it carries **no `decision` attribute**, which is exactly how
 *  `readCase` tells the two apart. Under a naive prefix test this row would
 *  become a phantom deny attributed to nobody (`decided_by` is absent), and an
 *  outcome written before any human decision would make `alreadyDecided` true —
 *  so the *first* caseworker decision on a case would refuse itself as a
 *  duplicate. `readCase` also joins this row to its human row by their shared
 *  `decided_at`, which is why `decided_at` is written here and must stay
 *  identical to the decision row's. Do not change either field without reading
 *  `readCase`'s `DECISION#` branch. */
async function writeOutcome(
  dynamo: DynamoDBClient,
  tableName: string,
  caseId: string,
  decidedAt: string,
  outcome: string,
): Promise<void> {
  await dynamo.send(new PutItemCommand({
    TableName: tableName,
    Item: {
      pk: { S: `CASE#${caseId}` },
      sk: { S: `DECISION#${decidedAt}#outcome` },
      case_id: { S: caseId },
      // The join key back to the human decision this outcome belongs to.
      decided_at: { S: decidedAt },
      outcome: { S: outcome },
    },
  }));
}
