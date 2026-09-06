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
          Grace will pick it up on its next sweep, read the rules for its program, and either file
          the renewal or escalate it to you with a reason. Nothing is filed from this page.
        </p>
      </header>

      <div className="max-w-prose space-y-3 rounded-md border border-rule bg-paper px-4 py-3 text-sm text-muted">
        <p>
          <span className="font-medium text-ink">This form collects no household identity.</span>{" "}
          No name, no phone number, no address. Grace does not need to know who the family is to
          know whether their paperwork is complete, and every field it holds is one a model or a log
          could eventually repeat — so it holds none that could identify anyone.
        </p>
        <p>
          <span className="font-medium text-ink">No documents are uploaded either.</span> Grace
          records that a proof is on file and when it arrived — never the file. It checks whether
          paperwork is complete and current, not whether it is authentic; that judgement stays with
          the people who collected it.
        </p>
        <p>
          All data in this deployment is <span className="font-medium text-ink">synthetic</span>.
        </p>
      </div>

      <IntakeForm />
    </section>
  );
}
