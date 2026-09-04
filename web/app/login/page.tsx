import { redirect } from "next/navigation";
import { hostedUiUrl } from "@/lib/cognito";

/**
 * `force-dynamic`, because this page must be evaluated per request.
 *
 * Without it `next build` prerenders `/login` as a static page and
 * `hostedUiUrl` runs at **build** time — which fails the build outright when
 * `COGNITO_DOMAIN` is absent from the build environment (measured:
 * `Error: COGNITO_DOMAIN is not set.` … `Export encountered an error on
 * /login/page`, exit 1). That alone would be caught, but the quieter failure is
 * the one that matters: with the variable *present* at build time, the redirect
 * URL — including `DASHBOARD_URL` and the client id — is baked into the bundle,
 * so rotating the app client or moving the dashboard's hostname silently keeps
 * sending caseworkers to the old one until someone rebuilds. A sign-in redirect
 * is request-time configuration, not build-time content.
 */
export const dynamic = "force-dynamic";

export default function Login() {
  const base = process.env.DASHBOARD_URL ?? "http://localhost:3000";
  redirect(hostedUiUrl(`${base}/api/auth/callback`));
}
