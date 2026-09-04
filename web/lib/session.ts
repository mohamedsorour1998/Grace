/**
 * THE SESSION GATE FOR A PAGE, as opposed to for the write route.
 *
 * **This existed nowhere before Task 6, and its absence was measured.**
 * `proxy.ts`'s docstring states that it is "a redirect convenience, and **never
 * the security boundary** — a forged cookie gets past it and is then refused by
 * `verifySession`, which is the check that matters", and says `verifySession`
 * "still refuses on every page and on the decide route". The second half was
 * true of the decide route and **false of every page**: `grep verifySession`
 * across `app/` matched only `api/auth/callback` and `api/case/[id]/decide`. No
 * page read a cookie at all.
 *
 * So the pages' only guard was the presence check in `proxy.ts`. Measured
 * against a real `next start` on the live table:
 *
 *     curl -s -o /dev/null -w '%{http_code} %{redirect_url}' localhost:3111/
 *       -> 307 http://localhost:3111/login              (no cookie: redirected)
 *     curl -H 'Cookie: grace_session=totally.forged.token' localhost:3111/
 *       -> 200, 45143 bytes, all twelve case ids, all three typed escalation
 *          reasons, and the 9-handled/3-waiting headline
 *
 * An unsigned, unparseable string — not a forged JWT, a literal sentence — was a
 * complete authentication bypass for every read in the application. That is a
 * *worse* position than having no gate at all, because the surrounding
 * documentation asserted the gate was there.
 *
 * `requireSession` closes it the way the decide route already does: verify the
 * signature, issuer, audience, expiry, `token_use`, and role against Cognito's
 * published keys, and send anything else to `/login`. Called first in every page
 * that reads household data, before `lib/cases.ts` is touched — so an
 * unauthenticated request performs no DynamoDB read either.
 */

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { SESSION_COOKIE, verifySession } from "@/lib/cognito";
import type { SessionIdentity } from "@/lib/types";

/** Verify the session, or redirect to sign-in. Never returns a partial identity.
 *
 *  `redirect()` throws a Next control-flow error, so a caller cannot forget to
 *  branch on the result — there is no falsy value to ignore. Same reasoning as
 *  `verifySession` having no middle value between an identity and `null`.
 *
 *  **The `catch` assigns `null`, and the reason it must is not that it fires
 *  today — it is that nothing proves it cannot.** Measured while testing this
 *  file: `verifySession` never throws for any input, because `issuer()` and
 *  `clientId()` are called *inside* its own `try`, so even an unset
 *  `COGNITO_ISSUER` comes back as `null` rather than as an exception (probed with
 *  a forged string, `123`, `{}`, and `[]` — all `null`). So this `catch` is
 *  currently unreachable, and a version of it that fabricated
 *  `{ role: "caseworker" }` passed all 155 tests precisely because no input could
 *  reach it. That is the Plan 2 lesson about a fake that cannot fail: an
 *  unreachable branch is still shipped code, and the next edit to `cognito.ts`
 *  that moves a `throw` outside that `try` makes this one live. The value that
 *  fails closed is the only correct one to leave here, and
 *  `__tests__/render.test.ts` asserts it by *calling* this function with a
 *  throwing verifier rather than by grepping for the assignment — a source-shape
 *  guard was what the fabricated version slipped past. */
export async function requireSession(): Promise<SessionIdentity> {
  let session: SessionIdentity | null;
  try {
    // `cookies()` is inside the `try` on purpose. It throws outside a request
    // scope, and with it above the `try` that throw propagated past this
    // function's own fail-closed handling — so the one call here that genuinely
    // can fail was the one not covered. Plan 1 Task 6 found the identical shape
    // in `list_documents`: "this function already fails closed" is not the same
    // claim as "every line in it is inside the `try`".
    const jar = await cookies();
    session = await verifySession(jar.get(SESSION_COOKIE)?.value);
  } catch {
    // A verifier that could not run has authenticated nobody. Fail closed.
    session = null;
  }
  if (session === null) redirect("/login");
  return session;
}
