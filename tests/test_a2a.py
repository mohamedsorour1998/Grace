"""The A2A executor: a `message/send` becomes exactly the payload the HTTP runtime takes.

An A2A caller is another agent, not a caseworker, so the request shape is an
allowlist: `case_id` and `today`, and nothing else — `caseworker_approved`
included. `process` is a fake in every test here; nothing calls Bedrock.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from importlib.metadata import version

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    DataPart,
    Message,
    MessageSendParams,
    Part,
    Role,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils.errors import ServerError

from grace.a2a import CaseExecutor, RequestError, build_agent_card, parse_request

GOOD = {"case_id": "c-001", "today": "2026-10-01"}


def message(*, data: dict | None = None, text: str | None = None) -> Message:
    parts: list[Part] = []
    if data is not None:
        parts.append(Part(root=DataPart(data=data)))
    if text is not None:
        parts.append(Part(root=TextPart(text=text)))
    return Message(role=Role.user, parts=parts, message_id="m-1")


async def run(executor: CaseExecutor, msg: Message):
    queue = EventQueue()
    await executor.execute(RequestContext(request=MessageSendParams(message=msg)), queue)
    return await queue.dequeue_event(no_wait=True)


def reply_data(reply: Message) -> dict:
    return next(p.root.data for p in reply.parts if isinstance(p.root, DataPart))


def reply_text(reply: Message) -> str:
    return next(p.root.text for p in reply.parts if isinstance(p.root, TextPart))


# --- parse_request -----------------------------------------------------------


def test_a_data_part_becomes_the_payload():
    assert parse_request(message(data=GOOD)) == GOOD


def test_a_text_part_holding_the_json_is_accepted():
    assert parse_request(message(text=json.dumps(GOOD))) == GOOD


def test_a_data_part_wins_over_text():
    assert parse_request(message(data=GOOD, text="ignored prose")) == GOOD


def test_a_text_only_message_explains_the_expected_shape():
    with pytest.raises(RequestError, match=r'"case_id".*"today"'):
        parse_request(message(text="please check my case"))


def test_an_empty_message_explains_the_expected_shape():
    with pytest.raises(RequestError, match=r'"case_id".*"today"'):
        parse_request(message())


def test_json_that_is_not_an_object_is_refused():
    with pytest.raises(RequestError):
        parse_request(message(text="[1, 2]"))


def test_an_unknown_key_is_refused():
    # An A2A caller is not a caseworker; the approval flag must not ride along.
    with pytest.raises(RequestError, match="case_id.*today"):
        parse_request(message(data={**GOOD, "caseworker_approved": True}))


def test_a_missing_key_is_refused():
    with pytest.raises(RequestError):
        parse_request(message(data={"case_id": "c-001"}))


def test_a_bad_date_is_refused():
    for today in ("2026-13-01", "tomorrow", 20261001, ""):
        with pytest.raises(RequestError):
            parse_request(message(data={**GOOD, "today": today}))


def test_a_bad_case_id_is_refused():
    for case_id in (123, "", " ", "c-001\nFAKE LOG LINE", "x" * 65, "../c-001"):
        with pytest.raises(RequestError):
            parse_request(message(data={**GOOD, "case_id": case_id}))


# --- CaseExecutor --------------------------------------------------------------


async def test_the_outcome_is_the_reply_and_reported_once():
    seen: list[dict] = []
    ex = CaseExecutor(process=lambda p: {"status": "acted", "case_id": p["case_id"], "filed": True},
                      on_outcome=seen.append)
    reply = await run(ex, message(data=GOOD))
    assert isinstance(reply, Message) and reply.role == Role.agent
    assert reply_data(reply) == {"status": "acted", "case_id": "c-001", "filed": True}
    assert reply_text(reply) == "c-001: acted"
    assert seen == [reply_data(reply)]


async def test_the_payload_reaching_process_is_exactly_the_allowlist():
    received: list[dict] = []
    ex = CaseExecutor(process=lambda p: received.append(p) or {"status": "acted", "case_id": p["case_id"]})
    await run(ex, message(data=GOOD))
    assert received == [GOOD]


async def test_a_refused_request_is_answered_and_not_reported():
    seen: list[dict] = []
    called: list[dict] = []
    ex = CaseExecutor(process=lambda p: called.append(p) or {}, on_outcome=seen.append)
    reply = await run(ex, message(text="please check my case"))
    data = reply_data(reply)
    assert data["status"] == "error"
    assert '"case_id"' in data["reason"]
    assert called == [] and seen == []


async def test_the_executor_never_raises():
    seen: list[dict] = []

    def explode(_payload):
        raise ZeroDivisionError("division by zero in the household's record")

    reply = await run(CaseExecutor(process=explode, on_outcome=seen.append), message(data=GOOD))
    data = reply_data(reply)
    assert data["status"] == "error" and data["case_id"] == "c-001"
    assert "household" not in data["reason"]  # the exception's text is not echoed
    assert seen == [data]


async def test_a_failing_observer_never_loses_the_outcome(caplog):
    outcome = {"status": "escalated", "case_id": "c-001", "reason": "The Example Household reports"}

    def observer(_):
        raise RuntimeError("observer broke")

    with caplog.at_level(logging.WARNING):
        reply = await run(CaseExecutor(process=lambda p: dict(outcome), on_outcome=observer), message(data=GOOD))
    assert reply_data(reply) == outcome
    assert "c-001" in caplog.text
    assert "Example" not in caplog.text


async def test_a_long_case_does_not_block_the_event_loop():
    # A real case can run for minutes; /ping is served by the same event loop.
    def slow(payload):
        time.sleep(0.5)
        return {"status": "acted", "case_id": payload["case_id"]}

    ticks: list[float] = []

    async def ticker():
        await asyncio.sleep(0.05)
        ticks.append(time.monotonic())

    start = time.monotonic()
    task = asyncio.create_task(ticker())
    await run(CaseExecutor(process=slow), message(data=GOOD))
    done = time.monotonic()
    await task
    assert ticks, "the event loop never ran the ticker"
    assert ticks[0] - start < 0.3
    assert done - ticks[0] > 0.3


async def test_cancel_is_unsupported():
    queue = EventQueue()
    with pytest.raises(ServerError) as exc:
        await CaseExecutor().cancel(RequestContext(request=MessageSendParams(message=message(data=GOOD))), queue)
    assert isinstance(exc.value.error, UnsupportedOperationError)


# --- build_agent_card ----------------------------------------------------------


def test_the_card_names_grace_and_one_skill():
    card = build_agent_card("http://example.test/")
    assert card.name == "Grace"
    assert card.url == "http://example.test/"
    assert card.version == version("grace")
    assert card.capabilities.streaming is False
    assert [s.id for s in card.skills] == ["check-case"]
    example = json.loads(card.skills[0].examples[0])
    assert set(example) == {"case_id", "today"}


# --- Pathological JSON (review, 2026-11-01) ------------------------------------


def test_deeply_nested_json_is_refused_not_raised():
    with pytest.raises(RequestError):
        parse_request(message(text="[" * 100_000 + "]" * 100_000))


def test_an_integer_too_long_to_parse_is_refused_not_raised():
    # json.loads raises a plain ValueError past 4300 digits, not JSONDecodeError.
    with pytest.raises(RequestError):
        parse_request(message(text="1" * 5000))


async def test_the_executor_answers_even_if_parsing_breaks(monkeypatch):
    import grace.a2a as module

    def broken(_message):
        raise MemoryError("parser ran out of room")

    monkeypatch.setattr(module, "parse_request", broken)
    called: list[dict] = []
    reply = await run(CaseExecutor(process=lambda p: called.append(p) or {}), message(data=GOOD))
    data = reply_data(reply)
    assert data["status"] == "error" and '"case_id"' in data["reason"]
    assert "room" not in data["reason"]
    assert called == []
