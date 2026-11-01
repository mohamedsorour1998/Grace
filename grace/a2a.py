"""A2A adapter: `message/send` in, one case run, one agent message out.

Deliberately thin, like `runtime_app.py`: everything of substance is in
`grace.entrypoint.process_case`, which never raises. This module decides only
what an A2A request may carry and how the outcome is returned.

**The request is an allowlist, and that is the security property.** An A2A
caller is another agent, not a caseworker. It may name a case and the date to
evaluate it on — `case_id` and `today` — and nothing else. Any other key is
refused, not dropped, so a caller cannot slip `caseworker_approved` (or a key a
future version of `process_case` starts reading) through this door. The case id
is restricted to characters that cannot forge a log line.

**It never raises.** A malformed request, or a case that fails, comes back as an
outcome with `status: "error"` and a reason — the JSON-RPC call itself succeeds,
as the AgentCore A2A contract asks. The run happens on a worker thread, because
a real case can take minutes and `/ping` is served by the same event loop.

**Nothing identifying reaches a log.** Log lines name the case id and an
exception type, never an outcome's reason or an exception's message (hard
rule 9); the text summary in the reply is `<case_id>: <status>`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from datetime import date
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    DataPart,
    Message,
    Part,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils.errors import ServerError
from a2a.utils.message import new_agent_parts_message

from grace.entrypoint import CaseOutcome, process_case

logger = logging.getLogger(__name__)

# The single source for the request shape: RequestError's message and the agent
# card's skill description both use it, so the two cannot disagree.
EXPECTED_SHAPE = '{"case_id": "<id>", "today": "YYYY-MM-DD"}'
ALLOWED_KEYS = ("case_id", "today")

# Letters, digits, `.`, `_`, `-`; starts with a letter or digit; at most 64.
# Broader than the dashboard's `c-NNN`, because a deployer's own households file
# may use its own ids — but no whitespace or control character, so a case id
# can never inject a line into a log, and no leading `.` (no `../`).
_CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class RequestError(ValueError):
    """An A2A request Grace will not run. The message says what to send instead
    and never echoes the offending value back."""

    def __init__(self, problem: str) -> None:
        super().__init__(
            f"{problem}. Send a data part (or a text part holding JSON) shaped "
            f"{EXPECTED_SHAPE}, with exactly the keys {' and '.join(ALLOWED_KEYS)}."
        )


def parse_request(message: Message | None) -> dict[str, str]:
    """The payload `process_case` takes, from an A2A message — or RequestError.

    The first data part wins; failing that, a text part whose whole text is a
    JSON object."""
    parts = [p.root for p in (message.parts if message is not None else [])]
    data: Any = next((p.data for p in parts if isinstance(p, DataPart)), None)
    if data is None:
        text = next((p.text for p in parts if isinstance(p, TextPart)), None)
        if text is None:
            raise RequestError("The message carries no data part and no text")
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
            # Not JSONDecodeError alone: past 4300 digits an integer raises a
            # plain ValueError, and deep nesting raises RecursionError. Catching
            # only the error seen first is how `check_window` failed open once.
            raise RequestError("The text is not JSON Grace can read") from None
    if not isinstance(data, dict):
        raise RequestError("The request is not a JSON object")
    if set(data) != set(ALLOWED_KEYS):
        raise RequestError("The request must carry exactly case_id and today, and nothing else")

    case_id, today = data["case_id"], data["today"]
    if not isinstance(case_id, str) or not _CASE_ID.fullmatch(case_id):
        raise RequestError(
            "case_id must be 1-64 letters, digits, '.', '_' or '-', starting with a letter or digit"
        )
    if not isinstance(today, str) or not _ISO_DATE.fullmatch(today):
        raise RequestError("today must be a date written YYYY-MM-DD")
    try:
        date.fromisoformat(today)
    except ValueError:
        raise RequestError("today must be a real calendar date") from None
    return {"case_id": case_id, "today": today}


class WithholdRequestContent(logging.Filter):
    """Replace an A2A SDK log record's content with a fixed sentence.

    Hard rule 9. The SDK logs every request body at DEBUG and a validation
    failure's input at WARNING/ERROR (measured 2026-11-01), so whatever a caller
    put in a request — a name, a phone number — would be written to the log.
    The record keeps its level, its logger name, and its exception type, which
    is enough to know something happened; its message, arguments, and traceback
    (whose exception text can quote the request too) are dropped.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc_type = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else None
        record.msg = (
            f"{record.levelname} from the A2A SDK; detail withheld because it can quote the request"
            + (f" ({exc_type})" if exc_type else "")
        )
        record.args = None
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def withhold_sdk_request_content() -> int:
    """Attach `WithholdRequestContent` to every `a2a` logger that exists now.

    On the loggers themselves rather than on a handler, so it holds for any
    handler attached later (a test's capture handler, a runtime's log shipper).
    A logger filter applies only to records that logger creates, so it goes on
    each one; call this after the SDK modules are imported. Returns how many
    loggers carry it, so a caller can tell it found some."""
    names = ["a2a", *(n for n in list(logging.root.manager.loggerDict) if n.startswith("a2a."))]
    for name in names:
        target = logging.getLogger(name)
        if not any(isinstance(f, WithholdRequestContent) for f in target.filters):
            target.addFilter(WithholdRequestContent())
    return len(names)


class CaseExecutor(AgentExecutor):
    """Runs one case per `message/send` and answers with one agent message.

    `process` is `process_case` in production and a fake in tests.
    `on_outcome`, when given, sees every outcome of a well-formed request
    (including an `error` one), once, after the case has run: a hook for an
    embedding application to observe or count outcomes. A refused request
    never reaches it, and its failure never costs the caller the reply.
    """

    def __init__(
        self,
        process: Callable[[dict], CaseOutcome] = process_case,
        on_outcome: Callable[[CaseOutcome], None] | None = None,
    ) -> None:
        self._process = process
        self._on_outcome = on_outcome

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            payload = parse_request(context.message)
        except RequestError as exc:
            await event_queue.enqueue_event(
                _reply({"status": "error", "case_id": "", "reason": str(exc)}, context)
            )
            return
        except Exception as exc:  # noqa: BLE001 — never raise out of execute
            # Whatever broke, the caller still gets the shape it should send; the
            # exception's own text is not echoed (it may quote the request).
            logger.error("A2A request could not be parsed (%s)", type(exc).__name__)
            reason = str(RequestError("The request could not be read"))
            await event_queue.enqueue_event(_reply({"status": "error", "case_id": "", "reason": reason}, context))
            return
        outcome = await self._run(payload)
        self._observe(outcome)
        await event_queue.enqueue_event(_reply(outcome, context))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # A case run is one synchronous step with nothing to stop midway.
        raise ServerError(error=UnsupportedOperationError())

    async def _run(self, payload: dict[str, str]) -> CaseOutcome:
        case_id = payload["case_id"]
        try:
            outcome = await asyncio.to_thread(self._process, payload)
        except Exception as exc:  # noqa: BLE001 — answered, not swallowed
            logger.error("case %s: processing raised %s", case_id, type(exc).__name__)
            outcome = None
        if not isinstance(outcome, dict):
            # Hard rule 6: no claim about what did or did not happen.
            return {
                "status": "error",
                "case_id": case_id,
                "reason": "Grace could not process this case. Check its ledger before acting on it.",
            }
        return dict(outcome)

    def _observe(self, outcome: CaseOutcome) -> None:
        if self._on_outcome is None:
            return
        try:
            self._on_outcome(outcome)
        except Exception as exc:  # noqa: BLE001 — the caller still gets its reply
            logger.warning(
                "case %s: outcome observer failed (%s)", outcome.get("case_id", ""), type(exc).__name__
            )


def _reply(outcome: CaseOutcome, context: RequestContext) -> Message:
    summary = f"{outcome.get('case_id') or 'request'}: {outcome.get('status', 'error')}"
    return new_agent_parts_message(
        [Part(root=DataPart(data=dict(outcome))), Part(root=TextPart(text=summary))],
        context_id=context.context_id,
    )


def _version() -> str:
    try:
        return version("grace")
    except PackageNotFoundError:  # running from a source tree that was never installed
        return "0.0.0"


def build_agent_card(url: str) -> AgentCard:
    """Grace's A2A agent card: one skill, no streaming."""
    return AgentCard(
        name="Grace",
        description=(
            "Checks one benefit-renewal case against its rule pack. Grace files the renewal "
            "when every deadline and required document is in order, and otherwise escalates "
            "the case to a human caseworker with a typed reason."
        ),
        url=url,
        version=_version(),
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json", "text/plain"],
        skills=[
            AgentSkill(
                id="check-case",
                name="Check a renewal case",
                description=(
                    f"Run Grace on one case. Request: {EXPECTED_SHAPE}. Reply: the case outcome, "
                    "with status acted, escalated, or error and the reason."
                ),
                tags=["benefits", "renewal", "escalation"],
                examples=[json.dumps({"case_id": "c-001", "today": "2026-10-01"})],
            )
        ],
    )
