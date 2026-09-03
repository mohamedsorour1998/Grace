"""Memory wiring. Mostly about where it must NOT go.

`build_session_manager` returns `None` when no memory is configured, which is the
normal case for the fast suite and for a local sweep — Memory is an AWS resource
and these tests are offline. Every test here runs without network: the one that
exercises the configured path substitutes the session-manager class, and the
provisioning tests drive a fake control-plane client.

**Why the provisioning tests are here rather than in a separate module.** The
namespace list and the strategies that create it are the same fact expressed
twice, and a mismatch retrieves nothing *silently* — no error, no empty-result
signal, just a memory that never recalls anything. The tests that pin the two
together belong next to each other.
"""

from __future__ import annotations

import re

import pytest
from botocore.exceptions import ClientError

from grace import memory
from infra import naming, provision_memory

# A session id inside the API's verified constraints: min 1, max 100,
# `[a-zA-Z0-9][a-zA-Z0-9-_]*`. Note that pattern admits neither `.` nor `:`, so
# a session id built from an ISO timestamp is invalid — see
# `test_a_session_id_the_service_would_refuse_never_reaches_the_service`.
VALID_SESSION = "session-" + "x" * 30


# ---------------------------------------------------------------------------
# Identity and the offline path
# ---------------------------------------------------------------------------


def test_the_actor_is_the_household_case(monkeypatch):
    """`actor_id` scopes long-term facts. One actor per case means one
    family's history can never be retrieved into another's run."""
    assert memory.actor_id("c-011") == "c-011"


def test_no_memory_configured_returns_none(monkeypatch):
    """The offline path. A local sweep must not require an AWS memory
    resource, and a missing one must not raise — it must simply mean "no
    long-term recall this run"."""
    monkeypatch.delenv("GRACE_MEMORY_ID", raising=False)
    assert memory.build_session_manager("c-001", VALID_SESSION) is None


def test_a_blank_memory_id_is_treated_as_absent(monkeypatch):
    """`GRACE_MEMORY_ID=` — set but empty — must mean "not configured".

    `os.getenv(name, default)` only defaults on *absence*, and Plan 2's store
    factory already shipped this bug once: a blank `GRACE_STORE` bypassed the
    in-memory default. Here the consequence is milder (a memory id of `""`
    reaches the config and fails its `min_length=1` validator) but the failure
    would arrive as a swallowed exception rather than as the honest "no memory
    configured" this branch exists to express.
    """
    monkeypatch.setenv("GRACE_MEMORY_ID", "")
    assert memory.build_session_manager("c-001", VALID_SESSION) is None
    monkeypatch.setenv("GRACE_MEMORY_ID", "   ")
    assert memory.build_session_manager("c-001", VALID_SESSION) is None


def test_a_construction_failure_degrades_to_no_recall(monkeypatch):
    """Memory is an enhancement to outreach quality, never an input to a
    verdict — the authority gate reads the case record and never memory. So a
    memory resource that is missing, unreachable, or misconfigured must cost
    recall and nothing else.

    `AgentCoreMemorySessionManager.__init__` calls `read_session` against the
    live service (verified: `RepositorySessionManager.__init__` reads the
    session and creates it if absent), so this is the ordinary failure on a
    machine with no credentials — not a hypothetical.
    """
    def explode(*args, **kwargs):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(memory, "AgentCoreMemorySessionManager", explode)
    assert memory.build_session_manager(
        "c-001", VALID_SESSION, memory_id="grace_household_memory-abc1234567"
    ) is None


def test_the_configured_path_passes_the_declared_namespaces_through(monkeypatch):
    """The happy path, offline. What matters is that the retrieval config
    reaching the manager is the *declared* one — a manager built without it
    would retrieve nothing while looking entirely healthy."""
    captured = {}

    class FakeManager:
        def __init__(self, config, region_name=None):
            captured["config"] = config
            captured["region_name"] = region_name

    monkeypatch.setattr(memory, "AgentCoreMemorySessionManager", FakeManager)
    manager = memory.build_session_manager(
        "c-011", VALID_SESSION, memory_id="grace_household_memory-abc1234567"
    )

    assert isinstance(manager, FakeManager)
    config = captured["config"]
    assert config.memory_id == "grace_household_memory-abc1234567"
    assert config.session_id == VALID_SESSION
    assert config.actor_id == "c-011"
    assert set(config.retrieval_config) == set(memory.RETRIEVAL_NAMESPACES)
    assert captured["region_name"] == naming.REGION


def test_persistence_mode_is_chosen_explicitly_not_defaulted(monkeypatch):
    """`PersistenceMode` is `FULL`/`NONE` only — there is no per-turn
    selectivity, so the spec's "writes to memory selectively" is not
    expressible as a flag and the hazard is handled by scope instead. Passing
    the mode explicitly records that a decision was made rather than
    inherited: `FULL` is required for anything to be remembered at all, and
    `NONE` would leave retrieval working while writing nothing, which looks
    identical in every test that only reads.
    """
    from bedrock_agentcore.memory.integrations.strands.config import PersistenceMode

    # Pinned against the installed package rather than trusted from the plan.
    assert [mode.name for mode in PersistenceMode] == ["FULL", "NONE"]

    captured = {}

    class FakeManager:
        def __init__(self, config, region_name=None):
            captured["config"] = config

    monkeypatch.setattr(memory, "AgentCoreMemorySessionManager", FakeManager)
    memory.build_session_manager(
        "c-001", VALID_SESSION, memory_id="grace_household_memory-abc1234567"
    )
    assert captured["config"].persistence_mode is PersistenceMode.FULL


# ---------------------------------------------------------------------------
# The identifiers the service would refuse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "session_id",
    [
        "",                                # min 1
        "2026-10-01T00:00:00+00:00",       # `:` and `+` are not in the pattern
        "c-001.2026-10-01",                # `.` is not in the pattern
        "-leading-dash",                   # must start alphanumeric
        "x" * 101,                         # max 100
    ],
)
def test_a_session_id_the_service_would_refuse_never_reaches_the_service(
    monkeypatch, session_id
):
    """`CreateEvent`'s `sessionId` is `min 1, max 100, [a-zA-Z0-9][a-zA-Z0-9-_]*`
    (read off the live API model). The pattern admits neither `.` nor `:`, so
    the obvious "case id plus a timestamp" session id is invalid — and the
    failure would otherwise surface as a swallowed exception from deep inside
    the manager's constructor, indistinguishable from "no memory configured".
    Refusing locally makes the reason loggable.
    """
    constructed: list[str] = []
    monkeypatch.setattr(
        memory, "AgentCoreMemorySessionManager",
        lambda *a, **k: constructed.append("built"),
    )
    assert memory.build_session_manager(
        "c-001", session_id, memory_id="grace_household_memory-abc1234567"
    ) is None
    assert not constructed, "a session id the service refuses was sent anyway"


def test_an_actor_id_that_would_escape_its_namespace_is_refused(monkeypatch):
    """The namespace is `/facts/{actorId}`, resolved by `str.format` inside the
    session manager (verified in its source). An actor id containing `/`
    therefore does not merely look odd — it *changes the namespace path*,
    nesting one household's records under a path another household's retrieval
    may span. `case_id` reaches here from a payload in the deployed runtime, so
    it is untrusted input, exactly as rule-pack `program`/`state` are.

    Fail closed here: refusing costs recall, which cannot change a verdict.
    """
    constructed: list[str] = []
    monkeypatch.setattr(
        memory, "AgentCoreMemorySessionManager",
        lambda *a, **k: constructed.append("built"),
    )
    for case_id in ("c-001/../c-002", "c-001/x", "", "/c-001"):
        assert memory.build_session_manager(
            case_id, VALID_SESSION, memory_id="grace_household_memory-abc1234567"
        ) is None, case_id
    assert not constructed


# ---------------------------------------------------------------------------
# The namespaces, and the two places they have to agree
# ---------------------------------------------------------------------------


def test_the_namespaces_match_what_provisioning_creates():
    """The namespace must match the `namespaceTemplates` set at memory
    creation. The AWS blog's `/users/{actorId}/facts` form does not match
    working code — `/facts/{actorId}` does — and a mismatch silently
    retrieves nothing rather than erroring."""
    assert set(memory.RETRIEVAL_NAMESPACES) == {
        "/facts/{actorId}",
        "/preferences/{actorId}",
    }


def test_provisioning_builds_its_strategies_from_the_declared_namespaces():
    """One list, not two.

    The stronger form of the test above: rather than asserting two literals
    match, this asserts the *request* `provision` would send is derived from
    `grace.memory.RETRIEVAL_NAMESPACES`. A duplicated list drifts; a derived
    one cannot — the same reason `list_documents` imports `_most_recent` from
    the gate instead of reimplementing it.
    """
    sent = {
        namespace
        for strategy in provision_memory.memory_strategies()
        for body in strategy.values()
        for namespace in body["namespaceTemplates"]
    }
    assert sent == set(memory.RETRIEVAL_NAMESPACES)
    assert sent, "no namespaces at all would make every check here vacuous"


def test_a_namespace_with_no_strategy_fails_loudly():
    """Adding a namespace to `grace/memory.py` without teaching provisioning
    which strategy extracts into it would create a memory that cannot serve
    that namespace — retrieving nothing, silently. This is the guard that
    turns that into an error at provision time."""
    original = dict(provision_memory._STRATEGY_KINDS)
    try:
        provision_memory._STRATEGY_KINDS.pop("/preferences/{actorId}")
        with pytest.raises(ValueError, match="namespace"):
            provision_memory.memory_strategies()
    finally:
        provision_memory._STRATEGY_KINDS.clear()
        provision_memory._STRATEGY_KINDS.update(original)


def test_provisioning_never_writes_the_legacy_namespaces_field():
    """`namespaces` is documented on the live `CreateMemory` model as "a legacy
    parameter, use `namespaceTemplates`". Both fields exist and both accept the
    same list, which is what makes this dangerous: writing the legacy one
    *succeeds*, and retrieval against the template form then returns nothing
    with no error anywhere.
    """
    for strategy in provision_memory.memory_strategies():
        for kind, body in strategy.items():
            assert "namespaceTemplates" in body, kind
            assert "namespaces" not in body, kind


def test_every_retrieval_config_has_a_relevance_floor():
    """A floor of 0 retrieves noise into an eligibility decision. Reflection
    lessons and remembered facts may only make Grace more cautious (hard rule
    5), so what gets recalled must at least be relevant."""
    for namespace, config in memory.RETRIEVAL_NAMESPACES.items():
        assert config.relevance_score > 0, namespace
        assert config.top_k > 0, namespace


def test_events_are_kept_for_a_full_recert_cycle():
    """A recert cycle is annual and Memory exists precisely to bridge the
    eleven months between contacts, so an expiry shorter than a year defeats
    the reason for the resource. 365 is the API maximum
    (`min 1, max 365`, read off the live model)."""
    assert provision_memory.EVENT_EXPIRY_DAYS == 365


# ---------------------------------------------------------------------------
# Hard rule 2: where Memory may not attach
# ---------------------------------------------------------------------------


def assert_no_session_manager(node, path: str) -> None:
    """Recursive assertion that `node` and everything inside it is manager-free.

    Both attribute spellings are checked because the two classes differ: an
    `Agent` stores its manager privately as `_session_manager`, while a `Swarm`
    (and a `Graph`) stores it publicly as `session_manager` and has no private
    one at all. Task 7 found the Task 6 version of this assertion passing
    **vacuously** on the swarm node for exactly that reason — `getattr(swarm,
    "_session_manager", None)` returns the default, so the check succeeded
    without checking anything, on the one node that contains three more agents.

    Exposed at module scope so
    `test_the_hard_rule_2_assertion_can_actually_fail` can drive it against
    nodes that *do* carry a manager. An assertion helper nobody has watched
    fail is indistinguishable from `pass`.
    """
    executor = getattr(node, "executor", node)
    for attribute in ("_session_manager", "session_manager"):
        assert getattr(executor, attribute, None) is None, f"{path}.{attribute}"
    nested = getattr(executor, "nodes", None)
    if isinstance(nested, dict):
        for name, child in nested.items():
            assert_no_session_manager(child, f"{path}.{name}")


def test_memory_is_never_attached_to_a_node_inside_the_graph():
    """Hard rule 2, asserted structurally rather than trusted.

    Agents inside a Graph or Swarm must not carry their own `session_manager` —
    Python raises `ValueError`. Memory therefore attaches to the orchestrator
    only, which is why `build_session_manager` is a factory the caller attaches
    rather than something `build_case_graph` wires up.

    Building the graph is free; only invoking it costs a Bedrock call.
    """
    from datetime import date

    from grace.cases.store import InMemoryCaseStore, load_fixture_cases
    from grace.graph import build_case_graph
    from grace.tools.action import TranscriptChannel

    graph = build_case_graph(
        InMemoryCaseStore(load_fixture_cases()), "c-011",
        date(2026, 10, 1), TranscriptChannel(),
    )

    assert graph.nodes, "the graph has no nodes, so this test proves nothing"
    # The swarm node is the one that matters and the one a shallow check
    # misses, so assert it is actually present rather than hoping.
    assert any(
        isinstance(getattr(node, "executor", node).nodes, dict)
        for node in graph.nodes.values()
        if hasattr(getattr(node, "executor", node), "nodes")
    ), "no nested multi-agent node found — the recursion below checks nothing"

    for name, node in graph.nodes.items():
        assert_no_session_manager(node, name)


def test_the_hard_rule_2_assertion_can_actually_fail():
    """Proof the assertion above is not vacuous, for all three shapes.

    Case 3 is the one that matters here and the one the SDK does **not** catch:
    `Graph`'s validator only inspects `isinstance(executor, Agent)`, and
    `Swarm._validate_swarm` only inspects each member's `_session_manager` —
    so a `Swarm` carrying its *own* `session_manager`, added as a graph node,
    is accepted with no error (confirmed directly against 1.54.0). Grace's
    topology is a Swarm inside a Graph, so the one arrangement the SDK does
    not police is the one Grace actually builds. Nothing in `grace/graph.py`
    attaches a manager, and this test is what keeps that true.
    """
    class Manager:
        pass

    class FakeAgent:
        def __init__(self):
            self._session_manager = Manager()

    class FakeSwarm:
        def __init__(self, nodes=None):
            self.session_manager = None
            self.nodes = nodes or {}

    # 1. An Agent-shaped node with a private manager.
    with pytest.raises(AssertionError, match=r"solo\._session_manager"):
        assert_no_session_manager(FakeAgent(), "solo")

    # 2. A Swarm-shaped node with a public manager — the spelling the vacuous
    #    version of this check could not see.
    swarm = FakeSwarm()
    swarm.session_manager = Manager()
    with pytest.raises(AssertionError, match=r"deliberate\.session_manager"):
        assert_no_session_manager(swarm, "deliberate")

    # 3. A clean Swarm whose *member* carries one. Reached only by recursing.
    class Node:
        def __init__(self, executor):
            self.executor = executor

    nested = FakeSwarm({"advocate": Node(FakeAgent())})
    with pytest.raises(AssertionError, match=r"deliberate\.advocate\._session_manager"):
        assert_no_session_manager(nested, "deliberate")


def test_the_sdk_does_not_itself_refuse_a_swarm_carrying_a_session_manager():
    """Pins the gap the test above defends against, so it is a known property
    rather than a surprise.

    Verified against `strands` 1.54.0: `_validate_node_executor` in
    `strands/multiagent/graph.py` guards only `isinstance(executor, Agent)`,
    and `Swarm._validate_swarm` guards only each member's `_session_manager`.
    Neither covers a `Swarm` that holds its own. If a future SDK version
    closes this, this test fails and can be deleted — which is the point of
    pinning it.
    """
    import inspect

    from strands.multiagent import graph as graph_module
    from strands.multiagent import swarm as swarm_module

    node_validator = inspect.getsource(graph_module._validate_node_executor)
    assert "isinstance(executor, Agent)" in node_validator
    assert "_session_manager" in node_validator
    # The public spelling — a Swarm's own manager — is not checked.
    assert "executor.session_manager" not in node_validator

    swarm_validator = inspect.getsource(swarm_module.Swarm._validate_swarm)
    assert "node._session_manager" in swarm_validator


# ---------------------------------------------------------------------------
# Provisioning, against a fake control plane
# ---------------------------------------------------------------------------


GRACE_ID = f"{naming.MEMORY}-Ab3Cd5Ef7G"


def _client_error(code: str, operation: str = "CreateMemory") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class FakeMemoryControl:
    """A control plane that can fail the ways the real one fails.

    Pages its `list_memories` at one memory per page, because a pagination loop
    that never iterates in any test is not a tested loop — and returns whatever
    namespace spelling it is told to, because which one the service echoes back
    is a property of the API rather than of what was sent.
    """

    def __init__(
        self,
        existing: list[dict] | None = None,
        create_error: ClientError | None = None,
        created_after_conflict: list[dict] | None = None,
        echo_key: str = "namespaceTemplates",
        echo_namespaces: list[str] | None = None,
        status: str = "ACTIVE",
    ):
        self.existing = list(existing or [])
        self.create_error = create_error
        self.created_after_conflict = created_after_conflict
        self.echo_key = echo_key
        self.echo_namespaces = echo_namespaces
        self.status = status
        self.create_calls: list[dict] = []
        self.list_calls: list[dict] = []
        self.waited: list[str] = []

    def list_memories(self, **kwargs):
        self.list_calls.append(kwargs)
        token = kwargs.get("nextToken")
        index = int(token) if token else 0
        page = self.existing[index : index + 1]
        response = {"memories": page}
        if index + 1 < len(self.existing):
            response["nextToken"] = str(index + 1)
        return response

    def create_memory(self, **kwargs):
        self.create_calls.append(kwargs)
        if self.create_error is not None:
            if self.created_after_conflict is not None:
                self.existing = list(self.created_after_conflict)
            raise self.create_error
        self.existing = [*self.existing, {"id": GRACE_ID, "status": "CREATING"}]
        return {"memory": {"id": GRACE_ID, "status": "CREATING"}}

    def get_memory(self, memoryId=None, **kwargs):
        namespaces = self.echo_namespaces
        if namespaces is None:
            namespaces = sorted(memory.RETRIEVAL_NAMESPACES)
        return {
            "memory": {
                "id": memoryId,
                "status": self.status,
                "strategies": [{self.echo_key: [n]} for n in namespaces],
            }
        }

    def get_waiter(self, name):
        assert name == "memory_created", name
        outer = self

        class Waiter:
            def wait(self, memoryId=None, **kwargs):
                outer.waited.append(memoryId)

        return Waiter()


def test_provision_creates_the_memory_and_returns_its_id():
    client = FakeMemoryControl()
    assert provision_memory.provision(client) == GRACE_ID
    assert len(client.create_calls) == 1
    request = client.create_calls[0]
    assert request["name"] == naming.MEMORY
    assert request["eventExpiryDuration"] == 365
    assert request["tags"] == naming.TAGS


def test_provision_waits_for_active_before_returning():
    """The status enum includes `CREATING`, so `create_memory` returning is not
    the same claim as the memory being usable — the same distinction
    `provision_dynamodb` draws between "the API call returned" and "the control
    is on". Reading namespaces off a half-built resource is how the agreement
    check would pass or fail for reasons unrelated to what was sent."""
    client = FakeMemoryControl()
    provision_memory.provision(client)
    assert client.waited == [GRACE_ID]


def test_provision_is_idempotent_and_does_not_create_twice():
    client = FakeMemoryControl(existing=[{"id": GRACE_ID, "status": "ACTIVE"}])
    assert provision_memory.provision(client) == GRACE_ID
    assert client.create_calls == []


def test_provision_waits_for_an_existing_memory_too():
    """An earlier interrupted run can leave a memory `CREATING`, and a `FAILED`
    one must not be handed back as though it worked. The waiter treats
    `CREATING` as retry and `FAILED` as failure, so routing the existing-memory
    path through it too is what makes "found" mean "usable"."""
    client = FakeMemoryControl(existing=[{"id": GRACE_ID, "status": "CREATING"}])
    provision_memory.provision(client)
    assert client.waited == [GRACE_ID]


def test_provision_reads_every_page_before_deciding_to_create():
    """`ListMemories` takes `maxResults`/`nextToken` and returns `nextToken`, so
    it paginates. Reading one page means an existing Grace memory on page two is
    invisible and a *second* memory gets created — a duplicate whose events the
    first one will never see. Same class of bug as the single-page `ledger()`
    read Task 2 fixed."""
    client = FakeMemoryControl(
        existing=[
            {"id": "rosettacloud_education_memory-evO1o3F0jN", "status": "ACTIVE"},
            {"id": "theagentorg_planner_mem-FM9Dgv31gr", "status": "ACTIVE"},
            {"id": GRACE_ID, "status": "ACTIVE"},
        ]
    )
    assert provision_memory.provision(client) == GRACE_ID
    assert client.create_calls == []
    assert len(client.list_calls) == 3, "the pagination loop did not iterate"


def test_another_projects_memory_is_not_mistaken_for_graces():
    """A memory id is `<name>-<10 random chars>`, so the match must be anchored.
    `startswith(name)` also matches `grace_household_memory_v2-...`, and
    returning another resource's id would point Grace's whole recall path at it.
    Real ids from this account are used above; the collision below is the one a
    prefix test would wave through."""
    client = FakeMemoryControl(
        existing=[
            {"id": f"{naming.MEMORY}_v2-vvC3mbAmra", "status": "ACTIVE"},
            {"id": f"{naming.MEMORY}_archive-FM9Dgv31gr", "status": "ACTIVE"},
        ]
    )
    assert provision_memory.provision(client) == GRACE_ID
    assert len(client.create_calls) == 1, "an unrelated memory was reused"


def test_a_conflict_falls_back_to_the_memory_another_run_created():
    """`ConflictException` means the name is taken, which for an idempotent
    script is success — provided the thing that took it is Grace's own."""
    client = FakeMemoryControl(
        create_error=_client_error("ConflictException"),
        created_after_conflict=[{"id": GRACE_ID, "status": "ACTIVE"}],
    )
    assert provision_memory.provision(client) == GRACE_ID


def test_a_validation_error_is_never_treated_as_already_exists():
    """The dangerous half of the plan's draft, which caught
    `{ConflictException, ValidationException}` together.

    `ValidationException` means the *request* was malformed — a bad strategy, an
    out-of-range expiry. If a Grace memory happens to exist already, treating it
    as "already exists" finds that id, returns it, and reports success while
    silently dropping whatever the invalid strategy was meant to add. The
    operator sees an exit code of 0 and a memory that cannot serve a namespace.
    """
    client = FakeMemoryControl(
        create_error=_client_error("ValidationException"),
        created_after_conflict=[{"id": GRACE_ID, "status": "ACTIVE"}],
    )
    with pytest.raises(ClientError) as caught:
        provision_memory.provision(client)
    assert caught.value.response["Error"]["Code"] == "ValidationException"


def test_a_conflict_with_no_grace_memory_afterwards_still_raises():
    """The name is taken by something that is not Grace's. Silently returning
    nothing usable is worse than failing a deploy."""
    client = FakeMemoryControl(create_error=_client_error("ConflictException"))
    with pytest.raises(ClientError):
        provision_memory.provision(client)


def test_provision_verifies_the_namespaces_the_service_actually_stored():
    """The read-back is the arbiter, not the fact that `create_memory`
    returned — `provision_dynamodb`'s point-in-time-recovery lesson applied to
    a control whose absence is even quieter. A namespace mismatch produces no
    error at retrieval time; it just never recalls anything."""
    client = FakeMemoryControl(echo_namespaces=["/users/{actorId}/facts"])
    with pytest.raises(RuntimeError, match="namespace"):
        provision_memory.provision(client)


def test_the_verification_reads_both_namespace_spellings():
    """Which spelling a strategy echoes back is a property of the API, not of
    what was sent, so reading only `namespaceTemplates` would fail against a
    service that answers in the legacy field — and reading only `namespaces`
    would pass vacuously against an empty set. Union both."""
    client = FakeMemoryControl(echo_key="namespaces")
    assert provision_memory.provision(client) == GRACE_ID


def test_the_verification_cannot_pass_on_an_empty_read_back():
    """The vacuity guard. A memory whose strategies come back empty must fail
    the check rather than satisfy it by comparing two empty sets — the Task 8
    lesson, applied to a provisioning read-back."""
    client = FakeMemoryControl(echo_namespaces=[])
    with pytest.raises(RuntimeError, match="namespace"):
        provision_memory.provision(client)


def test_the_memory_name_is_one_the_service_accepts():
    """`Name` is `[a-zA-Z][a-zA-Z0-9_]{0,47}` — no dashes, unlike every other
    Grace resource name. A dash here fails at provision time, which is late
    but survivable; asserting it fails at test time instead."""
    assert re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,47}", naming.MEMORY), naming.MEMORY


def test_every_strategy_name_is_one_the_service_accepts():
    """A strategy `name` takes the same pattern as the memory's own, and it
    admits no `-`. Every other Grace resource is `grace-something`, so a hyphen
    here is the natural thing to write — and the plan's draft did exactly that.
    Confirmed live: `household-facts` returned `ValidationException: Value at
    'memoryStrategies.1.member.semanticMemoryStrategy.name' failed to satisfy
    constraint`. This is what turns that into a test failure rather than a
    failed deploy."""
    names = [
        body["name"]
        for strategy in provision_memory.memory_strategies()
        for body in strategy.values()
    ]
    assert names, "no strategies at all would make this vacuous"
    for name in names:
        assert re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,47}", name), name
