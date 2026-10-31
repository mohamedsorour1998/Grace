# Lessons: trajectory evals and ledger–trace correlation (Plan 1, Tasks 8–9)

Moved verbatim from `CLAUDE.md` on 2026-10-31. Each lesson was learned on a real defect; paths and counts are as of when it was written.

### What Task 8 established — follow these

**`strands-agents-evals` is never installed, and this decision predates Task 8.** It depends
on `strands-agents-tools` — the same package Task 1's dependency rule already forbids, 25
packages including `slack-bolt` and `pillow`. Trajectory evals are ordinary pytest functions in
`evals/test_gate_trajectory.py`, run explicitly via `.venv/bin/python -m pytest evals/`.
`pyproject.toml`'s `testpaths = ["tests"]` already excludes `evals/` from a bare
`.venv/bin/python -m pytest`, so this costs nothing in the fast suite. **Never add
`strands-agents-evals` to any dependency list, and never import `strands_evals`.**

**A parametrized test that never reaches its own assertion body still passes.** An early
version of the suite's headline ordering test ran on all three escalating fixtures — but
`c-011`/`c-012` never execute a gated action at all (escalating is the point), so the loop
that does the checking never ran for them, and the test passed having asserted nothing while
still paying for ~37 of the suite's ~65 Bedrock invocations. **When a test's `for`/`if`
structure can be skipped entirely for a given input, add an explicit assertion that it wasn't**
(`assert ran_something`) — a parametrized case that silently checks nothing is worse than one
that fails, because it looks identical to a passing check in every report.

**A per-invocation cache must cache failures too, or a flaky run costs 4× and can silently mix
results from different invocations.** `_RUNS[case_id] = _Run(case_id)` never completes the
assignment if construction raises — confirmed live when `decide` hit `set_node_timeout(420.0)`
on Bedrock latency (512.92s against a typical ~75s). Every other test touching the same
`case_id` then retried the full graph invocation from scratch, and different tests for the
same nominal case could end up asserting against genuinely different runs. Cache the exception
and re-raise it on every subsequent lookup for that key.

**A raising operation should still leave partial, real evidence readable.** The evals'
`_Run.__init__` used to set `self.ledger` only after `graph(...)` returned — so a timeout
mid-run discarded a ledger that `LedgerHook` had already partially written and that `store`
(constructed before the `graph()` call) still held. A safety claim that could have been
checked from that partial data instead read as an unrelated infrastructure failure. Read
whatever state is available in a `finally`, regardless of whether the operation that populates
it raised.

**`decide`'s ledger cannot, by itself, distinguish "the swarm deliberated and `decide` trusted
it" from "`decide` escalated blind."** Only `decide` is built with `hooks=[ledger]` (see Task
6/7) — the swarm's own reads on `c-011`/`c-012` never reach the case ledger, so both scenarios
produce the identical shape on `decide`'s rows. `SwarmResult.node_history` closes this at zero
extra cost: it is already returned inside the `GraphResult` a graph invocation produces, and
asserting it names all three roles (not just "non-empty") is a genuine regression guard against
the exact collapse `grace/swarm.py`'s own docstring documents from a real run. **Before
concluding a ledger-based test can't observe something about a nested multi-agent node, check
whether the in-process result object already carries it — a hook is not the only way to see
what happened.**

**A test's classification as "safety" vs. "liveness" must match what could make it fail, not
what it is trying to catch.** A test asserting an escalating case does *something* (outreach,
escalation, or a refused attempt) sounds like a safety property, but the gate never *forces*
a tool call — a model that reads everything and answers only in prose passes the gate's own
checks while failing this test. Label by what can make an assertion fail on a correctly-behaving
system, not by how important the property feels.

### What Task 9 established — follow these

**There are two ledger writers, not one, and the plan only wired the trace ID into one of
them.** `LedgerHook._append` (`grace/ledger.py`) writes `tool_call`/`tool_result`; but
`make_action_tools`'s own `_log` (`grace/tools/action.py`) independently writes
`renewal_submitted`, `family_message_sent`, and `escalated` — the rows that record what Grace
actually *did* rather than which tools it invoked, and the ones `sweep` classifies a case from
(it looks for `renewal_submitted`, per Task 6). Wiring only the hook left exactly those rows
with no `trace_id`, unjoinable to their CloudWatch trace, while every test that inspected hook
rows still passed. Both now share `_current_trace_id`. `test_every_ledger_writer_in_grace_records_a_trace_id`
walks `grace/` with `pkgutil` and fails on any *new* module that calls `append_ledger` without
it — the same discovery-from-disk discipline Task 4's model-ID guard established, for the same
reason: a hardcoded list of writers someone remembered is how this was missed the first time.
**Before adding a field to "every ledger entry", grep for `append_ledger` and count the call
sites.**

**`_current_trace_id` is the one place in this codebase where fail-closed is the wrong
instinct — and a `HookProvider`'s exception is *not* swallowed the way a `SteeringHandler`'s
is.** Verified directly against the SDK: `HookRegistry.invoke_callbacks` re-raises anything
that is not an `InterruptException` (its own docstring says so), and `ToolExecutor._stream`
catches it and substitutes a `status: "error"` tool result. Confirmed empirically with a
raising hook on a real tool call. So an exception escaping `_current_trace_id` would convert a
tool that had *already passed the gate* into a failed call — `submit_renewal` reporting an
error on a clean case, and a renewal that never gets filed. That is the inverse of Task 5's
finding about `steer_before_tool`, where an exception is swallowed and the tool runs *ungated*.
**The distinction is what the code is deciding, not which class it lives in:** failing closed
on a *verification* question protects the family; failing closed on an *observability* question
harms them, because nothing relies on the trace ID to decide anything. `get_span_context()` is
a method on an arbitrary `Span` implementation and a misconfigured or partially shut-down
provider can raise from it, so the `try` is real, not boilerplate. Lose the trace ID; keep the
ledger row.

**Never assert a fixed tool-call count against a real model run.** Measured across three real
`c-001` invocations, `submit_renewal`'s `call_count` was `1`, `2`, `2` — the `2` when the gate
`Guide`s a first attempt and the model retries, both correct behaviour. The plan's own draft
cited `submit_renewal: 2` as the observed value, and a test asserting that number would have
failed one run in three with nothing wrong. The correlation test compares `decide`'s
`tool_metrics` **against its own ledger rows** as two `Counter`s, which is a property of the
wiring rather than of the model's choices, plus an explicit `assert from_ledger` so an empty-vs-
empty comparison cannot pass vacuously (the Task 8 lesson).

**`is_valid` bounds-checks the trace ID, so `format(..., "032x")` cannot overflow 32
characters.** `SpanContext.is_valid` is precomputed at construction and already rejects a
trace ID outside the 128-bit range — verified: `2**128` gives `is_valid == False`, `2**128 - 1`
formats to exactly 32 hex chars. No separate length guard is needed.
