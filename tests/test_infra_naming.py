"""Resource names are asserted, not eyeballed.

Seven provisioning scripts and a runbook otherwise each hardcode `grace-cases`,
and one typo becomes a resource nobody notices is orphaned. These tests are
cheap and they make a rename a one-file change.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from infra import naming


def test_every_resource_name_is_grace_prefixed():
    """So `list-*` output can be filtered, and teardown cannot match a
    resource belonging to another project in this shared account."""
    names = [
        naming.TABLE, naming.RUNTIME, naming.MEMORY, naming.LAMBDA,
        naming.STATE_MACHINE, naming.SCHEDULE_RULE, naming.ALARM,
    ]
    for name in names:
        assert name.startswith("grace"), name


def test_the_ledger_sort_key_sorts_lexically_in_time_order():
    """DynamoDB sorts the SK as a string, so ISO-8601 plus a zero-padded
    sequence is what makes `ScanIndexForward=True` mean chronological.
    An unpadded sequence sorts 10 before 9."""
    at = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    keys = [naming.ledger_sk(at, n) for n in (1, 2, 9, 10, 11)]
    assert keys == sorted(keys)


def test_two_entries_in_the_same_microsecond_get_different_sort_keys():
    """The collision this design exists to prevent. `LedgerEntry.at` is
    `datetime.now(timezone.utc)`, and one tool call writes `tool_call` and
    `tool_result` back to back; two rows sharing a timestamp would collide
    on the sort key and one would silently overwrite the other, losing an
    audit row."""
    at = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert naming.ledger_sk(at, 1) != naming.ledger_sk(at, 2)


def test_a_naive_timestamp_is_refused():
    """`LedgerEntry` already rejects a naive datetime at construction; the
    key builder must not be the place a naive one sneaks back in and sorts
    inconsistently against aware ones."""
    with pytest.raises(ValueError):
        naming.ledger_sk(datetime(2026, 10, 1, 12, 0, 0), 1)


def test_the_case_partition_key_is_opaque():
    """Hard rule 9's reasoning applied to storage: the key carries the case
    id, never a household name, phone, or address."""
    assert naming.case_pk("c-011") == "CASE#c-011"


# ---------------------------------------------------------------------------
# The offset trap: aware is not the same claim as UTC
# ---------------------------------------------------------------------------
#
# `LedgerEntry.__post_init__` rejects a *naive* datetime and nothing more, so an
# aware datetime at any offset reaches the key builders. Two rows an hour apart
# in real time sort backwards if their offsets differ, and the sort key is what
# `ScanIndexForward=True` orders by — so the ordering the evals read would be
# wrong with no error anywhere. Both key builders normalize to UTC first; these
# tests are what stop that normalization being dropped as redundant.


def test_a_non_utc_offset_still_sorts_chronologically():
    """The defect these keys must not have.

    An aware datetime at a non-UTC offset is accepted by `LedgerEntry`, and
    `isoformat()` renders its offset verbatim: 08:00-05:00 is one hour *after*
    12:00+00:00, but the raw string `...T08:00:00-05:00` sorts *before*
    `...T12:00:00+00:00`. DynamoDB compares the sort key bytewise and cannot
    know about offsets, so the fix has to be in the key.
    """
    earlier = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    later = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert later > earlier, "premise: the -05:00 timestamp is the later instant"

    assert naming.ledger_sk(earlier, 1) < naming.ledger_sk(later, 2)
    assert naming.escalation_sk(earlier) < naming.escalation_sk(later)


def test_the_same_instant_at_two_offsets_gets_one_key():
    """A single instant must have a single key regardless of how the caller
    happened to express it, or `escalation_sk` would write two queue rows for
    one escalation and a caseworker would see the same case twice."""
    utc = datetime(2026, 10, 1, 13, 0, 0, tzinfo=timezone.utc)
    same = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert utc == same, "premise: these are the same instant"

    assert naming.escalation_sk(utc) == naming.escalation_sk(same)
    assert naming.ledger_sk(utc, 1) == naming.ledger_sk(same, 1)
