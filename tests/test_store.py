import re
from datetime import date, datetime, timedelta, timezone

import pytest

from grace.cases.models import Case, Document, Household, LedgerEntry
from grace.cases.store import CaseStore, InMemoryCaseStore, InvalidFixtureData, load_fixture_cases
from grace.rules.clock import renewal_window, window_status
from grace.rules.pack import load_pack


def _case(case_id: str = "c-1") -> Case:
    return Case(
        case_id=case_id,
        household=Household(
            household_id="h-1",
            display_name="The Rivera Household",
            language="es",
            phone="+15550000001",
            monthly_income_cents=210_000,
            size=3,
        ),
        program="medicaid",
        state="NY",
        cert_end=date(2026, 12, 31),
        documents=(Document(doc_id="proof_of_income", received=date(2026, 11, 5), expires=None),),
        reported_income_cents=210_000,
        reported_size=3,
        source_conflicts=(),
    )


def test_get_returns_the_case():
    store = InMemoryCaseStore([_case()])
    assert store.get("c-1").household.display_name == "The Rivera Household"


def test_get_unknown_case_id_raises():
    """A missing case must fail loudly, not flow a None into Task 3's evaluate()."""
    store = InMemoryCaseStore([_case("c-1")])
    with pytest.raises(KeyError):
        store.get("c-2")


def test_open_cases_lists_all():
    store = InMemoryCaseStore([_case("c-1"), _case("c-2")])
    assert {c.case_id for c in store.open_cases()} == {"c-1", "c-2"}


def test_ledger_appends_in_order():
    store = InMemoryCaseStore([_case()])
    for kind in ("intake", "documents", "decided"):
        store.append_ledger(
            LedgerEntry(case_id="c-1", at=datetime.now(timezone.utc), kind=kind, detail={})
        )
    assert [e.kind for e in store.ledger("c-1")] == ["intake", "documents", "decided"]


def test_ledger_is_isolated_per_case():
    store = InMemoryCaseStore([_case("c-1"), _case("c-2")])
    store.append_ledger(
        LedgerEntry(case_id="c-1", at=datetime.now(timezone.utc), kind="intake", detail={})
    )
    assert store.ledger("c-2") == []


def test_ledger_for_unknown_case_is_rejected():
    """A typo'd case_id must fail at append, not open a phantom ledger bucket
    that ledger() would otherwise report as an innocent empty list."""
    store = InMemoryCaseStore([_case("c-1")])
    with pytest.raises(KeyError):
        store.append_ledger(
            LedgerEntry(case_id="c-DOES-NOT-EXIST", at=datetime.now(timezone.utc), kind="x", detail={})
        )


def test_ledger_snapshot_cannot_be_used_to_rewrite_the_audit_trail():
    """The list returned by ledger() must be a real snapshot: mutating it, or
    the LedgerEntry.detail it holds, must never change what the store recorded.

    LedgerEntry.detail is a MappingProxyType (models.py), so this exercises
    both list-copy and entry-level immutability.
    """
    store = InMemoryCaseStore([_case("c-1")])
    store.append_ledger(
        LedgerEntry(
            case_id="c-1",
            at=datetime.now(timezone.utc),
            kind="decided",
            detail={"gate": "escalate", "reason": "missing_document"},
        )
    )
    snapshot = store.ledger("c-1")
    snapshot.append(
        LedgerEntry(case_id="c-1", at=datetime.now(timezone.utc), kind="forged", detail={})
    )
    with pytest.raises(TypeError):
        snapshot[0].detail["gate"] = "act"  # type: ignore[index]

    assert [e.kind for e in store.ledger("c-1")] == ["decided"]
    assert store.ledger("c-1")[0].detail == {"gate": "escalate", "reason": "missing_document"}


def test_duplicate_case_ids_are_rejected():
    """A duplicated id would silently shrink the caseload.

    The store keys cases by id, so a copy-paste duplicate in the fixture file
    would drop one household without any error — and the demo's 9-act/3-escalate
    split would quietly become 8/3.
    """
    with pytest.raises(ValueError, match="duplicate case ids"):
        InMemoryCaseStore([_case("c-1"), _case("c-1")])


def test_case_store_protocol_is_actually_satisfied():
    """CaseStore is @runtime_checkable so Plan 2's DynamoDB store can be
    verified against the same Protocol, not just documented as an intent."""
    store = InMemoryCaseStore([_case()])
    assert isinstance(store, CaseStore)


# --- fixture data guards -----------------------------------------------

_NANP_PHONE = re.compile(r"^\+1555\d{7}$")
_HOUSEHOLD_NAME = re.compile(r"^The [A-Za-z ]+ Household$")


def _every_string_field(case: Case):
    """Every free-text string reachable from a Case — the surface a real PII
    leak would actually use, not just the two fields a narrow guard checks."""
    yield case.case_id
    yield case.program
    yield case.state
    yield case.household.household_id
    yield case.household.display_name
    yield case.household.language
    yield case.household.phone
    for doc in case.documents:
        yield doc.doc_id
    yield from case.source_conflicts


def test_fixtures_load_and_are_obviously_synthetic():
    """Every string field of every fixture case must be recognisably fictional.

    A prior version of this guard checked only `"Household" in display_name`
    (case-sensitive `in`, so "Householder" or "household" both pass/fail
    incorrectly) and `phone.startswith("+1555")` (which also passes
    `+15550`..`+1555999999999`, not just the twelve real fixture numbers).
    Exact-pattern matching over every string field closes both gaps.
    """
    cases = load_fixture_cases()
    assert len(cases) >= 10
    for case in cases:
        assert _NANP_PHONE.fullmatch(case.household.phone), case.household.phone
        assert _HOUSEHOLD_NAME.fullmatch(case.household.display_name), case.household.display_name
        for value in _every_string_field(case):
            # No street-address or SSN-shaped text anywhere, including inside
            # free-text source_conflicts — the field most likely to have a
            # real caseworker note pasted into it.
            assert not re.search(r"\d{3}-\d{2}-\d{4}", value), value
            assert not re.search(r"\d{3}-\d{3}-\d{4}", value), value


def test_fixtures_encode_the_demo_split():
    """The fixture set is the demo: 12 households, 9 clean, 3 that must escalate.

    Asserted here on the *data* rather than on gate behaviour, so an edit that
    quietly removes an escalation trigger fails in Task 2 rather than looking
    like a Task 3 gate regression.
    """
    cases = {c.case_id: c for c in load_fixture_cases()}
    assert len(cases) == 12

    missing_doc = cases["c-010"]
    assert {d.doc_id for d in missing_doc.documents} == {"proof_of_income"}

    income_change = cases["c-011"]
    assert income_change.reported_income_cents is not None
    assert income_change.reported_income_cents != income_change.household.monthly_income_cents

    assert cases["c-012"].source_conflicts

    # The other nine must not trip any of those three triggers, or the gate test
    # for the intended reason becomes ambiguous.
    for case_id, case in cases.items():
        if case_id in {"c-010", "c-011", "c-012"}:
            continue
        assert not case.source_conflicts, case_id
        assert case.reported_income_cents is None, case_id
        assert case.reported_size is None, case_id


def test_fixtures_are_consistent_with_the_rule_packs():
    """Every clean case must sit in an actionable window with every required
    document present and fresh — checked against the real YAML packs, not
    hardcoded thresholds, so an edit to either side is caught here.

    Without this, mutating a pack's max_age_days, moving a cert_end into
    `not_open`/`closed`, or dropping a document from a clean case all pass the
    rest of the suite silently: the 9-act/3-escalate split changes with no
    test failure anywhere.
    """
    escalators = {"c-010", "c-011", "c-012"}
    today = date(2026, 10, 1)
    for case in load_fixture_cases():
        if case.case_id in escalators:
            continue
        pack = load_pack(case.program, case.state)
        window = renewal_window(case.cert_end, pack)
        status = window_status(today, window)
        assert status in ("open", "overdue", "in_grace"), (case.case_id, status)

        on_file = {d.doc_id: d for d in case.documents}
        for required in pack.required_documents:
            doc = on_file.get(required.doc_id)
            assert doc is not None, (case.case_id, "missing", required.doc_id)
            age = (today - doc.received).days
            assert age <= required.max_age_days, (case.case_id, required.doc_id, age)
