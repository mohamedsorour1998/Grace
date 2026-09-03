"""Which `CaseStore` this process uses.

One place, so the `GRACE_STORE` branch is not duplicated between the entrypoint
and anything else that needs a store — two copies would eventually disagree
about the default.

The default is in-memory on purpose: the fast suite runs offline, and defaulting
to DynamoDB would make hundreds of passing tests require AWS credentials.
"""

from __future__ import annotations

import os

from grace.cases.models import Case
from grace.cases.store import CaseStore, InMemoryCaseStore, load_fixture_cases

_IN_MEMORY = "memory"
_DYNAMODB = "dynamodb"

# The variable Grace reads to decide. Named once so the error message below and
# the test's `match=` cannot drift from what is actually read.
_ENV_VAR = "GRACE_STORE"


def build_store(cases: list[Case] | None = None) -> CaseStore:
    """Build the store this process should use.

    An unrecognized `GRACE_STORE` raises rather than falling back. A typo'd
    `GRACE_STORE=dynamo` in the deployed runtime would otherwise write the
    ledger to memory and discard it at process exit — the dashboard would show
    an empty ledger while the sweep reported success, with nothing anywhere
    saying why. Failing at startup is the only version of that a human notices.

    The whitelist polarity is deliberate and matches `APPROVE_DECISIONS` in
    `grace/run.py` (Task 6): only an exact match to a known spelling selects a
    store, so an unrecognized value is always the one that refuses rather than
    the one that proceeds.

    `cases is not None` rather than `if cases`, so an explicit empty caseload is
    honoured instead of silently reloading the fixtures — the same reasoning
    Plan 1's Task 2 applied to a reported income of `0`: a legitimate falsy
    value cannot double as an absence marker.
    """
    cases = cases if cases is not None else load_fixture_cases()
    # Absent and set-but-blank are deliberately different states. `os.getenv`
    # returns the default only when the variable is *absent*, which is the fast
    # suite's case. `GRACE_STORE=` — set to an empty string, as a stray line in
    # an env file or an empty container variable produces — is someone having
    # tried to configure this and produced nothing, so it raises with everything
    # else unrecognized rather than quietly selecting the in-memory store in a
    # deployed runtime whose ledger would then vanish at process exit.
    raw = os.getenv(_ENV_VAR)
    kind = _IN_MEMORY if raw is None else raw.strip().lower()
    if kind == _IN_MEMORY:
        return InMemoryCaseStore(cases)
    if kind == _DYNAMODB:
        # Imported here, not at module scope: the in-memory path must not
        # require boto3 to be importable, and this keeps the fast suite's import
        # graph unchanged.
        from grace.cases.dynamo_store import DynamoDBCaseStore

        return DynamoDBCaseStore(cases)
    raise ValueError(f"{_ENV_VAR} must be {_IN_MEMORY!r} or {_DYNAMODB!r}, got {kind!r}")
