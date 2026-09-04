import { listCases } from "@/lib/cases";
import { requireSession } from "@/lib/session";
import { CaseTable, statusLabel, summarise } from "@/components/case-table";
import type { CaseSummary } from "@/lib/types";

/**
 * `force-dynamic`, so this reads the live table on every request.
 *
 * Not optional. Task 4 measured the failure: a page Next prerenders runs at
 * **build** time, where `readEnv()` throws on an absent `GRACE_TABLE_NAME` and
 * `next build` fails outright. And with the variables present at build time the
 * quieter half applies — the caseload would be baked in as a snapshot from
 * whenever someone last deployed, so a sweep that ran an hour ago would be
 * invisible. A work queue is request-time content.
 */
export const dynamic = "force-dynamic";

/** One block per household, in case order, coloured by verdict.
 *
 *  The caseload is a fixed twelve (`CASE_IDS` in `lib/cases.ts` — there is no
 *  index over "every case" and the SSR role holds no `dynamodb:Scan`), so the
 *  whole sweep fits on one line at a glance. That is what makes this worth
 *  drawing rather than decorative: the claim under the heading is a count, and
 *  this is the same claim as a shape, with each block linking to the case it
 *  stands for. */
function SweepStrip({ cases }: { cases: readonly CaseSummary[] }) {
  return (
    <ul className="flex flex-wrap gap-1" aria-label="Every case in this sweep">
      {cases.map(c => (
        <li key={c.caseId}>
          <a
            href={`/case/${c.caseId}`}
            className={`block h-8 w-8 border ${blockTone(c)} focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink`}
            title={`${c.caseId} — ${statusLabel(c.status)}`}
          >
            <span className="sr-only">{`${c.caseId}: ${statusLabel(c.status)}`}</span>
          </a>
        </li>
      ))}
    </ul>
  );
}

/** Filled for escalated, outlined for handled, hatched-red for no outcome.
 *
 *  `acted && filed`, not `acted` alone: the fill must not claim a filing the
 *  ledger did not confirm, and `summarise` counts the same way. */
function blockTone(c: CaseSummary): string {
  if (c.status === "escalated") return "border-escalate bg-escalate";
  if (c.status === "acted" && c.filed) return "border-acted bg-acted/15";
  return "border-error bg-error/15";
}

export default async function Home() {
  // First, and before any table read: `proxy.ts` checks only that a cookie
  // exists, and a forged one was measured serving this entire page. See
  // `lib/session.ts`.
  await requireSession();
  const cases = await listCases();
  const sweep = summarise(cases);
  return (
    <section className="space-y-10">
      <header className="space-y-5">
        <p className="font-mono text-[0.6875rem] uppercase tracking-[0.12em] text-muted">
          Today&apos;s sweep · {sweep.total} of 12 households read
        </p>
        {/* The one large type size in the application, on the one claim the
            whole system makes. */}
        <h1 className="max-w-3xl text-3xl leading-tight font-semibold tracking-tight text-balance">
          <span className="text-acted">{sweep.acted} handled alone</span>
          <span className="text-muted">, </span>
          <span className="text-escalate">{sweep.escalated} waiting on you</span>
          {sweep.incomplete > 0 && (
            <>
              <span className="text-muted">, </span>
              <span className="text-error">{sweep.incomplete} with no outcome</span>
            </>
          )}
          <span className="text-muted">.</span>
        </h1>
        <p className="max-w-prose text-sm text-muted">
          A case counts as handled only when a <code className="font-mono text-xs">renewal_submitted</code>{" "}
          row proves it was filed. Anything Grace could not decide is below, with the
          reason its own check produced.
        </p>
        <SweepStrip cases={cases} />
      </header>
      <CaseTable cases={cases} />
    </section>
  );
}
