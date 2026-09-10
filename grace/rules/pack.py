"""Rule packs: the authoritative source for every date and threshold.

Grace never lets a model infer a deadline. Windows come from these packs.

Everything here validates aggressively and raises `InvalidRulePack` on anything
it cannot fully verify. A rule pack sets the thresholds that decide whether a
family keeps coverage, so a malformed pack must never load in a degraded state:
a `required_documents: []` would make the missing-document check vacuous, and a
non-finite income threshold would disable the income check silently.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PACKS_DIR = Path(__file__).parent / "packs"


class InvalidRulePack(Exception):
    """A pack is missing, unreadable, or fails validation.

    One exception type for every failure mode so callers can fail closed on a
    single `except InvalidRulePack` rather than guessing which of KeyError,
    TypeError, ValueError, or yaml.YAMLError a malformed file happens to raise.
    """


@dataclass(frozen=True)
class RequiredDocument:
    doc_id: str
    max_age_days: int


@dataclass(frozen=True)
class RuleSource:
    """Where one rule-pack parameter comes from.

    `authority` is a citation or the literal string `policy choice`. Both are
    honest; an empty one is not. `note` says what the provision actually
    establishes, because a bare section number invites a reader to assume the
    regulation mandates the exact value when it often gives a range.
    """

    parameter: str
    authority: str
    note: str = ""


@dataclass(frozen=True)
class RulePack:
    program: str
    state: str
    version: str
    certification_period_months: int
    window_opens_days_before_end: int
    grace_period_days_after_end: int
    required_documents: tuple[RequiredDocument, ...]
    income_change_immaterial_pct: float
    # Defaulted so no existing construction site breaks. A pack that cites
    # nothing still loads — `tests/test_rule_pack_sources.py` is what requires
    # the shipped packs to cite everything, because that is a claim about
    # Grace's own packs rather than about the file format.
    sources: tuple[RuleSource, ...] = ()


def _require_str(raw: dict[str, Any], key: str) -> str:
    """Read a string field, rejecting YAML's implicit scalar coercions.

    Deliberately does not call `str()` on a non-string. An unquoted
    `version: 2026.10` parses as the float 2026.1, and coercing it would yield
    "2026.1" — indistinguishable from a genuine 2026.1. The ledger records which
    rule version authorized a filing, so two versions must never collapse.
    """
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidRulePack(f"{key!r} must be a non-empty string, got {value!r}")
    return value


def _require_non_negative_int(raw: dict[str, Any], key: str) -> int:
    """Read a day/month count, rejecting negatives and bools.

    A negative day count silently inverts a window — a negative grace period
    puts `grace_ends` before `due`, which `window_status` cannot detect.
    `bool` is excluded because it is an `int` subclass in Python, so `True`
    would otherwise pass as 1.
    """
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRulePack(f"{key!r} must be an int, got {value!r}")
    if value < 0:
        raise InvalidRulePack(f"{key!r} must be >= 0, got {value}")
    return value


def _require_finite_float(raw: dict[str, Any], key: str) -> float:
    """Read a percentage, rejecting NaN and infinity.

    Every comparison against NaN is False, so a NaN threshold would make the
    income check pass for any change at all — a 1000% income rise would not
    escalate.
    """
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidRulePack(f"{key!r} must be a number, got {value!r}")
    result = float(value)
    if not math.isfinite(result):
        raise InvalidRulePack(f"{key!r} must be finite, got {result}")
    if result < 0:
        raise InvalidRulePack(f"{key!r} must be >= 0, got {result}")
    return result


def _require_documents(raw: dict[str, Any]) -> tuple[RequiredDocument, ...]:
    """Read the required-document list, rejecting an empty one.

    An empty list would make the missing-document gate condition unreachable,
    so every case would pass document verification. Both shipped programs
    require documents; a pack that requires none is a malformed pack.
    """
    entries = raw.get("required_documents")
    if not isinstance(entries, list) or not entries:
        raise InvalidRulePack(
            f"'required_documents' must be a non-empty list, got {entries!r}"
        )
    documents = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise InvalidRulePack(f"each required document must be a mapping, got {entry!r}")
        documents.append(
            RequiredDocument(
                doc_id=_require_str(entry, "id"),
                max_age_days=_require_non_negative_int(entry, "max_age_days"),
            )
        )
    doc_ids = [d.doc_id for d in documents]
    if len(set(doc_ids)) != len(doc_ids):
        raise InvalidRulePack(f"duplicate required document ids: {doc_ids}")
    return tuple(documents)


def _require_sources(raw: dict[str, Any]) -> tuple[RuleSource, ...]:
    """Read the citation block, failing closed on anything malformed.

    Parsed with the same discipline as `required_documents`: a malformed block
    raises `InvalidRulePack` rather than loading a pack whose citations are
    silently absent. Plan 1 Task 1's contract — one exception type for missing,
    unreadable, malformed, mislabelled, and out-of-range — so a caller fails
    closed with a single `except InvalidRulePack`.

    Absence is permitted and an *empty* field is not, which is the same polarity
    as `authority: "policy choice"` being an acceptable answer while `TBD` is
    not. A citation that is present but blank looks like diligence from a
    distance; a missing block is visibly missing.

    Like `_require_str`, this refuses a non-string rather than coercing one.
    YAML's implicit scalars turn an unquoted `no` into a boolean and an
    unquoted `42 CFR 435.916` fragment into something unexpected, and
    `str()`-ing the result would render a parse accident as a citation.
    """
    entries = raw.get("sources", [])
    if not isinstance(entries, list):
        raise InvalidRulePack(
            f"'sources' must be a list of source entries, got {type(entries).__name__}"
        )
    sources: list[RuleSource] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise InvalidRulePack(f"each source must be a mapping, got {entry!r}")
        # `note` defaults to empty; `parameter` and `authority` have no default,
        # so an omitted one arrives as None and is refused below rather than
        # becoming the string "None".
        fields: dict[str, str] = {}
        for name, default in (("parameter", None), ("authority", None), ("note", "")):
            value = entry.get(name, default)
            if not isinstance(value, str):
                raise InvalidRulePack(f"source {name} must be a string, got {value!r}")
            fields[name] = value
        if not fields["parameter"].strip() or not fields["authority"].strip():
            raise InvalidRulePack(
                "source parameter and authority must be non-empty, got "
                f"{fields['parameter']!r}/{fields['authority']!r}"
            )
        sources.append(RuleSource(**fields))
    parameters = [s.parameter for s in sources]
    if len(set(parameters)) != len(parameters):
        # Two entries for one parameter means one of them is being ignored, and
        # which one wins would depend on order. A reader auditing the pack would
        # see a citation that does not govern.
        raise InvalidRulePack(f"duplicate source parameters: {parameters}")
    return tuple(sources)


def _pack_path(program: str, state: str) -> Path:
    """Resolve a pack path, refusing anything outside PACKS_DIR.

    `program` and `state` reach this function from case records, and in Plan 2
    from a Gateway payload. Without containment, `load_pack("../../evil", "NY")`
    would load an attacker-placed YAML that could forge a deadline or declare no
    required documents.
    """
    if not isinstance(program, str) or not isinstance(state, str) or not program or not state:
        raise InvalidRulePack(f"program and state must be non-empty strings, got {program!r}/{state!r}")

    filename = f"{program.lower()}-{state.lower()}.yaml"
    candidate = (PACKS_DIR / filename).resolve()
    if candidate.parent != PACKS_DIR.resolve():
        raise InvalidRulePack(f"No rule pack for {program}/{state}: path escapes pack directory")
    return candidate


def load_pack(program: str, state: str) -> RulePack:
    """Load and validate the rule pack for a program/state.

    Raises rather than returning a default: a missing pack must never be
    silently treated as "no deadline".
    """
    path = _pack_path(program, state)
    if not path.is_file():
        raise InvalidRulePack(f"No rule pack for {program}/{state} at {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise InvalidRulePack(f"Could not read rule pack at {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise InvalidRulePack(f"Rule pack at {path} must be a mapping, got {type(raw).__name__}")

    pack = RulePack(
        program=_require_str(raw, "program"),
        state=_require_str(raw, "state"),
        version=_require_str(raw, "version"),
        certification_period_months=_require_non_negative_int(raw, "certification_period_months"),
        window_opens_days_before_end=_require_non_negative_int(raw, "window_opens_days_before_end"),
        grace_period_days_after_end=_require_non_negative_int(raw, "grace_period_days_after_end"),
        required_documents=_require_documents(raw),
        income_change_immaterial_pct=_require_finite_float(raw, "income_change_immaterial_pct"),
        sources=_require_sources(raw),
    )

    # A pack whose own fields disagree with the file it was loaded from is
    # mislabelled; loading it would attribute one program's rules to another.
    if pack.program.lower() != program.lower() or pack.state.lower() != state.lower():
        raise InvalidRulePack(
            f"Pack at {path} declares {pack.program}/{pack.state}, requested {program}/{state}"
        )
    return pack
