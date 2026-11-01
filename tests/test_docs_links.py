"""Every link and every named docs path in the current documentation resolves.

The repository's documents were moved into `docs/runbooks/`, `docs/evidence/`,
`docs/hackathon/` and `docs/lessons/` on 2026-10-31, and `CLAUDE.md` alone
named `docs/...` paths in backticks dozens of times. A moved file breaks every
one of those silently: nothing renders a broken backticked path as an error.

`docs/superpowers/` is dated history and is not checked: paths inside it are as
of each document's date (see `docs/superpowers/README.md`). A path *to* a
history document from current text is still checked, because it must exist.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
_LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)\)")
_DOCS_PATH = re.compile(r"`(docs/[A-Za-z0-9_./-]+\.(?:md|png|drawio))`")


def _tracked(*patterns: str) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", *patterns],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return [ROOT / f for f in out if (ROOT / f).exists()]


def current_docs() -> list[Path]:
    top = [ROOT / n for n in ("README.md", "CLAUDE.md", "SECURITY.md", "CONTRIBUTING.md")]
    docs = [
        p for p in _tracked("docs")
        if p.suffix == ".md" and "docs/superpowers/" not in p.as_posix()
    ]
    return [p for p in top if p.exists()] + docs


def relative_links(doc: Path) -> list[str]:
    text = _FENCE.sub("", doc.read_text())
    targets = []
    for raw in _LINK.findall(text):
        if re.match(r"^[a-z]+:", raw) or raw.startswith("#"):
            continue
        targets.append(raw.split("#", 1)[0].split("?", 1)[0])
    return [t for t in targets if t]


def test_every_relative_link_resolves():
    checked = 0
    broken = []
    for doc in current_docs():
        for target in relative_links(doc):
            checked += 1
            if not (doc.parent / target).exists():
                broken.append(f"{doc.relative_to(ROOT)}: {target}")
    assert checked >= 10, checked  # so it cannot pass by finding nothing
    assert not broken, broken


def test_every_docs_path_named_in_backticks_exists():
    sources = current_docs() + [
        p for p in _tracked("grace", "infra", "tests", "web/app", "web/lib",
                            "web/components", "web/__tests__", "amplify.yml")
        if p.suffix in {".py", ".ts", ".tsx", ".yml"}
    ]
    checked = 0
    missing = []
    for source in sources:
        if source.resolve() == Path(__file__).resolve():
            continue
        for path in _DOCS_PATH.findall(source.read_text()):
            checked += 1
            if not (ROOT / path).exists():
                missing.append(f"{source.relative_to(ROOT)}: {path}")
    assert checked >= 10, checked
    assert not missing, missing
