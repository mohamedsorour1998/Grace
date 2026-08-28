"""Case data types. All frozen — a case is a snapshot the gate reasons over.

Frozen is a safety property, not a style choice. The authority gate in Task 3
reads these fields to decide whether Grace may act alone; if a case were mutable,
a tool running after verification could change the facts the decision rested on.
A snapshot cannot be edited out from under a decision that already cited it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class Document:
    doc_id: str
    received: date
    expires: date | None = None


@dataclass(frozen=True)
class Household:
    household_id: str
    display_name: str
    language: str
    phone: str
    monthly_income_cents: int
    size: int


@dataclass(frozen=True)
class Case:
    """One household's renewal, as of one moment.

    `reported_income_cents`/`reported_size` are what the family most recently
    told the agency; `household.monthly_income_cents`/`.size` are what is on
    file. The gate compares the two — which is why they are separate fields and
    neither is derived from the other.

    Both default to `None`, meaning "not reported this cycle" — never to a
    household's on-file value, and never to 0. `0` is not a safe sentinel here:
    a family whose income genuinely dropped to zero is the most
    eligibility-relevant case Grace will ever see, so `0` must remain available
    as a real reported value. `evaluate` (Task 3) treats `None` as "no change
    reported" and compares against the on-file value only when a figure is
    actually present.
    """

    case_id: str
    household: Household
    program: str
    state: str
    cert_end: date
    documents: tuple[Document, ...] = ()
    reported_income_cents: int | None = None
    reported_size: int | None = None
    source_conflicts: tuple[str, ...] = ()


LedgerDetailValue = str | int | float | bool | None


@dataclass(frozen=True)
class LedgerEntry:
    """One audit-trail row. `detail` is frozen into an immutable mapping so a
    caller holding a `ledger()` result cannot rewrite what the ledger recorded —
    the entry itself is frozen, but a plain `dict` field is not, and Task 8's
    evals treat the ledger as ground truth.

    Values are restricted to JSON-safe scalars: Plan 2 writes this straight to
    DynamoDB, and a `date` or nested dataclass here (e.g. a `Window`) would fail
    at the storage boundary instead of at construction.
    """

    case_id: str
    at: datetime
    kind: str
    detail: Mapping[str, LedgerDetailValue] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        for key, value in self.detail.items():
            if not isinstance(key, str) or not isinstance(value, LedgerDetailValue):
                raise TypeError(
                    f"LedgerEntry.detail must be str -> JSON-safe scalar, "
                    f"got {key!r}: {value!r}"
                )
        if not isinstance(self.detail, MappingProxyType):
            object.__setattr__(self, "detail", MappingProxyType(dict(self.detail)))
        if self.at.tzinfo is None:
            raise ValueError("LedgerEntry.at must be timezone-aware")
