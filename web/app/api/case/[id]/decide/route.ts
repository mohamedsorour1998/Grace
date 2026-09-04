/**
 * The write endpoint. POST only, session-gated, and it refuses before it
 * measures anything it does not need.
 *
 * There is no GET export on purpose: a decision must not be reachable by
 * following a link, which is also why the session cookie is `sameSite: "lax"`.
 *
 * `proxy.ts` checks only that a cookie *exists*, on the edge runtime. This is
 * the check that matters, and `__tests__/route-guard.test.ts` proves it refuses
 * on its own with nothing from that file involved.
 */

import { NextResponse } from "next/server";
import { authorize, type RefusalCode } from "@/lib/authorize";
import { readFacts } from "@/lib/cases";
import { recordDecision } from "@/lib/decide";
import { SESSION_COOKIE, verifySession } from "@/lib/cognito";

/** HTTP status per refusal code.
 *
 *  A `Record<RefusalCode, number>` rather than a nested ternary, so adding a
 *  code to `lib/authorize.ts` is a **compile error** here instead of silently
 *  falling through to 400. The draft's ternary chain mapped every unlisted code
 *  to 400 — including `case_incomplete`, which Task 3 added after the draft was
 *  written, and which is a server-side "re-run the sweep" rather than anything
 *  the client got wrong. `__tests__/route-guard.test.ts` asserts the map is
 *  exhaustive at runtime too, since a `Record` is erased at compile time. */
const STATUS: Record<RefusalCode, number> = {
  no_session: 401,
  session_expired: 401,
  wrong_role: 403,
  unknown_case: 404,
  not_escalated: 409,
  case_incomplete: 409,
  already_decided: 409,
  unknown_decision: 400,
  note_too_long: 400,
};

export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await context.params;

  // Verify the session here, not in the proxy. The proxy checks only that a
  // cookie exists; this is the check that matters.
  const cookie = request.headers
    .get("cookie")
    ?.split(";")
    .map(c => c.trim())
    .find(c => c.startsWith(`${SESSION_COOKIE}=`))
    ?.slice(SESSION_COOKIE.length + 1);

  // `verifySession` throws if `COGNITO_ISSUER`/`COGNITO_CLIENT_ID` are unset —
  // a misconfiguration, not a refusal, and one that must not surface as an
  // unhandled 500 with a stack trace. Treated as no session, because a verifier
  // that cannot run has not authenticated anybody. Fail closed.
  let session: Awaited<ReturnType<typeof verifySession>>;
  try {
    session = await verifySession(cookie);
  } catch {
    session = null;
  }

  let body: { decision?: unknown; note?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    body = {};
  }
  // A JSON body may legitimately parse to `null`, a number, or an array, on any
  // of which property access is either an error or silently `undefined`.
  // Normalising to an object here keeps `authorize` the only thing deciding.
  if (body === null || typeof body !== "object" || Array.isArray(body)) body = {};

  const attempt = {
    // Not coerced and not trimmed: `authorize` compares against an allowlist of
    // exactly "approve" and "deny", and trimming here would quietly accept
    // "approve ". Anything else refuses.
    //
    // The `as` cast is where TypeScript's promise stops being checked (Task 2),
    // which is why `authorize` re-checks both fields at runtime rather than
    // trusting these types. `note` is passed through **unchanged** — including
    // when it is not a string — so `authorize` can refuse the type rather than
    // this route inventing a note nobody wrote.
    decision: body.decision as "approve" | "deny",
    note: body.note as string,
  };

  // Facts are measured only once the session is known — an unauthenticated
  // caller learns nothing about which cases exist. `readFacts` returns `null`
  // on an unreadable case (it catches internally), but `readEnv` sits outside
  // that `catch` on purpose, so a missing table name still throws here.
  let facts: Awaited<ReturnType<typeof readFacts>> = null;
  if (session !== null) {
    try {
      facts = await readFacts(id);
    } catch {
      // A configuration failure, not "no such case". Refusing is right either
      // way — `authorize` treats null facts as undecidable — and the caseworker
      // gets a refusal rather than a stack trace.
      facts = null;
    }
  }
  const decision = authorize(session, facts, attempt, Date.now());

  if (!decision.permitted) {
    return NextResponse.json(
      { error: decision.code, message: decision.message },
      { status: STATUS[decision.code] },
    );
  }

  try {
    const outcome = await recordDecision(decision, id);
    return NextResponse.json(outcome, { status: 200 });
  } catch (error) {
    // The decision could not be recorded, so Grace was not re-run. Say so —
    // `recordDecision` only throws from the row write, and it writes before it
    // invokes precisely so this branch means "nothing happened".
    return NextResponse.json(
      {
        error: "not_recorded",
        message: `The decision was not recorded, so nothing was changed: ${
          error instanceof Error ? error.message : String(error)}`,
      },
      { status: 503 },
    );
  }
}
