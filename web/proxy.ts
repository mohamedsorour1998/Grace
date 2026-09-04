import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/cognito";

/**
 * Every route requires a session cookie, except the two that establish one.
 *
 * This is a *presence* check, not verification: this file runs on the edge
 * runtime where the JWKS fetch and `jose` verification belong badly. Each page
 * and the decide route verify the token properly server-side. So this is
 * a redirect convenience, and **never the security boundary** — a forged cookie
 * gets past it and is then refused by `verifySession`, which is the check that
 * matters. `__tests__/route-guard.test.ts` (Task 5) proves the write path
 * refuses on its own, with nothing from this file involved.
 *
 * **The file is `proxy.ts`, not `middleware.ts`, and the export is `proxy`.**
 * Next 16.3.4 deprecated the `middleware` convention in favour of `proxy` and
 * prints `⚠ The "middleware" file convention is deprecated. Please use "proxy"
 * instead.` on every build — and clean output, not merely a zero exit code, is
 * this project's bar. `PROXY_FILENAME = "proxy"` is present in
 * `next/dist/lib/constants.js`, so the new convention is genuinely supported
 * here rather than being a forward-looking warning. **Never have both files:**
 * that is a hard error, not a warning.
 */
export function proxy(request: NextRequest) {
  if (request.cookies.get(SESSION_COOKIE)) return NextResponse.next();
  const login = new URL("/login", request.url);
  return NextResponse.redirect(login);
}

export const config = {
  // Anchored on a segment boundary — `login/` and `login$`, not bare `login`.
  // A negative lookahead on a bare prefix matches a *prefix*, so `/loginx` and
  // `/api/authorize` both slipped past an earlier version of this matcher
  // (measured). Neither route exists today, which is what makes it the kind of
  // bug that ships later: someone adds `/api/authorize` and it is ungated on
  // arrival. This file is not the security boundary — `verifySession` still
  // refuses on every page and on the decide route — so this is a redirect
  // convenience with a latent hole rather than an open door, and it costs one
  // character per alternative to close.
  //
  // `__tests__/cognito.test.ts` drives this exact string through a path table,
  // so the anchors cannot be loosened without a test failing.
  matcher: [
    "/((?!login$|login/|api/auth$|api/auth/|_next/static/|_next/image/|favicon\\.ico$).*)",
  ],
};
