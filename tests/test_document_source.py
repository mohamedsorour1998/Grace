"""The document-provenance seam, and the one failure it exists to prevent.

The failure: an unimplemented source that answers with an empty tuple instead of
raising. `evaluate` does not read `()` as "unknown" — it reads it as the positive
claim that this household has submitted nothing, and emits `missing_document` for
every document the rule pack requires. All twelve seeded households would
escalate, each for several reasons that are not true, and no error would appear
anywhere. `test_a_source_that_needs_no_backing_store_must_raise_rather_than_answer`
is the guard, and it discovers its subjects from the module rather than from a
list someone remembered to update — the same discipline as Plan 1 Task 4's
`pkgutil` walk for model ids, for the same reason.

The second thing tested here is vocabulary, which sounds cosmetic and is not.
`ASSERTED_PROVENANCE` is the sentence that lets a caseworker tell an assertion
from a verified fact, so it is asserted to *say* that and asserted not to use
custody words. Hard rule 6 is about never claiming an unconfirmed thing happened;
"documents on file" is that claim compressed into three words.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from grace.authority import _most_recent, evaluate
from grace.cases import document_source
from grace.cases.document_source import (
    ASSERTED_PROVENANCE,
    AssertedDocumentSource,
    DocumentSource,
    StateEligibilityDocumentSource,
)
from grace.cases.models import Case, Document, Household
from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.rules.pack import load_pack

TODAY = date(2026, 10, 1)

# Every fixture surname, plus the reserved phone range. Listing all twelve rather
# than a sample is Plan 3's finding: a draft guard named three of twelve and
# missed `Fitzgerald` and `Yamamoto`, the two households most likely to leak.
FIXTURE_NAMES = [
    "Rivera", "Okonkwo", "Nguyen", "Haddad", "Delacroix", "Torres",
    "Abebe", "Silva", "Kowalski", "Fitzgerald", "Yamamoto", "Mensah",
]


def _store() -> InMemoryCaseStore:
    return InMemoryCaseStore(load_fixture_cases())


def _source_classes() -> list[tuple[str, type]]:
    """Every concrete source class defined in the module, read off the module.

    Discovery from disk, not a hardcoded tuple. A stub added later — the exact
    thing this plan's seam invites — is covered because it is found, not because
    anyone remembered to add it here.
    """
    found = []
    for name, obj in vars(document_source).items():
        if not isinstance(obj, type) or obj.__module__ != document_source.__name__:
            continue
        if obj is DocumentSource:  # the Protocol itself is not an implementation
            continue
        found.append((name, obj))
    return found


# ---------------------------------------------------------------------------
# What ships: the caseworker's assertion, named as one
# ---------------------------------------------------------------------------


def test_the_asserted_source_returns_exactly_the_records_documents():
    """All twelve, not a sample, and compared as tuples rather than as counts.

    A source that returned the right *number* of documents with one `doc_id`
    swapped would move a household between `missing_document` and clean, which is
    the only kind of difference that matters here.
    """
    store = _store()
    source = AssertedDocumentSource(store)
    checked = 0
    for case in load_fixture_cases():
        assert source.documents_for(case.case_id) == case.documents, case.case_id
        checked += 1
    assert checked == 12


def test_the_asserted_source_and_the_gate_read_the_same_copies():
    """The property that makes this seam safe to introduce at all.

    `_most_recent` is shared between `evaluate` and `list_documents` precisely so
    the model cannot reason from a different copy of a document than the gate
    permits from (Plan 1 Task 4). A document *source* is a third reader, and the
    same hazard applies: if it selected differently, a caseworker's brief would
    describe one document while the gate decided on another, with no error.
    """
    store = _store()
    source = AssertedDocumentSource(store)
    checked = 0
    for case in load_fixture_cases():
        pack = load_pack(case.program, case.state)
        for required in pack.required_documents:
            from_source = _most_recent(source.documents_for(case.case_id), required.doc_id)
            from_case = _most_recent(case.documents, required.doc_id)
            assert from_source == from_case, f"{case.case_id}/{required.doc_id}"
            checked += 1
    # The loop is the assertion, so prove it ran (Plan 1 Task 8: a `for` that can
    # be skipped passes having asserted nothing).
    assert checked >= 12


def test_the_asserted_source_changes_no_verdict():
    """Introducing the seam must not move the 9-act/3-escalate split.

    Asserted through `evaluate` rather than by inspecting documents, because the
    claim worth making is about verdicts, and the per-case comparison above would
    still pass if the gate read documents from somewhere else entirely.
    """
    store = _store()
    source = AssertedDocumentSource(store)
    escalating = []
    for case in load_fixture_cases():
        pack = load_pack(case.program, case.state)
        # The same case, rebuilt with the documents the source supplies. Identical
        # input to `evaluate` if and only if the source is faithful.
        from dataclasses import replace

        via_source = replace(case, documents=source.documents_for(case.case_id))
        assert evaluate(via_source, TODAY, pack) == evaluate(case, TODAY, pack)
        if evaluate(via_source, TODAY, pack).escalated:
            escalating.append(case.case_id)
    assert sorted(escalating) == ["c-010", "c-011", "c-012"]


def test_an_unreadable_case_raises_rather_than_reading_as_empty():
    """`()` is not "unknown"; to the gate it is "this family sent nothing".

    So a case the store cannot answer for must raise. Returning an empty tuple
    would escalate the household on `missing_document` for every required
    document — a wrong answer that looks exactly like a correct one, which is the
    shape of failure this whole project is organised against.
    """
    source = AssertedDocumentSource(_store())
    with pytest.raises(KeyError):
        source.documents_for("c-999")


# ---------------------------------------------------------------------------
# Provenance: the sentence that distinguishes an assertion from a fact
# ---------------------------------------------------------------------------


def test_the_provenance_says_it_is_an_assertion_and_not_a_verified_fact():
    """Hard rule 6 at the point the claim enters, not where an outcome leaves.

    Both halves are asserted. "Non-empty" alone is satisfied by the string
    `documents on file`, which is precisely the wording this plan exists to
    remove — it implies Grace or the navigator holds the document, and neither
    does. The family sends documents to the **state**.
    """
    provenance = AssertedDocumentSource(_store()).provenance()
    assert provenance.strip()
    assert "assert" in provenance.lower()
    assert "cannot verify" in provenance.lower()


@pytest.mark.parametrize(
    "custody_word",
    ["on file", "we hold", "verified", "confirmed", "uploaded to grace", "stored"],
)
def test_the_provenance_uses_no_custody_or_verification_vocabulary(custody_word: str):
    """Each word here is one a reader would take as a different claim.

    "on file" and "stored" imply custody, which no party in this system has.
    "verified" and "confirmed" imply a check nobody performed. A caseworker
    deciding an escalation acts on this sentence, so the words are the feature.
    """
    assert custody_word not in ASSERTED_PROVENANCE.lower()


def test_the_provenance_carries_no_household_identity():
    """Hard rule 9. This string is rendered on a page and may reach a brief."""
    blob = json.dumps(
        [ASSERTED_PROVENANCE, AssertedDocumentSource(_store()).provenance()]
    )
    for name in FIXTURE_NAMES:
        assert name not in blob
    assert "+1555" not in blob


def test_the_identity_scan_above_can_actually_fail():
    """A scanner that matches nothing reports clean on every input.

    The companion that makes the guard mean something — the same pairing as
    `tests/test_case_record.py`'s `test_the_identity_guard_can_actually_fail`.
    """
    blob = json.dumps(["asserted by the Mensah Household at +15559990101"])
    assert any(name in blob for name in FIXTURE_NAMES)
    assert "+1555" in blob


# ---------------------------------------------------------------------------
# The stub, and why it raises
# ---------------------------------------------------------------------------


def test_the_state_source_raises_from_every_protocol_method():
    """THE test in this file.

    A stub that returned `()` would report every household as missing every
    document. A stub whose `provenance()` returned a cheerful string would let a
    caseworker read "verified against the state system" about a call that was
    never made. Both methods raise; neither returns.
    """
    source = StateEligibilityDocumentSource()
    with pytest.raises(NotImplementedError):
        source.documents_for("c-001")
    with pytest.raises(NotImplementedError):
        source.provenance()


def test_the_state_sources_refusal_says_what_it_would_have_claimed():
    """An operator who trips this needs to know why it is not simply returning
    nothing. The message names the consequence rather than saying "TODO"."""
    source = StateEligibilityDocumentSource()
    with pytest.raises(NotImplementedError, match="missing"):
        source.documents_for("c-001")


def test_a_source_that_needs_no_backing_store_must_raise_rather_than_answer():
    """Discovered from the module, so a stub added later is covered by default.

    The rule: a source constructible with no arguments has no backing store, so
    it cannot know anything about a household — it must raise. A source that
    requires a store is exercised by the tests above.
    """
    checked = 0
    for name, cls in _source_classes():
        try:
            instance = cls()
        except TypeError:
            # Needs a backing store; its answers are tested above.
            continue
        with pytest.raises(NotImplementedError):
            instance.documents_for("c-001")
        with pytest.raises(NotImplementedError):
            instance.provenance()
        checked += 1
    assert checked >= 1, "no argument-free source was found; the guard asserted nothing"


def test_every_source_in_the_module_satisfies_the_protocol():
    """`DocumentSource` is `runtime_checkable`, so this is a real check rather
    than a documented intention. A source missing `provenance()` would be a
    source that cannot say how it knows."""
    store = _store()
    checked = 0
    for name, cls in _source_classes():
        try:
            instance = cls()
        except TypeError:
            instance = cls(store)
        assert isinstance(instance, DocumentSource), name
        checked += 1
    assert checked == len(_source_classes())
    assert checked >= 2


def test_nothing_in_the_sweep_is_wired_to_a_source_yet():
    """The seam is named, not switched on — the plan's own constraint.

    Wiring it in is a behaviour change this plan does not make, and the dangerous
    half of that change is the state source: a `NotImplementedError` on the
    request path would fail every case rather than escalating it. Asserted by
    grepping the modules that build and run the graph, so importing this module
    from one of them fails here rather than on a sweep.
    """
    import pathlib

    root = pathlib.Path(document_source.__file__).parent.parent
    scanned = 0
    for path in sorted(root.rglob("*.py")):
        if path.name == "document_source.py" or "__pycache__" in path.parts:
            continue
        assert "document_source" not in path.read_text(), path
        scanned += 1
    assert scanned > 10


# ---------------------------------------------------------------------------
# A source is about status, never about the document itself
# ---------------------------------------------------------------------------


def test_a_document_carries_two_dates_and_nothing_that_could_identify_anyone():
    """The reason S3 was rejected, expressed as a structural fact.

    `Document` has three fields and none of them is a file, a URL, a name, or an
    employer. A source cannot leak what the type cannot hold — capability
    absence, the same argument as `read_case` no longer returning
    `display_name`.
    """
    fields = set(Document.__dataclass_fields__)
    assert fields == {"doc_id", "received", "expires"}
    case = Case(
        case_id="c-901",
        household=Household(
            household_id="h-901",
            display_name="The Testcase Household",
            language="en",
            phone="+15559990901",
            monthly_income_cents=100_000,
            size=2,
        ),
        program="medicaid",
        state="NY",
        cert_end=date(2026, 11, 30),
        documents=(Document(doc_id="proof_of_income", received=date(2026, 9, 20)),),
    )
    source = AssertedDocumentSource(InMemoryCaseStore([case]))
    blob = json.dumps(
        [
            {"id": d.doc_id, "received": d.received.isoformat()}
            for d in source.documents_for("c-901")
        ]
    )
    assert "Testcase" not in blob
    assert "+1555" not in blob
    assert "proof_of_income" in blob
