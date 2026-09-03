"""Create the AgentCore Memory resource and its namespace strategies.

The `namespaceTemplates` here MUST match `grace.memory.RETRIEVAL_NAMESPACES`.
A mismatch retrieves nothing, silently — there is no error to notice. So they
are not two lists that have to agree: `memory_strategies()` *derives* the
request from `grace.memory.RETRIEVAL_NAMESPACES`, and refuses a namespace it
has no strategy for. Shared for the reason `list_documents` imports
`_most_recent` from the gate rather than reimplementing it: a duplicated
implementation drifts, a derived one cannot.

Idempotent, and raising is the correct failure mode here — this is a
provisioning script, not the request path, so a loud failure blocks a deploy and
the operator re-runs. That is the same reasoning `provision_dynamodb` records,
and the opposite of `grace/memory.py`'s, which degrades to "no recall" because
it runs while a family's case is being decided.
"""

from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

from grace.memory import RETRIEVAL_NAMESPACES
from infra import naming

# 365 days, the API maximum (`min 1, max 365`, read off the live model), because
# the gap this resource exists to bridge is a full recert cycle. A recert is
# annual: "income verified via pay stubs last cycle" is worth remembering
# precisely because eleven months pass before anyone looks at the case again, so
# the plan's draft 90 days would expire the events months before the contact
# they are meant to inform. 365 does not fully cover a 12-month gap either —
# nothing available does — but it is the longest the service allows.
EVENT_EXPIRY_DAYS = 365

# Which extraction strategy serves each namespace. Facts about what was verified
# are semantic; language and contact-time preferences are what the user-preference
# strategy is for. Keyed by namespace so a namespace added to
# `grace/memory.py` without a strategy here fails loudly at provision time
# rather than producing a memory that silently cannot serve it.
#
# The names carry underscores, not hyphens. A strategy `name` is
# `[a-zA-Z][a-zA-Z0-9_]{0,47}` — the same pattern as the memory's own name, and
# it admits no `-`. Every other Grace resource is `grace-something`, so a
# hyphen here is the natural thing to write and the service refuses it:
# confirmed live, `household-facts` returned
# `ValidationException: ... failed to satisfy constraint`. Pinned by
# `test_every_strategy_name_is_one_the_service_accepts`.
_STRATEGY_KINDS = {
    "/facts/{actorId}": ("semanticMemoryStrategy", "household_facts"),
    "/preferences/{actorId}": ("userPreferenceMemoryStrategy", "household_preferences"),
}


def memory_strategies() -> list[dict]:
    """The `memoryStrategies` request body, derived from the declared namespaces.

    Uses `namespaceTemplates`, NOT `namespaces`. Verified against the live API
    model: `namespaces` is documented as "a legacy parameter, use
    namespaceTemplates". Both fields exist and both accept the same list, which
    is exactly what makes the legacy one dangerous — writing it *succeeds*, and
    retrieval against the template form then returns nothing, with no error
    anywhere.
    """
    missing = sorted(set(RETRIEVAL_NAMESPACES) - set(_STRATEGY_KINDS))
    if missing:
        raise ValueError(
            f"no extraction strategy is declared for namespace(s) {missing}. "
            "grace.memory retrieves against them, so the memory would serve "
            "them empty rather than erroring — add the strategy here."
        )
    return [
        {kind: {"name": name, "namespaceTemplates": [namespace]}}
        for namespace in RETRIEVAL_NAMESPACES
        for kind, name in [_STRATEGY_KINDS[namespace]]
    ]


def _find_existing(client) -> str | None:
    """Grace's memory id, or `None`.

    **Paginates.** `ListMemories` takes `maxResults`/`nextToken` and returns
    `nextToken`, so reading one page would make an existing Grace memory on page
    two invisible — and this function's answer decides whether to *create* one,
    so a missed page means a duplicate memory whose events the first one never
    sees. Same class of bug as the single-page `ledger()` read.

    **Matches the full id shape, not a prefix.** A memory id is
    `<name>-<10 random chars>`, so an anchored comparison is required:
    `startswith(naming.MEMORY)` also matches `grace_household_memory_v2-...`, and
    handing back an unrelated resource's id would point Grace's whole recall path
    at it. Not hypothetical — this account already holds
    `rosettacloud_education_memory-...` alongside
    `rosettacloud_education_memory_v2-...`, which is precisely that collision.
    """
    token: str | None = None
    while True:
        kwargs = {"maxResults": 100}
        if token:
            kwargs["nextToken"] = token
        response = client.list_memories(**kwargs)
        for memory in response.get("memories", []):
            candidate = str(memory.get("id", ""))
            # `<name>-<suffix>`: split once from the right and compare the name
            # exactly, so `_v2` is a different memory rather than a prefix match.
            name, _, suffix = candidate.rpartition("-")
            if name == naming.MEMORY and suffix:
                return candidate
        token = response.get("nextToken")
        if not token:
            return None


def _verify_namespaces(client, memory_id: str) -> None:
    """Read the namespaces back and refuse a mismatch.

    The read-back is the arbiter, not the fact that `create_memory` returned —
    `provision_dynamodb`'s point-in-time-recovery lesson applied to a control
    whose absence is quieter still: a namespace mismatch produces no error at
    retrieval time, it just never recalls anything.

    Reads **both** spellings and unions them. Which one a strategy echoes back is
    a property of the API rather than of what was sent, so reading only
    `namespaceTemplates` could fail against a service answering in the legacy
    field, and reading only `namespaces` could compare two empty sets and pass
    having checked nothing. The non-empty assertion is what closes that second
    hole.
    """
    strategies = client.get_memory(memoryId=memory_id)["memory"].get("strategies", [])
    created = {
        namespace
        for strategy in strategies
        for key in ("namespaceTemplates", "namespaces")
        for namespace in (strategy.get(key) or [])
    }
    declared = set(RETRIEVAL_NAMESPACES)
    if not created:
        raise RuntimeError(
            f"{memory_id} reported no namespaces at all, in either spelling. "
            "Comparing against an empty set would pass vacuously, so this is a "
            "failure: grace.memory would retrieve nothing, silently."
        )
    if created != declared:
        raise RuntimeError(
            f"namespace mismatch on {memory_id}: created={sorted(created)} "
            f"declared={sorted(declared)}. Retrieval against a namespace that "
            "does not exist returns nothing rather than erroring, so this must "
            "fail here. Fix infra/provision_memory.py and grace/memory.py "
            "together — never one alone."
        )


def provision(client=None) -> str:
    """Create the memory if absent; return its id. Idempotent.

    Waits for `ACTIVE` on both paths. `MemoryStatus` is
    `CREATING|ACTIVE|FAILED|DELETING|UPDATING`, so `create_memory` returning is
    not the same claim as the memory being usable — and an earlier interrupted
    run can leave one `CREATING` or `FAILED`, which must not be handed back as
    though it worked. The `memory_created` waiter treats `CREATING` as retry and
    `FAILED` as failure, which is exactly the distinction needed, so it is used
    rather than a hand-rolled poll.
    """
    client = client or boto3.client("bedrock-agentcore-control", region_name=naming.REGION)

    memory_id = _find_existing(client)
    if memory_id is None:
        try:
            response = client.create_memory(
                name=naming.MEMORY,
                description="Per-household facts and preferences across annual recert cycles",
                eventExpiryDuration=EVENT_EXPIRY_DAYS,
                memoryStrategies=memory_strategies(),
                tags=naming.TAGS,
            )
        except ClientError as exc:
            # `ConflictException` only. The plan's draft also caught
            # `ValidationException`, which is a different thing entirely: it
            # means the *request* was malformed — a bad strategy, an
            # out-of-range expiry. If a Grace memory happens to exist already,
            # treating that as "already exists" finds the old id, returns it,
            # and reports success while silently dropping whatever the invalid
            # strategy was meant to add. The operator would see exit 0 and a
            # memory that cannot serve a namespace, which is the
            # control-looks-present-and-is-absent failure this codebase keeps
            # finding. A malformed request must be fixed, not absorbed.
            if exc.response["Error"]["Code"] != "ConflictException":
                raise
            # Another run created it between the list and the create.
            memory_id = _find_existing(client)
            if memory_id is None:
                raise
        else:
            memory_id = str(response["memory"]["id"])

    client.get_waiter("memory_created").wait(memoryId=memory_id)
    _verify_namespaces(client, memory_id)
    return memory_id


if __name__ == "__main__":
    print(f"GRACE_MEMORY_ID={provision()}")
