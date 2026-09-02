"""The ledger is ground truth for what executed; a trace is ground truth for
ordering and timing. If they disagree, something is wrong neither alone would
reveal — see the module docstring in `grace/ledger.py` for why this matters
and what it does and does not check.

Two tests here build a real graph and pay for real Bedrock inference
(`test_every_ledger_entry_carries_a_trace_id` and
`test_decides_tool_metrics_agree_with_its_ledger_tool_calls`); the rest
exercise `_current_trace_id` and the two `_append` paths directly and cost
nothing.

**Why the correlation test is scoped to `decide` alone.** Only `decide` is
built with `hooks=[ledger]` (Task 6/7, established by Task 8). `intake`,
`documents`, and the swarm's three agents call read tools whose calls never
reach the case ledger, by design. A test asserting every node's tool calls
appear in the ledger would fail on a correctly-running graph.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date

import pytest

from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.ledger import LedgerHook, _current_trace_id
from grace.tools.action import TranscriptChannel, make_action_tools

TODAY = date(2026, 10, 1)


# ---------------------------------------------------------------------------
# `_current_trace_id` itself
# ---------------------------------------------------------------------------


def test_current_trace_id_is_none_without_a_configured_tracer():
    """The normal unit-test path: no exporter attached, nothing raises.

    `trace.get_current_span()` returns `INVALID_SPAN` when nothing is in the
    context, whose `SpanContext.is_valid` is `False` — so this is a real
    branch of the function, not a coincidence of the test environment.
    """
    assert _current_trace_id() is None


def test_current_trace_id_is_32_lowercase_hex_when_a_tracer_is_active():
    """Matches the `traceparent` header format and CloudWatch's own
    rendering, so the value pastes straight into Transaction Search."""
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider()
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("test-span"):
        trace_id = _current_trace_id()
    assert trace_id is not None
    assert len(trace_id) == 32
    assert trace_id == trace_id.lower()
    int(trace_id, 16)  # raises ValueError if it is not valid hex


def test_current_trace_id_returns_none_rather_than_raising_on_a_broken_span():
    """A hook callback's exception is *not* swallowed — unlike a
    `SteeringHandler`'s, which the SDK wraps in `except Exception: return`
    (Task 5). `HookRegistry.invoke_callbacks` re-raises everything except
    `InterruptException`, and `ToolExecutor._stream` turns that into a
    `status: "error"` tool result. So an exception from here would convert a
    tool that *would have succeeded* into a failed one: `submit_renewal`
    would report an error to the model on a case the gate had already cleared,
    and the family's renewal would not be filed.

    `get_span_context()` is a method on an arbitrary `Span` implementation, so
    a broken or partially shut-down provider can raise from it. Verified
    against the real API rather than assumed: a `Span` whose
    `get_span_context` raises is reachable through
    `trace.set_span_in_context`, and without a `try` this function propagates
    that exception into the tool call.

    Observability must never be able to break the thing it observes.
    """
    from opentelemetry import context as context_api
    from opentelemetry import trace
    from opentelemetry.trace import Span

    class BrokenSpan(Span):
        """A span from a misconfigured provider."""

        def get_span_context(self):
            raise RuntimeError("tracer provider is broken")

        def is_recording(self) -> bool:
            return False

        def end(self, end_time=None) -> None: ...
        def set_attributes(self, attributes) -> None: ...
        def set_attribute(self, key, value) -> None: ...
        def add_event(self, name, attributes=None, timestamp=None) -> None: ...
        def add_link(self, context, attributes=None) -> None: ...
        def update_name(self, name) -> None: ...
        def set_status(self, status, description=None) -> None: ...
        def record_exception(self, exception, attributes=None, timestamp=None, escaped=False) -> None: ...

    token = context_api.attach(trace.set_span_in_context(BrokenSpan()))
    try:
        assert _current_trace_id() is None
    finally:
        context_api.detach(token)


def test_a_broken_tracer_does_not_stop_the_ledger_recording_the_call():
    """The failure mode above, at the level that matters: a broken tracer must
    still leave an audit row. Losing the trace ID is acceptable — losing the
    ledger entry is not, because the ledger is what the evals and the
    caseworker read.
    """
    from opentelemetry import context as context_api
    from opentelemetry import trace
    from opentelemetry.trace import Span

    class BrokenSpan(Span):
        def get_span_context(self):
            raise RuntimeError("tracer provider is broken")

        def is_recording(self) -> bool:
            return False

        def end(self, end_time=None) -> None: ...
        def set_attributes(self, attributes) -> None: ...
        def set_attribute(self, key, value) -> None: ...
        def add_event(self, name, attributes=None, timestamp=None) -> None: ...
        def add_link(self, context, attributes=None) -> None: ...
        def update_name(self, name) -> None: ...
        def set_status(self, status, description=None) -> None: ...
        def record_exception(self, exception, attributes=None, timestamp=None, escaped=False) -> None: ...

    class FakeBeforeEvent:
        tool_use = {"toolUseId": "tu-1", "name": "read_case", "input": {}}

    store = InMemoryCaseStore(load_fixture_cases())
    hook = LedgerHook(store, "c-001")

    token = context_api.attach(trace.set_span_in_context(BrokenSpan()))
    try:
        hook.on_before_tool(FakeBeforeEvent())
    finally:
        context_api.detach(token)

    entries = store.ledger("c-001")
    assert [e.kind for e in entries] == ["tool_call"]
    assert entries[0].detail["tool"] == "read_case"
    assert entries[0].detail["trace_id"] is None


# ---------------------------------------------------------------------------
# Every ledger row carries the key — both writers, not just the hook
# ---------------------------------------------------------------------------


def test_the_hooks_entries_carry_a_trace_id_key():
    """Present as a key even when its value is `None` — a caller reading
    `entry.detail["trace_id"]` must not need to guess whether tracing was
    configured for a particular run. Driven with fake events so this holds
    without paying for Bedrock.
    """

    class FakeBeforeEvent:
        tool_use = {"toolUseId": "tu-1", "name": "read_case", "input": {}}

    class FakeAfterEvent:
        tool_use = {"toolUseId": "tu-1", "name": "read_case", "input": {}}
        result = {"toolUseId": "tu-1", "status": "success", "content": []}

    store = InMemoryCaseStore(load_fixture_cases())
    hook = LedgerHook(store, "c-001")
    hook.on_before_tool(FakeBeforeEvent())
    hook.on_after_tool(FakeAfterEvent())

    entries = store.ledger("c-001")
    assert [e.kind for e in entries] == ["tool_call", "tool_result"]
    for entry in entries:
        assert "trace_id" in entry.detail


def test_the_action_tools_entries_carry_a_trace_id_key():
    """`LedgerHook` is not the only thing that writes to the ledger.

    `make_action_tools`'s own `_log` writes `renewal_submitted`,
    `family_message_sent`, and `escalated` — the three rows that record what
    Grace actually *did*, as opposed to which tools it called. Those are the
    rows a caseworker and Task 8's evals care about most (`sweep` classifies a
    case by looking for `renewal_submitted`), and they go through a different
    function from the hook's `_append`. Wiring the trace ID into
    `LedgerHook._append` alone would leave exactly those rows unjoinable to
    their CloudWatch trace, while every test that only inspected hook rows
    still passed.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    tools = {t.tool_name: t for t in make_action_tools(store, "c-001", TranscriptChannel())}

    async def call(name: str, tool_input: dict) -> None:
        async for _ in tools[name].stream(
            {"toolUseId": "tu-1", "name": name, "input": tool_input}, {}
        ):
            pass

    asyncio.run(call("submit_renewal", {}))
    asyncio.run(call("send_family_message", {"body": "Necesitamos un documento."}))
    asyncio.run(call("escalate_to_caseworker", {"question": "Which income figure applies?"}))

    entries = store.ledger("c-001")
    assert [e.kind for e in entries] == [
        "renewal_submitted",
        "family_message_sent",
        "escalated",
    ]
    for entry in entries:
        assert "trace_id" in entry.detail, entry.kind


def test_every_ledger_writer_in_grace_records_a_trace_id():
    """Structural, so a *new* ledger writer added later is caught here rather
    than by whichever test happened to inspect its rows.

    Both existing writers pass `trace_id` through a `**detail` kwargs helper,
    so a grep for the literal at each `append_ledger` call site would miss
    them. Instead: find every module that calls `append_ledger` and assert it
    references `_current_trace_id`.
    """
    import ast
    import inspect
    import pkgutil

    import grace

    offenders = []
    for info in pkgutil.walk_packages(grace.__path__, prefix="grace."):
        module = __import__(info.name, fromlist=["_"])
        try:
            source = inspect.getsource(module)
        except OSError:  # pragma: no cover — namespace packages have no source
            continue
        tree = ast.parse(source)
        calls_append = any(
            isinstance(node, ast.Attribute) and node.attr == "append_ledger"
            for node in ast.walk(tree)
        )
        if not calls_append:
            continue
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        if "_current_trace_id" not in names:
            offenders.append(info.name)

    assert not offenders, (
        f"these modules write ledger entries without a trace ID: {offenders} — "
        "a row that cannot be joined to its CloudWatch trace defeats Task 9"
    )


# ---------------------------------------------------------------------------
# The correlation itself. Real Bedrock.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def clean_case_run():
    """One real `c-001` graph invocation, shared by the two tests below.

    Module-scoped so the two correlation tests assert against the *same* run
    rather than two independent ones — Task 8 established that per-case caching
    matters here, both for cost and because two tests nominally about "c-001"
    otherwise check genuinely different runs.
    """
    from grace.graph import build_case_graph

    store = InMemoryCaseStore(load_fixture_cases())
    graph = build_case_graph(store, "c-001", TODAY, TranscriptChannel())
    result = graph(f"Process the renewal for case c-001. Today is {TODAY.isoformat()}.")
    return store, result


def test_every_ledger_entry_carries_a_trace_id(clean_case_run):
    """The key is present on every row a *real* run produces.

    The unit tests above drive `_append` directly; this one confirms the same
    holds when the hook is called from inside the SDK's own tool-execution
    path, where the callback runs under an active tool-call span rather than in
    a bare synchronous test.
    """
    store, _ = clean_case_run
    ledger = store.ledger("c-001")
    assert ledger, "no ledger entries were written at all"
    for entry in ledger:
        assert "trace_id" in entry.detail, entry.kind
        value = entry.detail["trace_id"]
        # Either tracing was not configured (None) or it produced a real
        # 32-hex W3C trace ID. Anything else means the format string changed.
        assert value is None or (
            isinstance(value, str) and len(value) == 32 and int(value, 16) >= 0
        ), (entry.kind, value)


def test_decides_tool_metrics_agree_with_its_ledger_tool_calls(clean_case_run):
    """The correlation this task exists to prove, scoped to the one node that
    has a `LedgerHook`.

    A tool named in `decide`'s own `tool_metrics` with no ledger row is a tool
    that ran without being logged — the exact failure a transcript-based eval
    would miss, and the reason the ledger is the evals' ground truth.

    **Counts are compared per tool, not asserted against fixed numbers.** The
    model chooses how many times to call each tool, and that varies run to
    run: `submit_renewal` appears twice on a run where the gate `Guide`s the
    first attempt and once where it does not, and a model may re-read
    `check_window` after a Guide. Both are correct behaviour. What must hold on
    *every* run is that the two tallies agree with each other, which is a
    property of the wiring rather than of the model's choices.
    """
    store, result = clean_case_run

    decide_node = next(n for n in result.execution_order if n.node_id == "decide")
    tool_metrics = decide_node.result.result.metrics.tool_metrics
    from_metrics = Counter({name: tm.call_count for name, tm in tool_metrics.items()})

    # `tool_call` rows only: `tool_result` rows would double-count, and the
    # action tools' own `renewal_submitted`/`escalated` rows are a different
    # kind of record (what Grace did, not which tool it invoked).
    from_ledger = Counter(
        str(e.detail["tool"]) for e in store.ledger("c-001") if e.kind == "tool_call"
    )

    # Guard against the vacuous pass: an empty Counter equals an empty
    # Counter, so a run where `decide` called nothing at all would satisfy the
    # comparison below while proving nothing.
    assert from_ledger, "decide recorded no tool calls, so there is nothing to correlate"

    assert from_metrics == from_ledger, (
        f"decide's own tool_metrics {dict(from_metrics)} does not match its "
        f"ledger tool_call counts {dict(from_ledger)}"
    )
