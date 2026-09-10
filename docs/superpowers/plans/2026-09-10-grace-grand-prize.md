# Grand Prize Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two claims Grace's code does not back, demonstrate the multilingual outreach the README leads with, ground the rule packs in cited regulation, and produce every remaining submission artifact — so that no judge reading the code finds a statement the repository cannot support.

**Architecture:** Four code changes and five document changes, in dependency order. The code changes are additive and each ships behind the existing authority gate: an outreach-drafter agent wrapped as a tool (the missing third multi-agent pattern), a household-facts memory module using the AgentCore Memory *data-plane client* rather than a session manager, a Spanish fixture that makes the outreach visible, and cited sources in the rule packs. The document changes correct the Builder ID, replace "people" with "Americans", relabel the diagram, add the Devpost description, and split the blog draft into three publishable posts.

**Tech Stack:** Python 3.12 (`strands-agents` 1.54.0, `bedrock-agentcore`, `boto3`, `pyyaml`, pytest), TypeScript (Next.js 16, vitest 5), DynamoDB, AgentCore Runtime + Memory, Step Functions, EventBridge.

**Spec:** `docs/superpowers/specs/2026-09-10-grace-grand-prize-spec.md` — read it before Task 1. It carries the competitive analysis and the judging-criteria map that justify every priority below.

## Global Constraints

- **Baseline that must hold or grow: 904 Python tests, 229 vitest tests across 11 files.** Five gates before every commit: `.venv/bin/python -m pytest -q -p no:warnings` from the repo root, and `npm run typecheck`, `npm run lint`, `npm run test`, `npm run build` from `web/`. **Lint must produce clean output, not merely exit 0.**
- **`grace/authority.py` stays pure** — no `strands`, no `boto3`, no file or network I/O. No task here modifies it. `test_authority_imports_only_pure_siblings` enforces this.
- **Amazon Nova only** in the request path. Model IDs live in `grace/models.py` and are referenced by role (`nova("outreach")`), never inlined. `BANNED_MODEL_IDS` must never be assigned to a role.
- **The advocate, verifier, and referee must remain three different models.**
- **Never put household identity anywhere a model or a log can reach it** — no name, phone, address, or email in a tool's returned text, a span attribute, a ledger row, an escalation reason, a memory record, or documentation. Case ids only. `read_case` returns no `display_name` and no phone; do not re-add either.
- **Never claim an action succeeded without tool confirmation** (hard rule 6). No task may loosen this.
- **Escalating is always allowed** and never gated.
- **Reflection and memory are advisory only** (hard rule 5). They may make Grace *more* cautious; they may never satisfy a gate condition. `evaluate()` must never read them.
- **All household data is synthetic**; fixture phones use the reserved `+1555` range.
- **The two DynamoDB invariants must survive every task:** `renewal_submitted` exists for exactly `c-001`–`c-009`; no household in `c-010`/`c-011`/`c-012` ever has one.
- **The demo headline must remain `9 handled alone, 3 waiting on you.`** byte for byte in the un-decided state.
- **Prove every new guard by sabotage.** Break the line the test protects, watch the *named* test fail, restore. Record the sabotage and the failing test name in the commit message.
- **Code freeze Friday 2026-09-12 EOD Cairo.** Tasks 1–7 are code; Tasks 8–12 are documents and may continue after the freeze.
- Conventional commits (`feat:`, `fix:`, `test:`, `docs:`). Comments explain *why*, and in this codebase the "why" is usually a safety property.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `grace/tools/outreach.py` | **New.** The outreach-drafter agent wrapped as a tool — the third multi-agent pattern | 1 |
| `tests/test_outreach_tool.py` | **New.** Its guards: no identity argument, no action tools, language honoured | 1 |
| `grace/graph.py` | Give `decide` the drafter tool; update its prompt | 1 |
| `fixtures/households.yaml` | `c-010` language `en` → `es`; `sources:` unchanged | 2 |
| `grace/rules/packs/{medicaid-ny,snap-ny}.yaml` | Add a `sources:` block citing each parameter's authority | 3 |
| `grace/rules/pack.py` | Parse and expose `sources`; refuse a malformed block | 3 |
| `tests/test_rule_pack_sources.py` | **New.** Every numeric parameter has a source entry | 3 |
| `grace/household_memory.py` | **New.** Write/read household facts via the AgentCore Memory data-plane client | 4 |
| `tests/test_household_memory.py` | **New.** Fail-open, no identity, advisory-only | 4 |
| `grace/entrypoint.py` | Call the memory writer after each outcome; read facts into the graph task | 5 |
| `agentcore/agentcore.json` | Add `GRACE_MEMORY_ID` to `envVars` | 5 |
| `grace/swarm.py` | Accept optional advisory context for the advocate | 6 |
| `README.md` | Builder ID, "Americans", Memory/patterns claims, rules sources, evals sentence | 8 |
| `docs/architecture.md` + `docs/architecture.png` | Label Strands; add the agent-loop inset | 9 |
| `docs/devpost-description.md` | **New.** The submission form's text description | 10 |
| `docs/builder-blog-post.md` | Post 1: Builder ID, "Americans", corrected counts | 11 |
| `docs/builder-blog-post-2-deployed-is-not-written.md` | **New.** Post 2 | 11 |
| `docs/builder-blog-post-3-sabotage.md` | **New.** Post 3 | 11 |
| `docs/demo-video-handout.md` | "Americans", the Spanish beat, corrected figures | 12 |
| `CLAUDE.md` | Builder ID; record what this plan established | 12 |

---

### Task 1: The outreach drafter — the missing third multi-agent pattern

**Why.** `README.md:159` claims *"Agents-as-tools — context isolation for the outreach drafter, policy retriever, and caseworker briefer"*. No `@tool` wraps an `Agent` anywhere in the repository, and `grace/models.py` defines an `outreach` role that nothing calls. That is an overclaim in a project whose own README says claiming an unshipped surface *"is the one thing that turns a working entry into a dishonest one"*. This task makes the claim true — and it is the same pattern as Module 6 of AWS's own Strands workshop.

**Safety shape.** The drafter is a *content* function: it takes the document id, the deadline, and the language, and returns prose. It receives **no case id**, **no store**, and **no action tools**, so it cannot read another household or send anything. `send_family_message` remains gated on `decide`'s `AuthorityGate` exactly as before, so the drafter cannot cause a send — it can only supply words for one.

**Files:**
- Create: `grace/tools/outreach.py`
- Create: `tests/test_outreach_tool.py`
- Modify: `grace/graph.py` (the `decide` agent's `tools=` list and `system_prompt`)

**Interfaces:**
- Consumes: `grace.models.nova(role)` → `BedrockModel`.
- Produces: `make_outreach_tool() -> list` returning a one-element list holding a `DecoratedFunctionTool` named `draft_family_message`, whose underlying callable is `draft_family_message(document_id: str, deadline: str, language: str) -> str`. Task 5 does not use it; only `grace/graph.py` does.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_outreach_tool.py`:

```python
"""THE OUTREACH DRAFTER IS A CONTENT FUNCTION, NOT A HOUSEHOLD-SCOPED ONE.

Every other agent in Grace is bound to one case at construction and its tools
take no arguments (CLAUDE.md layer 2). This one is the deliberate exception, and
the exception is safe for the opposite reason: it is bound to *nothing*. It has
no case id, no store, and no action tools, so there is no household it could
read and nothing it could send. It returns prose that `decide` then passes to
`send_family_message`, which is still gated.

The distinction matters because "takes arguments" reads like a violation of the
no-parameter rule. The rule is about *identity* — a tool that accepts a case id
can be pointed at another family. A tool that accepts a document id and a
language cannot.
"""

from __future__ import annotations

import inspect

from grace.tools.outreach import make_outreach_tool


def _tool():
    tools = make_outreach_tool()
    assert len(tools) == 1, tools
    return tools[0]


def test_the_tool_is_named_for_drafting_not_sending():
    # A model reading its own tool list must not be able to mistake this for a
    # send. `send_family_message` is the gated one and stays that way.
    assert _tool().tool_name == "draft_family_message"


def test_it_takes_content_arguments_and_no_identity():
    """The load-bearing test. A `case_id`, `household_id`, or `phone` parameter
    would make this the one tool a prompt injection could redirect."""
    params = inspect.signature(_tool()._tool_func).parameters
    assert set(params) == {"document_id", "deadline", "language"}
    for name in ("case_id", "household_id", "phone", "display_name", "name", "address"):
        assert name not in params, f"{name} is identity; this tool must not accept it"


def test_it_carries_no_store_and_no_action_tools():
    """Capability absence at the drafter. Even if a prompt talked it into
    sending, there is nothing in its closure to send with."""
    source = inspect.getsource(make_outreach_tool)
    for forbidden in ("CaseStore", "make_action_tools", "submit_renewal",
                      "send_family_message", "escalate_to_caseworker", "store"):
        assert forbidden not in source, f"{forbidden} must not appear in the drafter"


def test_it_uses_the_outreach_role_and_not_a_banned_model():
    from grace.models import BANNED_MODEL_IDS, _ROLES

    source = inspect.getsource(make_outreach_tool)
    assert 'nova("outreach")' in source, "the outreach role exists for this tool"
    assert _ROLES["outreach"] not in BANNED_MODEL_IDS


def test_the_drafter_agent_has_no_tools_at_all():
    """It writes; it does not act. An agent with tools inside a tool is a
    second loop nobody is watching."""
    source = inspect.getsource(make_outreach_tool)
    assert "tools=[]" in source, "the drafter agent must be constructed with tools=[]"
    assert "callback_handler=None" in source


def test_every_model_role_is_referenced_by_some_module():
    """**The overclaim guard.** `models.py` defined `outreach` and `judge` and
    nothing called either — a role that exists only in a table is the same shape
    of claim as a surface that exists only in a README. Walks the package from
    disk rather than a list someone remembered, the discipline Task 4 of Plan 1
    established for the model-ID guard.

    `judge` is exempted by name with its reason: it is reserved for an LLM
    steering handler that is specified in the spec as optional (P2.3). If that
    ships, delete the exemption. If it does not, delete the role.
    """
    import pkgutil
    import grace
    from grace.models import _ROLES

    referenced: set[str] = set()
    for module in pkgutil.walk_packages(grace.__path__, prefix="grace."):
        try:
            source = inspect.getsource(__import__(module.name, fromlist=["_"]))
        except Exception:  # noqa: BLE001 — a module that will not import is not a reference
            continue
        for role in _ROLES:
            if f'nova("{role}")' in source:
                referenced.add(role)

    unreferenced = set(_ROLES) - referenced
    assert unreferenced <= {"judge"}, (
        f"these roles are defined and never called: {sorted(unreferenced)}. "
        "A role nothing uses is an overclaim in models.py's own table."
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_outreach_tool.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'grace.tools.outreach'`.

- [ ] **Step 3: Write the drafter**

Create `grace/tools/outreach.py`:

```python
"""The outreach drafter: agents-as-tools, for context isolation.

**Why a nested agent rather than a prompt on `decide`.** `decide` is the node
that carries the action tools and the authority gate, and its context is the
case record plus every read tool's output. Asking it to also compose warm
multilingual prose puts translation chatter in the same window as the
eligibility reasoning — and `decide`'s prompt is already the longest in the
system. The drafter runs its own loop, sees only three facts, and returns a
string. That is the agents-as-tools pattern's actual purpose: context
isolation, not delegation for its own sake.

**Why it may take arguments when no other Grace tool does.** CLAUDE.md layer 2
says every *household-scoped* tool takes no arguments, because a `case_id`
parameter is something a prompt injection can poison. This tool is not
household-scoped: it is bound to nothing, holds no store, and has no case id to
be redirected to. Its arguments are *content* — which document, by when, in
which language — and none of them identifies anyone. `tests/test_outreach_tool.py`
pins that distinction so a later edit cannot quietly add an identity parameter.

**What it cannot do.** It is constructed with `tools=[]`, so it has no
capability at all beyond returning text. `send_family_message` remains on
`decide` behind the `AuthorityGate`, which still requires `read_case` and
`list_documents` to have run and still refuses any case whose problems are not
document-only. The drafter supplies words for a send; it can never cause one.

**No household identity reaches it.** The caller passes a document id, an ISO
date, and a language code. It never sees a name, a phone number, or an address —
`read_case` does not return them either, since a surname reaching a model is
exactly how one reached CloudWatch (see `grace/tools/read.py`).
"""

from __future__ import annotations

from strands import Agent, tool

from grace.models import nova

# The languages the fixtures use, and the ones `web/lib/intake.ts` accepts. An
# unrecognised code is not an error — the drafter is told to write in English
# and say so — because refusing to draft would mean refusing to contact a family
# about a deadline, which is worse than contacting them in the wrong language.
_PROMPT = (
    "You write one short SMS to a family about a benefits renewal.\n\n"
    "You will be told which document is needed, the deadline, and the "
    "language to write in. Write two or three sentences, warm and plain, "
    "naming the document and the date. Ask them to send it to the state "
    "agency handling their renewal.\n\n"
    "Write in the requested language. If you do not know it, write in English.\n\n"
    "You do not know the family's name and must not invent one. Do not open "
    "with a name or a greeting that needs one. Do not promise that anything "
    "has been filed, approved, or accepted — you are asking for a document, "
    "nothing more. Do not mention case numbers, systems, or agencies by name.\n\n"
    "Return only the message text."
)


def make_outreach_tool() -> list:
    """Build the drafter, wrapped as a tool for `decide`.

    Returns a one-element list so the call site reads like the other tool
    factories (`make_read_tools`, `make_action_tools`) and can be splatted into
    a `tools=[...]` list without a special case.
    """

    @tool
    def draft_family_message(document_id: str, deadline: str, language: str) -> str:
        """Draft a short SMS asking the family for one missing document.

        Args:
            document_id: Which document is needed, e.g. proof_of_residency.
            deadline: The certification end date, ISO format.
            language: The family's preferred language code, e.g. es, vi, ar.
        """
        # A fresh agent per call, with no tools and no callback handler. No
        # state survives between drafts, so one household's message can never
        # leak into another's — the same isolation the per-case graph gives the
        # rest of the system, at the tool level.
        drafter = Agent(
            name="outreach-drafter",
            model=nova("outreach"),
            system_prompt=_PROMPT,
            tools=[],
            callback_handler=None,
        )
        response = drafter(
            f"Document needed: {document_id}\n"
            f"Deadline: {deadline}\n"
            f"Write in: {language}"
        )
        return str(response)

    return [draft_family_message]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_outreach_tool.py -q -p no:warnings`
Expected: PASS, 6 tests.

- [ ] **Step 5: Wire it into `decide` only**

In `grace/graph.py`, add the import beside the other tool imports:

```python
from grace.tools.outreach import make_outreach_tool
```

In `build_case_graph`, after `action_tools = make_action_tools(...)`:

```python
    # Agents-as-tools, and only on `decide`. `intake`, `documents`, and the
    # three swarm agents keep read tools alone — the drafter is harmless, but
    # widening any node's tool list past what it needs is how a blast radius
    # grows. `decide` is the only node that sends anything, so it is the only
    # node that needs words to send.
    outreach_tools = make_outreach_tool()
```

Change `decide`'s `tools=` from `tools=[*read_tools, *action_tools],` to:

```python
        tools=[*read_tools, *outreach_tools, *action_tools],
```

And in `decide`'s `system_prompt`, replace the paragraph beginning `"If a required document is missing, stale, or expired: call "` with:

```python
            "If a required document is missing, stale, or expired: first call "
            "draft_family_message with the document id, the certification end "
            "date, and the family's preferred language from read_case. Then "
            "call send_family_message with exactly the text it returned. Do not "
            "rewrite the draft and do not call submit_renewal as well.\n\n"
```

- [ ] **Step 6: Verify the graph still has the right shape**

Run this and confirm only `decide` gained a tool, and that no node gained an action tool:

```bash
.venv/bin/python -c "
from datetime import date
from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.graph import build_case_graph
from grace.tools.action import TranscriptChannel
from grace.steering import AuthorityGate

g = build_case_graph(InMemoryCaseStore(load_fixture_cases()), 'c-010', date(2026,10,1), TranscriptChannel())
def gates(a):
    reg = getattr(a, '_plugin_registry', None); out=[]
    for n in dir(reg):
        if n.startswith('__'): continue
        v = getattr(reg, n, None)
        if isinstance(v, (list, tuple, set)): out += [x for x in v if isinstance(x, AuthorityGate)]
    return out
def walk(node, path=''):
    ex = node.executor
    inner = getattr(ex, 'nodes', None)
    if isinstance(inner, dict):
        for v in inner.values(): walk(v, node.node_id + '/')
        return
    tools = set(getattr(ex, 'tool_names', []) or [])
    acts = sorted(tools & {'submit_renewal','send_family_message','escalate_to_caseworker'})
    print(f'  {path}{node.node_id:12} gates={len(gates(ex))} drafter={\"draft_family_message\" in tools} actions={acts or \"-\"}')
for n in g.nodes.values(): walk(n)
"
```

Expected: `intake`, `documents`, and the three swarm agents show `gates=0 drafter=False actions=-`; `decide` shows `gates=1 drafter=True actions=['escalate_to_caseworker', 'send_family_message', 'submit_renewal']`.

- [ ] **Step 7: Run the full suites**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: PASS, at least **910 tests** (904 + 6).

Run from `web/`: `npm run test`
Expected: 229 tests across 11 files, unchanged — this task adds no TypeScript.

If a pre-existing test in `tests/test_graph.py` asserts `decide`'s exact tool count or list, that is expected. Read it before changing it, update it to include the drafter, and say in the commit message which assertion moved.

- [ ] **Step 8: Sabotage each guard**

| Sabotage | Must fail |
|---|---|
| add a `case_id: str` parameter to `draft_family_message` | `test_it_takes_content_arguments_and_no_identity` |
| construct the drafter with `tools=[*make_read_tools(...)]` — you will need a `store` import, which is itself the point | `test_it_carries_no_store_and_no_action_tools`, `test_the_drafter_agent_has_no_tools_at_all` |
| change `nova("outreach")` to `nova("classifier")` | `test_it_uses_the_outreach_role_and_not_a_banned_model` and `test_every_model_role_is_referenced_by_some_module` |
| rename the tool to `send_draft` | `test_the_tool_is_named_for_drafting_not_sending` |

Stage your work before each sabotage (`git add -A`) so `git checkout --` restores your edit rather than HEAD. Two agents on the previous plan lost work to that exact mistake.

- [ ] **Step 9: Commit**

```bash
git add grace/tools/outreach.py tests/test_outreach_tool.py grace/graph.py
git commit -m "feat: the outreach drafter, so agents-as-tools is a fact rather than a claim

README.md said Grace uses all three Strands multi-agent patterns and named
agents-as-tools for 'the outreach drafter, policy retriever, and caseworker
briefer'. No @tool wrapped an Agent anywhere, and models.py defined an outreach
role nothing called — an overclaim in a project whose README says claiming an
unshipped surface is the one thing that turns a working entry into a dishonest
one.

The drafter is a content function, not a household-scoped one, and that is what
makes its arguments safe. CLAUDE.md layer 2 is about identity: a tool taking a
case id can be pointed at another family. This one is bound to nothing — no
store, no case id, no tools at all — and takes a document id, a deadline, and a
language. send_family_message stays on decide behind the AuthorityGate, so the
drafter supplies words for a send and can never cause one.

Only decide gets it. Widening any other node's tool list past what it needs is
how a blast radius grows.

test_every_model_role_is_referenced_by_some_module walks the package from disk
and fails on any role defined and never called, so models.py's own table cannot
drift back into an overclaim. judge is exempted by name with its reason.

Four sabotages watched failing, including an identity parameter added to the
drafter."
```

---

### Task 2: Make the multilingual claim visible

**Why.** The README's second sentence says Grace *"chases the one missing document in the family's own language"*. Measured across the twelve fixtures at the pinned date, exactly one household triggers outreach — `c-010` — and its language is `en`. The claim is never demonstrated, on the dashboard or in the video. Changing `c-010` to Spanish makes the case page a judge opens show Grace writing to the family in their language.

**Depends on Task 1** — the drafter is what makes the language argument load-bearing.

**Files:**
- Modify: `fixtures/households.yaml` (the `c-010` household line)
- Modify: `tests/test_demo_dates.py` (add the outreach-language assertion)

**Interfaces:**
- Consumes: `grace.cases.store.load_fixture_cases`, `grace.authority.evaluate`, `grace.rules.pack.load_pack`.
- Produces: nothing importable. Later tasks rely only on `c-010.household.language == "es"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_demo_dates.py`:

```python
def test_the_household_grace_messages_does_not_speak_english():
    """**The README's second sentence, made checkable.**

    Grace claims to chase the missing document "in the family's own language".
    Exactly one fixture triggers outreach at the pinned date, and while it was
    `en` the claim was never demonstrated anywhere — not on the case page, not
    in the video, not in the ledger. A demo that cannot show its own headline
    feature is a headline feature nobody has verified.

    Asserted as "the outreach household is non-English" rather than "c-010 is
    Spanish", because the point is the demonstration, not the case id: if a
    later fixture edit moves which household needs a document, this still holds
    the property that matters.
    """
    from grace.authority import evaluate
    from grace.cases.store import load_fixture_cases
    from grace.rules.pack import load_pack

    DOCUMENT_CODES = {"missing_document", "stale_document"}
    messaged = []
    for case in load_fixture_cases():
        verdict = evaluate(case, PINNED, load_pack(case.program, case.state))
        codes = {r.code for r in verdict.reasons}
        # `send_family_message` is gated on every reason being document-only
        # (grace/steering.py's DOCUMENT_ONLY_CODES). A case that also fails on
        # income or a conflict escalates instead of being texted.
        if codes and codes <= DOCUMENT_CODES:
            messaged.append(case)

    assert messaged, "no fixture triggers outreach, so the language claim is undemonstrable"
    assert any(c.household.language != "en" for c in messaged), (
        "every household Grace texts speaks English, so the multilingual claim "
        f"is never shown: {[(c.case_id, c.household.language) for c in messaged]}"
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_demo_dates.py -k english -q -p no:warnings`
Expected: FAIL — `every household Grace texts speaks English … [('c-010', 'en')]`.

- [ ] **Step 3: Change the fixture**

In `fixtures/households.yaml`, on the `c-010` household line, change `language: "en"` to `language: "es"`. Change nothing else on that line — the `+1555` phone, the income, and the size are all load-bearing for other tests.

Add a comment above the `c-010` case block:

```yaml
  # Spanish on purpose. This is the one household whose only problem is a
  # missing document, so it is the only one Grace texts — and the README's
  # second sentence claims Grace writes "in the family's own language". While
  # this was `en` that claim was true of the code and demonstrated nowhere.
  # `tests/test_demo_dates.py` holds the property.
```

- [ ] **Step 4: Run it to verify it passes, and that the split is unmoved**

Run: `.venv/bin/python -m pytest tests/test_demo_dates.py -q -p no:warnings`
Expected: PASS, 5 tests.

Run:

```bash
.venv/bin/python -c "
from datetime import date
from grace.cases.store import load_fixture_cases
from grace.rules.pack import load_pack
from grace.authority import evaluate
cs = load_fixture_cases()
a = [c.case_id for c in cs if evaluate(c, date(2026,10,1), load_pack(c.program, c.state)).decision == 'act']
print(len(a), 'act /', len(cs)-len(a), 'escalate')
assert len(a) == 9, a
"
```

Expected: `9 act / 3 escalate`. **A language change must not move the split** — if it does, something reads `language` in the gate, which would itself be a defect.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: PASS, at least **911 tests**.

If a test fails because it pinned `c-010`'s language, read it first — it may be pinning something else that happens to use the value. Update it and say which assertion moved.

- [ ] **Step 6: Update the live record row**

The deployed table's `RECORD#v1` row for `c-010` still says `en`, and `infra/seed_cases.py` will not overwrite it (`attribute_not_exists(sk)`). Update just that attribute:

```bash
aws dynamodb update-item --region us-east-1 --table-name grace-cases \
  --key '{"pk":{"S":"CASE#c-010"},"sk":{"S":"RECORD#v1"}}' \
  --update-expression 'SET #l = :es' \
  --expression-attribute-names '{"#l":"language"}' \
  --expression-attribute-values '{":es":{"S":"es"}}' \
  --condition-expression 'attribute_exists(sk)' \
  --return-values ALL_NEW --output json | .venv/bin/python -c "
import json,sys
row = json.load(sys.stdin)['Attributes']
print('language now:', row['language']['S'])
print('cert_end unchanged:', row.get('cert_end',{}).get('S'))
print('program unchanged:', row.get('program',{}).get('S'))
"
```

Expected: `language now: es`, with `cert_end` and `program` unchanged. **If the attribute name is not `language`, stop and read the row first** (`aws dynamodb get-item` with the same key) — `grace/cases/record.py` is the authority on the shape, and writing a wrong attribute name adds a field rather than changing one.

- [ ] **Step 7: Verify from the store that the agent sees Spanish**

```bash
GRACE_STORE=dynamodb .venv/bin/python -c "
from grace.store_factory import build_store
print('c-010 language as the agent reads it:', build_store().get('c-010').household.language)
"
```

Expected: `es`.

- [ ] **Step 8: Commit**

```bash
git add fixtures/households.yaml tests/test_demo_dates.py
git commit -m "feat: the household Grace texts speaks Spanish, so the claim is demonstrable

README's second sentence says Grace chases the missing document 'in the
family's own language'. Exactly one fixture triggers outreach at the pinned
date — c-010 — and it was English, so the claim was true of the code and shown
nowhere: not on the case page, not in the ledger, not in the video.

The test asserts 'the household Grace texts is non-English' rather than 'c-010
is Spanish', so a later fixture edit that moves which household needs a
document still holds the property that matters.

The gate does not read language, and the 9/3 split is unmoved — verified rather
than assumed, because a language change moving the split would itself be a
defect. The live RECORD#v1 row was updated with a targeted UpdateItem, since
seed_cases.py refuses to overwrite."
```

---

### Task 3: Cite the regulations the rule packs encode

**Why.** `grace/rules/packs/medicaid-ny.yaml` is seven numeric parameters with no authority named. The Originality criterion asks whether the team *"demonstrates genuine understanding of the problem space"*, and a competing entry in this hackathon validated its SNAP rules against 42,388 real government records. Twelve synthetic households governed by uncited numbers reads as a toy. Citing each parameter is the cheapest available answer.

**The verification rule for this task, and it is the whole task:** every citation must be checked against the actual regulation text before it is written down. **A wrong citation is worse than none** — it is exactly the "asserting a property you did not verify" defect this project has found six times. Any parameter you cannot trace to a provision must be marked as the deployment's own policy choice, which is an honest and defensible answer.

**Files:**
- Modify: `grace/rules/packs/medicaid-ny.yaml`, `grace/rules/packs/snap-ny.yaml`
- Modify: `grace/rules/pack.py` (parse `sources`)
- Create: `tests/test_rule_pack_sources.py`

**Interfaces:**
- Consumes: `grace.rules.pack.load_pack`, `RulePack`, `InvalidRulePack`.
- Produces: `RulePack.sources: tuple[RuleSource, ...]` where `RuleSource` is a frozen dataclass with fields `parameter: str`, `authority: str`, `note: str`. Task 8 renders these in the README.

- [ ] **Step 1: Research and record the citations**

Before writing any code, establish the authority for each parameter. Use `WebSearch`/`WebFetch` against eCFR (`ecfr.gov`) and `regs.health.ny.gov`, and record for each what the provision actually says.

The parameters needing an entry:

| Pack | Parameter | Value |
|---|---|---|
| medicaid-ny | `certification_period_months` | 12 |
| medicaid-ny | `window_opens_days_before_end` | 60 |
| medicaid-ny | `grace_period_days_after_end` | 90 |
| medicaid-ny | `income_change_immaterial_pct` | 5.0 |
| medicaid-ny | `required_documents[].max_age_days` | 60, 365 |
| snap-ny | every parameter in that file | read it first |

Starting points to verify, **not** to copy without checking:

- **42 CFR § 435.916** — Medicaid renewal of eligibility. Check what it says about the renewal period for MAGI-based beneficiaries and about the reconsideration period following a procedural termination.
- **7 CFR § 273.10(f)** and **7 CFR § 273.14** — SNAP certification periods and recertification.
- **18 NYCRR** — New York's implementing regulations.
- **42 CFR § 435.952** — use of information and requests for additional information.

If a provision gives a *range* (e.g. "not to exceed 12 months") rather than a fixed number, the pack's value is a policy choice **within** that range: cite the provision and say so in the `note`. That is more honest than implying the regulation mandates the exact value.

**If you cannot verify a citation, write `authority: "policy choice"` with a note explaining the reasoning.** Do not guess a section number.

- [ ] **Step 2: Write the failing test**

Create `tests/test_rule_pack_sources.py`:

```python
"""EVERY NUMBER IN A RULE PACK TRACES TO SOMETHING.

The packs encode a benefits programme's clocks: how long a certification lasts,
when the renewal window opens, how late is still savable, what income movement
is immaterial. Those numbers decide whether a family keeps coverage, and an
uncited number is indistinguishable from an invented one.

`authority: "policy choice"` is a valid and honest answer — many of these
parameters are choices a deployment makes within a range the regulation allows.
What is not valid is silence.
"""

from __future__ import annotations

import pytest

from grace.rules.pack import InvalidRulePack, load_pack

PACKS = [("medicaid", "NY"), ("snap", "NY")]

# The numeric parameters that decide a verdict. A source entry is required for
# each. Listed here rather than derived from the dataclass so that adding a
# parameter without citing it is a failure rather than an automatic pass.
CITED_PARAMETERS = frozenset({
    "certification_period_months",
    "window_opens_days_before_end",
    "grace_period_days_after_end",
    "income_change_immaterial_pct",
})


@pytest.mark.parametrize("program,state", PACKS)
def test_every_deciding_parameter_carries_a_source(program, state):
    pack = load_pack(program, state)
    cited = {s.parameter for s in pack.sources}
    missing = CITED_PARAMETERS - cited
    assert not missing, f"{program}-{state} has uncited parameters: {sorted(missing)}"


@pytest.mark.parametrize("program,state", PACKS)
def test_every_required_document_carries_a_source(program, state):
    pack = load_pack(program, state)
    cited = {s.parameter for s in pack.sources}
    for required in pack.required_documents:
        key = f"required_documents.{required.doc_id}"
        assert key in cited, f"{program}-{state}: {required.doc_id} has no source"


@pytest.mark.parametrize("program,state", PACKS)
def test_no_source_is_empty_or_a_placeholder(program, state):
    """A citation field filled with 'TBD' is worse than an absent one: it looks
    like diligence from a distance."""
    pack = load_pack(program, state)
    assert pack.sources, f"{program}-{state} cites nothing at all"
    for source in pack.sources:
        assert source.parameter.strip(), source
        assert source.authority.strip(), source
        for placeholder in ("tbd", "todo", "xxx", "fixme", "?"):
            assert placeholder not in source.authority.lower(), source


@pytest.mark.parametrize("program,state", PACKS)
def test_a_cited_parameter_actually_exists_in_the_pack(program, state):
    """A source for a parameter the pack does not have is a citation of
    nothing — the same defect as a docstring describing code that moved."""
    pack = load_pack(program, state)
    document_ids = {r.doc_id for r in pack.required_documents}
    for source in pack.sources:
        if source.parameter.startswith("required_documents."):
            assert source.parameter.split(".", 1)[1] in document_ids, source
        else:
            assert hasattr(pack, source.parameter), (
                f"{source.parameter} is cited but is not a field on RulePack"
            )


def test_a_malformed_sources_block_is_refused(tmp_path, monkeypatch):
    """`load_pack` raises `InvalidRulePack` and nothing else — Plan 1 Task 1's
    single-exception contract. A sources block that is a string, or whose entry
    is missing `authority`, must fail closed like every other malformed field
    rather than loading a pack with silent gaps."""
    import grace.rules.pack as pack_module

    good = (tmp_path / "medicaid-ny.yaml")
    good.write_text(
        (pack_module.PACKS_DIR / "medicaid-ny.yaml").read_text().replace(
            "sources:", "sources: 'not a list'  # was:", 1
        )
    )
    monkeypatch.setattr(pack_module, "PACKS_DIR", tmp_path)
    with pytest.raises(InvalidRulePack):
        load_pack("medicaid", "NY")
```

The packs-directory constant is `PACKS_DIR` (verified in `grace/rules/pack.py:21`). The test above uses it; do not add an alias.

- [ ] **Step 3: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_rule_pack_sources.py -q -p no:warnings`
Expected: FAIL — `AttributeError: 'RulePack' object has no attribute 'sources'`.

- [ ] **Step 4: Add `sources` to the packs**

Append to `grace/rules/packs/medicaid-ny.yaml` (values below are the *shape*; the `authority` and `note` text must be what your Step 1 research actually established):

```yaml
# Where each number comes from. `policy choice` is an honest answer where the
# regulation gives a range rather than a value — what is not honest is an
# uncited number deciding whether a family keeps coverage.
sources:
  - parameter: certification_period_months
    authority: "<verified citation from Step 1>"
    note: "<what the provision actually says about this value>"
  - parameter: window_opens_days_before_end
    authority: "<verified citation, or 'policy choice'>"
    note: "<reasoning>"
  - parameter: grace_period_days_after_end
    authority: "<verified citation, or 'policy choice'>"
    note: "<reasoning>"
  - parameter: income_change_immaterial_pct
    authority: "<verified citation, or 'policy choice'>"
    note: "<reasoning>"
  - parameter: required_documents.proof_of_income
    authority: "<verified citation, or 'policy choice'>"
    note: "<reasoning>"
  - parameter: required_documents.proof_of_residency
    authority: "<verified citation, or 'policy choice'>"
    note: "<reasoning>"
```

Do the same for `snap-ny.yaml`, one entry per parameter that file actually has — read it first rather than assuming it matches.

- [ ] **Step 5: Parse it**

In `grace/rules/pack.py`, add the dataclass beside `RequiredDocument`:

```python
@dataclass(frozen=True)
class RuleSource:
    """Where one rule-pack parameter comes from.

    `authority` is a citation or the literal string `policy choice`. Both are
    honest; an empty one is not. `note` says what the provision actually
    establishes, because a bare section number invites a reader to assume the
    regulation mandates the exact value when it often gives a range.
    """

    parameter: str
    authority: str
    note: str = ""
```

Add `sources: tuple[RuleSource, ...] = ()` to `RulePack`. In the loader, parse it with the same fail-closed discipline as every other field:

```python
    # Parsed with the same discipline as `required_documents`: a malformed
    # block raises `InvalidRulePack` rather than loading a pack whose citations
    # are silently absent. Plan 1 Task 1's contract — one exception type for
    # missing, unreadable, malformed, and out-of-range — so a caller fails
    # closed with a single `except InvalidRulePack`.
    raw_sources = data.get("sources", [])
    if not isinstance(raw_sources, list):
        raise InvalidRulePack(f"{path}: 'sources' must be a list, got {type(raw_sources).__name__}")
    sources: list[RuleSource] = []
    for entry in raw_sources:
        if not isinstance(entry, dict):
            raise InvalidRulePack(f"{path}: each source must be a mapping, got {entry!r}")
        parameter = entry.get("parameter")
        authority = entry.get("authority")
        note = entry.get("note", "")
        for name, value in (("parameter", parameter), ("authority", authority), ("note", note)):
            if not isinstance(value, str):
                raise InvalidRulePack(f"{path}: source {name} must be a string, got {value!r}")
        if not parameter.strip() or not authority.strip():
            raise InvalidRulePack(f"{path}: source parameter and authority must be non-empty")
        sources.append(RuleSource(parameter=parameter, authority=authority, note=note))
```

Pass `sources=tuple(sources)` into the `RulePack(...)` construction.

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_rule_pack_sources.py tests/test_rules.py -q -p no:warnings`
Expected: PASS. If `tests/test_rules.py` has a test asserting the pack's exact field set, update it and say so.

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: PASS, at least **920 tests** (911 + 9).

- [ ] **Step 7: Sabotage**

| Sabotage | Must fail |
|---|---|
| delete the `income_change_immaterial_pct` entry from `medicaid-ny.yaml` | `test_every_deciding_parameter_carries_a_source[medicaid-NY]` |
| set one `authority:` to `"TBD"` | `test_no_source_is_empty_or_a_placeholder` |
| add an entry for `parameter: nonexistent_field` | `test_a_cited_parameter_actually_exists_in_the_pack` |
| change `sources:` to a bare string in the pack | `test_a_malformed_sources_block_is_refused` |

- [ ] **Step 8: Commit**

```bash
git add grace/rules/packs/ grace/rules/pack.py tests/test_rule_pack_sources.py
git commit -m "feat: every rule-pack parameter names its authority

The packs encode a benefits programme's clocks — certification length, when the
renewal window opens, how late is still savable, what income movement is
immaterial — and those numbers decide whether a family keeps coverage. They
cited nothing. An uncited number is indistinguishable from an invented one, and
a competing entry in this hackathon validated its SNAP rules against 42,388 real
government records.

Every citation was checked against the regulation text before it was written
down. Where a provision gives a range rather than a value, the entry says
'policy choice' and the note explains the reasoning — that is honest, and
implying a regulation mandates a number it merely permits would not be.

Parsed with the same fail-closed contract as every other pack field: a
malformed sources block raises InvalidRulePack rather than loading a pack whose
citations are silently absent.

Four sabotages watched failing, including a citation of a parameter the pack
does not have."
```

---

### Task 4: Household memory — the write and read halves

**Why.** `README.md` lists AgentCore Memory as **"Shipped"** and `docs/architecture.md` draws an edge from `decide` to it. `grace/memory.py`'s `build_session_manager` has **zero callers** — not in `entrypoint.py`, `run.py`, `runtime_app.py`, `agentcore.json`, or the Lambda. The resource exists and ACTIVE in the account with the right namespaces; nothing reads or writes it.

**Why not the session manager.** Verified against the installed `bedrock-agentcore`: `AgentCoreMemorySessionManager.create_multi_agent` **raises `NotImplementedError("MultiAgent is not implemented for this repository")`**. `GraphBuilder.set_session_manager` exists, so attaching one is syntactically possible — and it would raise on the first sweep. `grace/memory.py`'s docstring says there is "no legal place inside the graph" for a session manager; the docstring's conclusion is right and its stated reason is wrong. So this task uses the **data-plane client** (`MemoryClient.create_event` / `retrieve_memories`), which is the supported path for writing facts an extraction strategy can process.

**Files:**
- Create: `grace/household_memory.py`
- Create: `tests/test_household_memory.py`

**Interfaces:**
- Consumes: `bedrock_agentcore.memory.MemoryClient`, `grace.memory.actor_id`, `infra.naming.REGION`.
- Produces:
  - `remember_outcome(case_id: str, summary: str, *, client=None, memory_id: str | None = None) -> bool` — writes one fact; returns `True` on a confirmed write, `False` on any failure.
  - `recall_facts(case_id: str, query: str, *, client=None, memory_id: str | None = None, top_k: int = 5) -> tuple[str, ...]` — returns remembered fact strings, newest first; `()` on any failure.
  - `MEMORY_NAMESPACE = "/facts/{actorId}"`
  - Task 5 calls both.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_household_memory.py`:

```python
"""PER-HOUSEHOLD RECALL, AND WHY IT CANNOT CHANGE A VERDICT.

A recertification cycle is annual, so a fact learned this cycle is only useful
if it survives eleven months. AgentCore Memory holds it. But recall is
*advisory* (CLAUDE.md hard rule 5): it may make Grace more cautious and may
never satisfy a gate condition, and `evaluate()` never sees it.

That polarity decides every error path here. Losing recall degrades the quality
of an outreach message; raising would fail a sweep. So both functions fail open
— returning `False` and `()` — and neither is ever consulted by the gate.
"""

from __future__ import annotations

import inspect

from grace.household_memory import MEMORY_NAMESPACE, recall_facts, remember_outcome


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
        self.events.append(kwargs)
        return {"eventId": "e-1"}

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
    # The payload is (role, text) tuples, per create_event's signature.
    roles = {role for role, _ in event["messages"]}
    assert roles <= {"ASSISTANT", "USER"}, event["messages"]
    assert "proof_of_residency" in " ".join(text for _, text in event["messages"])


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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_household_memory.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'grace.household_memory'`.

- [ ] **Step 3: Write the module**

Create `grace/household_memory.py`:

```python
"""Per-household facts that outlive a sweep.

**What this is for.** A recertification cycle is annual. "Income was verified
by pay stubs last cycle", "this family reads Spanish", "the last renewal
escalated on a missing proof of residency" — all of it has to survive eleven
months between contacts, and none of it belongs in the case record, which is
what the *state* would hold. AgentCore Memory holds it.

**Why the data-plane client rather than a session manager.** Verified against
the installed `bedrock-agentcore`: `AgentCoreMemorySessionManager.create_multi_agent`
raises `NotImplementedError("MultiAgent is not implemented for this repository")`.
`GraphBuilder.set_session_manager` exists, so attaching one to Grace's Graph is
syntactically possible and would raise on the first sweep. `grace/memory.py`'s
docstring reaches the right conclusion — no session manager inside the graph —
by the wrong route; the real reason is this, not the `ValueError` it cites.
`create_event` / `retrieve_memories` are the supported path for writing facts an
extraction strategy can process.

**Everything here fails open, and that is the opposite of Grace's usual
polarity.** The gate fails *closed* because an unverified case must reach a
human. This is not a verification question. Recall is advisory (hard rule 5): it
may make Grace more cautious and may never satisfy a gate condition, and
`evaluate()` never sees it. So a memory outage degrades the quality of an
outreach message and must never fail a sweep — `tests/test_household_memory.py`
pins that `authority.py` and `steering.py` cannot even import this module.

**The actor id is the case id, and nothing else.** It is already opaque (hard
rule 9), which a household name would not be, and one actor per case means one
family's history can never be retrieved into another family's run.
"""

from __future__ import annotations

import logging
import os

from grace.memory import actor_id
from infra import naming

logger = logging.getLogger(__name__)

# Must match the `namespaceTemplates` set at memory creation. A mismatch
# retrieves nothing *silently* rather than raising — Plan 2 measured that on the
# live service. `tests/test_household_memory.py` pins this against
# `grace/memory.py`'s `RETRIEVAL_NAMESPACES` so the two cannot drift.
MEMORY_NAMESPACE = "/facts/{actorId}"

# One session per household's remembered history. The service constrains
# `sessionId` to `[a-zA-Z0-9][a-zA-Z0-9-_]*` — no dots, no colons — so a case id
# with a fixed prefix is safe where "case id plus an ISO timestamp" would be
# refused.
_SESSION_PREFIX = "history-"


def _client(client):
    """The data-plane client, or the injected fake.

    Imported inside the function so the fast suite never needs the package's
    boto3 client construction, and so a test can pass a fake without patching an
    import.
    """
    if client is not None:
        return client
    from bedrock_agentcore.memory import MemoryClient

    return MemoryClient(region_name=naming.REGION)


def _memory_id(memory_id: str | None) -> str:
    # `or`-guarded rather than `os.getenv(name, default)`: that form only
    # defaults on *absence*, so `GRACE_MEMORY_ID=` (set but blank) would sail
    # past it. Plan 2 shipped that exact bug once in the store factory.
    return (memory_id if memory_id is not None else os.getenv("GRACE_MEMORY_ID") or "").strip()


def remember_outcome(
    case_id: str, summary: str, *, client=None, memory_id: str | None = None
) -> bool:
    """Write one fact about this household. `True` only on a confirmed write.

    The return value exists so a caller can log honestly rather than claim a
    write it did not confirm — hard rule 6 applied to an observability path.
    Nothing branches on it to decide anything about a family.

    `summary` must carry no household identity: it is written to a service and
    read back into a model's context, which is exactly the path a surname took
    to CloudWatch. Callers pass case-level facts (a date, a reason code, a
    document id), never a name or a phone number.
    """
    resolved = _memory_id(memory_id)
    if not resolved or not summary.strip():
        return False
    try:
        _client(client).create_event(
            memory_id=resolved,
            actor_id=actor_id(case_id),
            session_id=f"{_SESSION_PREFIX}{case_id}",
            # `create_event` takes (role, text) tuples. ASSISTANT because this
            # is Grace's own record of what it concluded, not something a user
            # said — the extraction strategy reads both, and mislabelling the
            # speaker would make a later reader think a family reported it.
            messages=[("ASSISTANT", summary)],
        )
        return True
    except Exception:  # noqa: BLE001 — see the module docstring: recall is advisory
        logger.warning("could not record a household fact; continuing", exc_info=True)
        return False


def recall_facts(
    case_id: str,
    query: str,
    *,
    client=None,
    memory_id: str | None = None,
    top_k: int = 5,
) -> tuple[str, ...]:
    """What Grace remembers about this household, newest first. `()` if unknown.

    An empty tuple means "nothing to add", never "this family has no history" —
    the caller must not present absence of recall as a fact about the family.
    """
    resolved = _memory_id(memory_id)
    if not resolved:
        return ()
    try:
        records = _client(client).retrieve_memories(
            memory_id=resolved,
            namespace=MEMORY_NAMESPACE.format(actorId=actor_id(case_id)),
            query=query,
            actor_id=actor_id(case_id),
            top_k=top_k,
        )
    except Exception:  # noqa: BLE001 — losing recall cannot change a verdict
        logger.warning("could not recall household facts; continuing", exc_info=True)
        return ()

    facts: list[str] = []
    for record in records or ():
        # The record shape belongs to the service, not to this module. A record
        # with no text is skipped rather than crashing the sweep.
        if not isinstance(record, dict):
            continue
        text = (record.get("content") or {}).get("text") if isinstance(record.get("content"), dict) else None
        if isinstance(text, str) and text.strip():
            facts.append(text)
    return tuple(facts)
```

- [ ] **Step 4: Run to verify passing**

Run: `.venv/bin/python -m pytest tests/test_household_memory.py -q -p no:warnings`
Expected: PASS, 8 tests.

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: PASS, at least **928 tests**.

- [ ] **Step 5: Sabotage**

| Sabotage | Must fail |
|---|---|
| remove the `try` from `remember_outcome` so the exception propagates | `test_a_write_failure_is_reported_not_raised` |
| change `MEMORY_NAMESPACE` to `/households/{actorId}` | `test_the_namespace_matches_what_the_memory_was_created_with` |
| add `from grace.household_memory import recall_facts` to `grace/steering.py` | `test_nothing_in_the_gate_can_reach_this_module` |
| pass `actor_id=case_id + "/history"` in the write | `test_a_write_names_the_household_by_case_id_only` |

- [ ] **Step 6: Commit**

```bash
git add grace/household_memory.py tests/test_household_memory.py
git commit -m "feat: per-household facts that outlive a sweep

README listed AgentCore Memory as 'Shipped' and the architecture diagram drew
an edge from decide to it. build_session_manager had zero callers — not in
entrypoint, run, runtime_app, agentcore.json, or the Lambda. The resource was
ACTIVE with the right namespaces and nothing read or wrote it.

Not fixed with a session manager. Verified against the installed
bedrock-agentcore: AgentCoreMemorySessionManager.create_multi_agent raises
NotImplementedError, so attaching one to Grace's Graph — which
GraphBuilder.set_session_manager makes syntactically possible — would raise on
the first sweep. grace/memory.py's docstring reaches the right conclusion by
the wrong route. create_event / retrieve_memories are the supported path.

Everything here fails open, which is the opposite of Grace's usual polarity and
deliberate: the gate fails closed because an unverified case must reach a human,
and this is not a verification question. Recall is advisory — hard rule 5 — so a
memory outage may degrade an outreach message and must never fail a sweep. A
test asserts authority.py and steering.py cannot even import this module.

Four sabotages watched failing, including an actor id with a path separator,
which would nest one household's records where another's retrieval could span
them."
```

---

### Task 5: Wire memory into the deployed request path

**Depends on Task 4.** This is what makes the README's "Shipped" true.

**Files:**
- Modify: `grace/entrypoint.py`
- Modify: `agentcore/agentcore.json`
- Modify: `tests/test_entrypoint.py`

**Interfaces:**
- Consumes: `remember_outcome`, `recall_facts` from Task 4.
- Produces: nothing importable. Task 6 reads the same `recall_facts`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_entrypoint.py`:

```python
def test_the_outcome_is_recorded_to_household_memory(monkeypatch):
    """**What makes 'Memory: Shipped' true.** The resource existed and ACTIVE
    for a week with nothing reading or writing it."""
    from datetime import date

    import grace.entrypoint as entrypoint
    from grace.cases.store import InMemoryCaseStore, load_fixture_cases

    written: list[tuple[str, str]] = []
    monkeypatch.setattr(
        entrypoint, "remember_outcome",
        lambda case_id, summary, **kw: (written.append((case_id, summary)), True)[1],
    )
    monkeypatch.setattr(entrypoint, "recall_facts", lambda *a, **k: ())
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: FakeGraph())

    outcome = entrypoint.process_case(
        {"case_id": "c-010", "today": "2026-10-01"},
        store=InMemoryCaseStore(load_fixture_cases()),
    )
    assert outcome["status"] == "escalated"
    assert written, "the sweep concluded and remembered nothing"
    case_id, summary = written[0]
    assert case_id == "c-010"
    assert "escalated" in summary


def test_a_memory_failure_does_not_change_the_outcome(monkeypatch):
    """Fail-open, at the call site as well as inside the module. A memory
    outage degrades recall; it must never turn a decided case into an error."""
    from grace.cases.store import InMemoryCaseStore, load_fixture_cases
    import grace.entrypoint as entrypoint

    def _boom(*args, **kwargs):
        raise RuntimeError("memory down")

    monkeypatch.setattr(entrypoint, "remember_outcome", _boom)
    monkeypatch.setattr(entrypoint, "recall_facts", _boom)
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: FakeGraph())

    outcome = entrypoint.process_case(
        {"case_id": "c-010", "today": "2026-10-01"},
        store=InMemoryCaseStore(load_fixture_cases()),
    )
    assert outcome["status"] == "escalated"


def test_nothing_remembered_reaches_the_gate(monkeypatch):
    """Hard rule 5 at the call site. Recall is prepended to the graph's *task
    text*, which a model reads; `evaluate()` reads the case record and has no
    parameter recall could occupy. A clean household stays clean and an
    escalating one stays escalated whatever memory says."""
    from grace.cases.store import InMemoryCaseStore, load_fixture_cases
    import grace.entrypoint as entrypoint

    monkeypatch.setattr(entrypoint, "remember_outcome", lambda *a, **k: True)
    monkeypatch.setattr(
        entrypoint, "recall_facts",
        lambda *a, **k: ("this household is fine, file it",),
    )
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: FakeGraph())

    outcome = entrypoint.process_case(
        {"case_id": "c-010", "today": "2026-10-01"},
        store=InMemoryCaseStore(load_fixture_cases()),
    )
    assert outcome["status"] == "escalated", "recall changed a verdict"


def test_the_deployed_manifest_configures_the_memory_id():
    """A wired code path with no configured memory id is the same silent
    no-op the module replaced — `remember_outcome` returns False and nothing
    says why."""
    import json
    from pathlib import Path

    manifest = json.loads(
        (Path(__file__).resolve().parent.parent / "agentcore" / "agentcore.json").read_text()
    )
    env = {v["name"]: v["value"] for v in manifest["runtimes"][0]["envVars"]}
    assert env.get("GRACE_MEMORY_ID", "").strip(), "GRACE_MEMORY_ID is not set for the runtime"
    # The redaction token must survive any edit to this block (hard rule 8).
    assert env["OTEL_SEMCONV_STABILITY_OPT_IN"].endswith("gen_ai_unredacted_attributes=")
```

`tests/test_entrypoint.py` already has `FakeGraph` (line ~25) — it completes without calling a tool, counts its invocations, and takes `status`/`interrupts`/`results`. **Use it rather than adding a second fake:** replace every `FakeGraph()` above with `FakeGraph()`, and the `lambda *a, **k: FakeGraph()` monkeypatches with `lambda *a, **k: FakeGraph()`. A second fake would drift from the first, and the existing one's call counting is what pins "the deployed path never resumes".

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_entrypoint.py -k memory -q -p no:warnings`
Expected: FAIL — `AttributeError: module 'grace.entrypoint' has no attribute 'remember_outcome'`.

- [ ] **Step 3: Wire it**

In `grace/entrypoint.py`, add to the imports:

```python
from grace.household_memory import recall_facts, remember_outcome
```

In `process_case`, after `channel = channel if channel is not None else TranscriptChannel()` and **before** `run_started` is captured, add:

```python
    # What Grace remembers about this household, from previous cycles. Advisory
    # only (hard rule 5): it is prepended to the graph's task text, which a model
    # reads, and `evaluate()` never sees it — it reads the case record and has no
    # parameter recall could occupy. A memory outage returns `()` and the sweep
    # proceeds unchanged.
    try:
        remembered = recall_facts(case_id, "previous renewal outcomes and preferences")
    except Exception:  # noqa: BLE001 — recall can never fail a sweep
        remembered = ()
```

Change the graph invocation's task text from the single f-string to:

```python
        task = f"Process the renewal for case {case_id}. Today is {today.isoformat()}."
        if remembered:
            # Labelled as history and as advisory, in the text itself. A model
            # reading an unlabelled fact cannot tell a remembered claim from a
            # current one — and a remembered claim is by definition stale.
            task += (
                "\n\nFrom previous cycles (history only — verify everything "
                "against the current case record):\n"
                + "\n".join(f"- {fact}" for fact in remembered)
            )
        result = graph(task)
```

Then, immediately before **each** `return` in `process_case` that reports a final outcome — the `_escalate(...)` calls and the `acted` return — the summary must be written. Rather than duplicating the call at four sites, add a helper above `process_case`:

```python
def _remember(case_id: str, outcome: CaseOutcome) -> None:
    """Record what this run concluded, for the next cycle to read.

    Called on every terminal path. Carries the case id, the status, and the
    gate's own reason — never a name, a phone number, or an address, because
    this text is read back into a model's context and that is precisely the
    path a surname took to CloudWatch.

    Swallows everything: this is an observability write on the way out, and a
    memory outage must not turn a decided case into an error. `remember_outcome`
    already fails open; this is the second belt, because the call site is inside
    a function whose module docstring promises it never raises.
    """
    try:
        status = outcome.get("status", "unknown")
        detail = outcome.get("reason") or outcome.get("detail") or ""
        remember_outcome(case_id, f"{status}: {detail}".strip().rstrip(":").strip())
    except Exception:  # noqa: BLE001 — see the docstring
        pass
```

and call it by wrapping the returns. The cleanest shape that touches the fewest lines: rename the existing `process_case` body's terminal returns to build the outcome first. If that is a large edit, instead wrap the whole function:

```python
def process_case(payload, store=None, channel=None) -> CaseOutcome:
    """<existing docstring>"""
    outcome = _process_case(payload, store=store, channel=channel)
    case_id = outcome.get("case_id", "")
    if case_id:
        _remember(case_id, outcome)
    return outcome
```

renaming the existing implementation to `_process_case`. **Prefer the wrapper** — it guarantees every terminal path is covered rather than relying on someone having found all four, and `tests/test_entrypoint.py`'s existing assertions about `process_case`'s behaviour keep passing unchanged.

If the module has an AST-based test asserting `process_case`'s structure (Plan 6 added one about `datetime.now` binding to `run_started`), check it still passes after the rename.

- [ ] **Step 4: Configure the runtime**

In `agentcore/agentcore.json`, add to `runtimes[0].envVars`:

```json
  {
    "name": "GRACE_MEMORY_ID",
    "value": "grace_household_memory-TCf1SS708O"
  }
```

**Verify the id first** rather than trusting this plan:

```bash
aws bedrock-agentcore-control list-memories --region us-east-1 \
  --query "memories[?starts_with(id,'grace_household_memory')].{id:id,status:status}" --output table
```

Expected: one row, `ACTIVE`.

- [ ] **Step 5: Confirm the runtime role can reach Memory**

```bash
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::339712964409:role/grace-runtime-role \
  --action-names bedrock-agentcore:CreateEvent bedrock-agentcore:RetrieveMemoryRecords \
  --query 'EvaluationResults[].{action:EvalActionName,decision:EvalDecision}' --output table
```

If either is `implicitDeny`, add the missing action to `infra/provision_iam.py`'s runtime policy scoped to the memory ARN, re-run `provision_iam`, and re-simulate. **Do not widen to `bedrock-agentcore:*`** — and do not remove the explicit `Deny` on `GetWorkloadAccessTokenForUserId`, which Appendix D.1 requires.

- [ ] **Step 6: Run the suites**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: PASS, at least **932 tests**.

Run from `web/`: `npm run test` → 229 unchanged.

- [ ] **Step 7: Sabotage**

| Sabotage | Must fail |
|---|---|
| delete the `_remember(...)` call from the wrapper | `test_the_outcome_is_recorded_to_household_memory` |
| remove the `try` from `_remember` | `test_a_memory_failure_does_not_change_the_outcome` |
| remove `GRACE_MEMORY_ID` from `agentcore.json` | `test_the_deployed_manifest_configures_the_memory_id` |
| truncate `OTEL_SEMCONV_STABILITY_OPT_IN` to drop the trailing `=` | same test (hard rule 8) |

- [ ] **Step 8: Commit**

```bash
git add grace/entrypoint.py agentcore/agentcore.json tests/test_entrypoint.py
git commit -m "feat: the sweep writes and reads household memory

Closes the second half of the Memory overclaim. Every terminal path now records
what the run concluded, and the next cycle reads it back into the graph's task
text — labelled as history and as advisory, because a model reading an
unlabelled fact cannot tell a remembered claim from a current one, and a
remembered claim is by definition stale.

Wrapped rather than edited at four return sites: process_case is renamed to
_process_case and the wrapper remembers whatever it returned, so every terminal
path is covered by construction rather than by someone having found all four.

Hard rule 5 holds structurally. Recall reaches the task text, which a model
reads; evaluate() reads the case record and has no parameter recall could
occupy. A test feeds 'this household is fine, file it' through recall and
asserts c-010 still escalates.

GRACE_MEMORY_ID is set on the runtime, verified against the live ACTIVE
resource, and the manifest test also pins hard rule 8's redaction token so an
edit to that block cannot silently drop it.

Four sabotages watched failing."
```

---

### Task 6: Redeploy and verify on the live system

**Depends on Tasks 1, 2, 5.** All three ship in the container image. **This task is the Friday gate: if it does not pass, revert to v5 and take the honest-downgrade path in Task 8.**

**Files:** none. This is verification.

- [ ] **Step 1: Baseline the table before anything changes**

```bash
.venv/bin/python - <<'PY'
import boto3
from collections import Counter
d = boto3.client("dynamodb", region_name="us-east-1")
key, rows, kinds, filed = None, 0, Counter(), set()
while True:
    kw = {"TableName": "grace-cases"}
    if key: kw["ExclusiveStartKey"] = key
    p = d.scan(**kw)
    for it in p["Items"]:
        rows += 1
        k = it.get("kind", {}).get("S")
        if k: kinds[k] += 1
        if k == "renewal_submitted": filed.add(it["pk"]["S"].removeprefix("CASE#"))
    key = p.get("LastEvaluatedKey")
    if not key: break
print("BASELINE rows:", rows)
print("kinds:", dict(kinds))
print("filed:", sorted(filed))
PY
```

Record the output. The delta after the sweep is only attributable against it.

- [ ] **Step 2: Deploy**

```bash
agentcore deploy --dry-run 2>&1 | tail -3
.venv/bin/python -c "
import json, glob
d = json.load(open(sorted(glob.glob('agentcore/cdk/cdk.out/*.template.json'))[0]))
for _, r in d['Resources'].items():
    if r['Type'] == 'AWS::BedrockAgentCore::Runtime':
        print('RoleArn:', r['Properties']['RoleArn'])
        print('Env:', json.dumps(r['Properties'].get('EnvironmentVariables'), indent=1))
"
```

Confirm `GRACE_MEMORY_ID` is present and `OTEL_SEMCONV_STABILITY_OPT_IN` still ends in `gen_ai_unredacted_attributes=`. Then:

```bash
agentcore deploy -y --verbose
```

Poll until the runtime version increments:

```bash
aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id grace_grace-oTyyvo8stE \
  --region us-east-1 --query '{v:agentRuntimeVersion,s:status}' --output text
```

Expected: version 6 (or higher), `READY`.

- [ ] **Step 3: Sweep and read the outcome**

```bash
EXEC=$(aws stepfunctions start-execution --region us-east-1 \
  --state-machine-arn arn:aws:states:us-east-1:339712964409:stateMachine:grace-sweep \
  --input '{"today":"2026-10-01"}' --query executionArn --output text)
echo "${EXEC##*:}"
```

Poll `describe-execution` until `SUCCEEDED`, then:

```bash
aws stepfunctions describe-execution --execution-arn "$EXEC" --region us-east-1 \
  --query output --output text > /tmp/sweep.json
.venv/bin/python -c "
import json; from collections import Counter
o = json.load(open('/tmp/sweep.json'))
print('outcomes:', len(o), Counter(x['status'] for x in o))
print('escalated:', sorted(x['case_id'] for x in o if x['status']=='escalated'))
"
```

**Required: `12 outcomes, 9 acted / 3 escalated`, escalating exactly `c-010`/`c-011`/`c-012`.**

If it is not 9/3, **revert immediately**: `agentcore deploy` from the previous commit, or roll the runtime back to v5, and take Task 8's downgrade path. A broken demo loses more than a missing feature. Record what failed in the commit message and in `docs/deployed-verification.md`.

- [ ] **Step 4: Verify the invariants and the new behaviour**

```bash
.venv/bin/python - <<'PY'
import boto3, json
from collections import Counter
d = boto3.client("dynamodb", region_name="us-east-1")
key, filed, esc, msgs = None, set(), set(), []
while True:
    kw = {"TableName": "grace-cases"}
    if key: kw["ExclusiveStartKey"] = key
    p = d.scan(**kw)
    for it in p["Items"]:
        k = it.get("kind", {}).get("S")
        cid = it["pk"]["S"].removeprefix("CASE#")
        if k == "renewal_submitted": filed.add(cid)
        if k == "family_message_sent": msgs.append((cid, it["sk"]["S"], it.get("d_body", {}).get("S", "")))
        if it["sk"]["S"].startswith("ESCALATION#"): esc.add(cid)
    key = p.get("LastEvaluatedKey")
    if not key: break
print("INVARIANT filed == c-001..c-009:", sorted(filed) == [f"c-{i:03d}" for i in range(1,10)])
print("INVARIANT no escalating household filed:", not ({"c-010","c-011","c-012"} & filed))
print("queue exactly the three:", sorted(esc) == ["c-010","c-011","c-012"])
newest = sorted(msgs, key=lambda m: m[1])[-1] if msgs else None
print("newest outreach:", newest[0] if newest else None)
print("body:", (newest[2][:200] if newest else "NONE"))
PY
```

Both invariants must be `True`. **The newest `family_message_sent` body must be Spanish**, and it must contain no name, no phone number, and no address.

- [ ] **Step 5: Verify Memory actually holds something**

```bash
.venv/bin/python - <<'PY'
from bedrock_agentcore.memory import MemoryClient
c = MemoryClient(region_name="us-east-1")
MID = "grace_household_memory-TCf1SS708O"
for case in ("c-010", "c-011", "c-012"):
    try:
        events = c.list_events(memory_id=MID, actor_id=case, session_id=f"history-{case}", max_results=5)
        print(f"{case}: {len(events)} event(s)")
        for e in events[:2]:
            print("   ", str(e)[:160])
    except Exception as exc:
        print(f"{case}: ERROR {exc}")
PY
```

**Required: at least one event per escalated household.** This is what makes "Memory: Shipped" true — a write confirmed by reading it back, not by the API call returning.

Extraction into `/facts/` is asynchronous, so `retrieve_memories` may return nothing immediately. Wait a few minutes and re-check:

```bash
.venv/bin/python -c "
from grace.household_memory import recall_facts
print(recall_facts('c-010', 'previous renewal outcomes'))
"
```

If `list_events` shows events but `recall_facts` stays empty after ~10 minutes, the *write* half is real and the *retrieval* half is not — say exactly that in the README rather than claiming both.

- [ ] **Step 6: Verify the deployed dashboard**

Mint a Cognito session (the round-trip script in the scratchpad, or the runbook's steps) and check:

- `/` renders `9 handled alone, 3 waiting on you.`
- `/queue` shows exactly `c-010 c-011 c-012`
- `/case/c-010` shows the Spanish outreach body in its ledger
- Unauthenticated `/`, `/queue`, `/new` → 307
- A PII scan of the fetched markup returns NONE, **with the scanner self-tested against a planted name and `+1555` first**

- [ ] **Step 7: Confirm no infrastructure drift**

```bash
.venv/bin/python -m infra.verify_deployed
```

Expected: no drift.

- [ ] **Step 8: Record the evidence and commit**

Append a dated section to `docs/deployed-verification.md` carrying the sweep output, both invariants, the Spanish body (redacted of nothing, since it contains no identity), the memory events, and the dashboard checks. Then:

```bash
git add docs/deployed-verification.md
git commit -m "docs: runtime v6 verified — outreach drafter, Spanish outreach, and Memory in use

One sweep on the schedule's own input proves three changes at once: the
agents-as-tools drafter wrote the message, the message is Spanish because the
household reads Spanish, and the outcome was written to AgentCore Memory and
read back by listing events rather than by trusting the write to return.

Both invariants held: renewal_submitted for exactly c-001..c-009, no escalating
household filed, queue exactly the three."
```

---

### Task 7: Reflection — remembered lessons reach the advocate

**Optional (spec P2.1). Do only if Task 6 passed by Friday noon.** The README calls this *"genuinely the originality differentiator"* and defers it because *"it cannot be built before a deployed sweep exists to reflect on."* Seven sweeps now exist and Task 5 writes to memory, so the stated reason no longer holds.

**Files:**
- Modify: `grace/swarm.py` (accept optional advisory context)
- Modify: `grace/graph.py` (pass it through)
- Modify: `tests/test_swarm.py`

**Interfaces:**
- Consumes: `recall_facts` from Task 4.
- Produces: `build_deliberation_swarm(read_tools, *, prior_lessons: tuple[str, ...] = ())` — an added keyword-only parameter with a default, so every existing call site keeps working.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_swarm.py`:

```python
def test_prior_lessons_reach_the_advocate_and_nothing_else():
    """Reflection, and the rule that bounds it.

    A lesson from a previous cycle may make Grace *more* cautious and may never
    satisfy a gate condition (hard rule 5). It reaches the advocate's prompt —
    the agent whose job is to argue — and it reaches nothing that decides.
    """
    from grace.swarm import build_deliberation_swarm

    lessons = ("2026-09-01: a size conflict on this household resolved in the family's favour",)
    swarm = build_deliberation_swarm([], prior_lessons=lessons)
    advocate = swarm.nodes["advocate"].executor
    assert lessons[0] in advocate.system_prompt

    for role in ("verifier", "referee"):
        assert lessons[0] not in swarm.nodes[role].executor.system_prompt, (
            f"a prior lesson reached the {role}; only the advocate argues from history"
        )


def test_the_advocate_is_told_a_lesson_cannot_settle_the_case():
    from grace.swarm import build_deliberation_swarm

    swarm = build_deliberation_swarm([], prior_lessons=("anything",))
    prompt = swarm.nodes["advocate"].executor.system_prompt.lower()
    assert "advisory" in prompt or "cannot settle" in prompt


def test_no_lessons_leaves_every_prompt_unchanged():
    """The nine clean households never deliberate, and an ambiguous case with
    no history must read exactly as it did before this feature existed."""
    from grace.swarm import build_deliberation_swarm

    with_none = build_deliberation_swarm([], prior_lessons=())
    default = build_deliberation_swarm([])
    for role in ("advocate", "verifier", "referee"):
        assert (with_none.nodes[role].executor.system_prompt
                == default.nodes[role].executor.system_prompt)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_swarm.py -k lesson -q -p no:warnings`
Expected: FAIL — `TypeError: build_deliberation_swarm() got an unexpected keyword argument 'prior_lessons'`.

- [ ] **Step 3: Implement**

In `grace/swarm.py`, change the signature to:

```python
def build_deliberation_swarm(read_tools: list, *, prior_lessons: tuple[str, ...] = ()) -> Swarm:
```

and build the advocate's prompt with an appended block when lessons exist:

```python
    # Reflection, bounded. A lesson from a previous cycle reaches the *advocate*
    # — the agent whose job is to argue the family's case — and nothing that
    # decides. Hard rule 5: it may make Grace more cautious and may never
    # satisfy a gate condition. The verifier checks claims against readable
    # facts and the referee concludes; a remembered claim is neither, and
    # feeding it to either would let a model's memory of one household argue
    # another's case.
    advocate_prompt = ADVOCATE_PROMPT
    if prior_lessons:
        advocate_prompt += (
            "\n\nFrom previous cycles, advisory only — this is history, it "
            "cannot settle the present case, and the verifier will check every "
            "claim you make against the current record:\n"
            + "\n".join(f"- {lesson}" for lesson in prior_lessons)
        )
```

The prompt constants are already named `ADVOCATE_PROMPT`, `VERIFIER_PROMPT`, and `REFEREE_PROMPT` (verified in `grace/swarm.py`), so no extraction is needed — pass `advocate_prompt` where `system_prompt=ADVOCATE_PROMPT` is today.

In `grace/graph.py`, thread it through:

```python
def build_case_graph(
    store: CaseStore, case_id: str, today: date, channel: Channel,
    *, prior_lessons: tuple[str, ...] = (),
) -> Graph:
```

and `deliberate = build_deliberation_swarm(read_tools, prior_lessons=prior_lessons)`.

In `grace/entrypoint.py`, pass the recalled facts:

```python
        graph = build_case_graph(store, case_id, today, channel, prior_lessons=remembered)
```

- [ ] **Step 4: Run to verify passing, and that the split holds**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: PASS, at least **935 tests**.

Run the 9/3 check from Task 2 Step 4. Expected: `9 act / 3 escalate`.

- [ ] **Step 5: Sabotage**

| Sabotage | Must fail |
|---|---|
| append the lessons to the verifier's prompt as well | `test_prior_lessons_reach_the_advocate_and_nothing_else` |
| append the lessons without the advisory wording | `test_the_advocate_is_told_a_lesson_cannot_settle_the_case` |
| append an empty block when `prior_lessons` is `()` | `test_no_lessons_leaves_every_prompt_unchanged` |

- [ ] **Step 6: Commit and redeploy**

```bash
git add grace/swarm.py grace/graph.py grace/entrypoint.py tests/test_swarm.py
git commit -m "feat: a previous cycle's lesson reaches the advocate, and nothing that decides

The README called reflection 'genuinely the originality differentiator' and
deferred it because it could not be built before a deployed sweep existed to
reflect on. Seven sweeps exist and the sweep now writes to Memory, so the
stated reason no longer holds.

Bounded by hard rule 5 and by topology. A lesson reaches the advocate — whose
job is to argue — and neither the verifier, which checks claims against
readable facts, nor the referee, which concludes. A remembered claim is neither
a readable fact nor a conclusion, and feeding it to either would let a model's
memory of one household argue another's case. The gate never sees it at all.

Three sabotages watched failing, including lessons leaking to the verifier."
```

Then repeat Task 6's Steps 2–5. **If the sweep is not 9/3, revert this task and ship without it** — it is the optional one.

---

### Task 8: The README says only what the code supports

**Depends on Tasks 1, 5, 6 (or their revert).** Write this **after** Task 6's outcome is known, because what it says about Memory depends on whether Task 6 passed.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Builder ID**

Replace `**@sorour**` (line ~447) and `**AWS Builder ID:** `@sorour`` (line ~452) with `mohamedsorour1998@gmail.com`, and add the sentence: *"The Devpost form asks for the email used to create the Builder ID."*

- [ ] **Step 2: "Americans"**

In the opening block and "The problem" section, change:
- "more than 25 million people lost coverage" → "more than 25 million **Americans** lost coverage"
- "Roughly 17 million people lost health insurance" → "Roughly 17 million **Americans** lost health insurance"
- "That is roughly 17 million people who lost health insurance they were entitled to" → "…17 million **Americans**…"

Leave the citation footnote's wording alone — it quotes the sources.

- [ ] **Step 3: The Memory claim, matched to reality**

If Task 6 Step 5 showed events **and** `recall_facts` returned them, change the Memory row to:

```markdown
| **Memory** | Shipped and **in use**. `grace_household_memory`, 365-day expiry, one actor per household. Every sweep writes what it concluded; the next cycle reads it back into the graph's task text as advisory history. Recall is never consulted by the gate — `evaluate()` has no parameter it could occupy. |
```

If events were written but retrieval stayed empty, say exactly that instead:

```markdown
| **Memory** | Shipped, and honest about which half works. `grace_household_memory` is ACTIVE with one actor per household, and every sweep writes what it concluded — confirmed by listing events, not by the write returning. Retrieval into `/facts/` had not surfaced records at the time of writing; the read path is wired and returns `()` until extraction catches up, which degrades outreach quality and can never change a verdict. |
```

If Task 6 failed and was reverted:

```markdown
| **Memory** | **Provisioned, not consulted by the agent.** `grace_household_memory` is ACTIVE with the right namespaces, and no code path reads or writes it — `AgentCoreMemorySessionManager.create_multi_agent` raises `NotImplementedError`, so a session manager cannot attach to Grace's Graph, and the data-plane wiring did not land before the freeze. Claiming otherwise is the one thing that turns a working entry into a dishonest one. |
```

and change "**Four AgentCore surfaces, not five**" to "**Three AgentCore surfaces in use, with a fourth provisioned**", and remove the `DECIDE <--> MEM` edge in Task 9's diagram work.

- [ ] **Step 4: The agents-as-tools claim**

Replace the "Agents-as-tools" paragraph (~line 159) with:

```markdown
**Agents-as-tools** — context isolation for the outreach drafter. When a
document is missing, `decide` calls `draft_family_message`, which runs its own
agent on Nova 2 Lite with **no tools at all** and returns the message text. It
is bound to nothing — no store, no case id — so its arguments are content
rather than identity: which document, by when, in which language. Translation
chatter never enters the eligibility reasoning's context, and the drafter
cannot send anything: `send_family_message` stays on `decide` behind the gate.
```

- [ ] **Step 5: Show the multilingual claim**

In "How a household gets into Grace", after the document paragraph, add:

```markdown
**The outreach is in the family's language, and the demo shows it.** `c-010` —
the household missing `proof_of_residency` — reads Spanish, so the message in
its audit trail is Spanish. That is the one place the README's second sentence
is checkable rather than asserted, and `tests/test_demo_dates.py` holds the
property that the household Grace texts is not an English speaker.
```

- [ ] **Step 6: The rule packs' sources**

After the "Grace may act alone only if" list, add:

```markdown
**Every number in a rule pack names its authority.** `certification_period_months`,
the renewal window, the grace period, and the immaterial-income band each carry a
`sources` entry citing the provision that establishes them — or the literal string
`policy choice` where the regulation gives a range and the value is this
deployment's decision within it. An uncited number that decides whether a family
keeps coverage is indistinguishable from an invented one, and `load_pack` refuses a
malformed `sources` block with the same `InvalidRulePack` it raises for every other
malformed field.
```

- [ ] **Step 7: The evals sentence**

In the deferred table, replace the Skills row's neighbour or add:

```markdown
| **`strands-agents-evals`** | The trajectory evals are ordinary pytest functions in `evals/` that read the ledger — the ground truth for what executed, which a transcript-based eval would miss. The package was not adopted because it depends on `strands-agents-tools`, 25 packages including `slack-bolt` and `pillow` that Grace never imports. |
```

- [ ] **Step 8: Re-lead the opening**

The README currently reaches "What makes Grace different: an escalation boundary" at line ~70. In this hackathon that idea is the dominant pattern — at least ten competing builder.aws posts describe it. Change that heading's opening sentence from:

> Most agents are a model with tools and a prompt asking it to be careful. Grace has an **escalation boundary**…

to:

```markdown
Plenty of agents can file a form. What Grace does that a form-filler cannot is
**refuse** — and prove where the refusal comes from. It has run unattended on
AWS every day since deploy, handling nine of twelve households alone and
escalating three with typed reasons, and the refusal is enforced in code rather
than requested in a prompt.

Three layers, strongest first:
```

- [ ] **Step 9: Gates and commit**

Run all five gates. Then:

```bash
git add README.md
git commit -m "docs: the README says only what the code supports

Builder ID is the email the Devpost form asks for. The unwinding figures say
'Americans', which is accurate — Medicaid is a US programme — and lands harder
with judges who are American.

Two claims corrected against what shipped: Memory and agents-as-tools now
describe what the code does, in whichever form Task 6's deployed verification
established. The rule packs' new citations and the Spanish outreach are
documented where a reader would look for them.

Re-led the differentiator section. The escalation boundary is the dominant
pattern in this hackathon's field — at least ten competing posts describe it —
so leading with it concedes the ground. What is rarer is a system that has run
unattended for a week and can prove where its refusals come from."
```

---

### Task 9: The architecture diagram names Strands

**Why.** The hackathon FAQ lists the required diagram contents: user interface, **Strands Agents (the core agent and its agentic loop: model → tools → reasoning → response)**, tools and integrations, AWS services, and output. `docs/architecture.md` mentions "Strands" exactly **once**, in prose, and never in the diagram. The Devpost update says: *"Name Strands Agents explicitly — it's one of the first things reviewed."*

**Files:**
- Modify: `docs/architecture.md`
- Regenerate: `docs/architecture.png`

- [ ] **Step 1: Label the runtime subgraph**

Change:

```
    subgraph runtime["AgentCore Runtime — grace_grace-oTyyvo8stE"]
```

to:

```
    subgraph runtime["AgentCore Runtime — grace_grace-oTyyvo8stE<br/><b>Strands Agents SDK</b> · Graph · Swarm · agents-as-tools · SteeringHandler · HookProvider"]
```

- [ ] **Step 2: Add the agent-loop inset**

After the `runtime` subgraph's closing brace, add:

```
    subgraph loop["One Strands node's agentic loop"]
        direction LR
        L1["model<br/>Amazon Nova"] --> L2["tool selection"]
        L2 --> L3{{"SteeringHandler<br/><b>the authority gate</b><br/>Proceed · Guide · Interrupt"}}
        L3 -->|"permitted"| L4["tool executes<br/><i>Sequential</i>"]
        L3 -->|"refused"| L5["escalate to a human"]
        L4 --> L6["HookProvider<br/>appends to the ledger"]
        L6 --> L1
    end
    DECIDE -.->|"every state-changing call"| L3
```

and add `class L3 gate` to the `class` lines at the bottom.

- [ ] **Step 3: Add the drafter and Memory to the runtime subgraph**

Inside `runtime`, after `DECIDE`:

```
        DRAFT["draft_family_message<br/><i>agents-as-tools</i><br/>Nova 2 Lite · no tools · no store"]
```

and the edge `DECIDE -.->|"needs words for a family"| DRAFT`.

If Task 6 confirmed Memory, keep `DECIDE <--> MEM` and relabel it `DECIDE <-->|"writes the outcome · reads prior cycles"| MEM`. If Task 6 failed, **delete that edge** and mark `MEM` as `AgentCore Memory<br/><i>provisioned, not consulted</i>`.

- [ ] **Step 4: Verify the Mermaid renders**

Paste the block into `mermaid.live` or run a local render. A syntax error makes the whole diagram vanish on GitHub, which is worse than the unlabelled version.

- [ ] **Step 5: Regenerate the PNG**

Use whatever produced the existing `docs/architecture.png` (283 KB). Confirm the new file is non-trivial in size and that the Strands label is legible at 100%.

- [ ] **Step 6: Commit**

```bash
git add docs/architecture.md docs/architecture.png
git commit -m "docs: the diagram names Strands Agents and shows the agentic loop

The hackathon FAQ lists what an architecture diagram must contain, and 'Strands
Agents: the core agent and its agentic loop (model → tools → reasoning →
response)' was the one element missing — the word appeared once in prose and
never in the diagram, while a Devpost update says naming it explicitly is one
of the first things reviewed.

Adds an inset showing one node's loop with the authority gate sitting in it as
a SteeringHandler, which is also the clearest single picture of what makes
Grace different from an agent with a careful prompt."
```

---

### Task 10: The Devpost text description

**Why.** The submission form requires a text description, and the rules say judges *"may choose to judge based solely on the text description, images, and video."* No such artifact exists — the README is 500 lines and is not a substitute.

**Files:**
- Create: `docs/devpost-description.md`

- [ ] **Step 1: Write it**

Start from Appendix A of the spec (`docs/superpowers/specs/2026-09-10-grace-grand-prize-spec.md`), which is a complete draft. Adjust three things against what actually shipped:

1. The surface list and the Memory sentence must match Task 8's outcome.
2. "three different Nova models" stays only if the swarm is unchanged.
3. Add one sentence on the outreach drafter, since it is now the third pattern.

Target 350–450 words. No headers deeper than one level, no code blocks — it is pasted into a form field. **Name "Strands Agents SDK" and "Amazon Bedrock AgentCore" in the first paragraph.**

- [ ] **Step 2: Check the length and the required names**

```bash
.venv/bin/python -c "
import pathlib, re
t = pathlib.Path('docs/devpost-description.md').read_text()
body = re.sub(r'^#.*$', '', t, flags=re.M)
print('words:', len(body.split()))
for term in ('Strands Agents', 'AgentCore', 'Medicaid', 'Americans', 'caseworker'):
    print(f'  {term}:', term in t)
"
```

Expected: 350–450 words, every term `True`.

- [ ] **Step 3: PII check**

```bash
.venv/bin/python -c "
import re, pathlib
NAMES = ['Mensah','Rivera','Okonkwo','Fitzgerald','Yamamoto','Nguyen','Alvarez','Kowalski','Haddad','Petrov','Silva','Bergstrom','Delacroix','Torres','Abebe']
pat = re.compile('|'.join(NAMES + [r'\+1555']), re.I)
print('self-test:', sorted(set(pat.findall('Yamamoto +15555550123'))))
print('description:', sorted(set(pat.findall(pathlib.Path('docs/devpost-description.md').read_text()))) or 'NONE')
"
```

- [ ] **Step 4: Commit**

```bash
git add docs/devpost-description.md
git commit -m "docs: the Devpost submission description

A required artifact that did not exist. The rules say judges may choose to
judge based solely on the text description, images, and video — the README is
500 lines and is not a substitute for a form field.

Leads with the problem in two sentences, names Strands Agents SDK and Amazon
Bedrock AgentCore in the first paragraph per the Devpost guidance that naming
the SDK explicitly is one of the first things reviewed, then who it is for, how
it works, what is deployed, and what is deliberately not claimed."
```

---

### Task 11: Three builder.aws posts

**Why.** Bonus scoring is 0.2 per post to a maximum of 0.6, on a 5-point scale — **up to 12% of the maximum score**, and the cheapest points available. One draft exists. The other two are already written as sections of existing documents; this task lifts and expands them.

**Files:**
- Modify: `docs/builder-blog-post.md` (post 1)
- Create: `docs/builder-blog-post-2-deployed-is-not-written.md`
- Create: `docs/builder-blog-post-3-sabotage.md`

- [ ] **Step 1: Correct post 1**

In `docs/builder-blog-post.md`:
- `**Team:** Mohamed Sorour (@sorour)` → `**Team:** Mohamed Sorour (mohamedsorour1998@gmail.com)`
- "more than 25 million people lost coverage" → "**Americans**"; "About 17 million people" → "**Americans**"
- "**874 Python tests and 211 frontend tests**" → the counts after Task 7 (re-measure; do not copy)
- The surface list and the "How I Built This" architecture block must match Task 8's Memory outcome
- Add two sentences to "Three choices worth explaining" on the outreach drafter and why its arguments are safe

- [ ] **Step 2: Write post 2**

Create `docs/builder-blog-post-2-deployed-is-not-written.md`. Title: **`Agents for Humans: the defect that was invisible because nothing failed`**.

Source material, all already written: the "defect that was invisible" section of post 1, the "That took two fixes" passage in `README.md`, and `infra/verify_deployed.py`'s docstring. Structure:

1. **The symptom that wasn't one.** A caseworker adds a household through the dashboard. It renders. The daily sweep never visits it. Nothing fails — green schedule, `SUCCEEDED` executions, correct 9/3 count.
2. **Two defects wearing one symptom.** The deployed image was three days older than the code that could read record rows; and the EventBridge target carried a hardcoded list of twelve case ids. Fixing either alone leaves the household invisible.
3. **Why neither could fail.** A frozen caseload cannot report that it has gone stale.
4. **How it was found.** *Is the thing I deployed the thing I wrote?* Compare the running artifact's build time against the commit; read the orchestrator's actual input payload.
5. **The fix, and the guard.** `ListCases` over the directory partition; `CheckDirectoryComplete` failing the execution on `LastEvaluatedKey`; `infra/verify_deployed.py` as a read-only drift check that reports every mismatch rather than the first.
6. **The general form.** A store change, a container image, and an orchestrator's input list are three separate places a caseload lives. A claim about your agent is only as deployed as the last of them.

1,200–1,800 words. Include the measured evidence: `13 outcomes, 9 acted / 4 escalated` with the probe household present, `12 outcomes, 9 acted / 3 escalated` after removing it. One `<replace this text by a screenshot of …>` marker for the Step Functions graph showing `ListCases`.

- [ ] **Step 3: Write post 3**

Create `docs/builder-blog-post-3-sabotage.md`. Title: **`Agents for Humans: a test you never watched fail is a sentence that agrees with you`**.

Source material: the "test that passed with the safety removed" and "through-line" sections of post 1, plus the audit findings. Structure:

1. **The headline safety test that passed with the gate bypassed.** The fake filed nothing, so a different branch escalated for every input. True of the run, unproven by the test. *"When several code paths converge on the same observable result, asserting that result says nothing about which produced it."*
2. **The trap that would have expired.** A ledger row stamped `2026-10-01T12:00Z` counted as "this run" only because that date was in the future, and its arming assertion asked the all-time question — structurally unable to notice its own disarming.
3. **The comment that vouched for a check nobody performed.** `verifySession` "refuses on every page"; no page read a cookie. A forged literal string returned 200 with every record.
4. **The no-drift test that passed against a `return []` stub.** Caught by an implementer who measured its own test rather than trusting it.
5. **The method.** Break the line the test protects, watch the *named* test fail, restore. And the corollary that a crashing sabotage scores as a survivor on an assertion-counting harness — weaken a bound rather than removing it.
6. **Why it is worth the cost, in this domain specifically.** The failure mode is a family losing health coverage with no error message anywhere.

1,200–1,800 words. One `<replace this text by a screenshot of …>` marker for a sabotage run showing the named test failing.

- [ ] **Step 4: Verify all three**

```bash
for f in docs/builder-blog-post.md docs/builder-blog-post-2-deployed-is-not-written.md docs/builder-blog-post-3-sabotage.md; do
  echo "=== $f ==="
  head -1 "$f"
  .venv/bin/python -c "
import pathlib, re, sys
t = pathlib.Path('$f').read_text()
print('  words:', len(re.sub(r'\`\`\`.*?\`\`\`', '', t, flags=re.S).split()))
print('  title has Agents for Humans:', 'Agents for Humans' in t.splitlines()[0])
print('  markers:', t.count('<replace this text'))
NAMES=['Mensah','Rivera','Okonkwo','Fitzgerald','Yamamoto','Nguyen','Alvarez','Kowalski','Haddad','Petrov','Silva','Bergstrom','Delacroix','Torres','Abebe']
pat=re.compile('|'.join(NAMES+[r'\+1555']), re.I)
print('  PII:', sorted(set(pat.findall(t))) or 'NONE')
"
done
```

Every title must contain "Agents for Humans" (the rules require it). PII must be NONE.

- [ ] **Step 5: Commit**

```bash
git add docs/builder-blog-post.md docs/builder-blog-post-2-deployed-is-not-written.md docs/builder-blog-post-3-sabotage.md
git commit -m "docs: three builder.aws posts, not one

Bonus scoring is 0.2 per post to a maximum of 0.6 on a 5-point scale — up to
12% of the maximum score and the cheapest points available. Posts 2 and 3 were
already written as sections of post 1 and of the verification docs; this lifts
them out and gives each the room its argument needs.

Post 1 corrected: Builder ID email, 'Americans', re-measured test counts, and
the Memory and agents-as-tools claims matched to what shipped.

Post 2 is the deployed-is-not-written story — two defects wearing one symptom,
neither of which could fail. Post 3 is the sabotage methodology, which nothing
else in this hackathon's field describes."
```

**Publishing is a human step.** Each post must be public on builder.aws.com before the deadline, with "Agents for Humans" in the title.

---

### Task 12: The handout, CLAUDE.md, and the final verification

**Files:**
- Modify: `docs/demo-video-handout.md`, `CLAUDE.md`

- [ ] **Step 1: Update the handout**

- "Americans" in the 0:00–0:45 script block, matching Task 8's wording exactly.
- Builder ID → the email.
- Figure-provenance table: re-measured test counts, runtime version, and two new rows:

```markdown
| the outreach is in the family's language | `c-010` reads Spanish; the message body in its ledger is Spanish. **Say "in the family's own language" and then show it** — this is the one place that claim is checkable. |
| Memory is read and written | `list_events` on the household's actor after a sweep. **Match the wording to `README.md`'s Memory row** — if only the write half is confirmed, say only that. |
```

- Add a beat to the 2:15–3:15 section, after the documents-panel paragraph:

```markdown
> And look at the message Grace sent. This family reads Spanish, so Grace wrote
> in Spanish — a separate agent drafts it with no tools and no access to the
> case, and `decide` sends exactly what it returned. The agent that writes the
> words cannot send them.

`<replace this text by a screenshot of the c-010 ledger showing the Spanish family_message_sent body>`
```

- In "Things not to claim", add:

```markdown
- Do **not** claim Memory does more than it does. Say exactly what `README.md`'s
  Memory row says. If only the write half is confirmed, "Grace records what each
  sweep concluded for the next cycle" is true and "Grace remembers and recalls"
  is not.
```

- [ ] **Step 2: Update CLAUDE.md**

Add to the current-state section:

```markdown
**Two overclaims were closed on 2026-09-10, and the way they were found is the
transferable part.** `README.md` listed AgentCore Memory as "Shipped" and named
agents-as-tools as one of three multi-agent patterns. `build_session_manager`
had zero callers; no `@tool` wrapped an `Agent`; and `models.py` defined
`outreach` and `judge` roles that nothing called. All three were found by
grepping the *request path* for the thing the documentation claimed, rather than
by reading the documentation — the same "is the thing I deployed the thing I
wrote?" question that found the frozen caseload.

**`AgentCoreMemorySessionManager` cannot attach to a Graph.** `create_multi_agent`
raises `NotImplementedError("MultiAgent is not implemented for this repository")`,
verified against the installed package. `GraphBuilder.set_session_manager` exists,
so this is syntactically possible and would raise on the first sweep.
`grace/memory.py`'s docstring reaches the right conclusion by the wrong route: it
cites a `ValueError` about agents inside a Graph, which is a different guard.
`grace/household_memory.py` uses the data-plane client instead.

**A role defined in `models.py` and called by nothing is an overclaim.**
`test_every_model_role_is_referenced_by_some_module` walks the package from disk
and fails on any unreferenced role, `judge` exempted by name with its reason.
Delete the exemption if LLM steering ships; delete the role if it does not.
```

- [ ] **Step 3: Final verification**

Run every gate, the live drift check, and the deployed probe:

```bash
.venv/bin/python -m pytest -q -p no:warnings
cd web && npm run typecheck && npm run lint && npm run test && npm run build && cd ..
.venv/bin/python -m infra.verify_deployed
aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id grace_grace-oTyyvo8stE \
  --region us-east-1 --query '{v:agentRuntimeVersion,s:status}' --output text
aws amplify list-jobs --app-id dbi97xicbjbv8 --branch-name main --region us-east-1 \
  --max-results 1 --query 'jobSummaries[0].{job:jobId,status:status,commit:commitId}' --output text
git status --porcelain | wc -l
```

Then re-check both DynamoDB invariants with the scan from Task 6 Step 4, and confirm `/` still renders `9 handled alone, 3 waiting on you.`

- [ ] **Step 4: Commit and push**

```bash
git add docs/demo-video-handout.md CLAUDE.md
git commit -m "docs: the handout shows the Spanish outreach, and CLAUDE.md records how the overclaims were found

The handout gains the beat that demonstrates the README's second sentence
rather than asserting it, and a 'do not claim' entry for Memory so the video's
wording tracks whichever half of it is confirmed.

CLAUDE.md records the transferable finding: both overclaims were found by
grepping the request path for what the documentation claimed, not by reading
the documentation — and that AgentCoreMemorySessionManager.create_multi_agent
raises, so a session manager cannot attach to a Graph however syntactically
inviting GraphBuilder.set_session_manager looks."
git push origin main
```

---

## Risks

| Risk | Mitigation |
|---|---|
| **The redeploy breaks the 9/3 split three days before the deadline.** Tasks 1, 2, and 5 all ship in the container image. | Task 6 is an explicit gate with a stated revert: roll back to v5 **the same day** and take Task 8's downgrade path. A broken demo loses more than a missing feature. Deploy Thursday, not Sunday. |
| **The drafter adds latency or a failure mode to every escalating case.** One extra Bedrock call inside `decide`'s loop. | It runs only on document-only cases — one household of twelve. If a sweep times out, the fallback is `decide` writing the message itself: revert Task 1's prompt change only, keeping the tool for the tests. |
| **Nova writes English despite `language: es`.** | Task 6 Step 4 checks the actual body. If it is English, the drafter's prompt is the single place to fix it (it has one job and language is an explicit argument) — that is exactly why the drafter exists rather than a paragraph in `decide`'s prompt. |
| **Memory's extraction into `/facts/` is asynchronous and may not surface during verification.** | Task 6 Step 5 checks `list_events` (synchronous, proves the write) separately from `recall_facts` (asynchronous). Task 8 has a README wording for each outcome. Never claim retrieval works because the write returned. |
| **A regulation citation is wrong.** | Task 3 Step 1 requires verifying each against the actual text, and `policy choice` is an accepted answer. A wrong citation is worse than none — it is the exact "asserting a property you did not verify" defect this project has found six times. |
| **The `c-010` language change breaks a test that pinned it.** | Task 2 Step 5 runs the full suite and instructs reading the test before changing it. A grep found nothing pinning it, but the suite is the check. |
| **Tasks run long and the video slips.** | Tasks 8–12 are documents and can be done after the Friday code freeze. **Task 6 failing does not block the video** — it changes what Task 8 says. The video is the only pass/fail artifact; if Saturday arrives with tasks incomplete, stop and record. |

## Out of scope, and why

**CloudWatch traces / `aws-opentelemetry-distro`.** Documented as not working, honestly. Changing the telemetry provider on the deployed runtime four days out risks the one thing that must not break, for a nicer screenshot.

**A second state's rule pack (spec P2.2).** Depends on Task 3's citation research being fast enough to extend. If Task 3 finishes Wednesday with sources that make a Texas pack straightforward, it is a two-hour addition; otherwise it invents parameters, which is the failure Task 3 exists to prevent.

**`LLMSteeringHandler` on the drafter (spec P2.3).** Only if Tasks 1–11 are done by Saturday. It uses the `judge` role, which Task 1's test currently exempts by name.

**Rewriting the swarm, the gate, or the dashboard.** They work and are verified. Every hour here is an hour not spent on the video.

**AgentCore Gateway, real SMS, Skills.** Correctly deferred with written reasons that survive scrutiny.
