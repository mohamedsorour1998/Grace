import { requireSession } from "@/lib/session";
import { IntakeForm } from "@/components/intake-form";

/**
 * `force-dynamic`, like every other page here. This one reads no household data,
 * but it does call `requireSession`, and a prerender would run that at build
 * time — where there is no request and therefore no cookie. Task 4 of Plan 3
 * measured the neighbouring failure: `/login` was prerendered, `hostedUiUrl`
 * threw on a missing environment variable, and `next build` exited 1.
 */
export const dynamic = "force-dynamic";

export default async function NewCase() {
  // Before anything renders. `lib/session.ts` records what a forged cookie
  // reached when the pages had no session check at all.
  await requireSession();
  return (
    <section className="space-y-8">
      <header className="space-y-4">
        <p className="font-mono text-[0.6875rem] uppercase tracking-[0.12em] text-muted">
          Intake
        </p>
        <h1 className="text-2xl leading-tight font-semibold tracking-tight">
          Add a household to Grace&rsquo;s caseload.
        </h1>
        <p className="max-w-prose text-sm text-muted">
          Grace picks it up on its next sweep and either files the renewal or escalates it to you
          with a reason. Nothing is filed from this page.
        </p>
      </header>

      {/* Four points, not four paragraphs. Everything here is a constraint a
          caseworker needs before they type — what Grace will not hold, and what
          a ticked box actually means — and a wall of prose above a form is a
          wall of prose nobody reads. The reasoning behind each line lives in
          `docs/superpowers/plans/2026-09-07-grace-document-provenance.md`; the
          page states the rule. */}
      <ul className="max-w-prose space-y-2.5 rounded-md border border-rule bg-paper px-4 py-3.5 text-sm text-muted">
        <li>
          <span className="font-medium text-ink">No household identity.</span> No name, phone or
          address. Grace does not need to know who the family is to know whether their paperwork is
          complete.
        </li>
        <li>
          <span className="font-medium text-ink">No files, anywhere.</span> A document here is an id
          and a date. Grace tracks whether a proof was sent and is still current, never what it
          contains.
        </li>
        <li>
          <span className="font-medium text-ink">The family sends documents to the state.</span>{" "}
          The state&rsquo;s system decides and holds the paperwork. Grace works alongside a
          navigator — a clinic, a food bank, a school — so it tracks status, not custody.
        </li>
        <li>
          <span className="font-medium text-ink">Ticking a box is your assertion.</span> Grace
          cannot verify it, so it records your opaque id and the date and shows both on the case
          page. <code className="font-mono text-xs">grace/cases/document_source.py</code> is the
          seam where a state integration would replace it.
        </li>
        <li>
          <span className="font-medium text-ink">Synthetic data only.</span> Nothing here is a real
          household.
        </li>
      </ul>

      <IntakeForm />
    </section>
  );
}
