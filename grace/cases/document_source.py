"""Where the claim "this household has a current proof of income" comes from.

**The domain fact this module is built around.** The family sends documents to
the **state**, and the state's eligibility system is the system of record. The
sequence is: the state attempts an *ex parte* renewal from wage and tax data it
already holds; if that fails it mails a renewal form; if it still cannot verify
something it requests specific documents; the family submits them to the state
by portal, mail, or in person; a state worker decides. Grace is none of those
parties. Grace's users are **navigators** — community clinics, food banks,
school family-support offices — whose real knowledge is *"I helped this family
upload their paystub on the 20th"*. That is **status, not custody**.

So Grace's document model is not wrong; it was under-described. A `Document`
records which kind of proof it is and when it moved, and the gate reasons about
the clock on it (`grace/authority.py`'s `document_problems`). Nothing in this
system has ever opened a document, and nothing ever will.

**Why an interface rather than a sentence in a README.** A seam that exists as
prose is a promise. A seam that exists as a Protocol with two implementations is
a design: the question "how do you know this document exists?" now has a typed
answer per source, and a caseworker reading an escalation can tell an
*assertion* apart from a *verified fact*. That distinction is hard rule 6 at the
point where the claim enters the system rather than at the point where Grace
reports an outcome.

**Storing the document itself was considered and rejected.** A proof of income
carries a name, an address, an employer, and often a social security number —
the most sensitive possible payload, in a system whose entire architecture is
"no household identity anywhere" (hard rule 9, which exists because a single
`display_name` field leaked a surname to CloudWatch). It also buys the gate
nothing: `document_problems` reads two dates. Maximum PII exposure for zero gate
value.

**Nothing here is wired into the sweep, and that is deliberate.**
`grace/cases/store.py` and `grace/cases/dynamo_store.py` still hand `Case`
objects to the graph exactly as before. This module names the seam and gives the
shipped behaviour an honest label; it does not change a verdict. `authority.py`
is untouched.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from grace.cases.models import Document
from grace.cases.store import CaseStore

# The words that go on screen and into a caseworker's brief. A module constant
# rather than a literal at each call site, so the claim Grace makes about its own
# knowledge is written once — the same reason `_most_recent` is shared between
# the gate and the read tool instead of being reimplemented.
#
# Note what it does NOT say: not "on file", not "held", not "verified". Every one
# of those implies custody or a check that never happened.
ASSERTED_PROVENANCE = (
    "asserted by a caseworker at intake — Grace tracks the deadline on it and "
    "cannot verify it independently"
)


@runtime_checkable
class DocumentSource(Protocol):
    """Where a household's document status comes from, and how it is known.

    `provenance()` is the point of the interface. Two sources can return the
    identical tuple of `Document`s and mean entirely different things by it —
    one a navigator's tick-box, the other a state eligibility system's answer —
    and a caseworker deciding an escalation needs to know which. A source that
    cannot say how it knows has no business supplying facts to a gate.

    `runtime_checkable` for the same reason `CaseStore` is: a Protocol that
    nothing can assert against only documents an intention.
    """

    def documents_for(self, case_id: str) -> tuple[Document, ...]:
        """Every document this source knows about for one household."""
        ...

    def provenance(self) -> str:
        """How this source knows. Non-empty, and never a household identifier."""
        ...


class AssertedDocumentSource:
    """What ships today: the documents already on the case record.

    This changes no behaviour. It **names** the behaviour that exists — a
    caseworker ticked a box at intake, or a fixture said so, and Grace has taken
    that as the state of the world ever since. Before this class the claim was
    indistinguishable from a verified one at every surface that rendered it.

    A read failure propagates. `CaseStore.get` raises `KeyError` for a case it
    does not hold, and returning `()` instead would be the single worst
    behaviour available here: an empty document tuple makes `evaluate` report
    `missing_document` for every required document, so an unreadable case would
    look exactly like a household that has submitted nothing. Escalating for a
    reason that is not true is still a wrong answer.
    """

    def __init__(self, store: CaseStore) -> None:
        self._store = store

    def documents_for(self, case_id: str) -> tuple[Document, ...]:
        return self._store.get(case_id).documents

    def provenance(self) -> str:
        return ASSERTED_PROVENANCE


class StateEligibilityDocumentSource:
    """The integration that would make the claim a verified one. Not built.

    **It raises, and it must keep raising.** The tempting shape for an
    unimplemented source is one that returns `()` so callers "work" — and that
    is the bug this class exists to prevent. An empty tuple is not "I don't
    know"; to `evaluate` it is the positive claim *this household has submitted
    no documents at all*, which turns every required document into a
    `missing_document` reason. Twelve clean households would escalate for twelve
    reasons that are not true, and nothing anywhere would report an error. A
    `NotImplementedError` on the first call is loud, immediate, and cannot be
    mistaken for data.

    **What a real implementation needs**, none of which is code:

    1. A **per-state data-sharing agreement** with the agency that runs the
       eligibility system. This is a legal instrument, negotiated per state, and
       it is the actual blocker — not the HTTP call.
    2. **Credentials** issued under that agreement, and a place to hold them
       that is not this repository.
    3. A **query** the agency's system actually answers: *does this household
       have a current verification of X on file, and as of when?* Note the shape
       — a status and a date, not a document. Grace must not receive the file
       even where it is entitled to ask about it, or every argument in this
       module's docstring stops holding.

    This is what **AgentCore Gateway** was deferred for: an outbound call to a
    system Grace does not own. The deferral reason recorded in the README —
    outbound auth differs per target type, and that is the most common deploy-day
    failure — is exactly why this stays a stub rather than a half-built client.
    """

    def documents_for(self, case_id: str) -> tuple[Document, ...]:
        raise NotImplementedError(
            "No state eligibility integration exists. Answering this from an "
            "empty tuple would report every required document as missing for "
            f"{case_id!r}; see this class's docstring for what a real "
            "implementation needs."
        )

    def provenance(self) -> str:
        raise NotImplementedError(
            "No state eligibility integration exists, so there is no provenance "
            "to state. A source that cannot say how it knows must not supply "
            "facts to the gate."
        )
