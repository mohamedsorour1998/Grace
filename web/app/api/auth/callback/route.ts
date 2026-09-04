import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, exchangeCode, verifySession } from "@/lib/cognito";

export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  const base = process.env.DASHBOARD_URL ?? request.nextUrl.origin;
  if (!code) return NextResponse.redirect(new URL("/login", base));

  const idToken = await exchangeCode(code, `${base}/api/auth/callback`);
  // Verify the token we just received before trusting it into a cookie. The
  // exchange succeeding is not the same claim as the token being usable.
  if (!idToken || (await verifySession(idToken)) === null) {
    return NextResponse.redirect(new URL("/login", base));
  }

  const response = NextResponse.redirect(new URL("/queue", base));
  response.cookies.set(SESSION_COOKIE, idToken, {
    httpOnly: true,   // no script can read it
    secure: true,     // https only
    sameSite: "lax",  // survives the OAuth redirect, refuses cross-site POSTs
    path: "/",
    maxAge: 60 * 60,
  });
  return response;
}
