import { listQueue } from "@/lib/cases";
import { requireSession } from "@/lib/session";
import { CaseTable } from "@/components/case-table";

/**
 * `force-dynamic` for the same reason as `/`: this page's whole content is a
 * live read, and a prerender would serve a snapshot from deploy time. See
 * `app/page.tsx` for the measured failure.
 */
export const dynamic = "force-dynamic";

export default async function Queue() {
  // Before the GSI read. See `lib/session.ts` for what a forged cookie reached.
  await requireSession();
  const queue = await listQueue();
  const waiting = queue.length;
  return (
    <section className="space-y-8">
      <header className="space-y-4">
        <p className="font-mono text-[0.6875rem] uppercase tracking-[0.12em] text-muted">
          The escalation queue
        </p>
        <h1 className="text-2xl leading-tight font-semibold tracking-tight">
          {waiting === 0
            ? "Nothing is waiting on a caseworker."
            : `${waiting} ${waiting === 1 ? "household needs" : "households need"} a decision.`}
        </h1>
        <p className="max-w-prose text-sm text-muted">
          {waiting === 0
            ? "Grace reached an outcome on every case in the last sweep. Cases appear here only when its own check could not settle eligibility."
            : "Soonest certification deadline first. Grace refused to decide each of these itself, and the reason below is the check that stopped it."}
        </p>
      </header>
      <CaseTable cases={queue} />
    </section>
  );
}
