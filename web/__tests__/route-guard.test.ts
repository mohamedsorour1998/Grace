import { describe, expect, it, vi, beforeEach, beforeAll } from "vitest";
import { exportJWK, generateKeyPair, SignJWT } from "jose";
import { CASEWORKER_ROLE, type CaseFacts, type RefusalCode } from "@/lib/authorize";

/** The write route must refuse without a session AND write nothing. Both halves
 *  matter: a refusal that still wrote a row would be the whole point missed.
 *
 *  `recordDecision` is mocked so nothing here can reach AWS, and the mock pushes
 *  to `writes` — so "did the route refuse" and "did the route write" are two
 *  separate assertions rather than one inferred from the other.
 *
 *  **`readFacts` and `verifySession` are both mocked at module scope, and the
 *  plan's draft mocked only the first.** Without a session mock, every test in
 *  its suite refused at `no_session` and the *only* refusal ever exercised was
 *  the first branch. Its `returns 400 for an unrecognised decision word` test
 *  papered over this by accepting `[400, 401]` — so a route that returned 401 to
 *  a fully authenticated caseworker would have passed it. Both codes are
 *  asserted exactly here, which needs a real verifiable session for the 400.
 *
 *  A genuine RS256 key pair mints the valid token rather than stubbing
 *  `verifySession`'s return value, so the route's own cookie parsing and the
 *  real verifier both stay in the path. `lib/cognito.ts` reads an injected JWKS
 *  only when `NODE_ENV === "test"`. */

const writes: Array<{ caseId: string; decision: string }> = [];
vi.mock("@/lib/decide", () => ({
  recordDecision: vi.fn(async (permit: { decision: string }, caseId: string) => {
    writes.push({ caseId, decision: permit.decision });
    return {
      recorded: true, caseId, decision: permit.decision,
      graceOutcome: "Grace re-checked and did not file.", filed: false,
    };
  }),
}));

/** Typed as `CaseFacts | null` rather than inferred, because `vi.fn` narrows to
 *  the literal type of its initial implementation — so `status: "escalated"`
 *  would make every `mockImplementation` returning `"acted"` or `"error"` a
 *  compile error, and `npm run typecheck` is one of this project's five gates. */
const facts = vi.fn<(caseId: string) => Promise<CaseFacts | null>>(
  async (caseId: string) => ({
    caseId, status: "escalated", alreadyDecided: false,
  }),
);
vi.mock("@/lib/cases", () => ({ readFacts: (id: string) => facts(id) }));

const NOW_EXP_SECONDS = 3600;
const ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TESTPOOL";
const CLIENT_ID = "test-client-id";
let validToken: string;

beforeAll(async () => {
  const { privateKey, publicKey } = await generateKeyPair("RS256");
  const jwk = { ...(await exportJWK(publicKey)), kid: "test-kid", alg: "RS256", use: "sig" };
  process.env.COGNITO_ISSUER = ISSUER;
  process.env.COGNITO_CLIENT_ID = CLIENT_ID;
  process.env.COGNITO_TEST_JWKS = JSON.stringify({ keys: [jwk] });
  process.env.GRACE_TABLE_NAME = "grace-cases";
  process.env.GRACE_ESCALATION_INDEX = "escalation-queue";
  process.env.GRACE_RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-east-1:1:runtime/grace";
  validToken = await new SignJWT({
    token_use: "id", aud: CLIENT_ID, iss: ISSUER,
    sub: "7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d", "custom:role": CASEWORKER_ROLE,
  })
    .setProtectedHeader({ alg: "RS256", kid: "test-kid" })
    .setIssuedAt()
    .setExpirationTime(Math.floor(Date.now() / 1000) + NOW_EXP_SECONDS)
    .sign(privateKey);
});

beforeEach(() => {
  writes.length = 0;
  facts.mockClear();
  facts.mockImplementation(async (caseId: string) => ({
    caseId, status: "escalated", alreadyDecided: false,
  }));
});

async function post(body: unknown, cookie?: string, id = "c-010") {
  const { POST } = await import("@/app/api/case/[id]/decide/route");
  const headers = new Headers({ "content-type": "application/json" });
  if (cookie) headers.set("cookie", `grace_session=${cookie}`);
  const request = new Request(`http://localhost:3000/api/case/${id}/decide`, {
    method: "POST", headers, body: JSON.stringify(body),
  });
  return POST(request as never, { params: Promise.resolve({ id }) } as never);
}

describe("the decide route", () => {
  it("returns 401 and writes NOTHING without a session cookie", async () => {
    const response = await post({ decision: "approve", note: "" });
    expect(response.status).toBe(401);
    expect(writes, "a refused request must not write").toHaveLength(0);
  });

  it("returns 401 and writes nothing for a forged cookie", async () => {
    // The proxy only checks presence; verification happens here. A forged
    // cookie gets past the redirect and must still be refused.
    const response = await post({ decision: "approve", note: "" }, "not-a-real-jwt");
    expect(response.status).toBe(401);
    expect(writes).toHaveLength(0);
  });

  it("does not even measure the case facts without a session", async () => {
    // Session checks precede fact checks, or the difference between `no_session`
    // and `unknown_case` tells an unauthenticated caller which case IDs exist.
    // `authorize` orders its own refusals correctly; this asserts the *route*
    // does not read the table before it knows who is asking.
    await post({ decision: "approve", note: "" }, "not-a-real-jwt");
    expect(facts).not.toHaveBeenCalled();
  });

  it("returns 400 and writes nothing for an unrecognised decision word", async () => {
    // A real session, so this reaches the decision-word branch rather than
    // refusing at `no_session`. The draft accepted `[400, 401]`, which a route
    // refusing every authenticated caseworker would also satisfy.
    const response = await post({ decision: "needs review", note: "" }, validToken);
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ error: "unknown_decision" });
    expect(writes).toHaveLength(0);
  });

  it("refuses every near-miss of the two honoured words", async () => {
    // Allowlist polarity: the *unrecognised* answer must be the safe one. Task 6
    // measured "Escalate.", "no, hold this one", and "needs review" each
    // resuming a graph and filing a renewal for a household missing a document.
    let checked = 0;
    for (const decision of [
      "Approve", "approve ", " approve", "APPROVE", "yes", "file", "proceed",
      "Escalate.", "no, hold this one", "", null, 1, true, ["approve"],
      { decision: "approve" },
    ]) {
      const response = await post({ decision, note: "" }, validToken);
      expect(response.status, JSON.stringify(decision)).toBe(400);
      checked += 1;
    }
    expect(checked).toBe(15);
    expect(writes).toHaveLength(0);
    // And the two real words are honoured, or the loop above would pass against
    // a route that refuses everything.
    for (const decision of ["approve", "deny"]) {
      const response = await post({ decision, note: "" }, validToken);
      expect(response.status, decision).toBe(200);
    }
    expect(writes.map(w => w.decision)).toEqual(["approve", "deny"]);
  });

  it("writes for an authenticated caseworker on an escalated case", async () => {
    // The positive path, without which every refusal test above could be
    // satisfied by a route that refuses unconditionally — the Task 2 lesson
    // about `authorize` tests, applied to the route.
    const response = await post({ decision: "approve", note: "Wage record is stale." },
      validToken);
    expect(response.status).toBe(200);
    expect(writes).toEqual([{ caseId: "c-010", decision: "approve" }]);
    expect(await response.json()).toMatchObject({ recorded: true, filed: false });
  });

  it("refuses a note over the cap without writing", async () => {
    const response = await post({ decision: "approve", note: "x".repeat(2001) },
      validToken);
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ error: "note_too_long" });
    expect(writes).toHaveLength(0);
  });

  it("refuses a non-string note rather than inventing an empty one", async () => {
    // The route passes `note` through unchanged so `authorize` can refuse the
    // type. The draft's route did `typeof body.note === "string" ? body.note : ""`,
    // which **coerces** — it invents a note nobody wrote and then permits the
    // decision, so `authorize`'s own non-string guard (Task 2, measured) became
    // unreachable from the only route that calls it.
    let checked = 0;
    for (const note of [null, 42, {}, [], true]) {
      const response = await post({ decision: "approve", note }, validToken);
      expect(response.status, JSON.stringify(note)).toBe(400);
      expect(await response.json()).toMatchObject({ error: "note_too_long" });
      checked += 1;
    }
    // An absent `note` is also a non-string, and must refuse rather than
    // defaulting.
    const absent = await post({ decision: "approve" }, validToken);
    expect(absent.status).toBe(400);
    checked += 1;
    expect(checked).toBe(6);
    expect(writes).toHaveLength(0);
  });

  it("returns 404 for a case that cannot be read, and writes nothing", async () => {
    facts.mockImplementation(async () => null);
    const response = await post({ decision: "approve", note: "" }, validToken);
    expect(response.status).toBe(404);
    expect(await response.json()).toMatchObject({ error: "unknown_case" });
    expect(writes).toHaveLength(0);
  });

  it("returns 409 rather than 400 for a case Grace already handled", async () => {
    // `not_escalated` is not a malformed request — the client asked a coherent
    // question about a case in the wrong state. The draft's ternary chain
    // returned 400 for this and for `case_incomplete` and `already_decided`.
    facts.mockImplementation(async (caseId: string) => ({
      caseId, status: "acted", alreadyDecided: false,
    }));
    const response = await post({ decision: "approve", note: "" }, validToken);
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ error: "not_escalated" });
    expect(writes).toHaveLength(0);
  });

  it("returns 409 for a case whose last run reached no outcome", async () => {
    // `case_incomplete` was added after the plan's draft was written, so the
    // draft's ternary had no branch for it and it fell through to 400.
    facts.mockImplementation(async (caseId: string) => ({
      caseId, status: "error", alreadyDecided: false,
    }));
    const response = await post({ decision: "approve", note: "" }, validToken);
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ error: "case_incomplete" });
    expect(writes).toHaveLength(0);
  });

  it("returns 409 and writes nothing for a case already decided", async () => {
    facts.mockImplementation(async (caseId: string) => ({
      caseId, status: "escalated", alreadyDecided: true,
    }));
    const response = await post({ decision: "approve", note: "" }, validToken);
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ error: "already_decided" });
    expect(writes).toHaveLength(0);
  });

  it("maps every refusal code to a status, checked at runtime", async () => {
    // `Record<RefusalCode, number>` makes a missing code a compile error, but a
    // type is erased — so if someone widens the map's type this asserts the
    // mapping is still total. An unmapped code would return `undefined` to
    // `NextResponse.json`, which throws rather than refusing.
    const codes: RefusalCode[] = [
      "no_session", "session_expired", "wrong_role", "unknown_case",
      "not_escalated", "case_incomplete", "already_decided",
      "unknown_decision", "note_too_long",
    ];
    const src = await import("node:fs").then(fs =>
      fs.readFileSync(
        new URL("../app/api/case/[id]/decide/route.ts", import.meta.url), "utf8"));
    for (const code of codes) expect(src, code).toContain(`${code}:`);
    expect(codes).toHaveLength(9);
  });

  it("refuses a body that is not a JSON object at all", async () => {
    // `await request.json()` on `null`, a number, or an array parses fine, and
    // property access on the first two is `undefined` while `[].decision` is
    // too — so the route must normalise rather than trust the shape.
    let checked = 0;
    for (const body of [null, 42, "approve", [{ decision: "approve" }], true]) {
      const response = await post(body, validToken);
      expect(response.status, JSON.stringify(body)).toBe(400);
      checked += 1;
    }
    expect(checked).toBe(5);
    expect(writes).toHaveLength(0);
  });

  it("refuses an unparseable body without throwing", async () => {
    const { POST } = await import("@/app/api/case/[id]/decide/route");
    const request = new Request("http://localhost:3000/api/case/c-010/decide", {
      method: "POST",
      headers: new Headers({
        "content-type": "application/json",
        cookie: `grace_session=${validToken}`,
      }),
      body: "{not json",
    });
    const response = await POST(request as never,
      { params: Promise.resolve({ id: "c-010" }) } as never);
    expect(response.status).toBe(400);
    expect(writes).toHaveLength(0);
  });

  it("decides the case in the URL, never one named in the body", async () => {
    // Identity from the session and the path, never from the payload — layer 2
    // of the escalation boundary, at the HTTP edge. A `case_id` in the body must
    // not redirect the write to another household.
    const response = await post(
      { decision: "approve", note: "", case_id: "c-001", caseId: "c-001", id: "c-001" },
      validToken, "c-011");
    expect(response.status).toBe(200);
    expect(writes).toEqual([{ caseId: "c-011", decision: "approve" }]);
    expect(facts).toHaveBeenCalledWith("c-011");
  });

  it("rejects a GET", async () => {
    const mod = await import("@/app/api/case/[id]/decide/route");
    expect((mod as Record<string, unknown>).GET).toBeUndefined();
  });

  it("exports POST and nothing else that mutates", async () => {
    // A decision must not be reachable by following a link, so no GET — and no
    // PUT/PATCH/DELETE either, which the draft did not check.
    const mod = await import("@/app/api/case/[id]/decide/route") as Record<string, unknown>;
    for (const method of ["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"]) {
      expect(mod[method], method).toBeUndefined();
    }
    expect(typeof mod.POST).toBe("function");
  });

  it("carries no resume vocabulary", async () => {
    const src = await import("node:fs").then(fs =>
      fs.readFileSync(
        new URL("../app/api/case/[id]/decide/route.ts", import.meta.url), "utf8"));
    for (const forbidden of ["interruptResponse", "APPROVE_DECISIONS", "MAX_RESUME_ROUNDS"]) {
      expect(src, forbidden).not.toContain(forbidden);
    }
  });
});
