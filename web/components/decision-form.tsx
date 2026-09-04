"use client";

/**
 * The only client component in the application.
 *
 * It holds three pieces of state and posts once. Everything else is a server
 * component reading DynamoDB directly, which is why `lib/cases.ts` and its
 * `@aws-sdk` import graph never reach the browser.
 *
 * **No resume vocabulary, deliberately.** Approving does not resume a paused
 * graph — Plan 1 Task 6 measured that any truthy resume response *approves* the
 * blocked tool, so `"needs review"` filed a renewal for a household missing a
 * document. This posts a decision, the route records it, and Grace is
 * re-invoked so the authority gate evaluates the case facts again from scratch.
 * A still-missing document still refuses.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, Card, CardBody, CardHeader, CardTitle } from "@/components/ui/primitives";
import { MAX_NOTE_LENGTH } from "@/lib/authorize";

type Outcome =
  | { kind: "outcome"; message: string; filed: boolean }
  | { kind: "refused"; message: string };

export function DecisionForm({ caseId }: { caseId: string }) {
  const router = useRouter();
  const [note, setNote] = useState("");
  const [result, setResult] = useState<Outcome | null>(null);
  const [busy, setBusy] = useState(false);

  async function decide(decision: "approve" | "deny") {
    setBusy(true);
    setResult(null);
    try {
      const response = await fetch(`/api/case/${caseId}/decide`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, note }),
      });
      // The route answers JSON on every path, but a proxy, a redirect to the
      // hosted UI, or a framework-level error can answer HTML — and
      // `response.json()` then throws, which the draft did not handle. An
      // unhandled rejection here leaves the form spinning with no explanation
      // after a decision that may well have been recorded.
      let body: Record<string, unknown> = {};
      try {
        body = (await response.json()) as Record<string, unknown>;
      } catch {
        body = {};
      }

      if (response.ok) {
        // Report exactly what Grace said, including a refusal to file.
        // "Approved" on a case Grace then refused would be the dashboard telling
        // a comfortable lie — hard rule 6 at the last surface it can be broken
        // on, the one a human actually reads.
        setResult({
          kind: "outcome",
          message: text(body.graceOutcome)
            ?? "The decision was recorded. Grace returned no description of what it did.",
          // `=== true`, not truthiness: this crossed a JSON boundary, so the
          // string "false" would otherwise read as a filing.
          filed: body.filed === true,
        });
        // The decision is durable now, so the page's own data is stale — it
        // still shows the form and no decision history. Re-read it from the
        // server rather than leaving the caseworker looking at a page that
        // disagrees with the table.
        router.refresh();
      } else {
        const code = text(body.error) ?? String(response.status);
        const message = text(body.message) ?? "The decision was not recorded.";
        setResult({ kind: "refused", message: `${code} — ${message}` });
      }
    } catch (error) {
      // A network failure. The decision may or may not have been recorded, and
      // saying so is better than implying either.
      setResult({
        kind: "refused",
        message: `The request did not complete, so it is unclear whether the decision was recorded. Reload the case before deciding again. (${
          error instanceof Error ? error.message : String(error)})`,
      });
    } finally {
      setBusy(false);
    }
  }

  const over = note.length > MAX_NOTE_LENGTH;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Your decision</CardTitle>
        <p className="mt-1 max-w-prose text-sm text-muted">
          Approving asks Grace to check this case again. It files only if its own
          gate clears the household — if a required document is still missing,
          nothing is filed and the case stays here.
        </p>
      </CardHeader>
      <CardBody className="space-y-4">
        <div className="space-y-1.5">
          <label htmlFor="decision-note" className="block font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-muted">
            What did you check?
          </label>
          <textarea
            id="decision-note"
            className="w-full rounded-none border border-rule bg-paper px-3 py-2 text-sm outline-none focus-visible:border-ink focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-2 focus-visible:ring-offset-paper"
            rows={3}
            // `maxLength` is a convenience, not the bound. `authorize` re-checks
            // the length and the type server-side, because an attribute on an
            // input element constrains only a cooperating browser.
            maxLength={MAX_NOTE_LENGTH}
            value={note}
            onChange={e => setNote(e.target.value)}
            aria-describedby="decision-note-count"
          />
          <p id="decision-note-count" className={`font-mono text-[0.6875rem] ${over ? "text-error" : "text-muted"}`}>
            {note.length} / {MAX_NOTE_LENGTH}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" disabled={busy} onClick={() => decide("approve")}>
            {busy ? "Working…" : "Approve and re-check"}
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => decide("deny")}>
            Keep escalated
          </Button>
        </div>
        {result && (
          <p
            role="status"
            className={`max-w-prose border-l-2 pl-3 text-sm ${
              result.kind === "refused"
                ? "border-error text-error"
                : result.filed
                  ? "border-acted text-acted"
                  : "border-escalate text-escalate"
            }`}
          >
            {result.message}
          </p>
        )}
      </CardBody>
    </Card>
  );
}

/** A non-empty string, or nothing. The route's own body is typed, but this
 *  crosses a JSON boundary — `String(undefined)` is `"undefined"`, which reads
 *  to a caseworker as a bug in the dashboard rather than as a missing field. */
function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}
