/**
 * WHAT MAY BE SUBMITTED AS A NEW CASE. Pure, and deliberately so.
 *
 * This is `lib/authorize.ts`'s sibling: it maps a session and a posted body onto
 * a `Permit` or a typed `Refusal` and it touches nothing — no AWS client, no
 * clock read (the current time arrives as an argument), no table lookup. The
 * route measures and writes; this file decides.
 *
 * **The form collects no household identity, and the validator refuses it
 * rather than ignoring it.** Hard rule 9, and the wider version Plan 2 learned
 * the hard way: `read_case` used to return `display_name`, a referee quoted it
 * into its deliberation prose, that prose became an escalation reason, and the
 * reason reached CloudWatch as a Step Functions payload — a path span redaction
 * does not cover at all. An intake form is exactly where a name, a phone number,
 * and an address feel natural to collect, which is why the refusal is
 * structural: the accepted keys are an **allowlist**, so an identity field is
 * rejected by construction rather than by anyone remembering to strip it, and a
 * posted key that looks identity-shaped is refused with a code that says so out
 * loud instead of being silently dropped.
 *
 * Grace does not need to know who the family is in order to know whether their
 * paperwork is complete. A real deployment's identity lives in the system of
 * record that referred the household, keyed by case id.
 *
 * **There is no free-text field here at all**, and that is the property that
 * makes the guarantee cheap to keep. Every accepted value is drawn from a closed
 * set (`program`, `state`, `language`, a document id), or is a bounded integer,
 * or is an ISO date, or is a case id matching a fixed pattern. `source_conflicts`
 * — the one free-text field a case record can carry — is deliberately not
 * collectable, so no prose a caseworker types can reach a model's context.
 * `__tests__/intake.test.ts` asserts that closure rather than trusting it.
 *
 * **Uniqueness is not checked here, and that is a correction to the plan.** A
 * "does this case id already exist" read followed by a write is a race: two
 * submissions of the same id both read absent and both write, and the second
 * overwrites a case record whose ledger, escalation, and decision history live
 * in the same partition. The route writes under
 * `ConditionExpression="attribute_not_exists(sk)"` instead, which is atomic, and
 * turns the service's refusal into `case_exists`. The shape of an id is
 * checkable purely; its availability is not.
 *
 * Hard rule 3: everything entered is synthetic, and `app/new/page.tsx` says so
 * on screen.
 */

import type { AttributeValue } from "@aws-sdk/client-dynamodb";
import type { SessionIdentity } from "./types";
import { CASEWORKER_ROLE } from "./authorize";

/** The programs Grace holds a rule pack for. An allowlist, matching
 *  `grace/rules/packs/*.yaml` — `__tests__/intake.test.ts` reads that directory
 *  and asserts these two sets are equal, so adding a pack without updating this
 *  constant fails a test rather than shipping a form that offers a program the
 *  gate cannot evaluate.
 *
 *  **That test named a file that did not exist until now.** This comment said
 *  `tests/test_intake_contract.py`; there is no such file, and never was. A
 *  docstring asserting that some other layer performs a check is not evidence
 *  that it does — the Plan 3 finding, in the same repository, one plan later. */
export const PROGRAMS = ["medicaid", "snap"] as const;

/** Likewise for states. One today; the same disk-derived test guards it. */
export const STATES = ["NY"] as const;

/** Every document id any pack asks for. A document the packs never require can
 *  only ever be ignored by the gate, so accepting one would invite a caseworker
 *  to believe they had satisfied a requirement they had not. */
export const DOCUMENT_IDS = [
  "proof_of_income",
  "proof_of_residency",
  "proof_of_identity",
  "proof_of_expenses",
] as const;

/** The languages outreach can be drafted in.
 *
 *  A language is a drafting preference, not an identifier — it says nothing
 *  about who the family is, and `read_case` already surfaces it so the outreach
 *  drafter writes in the family's own language. Kept to a closed set rather than
 *  a free string for the same reason everything else here is closed. */
export const LANGUAGES = ["en", "es", "vi", "ar", "fr", "am", "pt", "pl", "ja"] as const;

/** Exactly the keys a submission may carry. Everything else refuses.
 *
 *  An allowlist, not a denylist. Plan 3 measured what a denylist costs: a purity
 *  guard that grepped for literal spellings was defeated three separate ways by
 *  equivalent spellings nobody had thought of. Here the equivalent leak would be
 *  `householdName` slipping past a list that only knew `household_name`. */
const ALLOWED_KEYS = [
  "case_id",
  "program",
  "state",
  "cert_end",
  "language",
  "monthly_income_cents",
  "size",
  "reported_income_cents",
  "reported_size",
  "documents",
] as const;

/** Substrings that make an unexpected key *identity-shaped*.
 *
 *  This does not decide whether a key is accepted — `ALLOWED_KEYS` already did
 *  that, and every key outside it is refused either way. It decides only which
 *  refusal the caseworker reads, so someone who tries to add a name to the form
 *  is told why the field does not exist rather than being told "unknown field"
 *  and going looking for the right spelling. */
const IDENTITY_MARKERS = [
  "name", "phone", "mobile", "tel", "address", "street", "city", "zip",
  "postal", "postcode", "email", "mail", "ssn", "social", "birth", "dob",
  "contact", "guardian", "household_id", "householdid", "person", "applicant",
];

/** Bounds. None is a DynamoDB limit; each exists so one request cannot write an
 *  item that is absurd on its face.
 *
 *  `MAX_DOCUMENTS` mirrors `grace/cases/record.py`'s constant of the same name,
 *  and `__tests__/intake.test.ts` reads that constant off the Python source and
 *  asserts the two agree — a form that accepted more documents than the reader
 *  will parse would write a row the agent then refuses to load. */
export const MAX_DOCUMENTS = 32;
export const MAX_INCOME_CENTS = 100_000_000;
export const MAX_HOUSEHOLD_SIZE = 30;
const MIN_YEAR = 2000;
const MAX_YEAR = 2100;

/** `c-` and three digits, which is every case id that has ever existed here.
 *
 *  Strict on purpose: this value becomes a DynamoDB partition key, appears in
 *  CloudWatch metrics and Step Functions payloads, and is the one identifier
 *  Grace attaches to a household. A pattern loose enough to accept
 *  `c-001-jane-doe` would let a name in through the only field that is not a
 *  closed set. */
const CASE_ID = /^c-[0-9]{3}$/;

const ISO_DATE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;

/** What may be written as `created_by`: an opaque identifier and nothing else.
 *
 *  The value is the caseworker's Cognito `sub`, and it becomes a durable
 *  `RECORD#v1` attribute so the case page can say *whose* assertion the document
 *  status is — Grace takes a caseworker's word that a document was sent to the
 *  state and cannot verify it, and hard rule 6 is about never letting an
 *  assertion read as a confirmed fact.
 *
 *  It is checked here because nothing upstream checks it. `verifySession` asks
 *  only that `sub` is a non-empty string, so an identity provider issuing an
 *  email as the subject would put an address into a row a model reads and Step
 *  Functions logs — the exact path a surname took to CloudWatch in Plan 2.
 *  Mirrors `_OPAQUE_SUBJECT` in `grace/cases/record.py`, which refuses the same
 *  shapes on the Python side; the contract test below pins the pair. */
const OPAQUE_SUBJECT = /^[A-Za-z0-9._:-]{1,128}$/;

/** The sort key of a case record. One row per case, version in the key, so a
 *  future v2 shape is a new row an old reader simply does not match rather than
 *  a changed row it misparses. Mirrors `infra/naming.RECORD_SK`. */
export const RECORD_SK = "RECORD#v1";

/** The partition that enumerates every case. Mirrors
 *  `infra/naming.CASE_DIRECTORY_PK`. */
export const CASE_DIRECTORY_PK = "CASE_DIRECTORY";

export type Program = (typeof PROGRAMS)[number];
export type StateCode = (typeof STATES)[number];
export type DocumentId = (typeof DOCUMENT_IDS)[number];
export type Language = (typeof LANGUAGES)[number];

export interface IntakeDocument {
  id: DocumentId;
  received: string;
  expires: string | null;
}

/** A submission, normalised. This is what the route writes, and nothing here is
 *  optional — the validator has already resolved every absence into an explicit
 *  value, so no writer downstream has to guess one. */
export interface CaseRecordInput {
  caseId: string;
  program: Program;
  state: StateCode;
  certEnd: string;
  language: Language;
  monthlyIncomeCents: number;
  size: number;
  reportedIncomeCents: number | null;
  reportedSize: number | null;
  documents: IntakeDocument[];
}

export type IntakeRefusalCode =
  | "no_session"
  | "session_expired"
  | "wrong_role"
  | "identity_subject"
  | "identity_field"
  | "unknown_field"
  | "bad_case_id"
  | "unknown_program"
  | "unknown_state"
  | "unknown_language"
  | "bad_cert_end"
  | "bad_income"
  | "bad_size"
  | "bad_reported_income"
  | "bad_reported_size"
  | "bad_documents"
  | "case_exists";

export interface IntakeRefusal {
  permitted: false;
  code: IntakeRefusalCode;
  message: string;
}

export interface IntakePermit {
  permitted: true;
  createdBy: string;
  record: CaseRecordInput;
}

export type IntakeAuthorisation = IntakePermit | IntakeRefusal;

function refuse(code: IntakeRefusalCode, message: string): IntakeRefusal {
  return { permitted: false, code, message };
}

/** A whole number inside a range, refusing every shape that is not one.
 *
 *  `Number.isInteger` refuses `NaN`, both infinities, and `4.5` — all three of
 *  which a JSON body can carry. `JSON.parse('{"n":1e400}').n` is `Infinity` and
 *  `typeof` it is `"number"`, which is the exact hole Plan 3 found in the JWT
 *  expiry check: a non-finite number passes every comparison and behaves like
 *  nothing. Refuse the type; never coerce it, because coercion invents a figure
 *  nobody entered. */
function wholeNumberInRange(value: unknown, min: number, max: number): number | null {
  if (typeof value !== "number" || !Number.isInteger(value)) return null;
  if (value < min || value > max) return null;
  return value;
}

/** An ISO calendar date that is also a real day.
 *
 *  The regex alone accepts `2026-02-30`, and `new Date("2026-02-30")` in Node
 *  rolls it forward to 2 March rather than refusing — so the parsed date is
 *  re-rendered and compared against the input. A certification end date silently
 *  moved two days puts the renewal window somewhere nobody chose, and every
 *  verdict downstream is computed from it with no error anywhere. */
function isoDate(value: unknown): string | null {
  if (typeof value !== "string" || !ISO_DATE.test(value)) return null;
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return null;
  if (parsed.toISOString().slice(0, 10) !== value) return null;
  const year = parsed.getUTCFullYear();
  if (year < MIN_YEAR || year > MAX_YEAR) return null;
  return value;
}

function looksLikeIdentity(key: string): boolean {
  const lower = key.toLowerCase();
  return IDENTITY_MARKERS.some(marker => lower.includes(marker));
}

function documents(value: unknown): IntakeDocument[] | null {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) return null;
  if (value.length > MAX_DOCUMENTS) return null;
  const parsed: IntakeDocument[] = [];
  for (const entry of value) {
    if (entry === null || typeof entry !== "object" || Array.isArray(entry)) return null;
    const row = entry as Record<string, unknown>;
    for (const key of Object.keys(row)) {
      // Same allowlist discipline one level down. A document entry is an object
      // a client controls, so it is exactly as good a place to smuggle a field
      // as the body itself.
      if (key !== "id" && key !== "received" && key !== "expires") return null;
    }
    const id = row.id;
    if (typeof id !== "string" || !(DOCUMENT_IDS as readonly string[]).includes(id)) {
      return null;
    }
    const received = isoDate(row.received);
    if (received === null) return null;
    let expires: string | null = null;
    if (row.expires !== undefined && row.expires !== null && row.expires !== "") {
      expires = isoDate(row.expires);
      if (expires === null) return null;
    }
    parsed.push({ id: id as DocumentId, received, expires });
  }
  return parsed;
}

/** Decide whether this submission may be written.
 *
 *  Session checks precede field checks, for the same reason `authorize` orders
 *  its refusals that way: what an unauthenticated caller learns from a refusal
 *  should not depend on the data. */
export function validateIntake(
  session: SessionIdentity | null,
  body: unknown,
  nowMs: number,
): IntakeAuthorisation {
  if (session === null) return refuse("no_session", "Sign in to add a case.");
  if (!Number.isFinite(session.expiresAt) || !Number.isFinite(nowMs)) {
    return refuse("session_expired", "Your session expired. Sign in again.");
  }
  // `<=`, not `<`: a session expiring exactly now is expired. The other way
  // round honours a just-expired session, which is the fail-open direction.
  if (session.expiresAt <= nowMs) {
    return refuse("session_expired", "Your session expired. Sign in again.");
  }
  if (session.role !== CASEWORKER_ROLE) {
    return refuse("wrong_role", "This account may not add cases.");
  }
  // Still a session check, so it sits with the others and ahead of every field
  // check. The subject is about to be written onto a durable row as the
  // provenance of a document assertion, and an email or a name there is identity
  // in a place hard rule 9 forbids. Refused rather than silently dropped:
  // dropping it would leave the case page saying nobody asserted the document
  // status, which is a different false claim rather than a safe default.
  if (!OPAQUE_SUBJECT.test(session.sub)) {
    return refuse(
      "identity_subject",
      "This account's identifier is not an opaque id, so it cannot be recorded " +
      "as the source of a document assertion.",
    );
  }

  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    return refuse("unknown_field", "Send a JSON object.");
  }
  const raw = body as Record<string, unknown>;

  for (const key of Object.keys(raw)) {
    if ((ALLOWED_KEYS as readonly string[]).includes(key)) continue;
    if (looksLikeIdentity(key)) {
      return refuse(
        "identity_field",
        `Grace does not store household identity, so ${key} cannot be submitted. ` +
        "A case is identified by its case id alone.",
      );
    }
    return refuse("unknown_field", `${key} is not a field on a case record.`);
  }

  const caseId = raw.case_id;
  if (typeof caseId !== "string" || !CASE_ID.test(caseId)) {
    return refuse("bad_case_id", "A case id looks like c-013 — the letter c, a dash, three digits.");
  }
  const program = raw.program;
  if (typeof program !== "string" || !(PROGRAMS as readonly string[]).includes(program)) {
    return refuse("unknown_program", `Grace holds rules for ${PROGRAMS.join(" and ")} only.`);
  }
  const state = raw.state;
  if (typeof state !== "string" || !(STATES as readonly string[]).includes(state)) {
    return refuse("unknown_state", `Grace holds rules for ${STATES.join(", ")} only.`);
  }
  // Absent is allowed and means English; a *wrong* value is not, because
  // silently drafting outreach in the wrong language is worse than refusing.
  const languageRaw = raw.language === undefined || raw.language === "" ? "en" : raw.language;
  if (typeof languageRaw !== "string" || !(LANGUAGES as readonly string[]).includes(languageRaw)) {
    return refuse("unknown_language", `Choose one of: ${LANGUAGES.join(", ")}.`);
  }
  const certEnd = isoDate(raw.cert_end);
  if (certEnd === null) {
    return refuse("bad_cert_end", "The certification end date must be a real date, as YYYY-MM-DD.");
  }
  const income = wholeNumberInRange(raw.monthly_income_cents, 0, MAX_INCOME_CENTS);
  if (income === null) {
    return refuse("bad_income", "Monthly income must be a whole number of cents, zero or more.");
  }
  const size = wholeNumberInRange(raw.size, 1, MAX_HOUSEHOLD_SIZE);
  if (size === null) {
    return refuse("bad_size", `Household size must be a whole number from 1 to ${MAX_HOUSEHOLD_SIZE}.`);
  }
  // Absent or null means "not reported this cycle" and must stay null — never
  // the on-file figure and never 0. A family whose income genuinely dropped to
  // zero is the most eligibility-relevant case Grace will see, so 0 has to stay
  // available as a real reported value (Plan 1 Task 2).
  let reportedIncome: number | null = null;
  if (raw.reported_income_cents !== undefined && raw.reported_income_cents !== null) {
    reportedIncome = wholeNumberInRange(raw.reported_income_cents, 0, MAX_INCOME_CENTS);
    if (reportedIncome === null) {
      return refuse("bad_reported_income", "Reported income must be a whole number of cents, or left blank.");
    }
  }
  let reportedSize: number | null = null;
  if (raw.reported_size !== undefined && raw.reported_size !== null) {
    reportedSize = wholeNumberInRange(raw.reported_size, 1, MAX_HOUSEHOLD_SIZE);
    if (reportedSize === null) {
      return refuse("bad_reported_size", "Reported household size must be a whole number, or left blank.");
    }
  }
  const docs = documents(raw.documents);
  if (docs === null) {
    return refuse(
      "bad_documents",
      `Each document needs a known id and a real received date, and there may be at most ${MAX_DOCUMENTS}.`,
    );
  }

  return {
    permitted: true,
    // The opaque Cognito `sub`. Never an email or a name — those claims are
    // logged to CloudTrail, outside every redaction Grace has.
    createdBy: session.sub,
    record: {
      caseId,
      program: program as Program,
      state: state as StateCode,
      certEnd,
      language: languageRaw as Language,
      monthlyIncomeCents: income,
      size,
      reportedIncomeCents: reportedIncome,
      reportedSize,
      documents: docs,
    },
  };
}

/** The record row, in exactly the shape `grace/cases/record.py` reads.
 *
 *  **Both sides assert against `fixtures/case-record-shape.json` rather than
 *  against each other.** Two writers in two languages agreeing by memory is how
 *  a submitted case ends up unparseable to the agent that has to read it — the
 *  case renders on the dashboard and Grace never sees it, which is the exact
 *  failure Plan 4 exists to close. `__tests__/intake.test.ts` compares this
 *  function's output to that file; `tests/test_case_record.py` compares the
 *  Python writer's to the same file.
 *
 *  `createdAt` and `createdBy` are parameters rather than a clock read and a
 *  reach into the permit, so the comparison can be byte for byte.
 *
 *  `createdBy` is a third parameter rather than a field on `CaseRecordInput`
 *  because it is **not a case fact**. Everything in `CaseRecordInput` is
 *  something the gate reasons over; this is provenance about the row — who
 *  asserted that these documents were sent. Keeping it out of the record input
 *  is the same separation as `grace/cases/record.py` reading it with its own
 *  function rather than putting it on `Case`, and for the same reason: an
 *  identifier inside the object a model receives is how `display_name` reached
 *  CloudWatch. */
export function toRecordItem(
  input: CaseRecordInput,
  createdAt: Date,
  createdBy: string,
): Record<string, AttributeValue> {
  return {
    pk: { S: `CASE#${input.caseId}` },
    sk: { S: RECORD_SK },
    case_id: { S: input.caseId },
    program: { S: input.program },
    state: { S: input.state },
    cert_end: { S: input.certEnd },
    language: { S: input.language },
    monthly_income_cents: { N: String(input.monthlyIncomeCents) },
    size: { N: String(input.size) },
    reported_income_cents:
      input.reportedIncomeCents === null
        ? { NULL: true }
        : { N: String(input.reportedIncomeCents) },
    reported_size:
      input.reportedSize === null ? { NULL: true } : { N: String(input.reportedSize) },
    documents: {
      L: input.documents.map(d => ({
        M: {
          id: { S: d.id },
          received: { S: d.received },
          expires: d.expires === null ? { NULL: true } : { S: d.expires },
        },
      })),
    },
    // No `source_conflicts` values are collectable, but the key is written as an
    // empty list rather than omitted so the item's attribute set is identical to
    // the Python writer's. A reader that required the key would otherwise parse
    // a seeded case and refuse a submitted one.
    source_conflicts: { L: [] },
    // `toISOString()` is UTC by definition. Plan 2 established that a non-UTC
    // offset sorts a later instant *before* an earlier one when DynamoDB
    // compares bytewise; this field is not a sort key, but two records written
    // at the same instant must still read back as the same time.
    created_at: { S: utcIsoWithOffset(createdAt) },
    // `NULL` when nobody asserted this record — the shape `infra/seed_cases.py`
    // writes for the twelve fixture households. The key is always present so the
    // attribute set matches the Python writer's exactly; a reader that required
    // it would otherwise parse a seeded case and refuse a submitted one.
    created_by: createdBy === "" ? { NULL: true } : { S: createdBy },
  };
}

/** One case's directory entry: the case id and nothing else.
 *
 *  It exists so the caseload can be read with a single Query rather than a Scan
 *  — neither the runtime role nor the dashboard's compute role holds
 *  `dynamodb:Scan`, deliberately, because a Scan bug could read the whole audit
 *  trail. A row that carried more would be a second copy of the record, able to
 *  drift from the first. */
export function toDirectoryItem(caseId: string): Record<string, AttributeValue> {
  return {
    pk: { S: CASE_DIRECTORY_PK },
    sk: { S: `CASE#${caseId}` },
    case_id: { S: caseId },
  };
}

/** `2026-09-06T12:00:00+00:00`, not `...Z`.
 *
 *  Python's `datetime.isoformat()` writes the offset form, and every timestamp
 *  already in this table has it. The two spellings are both valid ISO 8601 and
 *  both parse — but they do not *sort* alike, because `Z` (0x5A) is above `.`
 *  (0x2E), so a string comparison makes the older `Z` row beat the newer offset
 *  row. `lib/cases.ts` compares with `Date.parse` for exactly that reason;
 *  matching the existing spelling here means one fewer place the hazard exists. */
function utcIsoWithOffset(at: Date): string {
  return `${at.toISOString().replace(/(\.000)?Z$/, "")}+00:00`;
}
