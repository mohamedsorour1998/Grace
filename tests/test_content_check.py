"""`scripts/content_check.sh` keeps names out of public text without publishing them.

The word list lives outside the repository (a file locally, a secret in CI), so
the list itself is never committed. Every test here builds a throwaway git
repository and uses invented words only.

Two properties matter more than the matching itself:

- **A missing or empty list refuses (exit 2).** A check with nothing to check
  for reports clean on every input, which is indistinguishable from a pass.
- **A hit prints `path:line` and never the word.** CI logs on a public
  repository are public, and a check that echoes what it found would publish
  the list it exists to protect.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "content_check.sh"


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    return repo


def _run(repo: Path, denylist: Path | None) -> subprocess.CompletedProcess[str]:
    args = ["bash", str(SCRIPT)] + ([str(denylist)] if denylist else [])
    return subprocess.run(args, cwd=repo, capture_output=True, text=True)


def _list(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "denylist"
    path.write_text(text)
    return path


def test_a_denied_word_is_reported_with_its_line(tmp_path):
    repo = _repo(tmp_path, {"a.md": "one\ntwo\nmade by ZebraCorp\n"})
    out = _run(repo, _list(tmp_path, "deny:zebracorp\n"))
    assert out.returncode == 1
    assert "a.md:3" in out.stdout


def test_a_hit_never_prints_the_word(tmp_path):
    repo = _repo(tmp_path, {"a.md": "made by ZebraCorp\n"})
    out = _run(repo, _list(tmp_path, "deny:zebracorp\n"))
    assert out.returncode == 1
    assert "zebracorp" not in (out.stdout + out.stderr).lower()


def test_a_word_inside_an_identifier_is_still_a_hit(tmp_path):
    # Resource names join words with underscores, which a whole-word match
    # treats as one word and misses.
    repo = _repo(tmp_path, {"b.py": "memory_id = 'zebracorp_education_memory-x1'\n"})
    out = _run(repo, _list(tmp_path, "deny:zebracorp\n"))
    assert out.returncode == 1
    assert "b.py:1" in out.stdout


def test_an_allowed_hostname_is_not_a_hit(tmp_path):
    repo = _repo(tmp_path, {"a.md": "see https://grace.zebracorp.example/login\n"})
    out = _run(repo, _list(tmp_path, "deny:zebracorp\nallow:([a-z0-9-]+\\.)*zebracorp\\.example\n"))
    assert out.returncode == 0, out.stdout + out.stderr


def test_an_allowed_hostname_does_not_hide_a_hit_on_the_same_line(tmp_path):
    repo = _repo(tmp_path, {"a.md": "ZebraCorp runs grace.zebracorp.example\n"})
    out = _run(repo, _list(tmp_path, "deny:zebracorp\nallow:([a-z0-9-]+\\.)*zebracorp\\.example\n"))
    assert out.returncode == 1
    assert "a.md:1" in out.stdout


def test_history_is_exempt(tmp_path):
    repo = _repo(tmp_path, {"docs/superpowers/plans/old.md": "ZebraCorp\n", "ok.md": "fine\n"})
    out = _run(repo, _list(tmp_path, "deny:zebracorp\n"))
    assert out.returncode == 0, out.stdout + out.stderr


def test_a_clean_tree_passes(tmp_path):
    repo = _repo(tmp_path, {"a.md": "nothing to see\n"})
    out = _run(repo, _list(tmp_path, "deny:zebracorp\n"))
    assert out.returncode == 0, out.stdout + out.stderr


def test_a_missing_denylist_refuses(tmp_path):
    repo = _repo(tmp_path, {"a.md": "ZebraCorp\n"})
    assert _run(repo, None).returncode == 2
    assert _run(repo, tmp_path / "absent").returncode == 2


def test_an_empty_denylist_refuses(tmp_path):
    repo = _repo(tmp_path, {"a.md": "ZebraCorp\n"})
    out = _run(repo, _list(tmp_path, "allow:([a-z0-9-]+\\.)*zebracorp\\.example\n\n"))
    assert out.returncode == 2
