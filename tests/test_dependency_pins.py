"""Dependency pins that a resolver cannot be trusted to get right on its own.

`bedrock-agentcore` 1.23.1 and later import `strands.experimental.bidi` from
their Strands memory integration, which `strands-agents` 1.54.0 does not have.
They do declare the constraint (`strands-agents>=1.56.0`), but only under
their own `strands-agents` extra — which Grace does not install, so the
resolver never sees it. Measured on 2026-10-31: with `bedrock-agentcore>=1.22`
the lock resolved 1.24.1 and seven test modules failed to import
`grace.memory`. The deployed image is worse off than the venv: its
`uv pip install .` resolves from `pyproject.toml`, not from the lock, so a
rebuild would have shipped a container that dies at import.

Two guards, because each covers what the other cannot. The installed-metadata
check catches an incompatible pair in this venv; the exact-pin check is what
makes the image resolve the same version the venv was tested with.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import distributions, version
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def test_no_installed_package_requires_a_different_strands_agents():
    installed = Version(version("strands-agents"))
    checked: set[str] = set()
    conflicts: list[str] = []
    for dist in distributions():
        for raw in dist.requires or []:
            requirement = Requirement(raw)
            if requirement.name != "strands-agents":
                continue
            # Extras included on purpose: an extra-only constraint is exactly
            # how this conflict hid from the resolver.
            checked.add(dist.metadata["Name"])
            if not requirement.specifier.contains(installed, prereleases=True):
                conflicts.append(f"{dist.metadata['Name']} {dist.version}: {raw}")
    assert "bedrock-agentcore" in checked, checked  # not vacuous
    assert not conflicts, f"strands-agents {installed} is outside: {conflicts}"


def test_bedrock_agentcore_is_pinned_exactly():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    pins = [Requirement(d) for d in deps if Requirement(d).name == "bedrock-agentcore"]
    assert len(pins) == 1
    (spec,) = pins[0].specifier
    assert spec.operator == "==", pins[0]


def test_the_dev_extra_brings_the_a2a_extra():
    """The documented setup is `uv sync --extra dev`, and the suite imports
    `grace.a2a` (its own tests, and the package walk that guards model ids).
    Without this, that setup stops at collection with an ImportError."""
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]
    assert "grace[a2a]" in extras["dev"]
