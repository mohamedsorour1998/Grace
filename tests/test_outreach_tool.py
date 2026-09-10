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

    Two details in how the match is made, both of which the obvious version got
    wrong and neither of which is cosmetic:

    1. **`grace.models` is not a call site.** Its own module docstring carries
       `nova("verifier")` as the example of how to reference a role — so a walk
       that includes it reports `verifier` as referenced even if nothing calls
       it. The table alibiing itself is precisely the vacuity this test exists
       to prevent: it would then be true of a role nobody uses, which is the
       overclaim, and no report would look any different. Verified by deleting
       `swarm.py`'s verifier line with `grace.models` included — the test still
       passed.
    2. **Match `nova("role"`, not `nova("role")`.** `swarm.py` passes a
       temperature: `nova("advocate", temperature=0.4)`. Anchoring on the
       closing paren misses every call that takes a keyword argument, which is
       three of the seven roles — so the guard would fail on correct code and
       the reflex fix would be to weaken it.
    """
    import pkgutil

    import grace
    from grace.models import _ROLES

    referenced: set[str] = set()
    for module in pkgutil.walk_packages(grace.__path__, prefix="grace."):
        if module.name == "grace.models":
            continue
        try:
            source = inspect.getsource(__import__(module.name, fromlist=["_"]))
        except Exception:  # noqa: BLE001 — a module that will not import is not a reference
            continue
        for role in _ROLES:
            if f'nova("{role}"' in source:
                referenced.add(role)

    unreferenced = set(_ROLES) - referenced
    assert unreferenced <= {"judge"}, (
        f"these roles are defined and never called: {sorted(unreferenced)}. "
        "A role nothing uses is an overclaim in models.py's own table."
    )
