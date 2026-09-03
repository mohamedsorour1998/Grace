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

export type CaseStatus = "acted" | "escalated" | "error";

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

export interface CaseDetail {
  summary: CaseSummary;
  ledger: LedgerRow[];
  decisions: Decision[];
}

/** Only the opaque `sub`, the role, and the expiry. Never an email or a name:
 *  inbound JWT claims are logged to CloudTrail, which is outside every
 *  redaction Grace has. */
export interface SessionIdentity {
  sub: string;
  role: string;
  expiresAt: number;
}
