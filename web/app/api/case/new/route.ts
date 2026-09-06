/**
 * The intake endpoint. POST only, session-gated, and it refuses before it
 * writes anything.
 *
 * Gated exactly like `api/case/[id]/decide` rather than in some new way:
 * `verifySession` first — the check that matters, since `proxy.ts` only asks
 * whether a cookie exists — then a pure validation function, then the write.
 * There is no GET export, so a case cannot be created by following a link.
 */

import { NextResponse } from "next/server";
import { SESSION_COOKIE, verifySession } from "@/lib/cognito";
import { validateIntake, type IntakeRefusalCode } from "@/lib/intake";
import { CaseAlreadyExists, createCase } from "@/lib/create-case";

/** HTTP status per refusal code.
 *
 *  A `Record<IntakeRefusalCode, number>` rather than a ternary chain, so adding
 *  a code in `lib/intake.ts` is a **compile error** here instead of silently
 *  falling through to 400. Plan 3 measured what the ternary cost: a code added
 *  after the draft was written fell through to 400 and reported a server-side
 *  "re-run the sweep" as a client mistake. `__tests__/intake.test.ts` asserts the
 *  map is total at runtime too, because a `Record`'s keys are erased at compile
 *  time — and it reads the codes off `IntakeRefusalCode`'s union on disk rather
 *  than from a list, since a list someone maintains by hand cannot fail for the
 *  code they forgot to add.
 *
 *  **This comment named `__tests__/intake-route.test.ts`, which does not exist
 *  and never did.** A docstring asserting that a check happens is not the check. */
const STATUS: Record<IntakeRefusalCode, number> = {
  no_session: 401,
  session_expired: 401,
  wrong_role: 403,
  // 403, not 400. The body is fine; the *account* cannot be recorded as the
  // source of a document assertion because its subject is not an opaque id.
  // Nothing the caseworker can retype fixes it, so it is not a client mistake —
  // the same reasoning that puts `wrong_role` at 403. This code was added after
  // this map was written, and the `Record<IntakeRefusalCode, number>` made
  // forgetting it a compile error rather than a silent fall-through to 400.
  identity_subject: 403,
  identity_field: 400,
  unknown_field: 400,
  bad_case_id: 400,
  unknown_program: 400,
  unknown_state: 400,
  unknown_language: 400,
  bad_cert_end: 400,
  bad_income: 400,
  bad_size: 400,
  bad_reported_income: 400,
  bad_reported_size: 400,
  bad_documents: 400,
  case_exists: 409,
};

export async function POST(request: Request): Promise<Response> {
  const cookie = request.headers
    .get("cookie")
    ?.split(";")
    .map(c => c.trim())
    .find(c => c.startsWith(`${SESSION_COOKIE}=`))
    ?.slice(SESSION_COOKIE.length + 1);

  // `verifySession` throws if `COGNITO_ISSUER`/`COGNITO_CLIENT_ID` are unset —
  // a misconfiguration, not a refusal. Treated as no session, because a verifier
  // that cannot run has authenticated nobody. Fail closed.
  let session: Awaited<ReturnType<typeof verifySession>>;
  try {
    session = await verifySession(cookie);
  } catch {
    session = null;
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    // Not normalised to `{}` here the way the decide route does it: an
    // unparseable body must not be indistinguishable from an empty object, and
    // `validateIntake` refuses a non-object anyway.
    body = null;
  }

  const decision = validateIntake(session, body, Date.now());
  if (!decision.permitted) {
    return NextResponse.json(
      { error: decision.code, message: decision.message },
      { status: STATUS[decision.code] },
    );
  }

  try {
    const outcome = await createCase(decision);
    return NextResponse.json(
      {
        ...outcome,
        // Said here rather than left for the page to guess. A new case has no
        // ledger until a sweep runs, and an empty audit trail with no
        // explanation reads as a broken feature — hard rule 6's spirit at the
        // surface a human actually reads: never imply Grace has done something
        // it has not.
        message:
          "The case record is written. Grace has not run on it yet, so it has " +
          "no audit trail until the next sweep evaluates it.",
      },
      { status: 201 },
    );
  } catch (error) {
    if (error instanceof CaseAlreadyExists) {
      return NextResponse.json(
        {
          error: "case_exists" satisfies IntakeRefusalCode,
          message: `${decision.record.caseId} already exists. Choose a different case id.`,
        },
        { status: STATUS.case_exists },
      );
    }
    // The record was not written, so nothing was created. Say exactly that
    // rather than reporting a case a sweep will never find.
    return NextResponse.json(
      {
        error: "not_created",
        message: `The case was not created: ${
          error instanceof Error ? error.message : String(error)}`,
      },
      { status: 503 },
    );
  }
}
