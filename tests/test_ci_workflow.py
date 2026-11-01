"""The CI workflow's safety properties, asserted rather than trusted.

A workflow is configuration nobody re-reads until it fails, and the properties
that matter here fail silently: a moved tag runs someone else's code with this
repository's token, a missing `-m "not live"` makes CI need AWS credentials, and
a scan without `--exit-code 1` reports every CVE and passes anyway.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _steps(job: str) -> list[dict]:
    return _workflow()["jobs"][job]["steps"]


def _runs(job: str) -> str:
    return "\n".join(step.get("run", "") for step in _steps(job))


def test_the_four_jobs_exist():
    assert set(_workflow()["jobs"]) == {"python", "web", "a2a-image", "content-check"}


def test_every_action_is_pinned_to_a_commit():
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", WORKFLOW.read_text(), re.MULTILINE)
    assert uses, "no actions found"  # not vacuous
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), ref


def test_the_workflow_can_only_read():
    assert _workflow()["permissions"] == {"contents": "read"}
    for name, job in _workflow()["jobs"].items():
        assert "permissions" not in job, name  # no job widens it


def test_ci_deselects_live_tests():
    runs = _runs("python")
    assert re.search(r'pytest -m "not live"', runs)
    # And holds no AWS credentials to fall back on.
    assert "AWS_ACCESS_KEY_ID" not in WORKFLOW.read_text()


def test_the_python_job_installs_from_the_lock():
    assert "--locked" in _runs("python")


def test_the_web_job_runs_all_four_gates():
    runs = _runs("web")
    for gate in ("npm ci", "npm run typecheck", "npm run lint", "npm run test", "npm run build"):
        assert gate in runs, gate


def test_the_image_is_built_on_arm():
    job = _workflow()["jobs"]["a2a-image"]
    assert job["runs-on"] == "ubuntu-24.04-arm"
    assert "Dockerfile.a2a" in _runs("a2a-image")


def test_the_scan_fails_the_build():
    runs = _runs("a2a-image")
    assert "trivy image" in runs
    assert "--exit-code 1" in runs and "--severity HIGH,CRITICAL" in runs
    assert "sha256sum" in runs  # the scanner itself is verified before it runs


def test_the_content_check_reads_its_list_from_a_secret():
    text = WORKFLOW.read_text()
    assert "secrets.CONTENT_DENYLIST" in text
    assert "scripts/content_check.sh" in _runs("content-check")
