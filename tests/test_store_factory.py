"""Which store this process uses, decided in one place.

Without a factory the `GRACE_STORE` branch ends up duplicated in the entrypoint
and anywhere else that needs a store, and the two copies disagree about the
default. The default must be in-memory: the fast suite is offline, and a default
of "dynamo" would make hundreds of passing tests suddenly require AWS.
"""

from __future__ import annotations

import pytest

from grace.cases.dynamo_store import DynamoDBCaseStore
from grace.cases.store import CaseStore, InMemoryCaseStore, load_fixture_cases
from grace.store_factory import build_store


def test_the_default_is_in_memory(monkeypatch):
    """No env var means offline. The fast suite depends on this."""
    monkeypatch.delenv("GRACE_STORE", raising=False)
    assert isinstance(build_store(), InMemoryCaseStore)


def test_dynamo_is_selected_explicitly(monkeypatch):
    monkeypatch.setenv("GRACE_STORE", "dynamodb")
    store = build_store()
    assert isinstance(store, DynamoDBCaseStore)


def test_an_unrecognized_value_raises_rather_than_defaulting(monkeypatch):
    """A typo'd `GRACE_STORE=dynamo` must not silently fall back to
    in-memory in the deployed runtime — the ledger would look empty to the
    dashboard while the sweep reported success, and nothing would say why."""
    monkeypatch.setenv("GRACE_STORE", "dynamo")
    with pytest.raises(ValueError, match="GRACE_STORE"):
        build_store()


@pytest.mark.parametrize("value", ["", "  ", "none", "dynamodb-local", "memory:", "DYNAMO"])
def test_no_unrecognized_spelling_silently_selects_a_store(monkeypatch, value):
    """The refusal is a whitelist, not a check for the one typo above.

    An empty string is the interesting case: `os.getenv(name, default)` returns
    the default only when the variable is *absent*, so `GRACE_STORE=` set to
    empty bypasses the default entirely. Failing loudly is right for the same
    reason `APPROVE_DECISIONS` is an allowlist (Task 6) — the unrecognized
    value must be the safe-to-refuse one, never the one that proceeds.
    """
    monkeypatch.setenv("GRACE_STORE", value)
    with pytest.raises(ValueError, match="GRACE_STORE"):
        build_store()


@pytest.mark.parametrize("value", ["memory", "MEMORY", " memory ", "Dynamodb", " dynamodb"])
def test_recognized_values_are_case_and_whitespace_tolerant(monkeypatch, value):
    """A trailing space from a copy-pasted env file must not take down the
    runtime, and the two accepted spellings are the only tolerance offered."""
    monkeypatch.setenv("GRACE_STORE", value)
    assert isinstance(build_store(), CaseStore)


def test_whatever_it_returns_satisfies_the_protocol(monkeypatch):
    monkeypatch.delenv("GRACE_STORE", raising=False)
    assert isinstance(build_store(), CaseStore)


def test_the_dynamo_store_also_satisfies_the_protocol(monkeypatch):
    """Asserted on the DynamoDB branch too, not just the default. The factory's
    whole purpose is that callers hold a `CaseStore` and never ask which one,
    so both branches must actually be one."""
    monkeypatch.setenv("GRACE_STORE", "dynamodb")
    assert isinstance(build_store(), CaseStore)


def test_explicit_cases_are_used_rather_than_the_fixtures(monkeypatch):
    """The `cases` argument exists so the entrypoint can pass a caseload it
    already loaded rather than re-reading the fixture file per invocation."""
    monkeypatch.delenv("GRACE_STORE", raising=False)
    one = load_fixture_cases()[:1]
    store = build_store(one)
    assert [c.case_id for c in store.open_cases()] == [one[0].case_id]


def test_an_empty_case_list_is_honoured_rather_than_replaced(monkeypatch):
    """`cases=[]` must not be mistaken for "no argument given" and silently
    reload twelve fixtures. `if cases is not None` rather than `if cases` —
    the same distinction Plan 1's Task 2 established for a reported income of
    `0`, where a legitimate falsy value cannot double as an absence marker."""
    monkeypatch.delenv("GRACE_STORE", raising=False)
    assert build_store([]).open_cases() == []


def test_the_dynamo_branch_does_not_reach_aws_at_construction(monkeypatch):
    """Building the store must not perform a describe/read call.

    The fast suite is offline and this test runs in it, so a constructor that
    validated the table's existence over the network would make the whole suite
    require credentials — exactly what the in-memory default protects against.
    Asserted by giving botocore an endpoint that cannot resolve: construction
    still succeeds, because `boto3.client` does no I/O.
    """
    monkeypatch.setenv("GRACE_STORE", "dynamodb")
    monkeypatch.setenv("AWS_ENDPOINT_URL_DYNAMODB", "http://127.0.0.1:1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    store = build_store()
    assert isinstance(store, DynamoDBCaseStore)
