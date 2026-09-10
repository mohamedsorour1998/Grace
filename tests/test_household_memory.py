"""PER-HOUSEHOLD RECALL, AND WHY IT CANNOT CHANGE A VERDICT.

A recertification cycle is annual, so a fact learned this cycle is only useful
if it survives eleven months. AgentCore Memory holds it. But recall is
*advisory* (CLAUDE.md hard rule 5): it may make Grace more cautious and may
never satisfy a gate condition, and `evaluate()` never sees it.

That polarity decides every error path here. Losing recall degrades the quality
of an outreach message; raising would fail a sweep. So both functions fail open
— returning `False` and `()` — and neither is ever consulted by the gate.

**The fake validates the way the service validates, and that is load-bearing
rather than tidy.** `MemoryClient.create_event` takes `(text, role)` tuples and
resolves the second element through `MessageRole`, so a `(role, text)` payload
raises `ValueError` *before* the network — which `remember_outcome` catches and
reports as a failed write. Every write would have failed silently, forever,
while a hand-written fake that appends whatever it is given passed green. So the
fake runs the write through the **real SDK method** with only its data-plane
client stubbed: no AWS call, and no second implementation of the contract to
drift out of step with the first.
"""

from __future__ import annotations

import inspect

from bedrock_agentcore.memory import MemoryClient

from grace.household_memory import MEMORY_NAMESPACE, recall_facts, remember_outcome


def _sdk_validated_event(**kwargs) -> dict:
    """Run a write through the real `MemoryClient.create_event`, network stubbed.

    `object.__new__` skips `__init__`, which is what would construct boto3
    clients — so this touches no AWS API and needs no credentials, while still
    executing the SDK's own argument validation.
    """

    class _StubDataPlane:
        def create_event(self, **params):
            return {"event": {"eventId": "e-1", **params}}

    client = object.__new__(MemoryClient)
    client.gmdp_client = _StubDataPlane()
    return client.create_event(**kwargs)


class _FakeMemory:
    """Records what it was asked, and can be told to fail like the service."""

    def __init__(self, *, records=None, raises=False):
        self.raises = raises
        self._records = records or []
        self.events: list[dict] = []
        self.queries: list[dict] = []

    def create_event(self, **kwargs):
        if self.raises:
            raise RuntimeError("service unavailable")
        # Validate first, record second: a payload the service would refuse must
        # not show up in `self.events` as though it had been written.
        event = _sdk_validated_event(**kwargs)
        self.events.append(kwargs)
        return event

    def retrieve_memories(self, **kwargs):
        if self.raises:
            raise RuntimeError("service unavailable")
        self.queries.append(kwargs)
        return self._records


def test_a_write_names_the_household_by_case_id_only():
    fake = _FakeMemory()
    assert remember_outcome("c-010", "escalated: proof_of_residency missing",
                            client=fake, memory_id="m-1") is True
    event = fake.events[0]
    assert event["actor_id"] == "c-010"
    assert event["memory_id"] == "m-1"
    # The payload is (text, role) tuples — that order is the SDK's, read off
    # `create_event`'s own body (`text, role = msg`), not off its parameter name.
    roles = {role for _, role in event["messages"]}
    assert roles <= {"ASSISTANT", "USER"}, event["messages"]
    assert "proof_of_residency" in " ".join(text for text, _ in event["messages"])

    # A case id carrying a path separator is not a case id. `recall_facts`
    # resolves the namespace with `str.format(actorId=...)`, so a `/` does not
    # merely look odd: it moves this household's records to a different
    # namespace *path*, where another household's retrieval may span them.
    # Refused before anything reaches the network, and nothing is written.
    assert remember_outcome("c-010/history", "x", client=fake, memory_id="m-1") is False
    # Long enough to be a legal actor id (255) and too long to be a session id
    # (100) — the service would refuse it three frames deep inside create_event.
    assert remember_outcome("c" * 200, "x", client=fake, memory_id="m-1") is False
    assert len(fake.events) == 1, "a refused case id must write nothing"


def test_a_write_failure_is_reported_not_raised():
    """**The polarity that matters.** A memory outage must not fail a sweep.
    Nothing downstream reads the return value to decide anything — it exists so
    the caller can log honestly rather than claim a write it did not confirm
    (hard rule 6)."""
    fake = _FakeMemory(raises=True)
    assert remember_outcome("c-010", "anything", client=fake, memory_id="m-1") is False


def test_recall_returns_the_remembered_text():
    fake = _FakeMemory(records=[
        {"content": {"text": "2026-10-01: escalated, proof_of_residency missing"}},
        {"content": {"text": "2026-09-01: family messaged in Spanish"}},
    ])
    facts = recall_facts("c-010", "renewal history", client=fake, memory_id="m-1")
    assert facts == (
        "2026-10-01: escalated, proof_of_residency missing",
        "2026-09-01: family messaged in Spanish",
    )
    assert fake.queries[0]["actor_id"] == "c-010"
    assert fake.queries[0]["namespace"] == "/facts/c-010"

    # The namespace is what actually scopes the read — the real client accepts
    # `actor_id` and ignores it (its own docstring marks it deprecated). So a
    # case id that could bend the namespace must never reach the query at all.
    assert recall_facts("c-010/history", "q", client=fake, memory_id="m-1") == ()
    assert len(fake.queries) == 1, "a refused case id must query nothing"


def test_recall_failure_returns_nothing_rather_than_raising():
    fake = _FakeMemory(raises=True)
    assert recall_facts("c-010", "anything", client=fake, memory_id="m-1") == ()


def test_recall_tolerates_a_record_shape_it_does_not_recognise():
    """The service's record shape is not something this module controls. A
    record with no text must be skipped, not crash the sweep — same reason as
    the fail-open above."""
    fake = _FakeMemory(records=[
        {"content": {"text": "kept"}},
        {"content": {}},
        {},
        {"content": {"text": ""}},
    ])
    assert recall_facts("c-010", "q", client=fake, memory_id="m-1") == ("kept",)


def test_no_memory_id_is_a_degraded_mode_not_an_error():
    """The fast suite and a local sweep both run offline. Absent configuration
    means 'no recall this run', which is a degraded mode — the same call
    `build_session_manager` already makes."""
    assert remember_outcome("c-010", "x", client=_FakeMemory(), memory_id="") is False
    assert recall_facts("c-010", "x", client=_FakeMemory(), memory_id="") == ()


def test_the_namespace_matches_what_the_memory_was_created_with():
    """A retrieval namespace that does not match the `namespaceTemplates` set at
    creation retrieves **nothing, silently** — Plan 2 recorded that as a live
    finding. This module and `grace/memory.py` must not drift apart."""
    from grace.memory import RETRIEVAL_NAMESPACES

    assert MEMORY_NAMESPACE in RETRIEVAL_NAMESPACES


def test_nothing_in_the_gate_can_reach_this_module():
    """Hard rule 5, structurally. Recall may make Grace more cautious and may
    never satisfy a gate condition — so `authority.py` must not import it, and
    neither may `steering.py`, which is the gate's only adapter."""
    import grace.authority
    import grace.steering

    for module in (grace.authority, grace.steering):
        source = inspect.getsource(module)
        assert "household_memory" not in source, (
            f"{module.__name__} reaches memory; a verdict must never depend on recall"
        )
