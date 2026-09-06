"use client";

/**
 * ADDING A HOUSEHOLD, WITHOUT LEARNING WHO THEY ARE.
 *
 * **There is no name field, no phone field, and no address field — and their
 * absence is the design.** An intake form is exactly where identity feels
 * natural to collect, and `read_case` returning `display_name` is precisely how
 * a household surname reached CloudWatch in Plan 2: a tool returned it, a
 * referee quoted it into its reasoning, that prose became an escalation reason,
 * and the reason was logged as a Step Functions payload — a path span redaction
 * does not cover.
 *
 * Grace does not need to know who the family is in order to know whether their
 * paperwork is complete. A real deployment's identity stays in the system of
 * record that referred the household, keyed by case id. So this form cannot
 * collect identity, and `lib/intake.ts` refuses any field that looks like it —
 * capability absence rather than discipline, the same reasoning as layer 1 of
 * the escalation boundary.
 *
 * The second client component in the application, for the same reason as the
 * first: it holds form state and posts once. Every read stays a server
 * component, which is why `lib/cases.ts` and its `@aws-sdk` import graph never
 * reach the browser.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, Card, CardBody, CardHeader, CardTitle } from "@/components/ui/primitives";
import { DOCUMENT_IDS, LANGUAGES, PROGRAMS, STATES } from "@/lib/intake";

/** Cents, entered as whole currency units. Kept out of the component so the
 *  conversion is one expression rather than scattered through handlers.
 *
 *  Returns `null` rather than `0` for anything unparseable. `0` is a real income
 *  a family can report — a genuine loss of all income is the single most
 *  eligibility-relevant case Grace will see — so it must never double as an
 *  absence marker. Plan 1 Task 2 established that for `reported_income_cents`;
 *  the same reasoning applies at this boundary. */
function toCents(value: string): number | null {
  if (value.trim() === "") return null;
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) return null;
  return Math.round(n * 100);
}

export function IntakeForm() {
  const router = useRouter();
  const [caseId, setCaseId] = useState("");
  const [program, setProgram] = useState<string>(PROGRAMS[0]);
  const [state, setState] = useState<string>(STATES[0]);
  const [certEnd, setCertEnd] = useState("");
  const [language, setLanguage] = useState<string>(LANGUAGES[0]);
  const [income, setIncome] = useState("");
  const [size, setSize] = useState("");
  const [documents, setDocuments] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggleDocument(id: string) {
    setDocuments(current =>
      current.includes(id) ? current.filter(d => d !== id) : [...current, id]);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/case/new", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          case_id: caseId.trim(),
          program,
          state,
          cert_end: certEnd,
          language,
          monthly_income_cents: toCents(income),
          size: size.trim() === "" ? null : Number(size),
          // Received today, which is the honest default for a document a
          // caseworker is confirming they hold right now. `expires: null` means
          // "does not expire" rather than "unknown" — the gate reads the two
          // differently.
          documents: documents.map(id => ({
            id,
            received: new Date().toISOString().slice(0, 10),
            expires: null,
          })),
        }),
      });
      // `response.json()` throws on an HTML error page, which would leave the
      // form spinning after a write that may well have succeeded. Read it
      // defensively and say something either way.
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setError(
          (body as { message?: string } | null)?.message
            ?? `The case was not created (HTTP ${response.status}).`);
        return;
      }
      router.push(`/case/${caseId.trim()}`);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The case was not created.");
    } finally {
      setBusy(false);
    }
  }

  const label = "block font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-muted";
  const field =
    "mt-2 w-full rounded-md border border-rule bg-paper px-3 py-2 text-sm text-ink " +
    "focus:border-escalate focus:outline-2 focus:outline-escalate/20";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Add a household</CardTitle>
      </CardHeader>
      <CardBody>
        <form onSubmit={submit} className="space-y-6">
          <div className="grid gap-5 sm:grid-cols-2">
            <div>
              <label className={label} htmlFor="case_id">Case id</label>
              <input
                id="case_id" name="case_id" value={caseId} required
                onChange={e => setCaseId(e.target.value)}
                placeholder="c-013" className={field}
              />
              <p className="mt-1 text-xs text-muted">
                The letter c, a dash, three digits. This is the only identifier Grace holds.
              </p>
            </div>
            <div>
              <label className={label} htmlFor="cert_end">Certification ends</label>
              <input
                id="cert_end" name="cert_end" type="date" value={certEnd} required
                onChange={e => setCertEnd(e.target.value)} className={field}
              />
            </div>
            <div>
              <label className={label} htmlFor="program">Program</label>
              <select id="program" name="program" value={program}
                      onChange={e => setProgram(e.target.value)} className={field}>
                {PROGRAMS.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label className={label} htmlFor="state">State</label>
              <select id="state" name="state" value={state}
                      onChange={e => setState(e.target.value)} className={field}>
                {STATES.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label className={label} htmlFor="income">Monthly income</label>
              <input
                id="income" name="income" inputMode="decimal" value={income} required
                onChange={e => setIncome(e.target.value)}
                placeholder="2400" className={field}
              />
              <p className="mt-1 text-xs text-muted">
                Whole currency units. Zero is a real answer, not a blank.
              </p>
            </div>
            <div>
              <label className={label} htmlFor="size">Household size</label>
              <input
                id="size" name="size" inputMode="numeric" value={size} required
                onChange={e => setSize(e.target.value)}
                placeholder="3" className={field}
              />
            </div>
            <div>
              <label className={label} htmlFor="language">Outreach language</label>
              <select id="language" name="language" value={language}
                      onChange={e => setLanguage(e.target.value)} className={field}>
                {LANGUAGES.map(l => <option key={l} value={l}>{l}</option>)}
              </select>
              <p className="mt-1 text-xs text-muted">
                What a reminder text would be written in.
              </p>
            </div>
          </div>

          <fieldset>
            <legend className={label}>Documents on file</legend>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              {DOCUMENT_IDS.map(id => (
                <label key={id} className="flex items-center gap-2 text-sm text-ink">
                  <input
                    type="checkbox" name="documents" value={id}
                    checked={documents.includes(id)}
                    onChange={() => toggleDocument(id)}
                    className="size-4 accent-[var(--color-ink)]"
                  />
                  <span className="font-mono text-xs">{id}</span>
                </label>
              ))}
            </div>
            <p className="mt-2 text-xs text-muted">
              <span className="font-medium text-ink">Nothing is uploaded here, on purpose.</span>{" "}
              Grace records <em>that</em> a document is on file and when it arrived — never the
              document itself. A proof of income carries a name, an address and an employer, so
              storing one would put back exactly the identity this form refuses to collect. The file
              stays with whoever collected it; Grace tracks the clock on it.
            </p>
            <p className="mt-2 text-xs text-muted">
              Leave one unticked and Grace will escalate rather than file — which is the behaviour
              worth demonstrating.
            </p>
          </fieldset>

          {error !== null && (
            <p role="alert" className="rounded-md border border-error px-3 py-2 text-sm text-error">
              {error}
            </p>
          )}

          <div className="flex items-center gap-4">
            <Button type="submit" variant="primary" disabled={busy}>
              {busy ? "Adding…" : "Add household"}
            </Button>
            <p className="text-xs text-muted">
              No sweep has run on a new case, so its audit trail stays empty until Grace&rsquo;s next run.
            </p>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}
