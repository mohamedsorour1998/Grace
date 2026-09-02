"""Tool construction tests.

The property under test throughout is *capability shape*, not model behaviour:
a read tool with no household parameter cannot be redirected to another
family's record, because there is no argument to poison. That is layer 2 of the
escalation boundary (CLAUDE.md), and it is a structural claim about the tool
spec that a test can prove without invoking a model.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import pkgutil
from datetime import date
from pathlib import Path

import pytest

import grace
import grace.authority as authority_module
import grace.models as models
import grace.tools.action as action_module
import grace.tools.read as read_module
from grace.authority import ACTION_TOOLS
from grace.cases.models import Case, Document, Household
from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.models import ADVERSARIAL_ROLES, BANNED_MODEL_IDS, _ROLES, nova
from grace.rules.pack import InvalidRulePack, load_pack
from grace.tools.action import TranscriptChannel, make_action_tools
from grace.tools.read import make_read_tools


# Pinned, as in every other test module: fixture c-002 goes `closed` on
# 2026-10-31, so a real `date.today()` here would silently change the verdicts.
TODAY = date(2026, 10, 1)


@pytest.fixture
def store() -> InMemoryCaseStore:
    return InMemoryCaseStore(load_fixture_cases())


def _by_name(tools) -> dict:
    return {t.tool_spec["name"]: t for t in tools}


# --------------------------------------------------------------------------
# Capability shape: no argument to poison
# --------------------------------------------------------------------------


def test_read_tools_take_no_case_id_argument(store):
    """Identity comes from the session, never from a model-supplied argument.

    A tool with no household parameter cannot be redirected to another
    family's case by prompt injection — there is nothing to poison.
    """
    tools = make_read_tools(store, case_id="c-001", today=TODAY)
    names = {t.tool_spec["name"] for t in tools}
    assert names == {"read_case", "check_window", "list_documents"}
    for tool in tools:
        schema = tool.tool_spec["inputSchema"]["json"]
        assert schema.get("properties", {}) == {}, f"{tool.tool_spec['name']} takes arguments"
        # `required` is absent entirely for a no-argument tool in strands
        # 1.54.0 — assert on both keys so a future SDK version that emits an
        # empty `properties` alongside a populated `required` cannot pass.
        assert not schema.get("required"), f"{tool.tool_spec['name']} has required arguments"


def test_no_tool_anywhere_accepts_an_identity_argument(store):
    """The rule is global, not per-tool: no tool Grace exposes may take a
    case, household, or phone argument.

    Read tools take nothing at all; the two action tools that do take an
    argument take *content* (`body`, `question`), never identity. Asserted over
    the union so adding a fourth tool with a `case_id` parameter fails here
    rather than in review.
    """
    forbidden = {"case_id", "household_id", "household", "case", "phone", "actor_id", "user_id"}
    tools = make_read_tools(store, "c-001", TODAY) + make_action_tools(
        store, "c-001", TranscriptChannel()
    )
    for tool in tools:
        props = set(tool.tool_spec["inputSchema"]["json"].get("properties", {}))
        assert not props & forbidden, f"{tool.tool_spec['name']} exposes {props & forbidden}"


def test_an_injected_identity_argument_cannot_redirect_a_read(store):
    """The load-bearing test for layer 2: a model that emits an extra
    `case_id` in its toolUse must still read the bound case.

    Verified against the real agent invocation path (`tool.stream`), not just a
    direct Python call, because the two behave differently and only one of them
    is how a model reaches a tool. strands validates the toolUse input against
    the tool spec and **silently discards** an argument the schema does not
    declare — so the injection does not even produce an error the model could
    learn from. A direct Python call raises `TypeError` instead (asserted
    below), but no model ever takes that path.
    """
    tools = _by_name(make_read_tools(store, "c-001", TODAY))
    use = {"toolUseId": "t1", "name": "read_case", "input": {"case_id": "c-002"}}

    async def run() -> dict:
        async for event in tools["read_case"].stream(use, {}):
            return event["tool_result"]
        raise AssertionError("tool produced no result")

    result = asyncio.run(run())
    assert result["status"] == "success"
    text = result["content"][0]["text"]
    assert "Rivera" in text
    assert "Okonkwo" not in text

    # The direct-call path is stricter; both are safe, for different reasons.
    with pytest.raises(TypeError):
        tools["read_case"](case_id="c-002")


def test_an_injected_phone_cannot_redirect_an_outbound_message(store):
    """Same property on the action side, where the consequence is worse: a
    model that injects a `phone` must not be able to send one family's renewal
    details to a number it chose. `body` is content; the destination comes from
    the bound case."""
    channel = TranscriptChannel()
    tools = _by_name(make_action_tools(store, "c-001", channel))
    use = {
        "toolUseId": "t1",
        "name": "send_family_message",
        "input": {"body": "hola", "phone": "+19998887777", "case_id": "c-002"},
    }

    async def run() -> dict:
        async for event in tools["send_family_message"].stream(use, {}):
            return event["tool_result"]
        raise AssertionError("tool produced no result")

    assert asyncio.run(run())["status"] == "success"
    assert channel.sent == [(store.get("c-001").household.phone, "hola")]
    assert [e.kind for e in store.ledger("c-001")] == ["family_message_sent"]
    assert store.ledger("c-002") == []


# --------------------------------------------------------------------------
# Read tools
# --------------------------------------------------------------------------


def test_read_case_returns_the_bound_case_only(store):
    tools = _by_name(make_read_tools(store, "c-001", TODAY))
    out = tools["read_case"]()
    assert "Rivera" in out
    assert "Okonkwo" not in out


def test_read_case_does_not_leak_the_household_phone_number(store):
    """The phone is PII and the model never needs it: `send_family_message`
    reads it from the bound case itself. Keeping it out of the tool result
    keeps it out of the model transcript, and therefore out of any span or log
    that captures one."""
    out = _by_name(make_read_tools(store, "c-001", TODAY))["read_case"]()
    assert store.get("c-001").household.phone not in out


def test_read_case_says_no_change_rather_than_none(store):
    """`None` means "not reported this cycle", which is the ordinary case. A
    literal `reported: None` reads to a model (and a caseworker) as missing
    data, which invites exactly the wrong inference — that something failed to
    load and the case needs a human."""
    out = _by_name(make_read_tools(store, "c-001", TODAY))["read_case"]()
    assert "None" not in out
    assert "not reported this cycle" in out


def test_read_case_reports_a_figure_the_family_did_report(store):
    out = _by_name(make_read_tools(store, "c-011", TODAY))["read_case"]()
    assert "260000" in out


def test_read_case_reports_a_reported_zero_income_as_a_figure(store):
    """0 is a real reported income, not an absence — a family whose income
    dropped to zero is the most eligibility-relevant case Grace will see. It
    must never render as "not reported"."""
    zero = Case(
        case_id="c-zero",
        household=Household(
            household_id="h-zero",
            display_name="The Zero Household",
            language="en",
            phone="+15550000099",
            monthly_income_cents=180_000,
            size=2,
        ),
        program="medicaid",
        state="NY",
        cert_end=date(2026, 10, 15),
        reported_income_cents=0,
        reported_size=0,
    )
    out = _by_name(make_read_tools(InMemoryCaseStore([zero]), "c-zero", TODAY))["read_case"]()
    assert "not reported this cycle" not in out
    assert "0 cents" in out


def test_check_window_reports_status(store):
    out = _by_name(make_read_tools(store, "c-001", TODAY))["check_window"]()
    assert "open" in out.lower()


def test_check_window_uses_the_bound_date_not_today(store):
    """The sweep date is bound at construction. A `date.today()` inside the
    tool would turn the 9-act/3-escalate demo into 8/4 on 2026-10-31."""
    out = _by_name(make_read_tools(store, "c-002", date(2026, 10, 31)))["check_window"]()
    assert "closed" in out.lower()
    assert "2026-10-31" in out


def test_check_window_fails_closed_on_an_unloadable_pack(store):
    """`load_pack` raises `InvalidRulePack` for a missing or corrupt pack.
    check_window must not let that reach the model as a raw exception carrying
    a filesystem path, and must not report a window it could not verify —
    it says so plainly instead, so the model's only remaining move is to
    escalate."""
    broken = Case(
        case_id="c-broken",
        household=Household(
            household_id="h-broken",
            display_name="The Broken Household",
            language="en",
            phone="+15550000098",
            monthly_income_cents=100_000,
            size=1,
        ),
        program="no_such_program",
        state="ZZ",
        cert_end=date(2026, 10, 15),
    )
    tools = _by_name(make_read_tools(InMemoryCaseStore([broken]), "c-broken", TODAY))
    for name in ("check_window", "list_documents"):
        out = tools[name]()
        assert "cannot be verified" in out.lower(), name
        assert "escalate" in out.lower(), name
        # `_UNVERIFIABLE` is a fixed constant, so a bare "/" check against it
        # can never fail regardless of what the tool actually does — the real
        # property is that the raw InvalidRulePack message (which does carry
        # an absolute path, e.g. ".../no_such_program-zz.yaml") never reaches
        # this string at all.
        assert out == read_module._UNVERIFIABLE, name
        try:
            load_pack("no_such_program", "ZZ")
        except InvalidRulePack as exc:
            assert str(exc) not in out, name
        else:
            pytest.fail("expected InvalidRulePack for a nonexistent program/state")


def test_list_documents_flags_a_missing_required_document(store):
    out = _by_name(make_read_tools(store, "c-010", TODAY))["list_documents"]()
    assert "proof_of_residency" in out
    assert "MISSING" in out


def test_list_documents_ignores_documents_the_pack_does_not_require(store):
    """Driven by the pack's list, not the household's. Extra paperwork can
    never be reported as a problem."""
    out = _by_name(make_read_tools(store, "c-001", TODAY))["list_documents"]()
    assert "proof_of_expenses" not in out


@pytest.mark.parametrize("stale_first", [True, False])
def test_list_documents_reports_the_most_recent_copy_regardless_of_order(stale_first: bool):
    """Same rule as the authority gate: never select a document by record
    order. This tool is what a model reads before deciding whether to act, so
    reporting a superseded copy as current would invite a filing the gate
    would refuse — or worse, make the model's summary disagree with the gate's
    verdict on identical facts."""
    stale = Document(doc_id="proof_of_income", received=date(2026, 6, 1))
    fresh = Document(doc_id="proof_of_income", received=date(2026, 9, 20))
    copies = (stale, fresh) if stale_first else (fresh, stale)
    case = Case(
        case_id="c-dup",
        household=Household(
            household_id="h-dup",
            display_name="The Duplicate Household",
            language="en",
            phone="+15550000097",
            monthly_income_cents=180_000,
            size=2,
        ),
        program="medicaid",
        state="NY",
        cert_end=date(2026, 10, 15),
        documents=copies + (Document(doc_id="proof_of_residency", received=date(2026, 3, 1)),),
    )
    out = _by_name(make_read_tools(InMemoryCaseStore([case]), "c-dup", TODAY))["list_documents"]()
    assert "2026-09-20" in out
    assert "2026-06-01" not in out


@pytest.mark.parametrize("expired_first", [True, False])
def test_list_documents_breaks_an_exact_date_tie_conservatively(expired_first: bool):
    """The tie-break must match the gate's exactly: on an identical `received`
    date, the *earliest* expiry wins, so a duplicate can only make the report
    stricter. If this tool and `evaluate` disagreed on which copy is "the"
    document, the model would be reasoning from facts the gate does not share."""
    expired = Document(
        doc_id="proof_of_income", received=date(2026, 9, 20), expires=date(2026, 9, 30)
    )
    valid = Document(doc_id="proof_of_income", received=date(2026, 9, 20), expires=None)
    copies = (expired, valid) if expired_first else (valid, expired)
    case = Case(
        case_id="c-tie",
        household=Household(
            household_id="h-tie",
            display_name="The Tie Household",
            language="en",
            phone="+15550000096",
            monthly_income_cents=180_000,
            size=2,
        ),
        program="medicaid",
        state="NY",
        cert_end=date(2026, 10, 15),
        documents=copies + (Document(doc_id="proof_of_residency", received=date(2026, 3, 1)),),
    )
    out = _by_name(make_read_tools(InMemoryCaseStore([case]), "c-tie", TODAY))["list_documents"]()
    assert "expires 2026-09-30" in out


def test_list_documents_does_not_select_documents_by_record_order():
    """Structural guard on the exact bug CLAUDE.md records: a
    `{d.doc_id: d for d in ...}` comprehension is last-wins by *order*, not by
    which copy is newest. The behavioural tests above catch it for the cases
    they enumerate; this catches a reintroduction anywhere in the module."""
    tree = ast.parse(inspect.getsource(read_module))
    for node in ast.walk(tree):
        if isinstance(node, ast.DictComp) and isinstance(node.key, ast.Attribute):
            assert node.key.attr != "doc_id", (
                "documents must be selected by _most_recent, not by a "
                "last-wins dict comprehension keyed on doc_id"
            )


# --------------------------------------------------------------------------
# Channel
# --------------------------------------------------------------------------


def test_transcript_channel_records_instead_of_sending():
    channel = TranscriptChannel()
    channel.send("+15550000001", "Hola, falta un documento.")
    assert channel.sent == [("+15550000001", "Hola, falta un documento.")]


def test_transcript_channel_snapshot_cannot_rewrite_the_transcript():
    """The transcript is what the dashboard renders as proof of what was said
    to a family. A caller iterating it must not be able to append to it."""
    channel = TranscriptChannel()
    channel.send("+15550000001", "one")
    channel.sent.append(("+15550000001", "forged"))
    assert [b for _, b in channel.sent] == ["one"]


# --------------------------------------------------------------------------
# Action tools
# --------------------------------------------------------------------------


def test_action_tools_are_named_as_the_gate_expects(store):
    """Every state-changing tool Grace exposes must be known to the gate.

    A name the gate does not recognise is treated as a read and bypasses the
    authority check entirely — the exact failure this design exists to prevent.
    """
    names = {t.tool_spec["name"] for t in make_action_tools(store, "c-001", TranscriptChannel())}
    assert names - {"escalate_to_caseworker"} <= ACTION_TOOLS


def test_escalate_to_caseworker_is_not_in_action_tools():
    """CLAUDE.md hard rule 7: escalating is always allowed, so it must not be
    in the gated set. Asserted explicitly rather than left implicit in the
    subtraction above."""
    assert "escalate_to_caseworker" not in ACTION_TOOLS


def test_submit_renewal_writes_to_the_ledger(store):
    tools = _by_name(make_action_tools(store, "c-001", TranscriptChannel()))
    tools["submit_renewal"]()
    kinds = [e.kind for e in store.ledger("c-001")]
    assert "renewal_submitted" in kinds


def test_ledger_details_are_json_safe_scalars(store):
    """`LedgerEntry.detail` rejects anything that is not a JSON-safe scalar,
    because Plan 2 writes it straight to DynamoDB. Every action tool is
    exercised here so a future tool passing a `date` or a `Window` fails in
    this suite rather than at the storage boundary in production."""
    tools = _by_name(make_action_tools(store, "c-001", TranscriptChannel()))
    tools["submit_renewal"]()
    tools["send_family_message"](body="Necesitamos un documento.")
    tools["escalate_to_caseworker"](question="Which income figure applies?")
    entries = store.ledger("c-001")
    assert len(entries) == 3
    for entry in entries:
        for key, value in entry.detail.items():
            assert isinstance(key, str)
            assert isinstance(value, (str, int, float, bool, type(None))), (entry.kind, key, value)


def test_send_family_message_uses_the_bound_households_phone(store):
    """The phone is never a tool argument — `body` is content, identity comes
    from the bound case. A model cannot send a family's renewal details to a
    number it chose."""
    channel = TranscriptChannel()
    tools = _by_name(make_action_tools(store, "c-001", channel))
    tools["send_family_message"](body="Necesitamos un documento.")
    assert channel.sent == [(store.get("c-001").household.phone, "Necesitamos un documento.")]


def test_send_family_message_does_not_log_the_phone_number(store):
    """The ledger goes to DynamoDB and, in Plan 2, is read by evals and a
    dashboard. It records *that* a family was contacted and with what text;
    the number lives on the household record and does not need duplicating
    into an audit row."""
    tools = _by_name(make_action_tools(store, "c-001", TranscriptChannel()))
    tools["send_family_message"](body="Necesitamos un documento.")
    entry = [e for e in store.ledger("c-001") if e.kind == "family_message_sent"][0]
    assert store.get("c-001").household.phone not in str(dict(entry.detail))


def test_send_family_message_does_not_claim_success_without_channel_confirmation(store):
    """CLAUDE.md hard rule 6. A channel that fails must not produce a ledger
    entry or a success string — telling a family their renewal was handled when
    it was not is the specific failure this system must not have."""

    class FailingChannel:
        def send(self, phone: str, body: str) -> str:
            raise RuntimeError("carrier rejected")

    tools = _by_name(make_action_tools(store, "c-001", FailingChannel()))
    with pytest.raises(RuntimeError):
        tools["send_family_message"](body="Necesitamos un documento.")
    assert store.ledger("c-001") == []


def test_escalate_records_the_reason(store):
    tools = _by_name(make_action_tools(store, "c-001", TranscriptChannel()))
    tools["escalate_to_caseworker"](question="Income moved 30% — which figure applies?")
    entries = [e for e in store.ledger("c-001") if e.kind == "escalated"]
    assert len(entries) == 1
    assert "30%" in entries[0].detail["question"]


def test_escalate_to_caseworker_has_no_precondition(store):
    """Escalating must work on a case in any state whatsoever — including one
    whose rule pack cannot be loaded, which is precisely when a human is most
    needed. Any precondition here would be a way to trap a case with no exit."""
    broken = Case(
        case_id="c-broken",
        household=Household(
            household_id="h-broken",
            display_name="The Broken Household",
            language="en",
            phone="+15550000095",
            monthly_income_cents=100_000,
            size=1,
        ),
        program="no_such_program",
        state="ZZ",
        cert_end=date(2026, 10, 15),
    )
    broken_store = InMemoryCaseStore([broken])
    tools = _by_name(make_action_tools(broken_store, "c-broken", TranscriptChannel()))
    tools["escalate_to_caseworker"](question="No rule pack — what applies here?")
    assert [e.kind for e in broken_store.ledger("c-broken")] == ["escalated"]


# --------------------------------------------------------------------------
# Model registry — CLAUDE.md hard rules 1 and 2
# --------------------------------------------------------------------------


def test_every_role_maps_to_an_amazon_nova_model():
    """Hard rule 1: Amazon Nova only, no third-party LLM in the request path."""
    for role, model_id in _ROLES.items():
        assert model_id.split(".")[1] == "amazon", (role, model_id)
        assert "nova" in model_id, (role, model_id)


def test_the_three_adversarial_roles_run_three_different_models():
    """Hard rule 2. Two instances of the same model agreeing proves nothing,
    and nothing may referee its own argument."""
    ids = [_ROLES[r] for r in ADVERSARIAL_ROLES]
    assert len(set(ids)) == len(ids), ids


def test_no_role_uses_the_model_that_ignored_a_gate_instruction():
    """nova-lite-v1:0 filed a renewal it had been explicitly told not to file.
    A comment recording that is not enforcement — this is."""
    assert BANNED_MODEL_IDS.isdisjoint(_ROLES.values())


def test_unknown_role_raises_rather_than_defaulting():
    """A typo must not silently route a verifier to a cheap model: it would
    still return confident-looking output, so the failure would be invisible."""
    with pytest.raises(KeyError):
        nova("verfier")


def _every_grace_module():
    """Every importable module under `grace/` that has retrievable source,
    discovered from disk.

    A hardcoded tuple of modules only protects the modules someone remembered
    to list — the moment a new one is added (Task 5's `steering.py`, Task 6's
    `run.py`, Task 7's swarm modules) it is covered by nothing until someone
    thinks to add it back in. Walking the package means new modules are
    covered automatically, which matters here because Task 5 lands next.

    Package `__init__.py` files created empty in Task 1 have no retrievable
    source at all — `inspect.getsource` raises `OSError` on a truly empty
    file, not because anything is hidden in them. Skipped rather than
    silently swallowing every `OSError`, so a module that fails to import for
    a real reason still surfaces as a real test failure.
    """
    modules = []
    for info in pkgutil.walk_packages(grace.__path__, prefix="grace."):
        module = importlib.import_module(info.name)
        try:
            inspect.getsource(module)
        except OSError:
            continue
        modules.append(module)
    return modules


def test_no_model_id_is_inlined_outside_the_registry():
    """Hard rule 1 again: IDs live in `grace/models.py` and are referenced by
    role. An inlined ID elsewhere would survive a change to this file."""
    for module in _every_grace_module():
        if module is models:
            continue
        assert "amazon.nova" not in inspect.getsource(module), module.__name__


def _string_literals(module) -> list[str]:
    """Every string literal in a module's code.

    Parsed rather than grepped, for the reason `test_authority.py` documents:
    a raw substring search over the source also matches comments, and this
    module's comments legitimately mention "CLAUDE.md" — which contains
    "claude". Grepping would fail on a comment and push the next person to
    weaken the check instead of the code.
    """
    return [
        node.value
        for node in ast.walk(ast.parse(inspect.getsource(module)))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_no_module_declares_a_non_nova_provider():
    """Hard rule 1: no third-party LLM in the request path.

    Checking `grace/models.py` alone misses the actual mistake this rule
    exists to prevent, which is a third-party model ID or import landing in
    ANY module — the previous version of this test checked only `models`,
    so a Claude/GPT/Gemini ID inlined directly into a tool module (bypassing
    the registry entirely, which `test_no_model_id_is_inlined_outside_the_
    registry` above does not catch either, since that test only looks for
    the string "amazon.nova") passed the full suite. Confirmed by planting
    `"us.anthropic.claude-sonnet-4-20250514-v1:0"` in `grace/tools/read.py`
    and watching all 157 tests pass.

    Checks string literals, so a `BANNED_*` constant or an explanatory
    docstring can still name a vendor for documentation purposes.
    """
    for module in _every_grace_module():
        for literal in _string_literals(module):
            if literal in BANNED_MODEL_IDS:
                continue
            for vendor in (
                "anthropic",
                "claude-",
                "gpt-",
                "gemini",
                "llama",
                "mistral",
                "cohere",
            ):
                assert vendor not in literal.lower(), (module.__name__, vendor, literal)


# ---------------------------------------------------------------------------
# list_documents states the freshness verdict rather than the arithmetic.
#
# Task 6: an earlier version reported `received` plus `max_age_days` and left
# the subtraction to the model. On a real sweep it got that wrong on two of the
# nine clean fixture cases, told those families a current document had expired,
# and texted them about it — a false alarm sent to a family whose paperwork was
# in order. Deadline math is a tool, not an agent; handing a model two dates and
# asking for a comparison is an agent.
# ---------------------------------------------------------------------------


def test_list_documents_says_current_for_a_clean_case(store):
    out = _by_name(make_read_tools(store, "c-001", TODAY))["list_documents"]()
    assert out.count("CURRENT") == 2
    assert "STALE" not in out
    assert "EXPIRED" not in out


def test_list_documents_does_not_make_the_model_do_the_arithmetic(store):
    """The raw allowance must not appear as a number to subtract with. It may
    appear inside the STALE explanation, where the verdict is already stated."""
    out = _by_name(make_read_tools(store, "c-001", TODAY))["list_documents"]()
    assert "max age" not in out.lower()


def test_list_documents_says_stale_for_a_document_past_its_max_age():
    case = Case(
        case_id="c-stale",
        household=Household(
            household_id="h-stale",
            display_name="The Stale Household",
            language="en",
            phone="+15550000095",
            monthly_income_cents=180_000,
            size=2,
        ),
        program="medicaid",
        state="NY",
        cert_end=date(2026, 10, 15),
        documents=(
            Document(doc_id="proof_of_income", received=date(2026, 1, 1)),
            Document(doc_id="proof_of_residency", received=date(2026, 3, 1)),
        ),
    )
    out = _by_name(make_read_tools(InMemoryCaseStore([case]), "c-stale", TODAY))[
        "list_documents"
    ]()
    assert "STALE" in out
    assert "proof_of_income" in out


def test_list_documents_says_expired_for_a_document_past_its_expiry():
    case = Case(
        case_id="c-exp",
        household=Household(
            household_id="h-exp",
            display_name="The Expired Household",
            language="en",
            phone="+15550000094",
            monthly_income_cents=180_000,
            size=2,
        ),
        program="medicaid",
        state="NY",
        cert_end=date(2026, 10, 15),
        documents=(
            Document(
                doc_id="proof_of_income",
                received=date(2026, 9, 20),
                expires=date(2026, 9, 30),
            ),
            Document(doc_id="proof_of_residency", received=date(2026, 3, 1)),
        ),
    )
    out = _by_name(make_read_tools(InMemoryCaseStore([case]), "c-exp", TODAY))[
        "list_documents"
    ]()
    assert "EXPIRED" in out


def test_list_documents_and_the_gate_never_disagree_on_any_fixture():
    """The property that matters: the tool a model reads and the gate that
    permits the action must describe the same reality.

    If `list_documents` said CURRENT where `evaluate` says `stale_document`, the
    model would reason from facts the gate does not share and the disagreement
    would be invisible — no error, tool says current, gate says stale. Both call
    `document_problems`, so this asserts the wiring holds for all twelve
    households rather than for the cases someone thought to enumerate.
    """
    from grace.authority import evaluate
    from grace.cases.store import load_fixture_cases

    for case in load_fixture_cases():
        store_one = InMemoryCaseStore([case])
        out = _by_name(make_read_tools(store_one, case.case_id, TODAY))["list_documents"]()
        gate_says_stale = any(
            r.code == "stale_document"
            for r in evaluate(case, TODAY, load_pack(case.program, case.state)).reasons
        )
        tool_says_stale = "STALE" in out or "EXPIRED" in out
        assert gate_says_stale == tool_says_stale, (case.case_id, out)
