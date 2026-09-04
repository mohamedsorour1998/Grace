import { describe, expect, it, vi } from "vitest";
import { recordDecision } from "@/lib/decide";
import type { Permit } from "@/lib/authorize";

/** A fake DynamoDB client that records what it was asked to write. */
class FakeDynamo {
  public puts: Array<Record<string, unknown>> = [];
  constructor(private failOnPut = false) {}
  async send(command: { input: Record<string, unknown> }): Promise<unknown> {
    if (this.failOnPut) throw new Error("dynamodb refused the write");
    this.puts.push(command.input);
    return {};
  }
}

/** A fake runtime whose `response` has the shape the **real** SDK returns.
 *
 *  This is the defect the plan's draft shipped: `InvokeAgentRuntimeResponse.response`
 *  is typed `StreamingBlobTypes` and arrives as a Node `IncomingMessage` carrying
 *  `sdkStreamMixin`, not a `Uint8Array`. A fake returning raw bytes would let
 *  `new TextDecoder().decode(...)` pass in every test while throwing `TypeError`
 *  against the real client — and `recordDecision` catches, so the symptom in
 *  production is "Grace could not be re-run" on every approve that in fact ran.
 *  A fake that cannot fail the way the real service fails is worse than no fake
 *  (Plan 2's `FakeTable` lesson), so this one exposes `transformToString` only. */
class FakeRuntime {
  public invocations: Array<Record<string, unknown>> = [];
  constructor(
    private body: Record<string, unknown> = { status: "escalated", case_id: "c-010" },
  ) {}
  async send(command: { input: Record<string, unknown> }): Promise<unknown> {
    this.invocations.push(command.input);
    const json = JSON.stringify(this.body);
    return { response: { transformToString: async () => json } };
  }
}

const permit = (over: Partial<Permit> = {}): Permit => ({
  permitted: true,
  decidedBy: "7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d",
  decision: "approve",
  note: "Wage record is stale.",
  ...over,
});

function withEnv<T>(run: () => Promise<T>): Promise<T> {
  process.env.GRACE_TABLE_NAME = "grace-cases";
  process.env.GRACE_ESCALATION_INDEX = "escalation-queue";
  process.env.GRACE_RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-east-1:1:runtime/grace";
  process.env.AWS_REGION = "us-east-1";
  return run();
}

/** The payload sent to the runtime, decoded. */
function sentPayload(runtime: FakeRuntime): Record<string, unknown> {
  const raw = runtime.invocations[0]!.payload as Uint8Array;
  return JSON.parse(new TextDecoder().decode(raw)) as Record<string, unknown>;
}

/** The `Item` of the nth `PutItem`. */
function put(dynamo: FakeDynamo, n: number): Record<string, { S?: string }> {
  return dynamo.puts[n]!.Item as Record<string, { S?: string }>;
}

describe("recordDecision", () => {
  it("builds a runtime client that cannot retry a non-idempotent invocation", () =>
    withEnv(async () => {
      // Not tuning — a safety property, and asserted because a config value
      // nobody checks can be deleted silently. `InvokeAgentRuntime` re-runs the
      // whole graph per attempt, so a retry could file one renewal twice.
      // Measured against a black-hole socket: the JS SDK default is **3**
      // attempts, `maxAttempts: 1` is exactly 1. (boto3 differs: `max_attempts:
      // 1` still gave 2 there, only `total_max_attempts` gave 1 — and that key
      // does not exist in this SDK.)
      //
      // No client is injected here, so the module builds its own — which is the
      // path the deployed route takes and the only path this config is on.
      const { BedrockAgentCoreClient } = await import("@aws-sdk/client-bedrock-agentcore");
      const built: unknown[] = [];
      const spy = vi.spyOn(BedrockAgentCoreClient.prototype, "send")
        .mockImplementation(async function (this: unknown) {
          built.push(this);
          return { response: { transformToString: async () => "{}" } };
        } as never);
      try {
        await recordDecision(permit(), "c-010", { dynamo: new FakeDynamo() as never });
        expect(built.length).toBe(1);
        const client = built[0] as { config: { maxAttempts: unknown } };
        // `maxAttempts` is a provider function on a resolved client config.
        const attempts = typeof client.config.maxAttempts === "function"
          ? await (client.config.maxAttempts as () => Promise<number>)()
          : client.config.maxAttempts;
        expect(attempts).toBe(1);
      } finally {
        spy.mockRestore();
      }
    }));

  it("bounds the invocation with a timeout that throws rather than hanging", () =>
    withEnv(async () => {
      // The other half of the client config, and the half a `maxAttempts` test
      // cannot see. Measured: `requestTimeout` alone logs "a request has
      // exceeded the configured requestTimeout" and leaves the promise
      // **pending** — in an SSR route that holds the caseworker's browser open
      // with no error to report. `throwOnRequestTimeout: true` turns it into a
      // `TimeoutError`.
      const { BedrockAgentCoreClient } = await import("@aws-sdk/client-bedrock-agentcore");
      const built: unknown[] = [];
      const spy = vi.spyOn(BedrockAgentCoreClient.prototype, "send")
        .mockImplementation(async function (this: unknown) {
          built.push(this);
          return { response: { transformToString: async () => "{}" } };
        } as never);
      try {
        await recordDecision(permit(), "c-010", { dynamo: new FakeDynamo() as never });
        // `NodeHttpHandler` resolves its options lazily: `.config` is
        // `undefined` until the first request completes, and the values live
        // behind an async `configProvider`. Measured — a `.config` read here
        // returns `undefined` and `expect(undefined?.x).toBe(true)` fails, which
        // is how this was found rather than assumed.
        const handler = (built[0] as {
          config: { requestHandler: { configProvider: Promise<Record<string, unknown>> } };
        }).config.requestHandler;
        const resolved = await handler.configProvider;
        expect(resolved.throwOnRequestTimeout).toBe(true);
        expect(resolved.requestTimeout as number).toBeGreaterThan(600_000);
      } finally {
        spy.mockRestore();
      }
    }));

  it("reads the response as a stream, the way the real SDK returns it", () =>
    withEnv(async () => {
      // The plan's draft did `new TextDecoder().decode(response.response)`.
      // Measured against the real client against a local HTTP server: `response`
      // is an `IncomingMessage`, and decoding it throws
      // `TypeError: The "list" argument must be an instance of ... ArrayBufferView`.
      // `recordDecision` catches, so every approve would have reported "Grace
      // could not be re-run" *after* Grace had already run — a false failure on
      // the one action this application performs.
      const runtime = new FakeRuntime({
        status: "escalated", case_id: "c-010",
        reason: "missing_document: proof_of_residency is not on file",
      });
      const out = await recordDecision(permit(), "c-010",
        { dynamo: new FakeDynamo() as never, runtime: runtime as never });
      expect(out.graceOutcome).toContain("missing_document");
      expect(out.graceOutcome).not.toContain("could not be re-run");
    }));

  it("writes the decision row BEFORE invoking the runtime", () =>
    withEnv(async () => {
      // The opposite ordering to action.py, and deliberately so: the row claims
      // "a human decided", which is true the moment they did. Losing it to an
      // invocation failure would discard the caseworker's work.
      const order: string[] = [];
      const dynamo = { send: async () => { order.push("row"); return {}; } };
      const runtime = {
        send: async () => {
          order.push("invoke");
          return { response: { transformToString: async () => "{}" } };
        },
      };
      await recordDecision(permit(), "c-010",
        { dynamo: dynamo as never, runtime: runtime as never });
      // The outcome row lands after the invocation, so three events in order.
      expect(order).toEqual(["row", "invoke", "row"]);
    }));

  it("records the opaque sub, never a name or an email", () =>
    withEnv(async () => {
      const dynamo = new FakeDynamo();
      await recordDecision(permit(), "c-010",
        { dynamo: dynamo as never, runtime: new FakeRuntime() as never });
      const item = put(dynamo, 0);
      expect(item.decided_by!.S).toBe("7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d");
      expect(JSON.stringify(item)).not.toContain("@");
    }));

  it("writes no household or caseworker identity on any row", () =>
    withEnv(async () => {
      // Hard rule 9 at this boundary. A companion to the test above, which only
      // checks the decision row and only for an `@`. Both rows, and the twelve
      // fixture surnames — the pattern Task 6's draft got wrong by listing three
      // of them, missing Fitzgerald and Yamamoto, the two households most likely
      // to carry a name in an escalation reason.
      const dynamo = new FakeDynamo();
      await recordDecision(permit(), "c-010",
        { dynamo: dynamo as never, runtime: new FakeRuntime() as never });
      const NAMES = /Mensah|Rivera|Okonkwo|Fitzgerald|Yamamoto|Nguyen|Haddad|Silva|Kowalski|Abebe|Castillo|Petrov|\+1555|Household/i;
      expect(dynamo.puts).toHaveLength(2);
      for (const [n] of dynamo.puts.entries()) {
        expect(JSON.stringify(put(dynamo, n))).not.toMatch(NAMES);
      }
    }));

  it("catches a name reaching a row, so the guard above is not vacuous", () =>
    withEnv(async () => {
      // Feed a surname in through the one field that carries free text, and
      // assert the pattern catches it. Otherwise "no name in this row" is true
      // of every input and proves nothing.
      const dynamo = new FakeDynamo();
      await recordDecision(permit({ note: "Spoke to the Yamamoto household." }),
        "c-011", { dynamo: dynamo as never, runtime: new FakeRuntime() as never });
      const NAMES = /Mensah|Rivera|Okonkwo|Fitzgerald|Yamamoto|Nguyen|Haddad|Silva|Kowalski|Abebe|Castillo|Petrov|\+1555|Household/i;
      expect(JSON.stringify(put(dynamo, 0))).toMatch(NAMES);
    }));

  it("keys the row so it cannot overwrite a ledger row or an earlier decision", () =>
    withEnv(async () => {
      const dynamo = new FakeDynamo();
      await recordDecision(permit(), "c-011",
        { dynamo: dynamo as never, runtime: new FakeRuntime() as never });
      const item = put(dynamo, 0);
      expect(item.pk!.S).toBe("CASE#c-011");
      expect(item.sk!.S).toMatch(/^DECISION#\d{4}-\d{2}-\d{2}T/);
      // UTC, so the sort key orders correctly bytewise — Plan 2's finding that a
      // non-UTC offset sorts a later instant before an earlier one.
      expect(item.sk!.S).toMatch(/\+00:00$|Z$/);
      // And it never collides with the ledger's own prefix.
      expect(item.sk!.S!.startsWith("LEDGER#")).toBe(false);
      expect(dynamo.puts[0]!.ConditionExpression).toBe("attribute_not_exists(sk)");
    }));

  it("writes the outcome row in the exact shape lib/cases.ts discriminates on", () =>
    withEnv(async () => {
      // `readCase` collects decisions by the `DECISION#` prefix and tells the
      // human row from Grace's outcome by the **presence of `decision`**, then
      // joins them by their shared `decided_at`. Both halves are asserted here
      // because both are load-bearing in a file this task may not edit: an
      // outcome row carrying `decision` becomes a phantom deny attributed to
      // nobody, and a mismatched `decided_at` makes `Decision.outcome`
      // permanently null.
      const dynamo = new FakeDynamo();
      await recordDecision(permit(), "c-010",
        { dynamo: dynamo as never, runtime: new FakeRuntime() as never });
      expect(dynamo.puts).toHaveLength(2);
      const human = put(dynamo, 0);
      const outcome = put(dynamo, 1);
      expect(outcome.sk!.S).toBe(`${human.sk!.S}#outcome`);
      expect(outcome.decision).toBeUndefined();
      expect(outcome.decided_at!.S).toBe(human.decided_at!.S);
      expect(outcome.outcome!.S).toBeTruthy();
      // The human row is the one that carries `decision` and `decided_by`.
      expect(human.decision!.S).toBe("approve");
      expect(human.decided_by!.S).toBeTruthy();
      expect(outcome.decided_by).toBeUndefined();
    }));

  it("sends caseworker_approved: true only for an approve", () =>
    withEnv(async () => {
      const approved = new FakeRuntime();
      await recordDecision(permit({ decision: "approve" }), "c-010",
        { dynamo: new FakeDynamo() as never, runtime: approved as never });
      const payload = sentPayload(approved);
      expect(payload.caseworker_approved).toBe(true);
      expect(payload.case_id).toBe("c-010");
    }));

  it("pins `today` rather than sending a live clock", () =>
    withEnv(async () => {
      // A live date evaluates every renewal window against the wrong day with no
      // error. Fixture c-002's SNAP grace period ends 2026-10-30, so a real
      // clock turns the 9-act/3-escalate demo into 8/4 from 2026-10-31 — and the
      // dashboard and the sweep would then disagree about which day they are
      // evaluating, producing two correct-looking answers.
      const runtime = new FakeRuntime();
      await recordDecision(permit(), "c-010",
        { dynamo: new FakeDynamo() as never, runtime: runtime as never });
      expect(sentPayload(runtime).today).toBe("2026-10-01");
    }));

  it("carries no resume vocabulary in the payload it sends", () =>
    withEnv(async () => {
      // The structural test below reads the file; this one reads the wire. A
      // resume key in the payload is what would actually approve the blocked
      // tool, and the deployed entrypoint would ignore it — but the next version
      // might not.
      const runtime = new FakeRuntime();
      await recordDecision(permit(), "c-010",
        { dynamo: new FakeDynamo() as never, runtime: runtime as never });
      const keys = Object.keys(sentPayload(runtime)).sort();
      expect(keys).toEqual(["case_id", "caseworker_approved", "today"]);
    }));

  it("does not re-invoke Grace at all for a deny", () =>
    withEnv(async () => {
      // A deny means "leave it escalated". There is nothing for Grace to do, and
      // an invocation would cost real Bedrock for no decision.
      const runtime = new FakeRuntime();
      const out = await recordDecision(permit({ decision: "deny" }), "c-010",
        { dynamo: new FakeDynamo() as never, runtime: runtime as never });
      expect(runtime.invocations).toHaveLength(0);
      expect(out.filed).toBe(false);
      expect(out.graceOutcome).toContain("not re-run");
    }));

  it("still records a deny's outcome row, so the case is not left silent", () =>
    withEnv(async () => {
      const dynamo = new FakeDynamo();
      const out = await recordDecision(permit({ decision: "deny" }), "c-010",
        { dynamo: dynamo as never, runtime: new FakeRuntime() as never });
      expect(dynamo.puts).toHaveLength(2);
      expect(put(dynamo, 0).decision!.S).toBe("deny");
      expect(put(dynamo, 1).outcome!.S).toContain("not re-run");
      expect(out.decision).toBe("deny");
    }));

  it("reports filed: false when Grace escalates again", () =>
    withEnv(async () => {
      const out = await recordDecision(permit(), "c-010", {
        dynamo: new FakeDynamo() as never,
        runtime: new FakeRuntime({ status: "escalated", case_id: "c-010",
          reason: "missing_document: proof_of_residency is not on file" }) as never,
      });
      expect(out.filed).toBe(false);
      expect(out.graceOutcome).toContain("missing_document");
    }));

  it("reports filed: true only when Grace says it acted and filed", () =>
    withEnv(async () => {
      const out = await recordDecision(permit(), "c-011", {
        dynamo: new FakeDynamo() as never,
        runtime: new FakeRuntime({ status: "acted", case_id: "c-011", filed: true }) as never,
      });
      expect(out.filed).toBe(true);
      expect(out.graceOutcome).toContain("filed");
    }));

  it("does not claim a filing when the runtime says acted without filed", () =>
    withEnv(async () => {
      // Hard rule 6 at this boundary: only a confirmed filing is reported.
      const out = await recordDecision(permit(), "c-011", {
        dynamo: new FakeDynamo() as never,
        runtime: new FakeRuntime({ status: "acted", case_id: "c-011" }) as never,
      });
      expect(out.filed).toBe(false);
    }));

  it("does not accept a stringy or truthy `filed` as a filing", () =>
    withEnv(async () => {
      // The value crosses a JSON boundary, where `"false"` is truthy. `=== true`
      // for the same reason the Python side uses `is True` — the unrecognised
      // value must be the safe one.
      let checked = 0;
      for (const value of ["true", "false", 1, "yes", {}, [], "1"]) {
        const out = await recordDecision(permit(), "c-011", {
          dynamo: new FakeDynamo() as never,
          runtime: new FakeRuntime({ status: "acted", filed: value }) as never,
        });
        expect(out.filed, JSON.stringify(value)).toBe(false);
        checked += 1;
      }
      expect(checked).toBe(7);
      // And the honest value still reports a filing, or the loop above would
      // pass against a `filed` that is permanently false.
      const honest = await recordDecision(permit(), "c-011", {
        dynamo: new FakeDynamo() as never,
        runtime: new FakeRuntime({ status: "acted", filed: true }) as never,
      });
      expect(honest.filed).toBe(true);
    }));

  it("says something useful when the runtime returns no reason at all", () =>
    withEnv(async () => {
      // The draft's inline `body.reason ?? body.detail ?? body.status` renders
      // the literal string "undefined" when all three are absent — which a
      // caseworker reads as a bug in the dashboard rather than as a runtime that
      // answered nothing.
      const out = await recordDecision(permit(), "c-010", {
        dynamo: new FakeDynamo() as never,
        runtime: new FakeRuntime({}) as never,
      });
      expect(out.filed).toBe(false);
      expect(out.graceOutcome).not.toContain("undefined");
      expect(out.graceOutcome).toContain("re-run the sweep");
    }));

  it("does not stringify a non-string reason into the outcome", () =>
    withEnv(async () => {
      const out = await recordDecision(permit(), "c-010", {
        dynamo: new FakeDynamo() as never,
        runtime: new FakeRuntime({ status: "escalated", reason: { a: 1 } }) as never,
      });
      expect(out.graceOutcome).not.toContain("[object Object]");
      // It falls through to `status`, which is a real string.
      expect(out.graceOutcome).toContain("escalated");
    }));

  it("propagates a failed row write instead of invoking Grace", () =>
    withEnv(async () => {
      // If the human's decision could not be recorded, do not act on it — the
      // audit trail would then have Grace filing with no record of who asked.
      const runtime = new FakeRuntime();
      await expect(recordDecision(permit(), "c-010",
        { dynamo: new FakeDynamo(true) as never, runtime: runtime as never }))
        .rejects.toThrow(/refused the write/);
      expect(runtime.invocations).toHaveLength(0);
    }));

  it("survives a runtime failure with the decision still recorded", () =>
    withEnv(async () => {
      const dynamo = new FakeDynamo();
      const broken = { send: () => Promise.reject(new Error("runtime unavailable")) };
      const out = await recordDecision(permit(), "c-010",
        { dynamo: dynamo as never, runtime: broken as never });
      expect(dynamo.puts).toHaveLength(2);   // the decision, then the outcome
      expect(out.filed).toBe(false);
      expect(out.graceOutcome).toContain("runtime unavailable");
    }));

  it("survives an unparseable runtime body without claiming a filing", () =>
    withEnv(async () => {
      const dynamo = new FakeDynamo();
      const garbage = {
        send: async () => ({ response: { transformToString: async () => "<html>502" } }),
      };
      const out = await recordDecision(permit(), "c-010",
        { dynamo: dynamo as never, runtime: garbage as never });
      expect(out.filed).toBe(false);
      expect(dynamo.puts).toHaveLength(2);
      expect(out.graceOutcome).toContain("could not be re-run");
    }));

  it("carries no resume vocabulary", async () => {
    const src = await import("node:fs").then(fs =>
      fs.readFileSync(new URL("../lib/decide.ts", import.meta.url), "utf8"));
    for (const forbidden of ["interruptResponse", "APPROVE_DECISIONS", "MAX_RESUME_ROUNDS"]) {
      expect(src, forbidden).not.toContain(forbidden);
    }
  });

  it("declares maxAttempts and throwOnRequestTimeout in its own source", async () => {
    // The behavioural tests above read them off a resolved client config, which
    // is the stronger check. This one guards the case where someone moves the
    // client construction behind a helper the spy no longer sees — the config
    // would silently revert to the SDK default of 3 attempts on a
    // non-idempotent call.
    const src = await import("node:fs").then(fs =>
      fs.readFileSync(new URL("../lib/decide.ts", import.meta.url), "utf8"));
    expect(src).toContain("maxAttempts: 1");
    expect(src).toContain("throwOnRequestTimeout: true");
    // boto3's knob does not exist in this SDK, so it must not be copied in as
    // *code*. Matched with a `:` so the module's comment explaining the
    // difference does not fail its own guard — a raw substring check over source
    // is defeated by prose about the thing it forbids, which is exactly how the
    // plan's Python resume-vocabulary test failed against correct code.
    expect(src).not.toMatch(/total_max_attempts\s*:/);
  });
});
