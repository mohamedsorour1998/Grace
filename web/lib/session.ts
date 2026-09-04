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
 *  A `verifySession` that *throws* (an unset `COGNITO_ISSUER` or
 *  `COGNITO_CLIENT_ID`) is treated as no session, exactly as the decide route
 *  treats it: a verifier that cannot run has authenticated nobody. Fail closed. */
export async function requireSession(): Promise<SessionIdentity> {
  const jar = await cookies();
  const token = jar.get(SESSION_COOKIE)?.value;

  let session: SessionIdentity | null;
  try {
    session = await verifySession(token);
  } catch {
    session = null;
  }
  if (session === null) redirect("/login");
  return session;
}
