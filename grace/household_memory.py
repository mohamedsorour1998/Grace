"""Per-household facts that outlive a sweep.

**What this is for.** A recertification cycle is annual. "Income was verified
by pay stubs last cycle", "this family reads Spanish", "the last renewal
escalated on a missing proof of residency" — all of it has to survive eleven
months between contacts, and none of it belongs in the case record, which is
what the *state* would hold. AgentCore Memory holds it.

**Why the data-plane client rather than a session manager.** Verified against
the installed `bedrock-agentcore`: `AgentCoreMemorySessionManager.create_multi_agent`
raises `NotImplementedError("MultiAgent is not implemented for this repository")`.
`GraphBuilder.set_session_manager` exists, so attaching one to Grace's Graph is
syntactically possible and would raise on the first sweep. `grace/memory.py`'s
docstring reaches the right conclusion — no session manager inside the graph —
by the wrong route; the real reason is this, not the `ValueError` it cites.
`create_event` / `retrieve_memories` are the supported path for writing facts an
extraction strategy can process.

**Everything here fails open, and that is the opposite of Grace's usual
polarity.** The gate fails *closed* because an unverified case must reach a
human. This is not a verification question. Recall is advisory (hard rule 5): it
may make Grace more cautious and may never satisfy a gate condition, and
`evaluate()` never sees it. So a memory outage degrades the quality of an
outreach message and must never fail a sweep — `tests/test_household_memory.py`
pins that `authority.py` and `steering.py` cannot even import this module.

**The actor id is the case id, and nothing else.** It is already opaque (hard
rule 9), which a household name would not be, and one actor per case means one
family's history can never be retrieved into another family's run.
"""

from __future__ import annotations

import logging
import os

# `_ACTOR_ID` and `_SESSION_ID` are imported rather than restated. They encode
# what the live API model accepts, and a second copy of a validation rule drifts
# out of step with the first — the same reason `grace/tools/read.py` imports
# `_most_recent` from `grace.authority` instead of reimplementing it.
from grace.memory import _ACTOR_ID, _SESSION_ID, actor_id
from infra import naming

logger = logging.getLogger(__name__)

# Must match the `namespaceTemplates` set at memory creation. A mismatch
# retrieves nothing *silently* rather than raising — Plan 2 measured that on the
# live service. `tests/test_household_memory.py` pins this against
# `grace/memory.py`'s `RETRIEVAL_NAMESPACES` so the two cannot drift.
MEMORY_NAMESPACE = "/facts/{actorId}"

# One session per household's remembered history. The service constrains
# `sessionId` to `[a-zA-Z0-9][a-zA-Z0-9-_]*` — no dots, no colons — so a case id
# with a fixed prefix is safe where "case id plus an ISO timestamp" would be
# refused.
_SESSION_PREFIX = "history-"


def _client(client):
    """The data-plane client, or the injected fake.

    Imported inside the function so the fast suite never needs the package's
    boto3 client construction, and so a test can pass a fake without patching an
    import.
    """
    if client is not None:
        return client
    from bedrock_agentcore.memory import MemoryClient

    return MemoryClient(region_name=naming.REGION)


def _memory_id(memory_id: str | None) -> str:
    # `or`-guarded rather than `os.getenv(name, default)`: that form only
    # defaults on *absence*, so `GRACE_MEMORY_ID=` (set but blank) would sail
    # past it. Plan 2 shipped that exact bug once in the store factory.
    return (memory_id if memory_id is not None else os.getenv("GRACE_MEMORY_ID") or "").strip()


def _scoped_ids(case_id: str) -> tuple[str, str] | None:
    """The (actor, session) this household writes and reads under, or `None`.

    `case_id` arrives from a payload in the deployed runtime, so it is untrusted
    in exactly the way rule-pack `program`/`state` are. The service *permits*
    `/` in an actor id and Grace must not: `recall_facts` resolves the namespace
    with `str.format(actorId=...)` right here in this module, so a `/` does not
    merely look odd — it moves the read to a different namespace *path*, nesting
    one household's records where another household's retrieval may span them.

    Checked before anything reaches the network, so a refusal is a named local
    reason rather than a `ValidationException` swallowed three frames deep
    inside the SDK. Refusing costs recall, and recall cannot satisfy a gate
    condition, so this is fail-closed on the only axis available here.
    """
    actor = actor_id(case_id)
    session = f"{_SESSION_PREFIX}{case_id}"
    if not _ACTOR_ID.match(actor) or not _SESSION_ID.match(session):
        # Deliberately does not log the value: `case_id` is the one household
        # identifier that is safe to log, but a rejected one is by definition
        # not a case id, and could be anything a payload put there.
        logger.warning(
            "case id is not one AgentCore Memory accepts, so it could resolve a "
            "namespace outside this household; continuing without recall"
        )
        return None
    return actor, session


def remember_outcome(
    case_id: str, summary: str, *, client=None, memory_id: str | None = None
) -> bool:
    """Write one fact about this household. `True` only on a confirmed write.

    The return value exists so a caller can log honestly rather than claim a
    write it did not confirm — hard rule 6 applied to an observability path.
    Nothing branches on it to decide anything about a family. It is `True` only
    when the service handed back an event id, not merely when the call did not
    raise: "the API returned" and "the write landed" are different claims, and
    Plan 2 already paid for treating them as one.

    `summary` must carry no household identity: it is written to a service and
    read back into a model's context, which is exactly the path a surname took
    to CloudWatch. Callers pass case-level facts (a date, a reason code, a
    document id), never a name or a phone number.
    """
    resolved = _memory_id(memory_id)
    if not resolved or not summary.strip():
        return False
    scoped = _scoped_ids(case_id)
    if scoped is None:
        return False
    actor, session = scoped
    try:
        event = _client(client).create_event(
            memory_id=resolved,
            actor_id=actor,
            session_id=session,
            # `create_event` takes **(text, role)** tuples — read off its body
            # (`text, role = msg`), not off the parameter name, because the
            # reverse order is not a type error: it resolves the *summary* as a
            # role, raises `ValueError` before the network, and lands in the
            # `except` below as a silently failed write. ASSISTANT because this
            # is Grace's own record of what it concluded, not something a family
            # said — the extraction strategy reads the role, and mislabelling
            # the speaker would make a later reader think a family reported it.
            messages=[(summary, "ASSISTANT")],
        )
        return bool(_event_id(event))
    except Exception:  # noqa: BLE001 — see the module docstring: recall is advisory
        logger.warning("could not record a household fact; continuing", exc_info=True)
        return False


def _event_id(event: object) -> str:
    """The id the service assigned, or `""` — the proof a write actually landed.

    Two shapes are accepted because the SDK unwraps one from the other
    (`create_event` returns `response["event"]`, and `eventId` is a *required*
    member of `Event` in the service model). Reading only the unwrapped shape
    would report a real write as unwritten if that unwrapping ever moved, which
    costs recall; inventing a `True` without an id would breach hard rule 6,
    which costs more. Hence strict about the id, tolerant about the wrapper.
    """
    if not isinstance(event, dict):
        return ""
    inner = event.get("event")
    if isinstance(inner, dict) and isinstance(inner.get("eventId"), str):
        return inner["eventId"]
    return event["eventId"] if isinstance(event.get("eventId"), str) else ""


def recall_facts(
    case_id: str,
    query: str,
    *,
    client=None,
    memory_id: str | None = None,
    top_k: int = 5,
) -> tuple[str, ...]:
    """What Grace remembers about this household. `()` if unknown.

    Records come back in the order the service returned them, which its own API
    model documents as **ordered by relevance** — not by time. Do not describe
    the first element to a caseworker as "the most recent"; `top_k` has already
    truncated by score, so re-sorting the survivors by date would name a newest
    among an arbitrary subset.

    An empty tuple means "nothing to add", never "this family has no history" —
    the caller must not present absence of recall as a fact about the family.
    """
    resolved = _memory_id(memory_id)
    if not resolved:
        return ()
    scoped = _scoped_ids(case_id)
    if scoped is None:
        return ()
    actor, _session = scoped
    try:
        records = _client(client).retrieve_memories(
            memory_id=resolved,
            # The namespace is what actually scopes this read. The real client
            # accepts `actor_id` and never uses it — its own docstring marks the
            # parameter deprecated — so it is passed for the audit trail and the
            # namespace is the load-bearing argument.
            namespace=MEMORY_NAMESPACE.format(actorId=actor),
            query=query,
            actor_id=actor,
            top_k=top_k,
        )
    except Exception:  # noqa: BLE001 — losing recall cannot change a verdict
        logger.warning("could not recall household facts; continuing", exc_info=True)
        return ()

    return tuple(text for text in map(_record_text, records or ()) if text)


def _record_text(record: object) -> str:
    """The fact text in one memory record, or `""` if there is not one.

    The record shape belongs to the service, not to this module. `content.text`
    is the shape botocore's `bedrock-agentcore` model defines and the SDK's own
    example reads — but `MemoryContent` is declared a **union**, so a future
    member would yield records carrying `content` with no `text`, and a
    mismatch here fails *silently*: `()` is indistinguishable from "no history".
    So two neighbouring spellings are accepted alongside it — a flattened
    string, and a top-level `text` (the shape this same SDK builds for message
    summaries). Beyond those it stops guessing and skips the record rather than
    crashing the sweep, for the same reason every other path here fails open.
    """
    if isinstance(record, str):
        return record.strip()
    if not isinstance(record, dict):
        return ""
    content = record.get("content")
    if isinstance(content, str):
        candidate = content
    elif isinstance(content, dict):
        candidate = content.get("text")
    else:
        candidate = record.get("text")
    return candidate.strip() if isinstance(candidate, str) else ""
