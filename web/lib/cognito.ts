/**
 * TURNING A COOKIE INTO AN IDENTITY, OR INTO NOTHING.
 *
 * `verifySession` returns a `SessionIdentity` or `null`. There is no middle
 * value — a token that fails any check produces `null`, and `authorize` refuses
 * on a null session. That is deliberate: a partially-trusted session is a thing
 * nobody can reason about.
 *
 * Every check here is one an attacker would otherwise skip: the signature
 * against Cognito's published keys, the issuer, the audience, the expiry, that
 * it is an **ID** token and not an access token, and that the role claim is
 * exactly `caseworker`. `jose` performs the cryptography; the value this file
 * adds is refusing everything else.
 *
 * Only `sub`, `role`, and the expiry reach `SessionIdentity`. Email and name are
 * deliberately dropped: inbound JWT claims are logged to CloudTrail, which is
 * outside every redaction Grace has (Plan 2, Appendix D.4), and a decision row
 * records the opaque `sub`.
 */

import { createRemoteJWKSet, jwtVerify, type JWTPayload } from "jose";
import { CASEWORKER_ROLE } from "./authorize";
import type { SessionIdentity } from "./types";

export const SESSION_COOKIE = "grace_session";

const ROLE_CLAIM = "custom:role";

function issuer(): string {
  const value = process.env.COGNITO_ISSUER;
  if (!value) throw new Error("COGNITO_ISSUER is not set.");
  return value;
}

function clientId(): string {
  const value = process.env.COGNITO_CLIENT_ID;
  if (!value) throw new Error("COGNITO_CLIENT_ID is not set.");
  return value;
}

type KeyResolver = Parameters<typeof jwtVerify>[1];

/** True only for a finite number, and it tells TypeScript so.
 *
 *  `Number.isFinite` is declared `(number: unknown): boolean` — a plain boolean,
 *  not a type predicate — so `if (!Number.isFinite(payload.exp)) return null;`
 *  leaves `payload.exp` typed `number | undefined` and the multiplication below
 *  fails to compile with `TS18048: 'payload.exp' is possibly 'undefined'`. That
 *  is what the plan's draft did.
 *
 *  Wrapping it rather than adding `typeof exp === "number"` alongside keeps
 *  `Number.isFinite` the **only** runtime check, which is the point: `typeof
 *  Infinity` is `"number"`, so a `typeof` test alone accepts `exp: 1e400` — a
 *  token `jose` genuinely verifies (measured in Task 2) whose `expiresAt` no
 *  `<=` comparison can ever call expired. `Number.isFinite` refuses `undefined`,
 *  `null`, a numeric *string*, `NaN`, and both infinities without coercing
 *  anything, so this predicate is sound in both directions. The alternative —
 *  a `payload.exp!` non-null assertion — is exactly the "the promise stops being
 *  checked" hole Task 2 found in `DecisionAttempt`. */
function isFiniteNumber(value: unknown): value is number {
  return Number.isFinite(value);
}

let cachedKeys: KeyResolver | undefined;
function keys(): KeyResolver {
  // Tests inject a key set so no test reaches the network. The `NODE_ENV`
  // guard is the load-bearing part: without it, setting COGNITO_TEST_JWKS on
  // the deployed app replaces Cognito's real key set with an attacker-supplied
  // one, and every forged token verifies. An env var that swaps out the trust
  // anchor must never be readable in production. Verified: vitest sets
  // NODE_ENV="test" (and VITEST="true"); `next build`/`next start` set
  // "production".
  const injected =
    process.env.NODE_ENV === "test" ? process.env.COGNITO_TEST_JWKS : undefined;
  if (injected) {
    const parsed = JSON.parse(injected) as { keys: Record<string, unknown>[] };
    // `createLocalJWKSet(parsed)` is the supported equivalent and was verified
    // to refuse a wrong key, alg:"none", and HS256 confusion identically; a
    // resolver is used here only to keep the shape parallel to the remote one.
    return (async (header: { kid?: string }) => {
      const { importJWK } = await import("jose");
      // Select by `kid` and REFUSE when it does not match — no `?? keys[0]`
      // fallback. A real Cognito pool publishes **two** signing keys (measured
      // against Grace's own pool: two RS256 `use: "sig"` keys), one for ID
      // tokens and one for access tokens. A resolver that falls back to the
      // first key when the `kid` misses would happily verify a token signed by
      // any key in the set, which is precisely the property the wrong-key test
      // exists to disprove. This path is test-only, so the failure mode is not a
      // production bypass — it is worse in a subtler way: it would make the
      // suite unable to tell a correct verifier from one that ignores `kid`, and
      // that is the Task 8 vacuity lesson.
      const jwk = parsed.keys.find(k => k.kid === header.kid);
      if (!jwk) throw new Error(`no key for kid ${header.kid}`);
      return importJWK(jwk as never, "RS256");
    }) as unknown as KeyResolver;
  }
  cachedKeys ??= createRemoteJWKSet(
    new URL(`${issuer()}/.well-known/jwks.json`),
  ) as unknown as KeyResolver;
  return cachedKeys;
}

export async function verifySession(
  idToken: string | undefined,
  nowMs: number = Date.now(),
): Promise<SessionIdentity | null> {
  if (!idToken) return null;

  let payload: JWTPayload;
  try {
    ({ payload } = await jwtVerify(idToken, keys(), {
      issuer: issuer(),
      audience: clientId(),
      // `jose` refuses `alg: "none"` and anything not listed here. Matches the
      // pool's own `id_token_signing_alg_values_supported`, which is `["RS256"]`
      // and nothing else — so this allowlist restricts nothing legitimate.
      algorithms: ["RS256"],
      currentDate: new Date(nowMs),
    }));
  } catch {
    // Any cryptographic or claim failure is the same answer: no session.
    return null;
  }

  // An access token has no role claim, so accepting one would authenticate a
  // session with no authorisation basis behind it.
  if (payload.token_use !== "id") return null;

  const role = payload[ROLE_CLAIM];
  // Exact match. `"Caseworker"` and `"caseworker "` are not this role — the same
  // allowlist discipline `authorize` applies to the decision word.
  if (role !== CASEWORKER_ROLE) return null;

  const sub = payload.sub;
  if (typeof sub !== "string" || sub === "") return null;
  // `Number.isFinite`, not `typeof === "number"`: `exp: 1e400` in a JWT payload
  // parses to `Infinity`, which IS a number, and `jose` verifies such a token —
  // both measured during Task 2. `Infinity` then becomes an `expiresAt` that no
  // `<=` comparison can ever call expired. `authorize` refuses a non-finite
  // expiry independently (defence in depth, since this file is not its only
  // caller), but the token should not get this far. Via `isFiniteNumber` so the
  // check also narrows `exp` away from `undefined` — see that function.
  if (!isFiniteNumber(payload.exp)) return null;

  // Only these three. Email and name are dropped on purpose.
  return { sub, role: CASEWORKER_ROLE, expiresAt: payload.exp * 1000 };
}

/** Where to send a signed-out visitor. */
export function hostedUiUrl(redirectUri: string): string {
  const domain = process.env.COGNITO_DOMAIN;
  if (!domain) throw new Error("COGNITO_DOMAIN is not set.");
  const params = new URLSearchParams({
    client_id: clientId(),
    response_type: "code",
    scope: "openid",
    redirect_uri: redirectUri,
  });
  // `/login` is the **classic hosted UI** path. Verified live: Grace's domain
  // reports `ManagedLoginVersion: 1`, which serves exactly this page. Managed
  // login (version 2) uses a different URL shape and needs a branding style, so
  // do not "upgrade" the domain without rewriting this builder and re-testing
  // the redirect end to end.
  return `${domain}/login?${params.toString()}`;
}

/** Exchange an authorization code for an ID token. Server-side only. */
export async function exchangeCode(
  code: string,
  redirectUri: string,
): Promise<string | null> {
  const domain = process.env.COGNITO_DOMAIN;
  if (!domain) throw new Error("COGNITO_DOMAIN is not set.");
  const response = await fetch(`${domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: clientId(),
      code,
      redirect_uri: redirectUri,
    }),
  });
  if (!response.ok) return null;
  const body = (await response.json()) as { id_token?: string };
  return body.id_token ?? null;
}
