"""Tests for the per-case audit ledger hook.

In a benefits context the ledger is the requirement, not the feature: Task 8's
trajectory evals read it as ground truth, so what it records — and what it
deliberately does not — matters as much as the gate itself.
"""

import ast
import inspect

import pytest

from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.ledger import LedgerHook


class FakeBeforeEvent:
    """Stands in for `BeforeToolCallEvent`, which needs a real Agent."""

    def __init__(self, name: str = "read_case", **kwargs) -> None:
        self.tool_use = {"name": name, "input": {}, "toolUseId": "tu-1"}
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeAfterEvent(FakeBeforeEvent):
    def __init__(self, name: str = "read_case", result=None, **kwargs) -> None:
        super().__init__(name, **kwargs)
        self.result = result


def _hook(case_id: str = "c-001"):
    store = InMemoryCaseStore(load_fixture_cases())
    return store, LedgerHook(store, case_id)


def test_hook_registers_the_events_it_needs():
    _, hook = _hook()
    registered = []

    class FakeRegistry:
        def add_callback(self, event_type, callback, **kwargs):
            registered.append(event_type.__name__)

    hook.register_hooks(FakeRegistry())
    assert "BeforeToolCallEvent" in registered
    assert "AfterToolCallEvent" in registered


def test_register_hooks_accepts_a_positional_registry():
    """The SDK calls `hook.register_hooks(self)` positionally, with no kwargs.

    A signature that only accepted a keyword would raise at agent
    construction — late enough to look like a wiring problem rather than a
    signature one.
    """
    params = inspect.signature(LedgerHook.register_hooks).parameters
    registry = list(params.values())[1]
    assert registry.kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.POSITIONAL_ONLY,
    )


def test_ledger_hook_satisfies_the_hook_provider_protocol():
    """`HookProvider` is `@runtime_checkable`, so this is a real check rather
    than a declaration of intent."""
    from strands.hooks import HookProvider

    _, hook = _hook()
    assert isinstance(hook, HookProvider)


def test_tool_calls_are_appended_to_the_case_ledger():
    store, hook = _hook()
    hook.on_before_tool(FakeBeforeEvent())
    entries = store.ledger("c-001")
    assert [e.kind for e in entries] == ["tool_call"]
    assert entries[0].detail["tool"] == "read_case"


def test_ledger_never_crosses_cases():
    store, hook = _hook("c-001")
    hook.on_before_tool(FakeBeforeEvent())
    assert store.ledger("c-002") == []


def test_tool_results_record_status_and_never_the_payload():
    """`read_case`'s tool result contains household income, size, and language.

    A ledger that copied `result["content"]` would fan that into DynamoDB and
    into whatever renders the audit trail — the same reason span attributes
    carry `case_id` only. Only the status is recorded.
    """
    store, hook = _hook()
    secret = "Rivera household, 412000 cents/month, size 4, Spanish"
    hook.on_after_tool(
        FakeAfterEvent(
            result={
                "toolUseId": "tu-1",
                "status": "success",
                "content": [{"text": secret}],
            }
        )
    )
    entries = store.ledger("c-001")
    assert [e.kind for e in entries] == ["tool_result"]
    assert entries[0].detail["status"] == "success"
    # No value anywhere in the entry contains any part of the payload.
    for value in entries[0].detail.values():
        assert secret not in str(value)
        assert "412000" not in str(value)
    assert "content" not in entries[0].detail


def test_ledger_does_not_read_result_content_at_all():
    """The property above, asserted structurally as well as behaviourally.

    A future edit that logs `result["content"]` for "better debugging" would
    pass the test above only if it happened to use the same fixture text.
    This fails on the subscript itself.
    """
    import grace.ledger as ledger

    tree = ast.parse(inspect.getsource(ledger))
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            assert node.slice.value != "content", ast.dump(node)
    # `.get("content")` would evade the check above.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant):
                    assert first.value != "content", ast.dump(node)


def test_a_missing_or_malformed_result_records_an_unknown_status():
    """`AfterToolCallEvent.result` is typed `ToolResult`, a TypedDict, so at
    runtime it is a plain dict with no guaranteed keys — and on some paths it
    can be absent. Recording nothing would lose the fact that the tool ran.
    """
    store, hook = _hook()
    hook.on_after_tool(FakeAfterEvent(result=None))
    hook.on_after_tool(FakeAfterEvent(result={}))
    hook.on_after_tool(FakeAfterEvent(result="not-a-dict"))
    entries = store.ledger("c-001")
    assert len(entries) == 3
    assert [e.detail["status"] for e in entries] == ["unknown"] * 3


def test_a_cancelled_tool_call_still_leaves_both_ledger_rows():
    """A gate-blocked tool produces `status: "error"`, not a missing result.

    The SDK builds a synthetic error `ToolResult` and fires
    `AfterToolCallEvent` for a tool cancelled by `cancel_tool` (which is what
    a `Guide` sets), so a Guided call appears in the ledger as
    call-then-error rather than as a call with no result. Task 8's evals
    reconstruct the trajectory from these pairs, so an unpaired `tool_call`
    would read as a tool that ran and vanished.
    """
    store, hook = _hook()
    hook.on_before_tool(FakeBeforeEvent("submit_renewal"))
    hook.on_after_tool(
        FakeAfterEvent(
            "submit_renewal",
            result={
                "toolUseId": "tu-1",
                "status": "error",
                "content": [{"text": "Tool call cancelled."}],
            },
        )
    )
    entries = store.ledger("c-001")
    assert [e.kind for e in entries] == ["tool_call", "tool_result"]
    assert entries[1].detail["status"] == "error"


def test_a_missing_tool_name_is_recorded_rather_than_dropped():
    """An unnameable tool call is exactly what an audit trail must not lose."""
    store, hook = _hook()

    class Nameless:
        tool_use: dict = {}

    hook.on_before_tool(Nameless())
    entries = store.ledger("c-001")
    assert len(entries) == 1
    assert entries[0].detail["tool"]


def test_ledger_entries_are_timezone_aware_and_ordered():
    """`LedgerEntry` rejects a naive datetime, and the ledger is read in order.

    Asserted here rather than trusted from `models.py` because a
    `datetime.now()` without `timezone.utc` in the hook would raise at
    construction — a failure that surfaces mid-run, on the first tool call, in
    a demo.
    """
    store, hook = _hook()
    for name in ("read_case", "check_window", "list_documents"):
        hook.on_before_tool(FakeBeforeEvent(name))
    entries = store.ledger("c-001")
    assert [e.detail["tool"] for e in entries] == [
        "read_case",
        "check_window",
        "list_documents",
    ]
    for entry in entries:
        assert entry.at.tzinfo is not None
    assert [e.at for e in entries] == sorted(e.at for e in entries)


def test_gateway_prefix_is_stripped_before_recording():
    """The ledger and the gate must name the same tool.

    Task 9 correlates ledger rows against `GraphResult.execution_order`, and
    Task 8's evals assert `read_case` precedes any action. A row recorded as
    `grace-reads___read_case` matches neither, so the eval would report a
    missing read on a run where the read happened.
    """
    store, hook = _hook()
    hook.on_before_tool(FakeBeforeEvent("grace-actions___submit_renewal"))
    assert store.ledger("c-001")[0].detail["tool"] == "submit_renewal"


def test_a_ledger_write_for_an_unknown_case_raises_rather_than_vanishing():
    """`InMemoryCaseStore.append_ledger` refuses a phantom case id.

    Surfaced here so the behaviour is a known property of the hook rather than
    a surprise in Task 6: a typo'd `case_id` must not silently produce a case
    whose `ledger()` reads as an innocent empty list.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    hook = LedgerHook(store, "c-typo")
    with pytest.raises(KeyError):
        hook.on_before_tool(FakeBeforeEvent())


def test_ledger_records_no_household_identity():
    """Same rule as span attributes and the JWT `sub`: `case_id` only.

    A DynamoDB table is not covered by the Bedrock guardrail, so nothing
    downstream redacts what this writes.
    """
    store, hook = _hook()
    case = store.get("c-001")
    hook.on_before_tool(FakeBeforeEvent())
    hook.on_after_tool(
        FakeAfterEvent(result={"toolUseId": "t", "status": "success", "content": []})
    )
    forbidden = (
        case.household.display_name,
        case.household.phone,
        case.household.household_id,
    )
    for entry in store.ledger("c-001"):
        rendered = f"{entry.detail}"
        for secret in forbidden:
            assert secret not in rendered, (secret, rendered)
