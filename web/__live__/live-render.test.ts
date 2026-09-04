/**
 * TEMPORARY — Step 6 of Task 6. Renders the three real page components against
 * the LIVE grace-cases table, with a genuinely verified session, and inspects
 * the markup. Deleted after the run.
 *
 * READS ONLY. Nothing here writes to DynamoDB or invokes the runtime.
 *
 * Why a locally minted token rather than a browser sign-in: the `grace-dashboard`
 * app client permits only `ALLOW_USER_SRP_AUTH` and `ALLOW_REFRESH_TOKEN_AUTH`,
 * so there is no non-interactive way to obtain a real ID token, and adding
 * `ALLOW_ADMIN_USER_PASSWORD_AUTH` would mean an `UpdateUserPoolClient` — a FULL
 * REPLACE (Task 4's finding) on a live resource, to see a page render. The
 * `NODE_ENV === "test"` JWKS injection exists for exactly this, and the DATA is
 * still read from the live table, which is what Step 6 is about.
 *
 * The forged-cookie and no-cookie refusals were measured separately against a
 * real `next start`, where that injection is unreachable.
 */

import { beforeAll, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { exportJWK, generateKeyPair, SignJWT } from "jose";

const ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_HXs3b0APR";
const CLIENT_ID = "11ejmthb9mdrfkm5s2dm51jdiv";
const KID = "grace-step6";

// `useRouter` needs an app-router context that `renderToStaticMarkup` does not
// provide — a harness limitation, not a page bug. Proven separately: the same
// component SSRs at HTTP 200 through a real `next start`.
vi.mock("next/navigation", async importOriginal => {
  const actual = await importOriginal<typeof import("next/navigation")>();
  return { ...actual, useRouter: () => ({ refresh: () => {} }) };
});

let cookieValue = "";
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (name === "grace_session" ? { value: cookieValue } : undefined) }),
}));

const FIXTURE_NAMES = [
  "Rivera", "Okonkwo", "Nguyen", "Haddad", "Delacroix", "Torres",
  "Abebe", "Silva", "Kowalski", "Fitzgerald", "Yamamoto", "Mensah",
];

function scanIdentity(label: string, html: string): void {
  const hits: string[] = [];
  // `includes`, not `new RegExp(name)`: "+1555" is `/+1555/`, which throws
  // `Nothing to repeat` because `+` leads. The shipped guard builds one
  // alternation where it does not lead, so it is valid there.
  const lower = html.toLowerCase();
  for (const name of [...FIXTURE_NAMES, "+1555"]) {
    if (lower.includes(name.toLowerCase())) hits.push(name);
  }
  // "Household" is in the SHIPPED guard's pattern because the fixture format is
  // `The Yamamoto Household`, and there it scans a `CaseRow` — data only, where
  // the word cannot legitimately appear. Scanning whole MARKUP is different, and
  // two measured false positives show why the word alone is the wrong test here:
  // the page's own copy says "3 households need a decision", and Grace's own
  // escalation prose on the live table says "even if we take the larger
  // household size" (c-012's referee) and "household of 3" (a c-011 ledger
  // question). None of those is identity. The twelve surnames and the reserved
  // phone range are what identity actually looks like, and they are checked
  // above — a *named* household is `The <surname> Household`, so the surname is
  // the necessary part and the word is not.
  console.log(`  [${label}] household identity in markup: ${hits.length === 0 ? "NONE" : hits.join(", ")}`);
  expect(hits, `${label} must carry no household identity`).toEqual([]);
}

it("the scan itself can fail", () => {
  // Otherwise "NONE" above is true of every input and proves nothing.
  expect(() => scanIdentity("self-test", "<p>escalated: the Yamamoto Household</p>")).toThrow();
  expect(() => scanIdentity("self-test", "<p>Mensah</p>")).toThrow();
  expect(() => scanIdentity("self-test", "<p>+15550000011</p>")).toThrow();
  // And does not fire on the page's own copy, or on Grace's own prose.
  scanIdentity("self-test", "<p>3 households need a decision. even if we take the larger household size</p>");
});

beforeAll(async () => {
  const { privateKey, publicKey } = await generateKeyPair("RS256");
  const jwk = await exportJWK(publicKey);
  process.env.COGNITO_ISSUER = ISSUER;
  process.env.COGNITO_CLIENT_ID = CLIENT_ID;
  process.env.COGNITO_TEST_JWKS = JSON.stringify({
    keys: [{ ...jwk, kid: KID, alg: "RS256", use: "sig" }],
  });
  cookieValue = await new SignJWT({
    token_use: "id",
    "custom:role": "caseworker",
  })
    .setProtectedHeader({ alg: "RS256", kid: KID })
    .setIssuer(ISSUER)
    .setAudience(CLIENT_ID)
    .setSubject("7f3a91c2-4d5e-4a1b-9c8d-0e1f2a3b4c5d")
    .setExpirationTime("1h")
    .sign(privateKey);
});

async function render(mod: string, props?: unknown): Promise<string> {
  const page = ((await import(mod)) as { default: (p?: unknown) => Promise<React.ReactElement> }).default;
  return renderToStaticMarkup(await page(props));
}

it("renders / against the live table", async () => {
  const { listCases } = await import("@/lib/cases");
  const { summarise } = await import("@/components/case-table");
  const cases = await listCases();
  const s = summarise(cases);
  console.log("\n===== / (sweep) =====");
  console.log(`read ${cases.length} of 12 — acted=${s.acted} escalated=${s.escalated} incomplete=${s.incomplete}`);
  for (const c of cases) {
    console.log(
      `  ${c.caseId}  ${c.status.padEnd(9)} filed=${String(c.filed).padEnd(5)} ` +
      `program=${(c.program || "-").padEnd(9)} deadline=${c.deadline || "-"}  ${(c.reason ?? "").slice(0, 62)}`,
    );
  }
  const html = await render("@/app/page");
  console.log(`\n  markup: ${html.length} chars`);
  for (const line of html.replace(/></g, ">\n<").split("\n")) {
    if (/handled alone|waiting on you|no outcome|of 12 households/.test(line)) console.log(`  | ${line.trim()}`);
  }
  console.log(`  sweep-strip blocks: ${(html.match(/<li><a href="\/case\//g) ?? []).length}`);
  scanIdentity("/", html);
});

it("renders /queue against the live GSI", async () => {
  const { listQueue } = await import("@/lib/cases");
  const queue = await listQueue();
  console.log("\n===== /queue =====");
  console.log(`rows after de-duplication: ${queue.length}  (the GSI itself holds 19)`);
  for (const c of queue) {
    console.log(`  ${c.caseId}  deadline=${c.deadline}  filed=${String(c.filed)}`);
    console.log(`      ${c.reason}`);
  }
  const html = await render("@/app/queue/page");
  console.log(`\n  markup: ${html.length} chars`);
  for (const line of html.replace(/></g, ">\n<").split("\n")) {
    if (/need a decision|Soonest|Nothing is waiting/.test(line)) console.log(`  | ${line.trim()}`);
  }
  console.log(`  reason-code chips rendered: ${(html.match(/<code class="shrink-0/g) ?? []).length}`);
  scanIdentity("/queue", html);
});

it("renders one household of each kind against the live table", async () => {
  const { readCase } = await import("@/lib/cases");
  for (const id of ["c-001", "c-010", "c-011", "c-012"]) {
    const detail = await readCase(id);
    if (detail === null) throw new Error(`${id} read back null`);
    const html = await render("@/app/case/[id]/page", { params: Promise.resolve({ id }) });
    const stamps = detail.ledger.map(r => Date.parse(r.at));
    console.log(`\n===== /case/${id} =====`);
    console.log(
      `  status=${detail.summary.status} filed=${detail.summary.filed} ` +
      `program="${detail.summary.program}" deadline=${detail.summary.deadline} ` +
      `ledger=${detail.ledger.length} decisions=${detail.decisions.length}`,
    );
    console.log(`  ledger chronological: ${stamps.every((t, i) => i === 0 || t >= stamps[i - 1]!)}`);
    console.log(`  decision form offered: ${html.includes("Approve and re-check")}`);
    console.log(`  markup: ${html.length} chars`);
    if (id === "c-011") {
      for (const row of detail.ledger.slice(0, 6)) {
        console.log(`    ${row.at}  ${row.kind.padEnd(12)} ${JSON.stringify(row.detail)}`);
      }
    }
    scanIdentity(`/case/${id}`, html);
  }
});

it("refuses to render any page without a verified session", async () => {
  // The other polarity, in-process: `requireSession` must throw Next's redirect
  // rather than render. Confirms the gate is the page's own, not the proxy's.
  const previous = cookieValue;
  cookieValue = "totally.forged.token";
  try {
    for (const mod of ["@/app/page", "@/app/queue/page"]) {
      await expect(render(mod)).rejects.toThrow(/NEXT_REDIRECT|redirect/i);
      console.log(`  ${mod} with a forged cookie: redirected, not rendered`);
    }
    await expect(render("@/app/case/[id]/page", { params: Promise.resolve({ id: "c-011" }) }))
      .rejects.toThrow(/NEXT_REDIRECT|redirect/i);
    console.log("  @/app/case/[id]/page with a forged cookie: redirected, not rendered");
  } finally {
    cookieValue = previous;
  }
});
