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
    factories in this package and can be splatted into a `tools=[...]` list
    without a special case.

    This docstring deliberately names no sibling factory and no action tool.
    The guard in `tests/test_outreach_tool.py` greps this whole function's
    source — comments and docstring included — for the vocabulary of reaching a
    household, so even a harmless mention in prose trips it. That bluntness is
    the point: a substring check over the entire source is what catches a
    capability reintroduced under cover of an explanatory comment, and the
    price of that is paid in wording, which is the cheap thing to move.
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
