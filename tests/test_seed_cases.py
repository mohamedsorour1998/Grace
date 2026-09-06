"""The seeding script, driven against the same fake table the store uses.

Two claims, and they are separate on purpose: that a second run **writes
nothing**, and that a second run **changes nothing**. Plan 2 recorded why —
`update_continuous_backups` returned successfully while leaving point-in-time
recovery off, so "the call succeeded" and "the state is what I meant" are
different statements and only the second is worth asserting. Here the stakes are
higher: the live table holds the demo's entire evidence, and a seeding script
that could rewrite a `CASE#` partition is one bad flag away from destroying it.
"""

from __future__ import annotations

import pytest

from grace.cases.dynamo_store import DynamoDBCaseStore
from grace.cases.store import load_fixture_cases
from infra import naming, seed_cases
from tests.test_dynamo_store import FakeTable


def _store(client: FakeTable) -> DynamoDBCaseStore:
    return DynamoDBCaseStore([], table_name="grace-cases-test", client=client)


def test_a_first_run_writes_every_fixture_and_verifies():
    client = FakeTable()
    cases = load_fixture_cases()
    written, present = seed_cases.seed(_store(client), cases)
    assert written == [c.case_id for c in cases]
    assert present == []
    assert seed_cases.verify(_store(client), cases) == []


def test_a_second_run_writes_nothing_and_changes_nothing():
    """Idempotence, asserted as two separate facts.

    The `before`/`after` snapshot is the one that matters. A script that
    re-wrote every record with a fresh `created_at` would report "already
    present" from `create_case`'s conditional and still have rewritten nothing —
    but a script that dropped the condition would report the same counts while
    replacing twelve rows, and only the snapshot tells them apart.
    """
    client = FakeTable()
    cases = load_fixture_cases()
    seed_cases.seed(_store(client), cases)
    before = {key: dict(item) for key, item in client.items.items()}

    written, present = seed_cases.seed(_store(client), cases)
    assert written == []
    assert present == [c.case_id for c in cases]
    assert client.items == before


def test_seeding_never_touches_a_ledger_escalation_or_decision_row():
    """The live table's existing 1000+ rows are the demo's evidence. The only
    keys this script may create are the record and directory rows."""
    client = FakeTable()
    cases = load_fixture_cases()
    seed_cases.seed(_store(client), cases)
    for pk, sk in client.items:
        assert sk == naming.RECORD_SK or (
            pk == naming.CASE_DIRECTORY_PK and sk.startswith("CASE#")
        ), (pk, sk)
    assert len(client.items) == 24


def test_verify_fails_loudly_when_a_record_is_absent():
    """Verification that cannot fail proves nothing. This is the state the
    script exists to detect: a table someone believes was seeded and was not."""
    client = FakeTable()
    cases = load_fixture_cases()
    seed_cases.seed(_store(client), cases)
    del client.items[(naming.case_pk("c-007"), naming.RECORD_SK)]
    wrong = seed_cases.verify(_store(client), cases)
    assert len(wrong) == 1
    assert "c-007" in wrong[0]


def test_verify_fails_when_a_stored_record_disagrees_with_the_fixture():
    """A field silently different in the table is the failure that would move a
    verdict — a `cert_end` a month out puts the renewal window somewhere nobody
    chose."""
    client = FakeTable()
    cases = load_fixture_cases()
    seed_cases.seed(_store(client), cases)
    client.items[(naming.case_pk("c-003"), naming.RECORD_SK)]["cert_end"] = {
        "S": "2027-01-31"
    }
    wrong = seed_cases.verify(_store(client), cases)
    assert len(wrong) == 1
    assert "c-003" in wrong[0]


def test_verify_reads_the_table_and_not_the_in_memory_seed():
    """The store `verify` builds carries an **empty** fallback seed on purpose.

    `get()` falls back to the constructor list when the table holds nothing, so
    a verification routed through `get()` on a fixture-seeded store would pass
    against a table that was never written — the exact false success this
    script exists to rule out.
    """
    client = FakeTable()
    cases = load_fixture_cases()
    wrong = seed_cases.verify(_store(client), cases)
    assert len(wrong) == 12
    assert all("no record row" in line for line in wrong)


def test_the_default_store_carries_no_fixture_seed():
    store = seed_cases._build_store("grace-cases-test", client=FakeTable())
    assert store._cases == {}


def test_the_cli_reports_failure_with_a_non_zero_exit(monkeypatch, capsys):
    """`main` must return 1 on a verification failure, or a broken seed exits 0
    in CI and nobody looks again."""
    fake = FakeTable()
    monkeypatch.setattr(
        seed_cases, "_build_store", lambda table, client=None: _store(fake)
    )
    assert seed_cases.main(["--table", "grace-cases-test"]) == 0
    # The patched builder must genuinely have been used, or this test would pass
    # against a `main` that talked to real AWS and returned 0 for its own reasons.
    assert len(fake.items) == 24
    # Now break one record and re-verify only.
    fake.items[(naming.case_pk("c-005"), naming.RECORD_SK)]["size"] = {"N": "99"}
    assert seed_cases.main(["--verify", "--table", "grace-cases-test"]) == 1
    assert "VERIFY FAILED" in capsys.readouterr().err


def test_a_verify_only_run_writes_nothing():
    client = FakeTable()
    cases = load_fixture_cases()
    seed_cases.seed(_store(client), cases)
    before = {key: dict(item) for key, item in client.items.items()}
    assert seed_cases.verify(_store(client), cases) == []
    assert client.items == before


def test_the_comparison_ignores_exactly_the_identity_fields_and_nothing_else():
    """`_comparable` is a narrowing, not a coercion.

    If it dropped anything the gate reads, verification would pass on a record
    that decides differently — which is the one thing seeding must not be able
    to hide. Asserted by mutating each gate-relevant field in turn and watching
    the comparison notice.
    """
    case = load_fixture_cases()[0]
    narrowed = seed_cases._comparable(case)
    assert narrowed.household.display_name == ""
    assert narrowed.household.phone == ""
    assert narrowed.household.household_id == ""
    assert narrowed.household.language == case.household.language
    assert narrowed.household.monthly_income_cents == case.household.monthly_income_cents
    assert narrowed.household.size == case.household.size
    assert narrowed.program == case.program
    assert narrowed.state == case.state
    assert narrowed.cert_end == case.cert_end
    assert narrowed.documents == case.documents
    assert narrowed.reported_income_cents == case.reported_income_cents
    assert narrowed.reported_size == case.reported_size
    assert narrowed.source_conflicts == case.source_conflicts


@pytest.mark.parametrize(
    "attribute,replacement",
    [
        ("program", {"S": "snap"}),
        ("state", {"S": "CA"}),
        ("language", {"S": "xx"}),
        ("monthly_income_cents", {"N": "1"}),
        ("size", {"N": "99"}),
        ("reported_income_cents", {"N": "1"}),
        ("reported_size", {"N": "9"}),
    ],
)
def test_verify_notices_a_change_to_any_gate_relevant_field(attribute, replacement):
    """One parametrized case per field, because a comparison that only looked at
    `case_id` would pass every test above."""
    client = FakeTable()
    cases = load_fixture_cases()
    seed_cases.seed(_store(client), cases)
    client.items[(naming.case_pk("c-001"), naming.RECORD_SK)][attribute] = replacement
    wrong = seed_cases.verify(_store(client), cases)
    assert any("c-001" in line for line in wrong), attribute
