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
          <span className="font-medium text-ink">No files are stored anywhere.</span> A document
          here is an id and a date — Grace tracks whether a required proof has been sent and is
          still current, never what it contains. Storing the file would also put back the identity
          above: a proof of income carries a name, an address and an employer.
        </p>
        <p>
          <span className="font-medium text-ink">The family sends documents to the state, not to
          Grace.</span> The state&rsquo;s eligibility system is the system of record: it decides,
          and it holds the paperwork. Grace is the layer alongside a navigator — a clinic, a food
          bank, a school family-support office — and what a navigator actually knows is
          <em> &ldquo;I helped this family send their paystub on the 20th&rdquo;</em>. That is
          status, not custody, and it is exactly what this form records.
        </p>
        <p>
          So ticking a box records <span className="font-medium text-ink">your assertion</span>,
          which Grace cannot verify. It stores your account&rsquo;s opaque id and the date beside
          it and shows both on the case page, so whoever decides an escalation can tell an assertion
          from a confirmed fact. In a real deployment that claim would arrive from the state system
          that received the document; <code className="font-mono text-xs">
          grace/cases/document_source.py</code> is the seam where that integration attaches, and it
          raises rather than pretending to answer.
        </p>
        <p>
          All data in this deployment is <span className="font-medium text-ink">synthetic</span>.
        </p>
      </div>

      <IntakeForm />
    </section>
  );
}
