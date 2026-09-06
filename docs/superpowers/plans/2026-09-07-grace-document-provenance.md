# Plan 5 — document provenance: say what Grace actually knows

Grace's weakest claim is `DOCUMENTS ON FILE`. A caseworker ticks a box, Grace treats it as fact, and
nothing anywhere records that it was an assertion rather than a verified thing. This plan does not add
document storage. It makes the existing claim **accurate**, and turns the gap into a named seam.

Written 2026-09-07. Deadline **2026-09-14 17:00 PT** — 7 days. Scope is deliberately half a day of
work; every task is independently shippable and none touches `grace/authority.py`.

---

## The domain finding this rests on

Established before writing, because the technical options only make sense against it:

**The family sends documents to the state.** The state's eligibility system is the system of record.
The sequence is: the state attempts an *ex parte* renewal from wage and tax data it already holds; if
that fails it mails a renewal form; if it still cannot verify something it requests specific documents;
the family submits them to the **state** by portal, mail, or in person; a state worker decides.

**The 69% procedural loss happens between the notice and the submission** — the letter never arrives,
or the document never gets sent.

**Grace's users are navigators**, not the state: community clinics, food banks, school family-support
offices. A navigator's real knowledge is *"I helped this family upload their paystub on the 20th"* —
**status, not custody**. So the current model is not wrong; it is under-described. The field says "on
file", which sounds like Grace or the org holds the document. Neither does.

## Two options considered and rejected, with reasons

**S3 upload (with or without Textract).** A proof of income carries a name, an address, an employer,
and often an SSN. Storing it puts the most sensitive possible payload into a system whose entire
architecture is "no household identity anywhere" — the rule that exists because a single
`display_name` field leaked a surname to CloudWatch. It also buys nothing the gate can use:
`document_problems` reads `received` and `expires` and never opens a document. **Rejected: maximum PII
exposure for zero gate value.**

**Modelling ex parte renewal.** Tempting, because it is the lever that actually prevents the 69%. But
ex parte is something the *state* does from data Grace does not have and cannot get. Building it would
mean Grace simulating a capability it does not possess, which is the opposite of everything else in
this project. **Rejected: it would be a claim Grace cannot back.**

---

## Task 1 — say what is actually known

**Files:** `web/components/intake-form.tsx`, `web/app/new/page.tsx`, `web/components/case-table.tsx`,
`README.md`, `docs/demo-video-handout.md`, `docs/builder-blog-post.md`

- [x] **Step 1: Rename the concept, everywhere it appears**

`Documents on file` becomes something that names the real fact. Preferred:
**`Documents sent to the state`**, with each entry reading *"sent 20 Sep"* rather than *"received"*.
`received` stays the field name in `models.py` — renaming a Plan 1 dataclass field would ripple through
`authority.py`, which this plan does not touch. The change is vocabulary at the surface, not in the gate.

- [x] **Step 2: Say who asserted it and when**

The form's helper text becomes explicit: ticking a box records *a caseworker's assertion* that the
family sent this document. Grace tracks the clock on it and cannot verify it independently.

- [x] **Step 3: Carry the same wording into the four documents**

`README.md` already has a "Grace stores no documents" section from the last change — extend it with the
navigator/state distinction. The video handout and the article need the same, because "documents on
file" spoken aloud in a demo implies custody.

---

## Task 2 — make the integration point real code

A seam that exists as a sentence is a promise. A seam that exists as an interface with two
implementations is a design.

**Files:** `grace/cases/document_source.py` (new), `tests/test_document_source.py` (new)

- [x] **Step 1: Define `DocumentSource`**

```python
class DocumentSource(Protocol):
    """Where the claim 'this household has a current proof of income' comes from."""

    def documents_for(self, case_id: str) -> tuple[Document, ...]: ...
    def provenance(self) -> str: ...
```

`provenance()` is the point of the interface: every source must be able to say **how it knows**, so a
caseworker reading an escalation can tell an assertion from a verified fact.

- [x] **Step 2: `AssertedDocumentSource` — what ships**

Reads the documents already on the case record. `provenance()` returns something like
`"asserted by a caseworker at intake"`. This changes no behaviour; it names the behaviour that exists.

- [x] **Step 3: `StateEligibilityDocumentSource` — the stub, and why it is a stub**

Raises `NotImplementedError` with a docstring stating exactly what a real implementation needs: a
per-state data-sharing agreement, credentials, and a query for whether a household has a current
verification on file. **It must not be wired in.** A stub that silently returns an empty tuple would
make every household look like it is missing every document.

This is what AgentCore **Gateway** was deferred for — an outbound call to a system Grace does not own —
and the deferral reason (outbound auth differs per target type) is exactly why this stays a stub.

- [x] **Step 4: Tests, including one that pins the stub's refusal**

Assert `AssertedDocumentSource` returns the record's documents and a non-empty provenance string, and
that the state source **raises** rather than returning empty. Sabotage each and watch it fail.

---

## Task 3 — show the provenance where the decision is read

**Files:** `web/lib/intake.ts`, `web/lib/create-case.ts`, `grace/cases/record.py`,
`web/app/case/[id]/page.tsx`

- [x] **Step 1: Persist who asserted, on the record**

The intake permit already carries `createdBy` (the opaque Cognito `sub`). Write it onto the `RECORD#v1`
row along with the timestamp. **No name, no email** — the same identity discipline as a decision row.

- [x] **Step 2: Surface it on the case page**

One line near the documents: *"Document status asserted by <opaque id> at intake on <date>. Grace tracks
the deadline on it and does not verify it independently."*

That sentence is the whole point of the plan. A caseworker deciding an escalation can see the basis of
the claim they are acting on.

- [x] **Step 3: Gates, sabotage, commit**

Five gates. Every new guard sabotaged and watched failing. `grace/authority.py` untouched — assert that
with a diff, not by memory.

---

## Risks

| Risk | Mitigation |
|---|---|
| Renaming ripples into the gate | Vocabulary changes at the surface only; `Document.received` keeps its name. Verify `grace/authority.py` has a zero-line diff |
| The stub gets wired in and returns empty | It raises `NotImplementedError`, and a test pins that it raises. Never return an empty tuple from an unimplemented source |
| The demo now sounds weaker | It sounds *accurate*. The article is graded on understanding the competitive landscape, and "we track status because the state holds the document" is domain knowledge, not a shortfall |
| Scope creep back toward S3 | Out of scope below, with the reason |

## Out of scope, and why

**Storing documents.** Rejected above: maximum PII exposure, zero gate value.

**Submitting documents or renewals to a state system.** Grace does not do this today — `submit_renewal`
writes a ledger row and there is no state integration behind it. Adding one needs a data-sharing
agreement, not code. Worth stating plainly in the README rather than leaving a reader to assume
otherwise.

**Ex parte modelling.** Rejected above.
