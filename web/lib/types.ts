/**
 * The shapes every later task imports by name.
 *
 * These mirror what Grace's DynamoDB rows carry, deliberately narrowed. Note
 * what is absent: no household name, phone, or address anywhere. Hard rule 9,
 * as widened in Plan 2 — a household name reached CloudWatch once because
 * `read_case` returned `display_name` and a referee quoted it. The dashboard
 * renders `caseId` and the gate's typed reason, and nothing here gives it the
 * option to render more.
 *
 * `LedgerRow.detail` is restricted to JSON-safe scalars because that is exactly
 * what `LedgerEntry.detail` allows on the Python side; a nested value in a
 * dashboard type would imply the ledger can carry one.
 */

/** What a sweep concluded about one household — or, for `new`, that no sweep has
 *  concluded anything yet.
 *
 *  `new` is Plan 4's addition and it is not a cosmetic one. A case submitted
 *  through `/new` has a record row and no ledger, which under the previous three
 *  variants read as `error` — and `error`'s message says "Grace's last run on
 *  this case reached no outcome. Re-run the sweep", a false claim about a run
 *  that never happened. The same objection Plan 3 raised when requiring evidence
 *  for `acted` made `error` reachable and left it wearing a sentence written for
 *  a different variant: when a change widens the set of inputs a branch can see,
 *  re-read that branch's message as well as its logic. */
export type CaseStatus = "acted" | "escalated" | "error" | "new";

export interface CaseSummary {
  caseId: string;
  status: CaseStatus;
  program: string;
  deadline: string;
  reason: string | null;
  filed: boolean;
}

export interface LedgerRow {
  at: string;
  kind: string;
  detail: Record<string, string | number | boolean | null>;
}

export interface Decision {
  decidedAt: string;
  decidedBy: string;
  decision: "approve" | "deny";
  note: string;
  outcome: string | null;
}

/** One document a caseworker asserted the family sent to the **state**.
 *
 *  `sent`, not `received`. The row attribute and `grace/cases/models.py`'s field
 *  are both still `received` and stay that way — renaming a Plan 1 dataclass
 *  field would ripple into `grace/authority.py`, which the provenance work
 *  deliberately does not touch. The rename is vocabulary at the surface, and
 *  this is the boundary where it happens.
 *
 *  Why the word matters: "received" and "on file" both imply that Grace, or the
 *  navigator using it, holds the document. Neither does. The family sends
 *  documents to the state's eligibility system, which is the system of record;
 *  Grace's users are navigators — clinics, food banks, school family-support
 *  offices — whose real knowledge is *"I helped this family upload their paystub
 *  on the 20th"*. Status, not custody.
 *
 *  There is no field here for the document itself, and there is nowhere for one.
 *  A proof of income carries a name, an address, an employer, and often an SSN,
 *  which is the maximum-PII payload in a system whose architecture is "no
 *  household identity anywhere" — and the gate reads two dates and never opens a
 *  document. */
export interface RecordDocument {
  id: string;
  sent: string;
  expires: string | null;
}

/** The `RECORD#v1` row: what a caseworker asserted about a household, and who.
 *
 *  `createdBy` is the opaque Cognito `sub` and `""` when nobody asserted — the
 *  twelve seeded households come from `fixtures/households.yaml`, so no
 *  caseworker vouched for them. Never a name or an email: both writers refuse a
 *  subject that is not opaque rather than stripping it (hard rule 9, and the
 *  same discipline as a decision row). */
export interface CaseRecordFacts {
  createdBy: string;
  createdAt: string;
  documents: RecordDocument[];
}

export interface CaseDetail {
  summary: CaseSummary;
  ledger: LedgerRow[];
  decisions: Decision[];
  /** `null` when the table holds no record row for this case — which is a real
   *  state, not an error: a case can exist in the ledger without one. The page
   *  must then say nothing about document provenance rather than guess. */
  record: CaseRecordFacts | null;
  /** Whether the newest human decision is newer than the newest escalation.
   *  `false` means this household is still waiting on a person — see the note
   *  in `lib/cases.ts` on why a decision is scoped to an escalation episode. */
  decidedSinceEscalation: boolean;
}

/** Only the opaque `sub`, the role, and the expiry. Never an email or a name:
 *  inbound JWT claims are logged to CloudTrail, which is outside every
 *  redaction Grace has. */
export interface SessionIdentity {
  sub: string;
  role: string;
  expiresAt: number;
}
