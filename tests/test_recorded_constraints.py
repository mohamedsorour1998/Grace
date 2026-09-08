"""TWO TRUE STATEMENTS THAT NOTHING WAS HOLDING.

Both were properties a future change could reverse without any test noticing,
and each has a named consequence when it does.

**Only the first was genuinely unheld, and that was measured rather than
assumed.** The audit named two findings; before this file existed, the three
sabotages it proposed were run against the whole suite. Making `submit_renewal`
short-circuit on an existing ledger row passed all 886 tests. The other two —
reversing the record-over-seed precedence, and turning `open_cases()`'s
`raise` into a `continue` — were each already caught by a behavioural test in
`tests/test_dynamo_store.py`. So the second finding is pinned here at the layer
that was actually bare: not the precedence itself, which is held, but the
*seeding prerequisite* that the precedence makes load-bearing.

**The first statement has since been inverted rather than merely pinned.**
Grace now files a renewal once per certification period, so the constraint
recorded here asserts the fixed behaviour instead of the old one — the
substance is in `tests/test_no_duplicate_filing.py`, and this file keeps the
constraint where it was first written down so a change cannot quietly reverse
it in the place a reader would look for it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from grace.cases import record
from grace.cases.dynamo_store import CaseAlreadyExists, DynamoDBCaseStore
from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.tools.action import TranscriptChannel, make_action_tools
from infra.seed_cases import seed
from tests.test_dynamo_store import FakeTable

TODAY = date(2026, 10, 1)
TABLE = "grace-cases-test"

# An opaque Cognito `sub`, the shape `/new` records as `created_by`. Never a
# name or an email — `record.to_item` refuses those outright (hard rule 9).
SUBMITTER = "2448a4e8-c021-70f6-382c-e8acbb6cc956"


def test_submit_renewal_does_not_duplicate_a_filing_for_the_same_period():
    """Kept from the audit, inverted by the fix.

    This test used to assert that Grace filed the same renewal every day —
    twelve rows per household on the live table — and to explain why that was
    left alone. `tests/test_no_duplicate_filing.py` now covers the behaviour
    properly; this remains as the constraint that a future change must not
    quietly reverse, in the file where the constraint was first recorded.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    tools = {t.tool_name: t for t in make_action_tools(store, "c-001", TranscriptChannel())}
    submit = tools["submit_renewal"]._tool_func
    submit()
    submit()
    filings = [e for e in store.ledger("c-001") if e.kind == "renewal_submitted"]
    assert len(filings) == 1, "the same renewal was filed twice"


def test_seeding_is_what_stops_intake_claiming_a_fixture_household():
    """**Why `infra/seed_cases.py` is a deploy prerequisite, not a convenience.**

    `open_cases()` unions the constructor seed with the table's record rows and
    lets the table win, so a household submitted through `/new` is swept even
    though no image was rebuilt. That precedence is deliberate and is already
    held by `test_the_table_record_wins_over_the_constructor_seed`. What nothing
    held is its consequence: the *same* precedence means an intake submission
    naming a fixture id replaces that household's facts for the agent, and the
    only thing standing in the way is that seeding got there first.

    So this asserts the contrast in both directions, because the refusal is
    worth nothing unless it depends on seeding having run:

    - **unseeded** — `create_case("c-001")` is accepted and the sweep now reads
      the submitter's `cert_end`, not the fixture's. The dashboard shows nothing
      wrong; the gate is simply reasoning over someone else's facts.
    - **seeded** — the identical call is refused by `create_case`'s
      `attribute_not_exists(sk)`, and the household's facts are untouched.

    Run `python -m infra.seed_cases --verify` before exposing intake against any
    new table. The refusal is a property of the table's contents, not of the
    code, so it does not travel with a deploy.
    """
    fixtures = load_fixture_cases()
    on_file = next(c for c in fixtures if c.case_id == "c-001")
    assert on_file.cert_end == date(2026, 10, 15), "fixture drift; re-read the case"
    # Same id, different certification end: an intake submission that would put
    # this household three years out and silently move its renewal window.
    submitted = replace(on_file, cert_end=date(2030, 1, 1))

    unseeded = DynamoDBCaseStore(fixtures, table_name=TABLE, client=FakeTable())
    unseeded.create_case(submitted, created_by=SUBMITTER)
    swept = {c.case_id: c for c in unseeded.open_cases()}
    assert swept["c-001"].cert_end == date(2030, 1, 1), (
        "the exposure this test exists to describe did not reproduce: an "
        "unseeded fixture id must be claimable, or the seeded half proves nothing"
    )

    seeded = DynamoDBCaseStore(fixtures, table_name=TABLE, client=FakeTable())
    seed(seeded, fixtures)
    with pytest.raises(CaseAlreadyExists, match="c-001"):
        seeded.create_case(submitted, created_by=SUBMITTER)
    swept = {c.case_id: c for c in seeded.open_cases()}
    assert swept["c-001"].cert_end == date(2026, 10, 15), (
        "the record was overwritten despite the refusal; the ledger and "
        "escalation history in that partition now belong to two different "
        "households and nothing in the audit trail says so"
    )


def test_a_dangling_directory_row_raises_even_when_the_count_still_looks_right():
    """The other half of the same precedence, and the loud direction on purpose.

    `web/lib/create-case.ts` writes the directory row first and the record row
    second, so a partial write leaves an id the directory names and the table
    cannot load. Skipping it would drop a household from the sweep while every
    count still looked plausible.

    **Overlap stated honestly:** `test_a_directory_entry_with_no_record_row_
    raises_rather_than_vanishing` already catches a `continue` here, and it was
    watched doing so. It builds the store with an *empty* seed, though, where a
    skip is glaring — `open_cases()` returns nothing. This is the deployed
    shape: `build_store()` passes the twelve fixtures, so a skip would return
    exactly twelve cases, which is the number every other test, the dashboard
    headline, and the demo all expect. That is the configuration in which the
    failure is invisible, and it was the one nothing exercised.
    """
    fixtures = load_fixture_cases()
    assert len(fixtures) == 12, "a skip here would return exactly this many, and look right"

    store = DynamoDBCaseStore(fixtures, table_name=TABLE, client=FakeTable())
    # The half-written case `create_case`'s directory-first ordering produces.
    store._client.put_item(TableName=TABLE, Item=record.directory_item("c-013"))

    with pytest.raises(record.InvalidCaseRecord, match="c-013"):
        store.open_cases()
