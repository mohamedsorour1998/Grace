"""AgentCore Memory wiring: per-household facts across the annual gap.

A recert cycle is annual, so "income verified via pay stubs last cycle" and
"prefers Arabic, evenings" have to survive eleven months between contacts.

**Where this may attach.** The orchestrator only. Agents inside a Graph or Swarm
must not carry their own `session_manager` — Python raises `ValueError` (hard rule
2) — so `decide`, `intake`, `documents`, and the swarm's three agents never get
one. That is why this module exposes a *factory* the caller attaches rather than
something `build_case_graph` wires up: there is no legal place inside the graph
for it to go.

`tests/test_memory.py` asserts that structurally, recursing into nested nodes and
checking both attribute spellings, because Task 7 found the equivalent Task 6
assertion passing vacuously through `getattr(..., None)` on the swarm node — the
one node that contains three more agents. That test is load-bearing rather than
belt-and-braces: verified against `strands` 1.54.0, the SDK does **not** catch
the arrangement Grace actually builds. `_validate_node_executor` guards only
`isinstance(executor, Agent)` and `Swarm._validate_swarm` guards only each
member's `_session_manager`, so a `Swarm` holding its *own* `session_manager`,
added as a graph node, is accepted with no error at all.

**The spec's selective-write plan is not implementable as written.**
`PersistenceMode` is `FULL` or `NONE` only — verified against the installed
package, no per-turn selectivity exists. The hazard the spec was guarding
(guardrail-blocked or errored turns persisting and poisoning later runs) is
therefore handled by *scope*: Memory sits on the orchestrator, whose turns are
the case's opening task and final summary, not on the nodes that carry raw tool
output and model errors. `FULL` is passed explicitly rather than defaulted, so
the decision is visible at the call site — `NONE` would leave retrieval working
while writing nothing, which reads identically to `FULL` in any test that only
retrieves.

**Retrieval is advisory.** Anything recalled here may make Grace more cautious
and may never satisfy a gate condition (hard rule 5). The gate reads the case
record, never memory. That is also why every failure path here returns `None`
instead of raising: losing recall degrades outreach quality, and cannot change a
verdict.
"""

from __future__ import annotations

import logging
import os
import re

from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    PersistenceMode,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)

from infra import naming

logger = logging.getLogger(__name__)

# `/facts/{actorId}`, NOT the AWS blog post's `/users/{actorId}/facts`. The
# namespace must match the `namespaceTemplates` set at memory creation, and a
# mismatch retrieves nothing silently rather than raising — so this dict and
# `infra/provision_memory.py` must be changed together. They are not two lists:
# provisioning derives its strategies from this one (`memory_strategies()`), and
# refuses a namespace it has no strategy for.
RETRIEVAL_NAMESPACES: dict[str, RetrievalConfig] = {
    # What was verified and how, last cycle.
    "/facts/{actorId}": RetrievalConfig(top_k=10, relevance_score=0.3),
    # Language and contact-time preferences for outreach.
    "/preferences/{actorId}": RetrievalConfig(top_k=5, relevance_score=0.5),
}

# Read off the live API model rather than the docs: `CreateEvent`'s `sessionId`
# is `min 1, max 100, [a-zA-Z0-9][a-zA-Z0-9-_]*`. Note what that pattern
# excludes — `.`, `:`, and `+` are all invalid, so the obvious "case id plus an
# ISO timestamp" session id is refused by the service. Checked here so the
# refusal is a logged reason rather than an exception swallowed three frames
# deep inside the session manager's constructor.
_SESSION_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9\-_]{0,99}\Z")

# `actorId` is `min 1, max 255, [a-zA-Z0-9][a-zA-Z0-9-_/]*(?::[a-zA-Z0-9-_/]+)*...`
# — the service permits `/`, and Grace must not. The session manager resolves a
# namespace with `str.format(actorId=...)` (read from its source), so a `/` in
# an actor id does not merely look odd: it changes the namespace *path*, nesting
# one household's records where another household's retrieval may span them.
# `case_id` arrives from a payload in the deployed runtime, so it is untrusted
# input in exactly the way rule-pack `program`/`state` are.
_ACTOR_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9\-_]{0,254}\Z")


def actor_id(case_id: str) -> str:
    """The memory actor for one household.

    One actor per case, so one family's history can never be retrieved into
    another family's run. The case id is already opaque (hard rule 9), so it is
    safe as an actor key in a way a household name would not be.
    """
    return case_id


def build_session_manager(
    case_id: str, session_id: str, memory_id: str | None = None
) -> AgentCoreMemorySessionManager | None:
    """Build the orchestrator's session manager, or `None` if unconfigured.

    Returns `None` rather than raising when no memory id is available: a local
    sweep and the fast suite must both run offline, and "no long-term recall this
    run" is a degraded mode, not an error. Memory is an enhancement to outreach
    quality — it is never consulted by the authority gate, so its absence cannot
    change a verdict.

    Note the environment read is `or`-guarded rather than
    `os.getenv(name, default)`: that form only defaults on *absence*, so
    `GRACE_MEMORY_ID=` (set but blank) would sail past it. Plan 2 already
    shipped that bug once in the store factory, where a blank `GRACE_STORE`
    bypassed the in-memory default.
    """
    memory_id = (memory_id or os.getenv("GRACE_MEMORY_ID") or "").strip()
    if not memory_id:
        return None

    # Identifier shape first, before anything reaches the network. Both are
    # fail-closed on the only axis available here: refusing costs recall, and
    # recall cannot satisfy a gate condition.
    if not _SESSION_ID.match(session_id):
        logger.warning(
            "session id is not one AgentCore Memory accepts "
            "(min 1, max 100, [a-zA-Z0-9][a-zA-Z0-9-_]*); continuing without recall"
        )
        return None
    actor = actor_id(case_id)
    if not _ACTOR_ID.match(actor):
        # Deliberately does not log the value: `case_id` is the one household
        # identifier that is safe to log, but a rejected one is by definition
        # not a case id, and could be anything a payload put there.
        logger.warning(
            "actor id is not a plain case id, so it could resolve a namespace "
            "outside this household; continuing without recall"
        )
        return None

    try:
        config = AgentCoreMemoryConfig(
            memory_id=memory_id,
            session_id=session_id,
            actor_id=actor,
            retrieval_config=RETRIEVAL_NAMESPACES,
            # Explicit rather than defaulted. `PersistenceMode` is FULL/NONE
            # only, so "write selectively" is not expressible here — see the
            # module docstring on why scope handles that instead. FULL is
            # required for anything to be remembered at all.
            persistence_mode=PersistenceMode.FULL,
        )
        return AgentCoreMemorySessionManager(config, region_name=naming.REGION)
    except Exception:  # noqa: BLE001 — recall is an enhancement, never a gate
        logger.warning("memory session manager unavailable; continuing without recall",
                       exc_info=True)
        return None
