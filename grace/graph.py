"""The deterministic graph spine.

    intake -> documents -> (deliberate) -> decide

Deadline math is a tool, not a node: deterministic work does not need a
model. The conditional edge to `deliberate` exists so the expensive
three-agent swarm runs only on cases that actually look ambiguous — that node
arrives in Task 7; `make_needs_deliberation` below is written and tested here
so Task 7 wires in a predicate that already fails closed.

**Why `needs_deliberation` is a factory bound to `(store, case_id, today)`,
not a free function reading a node's output.** A first version matched
substrings in the `documents` node's free-text summary — the same shape as
every other per-case component here would suggest is wrong on its face, and
it was: `documents` only ever calls `list_documents`, so its summary can
never mention income, household size, or a source conflict, and the
predicate fired on `c-010` (a missing document, needing no deliberation at
all) while staying silent on `c-011`/`c-012` (the two cases a deliberation
swarm exists for). `make_needs_deliberation` instead re-runs `evaluate()`
directly on the case, the same deterministic function the authority gate
runs, and answers from its reason codes rather than from a model's summary of
them. See the function's own docstring for which reason codes route to the
swarm and why the rest do not.

Why the topology matters for safety: `intake` and `documents` are handed
*read tools only*. That is capability absence (CLAUDE.md layer 1) applied at
the node level — no prompt reaching those two nodes can file a renewal,
because the tool does not exist in their context. Only `decide` receives the
action tools, and only `decide` carries the `AuthorityGate`. Handing every
node the full tool list would leave every gate test passing while widening
the blast radius of a prompt injection from one node to three.

One graph per case, one `AuthorityGate` and one `LedgerHook` per graph: both
are bound to a single `case_id` at construction, matching the tool factories.
A shared or cached gate would let reads observed on one household satisfy
another household's prerequisites, which is the cross-household leak the
no-argument tool design exists to prevent, reintroduced one layer up.
"""

from __future__ import annotations

from datetime import date

from strands import Agent
from strands.multiagent import GraphBuilder
from strands.multiagent.graph import Graph, GraphState
from strands.tools.executors import SequentialToolExecutor

from grace.authority import evaluate
from grace.cases.store import CaseStore
from grace.ledger import LedgerHook
from grace.models import nova
from grace.rules.pack import load_pack
from grace.steering import AuthorityGate
from grace.tools.action import Channel, make_action_tools
from grace.tools.read import make_read_tools


def make_needs_deliberation(store: CaseStore, case_id: str, today: date):
    """Build the conditional-edge predicate for one case, bound the same way
    every other per-case component in this file is bound.

    **Why this is a factory, not the free function the plan drafts.** The
    plan's `needs_deliberation(state)` decides from the `documents` node's
    free-text output — matching substrings like `"missing"`/`"conflict"`
    against a model's summary. That summary can only ever cover what
    `list_documents` reports, because that is the only tool `documents` is
    given: it has never seen income, household size, or source-conflict data
    at all. Measured against the real fixtures: `needs_deliberation` fired on
    `c-010` (a missing document — the one case that needs *no* deliberation,
    just a document request) and stayed silent on `c-011` (a 30% income
    change) and `c-012` (a source conflict) — the two genuinely ambiguous
    cases a deliberation swarm exists for. Widening the `documents` node's
    prompt to also relay income/conflict text just re-creates the bug
    `document_problems` was extracted to fix one function up: asking a model
    to compare two numbers and report the difference in prose, when the
    comparison already has a deterministic answer.

    So this predicate does not read a node's prose at all. It re-runs the same
    `evaluate()` the authority gate runs, directly on the case: deliberation is
    needed exactly when the case would escalate for a reason a three-agent
    argument could actually inform — `material_income_change`,
    `household_size_change`, or `source_conflict`. `missing_document`/
    `stale_document`/window reasons are not include here on purpose: no
    amount of deliberation resolves "the document is not on file", and running
    the expensive swarm on `c-010` would burn three extra model calls to reach
    a foregone conclusion. A clean case (`decision == "act"`) needs no
    deliberation either.

    Fails closed on the side that costs money, not coverage — exactly like the
    predicate it replaces: if the case or pack cannot be loaded, or `evaluate`
    itself raises (a structurally invalid pack, per Task 3/5's standing
    warning), deliberate rather than assume the case is clean. The cheap
    branch is the one that skips scrutiny, so "I could not tell" must never
    land there.
    """
    DELIBERATION_CODES = frozenset(
        {"material_income_change", "household_size_change", "source_conflict"}
    )

    def needs_deliberation(state: GraphState) -> bool:
        try:
            case = store.get(case_id)
            pack = load_pack(case.program, case.state)
            result = evaluate(case, today, pack)
        except Exception:  # noqa: BLE001 — deliberate: fail closed
            return True
        return any(r.code in DELIBERATION_CODES for r in result.reasons)

    return needs_deliberation


def build_case_graph(
    store: CaseStore, case_id: str, today: date, channel: Channel
) -> Graph:
    """Build the per-case graph. One graph per case keeps household data
    isolated — nothing is shared between cases.

    `today` is passed in and never read from the clock. Fixture `c-002`'s grace
    period ends 2026-10-30, so a `date.today()` here turns the 9-act/3-escalate
    demo into 8/4 from 2026-10-31 onward.
    """
    read_tools = make_read_tools(store, case_id, today)
    action_tools = make_action_tools(store, case_id, channel)
    gate = AuthorityGate(store, case_id, today)
    ledger = LedgerHook(store, case_id)

    # Read-only by construction. No action tool, no gate: there is nothing here
    # for a gate to block, and a second AuthorityGate would maintain its own
    # `_seen` set that disagrees with the one on `decide`.
    intake = Agent(
        name="intake",
        model=nova("classifier"),
        system_prompt=(
            "You open a benefits renewal case. Call read_case and check_window, "
            "then state in two sentences: which program, and where today falls "
            "in the renewal window. Do not speculate about eligibility."
        ),
        tools=read_tools,
        callback_handler=None,
    )

    # This node's output is descriptive only now — no longer read by any
    # conditional edge. `make_needs_deliberation` decides directly from
    # `evaluate()`'s reason codes (see the module docstring), so nothing
    # downstream depends on this prompt's exact vocabulary.
    documents = Agent(
        name="documents",
        model=nova("classifier"),
        system_prompt=(
            "You audit documents for a benefits renewal. Call list_documents. "
            "Report exactly which required documents are MISSING, expired, or "
            "older than the allowed age. If everything is present and current, "
            "say so plainly. Never guess at a document you cannot see."
        ),
        tools=read_tools,
        callback_handler=None,
    )

    # The only node that can change anything, and the only one the gate guards.
    # `plugins=[gate]` and `hooks=[ledger]` are two independent constructor
    # parameters: a SteeringHandler is a Plugin, a LedgerHook is a HookProvider.
    # Swapping them silently attaches nothing.
    #
    # `tool_executor=SequentialToolExecutor()` is a correctness requirement, not
    # a performance choice. The default executor is concurrent, and this model
    # routinely requests read_case, check_window, list_documents, and
    # submit_renewal in a single turn. Concurrently, `submit_renewal` reaches
    # the gate before its prerequisite reads have finished registering in
    # `AuthorityGate._seen`, so the gate Guides a call that was in fact
    # correctly ordered — and whether the model then retries is luck. Observed
    # directly: the same clean case filed on one run and not the next, turning
    # the 9/3 split into 8/4 with no error anywhere. Sequential execution also
    # stops at the first interrupt rather than running the remaining tools in
    # the batch, which is what a gate blocking an action should do.
    decide = Agent(
        name="decide",
        model=nova("briefer"),
        system_prompt=(
            "You act on a benefits renewal case.\n\n"
            "Always call read_case, check_window, and list_documents FIRST, "
            "one at a time, and wait for each result before the next call. "
            "Only then decide what to do. An authority gate blocks any action "
            "attempted before those three reads have returned.\n\n"
            "If every required document is present and current: call "
            "submit_renewal.\n\n"
            "If a required document is missing, stale, or expired: call "
            "send_family_message with a short, warm message in the family's "
            "preferred language asking for that one document, mentioning the "
            "deadline. Do not call submit_renewal as well.\n\n"
            "If anything else is unclear: call escalate_to_caseworker with the "
            "precise question a human must answer.\n\n"
            "Trust list_documents. It states whether each document is CURRENT, "
            "STALE, or EXPIRED — do not recompute that from the dates, and "
            "never describe a CURRENT document as expired.\n\n"
            "Never claim a renewal was filed unless submit_renewal returned "
            "successfully. An authority gate may block you and explain why — "
            "when it does, follow its instruction exactly."
        ),
        tools=[*read_tools, *action_tools],
        plugins=[gate],
        hooks=[ledger],
        tool_executor=SequentialToolExecutor(),
        context_manager="auto",
        callback_handler=None,
    )

    builder = GraphBuilder()
    builder.add_node(intake, "intake")
    builder.add_node(documents, "documents")
    builder.add_node(decide, "decide")
    builder.add_edge("intake", "documents")
    builder.add_edge("documents", "decide")
    builder.set_entry_point("intake")
    # Bounded on three axes so a model that loops cannot run the sweep into a
    # bill. `max_node_executions` is 12 for a three-node graph: generous enough
    # that a legitimate retry is not cut off, tight enough to stop a cycle.
    builder.set_node_timeout(120.0)
    builder.set_execution_timeout(600.0)
    builder.set_max_node_executions(12)
    return builder.build()
