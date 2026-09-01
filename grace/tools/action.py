"""Action tools. Every one of these changes state and is reachable only
through the authority gate in `grace/steering.py` (Task 5).

Two properties hold here as strictly as in the read tools:

- **No identity argument.** `submit_renewal` takes nothing; the two tools that
  do take an argument take *content* (`body`, `question`), never identity. The
  household's phone number is read from the bound case, so a model cannot send
  a family's details to a number it chose.
- **`escalate_to_caseworker` is never gated.** Handing a decision to a human is
  always allowed (CLAUDE.md hard rule 7), and it must work on a case in *any*
  state — including one whose rule pack will not load, which is precisely when
  a human is most needed. Any precondition here would be a way to trap a case
  with no exit.

Nothing here reports success it has not confirmed: the ledger entry is written
*after* the underlying operation returns, so a channel that raises produces no
ledger row and no success string (CLAUDE.md hard rule 6).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from strands import tool

from grace.cases.models import LedgerDetailValue, LedgerEntry
from grace.cases.store import CaseStore


class Channel(Protocol):
    def send(self, phone: str, body: str) -> str: ...


class TranscriptChannel:
    """Records messages instead of sending them.

    This is the always-works path: the dashboard renders the transcript, so the
    demo never depends on SMS provisioning (there are zero origination numbers
    on the account — see CLAUDE.md). The SNS implementation lands in Plan 2
    behind this same interface.
    """

    def __init__(self) -> None:
        self._sent: list[tuple[str, str]] = []

    @property
    def sent(self) -> list[tuple[str, str]]:
        # A copy, for the same reason `InMemoryCaseStore.ledger` returns one:
        # the transcript is the record of what was actually said to a family, so
        # a caller reading it must not be able to append to or truncate it.
        return list(self._sent)

    def send(self, phone: str, body: str) -> str:
        self._sent.append((phone, body))
        return f"recorded:{len(self._sent)}"


def make_action_tools(store: CaseStore, case_id: str, channel: Channel) -> list:
    """Build action tools bound to one case."""

    def _log(kind: str, **detail: LedgerDetailValue) -> None:
        # Values must be JSON-safe scalars: `LedgerEntry` type-checks them at
        # construction because Plan 2 writes this straight to DynamoDB, so a
        # `date` or a dataclass here would fail at the storage boundary instead
        # of here. Hence `cert_end.isoformat()` at the call site below — that is
        # deliberate, not incidental.
        #
        # The household's phone is deliberately never logged. The ledger records
        # *that* a family was contacted and with what text; the number lives on
        # the household record and does not need duplicating into an audit row
        # that a dashboard and Task 8's evals both read.
        store.append_ledger(
            LedgerEntry(
                case_id=case_id,
                at=datetime.now(timezone.utc),
                kind=kind,
                detail=detail,
            )
        )

    @tool
    def submit_renewal() -> str:
        """File the renewal for the current case.

        No arguments needed — identity is determined from the session. This
        tool only executes if the authority gate has already passed.
        """
        c = store.get(case_id)
        _log("renewal_submitted", program=c.program, cert_end=c.cert_end.isoformat())
        return f"Renewal filed for {c.case_id} ({c.program})."

    @tool
    def send_family_message(body: str) -> str:
        """Send a message to the family about a missing document.

        Args:
            body: The message text, already in the family's language.
        """
        c = store.get(case_id)
        # Send first, log second. A channel that raises must leave no ledger
        # entry claiming the family was contacted — hard rule 6 is about not
        # reporting an action Grace did not confirm, and a ledger row is a
        # stronger claim than a sentence.
        ref = str(channel.send(c.household.phone, body))
        # `Channel` is a Protocol, not @runtime_checkable, so its `-> str`
        # return annotation is not enforced anywhere. A real SNS
        # implementation naturally returns a boto3 response shape (e.g. a
        # dict with "MessageId"), not a bare string. Without the str() here,
        # that dict would reach LedgerEntry, which rejects non-scalar values
        # and raises AFTER the message was already sent — the family gets
        # contacted and the audit trail shows nothing happened, which is the
        # exact inverse of what the send-then-log ordering above exists to
        # prevent.
        _log("family_message_sent", ref=ref, body=body)
        return f"Message sent ({ref})."

    @tool
    def escalate_to_caseworker(question: str) -> str:
        """Hand this case to a human caseworker with a specific question.

        Always permitted — escalating is never blocked.

        Args:
            question: The precise decision the caseworker must make.
        """
        _log("escalated", question=question)
        return f"Escalated: {question}"

    return [submit_renewal, send_family_message, escalate_to_caseworker]
