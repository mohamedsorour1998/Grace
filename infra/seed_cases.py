"""Write the twelve fixture households into `grace-cases` as record rows.

Run this **before** anything reads records from the table, so the table is
complete the moment the store starts trusting it:

    .venv/bin/python -m infra.seed_cases            # write what is missing
    .venv/bin/python -m infra.seed_cases --verify   # read back and compare only

**Idempotent, and idempotent in the one way that matters: it never overwrites.**
`DynamoDBCaseStore.create_case` puts the record under
`ConditionExpression="attribute_not_exists(sk)"`, so a second run leaves every
existing row byte-for-byte untouched and reports it as unchanged. That is not a
convenience — the live table holds the demo's entire evidence (a
`renewal_submitted` row for exactly `c-001`–`c-009`, escalation rows for exactly
`c-010`/`c-011`/`c-012`), and a seeding script that could rewrite a partition is
one bad flag away from destroying it. This script issues **no** delete and no
unconditional overwrite of a record; the only unconditional write it makes is
the case's directory row, which carries a case id and nothing else.

**`--verify` is a separate claim from `--write`.** "The put returned" and "the
row is there and decodes to the case I meant" are different statements, and only
the second one is worth anything — the same lesson Plan 2 learned when
`update_continuous_backups` returned successfully and left point-in-time
recovery off. Verify reads every record back through `from_item` and compares
the decoded case against the fixture field by field, ignoring exactly the fields
a record deliberately does not carry.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

import boto3

from grace.cases.dynamo_store import CaseAlreadyExists, DynamoDBCaseStore
from grace.cases.models import Case
from grace.cases.store import load_fixture_cases
from infra import naming


def _comparable(case: Case) -> Case:
    """One case reduced to what a record row can carry.

    A record deliberately holds no household id, display name, or phone, so a
    round-trip comparison has to drop those on the fixture side too — otherwise
    every verification fails for the one reason that is correct behaviour. This
    is a *narrowing*, not a coercion: everything the gate reads (program, state,
    certification end, on-file income and size, reported figures, documents,
    source conflicts) is compared exactly.
    """
    return replace(
        case,
        household=replace(
            case.household, household_id="", display_name="", phone=""
        ),
    )


def seed(store: DynamoDBCaseStore, cases: list[Case]) -> tuple[list[str], list[str]]:
    """Write every missing record. Returns (written, already_present)."""
    written: list[str] = []
    present: list[str] = []
    for case in cases:
        try:
            store.create_case(case)
        except CaseAlreadyExists:
            present.append(case.case_id)
        else:
            written.append(case.case_id)
    return written, present


def verify(store: DynamoDBCaseStore, cases: list[Case]) -> list[str]:
    """Read every record back and compare. Returns the case ids that disagree.

    Reads through `DynamoDBCaseStore._record_item` + `record.from_item` — the
    same path the deployed agent uses — rather than through `get()`, because
    `get()` falls back to the in-memory seed when the table holds nothing, and a
    verification that can be satisfied by the fallback proves nothing about the
    table.
    """
    from grace.cases import record  # local: keeps this module's import graph flat

    wrong: list[str] = []
    for case in cases:
        item = store._record_item(case.case_id)
        if item is None:
            wrong.append(f"{case.case_id}: no record row in the table")
            continue
        try:
            stored = record.from_item(item)
        except record.InvalidCaseRecord as exc:
            wrong.append(f"{case.case_id}: record does not parse: {exc}")
            continue
        expected = _comparable(case)
        if stored != expected:
            wrong.append(f"{case.case_id}: stored {stored!r} != fixture {expected!r}")
    return wrong


def _build_store(table_name: str | None, client=None) -> DynamoDBCaseStore:
    return DynamoDBCaseStore(
        # An empty seed on purpose. This script's whole job is the table, and a
        # store carrying the twelve fixtures in memory could answer a read from
        # them and report a table that was never written as verified.
        [],
        table_name=table_name or naming.TABLE,
        client=client or boto3.client("dynamodb", region_name=naming.REGION),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="read the records back and compare; write nothing",
    )
    parser.add_argument("--table", default=None, help="table name (default: grace-cases)")
    args = parser.parse_args(argv)

    cases = load_fixture_cases()
    store = _build_store(args.table)

    if not args.verify:
        written, present = seed(store, cases)
        print(f"wrote {len(written)} record(s): {written or '—'}")
        print(f"already present, untouched: {len(present)} — {present or '—'}")

    wrong = verify(store, cases)
    if wrong:
        print(f"VERIFY FAILED for {len(wrong)} case(s):", file=sys.stderr)
        for line in wrong:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"verified {len(cases)} record(s) against the fixtures: all match")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
