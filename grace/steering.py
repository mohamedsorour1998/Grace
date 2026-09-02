"""The authority gate, wired into the Strands agent loop.

This is the only adapter between the pure decision logic in
grace/authority.py and the framework. It answers one question before every
state-changing tool call: may Grace do this alone?

Three outcomes:
  Proceed  — the gate passed, or the tool changes nothing
  Guide    — Grace skipped a step; correctable, so tell it what to do
  Interrupt— a human must decide; pause the run

`Interrupt` is only valid from `steer_before_tool`. `steer_after_model` can
return Proceed or Guide only, because the model has already responded.

**`steer_before_tool` must never raise.** The SDK's own
`provide_tool_steering_guidance` wraps this method in
`except Exception: return`, logs at debug level, and leaves `cancel_tool`
unset — so an exception escaping from here does not fail the run, it lets the
tool execute *ungated*. That is fail-open, and it is the precise failure this
module exists to prevent. It is verified by
`test_a_raising_handler_would_let_the_tool_execute`, which pins the SDK
behaviour so an upgrade that changes it is noticed. Hence every fallible call
below — including `evaluate` itself, which can raise `ValueError`, `TypeError`,
or `OverflowError` from a pack that loaded cleanly — sits inside one
`except Exception` that returns an `Interrupt`.

**`submit_renewal` and `send_family_message` are gated on two different
questions, not the same verdict.** `submit_renewal` needs a clean case: every
`evaluate()` condition must pass, because filing commits the family to the
figures on record. `send_family_message` needs something narrower: that the
*only* problem is paperwork Grace can chase by asking the family for it. A
case that is also off on income, size, or a source conflict must escalate
instead of texting the family — eligibility itself is in doubt, and outreach
would not fix that. Gating outreach on a full clean verdict would block the
one thing Grace exists to do automatically (the missing-document fixture,
`c-010`, would escalate instead of being chased); gating it on nothing but
"a document problem exists" would let outreach fire alongside an unrelated
income problem a human should see first. `DOCUMENT_ONLY_CODES` below is the
narrower predicate.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from grace.authority import ACTION_TOOLS, evaluate
from grace.cases.store import CaseStore
from grace.rules.pack import load_pack
from grace.vendored_actions import (
    Guide,
    Interrupt,
    LedgerProvider,
    Proceed,
    SteeringHandler,
    ToolSteeringAction,
)

# Reason codes that mean "the only problem is paperwork Grace itself can chase
# by asking the family for it." `send_family_message` is gated on this
# narrower set rather than on a clean `evaluate()` verdict — see the module
# docstring's note on outreach vs. filing.
DOCUMENT_ONLY_CODES = frozenset({"missing_document", "stale_document"})

# Reads that must have happened before an action is allowed. Grace must look
# before it acts — this is enforced, not requested.
#
# Ordered tuples, not sets: the Guide message below is built from these, and a
# set would order the missing reads by hash. That is stable within a process
# and not across processes, which is the kind of nondeterminism that shows up
# only once, in a recorded demo.
PREREQUISITES: dict[str, tuple[str, ...]] = {
    "submit_renewal": ("read_case", "check_window", "list_documents"),
    "send_family_message": ("read_case", "list_documents"),
}

# Escalating is always permitted (CLAUDE.md hard rule 7) — including on a case
# that will not load, which is exactly when a human is most needed. Checked
# before anything else in `steer_before_tool` so no precondition, not even
# "the case exists", can trap a case with no exit.
ALWAYS_ALLOWED = frozenset({"escalate_to_caseworker"})

# AgentCore Gateway exposes every tool as `${target_name}___${tool_name}`.
TARGET_PREFIX_SEPARATOR = "___"


def _bare_tool_name(name: str) -> str:
    """Strip the Gateway target prefix from an MCP tool name.

    Without this, `"grace-actions___submit_renewal" not in ACTION_TOOLS` is
    `True`, so the gate would classify every gateway-provided action tool as
    read-only and let it through unchecked — the exact failure this design
    exists to prevent (Appendix C.1).

    Falls back to the original name when stripping would leave nothing (a
    trailing separator, `"submit_renewal___"`). An empty name misses
    `ACTION_TOOLS` and would be treated as a read, so the degenerate case must
    never produce `""`.
    """
    _, _, bare = name.rpartition(TARGET_PREFIX_SEPARATOR)
    return bare or name


class AuthorityGate(SteeringHandler):
    """Deterministic gate on every state-changing tool call.

    One instance per case, matching `make_read_tools`/`make_action_tools`:
    `case_id` is bound at construction from the authenticated session and is
    never read from `tool_use["input"]`, which the model controls. `_seen` is
    per-instance for the same reason — reads observed on one household must
    never satisfy another household's prerequisites.
    """

    name = "authority-gate"

    def __init__(self, store: CaseStore, case_id: str, today: date) -> None:
        super().__init__(context_providers=[LedgerProvider()])
        self._store = store
        self._case_id = case_id
        # Passed in, never `date.today()`: fixture c-002 goes `closed` on
        # 2026-10-31, so a clock read here would turn the 9-act/3-escalate
        # demo into 8/4 on that date.
        self._today = today
        # Reads observed in this run. Tracked here rather than read from the
        # SDK's `LedgerProvider` context so the gate works even when that
        # context is empty — a gate whose prerequisite check silently depends
        # on an unpopulated provider would pass everything.
        self._seen: set[str] = set()

    async def steer_before_tool(
        self, *, agent: Any = None, tool_use: Any = None, **kwargs: Any
    ) -> ToolSteeringAction:
        # `tool_use` is a TypedDict, so at runtime it is a plain dict with no
        # required keys — a malformed one has no "name" at all. An unnameable
        # tool call cannot be classified, so it must not fall through to the
        # read-only branch below.
        raw = (tool_use or {}).get("name")
        if not isinstance(raw, str) or not raw:
            return Interrupt(
                reason=(
                    "A tool call arrived without a usable name, so the "
                    "authority gate cannot classify it. A caseworker must review."
                )
            )

        name = _bare_tool_name(raw)

        if name in ALWAYS_ALLOWED:
            return Proceed(reason="Escalating to a human is always permitted")

        # Reads change nothing. Record and allow. Recorded under the *bare*
        # name so a gateway-provided `grace-reads___list_documents` satisfies
        # the `list_documents` prerequisite; recording the prefixed name would
        # make the gate Guide forever on a case where every read did happen.
        if name not in ACTION_TOOLS:
            self._seen.add(name)
            return Proceed(reason="Read-only tool")

        # A state-changing tool with no declared prerequisites is one the gate
        # does not know how to evaluate. Fail closed.
        required = PREREQUISITES.get(name)
        if required is None:
            return Interrupt(
                reason=(
                    f"'{name}' changes state but has no gate policy. "
                    "A caseworker must approve this explicitly."
                )
            )

        # Iterates `required` (a declared tuple), not `self._seen` (a set), so
        # the message is byte-identical across runs.
        missing = [r for r in required if r not in self._seen]
        if missing:
            return Guide(
                reason=(
                    f"Before calling {name} you must first call: "
                    f"{', '.join(missing)}. Do that now, then retry."
                )
            )

        # One try around the load *and* the evaluation. `evaluate` is inside it
        # deliberately: a pack that passes `load_pack`'s validation can still
        # make the date arithmetic inside `evaluate` raise (`cert_end` near
        # `date.max` gives `OverflowError`), and a structurally invalid pack
        # built directly gives `ValueError` or `TypeError`. Any of those
        # escaping this method would be swallowed by the SDK and the tool would
        # run ungated — see the module docstring.
        try:
            case = self._store.get(self._case_id)
            pack = load_pack(case.program, case.state)
            result = evaluate(case, self._today, pack)
        except Exception as exc:  # noqa: BLE001 — deliberate: fail closed
            return Interrupt(
                reason=(
                    f"Verification error: could not verify case "
                    f"{self._case_id} ({exc}). A caseworker must review."
                )
            )

        if result.decision == "act":
            return Proceed(reason="Authority gate passed: case is unambiguous")

        if name == "send_family_message" and all(
            r.code in DOCUMENT_ONLY_CODES for r in result.reasons
        ):
            # The only problem is paperwork Grace itself can chase — not the
            # full clean verdict submit_renewal needs, but narrow enough that
            # an income, size, or source-conflict problem still escalates
            # instead of triggering outreach on a case that also needs a
            # human. `result.reasons` is non-empty here (decision != "act"),
            # so this never fires on a case with nothing to chase.
            return Proceed(
                reason="Authority gate passed: outreach is limited to a document request"
            )

        # Every reason, not `reasons[0]`: reason order is not a contract, and a
        # case can fail several conditions at once. The caseworker brief needs
        # all of them.
        #
        # `detail` carries untrusted free text verbatim (`source_conflicts`),
        # and is deliberately not escaped here for the reason authority.py
        # documents: this string's destination varies. It reaches a model
        # prompt from here, so treat it as data the model may try to follow.
        detail = "; ".join(f"{r.code}: {r.detail}" for r in result.reasons)
        return Interrupt(reason=f"A caseworker must decide. {detail}")
