/**
 * The caseload as a table, and the pure helpers the pages and the tests share.
 *
 * The helpers live here rather than in `lib/` because they are presentation:
 * `lib/cases.ts` deliberately returns `""` for a deadline it could not measure
 * and the gate's reason string verbatim, leaving the dash and the split to
 * whichever surface renders them. Same division of labour as `authority.py`
 * leaving escaping to the renderer.
 *
 * **Nothing here formats a household name, because nothing here can.**
 * `CaseSummary` carries none — hard rule 9 at the shape level. The row is the
 * one place a name would look natural to add, and the path that put one into
 * CloudWatch was exactly this shape of convenience: `read_case` returned
 * `display_name`, a referee quoted it, and the quote became an escalation
 * reason. `reason` is still the carrier a leak would arrive through, which is
 * why `__tests__/render.test.ts` feeds a name in that way and asserts it is
 * caught.
 */

import Link from "next/link";
import { Badge, Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/primitives";
import type { CaseStatus, CaseSummary } from "@/lib/types";

/** The eight codes `grace/authority.py` actually emits, and nothing else.
 *
 *  A closed set rather than "the text before the first colon". Measured on the
 *  live table, `c-012`'s newest escalation reason reads
 *  `"A caseworker must decide. source_conflict: household size 5 on
 *  application, 3 on most recent wage record Deliberation — CLEAR: …"` — Grace's
 *  own prose, the typed code, and the referee's conclusion in one string. A
 *  first-colon split renders `A caseworker must decide. source_conflict` in a
 *  monospaced chip that implies it came from the gate, which is a label out of
 *  no closed set at all. */
const REASON_CODES: readonly string[] = [
  "missing_document",
  "stale_document",
  "material_income_change",
  "household_size_change",
  "source_conflict",
  "window_not_open",
  "window_closed",
  "verification_error",
];

/** The columns, exported so the test asserts the shape rather than a heading
 *  string. There is no name column and nowhere for one. */
export const CASE_COLUMNS: readonly string[] = ["Case", "What Grace concluded", "Deadline"];

export interface CaseRow {
  title: string;
  /** The gate's typed reason code, or `null` when the reason is prose. */
  code: string | null;
  detail: string;
  deadline: string;
  status: CaseStatus;
}

/** Pull the gate's typed code off the front of a reason string.
 *
 *  `grace/run.py`'s `gate_reason` builds `f"{r.code}: {r.detail}"` and joins
 *  several with `"; "`, so the code is a value from `REASON_CODES` and the rest
 *  is prose a model may have contributed to. Split only on a recognised code:
 *  anything else stays whole, because a chip is a claim that the gate said this.
 *
 *  The remainder keeps every reason, not just the first — a case can fail
 *  several conditions at once and reason order is not a contract (Plan 1
 *  Task 3), so dropping the tail would drop a fact the caseworker needs. */
export function splitReason(reason: string): { code: string | null; detail: string } {
  const colon = reason.indexOf(": ");
  if (colon > 0) {
    const head = reason.slice(0, colon);
    if (REASON_CODES.includes(head)) {
      return { code: head, detail: reason.slice(colon + 2) };
    }
  }
  return { code: null, detail: reason };
}

/** What one row says.
 *
 *  `reason` first, and for an escalated case that is the only source — `filed`
 *  arrives from `listQueue` as `false` **by construction** rather than by
 *  measurement (the GSI projects escalation rows only, so that query cannot see
 *  a `renewal_submitted` row), so reading it on an escalated row would render a
 *  claim about a fact nobody measured.
 *
 *  With no reason, the three statuses say three different things, and the
 *  difference is whether the sentence is true. `acted` has a `renewal_submitted`
 *  row behind it. `error` means the sweep reached no outcome at all: nothing
 *  filed and nothing escalated, so "Grace handled this case" would be the
 *  unconfirmed-success claim hard rule 6 forbids, told to the one person who
 *  could still save the family. It says re-run instead. */
export function formatCaseRow(c: CaseSummary): CaseRow {
  const { code, detail } = c.reason === null
    ? { code: null, detail: fallbackDetail(c) }
    : splitReason(c.reason);
  return { title: c.caseId, code, detail, deadline: c.deadline, status: c.status };
}

function fallbackDetail(c: CaseSummary): string {
  if (c.status === "error") {
    // Deliberately says "no renewal was submitted" rather than "nothing was
    // filed". Both are true, but the second puts the word "filed" on a case
    // where nothing was — and the guard that protects hard rule 6 here is
    // "the word `filed` appears only where a filing is confirmed", which is a
    // far easier invariant to defend than a regex that has to tell a claim from
    // its negation. `renewal_submitted` is also the ledger row's actual name.
    return "This run reached no outcome — no renewal was submitted and nothing escalated. Re-run the sweep.";
  }
  if (c.status === "escalated") {
    // An escalated case with no reason. `filed` is deliberately not consulted:
    // from `listQueue` it is `false` by construction (the GSI projects
    // escalation rows only, so that query cannot see a `renewal_submitted` row),
    // so trusting it here would let the queue page make a claim about a fact
    // nobody measured — in either direction. Caught by the "only where a filing
    // is confirmed" test, which found this branch returning "Renewal filed." for
    // an escalated case.
    return "Grace escalated this case without recording a reason. Read the audit trail below.";
  }
  if (c.filed) return "Handled alone. Renewal filed.";
  return "Handled without a renewal on the ledger.";
}

/** Escalation gets the one saturated colour in the palette. A caseworker scans
 *  this list for work, and the work is the escalations.
 *
 *  Every arm named, no `default:` — adding a fourth status is then a compile
 *  error rather than a silent inheritance of `acted`'s green. */
export function statusTone(status: CaseStatus): string {
  switch (status) {
    case "escalated":
      return "text-escalate";
    case "error":
      return "text-error";
    case "acted":
      return "text-acted";
  }
}

/** The word a caseworker reads, in their vocabulary rather than the ledger's. */
export function statusLabel(status: CaseStatus): string {
  switch (status) {
    case "escalated":
      return "Needs a human";
    case "error":
      return "No outcome";
    case "acted":
      return "Handled";
  }
}

function statusBadgeTone(status: CaseStatus): "escalate" | "acted" | "error" {
  switch (status) {
    case "escalated":
      return "escalate";
    case "error":
      return "error";
    case "acted":
      return "acted";
  }
}

export interface SweepSummary {
  total: number;
  acted: number;
  escalated: number;
  incomplete: number;
}

/** The headline claim on `/`, counted rather than subtracted.
 *
 *  `acted = total - escalated` is the arithmetic that folds an `error` case into
 *  "handled alone" — nine handled and three escalated, with a family whose
 *  renewal was never filed sitting inside the nine. So `acted` requires the
 *  filing that proves it, exactly as `lib/cases.ts` requires it before reporting
 *  the status, and every case lands in exactly one bucket (Plan 1 Task 6: a case
 *  counted twice or counted nowhere makes a total that still looks plausible). */
export function summarise(cases: readonly CaseSummary[]): SweepSummary {
  let acted = 0;
  let escalated = 0;
  let incomplete = 0;
  for (const c of cases) {
    if (c.status === "escalated") escalated += 1;
    else if (c.status === "acted" && c.filed) acted += 1;
    else incomplete += 1;
  }
  return { total: cases.length, acted, escalated, incomplete };
}

/** A caseworker's note is untrusted free text, and this asserts what protects it
 *  rather than adding a second layer. React escapes text children itself.
 *  Measured with `renderToStaticMarkup`:
 *
 *    input   The family's wage record is stale.
 *    JSX     <p>The family&#x27;s wage record is stale.</p>      correct
 *    esc→JSX <p>The family&amp;#39;s wage record is stale.</p>   shows "&#39;" on screen
 *
 *    input   <img src=x onerror="alert(1)">
 *    JSX     <p>&lt;img src=x onerror=&quot;alert(1)&quot;&gt;</p>   no live tag
 *
 *  So escaping before handing a string to JSX is a bug, not defence in depth.
 *
 *  This function exists to be *checked*, not applied. It answers "would this
 *  note be safe if someone reached for `dangerouslySetInnerHTML`?", which is the
 *  only way markup could reach the page. Callers render `note` directly.
 *
 *  Only `<` and `>` — apostrophes and quotes cannot open a tag, and flagging
 *  them would reject ordinary prose. Verified against the same four fixtures:
 *  `The family's …` → true, `<img …>` → false, `5 > 3 && 2 < 4` → false,
 *  `"quoted" & ampersand` → true. **A no-op test's fixture must contain every
 *  character the function is meant to react to** — the draft's version passed
 *  against the double-escaping bug because its fixture had no apostrophe. */
export function noteIsInert(note: string): boolean {
  return !/[<>]/.test(note);
}

/** The reason line: the typed code as a chip, the measurement as prose.
 *
 *  Two typefaces doing two jobs — mono for anything Grace or the table wrote,
 *  the body face for the sentence a human reads. The chip is not decoration: it
 *  is a value from a closed set of eight, and that is what makes it worth
 *  setting apart from the prose beside it. */
function ReasonLine({ row }: { row: CaseRow }) {
  return (
    <span className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:gap-2">
      {row.code !== null && (
        <code className={`shrink-0 font-mono text-[0.6875rem] tracking-tight ${statusTone(row.status)}`}>
          {row.code}
        </code>
      )}
      <span className="text-ink">{row.detail}</span>
    </span>
  );
}

export function CaseTable({ cases }: { cases: readonly CaseSummary[] }) {
  if (cases.length === 0) {
    return (
      <p className="border border-dashed border-rule px-4 py-8 text-center text-sm text-muted">
        No cases to show. If you expected the caseload here, the sweep has not run
        against this table yet.
      </p>
    );
  }
  const rows = cases.map(formatCaseRow);
  return (
    <Table>
      <caption className="sr-only">
        Grace&apos;s caseload: one row per household, with what Grace concluded and the
        certification deadline.
      </caption>
      <TableHead>
        <TableRow className="border-rule">
          {CASE_COLUMNS.map((column, i) => (
            <TableHeaderCell key={column} scope="col" className={i === 2 ? "text-right" : undefined}>
              {column}
            </TableHeaderCell>
          ))}
        </TableRow>
      </TableHead>
      <TableBody>
        {rows.map(row => (
          <TableRow key={row.title}>
            <TableCell className="whitespace-nowrap">
              <Link
                href={`/case/${row.title}`}
                className="font-mono text-sm underline decoration-rule decoration-2 underline-offset-4 hover:decoration-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
              >
                {row.title}
              </Link>
              <Badge tone={statusBadgeTone(row.status)} className="ml-2 align-middle">
                {statusLabel(row.status)}
              </Badge>
            </TableCell>
            <TableCell className="max-w-prose">
              <ReasonLine row={row} />
            </TableCell>
            <TableCell className="whitespace-nowrap text-right font-mono text-xs tabular-nums text-muted">
              {/* `lib/cases.ts` returns "" for a deadline it could not measure,
                  on purpose — the dash belongs to the renderer. */}
              {row.deadline || "—"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
