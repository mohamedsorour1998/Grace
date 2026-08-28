"""Case storage. In-memory for local runs; DynamoDB lands in Plan 2.

`CaseStore` is a `@runtime_checkable` Protocol so both this store and Plan 2's
DynamoDB implementation can be verified against it with a plain `isinstance`
check, rather than the Protocol only documenting an intent nothing enforces.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from grace.cases.models import Case, Document, Household, LedgerEntry

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "households.yaml"


class InvalidFixtureData(Exception):
    """A fixture case is missing a field or has a value of the wrong type.

    Fixture data drives which households escalate, so a coercion that loads
    "successfully" with the wrong value is worse than a loud failure. A prior
    version let `program: 42` load as an int (later crashing deep inside
    `load_pack`'s `.lower()` call with an unrelated `AttributeError`) and let
    `source_conflicts: "text"` iterate into one conflict per character.
    """


def _require_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidFixtureData(f"{key!r} must be a non-empty string, got {value!r}")
    return value


@runtime_checkable
class CaseStore(Protocol):
    def open_cases(self) -> list[Case]: ...
    def get(self, case_id: str) -> Case: ...
    def append_ledger(self, entry: LedgerEntry) -> None: ...
    def ledger(self, case_id: str) -> list[LedgerEntry]: ...


class InMemoryCaseStore:
    """Local-run store. Ledger is per-case so one family's trail never
    leaks into another's."""

    def __init__(self, cases: list[Case]) -> None:
        # Keying by id silently drops a duplicate, which would shrink the
        # caseload without any error — a household would go unprocessed and the
        # sweep would still report success. Refuse instead.
        ids = [c.case_id for c in cases]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate case ids: {duplicates}")
        self._cases = {c.case_id: c for c in cases}
        self._ledger: dict[str, list[LedgerEntry]] = {}

    def open_cases(self) -> list[Case]:
        return list(self._cases.values())

    def get(self, case_id: str) -> Case:
        if case_id not in self._cases:
            raise KeyError(f"No such case: {case_id}")
        return self._cases[case_id]

    def append_ledger(self, entry: LedgerEntry) -> None:
        # A ledger entry for a case this store has never heard of is a bug at
        # the call site (a typo'd case_id), not a new case — fail loudly rather
        # than silently opening a ledger bucket for a phantom case that
        # `ledger()` would otherwise report as an innocent empty list.
        if entry.case_id not in self._cases:
            raise KeyError(f"Cannot append ledger entry for unknown case: {entry.case_id}")
        self._ledger.setdefault(entry.case_id, []).append(entry)

    def ledger(self, case_id: str) -> list[LedgerEntry]:
        # A copy of the list: the ledger is the audit trail, so a caller
        # iterating it must not be able to append to or truncate the stored
        # list. Each LedgerEntry is itself immutable (frozen, and `detail` is a
        # MappingProxyType — see models.py), so this copy is a real snapshot,
        # not merely a list whose entries can still be edited in place.
        return list(self._ledger.get(case_id, []))


def _reported(raw: dict[str, Any], key: str) -> int | None:
    """Read a family-reported figure. Absent or explicit YAML `null` means
    "not reported this cycle" and must stay `None` — never default to the
    on-file value or to 0. Both are legitimate reported figures in their own
    right (a family can genuinely report a $0 income), so only true absence of
    the field may mean "no change reported"; `evaluate` (Task 3) is what
    decides what `None` means, not the loader.
    """
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise InvalidFixtureData(f"{key!r} must be a number, got {value!r}")
    return int(value)


def load_fixture_cases(path: Path | None = None) -> list[Case]:
    raw = yaml.safe_load((path or FIXTURES).read_text())
    cases: list[Case] = []
    for c in raw["cases"]:
        h = c["household"]
        conflicts = c.get("source_conflicts") or []
        if not isinstance(conflicts, list) or not all(isinstance(x, str) for x in conflicts):
            raise InvalidFixtureData(
                f"{c.get('case_id', '?')!r}: source_conflicts must be a list of "
                f"strings, got {conflicts!r}"
            )
        cases.append(
            Case(
                case_id=_require_str(c, "case_id"),
                household=Household(
                    household_id=_require_str(h, "household_id"),
                    display_name=_require_str(h, "display_name"),
                    language=_require_str(h, "language"),
                    phone=_require_str(h, "phone"),
                    monthly_income_cents=int(h["monthly_income_cents"]),
                    size=int(h["size"]),
                ),
                program=_require_str(c, "program"),
                state=_require_str(c, "state"),
                cert_end=date.fromisoformat(c["cert_end"]),
                documents=tuple(
                    Document(
                        doc_id=d["id"],
                        received=date.fromisoformat(d["received"]),
                        expires=date.fromisoformat(d["expires"]) if d.get("expires") else None,
                    )
                    for d in c.get("documents") or []
                ),
                reported_income_cents=_reported(c, "reported_income_cents"),
                reported_size=_reported(c, "reported_size"),
                source_conflicts=tuple(conflicts),
            )
        )
    return cases
