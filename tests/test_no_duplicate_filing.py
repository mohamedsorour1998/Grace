"""GRACE FILES A RENEWAL ONCE PER CERTIFICATION PERIOD.

The gate is pure and cannot read the ledger, so nothing asked whether this
renewal had already been filed. The daily sweep filed each clean household
again every day — twelve rows each on the live table, all for the same
certification period. Inert while `submit_renewal` writes a ledger row and
nothing else; a duplicate submission the moment a real endpoint is attached.

**The short-circuit still writes a row**, of kind `renewal_already_filed`.
Returning early without one would make Task 2's run-scoped `renewal_filed`
read day two as "nothing was filed" and escalate a clean household — so the
run-scoped property and the no-duplicate property would be in direct conflict.
Two kinds, both counted, keeps both: a run that reached no outcome writes
neither and still escalates.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.run import FILED_KINDS, renewal_filed
from grace.tools.action import TranscriptChannel, make_action_tools


def _submit(store: InMemoryCaseStore, case_id: str = "c-001"):
    tools = {t.tool_name: t for t in make_action_tools(store, case_id, TranscriptChannel())}
    # `DecoratedFunctionTool._tool_func` is the undecorated callable. Verified:
    # `original_function` does not exist on this SDK version, and `stream()`
    # would drag the whole tool-execution path into a test about ledger rows.
    return tools["submit_renewal"]._tool_func


def _kinds(store: InMemoryCaseStore, case_id: str = "c-001") -> list[str]:
    return [e.kind for e in store.ledger(case_id)]


def test_the_first_call_files():
    store = InMemoryCaseStore(load_fixture_cases())
    result = _submit(store)()
    assert "filed" in result.lower()
    assert _kinds(store).count("renewal_submitted") == 1


def test_a_second_call_for_the_same_period_does_not_file_again():
    store = InMemoryCaseStore(load_fixture_cases())
    submit = _submit(store)
    submit()
    result = submit()
    assert _kinds(store).count("renewal_submitted") == 1, "filed twice"
    assert _kinds(store).count("renewal_already_filed") == 1
    assert "already" in result.lower()


def test_the_short_circuit_still_writes_a_row_so_the_run_is_not_reported_empty():
    """The property that keeps this compatible with run-scoped classification.

    A second-day run that short-circuited *silently* would leave the run with
    no filing row at all, and `renewal_filed(since=run_started)` would escalate
    a clean household. Both kinds count.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    submit = _submit(store)
    submit()

    run_started = datetime.now(timezone.utc)
    # The day-one row must be *outside* this run's boundary before the real
    # assertion is worth anything. Without this line the test leans on the wall
    # clock: `renewal_filed` uses `>=`, so a clock coarse enough to stamp the
    # day-one row at `run_started` would satisfy the final assertion from that
    # row alone — and the test would pass with the short-circuit's `_log` line
    # deleted, which is the one change that reintroduces the conflict with
    # run-scoped classification. Prove the boundary excludes day one first, then
    # ask whether day two put something inside it.
    assert renewal_filed(store, "c-001", since=run_started) is False

    submit()
    assert renewal_filed(store, "c-001", since=run_started) is True


def test_a_run_that_calls_nothing_still_reports_no_filing():
    """The other half — the guard against this fix reintroducing blindness."""
    store = InMemoryCaseStore(load_fixture_cases())
    _submit(store)()
    run_started = datetime.now(timezone.utc) + timedelta(seconds=1)
    assert renewal_filed(store, "c-001", since=run_started) is False


def test_a_new_certification_period_files_again():
    """Dedup is keyed on the period, not on the household. A household whose
    certification has rolled over needs a new renewal, and refusing to file it
    would be the family-harming direction of this change."""
    from dataclasses import replace

    cases = load_fixture_cases()
    case = next(c for c in cases if c.case_id == "c-001")
    store = InMemoryCaseStore(cases)
    _submit(store)()

    # The next cycle: same household, a later certification end.
    rolled = replace(case, cert_end=date(2027, 10, 15))
    store_next = InMemoryCaseStore([rolled])
    for entry in store.ledger("c-001"):
        store_next.append_ledger(entry)
    _submit(store_next)()
    assert _kinds(store_next).count("renewal_submitted") == 2


def test_an_unreadable_ledger_files_rather_than_skipping():
    """**Fails toward filing, which is the opposite polarity to verification.**

    Everywhere Grace answers a *verification* question it fails closed, because
    an unverified case must reach a human. This is not that question. The gate
    has already cleared this case; the only thing in doubt is whether a
    duplicate exists. Declining to file on a failed read would mean a family's
    renewal silently never happens, while filing means a duplicate ledger row.
    """
    class _BlindStore(InMemoryCaseStore):
        def ledger(self, case_id: str):
            raise RuntimeError("ledger unavailable")

    store = _BlindStore(load_fixture_cases())
    written: list[str] = []
    store.append_ledger = lambda entry: written.append(entry.kind)  # type: ignore[method-assign]
    _submit(store)()
    assert written == ["renewal_submitted"]


def test_both_kinds_mean_a_renewal_is_on_file():
    assert FILED_KINDS == frozenset({"renewal_submitted", "renewal_already_filed"})
