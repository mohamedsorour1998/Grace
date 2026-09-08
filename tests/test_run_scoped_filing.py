"""A FILING IS A CLAIM ABOUT THIS RUN, NOT ABOUT HISTORY.

`renewal_filed` read the whole ledger, so a household filed once was reported
`acted` by every later run whatever happened in it. Live, each clean case
carried twelve `renewal_submitted` rows across twelve sweeps — the agent was
filing every run, and this check could not have told that from a gate that had
stopped working entirely. The demo's own invariant was therefore unfalsifiable.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from strands.multiagent.base import Status

from grace.cases.models import LedgerEntry
from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.entrypoint import process_case
from grace.run import renewal_filed

TODAY = date(2026, 10, 1)


def _store() -> InMemoryCaseStore:
    return InMemoryCaseStore(load_fixture_cases())


def _filing(case_id: str, at: datetime) -> LedgerEntry:
    return LedgerEntry(
        case_id=case_id, at=at, kind="renewal_submitted",
        detail={"program": "medicaid", "cert_end": "2026-11-30"},
    )


class _NeverFilesGraph:
    """A graph that completes without calling a single tool.

    This is the shape the check must be able to see: the run ends cleanly and
    nothing was filed. Before run-scoping, an old ledger row made it
    indistinguishable from a run that filed correctly.
    """

    class _Result:
        status = Status.COMPLETED
        interrupts = ()
        results = {}

    def __call__(self, *_args, **_kwargs):
        return self._Result()


def test_a_filing_from_an_earlier_run_does_not_count_as_this_one():
    store = _store()
    run_started = datetime.now(timezone.utc)
    store.append_ledger(_filing("c-001", run_started - timedelta(days=1)))
    assert renewal_filed(store, "c-001") is True, "the all-time reading still works"
    assert renewal_filed(store, "c-001", since=run_started) is False


def test_a_filing_from_this_run_counts():
    store = _store()
    run_started = datetime.now(timezone.utc)
    store.append_ledger(_filing("c-001", run_started + timedelta(seconds=1)))
    assert renewal_filed(store, "c-001", since=run_started) is True


def test_a_filing_at_exactly_the_run_start_counts():
    # `>=`, not `>`. A row written in the same microsecond the run began is
    # this run's; excluding it would report a real filing as absent.
    store = _store()
    run_started = datetime.now(timezone.utc)
    store.append_ledger(_filing("c-001", run_started))
    assert renewal_filed(store, "c-001", since=run_started) is True


def test_a_clean_case_that_files_nothing_escalates_despite_an_old_filing(monkeypatch):
    """The headline. A clean household with a filing from yesterday and a run
    that does nothing today must escalate, not report `acted`.

    Before run-scoping this returned `acted` with `filed: True`, and
    `web/lib/decide.ts` would have told a caseworker "the renewal was filed"
    about a run that filed nothing.
    """
    store = _store()
    store.append_ledger(_filing("c-001", datetime.now(timezone.utc) - timedelta(days=1)))
    monkeypatch.setattr(
        "grace.entrypoint.build_case_graph",
        lambda *args, **kwargs: _NeverFilesGraph(),
    )
    outcome = process_case({"case_id": "c-001", "today": TODAY.isoformat()}, store=store)
    assert outcome["status"] == "escalated", outcome
    assert "no renewal was filed" in outcome["reason"]
    assert outcome.get("filed") is None, "an escalated outcome must not carry filed"


def test_a_clean_case_that_does_file_reports_acted(monkeypatch):
    """The other half, so the test above cannot pass by escalating everything —
    the Task 8 lesson about removing the other branches' alibis."""
    store = _store()

    class _FilesOnce(_NeverFilesGraph):
        def __call__(self, *args, **kwargs):
            store.append_ledger(_filing("c-001", datetime.now(timezone.utc)))
            return super().__call__(*args, **kwargs)

    monkeypatch.setattr(
        "grace.entrypoint.build_case_graph", lambda *a, **k: _FilesOnce()
    )
    outcome = process_case({"case_id": "c-001", "today": TODAY.isoformat()}, store=store)
    assert outcome["status"] == "acted", outcome
    assert outcome["filed"] is True


def test_outreach_is_deliberately_not_run_scoped():
    """`outreach_sent` answers a different question and must stay all-time.

    Its purpose is to tell a caseworker the family has already been asked, so
    they do not ask again. Scoping it to one run would make Grace forget every
    message it had ever sent, and duplicate requests are exactly the confusion
    that makes families give up on paperwork.
    """
    import inspect

    from grace.run import outreach_sent

    assert "since" not in inspect.signature(outreach_sent).parameters
