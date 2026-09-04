import { describe, expect, it, vi } from "vitest";
import {
  CASE_COLUMNS,
  formatCaseRow,
  noteIsInert,
  splitReason,
  statusLabel,
  statusTone,
  summarise,
} from "@/components/case-table";
import type { CaseStatus, CaseSummary } from "@/lib/types";

// Every fixture surname, so the guard cannot pass by naming the wrong three.
// The plan's draft listed Mensah/Rivera/Okonkwo — and the two households most
// likely to carry a name in a reason are c-010 and c-011, Fitzgerald and
// Yamamoto, neither of which was in it. Read off fixtures/households.yaml;
// Task 8 re-derives it from the file.
const FIXTURE_NAMES = [
  "Rivera", "Okonkwo", "Nguyen", "Haddad", "Delacroix", "Torres",
  "Abebe", "Silva", "Kowalski", "Fitzgerald", "Yamamoto", "Mensah",
];
const IDENTITY = new RegExp(`${FIXTURE_NAMES.join("|")}|\\+1555|Household`, "i");

const escalated: CaseSummary = {
  caseId: "c-011", status: "escalated", program: "medicaid",
  deadline: "2026-10-22", reason: "material_income_change: Income moved 30.0%", filed: false,
};

describe("the case row", () => {
  it("shows the case id and never a household name", () => {
    // Hard rule 9. The row is the one place a name would look natural to add.
    const row = formatCaseRow(escalated);
    expect(row.title).toBe("c-011");
    expect(JSON.stringify(row)).not.toMatch(IDENTITY);
  });

  it("passes a name through only if one is in the input, and there is nowhere for one to be", () => {
    // Proves the guard above can fail rather than being true of every input:
    // feeding a name in via `reason` — the exact path that put one into
    // CloudWatch — must be caught. `CaseSummary` has no name field, so `reason`
    // is the only carrier, which is why this is the shape the test uses.
    const leaked: CaseSummary = { ...escalated, reason: "source_conflict: The Yamamoto Household disagrees" };
    expect(JSON.stringify(formatCaseRow(leaked))).toMatch(IDENTITY);
  });

  it("lists all twelve fixture surnames, so it cannot pass by naming the wrong three", () => {
    // The draft's pattern had three of twelve. A guard that omits the surname of
    // the household most likely to leak is indistinguishable from one that
    // works, on every input either of them sees.
    expect(FIXTURE_NAMES).toHaveLength(12);
    for (const name of ["Fitzgerald", "Yamamoto", "Mensah"]) {
      expect(IDENTITY.test(`escalated: the ${name} Household`)).toBe(true);
    }
  });

  it("surfaces the gate's typed reason, not a generic label", () => {
    // "Needs review" tells a caseworker nothing. The reason code is the whole
    // value of the escalation.
    const row = formatCaseRow(escalated);
    expect(row.code).toBe("material_income_change");
    expect(row.detail).toContain("Income moved 30.0%");
  });

  it("reads an absent reason as handled rather than as an empty escalation", () => {
    const acted: CaseSummary = { ...escalated, status: "acted", reason: null, filed: true };
    expect(formatCaseRow(acted).detail).toMatch(/filed/i);
  });

  it("does not claim a filing the ledger did not confirm", () => {
    // hard rule 6 at the render boundary: `acted` with `filed: false` is the
    // "clean case, no renewal" outcome, and must not read as success.
    const odd: CaseSummary = { ...escalated, status: "acted", reason: null, filed: false };
    expect(formatCaseRow(odd).detail).not.toMatch(/\bfiled\b/i);
  });

  it("does not claim a filing for an error case either", () => {
    // `error` is what `lib/cases.ts` reports when nothing was filed AND nothing
    // escalated, and `authorize` refuses it as `case_incomplete`. The row must
    // say the sweep must be re-run, not that Grace handled it — the same
    // unconfirmed-success claim hard rule 6 forbids, aimed at the one person who
    // could still save the family.
    const failed: CaseSummary = { ...escalated, status: "error", reason: null, filed: false };
    const row = formatCaseRow(failed);
    expect(row.detail).not.toMatch(/\bfiled\b/i);
    expect(row.detail).not.toMatch(/handled/i);
    expect(row.detail).toMatch(/re-run/i);
  });

  it("uses the word `filed` only where a filing is confirmed", () => {
    // The invariant, over every status rather than one case at a time. Held as
    // "the word appears only with the evidence" rather than "this sentence does
    // not contain it": a message saying `nothing was filed` is true and still
    // fails, which is the right polarity for a guard that cannot read English.
    // A first draft of `fallbackDetail` said exactly that and this caught it.
    const statuses: CaseStatus[] = ["acted", "escalated", "error"];
    let checked = 0;
    for (const status of statuses) {
      for (const filed of [true, false]) {
        const detail = formatCaseRow({ ...escalated, status, reason: null, filed }).detail;
        const claimsFiling = /\bfiled\b/i.test(detail);
        expect(claimsFiling, `${status}/filed=${filed}: "${detail}"`)
          .toBe(status === "acted" && filed);
        checked += 1;
      }
    }
    expect(checked).toBe(6);
  });

  it("never renders `filed` as a claim on a row from listQueue", () => {
    // `listQueue`'s `filed` is `false` BY CONSTRUCTION, not by measurement: the
    // escalation-queue GSI projects escalation rows only, so that query cannot
    // see a `renewal_submitted` row. An escalated row must therefore reach its
    // detail from `reason`, never from `filed` — otherwise `/queue` would render
    // a claim about a fact it did not measure.
    const fromQueue: CaseSummary = { ...escalated, filed: false };
    const asIfFiled: CaseSummary = { ...escalated, filed: true };
    expect(formatCaseRow(fromQueue).detail).toBe(formatCaseRow(asIfFiled).detail);
  });

  it("gives escalation its own tone, so the eye finds it", () => {
    expect(statusTone("escalated")).not.toBe(statusTone("acted"));
    expect(statusTone("error")).not.toBe(statusTone("acted"));
  });

  it("gives every status a distinct tone and label, with no default arm to hide in", () => {
    // A `default:` arm in a switch over a union means adding a fourth status
    // silently inherits the third's colour. Every arm is named, so the set is
    // exhaustive by construction — and this asserts they differ, which a
    // three-way `default` would not.
    const all: CaseStatus[] = ["acted", "escalated", "error"];
    expect(new Set(all.map(statusTone)).size).toBe(3);
    expect(new Set(all.map(statusLabel)).size).toBe(3);
  });

  it("leaves an unknown deadline empty for the renderer to place a dash in", () => {
    // `lib/cases.ts` returns "" for a deadline it could not measure, on purpose:
    // a presentation dash inside the data layer is a magic value a caller cannot
    // tell from real data. So this helper must pass "" through rather than
    // substituting, and the page writes `{row.deadline || "—"}`.
    expect(formatCaseRow({ ...escalated, deadline: "" }).deadline).toBe("");
  });
});

describe("the reason line", () => {
  it("splits the gate's typed code from its measurement", () => {
    // `grace/run.py`'s `gate_reason` joins `GateReason.code` and `.detail` with
    // ": ", so the code is a value from a closed set of eight and the rest is
    // prose. Splitting them back apart is presentation, which is where the
    // division of labour puts it — `lib/cases.ts` returns the string verbatim.
    const { code, detail } = splitReason("missing_document: proof_of_residency is not on file");
    expect(code).toBe("missing_document");
    expect(detail).toBe("proof_of_residency is not on file");
  });

  it("only treats a real reason code as a code", () => {
    // Measured on the live table: c-012's newest escalation reason begins
    // "A caseworker must decide. source_conflict: household size 5 on
    // application…". Splitting on the first colon would render
    // "A caseworker must decide. source_conflict" as the typed code — a label
    // out of no closed set, in a monospaced chip that implies it came from the
    // gate. Only the eight codes `grace/authority.py` actually emits count.
    const live = "A caseworker must decide. source_conflict: household size 5 on application, 3 on most recent wage record";
    const { code, detail } = splitReason(live);
    expect(code).toBeNull();
    expect(detail).toBe(live);
  });

  it("keeps a multi-condition reason whole rather than showing only the first", () => {
    // `gate_reason` joins several reasons with "; " because a case can fail more
    // than one condition and reason order is not a contract (Plan 1 Task 3).
    // Showing only the first would drop a fact the caseworker needs.
    const both = "missing_document: proof_of_residency is not on file; source_conflict: sizes disagree";
    const { code, detail } = splitReason(both);
    expect(code).toBe("missing_document");
    expect(detail).toContain("source_conflict");
  });

  it("survives a verification error, which is a reason code too", () => {
    // `evaluate` emits `verification_error` when a pack will not load, and
    // `gate_reason` also returns a bare "Verification error: …" sentence from
    // its own `except`. Neither may render as an empty escalation.
    expect(splitReason("verification_error: pack would not load").code).toBe("verification_error");
    expect(splitReason("Verification error: could not verify case c-003 (boom).").detail)
      .toContain("could not verify");
  });
});

describe("the sweep summary", () => {
  it("counts escalations from the status, and acted only from a confirmed filing", () => {
    // The headline claim on `/`. `acted + escalated` must not be
    // `cases.length - escalated`: that arithmetic silently folds an `error` case
    // into "handled alone", which is the unconfirmed-success claim hard rule 6
    // exists to forbid. Measured against a caseload holding one of each.
    const cases: CaseSummary[] = [
      { ...escalated, caseId: "c-001", status: "acted", reason: null, filed: true },
      { ...escalated, caseId: "c-010" },
      { ...escalated, caseId: "c-003", status: "error", reason: null, filed: false },
    ];
    const s = summarise(cases);
    expect(s.acted).toBe(1);
    expect(s.escalated).toBe(1);
    expect(s.incomplete).toBe(1);
    expect(s.acted + s.escalated + s.incomplete).toBe(cases.length);
  });

  it("does not count an acted case with no filing behind it", () => {
    // `lib/cases.ts` cannot produce this shape — it reports `error` when nothing
    // was filed — but `summarise` takes a `CaseSummary[]` from anywhere, and the
    // count under the headline must never be larger than the evidence.
    const s = summarise([{ ...escalated, status: "acted", reason: null, filed: false }]);
    expect(s.acted).toBe(0);
    expect(s.incomplete).toBe(1);
  });

  it("puts every case in exactly one bucket", () => {
    // Plan 1 Task 6: a case counted twice or counted nowhere makes "nine handled
    // alone, three escalated" arithmetic that does not add up while each count
    // still looks plausible.
    const cases: CaseSummary[] = Array.from({ length: 12 }, (_, n) => ({
      ...escalated,
      caseId: `c-${String(n + 1).padStart(3, "0")}`,
      status: (n < 9 ? "acted" : "escalated") as CaseStatus,
      reason: n < 9 ? null : "missing_document: not on file",
      filed: n < 9,
    }));
    const s = summarise(cases);
    expect([s.acted, s.escalated, s.incomplete]).toEqual([9, 3, 0]);
  });

  it("reports an empty caseload as empty rather than as a clean sweep", () => {
    // A missing GRACE_TABLE_NAME makes all twelve reads return null, so `/`
    // renders zero rows. "0 handled alone" beside a healthy-looking page is the
    // failure `lib/cases.ts` keeps `readEnv` outside its `try` to avoid; the
    // page must at least not describe it as a completed sweep.
    const s = summarise([]);
    expect([s.acted, s.escalated, s.incomplete]).toEqual([0, 0, 0]);
    expect(s.total).toBe(0);
  });
});

describe("the caseworker's note", () => {
  it("renders markup as text rather than as an element", async () => {
    // The note is free text a human typed and DynamoDB stored verbatim. React
    // escapes text children itself, so the assertion is about the rendered
    // output, not about a helper: verified that renderToStaticMarkup turns
    // `<img src=x onerror="alert(1)">` into `&lt;img ...` with no live tag.
    const { renderToStaticMarkup } = await import("react-dom/server");
    const { createElement } = await import("react");
    const html = renderToStaticMarkup(
      createElement("p", null, '<img src=x onerror="alert(1)">'));
    expect(html).not.toMatch(/<img\s/);
    expect(html).toContain("&lt;img");
  });

  it("does not double-escape ordinary prose", () => {
    // The apostrophe is the whole point of this test. An escape helper applied
    // before JSX turns `the family's record` into `the family&#39;s record` on
    // screen, and the plan's original fixture had no apostrophe in it, so it
    // passed against the buggy version. `noteIsInert` is a check, not a
    // transform — nothing rewrites the caseworker's words.
    const note = "The family's wage record is stale; they re-filed.";
    expect(noteIsInert(note)).toBe(true);
    expect(noteIsInert('<img src=x onerror="alert(1)">')).toBe(false);
  });

  it("reacts to `<` and `>` only, so ordinary punctuation is not flagged", () => {
    // Apostrophes, quotes, and ampersands cannot open a tag, and flagging them
    // would reject prose a caseworker would reasonably type. Both directions
    // asserted, or "returns true" is true of every input.
    expect(noteIsInert('"quoted" & ampersand')).toBe(true);
    expect(noteIsInert("5 > 3 && 2 < 4")).toBe(false);
  });

  it("is a check and not a transform, so the note reaches the page unchanged", () => {
    // The property that matters is that nothing rewrites the words. A helper
    // returning a string would invite `{escapeNote(d.note)}`, which is the
    // double-escaping bug; a helper returning a boolean cannot be used that way.
    const note = "The family's record is fine.";
    expect(typeof noteIsInert(note)).toBe("boolean");
    expect(note).toBe("The family's record is fine.");
  });

  it("renders a note through React exactly as it was typed, apostrophe included", async () => {
    // The end-to-end version of the assertion above, because `noteIsInert`
    // returning a boolean does not by itself prove the page renders the raw
    // string. `&#x27;` in the markup is the correct escape of one apostrophe —
    // `&amp;#39;` would be the double-escaped bug, and it is what the draft's
    // `{escapeNote(d.note)}` produced.
    const { renderToStaticMarkup } = await import("react-dom/server");
    const { createElement } = await import("react");
    const note = "The family's wage record is stale.";
    const html = renderToStaticMarkup(createElement("p", null, note));
    expect(html).toBe("<p>The family&#x27;s wage record is stale.</p>");
    expect(html).not.toContain("&amp;");
  });
});

describe("the pages", () => {
  it("declares every data-reading page dynamic, so next build cannot prerender it", async () => {
    // Task 4 learned this the hard way: `/login` was prerendered at build time,
    // where reading configuration throws, and `next build` failed outright. A
    // page that reads the live table must be request-time — and the quieter half
    // is that a prerendered page would serve a snapshot of the caseload taken
    // whenever someone last deployed.
    //
    // Read off the module rather than grepped, so a page exporting the wrong
    // value fails here.
    const pages = ["@/app/page", "@/app/queue/page", "@/app/case/[id]/page"];
    let checked = 0;
    for (const page of pages) {
      const mod = (await import(page)) as { dynamic?: unknown };
      expect(mod.dynamic, `${page} must be force-dynamic`).toBe("force-dynamic");
      checked += 1;
    }
    // The loop is the assertion, so prove it ran (Plan 1 Task 8).
    expect(checked).toBe(3);
  });

  it("names the three columns a caseworker needs and no more", () => {
    // `CASE_COLUMNS` is exported so this is a property of the table rather than
    // of a heading string someone can edit past. No name column, and nowhere for
    // one — hard rule 9 at the shape level, the same way `CaseSummary` has no
    // name field.
    expect(CASE_COLUMNS).toEqual(["Case", "What Grace concluded", "Deadline"]);
    expect(CASE_COLUMNS.join(" ")).not.toMatch(IDENTITY);
  });

  it("verifies the session on every page that reads household data", async () => {
    // MEASURED BYPASS, not a precaution. Before this, no page called
    // `verifySession` at all — `grep verifySession app/` matched only the auth
    // callback and the decide route — so the pages' only guard was the *presence*
    // check in `proxy.ts`, which that file's own docstring calls "never the
    // security boundary". Against a real `next start` on the live table:
    //
    //   no cookie                              -> 307 to /login
    //   Cookie: grace_session=totally.forged.token -> 200, 45143 bytes,
    //     all twelve case ids and all three typed escalation reasons
    //
    // An unsigned literal string was a complete read bypass. Asserted against
    // the source because `requireSession` calls `next/headers`, which has no
    // request context in a unit test — the property is "this page calls the
    // verifier", and the verifier itself is tested in `cognito.test.ts`.
    const fs = await import("node:fs");
    const pages = ["../app/page.tsx", "../app/queue/page.tsx", "../app/case/[id]/page.tsx"];
    let checked = 0;
    for (const page of pages) {
      const src = fs.readFileSync(new URL(page, import.meta.url), "utf8");
      expect(src, `${page} must import requireSession`).toContain("requireSession");
      expect(src, `${page} must await it`).toMatch(/await\s+requireSession\(\)/);
      // Before the read, or an unauthenticated request still hits DynamoDB and
      // an error message can differ between an existing and a missing case.
      const gate = src.search(/await\s+requireSession\(\)/);
      const read = src.search(/await\s+(listCases|listQueue|readCase)\(/);
      expect(read, `${page} must read the table`).toBeGreaterThan(-1);
      expect(gate, `${page} must verify before it reads`).toBeLessThan(read);
      checked += 1;
    }
    expect(checked).toBe(3);
  });

  it("routes a failed verification to sign-in rather than to an empty page", async () => {
    // `requireSession` must `redirect`, not return null. A page rendering an
    // empty caseload for an unverified session is indistinguishable from a real
    // sweep that found nothing, which is the same class of confusion as
    // `lib/cases.ts` keeping `readEnv` outside its `try`.
    const fs = await import("node:fs");
    const src = fs.readFileSync(new URL("../lib/session.ts", import.meta.url), "utf8");
    expect(src).toContain('redirect("/login")');
  });

  it("refuses when the verifier itself cannot run, rather than inventing a session", async () => {
    // BEHAVIOURAL, and the first two attempts at this test both passed against a
    // `catch` that fabricated `{ sub: "x", role: "caseworker", … }` — a complete
    // authentication bypass, green on all 155 tests. Why, measured:
    //
    //   1. A source grep (`/catch\s*{[^}]*session = null/`) matched the
    //      fabricating version loosely enough to pass. A guard on the shape of
    //      the source is not a guard on what the function does.
    //   2. Deleting COGNITO_ISSUER does not make `verifySession` throw —
    //      `issuer()` is called INSIDE its own `try`, so a missing variable comes
    //      back as `null`. Probed with a forged string, `123`, `{}`, and `[]`:
    //      all `null`, never an exception. The `catch` was never reached.
    //
    // So the throw has to be injected. `cognito.ts` is mocked to throw the way a
    // misconfigured verifier would, which is the only route into that branch —
    // and with it mocked, a fabricated session makes this test fail.
    const previous = process.env.COGNITO_ISSUER;
    vi.resetModules();
    vi.doMock("next/headers", () => ({
      cookies: async () => ({ get: () => ({ value: "totally.forged.token" }) }),
    }));
    vi.doMock("@/lib/cognito", () => ({
      SESSION_COOKIE: "grace_session",
      verifySession: async () => {
        throw new Error("COGNITO_ISSUER is not set.");
      },
    }));
    try {
      const { requireSession } = await import("@/lib/session");
      // `redirect()` throws `NEXT_REDIRECT`. That it *throws* is the assertion: a
      // returned value here is a caseworker session nobody authenticated.
      await expect(requireSession()).rejects.toThrow(/NEXT_REDIRECT/);
    } finally {
      vi.doUnmock("@/lib/cognito");
      vi.doUnmock("next/headers");
      vi.resetModules();
      if (previous !== undefined) process.env.COGNITO_ISSUER = previous;
    }
  });

  it("fails closed when the cookie jar itself throws", async () => {
    // `cookies()` throws outside a request scope. It sat ABOVE `requireSession`'s
    // `try` at first, so the one call in that function which genuinely can fail
    // was the one its fail-closed handling did not cover — verified by mocking the
    // throw and watching it propagate rather than redirect. Plan 1 Task 6 found
    // the identical shape in `list_documents`, where an `OverflowError` escaped a
    // `try` whose indentation had not moved to cover a new loop.
    vi.resetModules();
    vi.doMock("next/headers", () => ({
      cookies: async () => {
        throw new Error("`cookies` was called outside a request scope.");
      },
    }));
    try {
      const { requireSession } = await import("@/lib/session");
      await expect(requireSession()).rejects.toThrow(/NEXT_REDIRECT/);
    } finally {
      vi.doUnmock("next/headers");
      vi.resetModules();
    }
  });

  it("carries no resume vocabulary anywhere in the page layer", async () => {
    // Plan 3's approve/deny path records the decision and re-invokes so the gate
    // re-evaluates; it never resumes a paused graph, because any truthy resume
    // response approves the blocked tool (Plan 1 Task 6). The decision form is
    // the surface that would have been tempted to add one.
    const fs = await import("node:fs");
    const files = [
      "../components/case-table.tsx",
      "../components/decision-form.tsx",
      "../app/page.tsx",
      "../app/queue/page.tsx",
      "../app/case/[id]/page.tsx",
    ];
    let scanned = 0;
    for (const file of files) {
      const src = fs.readFileSync(new URL(file, import.meta.url), "utf8");
      for (const forbidden of ["interruptResponse", "APPROVE_DECISIONS", "MAX_RESUME_ROUNDS"]) {
        expect(src, `${file} must not mention ${forbidden}`).not.toContain(forbidden);
      }
      // Anything `NEXT_PUBLIC_` is inlined into the client bundle, and every
      // value this app holds is a credential or a household-scoped identifier.
      expect(src, `${file} must not read a NEXT_PUBLIC_ variable`).not.toContain("NEXT_PUBLIC_");
      scanned += 1;
    }
    expect(scanned).toBe(files.length);
  });

  it("keeps the client bundle to the decision form alone", async () => {
    // `"use client"` on a page would ship the DynamoDB reader's import graph to
    // the browser and move the read off the server. Only the form needs state.
    const fs = await import("node:fs");
    const server = ["../app/page.tsx", "../app/queue/page.tsx", "../app/case/[id]/page.tsx",
      "../components/case-table.tsx"];
    for (const file of server) {
      const src = fs.readFileSync(new URL(file, import.meta.url), "utf8");
      expect(src, `${file} must stay a server component`).not.toContain("use client");
    }
    const form = fs.readFileSync(new URL("../components/decision-form.tsx", import.meta.url), "utf8");
    expect(form).toContain('"use client"');
  });

  it("reads the queue's rows from listQueue and the sweep's from listCases", async () => {
    // Not interchangeable. `listQueue` reads the GSI, which projects escalation
    // rows only and so cannot see a `renewal_submitted` row — its `filed` is
    // false by construction. The 9/3 split on `/` is a hard-rule-6 claim, so it
    // must come from `listCases`, which reads the ledger for all twelve.
    const fs = await import("node:fs");
    const home = fs.readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
    const queue = fs.readFileSync(new URL("../app/queue/page.tsx", import.meta.url), "utf8");
    expect(home).toContain("listCases");
    expect(home).not.toContain("listQueue");
    expect(queue).toContain("listQueue");
    expect(queue).not.toContain("listCases");
  });
});
