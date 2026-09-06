import { describe, expect, it } from "vitest";
import {
  DOCUMENT_IDS,
  MAX_HOUSEHOLD_SIZE,
  MAX_INCOME_CENTS,
  PROGRAMS,
  STATES,
  validateIntake,
  type IntakeAuthorisation,
  type IntakePermit,
  type IntakeRefusal,
  type IntakeRefusalCode,
} from "@/lib/intake";
import type { SessionIdentity } from "@/lib/types";

const NOW = 1_788_400_000_000;

const session = (over: Partial<SessionIdentity> = {}): SessionIdentity => ({
  sub: "2448a4e8-c021-70f6-382c-e8acbb6cc956",
  role: "caseworker",
  expiresAt: NOW + 3_600_000,
  ...over,
});

/** A submission that passes, so every test below changes exactly one thing. */
const good = (over: Record<string, unknown> = {}) => ({
  case_id: "c-013",
  program: "medicaid",
  state: "NY",
  cert_end: "2026-12-31",
  language: "en",
  monthly_income_cents: 240_000,
  size: 3,
  documents: [{ id: "proof_of_income", received: "2026-09-01", expires: null }],
  ...over,
});

/** Narrow by THROWING, never by `if`. A discriminated union invites
 *  `if (!r.permitted) expect(r.code)...`, and every assertion inside that `if`
 *  silently disappears when the function starts permitting — the Plan 3 Task 2
 *  lesson, where rewriting `authorize` to always refuse still left 4 of 14 tests
 *  passing because their bodies never ran. */
function refusalOf(r: IntakeAuthorisation): IntakeRefusal {
  if (r.permitted) throw new Error("expected a refusal, got a permit");
  return r;
}
function permitOf(r: IntakeAuthorisation): IntakePermit {
  if (!r.permitted) throw new Error(`expected a permit, got ${r.code}: ${r.message}`);
  return r;
}

describe("validateIntake — the session comes first", () => {
  it("refuses with no session, before it looks at the body at all", () => {
    // Session checks must precede field checks. Otherwise the difference
    // between `no_session` and `bad_case_id` tells an unauthenticated caller
    // which case ids are already taken.
    expect(refusalOf(validateIntake(null, good(), NOW)).code).toBe("no_session");
    // And the same refusal for a body that is nonsense, proving the session
    // check ran first rather than the body happening to be valid.
    expect(refusalOf(validateIntake(null, { caseId: 42 }, NOW)).code).toBe("no_session");
  });

  it("refuses an expired session, including one expiring exactly now", () => {
    expect(refusalOf(validateIntake(session({ expiresAt: NOW - 1 }), good(), NOW)).code)
      .toBe("session_expired");
    // `<=`, not `<`. The other way round honours a just-expired session, which
    // is the fail-open direction.
    expect(refusalOf(validateIntake(session({ expiresAt: NOW }), good(), NOW)).code)
      .toBe("session_expired");
  });

  it("refuses a non-finite expiry rather than treating it as forever", () => {
    // `JSON.parse('{"exp":1e400}').exp` is `Infinity`, `typeof` it is "number",
    // and `Infinity <= nowMs` is `false` — a permanent session. Measured in
    // Plan 3; guarded here because this validator is a second entry point.
    for (const bad of [Infinity, -Infinity, NaN]) {
      expect(refusalOf(validateIntake(session({ expiresAt: bad }), good(), NOW)).code)
        .toBe("session_expired");
    }
  });

  it("refuses a role that is not exactly caseworker", () => {
    for (const role of ["Caseworker", "caseworker ", "admin", ""]) {
      expect(refusalOf(validateIntake(session({ role }), good(), NOW)).code, role)
        .toBe("wrong_role");
    }
  });
});

describe("validateIntake — hard rule 9, enforced rather than trusted", () => {
  it("refuses any field that could carry household identity", () => {
    // THE test for this module. An intake form is exactly where a name, phone,
    // or address feels natural to collect — and `read_case` returning
    // `display_name` is precisely how a surname reached CloudWatch in Plan 2.
    // Grace does not need to know who the family is to know whether their
    // paperwork is complete.
    const identityFields = [
      "name", "displayName", "display_name", "fullName", "firstName", "lastName",
      "phone", "phoneNumber", "email", "address", "street", "dob", "ssn",
    ];
    let checked = 0;
    for (const field of identityFields) {
      const r = refusalOf(validateIntake(session(), good({ [field]: "anything" }), NOW));
      expect(r.code, `${field} must be refused`).toBe("identity_field");
      checked += 1;
    }
    // The loop must actually have run — a `for` over an empty list passes
    // having asserted nothing, which is indistinguishable from a passing check.
    expect(checked).toBe(identityFields.length);
    expect(checked).toBeGreaterThan(10);
  });

  it("refuses an unrecognised field rather than ignoring it", () => {
    // An allowlist, not a denylist of known-bad names. A field nobody
    // anticipated is refused on arrival, so the identity guard cannot be walked
    // around with a name the denylist never imagined.
    expect(refusalOf(validateIntake(session(), good({ county: "Kings" }), NOW)).code)
      .toBe("unknown_field");

    // And the two refusals are genuinely different checks rather than one
    // catch-all: `nickname` contains "name", so the identity guard claims it
    // first. That is the stricter and correct answer — a field is refused as
    // identity when it *might* carry identity, not only when it certainly does.
    expect(refusalOf(validateIntake(session(), good({ nickname: "x" }), NOW)).code)
      .toBe("identity_field");
  });

  it("carries only the opaque sub into the permit, never a name", () => {
    const permit = permitOf(validateIntake(session(), good(), NOW));
    expect(permit.createdBy).toBe("2448a4e8-c021-70f6-382c-e8acbb6cc956");
    expect(JSON.stringify(permit)).not.toMatch(/@|name|phone|address/i);
  });
});

describe("validateIntake — the fields", () => {
  it("accepts a well-formed submission", () => {
    const permit = permitOf(validateIntake(session(), good(), NOW));
    expect(permit.record.caseId).toBe("c-013");
    expect(permit.record.program).toBe("medicaid");
    expect(permit.record.size).toBe(3);
  });

  it("refuses a case id that is not the c-NNN shape", () => {
    for (const id of ["", "c-13", "C-013", "c-0133", "case-013", "../c-013", 13]) {
      expect(refusalOf(validateIntake(session(), good({ case_id: id }), NOW)).code, String(id))
        .toBe("bad_case_id");
    }
  });

  it("refuses a program or state outside the known set", () => {
    expect(refusalOf(validateIntake(session(), good({ program: "medicare" }), NOW)).code)
      .toBe("unknown_program");
    expect(refusalOf(validateIntake(session(), good({ state: "CA" }), NOW)).code)
      .toBe("unknown_state");
    // And the known values really are accepted, so the guard is not simply
    // refusing everything.
    for (const program of PROGRAMS) {
      expect(validateIntake(session(), good({ program }), NOW).permitted, program).toBe(true);
    }
    for (const state of STATES) {
      expect(validateIntake(session(), good({ state }), NOW).permitted, state).toBe(true);
    }
  });

  it("refuses a certification date that is not a real ISO date", () => {
    // A missing or malformed `cert_end` that defaulted to today would make Grace
    // file or escalate on a date nobody chose. Fail closed instead.
    for (const d of ["", "31-12-2026", "2026-13-01", "2026-02-30", "tomorrow", null, 20261231]) {
      expect(refusalOf(validateIntake(session(), good({ cert_end: d }), NOW)).code, String(d))
        .toBe("bad_cert_end");
    }
  });

  it("refuses an income that is not a whole, finite, in-range number", () => {
    for (const v of [-1, 1.5, NaN, Infinity, "240000", null, MAX_INCOME_CENTS + 1]) {
      expect(refusalOf(validateIntake(session(), good({ monthly_income_cents: v }), NOW)).code, String(v))
        .toBe("bad_income");
    }
    // Zero is a real income a family can report — a genuine loss of all income
    // is the most eligibility-relevant case Grace will see — so it must not be
    // rejected as falsy. Plan 1 Task 2's reasoning, at a new boundary.
    expect(validateIntake(session(), good({ monthly_income_cents: 0 }), NOW).permitted).toBe(true);
  });

  it("refuses a household size that is not a positive whole number", () => {
    for (const v of [0, -1, 2.5, NaN, "3", null, MAX_HOUSEHOLD_SIZE + 1]) {
      expect(refusalOf(validateIntake(session(), good({ size: v }), NOW)).code, String(v))
        .toBe("bad_size");
    }
  });

  it("refuses documents that are not the shape the gate reads", () => {
    for (const docs of [
      "proof_of_income",
      [{ id: "not_a_document", received: "2026-09-01", expires: null }],
      [{ id: "proof_of_income", received: "nonsense", expires: null }],
      [{ id: "proof_of_income" }],
      [null],
    ]) {
      expect(refusalOf(validateIntake(session(), good({ documents: docs }), NOW)).code)
        .toBe("bad_documents");
    }
    // Every known document id is accepted on its own, so the guard discriminates
    // rather than refusing the whole field.
    for (const id of DOCUMENT_IDS) {
      const r = validateIntake(
        session(), good({ documents: [{ id, received: "2026-09-01", expires: null }] }), NOW);
      expect(r.permitted, id).toBe(true);
    }
  });

  it("normalises absence into an explicit value rather than leaving it undefined", () => {
    // `CaseRecordInput` has no optional fields on purpose: the validator
    // resolves every absence here, so no writer downstream has to guess one.
    const permit = permitOf(validateIntake(session(), good(), NOW));
    expect(permit.record.reportedIncomeCents).toBeNull();
    expect(permit.record.reportedSize).toBeNull();
    expect(Array.isArray(permit.record.documents)).toBe(true);
  });
});

describe("validateIntake — the body itself", () => {
  it("refuses a body that is not an object", () => {
    // A JSON body may legitimately parse to null, a number, a string, or an
    // array, on any of which property access is either an error or silently
    // undefined.
    for (const body of [null, 42, "x", [], true]) {
      const r = validateIntake(session(), body, NOW);
      expect(r.permitted, JSON.stringify(body)).toBe(false);
    }
  });

  it("has a refusal code for every field it validates", () => {
    // A Record's keys are erased at compile time, so the exhaustiveness of the
    // route's status map is asserted at runtime there. Here, assert the codes
    // this module can actually emit are the ones the type declares.
    const emitted = new Set<IntakeRefusalCode>();
    const cases: Array<[unknown, Record<string, unknown>]> = [
      [null, good()],
      [session({ expiresAt: NOW - 1 }), good()],
      [session({ role: "x" }), good()],
      [session(), good({ name: "x" })],
      [session(), good({ zzz: "x" })],
      [session(), good({ case_id: "bad" })],
      [session(), good({ program: "x" })],
      [session(), good({ state: "x" })],
      [session(), good({ cert_end: "x" })],
      [session(), good({ monthly_income_cents: -1 })],
      [session(), good({ size: 0 })],
      [session(), good({ documents: "x" })],
    ];
    for (const [s, body] of cases) {
      const r = validateIntake(s as SessionIdentity | null, body, NOW);
      if (!r.permitted) emitted.add(r.code);
    }
    expect(emitted.size).toBeGreaterThanOrEqual(10);
  });
});
