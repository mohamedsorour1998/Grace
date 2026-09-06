/**
 * SIGNING OUT, ON BOTH SIDES.
 *
 * Clearing Grace's cookie is only half of it. Cognito keeps its own session
 * cookie on the sign-in domain, valid for an hour, so a caseworker who "signed
 * out" and clicked Sign in again would be returned straight to the dashboard
 * without being asked for anything — which reads as the sign-out having silently
 * failed. This clears the local cookie *and* redirects to Cognito's `/logout`
 * endpoint, which clears theirs and then sends the browser to a registered
 * logout URL.
 *
 * `logout_uri` must be one of the app client's `LogoutURLs` or Cognito refuses
 * the request outright; `https://grace.rosettacloud.app/login` is registered.
 *
 * GET rather than POST, deliberately: this is a navigation, it destroys nothing
 * a user would miss, and a link is what a nav bar can offer. The decide route is
 * POST-only for the opposite reason — it writes.
 */

import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/cognito";

export async function GET(request: NextRequest): Promise<Response> {
  const base = process.env.DASHBOARD_URL ?? request.nextUrl.origin;
  const domain = process.env.COGNITO_DOMAIN;
  const clientId = process.env.COGNITO_CLIENT_ID;

  // Where to land once both sessions are gone. `/login` immediately bounces to
  // the hosted UI, so the caseworker sees a sign-in prompt rather than a page
  // that looks signed-out-but-idle.
  const landing = `${base}/login`;

  // If Cognito is unconfigured, still clear the local cookie and go somewhere
  // sensible. A sign-out that throws would leave the session intact, which is
  // the one outcome this route must never produce — fail *open* here, because
  // "open" means signed out.
  const target =
    domain && clientId
      ? `${domain}/logout?client_id=${encodeURIComponent(clientId)}` +
        `&logout_uri=${encodeURIComponent(landing)}`
      : landing;

  const response = NextResponse.redirect(target);
  // Same attributes the callback set it with. A cookie deleted with a different
  // path or sameSite is a cookie that survives — the browser matches on those,
  // not just the name.
  response.cookies.set(SESSION_COOKIE, "", {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
  return response;
}
