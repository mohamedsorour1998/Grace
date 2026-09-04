import { describe, expect, it, beforeAll, vi } from "vitest";
import { CompactSign, SignJWT, exportJWK, generateKeyPair } from "jose";
import { verifySession } from "@/lib/cognito";
import { CASEWORKER_ROLE } from "@/lib/authorize";
import { config as proxyConfig } from "@/proxy";

const NOW = 1_788_400_000_000;
const ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TESTPOOL";
const CLIENT_ID = "test-client-id";

let sign: (claims: Record<string, unknown>, opts?: { alg?: string; expSec?: number }) => Promise<string>;
/** The private half of the injected key set, for tests that must sign raw bytes
 *  rather than go through `SignJWT` (which validates claims before signing). */
let signingKey: CryptoKey;

beforeAll(async () => {
  const { privateKey, publicKey } = await generateKeyPair("RS256");
  signingKey = privateKey;
  const jwk = { ...(await exportJWK(publicKey)), kid: "test-kid", alg: "RS256", use: "sig" };
  process.env.COGNITO_ISSUER = ISSUER;
  process.env.COGNITO_CLIENT_ID = CLIENT_ID;
  // Inject the key set so the verifier never reaches the network in tests.
  process.env.COGNITO_TEST_JWKS = JSON.stringify({ keys: [jwk] });
  sign = async (claims, opts = {}) =>
    new SignJWT({ token_use: "id", aud: CLIENT_ID, iss: ISSUER, ...claims })
      .setProtectedHeader({ alg: opts.alg ?? "RS256", kid: "test-kid" })
      .setIssuedAt(Math.floor(NOW / 1000) - 10)
      .setExpirationTime(Math.floor(NOW / 1000) + (opts.expSec ?? 3600))
      .sign(privateKey);
});

describe("verifySession — refusals", () => {
  it("returns null for a missing cookie", async () => {
    expect(await verifySession(undefined, NOW)).toBeNull();
  });

  it("returns null for a token that is not a JWT at all", async () => {
    expect(await verifySession("not-a-token", NOW)).toBeNull();
  });

  it("returns null for a token signed by the wrong key", async () => {
    const { privateKey } = await generateKeyPair("RS256");
    const forged = await new SignJWT({
      token_use: "id", aud: CLIENT_ID, iss: ISSUER,
      sub: "attacker", "custom:role": CASEWORKER_ROLE,
    })
      .setProtectedHeader({ alg: "RS256", kid: "test-kid" })
      .setExpirationTime(Math.floor(NOW / 1000) + 3600)
      .sign(privateKey);
    expect(await verifySession(forged, NOW)).toBeNull();
  });

  it("returns null for an expired token", async () => {
    const token = await sign({ sub: "s", "custom:role": CASEWORKER_ROLE }, { expSec: -60 });
    expect(await verifySession(token, NOW)).toBeNull();
  });

  it("returns null for the wrong issuer", async () => {
    const token = await sign({ sub: "s", "custom:role": CASEWORKER_ROLE, iss: "https://evil.example" });
    expect(await verifySession(token, NOW)).toBeNull();
  });

  it("returns null for the wrong audience", async () => {
    const token = await sign({ sub: "s", "custom:role": CASEWORKER_ROLE, aud: "another-client" });
    expect(await verifySession(token, NOW)).toBeNull();
  });

  it("returns null for an access token used as an ID token", async () => {
    // `token_use` distinguishes them. An access token carries no role claim, so
    // accepting one would authenticate a session with no authorisation basis.
    const token = await sign({ sub: "s", "custom:role": CASEWORKER_ROLE, token_use: "access" });
    expect(await verifySession(token, NOW)).toBeNull();
  });

  it("returns null when the role claim is absent", async () => {
    const token = await sign({ sub: "s" });
    expect(await verifySession(token, NOW)).toBeNull();
  });

  it("returns null when the role claim is close but not exact", async () => {
    for (const role of ["Caseworker", "caseworker ", "case-worker", "admin", ""]) {
      const token = await sign({ sub: "s", "custom:role": role });
      expect(await verifySession(token, NOW), role).toBeNull();
    }
  });

  it("returns null for an unsigned (alg: none) token", async () => {
    // The classic JWT bypass. `jose` should refuse it, but assert rather than
    // assume — this is the one that turns a verifier into a decoder.
    const unsigned = `${Buffer.from(JSON.stringify({ alg: "none", kid: "test-kid" })).toString("base64url")}.${
      Buffer.from(JSON.stringify({
        sub: "attacker", "custom:role": CASEWORKER_ROLE, iss: ISSUER,
        aud: CLIENT_ID, token_use: "id", exp: Math.floor(NOW / 1000) + 3600,
      })).toString("base64url")}.`;
    expect(await verifySession(unsigned, NOW)).toBeNull();
  });

  it("returns null for a token whose exp is not a finite number", async () => {
    // The plan's own findings call `Number.isFinite(payload.exp)` load-bearing
    // and then ship no test for it, so the guard was unproven. Measured here
    // rather than argued: `JSON.parse('{"exp":1e400}').exp` is `Infinity`,
    // `typeof` it is `"number"`, and `jose` **verifies** such a token — so a
    // `typeof exp === "number"` check would let it through, and `Infinity * 1000`
    // is an `expiresAt` that `authorize`'s `expiresAt <= nowMs` can never call
    // expired. A permanent session from a token that says it never expires.
    //
    // The token is hand-built because `SignJWT` refuses it at signing time
    // ('"exp" claim must be a finite number'), which is exactly why this needs a
    // raw-bytes signature: the hazard is in a token an attacker crafts, not one
    // `jose`'s own builder would produce.
    const body = JSON.stringify({
      sub: "attacker", "custom:role": CASEWORKER_ROLE, iss: ISSUER,
      aud: CLIENT_ID, token_use: "id",
    }).replace(/}$/, ',"exp":1e400}');
    expect(JSON.parse(body).exp).toBe(Infinity);   // the fixture is the hazard
    expect(typeof JSON.parse(body).exp).toBe("number");  // and it passes `typeof`
    const forged = await new CompactSign(new TextEncoder().encode(body))
      .setProtectedHeader({ alg: "RS256", kid: "test-kid" })
      .sign(signingKey);
    expect(await verifySession(forged, NOW)).toBeNull();
  });
});

describe("verifySession — the one acceptance", () => {
  it("accepts a correctly signed caseworker ID token", async () => {
    const token = await sign({ sub: "7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d", "custom:role": CASEWORKER_ROLE });
    const session = await verifySession(token, NOW);
    expect(session).not.toBeNull();
    expect(session!.sub).toBe("7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d");
    expect(session!.role).toBe(CASEWORKER_ROLE);
    expect(session!.expiresAt).toBeGreaterThan(NOW);
  });

  it("carries no email or name into the session", async () => {
    // Hard rule 9's reasoning for the caseworker: the token's claims are logged
    // to CloudTrail. `sub` is opaque; email is not, and nothing needs it.
    const token = await sign({
      sub: "7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d",
      "custom:role": CASEWORKER_ROLE,
      email: "someone@example.com", name: "A Person",
    });
    const session = await verifySession(token, NOW);
    expect(JSON.stringify(session)).not.toContain("@example.com");
    expect(JSON.stringify(session)).not.toContain("A Person");
    expect(Object.keys(session!).sort()).toEqual(["expiresAt", "role", "sub"]);
  });
});

describe("the trust anchor", () => {
  /** Set `NODE_ENV` in a way `process.env` actually accepts.
   *
   *  `NODE_ENV` is typed as read-only on `ProcessEnv`, so a plain assignment
   *  does not compile — hence `defineProperty`. Node then requires the
   *  descriptor to be configurable **and** writable **and** enumerable; any
   *  subset throws. Measured all four subsets. */
  const setNodeEnv = (value: string | undefined) => {
    Object.defineProperty(process.env, "NODE_ENV", {
      value, configurable: true, writable: true, enumerable: true,
    });
  };

  it("ignores an injected key set outside a test environment", async () => {
    // COGNITO_TEST_JWKS swaps out the trust anchor. If production code reads it,
    // setting it on the deployed app makes every forged token verify. Proving the
    // guard means proving the *same* token stops verifying when NODE_ENV changes.
    const token = await sign({ sub: "s", "custom:role": CASEWORKER_ROLE });
    expect(await verifySession(token, NOW)).not.toBeNull();

    const original = process.env.NODE_ENV;
    try {
      // The issuer points at a pool that does not exist, so the JWKS fetch
      // cannot yield a usable key. The only way this returns a session is by
      // reading the injected keys it must now ignore.
      //
      // All three descriptor flags are required. Measured: `process.env` refuses
      // `{ value, configurable: true }` with "'process.env' only accepts a
      // configurable, writable, and enumerable data descriptor", and so does
      // every other subset. The plan's draft set `configurable` alone, which
      // threw on the way *in* — and then the identical call in this `finally`
      // threw as well, replacing the original error, so the failure reported a
      // restore that never had anything to restore. Worse than the error: with
      // the write refused, `NODE_ENV` stayed `"test"`, so the assertion below
      // would have been checking the injected-keys path against itself.
      setNodeEnv("production");
      vi.resetModules();
      const { verifySession: prod } = await import("@/lib/cognito");
      // Prove the environment actually changed before trusting the refusal —
      // otherwise a silently-refused write makes this assert nothing.
      expect(process.env.NODE_ENV).toBe("production");
      expect(await prod(token, NOW)).toBeNull();
    } finally {
      setNodeEnv(original);
      vi.resetModules();
    }
    // And the guard is not one-way: the same token verifies again once the
    // environment is back. A test that left the module in a permanently refusing
    // state would look identical to one that proved the guard.
    expect(await verifySession(token, NOW)).not.toBeNull();
  });
});

describe("the proxy matcher", () => {
  /** The plan verifies this regex with a throwaway `node -e`, which proves it
   *  once and guards nothing afterwards. Read the pattern off `proxy.ts`
   *  itself so a later edit to the anchors has to fail here. (The file is
   *  `proxy.ts`, not `middleware.ts`: Next 16.3.4 deprecated the older
   *  convention and warns on every build.)
   *
   *  Next compiles a matcher with `path-to-regexp`, so this reconstruction is an
   *  approximation of the runtime behaviour — but the property under test is the
   *  negative lookahead's *anchoring*, which is plain regex either way, and the
   *  reconstruction is derived from the shipped string rather than retyped. */
  const pattern = proxyConfig.matcher[0]!;
  const asRegex = new RegExp(`^${pattern.replace(/\//g, "\\/")}$`);

  it("gates every application route, including near-misses of the exempt ones", () => {
    // `/loginx` and `/api/authorize` are the two that a bare-prefix lookahead
    // let through — measured. Neither exists yet, which is what makes this the
    // kind of hole that ships: someone adds `/api/authorize` and it arrives
    // ungated.
    for (const path of ["/", "/queue", "/case/c-010", "/api/decide", "/loginx", "/api/authorize"]) {
      expect(asRegex.test(path), `${path} must be gated`).toBe(true);
    }
  });

  it("exempts only the routes that establish a session, and the static paths", () => {
    for (const path of ["/login", "/api/auth/callback", "/_next/static/x.js", "/favicon.ico"]) {
      expect(asRegex.test(path), `${path} must be bypassed`).toBe(false);
    }
  });

  it("anchors each alternative on a segment boundary", () => {
    // The mechanical property behind both tests above: a bare `login` in the
    // lookahead matches the prefix of `loginx`. Assert the anchors are present
    // so a "simplification" that drops them fails here even if someone also
    // deletes the path tables.
    for (const anchored of ["login$", "login/", "api/auth$", "api/auth/", "favicon\\.ico$"]) {
      expect(pattern).toContain(anchored);
    }
  });
});
