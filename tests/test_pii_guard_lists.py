"""Every household-name guard must name every fixture household.

A PII guard is only as good as its list. The dashboard plan's first draft
matched three surnames of twelve, and missed the two households most likely to
carry a name into an escalation reason. On 2026-10-31 two guards were still
short: one lacked Torres, Abebe and Kowalski, another lacked Delacroix and
Torres. Each list was written by hand, so each could drift on its own.

The lists are found from disk rather than named here, for the same reason the
model-id guard walks the package: a list of guards someone remembered is how a
guard gets missed. A test file naming six or more fixture surnames is a guard
list, and must name all twelve.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GUARD_THRESHOLD = 6


def fixture_surnames() -> list[str]:
    data = yaml.safe_load((ROOT / "fixtures" / "households.yaml").read_text())
    names = []
    for case in data["cases"]:
        match = re.fullmatch(r"The (\w+) Household", case["household"]["display_name"])
        assert match, case["household"]["display_name"]
        names.append(match.group(1))
    return names


def _tracked_test_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "tests", "web/__tests__"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return [ROOT / f for f in out if f.endswith((".py", ".ts"))]


def names_in(path: Path, surnames: list[str]) -> set[str]:
    text = path.read_text()
    return {name for name in surnames if re.search(rf"\b{name}\b", text)}


def guard_files(surnames: list[str]) -> list[Path]:
    this = Path(__file__).resolve()
    return [
        f for f in _tracked_test_files()
        if f.resolve() != this and len(names_in(f, surnames)) >= GUARD_THRESHOLD
    ]


def test_every_surname_guard_names_every_fixture_household():
    surnames = fixture_surnames()
    assert len(surnames) == 12
    guards = guard_files(surnames)
    assert len(guards) >= 5, guards  # so it cannot pass by finding nothing
    missing = {
        str(f.relative_to(ROOT)): sorted(set(surnames) - names_in(f, surnames))
        for f in guards
    }
    assert not {f: m for f, m in missing.items() if m}
