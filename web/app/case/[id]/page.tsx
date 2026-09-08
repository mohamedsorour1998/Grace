import { notFound } from "next/navigation";
import { readCase } from "@/lib/cases";
import { requireSession } from "@/lib/session";
import {
  DOCUMENT_HEADING,
  documentProvenance,
  formatCaseRow,
  noteIsInert,
  sentLabel,
  statusLabel,
  statusTone,
} from "@/components/case-table";
import { Badge, Card, CardBody, CardHeader, CardTitle } from "@/components/ui/primitives";
import { DecisionForm } from "@/components/decision-form";
import type { LedgerRow } from "@/lib/types";

/**
 * `force-dynamic`. A prerender would run `readEnv()` at build time and fail the
 * build; see `app/page.tsx`. It would also be wrong even when it worked — this
 * page's audit trail is the record, and a snapshot of a record is not one.
 */
export const dynamic = "force-dynamic";

/** Ledger detail, minus the two keys that say nothing to a caseworker.
 *
 *  `trace_id` is `NULL` on 613 of 625 live rows and absent entirely on 12 more:
 *  AgentCore Runtime injects the OTEL environment variables without installing
 *  an in-process tracer provider, so nothing produced a trace. Rendering an
 *  empty column for it would read as a fault rather than as an honest "not
 *  traced". `tool` is promoted out of the detail into its own column, so it is
 *  dropped here to avoid printing it twice. */
function detailPairs(row: LedgerRow): [string, string][] {
  return Object.entries(row.detail)
    .filter(([key, value]) => key !== "trace_id" && key !== "tool" && value !== null)
    .map(([key, value]) => [key, String(value)]);
}

/** The clock time, without the date every row on this page shares. */
function clock(at: string): string {
  const t = at.indexOf("T");
  if (t < 0) return at;
  return at.slice(t + 1, t + 9) || at;
}

export default async function Case({ params }: { params: Promise<{ id: string }> }) {
  // Before the table read, and before `params` is even unwrapped: an
  // unauthenticated request must not learn whether a case id exists. See
  // `lib/session.ts` — a forged cookie was measured reaching one household's
  // full audit trail.
  await requireSession();
  const { id } = await params;
  const detail = await readCase(id);
  if (detail === null) notFound();

  const { summary, ledger, decisions, record, decidedSinceEscalation } = detail;
  const row = formatCaseRow(summary);
  // The same conditions `authorize` will re-check server-side when the form
  // posts, so the form is offered only where a decision is actually permitted.
  // Showing it on a case `authorize` refuses would let a caseworker write a
  // note and be told no — and `not_escalated` and `case_incomplete` are both
  // refusals, so `status === "escalated"` is not one condition but two.
  //
  // `decidedSinceEscalation`, never a raw count of `decisions` — a decision
  // answers the escalation it was made against, not the case forever (see the
  // note in `lib/cases.ts` beside where this field is computed). Tomorrow's
  // sweep re-escalates a household whose problem persists and writes a fresh
  // PENDING row; `/queue` lists it again and the write route accepts a new
  // decision, so the page must offer the form again too, or a caseworker who
  // clicks through from the queue reaches a case with nothing to do.
  const decidable = summary.status === "escalated" && !decidedSinceEscalation;

  return (
    <section className="space-y-10">
      <header className="space-y-4">
        <div className="flex flex-wrap items-baseline gap-3">
          <h1 className="font-mono text-2xl font-semibold tracking-tight">{summary.caseId}</h1>
          <Badge
            tone={summary.status === "escalated" ? "escalate" : summary.status === "error" ? "error" : "acted"}
          >
            {statusLabel(summary.status)}
          </Badge>
        </div>
        <p className={`max-w-prose ${statusTone(summary.status)}`}>
          {row.code !== null && <code className="mr-2 font-mono text-xs">{row.code}</code>}
          {row.detail}
        </p>
        <dl className="flex flex-wrap gap-x-8 gap-y-1 font-mono text-xs text-muted">
          <div className="flex gap-2">
            <dt>program</dt>
            {/* `lib/cases.ts` returns "" when the table holds no program for this
                case, which is every escalated case — `d_program` exists only on
                a `renewal_submitted` row. The dash belongs here, not there. */}
            <dd className="text-ink">{summary.program || "—"}</dd>
          </div>
          <div className="flex gap-2">
            <dt>certification ends</dt>
            <dd className="text-ink tabular-nums">{summary.deadline || "—"}</dd>
          </div>
          <div className="flex gap-2">
            <dt>renewal filed</dt>
            <dd className={summary.filed ? "text-acted" : "text-ink"}>
              {summary.filed ? "yes" : "no"}
            </dd>
          </div>
        </dl>
      </header>

      {decidable && <DecisionForm caseId={summary.caseId} />}

      {/* Above the audit trail and above the decision form's outcome, because a
          caseworker deciding an escalation is acting on exactly this claim.
          Rendered only when the table holds a record row: with none, Grace knows
          nothing about this household's documents, and a card saying so would
          imply the family has sent nothing. */}
      {record !== null && (
        <Card>
          <CardHeader>
            <CardTitle>{DOCUMENT_HEADING}</CardTitle>
            <p className="mt-1 max-w-prose text-sm text-muted">
              {/* THE sentence. Grace has never seen any of these documents —
                  the family sends them to the state, which is the system of
                  record. What is held here is a status a caseworker asserted,
                  and hard rule 6 is about never letting an assertion read as a
                  confirmed fact. The id is the opaque Cognito `sub`; both
                  writers refuse a subject that is not opaque, so a name cannot
                  reach this line. */}
              {documentProvenance(record)}
            </p>
          </CardHeader>
          <CardBody>
            {record.documents.length === 0 ? (
              <p className="text-sm text-muted">
                No document has been recorded as sent for this household.
              </p>
            ) : (
              <ul className="space-y-1 font-mono text-xs">
                {record.documents.map((d, i) => (
                  <li key={`${d.id}-${i}`} className="flex flex-wrap gap-x-3">
                    <span className="w-48 shrink-0 text-ink">{d.id}</span>
                    <span className="tabular-nums text-muted">{sentLabel(d)}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      )}

      {decisions.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Caseworker decisions</CardTitle>
          </CardHeader>
          <CardBody>
            <ul className="space-y-5 text-sm">
              {decisions.map(d => (
                <li key={d.decidedAt} className="border-l-2 border-rule pl-4">
                  <p className="flex flex-wrap items-baseline gap-2">
                    <Badge tone={d.decision === "approve" ? "acted" : "neutral"}>
                      {d.decision}
                    </Badge>
                    <span className="font-mono text-xs text-muted">
                      {d.decidedAt} · {d.decidedBy}
                    </span>
                  </p>
                  {/* Rendered directly. React escapes text children, and escaping
                      again shows `&#39;` to the caseworker — measured. Nothing
                      rewrites a caseworker's words; `noteIsInert` is a check for
                      whether markup is present, not a transform. It gates the
                      one thing that would matter: a note carrying `<` or `>` is
                      shown as monospaced source rather than as prose, so the
                      caseworker can see it for what it is. */}
                  {d.note && (
                    <p className={`mt-2 max-w-prose ${noteIsInert(d.note) ? "" : "font-mono text-xs"}`}>
                      {d.note}
                    </p>
                  )}
                  {d.outcome && (
                    <p className="mt-2 max-w-prose text-muted">{d.outcome}</p>
                  )}
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Audit trail</CardTitle>
          <p className="mt-1 max-w-prose text-sm text-muted">
            Every tool Grace called on this case, in the order the ledger recorded
            them. This is the record, not a summary of it — {ledger.length} rows.
          </p>
        </CardHeader>
        <CardBody>
          {ledger.length === 0 ? (
            <p className="text-sm text-muted">No ledger rows for this case.</p>
          ) : (
            /* Numbered, because this genuinely is a sequence — the sort key
               carries a sequence number and the order is the claim the trajectory
               evals assert (reads precede actions). */
            <ol className="space-y-1 font-mono text-xs">
              {ledger.map((entry, i) => (
                <li key={`${entry.at}-${i}`} className="flex flex-wrap gap-x-3 gap-y-0.5">
                  <span className="w-8 shrink-0 text-right tabular-nums text-muted">{i + 1}</span>
                  <span className="w-[4.5rem] shrink-0 tabular-nums text-muted">{clock(entry.at)}</span>
                  <span className="w-40 shrink-0 text-ink">{entry.kind}</span>
                  {typeof entry.detail.tool === "string" && (
                    <span className="w-40 shrink-0 text-ink">{entry.detail.tool}</span>
                  )}
                  <span className="min-w-0 break-words text-muted">
                    {detailPairs(entry).map(([k, v]) => `${k}=${v}`).join("  ")}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </CardBody>
      </Card>
    </section>
  );
}
