"""Case ledger.

In a benefits context an audit trail is a requirement, not a feature: every
autonomous action must be reconstructable afterwards. This ledger is also
the demo — it shows nine cases handled alone and three escalated, with the
reason.

Two things this hook deliberately does not record:

1. **The tool result payload.** `read_case` returns the household's income,
   size, and preferred language; a ledger row carrying `result["content"]`
   would fan that into DynamoDB, which is outside the Bedrock guardrail's
   redaction, and into whatever renders the audit trail. Only the status is
   recorded. `test_ledger_does_not_read_result_content_at_all` enforces this
   structurally, not just behaviourally.
2. **Any household identity.** `case_id` only — the same rule as span
   attributes and the JWT `sub`.

The ledger, not the model transcript, is the ground truth for what executed:
a transcript-based eval would miss a tool that ran but was not logged.

Every entry also carries the active OTEL trace ID, so a DynamoDB row joins to
its CloudWatch trace. The two are complements, not substitutes: the ledger says
*what* Grace decided and did, durably; the trace says *how long it took and in
what order*, sampled. A trace can be dropped by sampling; a ledger entry
cannot — which is why an eval assertion never moves from the ledger to a span.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from opentelemetry import trace
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookRegistry

from grace.cases.models import LedgerDetailValue, LedgerEntry
from grace.cases.store import CaseStore
from grace.steering import _bare_tool_name

# What a tool call is recorded as when its name cannot be read. An unnameable
# call is the last thing an audit trail should drop, so it is recorded under a
# placeholder rather than skipped.
_UNNAMED_TOOL = "<unnamed>"


def _current_trace_id() -> str | None:
    """Return the active W3C trace ID, or None when tracing is not configured.

    Recorded on every ledger entry so a DynamoDB row can be joined to its
    CloudWatch trace (Plan 2). The `032x` format matches the `traceparent`
    header and CloudWatch's own rendering, so the value pastes straight into
    Transaction Search.

    Returns None rather than raising when no exporter is attached, which is the
    normal case for unit tests: `trace.get_current_span()` yields `INVALID_SPAN`
    whose `SpanContext.is_valid` is False. This function must never be the
    reason a test needs a tracer configured.

    **The `try` is not defensive boilerplate — it is the opposite of the usual
    fail-closed rule, and deliberately so.** A `HookProvider`'s exception is
    *not* swallowed the way a `SteeringHandler`'s is (Task 5):
    `HookRegistry.invoke_callbacks` re-raises anything that is not an
    `InterruptException`, and `ToolExecutor._stream` catches it and substitutes
    a `status: "error"` tool result. So an exception raised here would turn a
    tool that had already passed the gate into a failed call — `submit_renewal`
    reporting an error on a clean case, and a renewal that does not get filed.
    Failing closed on a *verification* question protects the family; failing
    closed on an *observability* question harms them, because the trace ID is
    not evidence anything relies on to decide. `is_valid` is a precomputed
    bounds check, so `format` cannot overflow — but `get_span_context()` is a
    method on an arbitrary `Span` implementation and a misconfigured or
    partially shut-down provider can raise from it. Lose the trace ID; keep the
    ledger row.
    """
    try:
        ctx = trace.get_current_span().get_span_context()
        if not ctx.is_valid:
            return None
        return format(ctx.trace_id, "032x")
    except Exception:  # noqa: BLE001 — observability must not break the tool call
        return None


class LedgerHook:
    """Appends every tool call and result to one case's ledger.

    Bound to one case at construction, matching `AuthorityGate` and the tool
    factories. Attaches to an `Agent` via `hooks=[...]` — a `HookProvider`,
    not a `plugins=[...]` entry, which is where the steering handler goes.

    Structurally satisfies `strands.hooks.HookProvider` (a runtime-checkable
    Protocol) without subclassing it, so a change to the Protocol surfaces as
    a failing `isinstance` test rather than as an inherited stub that silently
    does nothing.
    """

    def __init__(self, store: CaseStore, case_id: str) -> None:
        self._store = store
        self._case_id = case_id

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        # `registry` is positional: the SDK calls `hook.register_hooks(self)`
        # with no keyword. `**kwargs` is the Protocol's own extensibility
        # escape hatch and is accepted so a future SDK that passes one does
        # not raise at agent construction.
        registry.add_callback(BeforeToolCallEvent, self.on_before_tool)
        registry.add_callback(AfterToolCallEvent, self.on_after_tool)

    def _append(self, kind: str, **detail: LedgerDetailValue) -> None:
        # `LedgerEntry` type-checks `detail` to JSON-safe scalars and rejects a
        # naive datetime, both because Plan 2 writes this straight to DynamoDB.
        # Hence `timezone.utc` here rather than a bare `datetime.now()`, which
        # would raise on the first tool call of a run.
        #
        # `trace_id` is always present as a key, even when its value is None:
        # a reader must not have to guess whether tracing was configured for a
        # given run. `LedgerDetailValue` already includes None (Task 2), so no
        # special-casing is needed to satisfy the type check.
        self._store.append_ledger(
            LedgerEntry(
                case_id=self._case_id,
                at=datetime.now(timezone.utc),
                kind=kind,
                detail={**detail, "trace_id": _current_trace_id()},
            )
        )

    def _tool_name(self, event: Any) -> str:
        """The bare tool name from an event, or a placeholder.

        Stripped of the AgentCore Gateway `target___tool` prefix for the same
        reason the gate strips it: Task 9 correlates ledger rows against
        `GraphResult.execution_order`, and Task 8's evals assert `read_case`
        precedes any action. A row recorded as `grace-reads___read_case`
        matches neither name, so the eval would report a missing read on a run
        where the read happened.
        """
        raw = (getattr(event, "tool_use", None) or {}).get("name")
        if not isinstance(raw, str) or not raw:
            return _UNNAMED_TOOL
        return _bare_tool_name(raw)

    def on_before_tool(self, event: Any) -> None:
        self._append("tool_call", tool=self._tool_name(event))

    def on_after_tool(self, event: Any) -> None:
        # Only the status, never the payload — see the module docstring.
        #
        # `ToolResult` is a TypedDict, so `isinstance(result, dict)` is True at
        # runtime, but a TypedDict guarantees no keys either, so `status` is
        # read with a default. A gate-blocked (Guided) call arrives here with a
        # synthetic `status: "error"` result rather than no result at all, so a
        # cancelled call still produces the call/result pair the evals expect.
        status: LedgerDetailValue = "unknown"
        result = getattr(event, "result", None)
        if isinstance(result, dict):
            value = result.get("status", "unknown")
            status = value if isinstance(value, str) and value else "unknown"
        self._append("tool_result", tool=self._tool_name(event), status=status)
