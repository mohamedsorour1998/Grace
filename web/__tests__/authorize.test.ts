import { describe, expect, it } from "vitest";
import { authorize, CASEWORKER_ROLE, MAX_NOTE_LENGTH } from "@/lib/authorize";
import type { Authorisation, CaseFacts, DecisionAttempt, Permit, Refusal } from "@/lib/authorize";
import type { SessionIdentity } from "@/lib/types";

const NOW = 1_788_400_000_000;
const session = (over: Partial<SessionIdentity> = {}): SessionIdentity => ({
  sub: "7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d",
  role: CASEWORKER_ROLE,
  expiresAt: NOW + 60_000,
  ...over,
});
const escalated = (over: Partial<CaseFacts> = {}): CaseFacts => ({
  caseId: "c-011",
  status: "escalated",
  alreadyDecided: false,
  ...over,
});
const approve: DecisionAttempt = { decision: "approve", note: "Wage record is stale." };

// The plan's draft asserted refusal codes inside `if (!r.permitted) { ... }`.
// That body does not run on a permit, so the code assertion silently vanishes
// on exactly the outcome it was written to catch. These two helpers narrow by
// *throwing*, so every assertion below is unconditional — the Task 8 vacuity
// lesson applied to a TypeScript discriminated union.
function refusalOf(r: Authorisation): Refusal {
  if (r.permitted) throw new Error(`expected a refusal, got a permit: ${JSON.stringify(r)}`);
  return r;
}
function permitOf(r: Authorisation): Permit {
  if (!r.permitted) throw new Error(`expected a permit, got ${r.code}: ${r.message}`);
  return r;
}

describe("authorize — refusals", () => {
  it("refuses with no session at all", () => {
    expect(refusalOf(authorize(null, escalated(), approve, NOW)).code).toBe("no_session");
  });

  it("refuses an expired session, even one millisecond past", () => {
    // Boundary, not a round number: `<=` written as `<` honours a
    // just-expired session, and that is the direction that fails open.
    expect(refusalOf(authorize(session({ expiresAt: NOW }), escalated(), approve, NOW)).code)
      .toBe("session_expired");
    expect(refusalOf(authorize(session({ expiresAt: NOW - 1 }), escalated(), approve, NOW)).code)
      .toBe("session_expired");
  });

  it("refuses an expiry that is not a finite number of milliseconds", () => {
    // Reachable, not defensive padding: `exp: 1e400` in a JWT payload parses to
    // `Infinity`, `jose` verifies such a token happily (measured), and Task 4's
    // `typeof payload.exp !== "number"` check passes it through — `Infinity` is
    // a number. `Infinity <= nowMs` is `false`, so without this guard the
    // session never expires. Plan 2's NaN finding in the other direction: a
    // non-finite number reads back as a number and behaves like nothing.
    for (const bad of [Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, Number.NaN]) {
      expect(refusalOf(authorize(session({ expiresAt: bad }), escalated(), approve, NOW)).code)
        .toBe("session_expired");
    }
    // The same must hold for a clock that arrives unusable.
    expect(refusalOf(authorize(session(), escalated(), approve, Number.NaN)).code)
      .toBe("session_expired");
  });

  it("refuses a session without the caseworker role", () => {
    // Exact match, so near-misses refuse too. `"Caseworker"` and `"caseworker "`
    // are not this role — the same allowlist polarity as the decision word.
    for (const role of ["viewer", "Caseworker", "CASEWORKER", "caseworker ", ""]) {
      expect(refusalOf(authorize(session({ role }), escalated(), approve, NOW)).code)
        .toBe("wrong_role");
    }
  });

  it("refuses a case that does not exist", () => {
    // `null` facts collapse "no such case" and "unreadable case" into one
    // answer on purpose — see the note in lib/cases.ts.
    expect(refusalOf(authorize(session(), null, approve, NOW)).code).toBe("unknown_case");
  });

  it("refuses a case Grace handled itself", () => {
    // Deciding an `acted` case would let a human retroactively "approve"
    // something already filed, which the audit trail would then imply they
    // authorised. `error` is not decidable either: a case whose sweep failed has
    // no measured verdict to approve.
    for (const status of ["acted", "error"] as const) {
      expect(refusalOf(authorize(session(), escalated({ status }), approve, NOW)).code)
        .toBe("not_escalated");
    }
  });

  it("refuses a second decision on the same case", () => {
    expect(refusalOf(authorize(session(), escalated({ alreadyDecided: true }), approve, NOW)).code)
      .toBe("already_decided");
  });

  it("refuses any decision word that is not exactly approve or deny", () => {
    // An ALLOWLIST, not a denylist. Plan 1's Task 6 proved a denylist makes the
    // UNRECOGNISED answer the dangerous one: "Escalate.", "no, hold this one",
    // and "needs review" all resumed a graph and filed a renewal for a
    // household missing a document. Anything unrecognised must refuse.
    const words = ["Approve", "APPROVE", "approve ", " approve", "approved", "yes", "file",
      "proceed", "needs review", "no, hold this one", "", "escalate", "Escalate.", "deny "];
    let checked = 0;
    for (const bad of words) {
      const r = authorize(session(), escalated(), { decision: bad as "approve", note: "x" }, NOW);
      expect(refusalOf(r).code, `${JSON.stringify(bad)} must refuse`).toBe("unknown_decision");
      checked += 1;
    }
    // A loop that never ran would assert nothing while reporting a pass.
    expect(checked).toBe(words.length);
  });

  it("refuses a note longer than the cap", () => {
    expect(refusalOf(authorize(session(), escalated(),
      { decision: "deny", note: "x".repeat(MAX_NOTE_LENGTH + 1) }, NOW)).code)
      .toBe("note_too_long");
    // The boundary itself is allowed; an off-by-one here refuses a legitimate note.
    expect(permitOf(authorize(session(), escalated(),
      { decision: "deny", note: "x".repeat(MAX_NOTE_LENGTH) }, NOW)).note.length)
      .toBe(MAX_NOTE_LENGTH);
  });

  it("refuses a note that is not a string", () => {
    // `.length` on a non-string is `undefined`, and `undefined > MAX_NOTE_LENGTH`
    // is `false` — so the cap passes silently and a non-string reaches the
    // decision row. `null` is worse: `.length` throws, and an exception out of
    // the pure gate is a 500 rather than a refusal. Refuse the type; coercing
    // would invent a note nobody wrote.
    for (const bad of [null, undefined, 42, {}, [], { length: 99999 }]) {
      expect(refusalOf(authorize(session(), escalated(),
        { decision: "deny", note: bad as unknown as string }, NOW)).code)
        .toBe("note_too_long");
    }
  });

  it("orders its checks so a refusal never leaks whether a case exists", () => {
    // An unauthenticated or wrong-role caller must not learn the difference
    // between a case that exists and one that does not. Session checks come
    // first, so both inputs give the same answer.
    expect(refusalOf(authorize(null, escalated(), approve, NOW)).code)
      .toBe(refusalOf(authorize(null, null, approve, NOW)).code);
    expect(refusalOf(authorize(session({ role: "viewer" }), escalated(), approve, NOW)).code)
      .toBe(refusalOf(authorize(session({ role: "viewer" }), null, approve, NOW)).code);
  });
});

describe("authorize — permits", () => {
  it("permits an approve from a valid caseworker on an escalated case", () => {
    const p = permitOf(authorize(session(), escalated(), approve, NOW));
    expect(p.decision).toBe("approve");
    expect(p.decidedBy).toBe(session().sub);
    expect(p.note).toBe("Wage record is stale.");
  });

  it("permits a session expiring one millisecond from now", () => {
    expect(permitOf(authorize(session({ expiresAt: NOW + 1 }), escalated(), approve, NOW)).decision)
      .toBe("approve");
  });

  it("permits a deny just as readily", () => {
    expect(permitOf(authorize(session(), escalated(), { decision: "deny", note: "" }, NOW)).decision)
      .toBe("deny");
  });

  it("carries the opaque sub, never a name", () => {
    // Hard rule 9's reasoning applied to the caseworker: the JWT `sub` is
    // logged to CloudTrail, which is outside every redaction Grace has.
    const p = permitOf(authorize(session(), escalated(), approve, NOW));
    expect(p.decidedBy).toMatch(/^[0-9a-f-]{36}$/);
    expect(p.decidedBy).not.toMatch(/@/);
  });

  it("carries nothing beyond the four fields a decision row needs", () => {
    // A permit is what `recordDecision` writes from. If `caseId`, a name, or a
    // whole session object rode along, hard rule 9's surface would widen without
    // anyone choosing to widen it.
    const p = permitOf(authorize(session(), escalated(), approve, NOW));
    expect(Object.keys(p).sort()).toEqual(["decidedBy", "decision", "note", "permitted"]);
  });

  it("permits without filing anything — a permit is not a filing", () => {
    // Stated as a test because it is the property most easily misread. Approving
    // `c-010`, a household missing a required document, is permitted here; the
    // authority gate re-evaluates the case record afterwards and still refuses
    // to file. This function authorises writing a decision row and re-invoking
    // the runtime, nothing more, which is why its result carries no verdict.
    const p = permitOf(authorize(session(), escalated({ caseId: "c-010" }), approve, NOW));
    expect(Object.keys(p)).not.toContain("filed");
    expect(Object.keys(p)).not.toContain("caseId");
  });
});

describe("authorize — purity", () => {
  it("is deterministic and mutates nothing it is given", () => {
    const s = session(); const f = escalated();
    const before = JSON.stringify({ s, f, approve });
    const a = authorize(s, f, approve, NOW);
    const b = authorize(s, f, approve, NOW);
    expect(JSON.stringify({ s, f, approve })).toBe(before);
    expect(a).toEqual(b);
  });

  it("imports nothing that performs I/O, and reads no clock", async () => {
    // Structural, so the purity survives a future edit. `authority.py` is
    // guarded the same way with a pkgutil walk; this is the TypeScript
    // equivalent, and it is why every refusal above needs no AWS.
    //
    // The plan's draft checked five literal spellings, which left three holes —
    // all three measured passing against it: `from "fs"` (it only forbade
    // `node:fs`), `new Date().getTime()` (it only forbade `Date.now()`), and
    // `globalThis.fetch` (it only forbade `fetch(`). A denylist of spellings
    // someone remembered is the same mistake Task 4's model-ID guard fixed by
    // discovering modules from disk. So: enumerate the imports that ARE there
    // and require every one to be type-only and relative.
    const { readFileSync } = await import("node:fs");
    const src = readFileSync(new URL("../lib/authorize.ts", import.meta.url), "utf8");

    const imports = [...src.matchAll(/^\s*import\s+([^;]+?)\s+from\s+["']([^"']+)["']/gm)]
      .map(m => ({ clause: m[1] ?? "", specifier: m[2] ?? "" }));
    expect(imports.length, "authorize.ts should import something, or this guard is vacuous")
      .toBeGreaterThan(0);
    for (const { clause, specifier } of imports) {
      // Type-only: erased at compile time, so it cannot execute I/O even if the
      // module it names would.
      expect(clause, `${specifier} must be imported as \`import type\``).toMatch(/^type\b/);
      // Relative: a bare specifier is a package, and no package in this
      // dependency tree is I/O-free.
      expect(specifier, `${specifier} must be a relative sibling`).toMatch(/^\.\.?\//);
    }

    // Anything that reaches outside the arguments, whatever its spelling.
    const forbidden: [RegExp, string][] = [
      [/@aws-sdk/, "an AWS SDK client"],
      [/\bfetch\b/, "fetch"],
      [/\brequire\s*\(/, "require()"],
      [/\bimport\s*\(/, "a dynamic import"],
      [/\bprocess\b/, "process"],
      [/\bDate\b/, "a clock read (Date)"],
      [/\bperformance\s*\./, "a clock read (performance)"],
      [/\bMath\.random\b/, "randomness"],
      [/\bglobalThis\b/, "globalThis"],
    ];
    for (const [pattern, what] of forbidden) {
      expect(pattern.test(src), `authorize.ts must not reference ${what}`).toBe(false);
    }
  });
});
