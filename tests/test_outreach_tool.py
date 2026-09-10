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
    # `nova("outreach"`, not `nova("outreach")` — the same correction the role
    # guard below needed, for the same reason. `swarm.py` already calls
    # `nova("advocate", temperature=0.4)`, so a closing-paren anchor declares
    # that a call site is only real when it passes no keyword arguments. Adding
    # a temperature to the drafter is an ordinary tuning change and must not
    # fail a correct file. The two anchors now agree about what a call site
    # looks like; they disagreed until this edit, which is how one guard can be
    # fixed and its sibling left brittle.
    assert 'nova("outreach"' in source, "the outreach role exists for this tool"
    assert _ROLES["outreach"] not in BANNED_MODEL_IDS


def test_the_drafter_agent_has_no_tools_at_all():
    """It writes; it does not act. An agent with tools inside a tool is a
    second loop nobody is watching."""
    source = inspect.getsource(make_outreach_tool)
    assert "tools=[]" in source, "the drafter agent must be constructed with tools=[]"
    assert "callback_handler=None" in source


def test_the_drafter_is_built_with_no_capability_and_its_result_is_coerced(monkeypatch):
    """What the three source greps above can only approximate, asserted against
    the constructed object instead.

    `Agent` is imported into `grace.tools.outreach`'s module namespace, so
    patching it there costs nothing and no Bedrock call is made. That matters
    beyond convenience: a grep for `"tools=[]"` is satisfied by the literal
    appearing anywhere in the function, so a module-level alias
    (`_A = Agent; ... _A(tools=[read_case])`) walks around it while leaving the
    string in a comment. An assertion about the kwargs the constructor actually
    received cannot be walked around that way. Keep the greps as well — they
    cover what this cannot, notably that `store` never appears in the source at
    all, which is a property of the text rather than of one call.

    The fake returns a non-string on purpose. `Agent.__call__` yields an
    `AgentResult`, not a `str`, and `send_family_message` puts whatever comes
    back into a family's message and then into a ledger row whose `detail`
    contract accepts JSON scalars only. Dropping the `str(...)` is the same
    shape of defect as a `Channel.send()` return reaching the ledger unwrapped:
    it fails after the work is done, which is the worst place for it. So the
    coercion is proven here, not assumed.
    """
    from strands.models.bedrock import BedrockModel

    from grace.models import BANNED_MODEL_IDS, _ROLES

    captured: dict = {}
    tasks: list[str] = []

    class _RecordingAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __call__(self, task):
            tasks.append(task)
            # Deliberately not a `str`, mirroring the real `AgentResult`.
            return self

        def __str__(self):
            return "Please send your proof of residency by 18 October."

    monkeypatch.setattr("grace.tools.outreach.Agent", _RecordingAgent)

    out = _tool()._tool_func("proof_of_residency", "2026-10-18", "es")

    # Capability absence, read off the constructor call rather than the source.
    assert captured["tools"] == [], captured["tools"]
    assert captured["callback_handler"] is None

    # Hard rules 1 and 2: a Nova model, the role the drafter is supposed to
    # use, and never the one that filed a renewal it had been told not to file.
    model = captured["model"]
    assert isinstance(model, BedrockModel), type(model)
    assert model.get_config()["model_id"] == _ROLES["outreach"]
    assert model.get_config()["model_id"] not in BANNED_MODEL_IDS

    # The coercion, and it is the return value that is checked — not the fake's
    # `__str__`, which would pass even if the tool returned the object.
    assert isinstance(out, str), type(out)
    assert out == "Please send your proof of residency by 18 October."

    # All three arguments reach the drafter. A tool that quietly dropped the
    # language would draft in English for every family and nothing would say so.
    assert len(tasks) == 1, tasks
    for value in ("proof_of_residency", "2026-10-18", "es"):
        assert value in tasks[0], (value, tasks[0])


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
