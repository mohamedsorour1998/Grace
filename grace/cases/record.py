"""One case record, as a DynamoDB row and back again.

**Why this module exists at all.** Until Plan 4, `build_store()` handed
`DynamoDBCaseStore` a list of `Case` objects loaded from
`fixtures/households.yaml`, and `get()`/`open_cases()` read that in-memory dict.
So the deployed agent's view of *which households exist* was fixed at container
image build time, and DynamoDB held only the ledger. A dashboard that wrote a
new case to the table would have rendered it on screen and the agent would never
have seen it — the "looks like it works" failure this project has spent three
plans eliminating. Case records had to move into the table, and this is the
translation.

**Two writers, one shape.** `infra/seed_cases.py` writes these rows from Python
and `web/app/api/case/new/route.ts` writes them from TypeScript. Two writers
agreeing by memory is how a submitted case ends up unparseable to the agent
that must read it, so `fixtures/case-record-shape.json` pins one example item
and both sides assert against it — `tests/test_case_record.py` here and
`web/__tests__/intake.test.ts` there. Neither test can drift without failing.

**What a record deliberately does NOT carry: any household identity.** No
name, no phone, no address, no email — hard rule 9, and the wider version Plan 2
learned the hard way when `read_case` returned `display_name`, a referee quoted
it into its deliberation prose, that prose became an escalation reason, and the
reason reached CloudWatch as a Step Functions payload. A DynamoDB row is a
surface a model reads and a service logs, and the table-wide PII scan this
project runs (all twelve surnames, `+1555`, `@`) must keep returning nothing.

That has one visible consequence, stated rather than hidden: a `Case` rebuilt
from a record has an **empty phone**, so `send_family_message` records an empty
destination in the transcript. That is honest. Grace's table is the *eligibility*
record, not the contact record — a real deployment resolves the family's channel
from the system that referred them, keyed by case id, and the ledger has never
recorded a phone number anyway (`grace/tools/action.py` says so explicitly).
`language` is kept because it is a message-drafting preference rather than an
identifier, and `read_case` surfaces it so outreach is written in the family's
own language.

**Parsing is strict and raises.** `InvalidCaseRecord` is the single exception
type, parallel to `InvalidRulePack` and `InvalidFixtureData`, so a caller fails
closed on one `except`. A half-populated `Case` is the dangerous outcome here: a
record whose `cert_end` fell back to today would have Grace file or escalate on
a date nobody chose, and nothing downstream could tell.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from grace.cases.models import Case, Document, Household
from infra import naming

# Bounds on the two variable-length fields. Neither is a DynamoDB limit (the
# item cap is 400KB); both exist so one request cannot write an item that later
# reads back slowly or, worse, exceeds the cap after a few more edits. A case
# with more than a handful of documents is a data-entry mistake, not a household.
MAX_DOCUMENTS = 32
MAX_SOURCE_CONFLICTS = 32

# The fields a record row carries, in the order `to_item` writes them. Named
# here so `tests/test_case_record.py` can assert the set exactly — an attribute
# added on one side of the language boundary and not the other is precisely the
# drift this module exists to prevent.
RECORD_ATTRIBUTES: tuple[str, ...] = (
    "pk",
    "sk",
    "case_id",
    "program",
    "state",
    "cert_end",
    "language",
    "monthly_income_cents",
    "size",
    "reported_income_cents",
    "reported_size",
    "documents",
    "source_conflicts",
    "created_at",
    "created_by",
)

# What a `created_by` value may look like: an opaque identifier and nothing else.
#
# **This is the same identity discipline as a decision row, enforced rather than
# intended.** The value is a Cognito `sub` — a UUID — and it is written so a
# caseworker reading an escalation can see *who asserted* the document status
# they are about to act on. That is the whole point of the field, and it is also
# exactly the field into which someone would one day put a username "because it
# is more readable". `verifySession` only checks that `sub` is a non-empty
# string, so nothing upstream refuses an email.
#
# The character class admits a UUID, an ARN-ish `:`-separated id, and a
# provider-prefixed id, and refuses `@`, whitespace, and anything with a space in
# it — the shapes a human-readable identifier actually takes. Hard rule 9: a
# record row is a surface a model reads and a service logs, and the table-wide
# PII scan must keep returning nothing.
#
# `\Z`, never `$`. Python's `$` also matches immediately *before* a trailing
# newline, so `^...$` accepts `"2448a4e8-\n"` — measured. JavaScript's `$` does
# not, so the mirrored `OPAQUE_SUBJECT` in `web/lib/intake.ts` is strict as
# written and the two would otherwise disagree about the same value across the
# language boundary. A trailing newline in a durable id is also exactly the shape
# that breaks a log line into two.
_OPAQUE_SUBJECT = re.compile(r"\A[A-Za-z0-9._:-]{1,128}\Z")


class InvalidCaseRecord(Exception):
    """A record row is missing a field, or has one of the wrong type.

    One exception type for every failure mode, so a reader fails closed with a
    single `except InvalidCaseRecord` rather than guessing which of `KeyError`,
    `TypeError`, or `ValueError` a malformed row happens to produce. Same
    contract as `InvalidRulePack` (Plan 1 Task 1) and `InvalidFixtureData`
    (Task 2).
    """


def _s(item: dict[str, Any], key: str) -> str:
    """A required non-empty string attribute."""
    attr = item.get(key)
    if not isinstance(attr, dict) or "S" not in attr:
        raise InvalidCaseRecord(f"{key!r} must be a DynamoDB string, got {attr!r}")
    value = attr["S"]
    if not isinstance(value, str) or not value.strip():
        raise InvalidCaseRecord(f"{key!r} must be a non-empty string, got {value!r}")
    return value


def _optional_s(item: dict[str, Any], key: str) -> str:
    """An optional string. Absent or NULL both read as `""`.

    Used only for `language`, where absence genuinely means "not stated" and the
    consequence is that outreach is drafted in English. Never used for a field
    the gate reasons over.
    """
    attr = item.get(key)
    if attr is None or attr.get("NULL"):
        return ""
    if "S" not in attr:
        raise InvalidCaseRecord(f"{key!r} must be a DynamoDB string, got {attr!r}")
    return str(attr["S"])


def _int(item: dict[str, Any], key: str) -> int:
    """A required integer attribute.

    DynamoDB carries numbers as strings, so this parses rather than casts, and
    it refuses anything with a fractional part instead of truncating it. A
    household size of 3.7 silently becoming 3 is a fact nobody entered.
    """
    attr = item.get(key)
    if not isinstance(attr, dict) or "N" not in attr:
        raise InvalidCaseRecord(f"{key!r} must be a DynamoDB number, got {attr!r}")
    raw = str(attr["N"])
    try:
        # `int(raw)` refuses "3.0" and "1E+30" outright, which is what we want:
        # both are shapes a careless writer produces and neither is an integer
        # a form collected. `Decimal` would accept both and then need a second
        # check.
        return int(raw)
    except ValueError as exc:
        raise InvalidCaseRecord(f"{key!r} must be an integer, got {raw!r}") from exc


def _optional_int(item: dict[str, Any], key: str) -> int | None:
    """A reported figure: absent or NULL both mean "not reported this cycle".

    Never 0, and never the household's on-file value — Plan 1 Task 2's rule. A
    family whose income genuinely dropped to zero is the most
    eligibility-relevant case Grace will see, so `0` has to stay available as a
    real reported value and only true absence may mean "no change reported".
    """
    attr = item.get(key)
    if attr is None or attr.get("NULL"):
        return None
    return _int(item, key)


def _iso_date(value: str, key: str) -> date:
    """An ISO date, or a refusal. Never a fallback to today.

    A `cert_end` that quietly defaulted to the current date would put the
    renewal window somewhere nobody chose, and every downstream verdict — file,
    escalate, or wait — would be computed against it with no error anywhere.
    """
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidCaseRecord(f"{key!r} must be an ISO date, got {value!r}") from exc


def _documents(item: dict[str, Any]) -> tuple[Document, ...]:
    attr = item.get("documents")
    if attr is None:
        return ()
    if not isinstance(attr, dict) or "L" not in attr or not isinstance(attr["L"], list):
        raise InvalidCaseRecord(f"'documents' must be a DynamoDB list, got {attr!r}")
    entries = attr["L"]
    if len(entries) > MAX_DOCUMENTS:
        raise InvalidCaseRecord(
            f"'documents' holds {len(entries)} entries, more than {MAX_DOCUMENTS}"
        )
    documents: list[Document] = []
    for entry in entries:
        if not isinstance(entry, dict) or "M" not in entry:
            raise InvalidCaseRecord(f"each document must be a map, got {entry!r}")
        fields = entry["M"]
        expires_attr = fields.get("expires")
        expires: date | None = None
        if expires_attr is not None and not expires_attr.get("NULL"):
            expires = _iso_date(_s(fields, "expires"), "expires")
        documents.append(
            Document(
                doc_id=_s(fields, "id"),
                received=_iso_date(_s(fields, "received"), "received"),
                expires=expires,
            )
        )
    return tuple(documents)


def _source_conflicts(item: dict[str, Any]) -> tuple[str, ...]:
    attr = item.get("source_conflicts")
    if attr is None:
        return ()
    if not isinstance(attr, dict) or "L" not in attr or not isinstance(attr["L"], list):
        raise InvalidCaseRecord(
            f"'source_conflicts' must be a DynamoDB list, got {attr!r}"
        )
    entries = attr["L"]
    if len(entries) > MAX_SOURCE_CONFLICTS:
        raise InvalidCaseRecord(
            f"'source_conflicts' holds {len(entries)} entries, more than "
            f"{MAX_SOURCE_CONFLICTS}"
        )
    conflicts: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or "S" not in entry:
            raise InvalidCaseRecord(f"each source conflict must be a string, got {entry!r}")
        value = entry["S"]
        if not isinstance(value, str) or not value.strip():
            raise InvalidCaseRecord(f"a source conflict must be non-empty, got {value!r}")
        conflicts.append(value)
    return tuple(conflicts)


def created_by_from_item(item: dict[str, Any]) -> str:
    """Who asserted this record's contents, or `""` if nobody is recorded.

    A separate reader rather than a field on `Case`, because it is **not a case
    fact**. `Case` is the snapshot the gate reasons over, and nothing in
    `authority.py` may ever be able to reach an identifier — adding a field there
    would put one inside the object every model in the graph receives. This is
    provenance about the record, read only by the surface that renders it.

    Absent or `NULL` both read as `""`, deliberately, and `""` is the honest
    value: the twelve seeded households were written by `infra/seed_cases.py`
    from a fixture, so no caseworker asserted anything about them. Inventing a
    placeholder that looks like an id — "system", "grace" — would be a magic
    value a renderer could not tell from a real one, the same objection
    `lib/cases.ts` raises to returning a presentation dash.
    """
    return _optional_s(item, "created_by")


def to_item(
    case: Case, *, created_at: datetime | None = None, created_by: str = ""
) -> dict[str, Any]:
    """One `Case` as a DynamoDB item.

    **Household identity is dropped here, not filtered downstream.** The
    household's name, phone, and internal id never reach the item, so no
    consumer of the table has the option of surfacing one. That is capability
    absence rather than redaction — the same reasoning as `read_case` no longer
    returning `display_name`.

    `created_at` is a parameter rather than a clock read so
    `fixtures/case-record-shape.json` can pin an exact item and both language
    bindings can be compared against it byte for byte.

    `created_by` is the opaque Cognito `sub` of the caseworker who submitted the
    case through `/new`, and `""` for a household seeded from the fixture. It is
    written so the dashboard can say *whose* assertion the document status is —
    Grace takes a caseworker's word that a document was sent to the state, and
    hard rule 6 is about never letting an assertion read as a confirmed fact. A
    value that is not opaque is **refused**, not stripped: silently dropping an
    email would leave the caseworker's page saying nobody asserted it, which is a
    different false claim.
    """
    if not isinstance(created_by, str):
        raise InvalidCaseRecord(f"created_by must be a string, got {created_by!r}")
    if created_by and not _OPAQUE_SUBJECT.match(created_by):
        # Refused rather than redacted. An `@` here is an email address, and a
        # space is a person's name; either would put household-adjacent identity
        # into a row that a model reads and Step Functions logs.
        raise InvalidCaseRecord(
            f"created_by must be an opaque identifier, got {created_by!r}"
        )
    stamp = created_at or datetime.now(timezone.utc)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        # Same refusal as `infra/naming._utc_stamp`, and for the same reason: a
        # naive `astimezone()` silently assumes the local clock, which is right
        # on a UTC host and wrong where this was written.
        raise InvalidCaseRecord("created_at must be a timezone-aware datetime")

    item: dict[str, Any] = {
        "pk": {"S": naming.case_pk(case.case_id)},
        "sk": {"S": naming.RECORD_SK},
        "case_id": {"S": case.case_id},
        "program": {"S": case.program},
        "state": {"S": case.state},
        "cert_end": {"S": case.cert_end.isoformat()},
        "language": {"S": case.household.language},
        "monthly_income_cents": {"N": str(case.household.monthly_income_cents)},
        "size": {"N": str(case.household.size)},
        "reported_income_cents": (
            {"NULL": True}
            if case.reported_income_cents is None
            else {"N": str(case.reported_income_cents)}
        ),
        "reported_size": (
            {"NULL": True} if case.reported_size is None else {"N": str(case.reported_size)}
        ),
        "documents": {
            "L": [
                {
                    "M": {
                        "id": {"S": d.doc_id},
                        "received": {"S": d.received.isoformat()},
                        "expires": (
                            {"NULL": True} if d.expires is None else {"S": d.expires.isoformat()}
                        ),
                    }
                }
                for d in case.documents
            ]
        },
        "source_conflicts": {"L": [{"S": c} for c in case.source_conflicts]},
        "created_at": {"S": stamp.astimezone(timezone.utc).isoformat()},
        # NULL rather than an empty string when nobody asserted this record, and
        # the key is always present so the attribute set is identical across both
        # writers. Same shape as `reported_income_cents`: absence is a real state
        # with its own meaning, not a value to be guessed at.
        "created_by": {"S": created_by} if created_by else {"NULL": True},
    }
    return item


def from_item(item: dict[str, Any]) -> Case:
    """One DynamoDB item as a `Case`, or `InvalidCaseRecord`.

    The rebuilt `Household` carries an empty `household_id`, `display_name`, and
    `phone`, because the record carries none of them. Empty is the honest value:
    Grace genuinely does not hold the family's name or number, and inventing a
    placeholder that *looks* like data ("Household c-013") would be the magic
    value `lib/cases.ts` refuses to return for a missing program.
    """
    if not isinstance(item, dict):
        raise InvalidCaseRecord(f"a case record must be a mapping, got {item!r}")
    case_id = _s(item, "case_id")
    return Case(
        case_id=case_id,
        household=Household(
            household_id="",
            display_name="",
            language=_optional_s(item, "language"),
            phone="",
            monthly_income_cents=_int(item, "monthly_income_cents"),
            size=_int(item, "size"),
        ),
        program=_s(item, "program"),
        state=_s(item, "state"),
        cert_end=_iso_date(_s(item, "cert_end"), "cert_end"),
        documents=_documents(item),
        reported_income_cents=_optional_int(item, "reported_income_cents"),
        reported_size=_optional_int(item, "reported_size"),
        source_conflicts=_source_conflicts(item),
    )


def directory_item(case_id: str) -> dict[str, Any]:
    """One case's entry in the enumeration partition.

    Carries the case id and nothing else — it exists so `open_cases()` can be a
    single Query rather than a Scan, and a row that carried more would be a
    second copy of the record able to drift from the first.
    """
    if not isinstance(case_id, str) or not case_id.strip():
        raise InvalidCaseRecord(f"case_id must be a non-empty string, got {case_id!r}")
    return {
        "pk": {"S": naming.CASE_DIRECTORY_PK},
        "sk": {"S": naming.directory_sk(case_id)},
        "case_id": {"S": case_id},
    }


def case_id_from_directory_item(item: dict[str, Any]) -> str:
    """Read a case id back out of a directory row."""
    return _s(item, "case_id")
