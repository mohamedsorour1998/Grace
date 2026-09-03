/**
 * WHO MAY DECIDE WHAT. Pure, and deliberately so.
 *
 * This is the dashboard's `grace/authority.py`: it maps a session, a case's
 * facts, and an attempted decision onto `Permit` or `Refusal`, and it touches
 * nothing. No AWS client, no HTTP, no clock read — the current time arrives as
 * an argument. `lib/cases.ts` measures the facts; this file decides over them,
 * and `__tests__/authorize.test.ts` proves the import graph stays clean.
 *
 * Two properties worth stating outright, both inherited from Plan 1:
 *
 * **The decision word is an allowlist.** Task 6 proved a denylist makes the
 * *unrecognised* answer the dangerous one: "Escalate.", "no, hold this one",
 * and "needs review" each resumed a paused graph and filed a renewal for a
 * household missing a required document. Only an exact `"approve"` or `"deny"`
 * is honoured here; everything else refuses.
 *
 * **A permit is not a filing.** Permitting an approve only authorises writing
 * the decision row and re-invoking the runtime. The authority gate then
 * re-evaluates the case facts and may still refuse to file — approving `c-010`
 * must not file, because the document is still missing.
 */

import type { CaseStatus, SessionIdentity } from "./types";

export const CASEWORKER_ROLE = "caseworker";

/** A caseworker's note is free text stored verbatim; bound it so a single
 *  request cannot write an unbounded item to DynamoDB. */
export const MAX_NOTE_LENGTH = 2000;

export type DecisionKind = "approve" | "deny";

/** Exactly the two words honoured. A `Set` of literals, not a regex: a regex
 *  invites `/approve/i` and case-insensitivity is how "Approve " gets in. */
const DECISIONS: ReadonlySet<string> = new Set<DecisionKind>(["approve", "deny"]);

export interface CaseFacts {
  caseId: string;
  status: CaseStatus;
  alreadyDecided: boolean;
}

export interface DecisionAttempt {
  decision: DecisionKind;
  note: string;
}

export type RefusalCode =
  | "no_session"
  | "session_expired"
  | "wrong_role"
  | "unknown_case"
  | "not_escalated"
  | "already_decided"
  | "unknown_decision"
  | "note_too_long";

export interface Refusal {
  permitted: false;
  code: RefusalCode;
  message: string;
}

export interface Permit {
  permitted: true;
  decidedBy: string;
  decision: DecisionKind;
  note: string;
}

export type Authorisation = Permit | Refusal;

function refuse(code: RefusalCode, message: string): Refusal {
  return { permitted: false, code, message };
}

export function authorize(
  session: SessionIdentity | null,
  facts: CaseFacts | null,
  attempt: DecisionAttempt,
  nowMs: number,
): Authorisation {
  if (session === null) {
    return refuse("no_session", "Sign in to decide a case.");
  }
  // Refuse anything that is not a finite number of milliseconds. `expiresAt`
  // arrives from a decoded JWT claim, and `NaN <= nowMs` is `false` — so a
  // malformed expiry would slip past the comparison below and be treated as a
  // session that never expires. Same reasoning as Plan 2's NaN finding: a NaN
  // reads back as a number and behaves like nothing.
  if (!Number.isFinite(session.expiresAt) || !Number.isFinite(nowMs)) {
    return refuse("session_expired", "Your session expired. Sign in again.");
  }
  // `<=` and not `<`: a session expiring exactly now is expired. Written the
  // other way, a just-expired session is honoured, which is the fail-open
  // direction.
  if (session.expiresAt <= nowMs) {
    return refuse("session_expired", "Your session expired. Sign in again.");
  }
  if (session.role !== CASEWORKER_ROLE) {
    return refuse("wrong_role", "This account may not decide cases.");
  }
  // `null` covers both "no such case" and "the case could not be read".
  // `lib/cases.ts` collapses them at the measurement, so this function cannot
  // tell them apart even if a later edit wanted to.
  if (facts === null) {
    return refuse("unknown_case", "No such case.");
  }
  if (facts.status !== "escalated") {
    return refuse(
      "not_escalated",
      "Grace handled this case itself; there is nothing to decide.",
    );
  }
  if (facts.alreadyDecided) {
    return refuse("already_decided", "A caseworker has already decided this case.");
  }
  if (!DECISIONS.has(attempt.decision)) {
    return refuse("unknown_decision", "Choose approve or deny.");
  }
  // A route handler builds `attempt` from a JSON body, so `note` can arrive as
  // anything a client sends. `.length` on a non-string is `undefined`, and
  // `undefined > MAX_NOTE_LENGTH` is `false` — the cap would pass silently and
  // an object would reach the decision row. Refuse the type, do not coerce it:
  // coercion invents a note nobody wrote.
  if (typeof attempt.note !== "string") {
    return refuse("note_too_long", `Keep the note under ${MAX_NOTE_LENGTH} characters.`);
  }
  if (attempt.note.length > MAX_NOTE_LENGTH) {
    return refuse("note_too_long", `Keep the note under ${MAX_NOTE_LENGTH} characters.`);
  }
  return {
    permitted: true,
    decidedBy: session.sub,
    decision: attempt.decision,
    note: attempt.note,
  };
}
