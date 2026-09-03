"""IAM policy shapes, asserted offline.

These build policy documents and check them as data — no AWS calls. A policy is
exactly the kind of thing that is easy to get subtly wrong and hard to notice,
so the wrong-shape cases are worth a test even though provisioning is a script.

The failure mode these guard against is specific: an over-broad policy produces
no error, no throttle, and no failing deploy. It looks exactly like a correct
one until someone uses the permission it should not have had.
"""

from __future__ import annotations

import json

import pytest

from infra import naming, provision_iam

ACCOUNT = "123456789012"

# Every role this module provisions, so the shape assertions below run against
# all four rather than only the runtime one. Derived from the module rather than
# retyped: a fifth role added later is covered without anyone remembering to
# extend this list — the same discovery-from-source discipline Task 4's model-id
# guard and Task 9's ledger-writer guard use.
ALL_PURPOSES = sorted(provision_iam.POLICY_BUILDERS)


def _as_list(value):
    return value if isinstance(value, list) else [value]


def _policy(purpose: str) -> dict:
    return provision_iam.POLICY_BUILDERS[purpose](ACCOUNT)


def _statements(policy: dict, effect: str) -> list[dict]:
    return [s for s in policy["Statement"] if s["Effect"] == effect]


# ---------------------------------------------------------------------------
# The one statement this task exists for
# ---------------------------------------------------------------------------


def test_the_runtime_policy_explicitly_denies_the_unverified_token_path():
    """Appendix D.1. `GetWorkloadAccessTokenForUserId` treats the userId as an
    opaque string with no verification, so an authenticated caseworker could
    pass any household id and get a token scoped to that household.

    An explicit Deny beats any Allow, including a future one — which is why
    this is asserted even though Identity is deferred.
    """
    policy = provision_iam.runtime_policy(ACCOUNT)
    denies = _statements(policy, "Deny")
    assert denies, "the runtime policy must carry an explicit Deny"
    actions = {a for s in denies for a in _as_list(s["Action"])}
    assert "bedrock-agentcore:GetWorkloadAccessTokenForUserId" in actions


def test_no_statement_anywhere_allows_the_unverified_token_path():
    """The Deny is only load-bearing if nothing re-grants the action.

    Asserting the Deny exists does not, by itself, establish that the unsafe
    action is unreachable: an `Allow` on the same action in another statement
    would be overridden by this Deny today, but the pairing is a trap for the
    next edit — AWS's own documented AgentCore execution-role example grants
    all three token actions together, so copying that example in later is the
    realistic path to reintroducing it. Checked across all four roles, because
    "the runtime policy is clean" is a narrower claim than "Grace never grants
    this anywhere."
    """
    for purpose in ALL_PURPOSES:
        allowed = {
            a
            for s in _statements(_policy(purpose), "Allow")
            for a in _as_list(s["Action"])
        }
        assert "bedrock-agentcore:GetWorkloadAccessTokenForUserId" not in allowed, purpose
        # A wildcard on the token family would grant it just as effectively as
        # naming it, and reads as innocuous.
        assert "bedrock-agentcore:GetWorkloadAccessToken*" not in allowed, purpose
        assert "bedrock-agentcore:*" not in allowed, purpose


def test_the_deny_names_the_directory_the_runtime_actually_uses():
    """A Deny scoped to the wrong resource is decorative.

    Appendix D.2 verified live that Runtime creates its workload identity under
    `workload-identity-directory/default`, and the nested identity ARN sits
    below it. A Deny naming only the directory would not cover an action
    authorized against the nested `workload-identity/<name>` resource, so both
    shapes must be denied.
    """
    denies = _statements(provision_iam.runtime_policy(ACCOUNT), "Deny")
    resources = [r for s in denies for r in _as_list(s["Resource"])]
    assert resources, "the Deny must name a resource"
    directory = (
        f"arn:aws:bedrock-agentcore:{naming.REGION}:{ACCOUNT}:"
        "workload-identity-directory/default"
    )
    assert directory in resources
    assert any(
        r.startswith(directory) and r.endswith("*") and "workload-identity/" in r
        for r in resources
    ), resources


# ---------------------------------------------------------------------------
# Least privilege, checked on every role rather than the interesting one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("purpose", ALL_PURPOSES)
def test_no_policy_grants_a_wildcard_action_on_a_wildcard_resource(purpose):
    """`Action: *` on `Resource: *` is the shape that makes every other
    scoping decision here decorative."""
    for statement in _policy(purpose)["Statement"]:
        if statement["Effect"] != "Allow":
            continue
        actions = _as_list(statement["Action"])
        resources = _as_list(statement["Resource"])
        assert not ("*" in actions and "*" in resources), statement


@pytest.mark.parametrize("purpose", ALL_PURPOSES)
def test_no_policy_grants_a_bare_wildcard_action(purpose):
    """`Action: "*"` is unacceptable regardless of how the resource is scoped.

    Stricter than the test above and deliberately separate: `Action: *` on a
    single named resource passes the wildcard-on-wildcard check while still
    granting every operation that resource supports — including `DeleteTable`
    on the ledger, which is the audit trail for every autonomous decision.
    """
    for statement in _policy(purpose)["Statement"]:
        if statement["Effect"] != "Allow":
            continue
        assert "*" not in _as_list(statement["Action"]), statement


@pytest.mark.parametrize("purpose", ALL_PURPOSES)
def test_every_statement_names_an_action_and_a_resource(purpose):
    """A statement missing `Resource` is not narrow — IAM has no implicit
    default, so an inline policy without it is rejected outright, and one built
    programmatically is the kind of typo that only surfaces at deploy time."""
    for statement in _policy(purpose)["Statement"]:
        assert statement.get("Action"), statement
        assert statement.get("Resource"), statement
        assert statement["Effect"] in ("Allow", "Deny"), statement


@pytest.mark.parametrize("purpose", ALL_PURPOSES)
def test_no_policy_permits_deleting_or_reconfiguring_the_ledger_table(purpose):
    """The ledger is the audit trail for every autonomous benefits decision.

    Nothing in the request path needs to delete a row, drop the table, or
    rewrite its configuration, so no role gets those actions. `DeleteItem`
    matters as much as `DeleteTable`: an agent that can delete a row can erase
    the `renewal_submitted` evidence hard rule 6 depends on.
    """
    forbidden = {
        "dynamodb:DeleteTable",
        "dynamodb:DeleteItem",
        "dynamodb:UpdateTable",
        "dynamodb:BatchWriteItem",
        "dynamodb:UpdateContinuousBackups",
    }
    granted = {
        a for s in _statements(_policy(purpose), "Allow") for a in _as_list(s["Action"])
    }
    assert not (granted & forbidden), (purpose, granted & forbidden)


# ---------------------------------------------------------------------------
# Hard rule 1 enforced by IAM, not only by the model-id test
# ---------------------------------------------------------------------------


def test_bedrock_access_is_scoped_to_the_three_nova_profiles():
    """Hard rule 1: Amazon Nova only. A wildcard on `bedrock:InvokeModel`
    would let a future edit reach a third-party model without tripping the
    model-id test that guards `grace/`."""
    policy = provision_iam.runtime_policy(ACCOUNT)
    resources = [
        r
        for s in _statements(policy, "Allow")
        for r in _as_list(s["Resource"])
        if "bedrock" in r and "agentcore" not in r
    ]
    assert resources, "no bedrock resources found in the runtime policy"
    assert all("nova" in r for r in resources), resources


def test_the_bedrock_grant_names_both_sides_of_the_inference_profile():
    """Verified against the live profiles: an inference profile ARN fans out to
    foundation-model ARNs, and the call reaches the model behind the profile.

    Granting only the profile is not enough, and granting only the foundation
    model is not either. Both shapes must be present, and every one of them
    must still name a specific Nova model — which is what keeps hard rule 1
    enforced by IAM rather than only by convention.
    """
    policy = provision_iam.runtime_policy(ACCOUNT)
    bedrock = [
        r
        for s in _statements(policy, "Allow")
        for r in _as_list(s["Resource"])
        if ":bedrock:" in r
    ]
    profiles = [r for r in bedrock if "inference-profile/" in r]
    models = [r for r in bedrock if "foundation-model/" in r]
    assert len(profiles) == 3, profiles
    assert len(models) == 3, models
    # The mapping the plan documents: `global.amazon.nova-2-lite-v1:0` on the
    # profile side becomes `amazon.nova-2-lite-v1:0` on the model side.
    assert any("amazon.nova-2-lite-v1:0" in r for r in models), models
    assert any("amazon.nova-pro-v1:0" in r for r in models), models
    assert any("amazon.nova-micro-v1:0" in r for r in models), models
    assert not any(r.endswith("foundation-model/*") for r in models), models


def test_the_foundation_model_region_stays_a_wildcard():
    """The wildcard region is deliberate and must not be "tightened".

    Verified against the live profiles: `us.amazon.nova-pro-v1:0` fans out to
    `us-west-2` and `us-east-2` as well as `us-east-1`, and
    `global.amazon.nova-2-lite-v1:0` fans out to
    `arn:aws:bedrock:::foundation-model/...` with an **empty** region field.
    Pinning `us-east-1` would fail to match either, so every Nova invocation
    would be denied — and the symptom is a runtime error at model-call time,
    not a failing test, which is why this is pinned here.
    """
    policy = provision_iam.runtime_policy(ACCOUNT)
    models = [
        r
        for s in _statements(policy, "Allow")
        for r in _as_list(s["Resource"])
        if "foundation-model/" in r
    ]
    assert models
    for arn in models:
        assert arn.startswith("arn:aws:bedrock:*::foundation-model/"), arn
    # The empty-region form a `global.` profile actually presents. A policy
    # region of `*` matches it; `us-east-1` does not.
    assert "arn:aws:bedrock:::foundation-model/amazon.nova-2-lite-v1:0".startswith(
        "arn:aws:bedrock:"
    )


def test_no_banned_model_is_reachable_through_the_runtime_policy():
    """`nova-lite-v1:0` filed a renewal it was explicitly told not to file
    (Task 4), which is why `grace/models.py` records it as banned. IAM is the
    layer that makes that unreachable rather than merely unassigned."""
    source = json.dumps(provision_iam.runtime_policy(ACCOUNT))
    for banned in ("nova-lite-v1:0",):
        # `nova-2-lite` must not trip this: check the model-id form only.
        assert f"amazon.{banned}" not in source, banned


def test_the_managed_full_access_policy_is_never_referenced():
    """The docs warn `BedrockAgentCoreFullAccess` grants the unsafe token
    action and is for development only. Grace must not use it."""
    source = json.dumps(provision_iam.runtime_policy(ACCOUNT))
    assert "BedrockAgentCoreFullAccess" not in source


def test_the_provisioner_never_attaches_a_managed_policy():
    """Stronger than checking the document text: the *script* must not call
    `attach_role_policy` at all.

    `runtime_policy` is a dict of inline statements, so no string check on it
    could ever catch an `attach_role_policy` call in `provision()` — the
    managed-policy risk lives in the provisioning code, not in the document.

    Parsed rather than grepped. A substring search over `inspect.getsource`
    matches this module's own comments explaining why it does not attach one, so
    it would fail on correct code — and worse, deleting that comment would
    "fix" it. Walking the AST asks the question that actually matters: does any
    attribute named `attach_role_policy` get called anywhere in this module.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(provision_iam))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "attach_role_policy" not in called, called
    assert "attach_group_policy" not in called, called
    assert "put_user_policy" not in called, called
    # No IAM user, access key, or login profile: roles only, so there is no
    # long-lived credential to leak.
    for forbidden in ("create_user", "create_access_key", "create_login_profile"):
        assert forbidden not in called, forbidden


# ---------------------------------------------------------------------------
# Resource scoping, per role
# ---------------------------------------------------------------------------


def test_dynamodb_access_is_scoped_to_the_grace_table():
    policy = provision_iam.runtime_policy(ACCOUNT)
    tables = [
        r
        for s in _statements(policy, "Allow")
        for r in _as_list(s["Resource"])
        if ":dynamodb:" in r
    ]
    assert tables
    assert all(naming.TABLE in r for r in tables), tables


def test_the_runtime_can_do_what_the_dynamodb_store_actually_calls():
    """Least privilege is only correct if it is also sufficient.

    `DynamoDBCaseStore` calls `put_item` and `query` (verified by reading it),
    so a policy missing either is narrow *and* broken — the ledger silently
    stops recording, which is the one thing this table exists not to do. This
    asserts the floor; the tests above assert the ceiling.
    """
    granted = {
        a
        for s in _statements(provision_iam.runtime_policy(ACCOUNT), "Allow")
        for a in _as_list(s["Action"])
    }
    assert "dynamodb:PutItem" in granted
    assert "dynamodb:Query" in granted


def test_the_lambda_role_may_invoke_only_the_grace_runtime():
    policy = provision_iam.POLICY_BUILDERS["lambda"](ACCOUNT)
    invoke = [
        r
        for s in _statements(policy, "Allow")
        for r in _as_list(s["Resource"])
        if "runtime/" in r
    ]
    assert invoke
    assert all(f"runtime/{naming.RUNTIME}" in r for r in invoke), invoke


def test_the_stepfunctions_role_may_invoke_only_the_case_lambda():
    policy = provision_iam.POLICY_BUILDERS["stepfunctions"](ACCOUNT)
    functions = [
        r
        for s in _statements(policy, "Allow")
        for r in _as_list(s["Resource"])
        if ":lambda:" in r
    ]
    assert functions == [
        f"arn:aws:lambda:{naming.REGION}:{ACCOUNT}:function:{naming.LAMBDA}"
    ], functions


def test_the_stepfunctions_role_can_write_the_escalation_row():
    """The Catch branch writes the escalation row. Without `PutItem` a failed
    case fails *silently* at the point whose whole purpose is not losing it —
    no verdict is not the same as nothing happened."""
    policy = provision_iam.POLICY_BUILDERS["stepfunctions"](ACCOUNT)
    granted = {
        a for s in _statements(policy, "Allow") for a in _as_list(s["Action"])
    }
    assert "dynamodb:PutItem" in granted


def test_the_eventbridge_role_may_start_only_the_grace_sweep():
    policy = provision_iam.POLICY_BUILDERS["eventbridge"](ACCOUNT)
    statements = _statements(policy, "Allow")
    granted = {a for s in statements for a in _as_list(s["Action"])}
    assert granted == {"states:StartExecution"}, granted
    resources = [r for s in statements for r in _as_list(s["Resource"])]
    assert resources == [
        f"arn:aws:states:{naming.REGION}:{ACCOUNT}:stateMachine:{naming.STATE_MACHINE}"
    ], resources


@pytest.mark.parametrize("purpose", ALL_PURPOSES)
def test_no_role_can_rewrite_its_own_permissions(purpose):
    """Privilege escalation via `iam:*` would make every scoping decision
    above reversible from inside the request path."""
    granted = {
        a for s in _statements(_policy(purpose), "Allow") for a in _as_list(s["Action"])
    }
    assert not any(a.startswith("iam:") for a in granted), (purpose, granted)
    assert not any(a.startswith("sts:") for a in granted), (purpose, granted)


# ---------------------------------------------------------------------------
# Trust policies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("purpose", ALL_PURPOSES)
def test_every_trust_policy_is_scoped_to_this_account(purpose):
    """The confused-deputy control. Without it, any account could induce the
    service to assume Grace's role on its behalf.

    Verified empirically for Lambda, the one service of the four whose docs do
    *not* show this condition: a probe role with a wrong account value was
    refused with "The role defined for the function cannot be assumed by
    Lambda", and the identical call succeeded once the value was corrected — so
    the key is populated and the condition is genuinely evaluated rather than
    silently ignored.
    """
    trust = provision_iam.trust_policy(purpose, ACCOUNT)
    statements = trust["Statement"]
    assert len(statements) == 1, statements
    condition = statements[0].get("Condition", {})
    assert condition.get("StringEquals", {}).get("aws:SourceAccount") == ACCOUNT, trust


@pytest.mark.parametrize("purpose", ALL_PURPOSES)
def test_every_trust_policy_names_exactly_one_service_principal(purpose):
    """A trust policy naming a second principal is how a role intended for one
    service quietly becomes assumable by another."""
    trust = provision_iam.trust_policy(purpose, ACCOUNT)
    statement = trust["Statement"][0]
    assert statement["Action"] == "sts:AssumeRole"
    assert statement["Effect"] == "Allow"
    principal = statement["Principal"]
    assert set(principal) == {"Service"}, principal
    assert principal["Service"] == provision_iam.TRUST_PRINCIPALS[purpose]
    assert principal["Service"].endswith(".amazonaws.com")


def test_no_trust_policy_trusts_a_bare_account_root_or_a_wildcard():
    """`Principal: "*"` or a bare account root would let any identity in the
    account — or anywhere — assume an execution role."""
    for purpose in ALL_PURPOSES:
        source = json.dumps(provision_iam.trust_policy(purpose, ACCOUNT))
        assert '"*"' not in source, purpose
        assert ":root" not in source, purpose


def test_the_four_roles_are_the_four_the_plan_names():
    """Names are asserted rather than eyeballed: `provision_all` and the
    runbook both look these up by purpose, and a rename that is not reflected
    in both places orphans a role nobody notices."""
    assert set(ALL_PURPOSES) == {"runtime", "lambda", "stepfunctions", "eventbridge"}
    assert set(provision_iam.TRUST_PRINCIPALS) == set(ALL_PURPOSES)
    for purpose in ALL_PURPOSES:
        assert provision_iam.role_name(purpose) == f"grace-{purpose}-role"
        assert provision_iam.role_name(purpose).startswith("grace-")


# ---------------------------------------------------------------------------
# Provisioning behaviour, against a fake IAM client
# ---------------------------------------------------------------------------


class _FakeIam:
    """Enough of the IAM client to observe what `provision` does.

    Records calls in order so idempotence can be asserted as a property of the
    call sequence rather than of a return value.
    """

    def __init__(self, existing: set[str] | None = None):
        self.existing = set(existing or ())
        self.calls: list[tuple[str, str]] = []
        self.trust: dict[str, dict] = {}
        self.inline: dict[str, dict] = {}

    def create_role(self, **kw):
        name = kw["RoleName"]
        self.calls.append(("create_role", name))
        if name in self.existing:
            from botocore.exceptions import ClientError

            raise ClientError(
                {"Error": {"Code": "EntityAlreadyExists", "Message": "exists"}},
                "CreateRole",
            )
        self.existing.add(name)
        self.trust[name] = json.loads(kw["AssumeRolePolicyDocument"])
        return {"Role": {"Arn": f"arn:aws:iam::{ACCOUNT}:role/{name}"}}

    def update_assume_role_policy(self, **kw):
        name = kw["RoleName"]
        self.calls.append(("update_assume_role_policy", name))
        self.trust[name] = json.loads(kw["PolicyDocument"])

    def put_role_policy(self, **kw):
        name = kw["RoleName"]
        self.calls.append(("put_role_policy", name))
        self.inline[name] = {
            "PolicyName": kw["PolicyName"],
            "PolicyDocument": json.loads(kw["PolicyDocument"]),
        }

    def get_role(self, **kw):
        name = kw["RoleName"]
        return {"Role": {"Arn": f"arn:aws:iam::{ACCOUNT}:role/{name}"}}


def test_provision_creates_all_four_roles_and_returns_their_arns():
    client = _FakeIam()
    arns = provision_iam.provision(client=client, account_id=ACCOUNT)
    assert set(arns) == set(ALL_PURPOSES)
    for purpose, arn in arns.items():
        assert arn == f"arn:aws:iam::{ACCOUNT}:role/grace-{purpose}-role"
    for purpose in ALL_PURPOSES:
        assert f"grace-{purpose}-role" in client.inline


def test_rerunning_provision_converges_on_the_intended_trust_policy():
    """Idempotence that means convergence, not just "does not crash".

    A re-run must overwrite whatever a previous version wrote. The specific
    risk: an earlier deploy left a trust policy with no account condition, the
    `EntityAlreadyExists` branch is taken, and a script that only swallowed the
    error would leave the weaker policy in place while reporting success — the
    same "control looks present and is absent" shape Task 1's PITR bug had.
    """
    stale = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    client = _FakeIam(existing={f"grace-{p}-role" for p in ALL_PURPOSES})
    client.trust = {f"grace-{p}-role": dict(stale) for p in ALL_PURPOSES}

    provision_iam.provision(client=client, account_id=ACCOUNT)

    for purpose in ALL_PURPOSES:
        name = f"grace-{purpose}-role"
        assert ("update_assume_role_policy", name) in client.calls
        written = client.trust[name]
        condition = written["Statement"][0]["Condition"]["StringEquals"]
        assert condition["aws:SourceAccount"] == ACCOUNT
        assert (
            written["Statement"][0]["Principal"]["Service"]
            == provision_iam.TRUST_PRINCIPALS[purpose]
        )


def test_provision_rewrites_the_inline_policy_on_every_run():
    """The Deny must land on a re-run too.

    `put_role_policy` is an overwrite, so an existing role converges on the
    current document — which is what makes adding the Deny to an already-created
    role a matter of re-running the script rather than deleting the role.
    """
    client = _FakeIam(existing={f"grace-{p}-role" for p in ALL_PURPOSES})
    provision_iam.provision(client=client, account_id=ACCOUNT)
    document = client.inline["grace-runtime-role"]["PolicyDocument"]
    actions = {
        a
        for s in document["Statement"]
        if s["Effect"] == "Deny"
        for a in _as_list(s["Action"])
    }
    assert "bedrock-agentcore:GetWorkloadAccessTokenForUserId" in actions


def test_provision_reraises_an_unexpected_iam_error():
    """A provisioning script that swallows an error it did not anticipate
    reports success while the role is absent or misconfigured. Only
    `EntityAlreadyExists` is a non-failure here."""
    from botocore.exceptions import ClientError

    class _Denied(_FakeIam):
        def create_role(self, **kw):
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "CreateRole"
            )

    with pytest.raises(ClientError):
        provision_iam.provision(client=_Denied(), account_id=ACCOUNT)


def test_each_role_gets_its_own_policy_document():
    """A copy-paste that gave every role the runtime's policy would grant the
    EventBridge role Bedrock and DynamoDB access, and would still pass every
    per-role test above if they all read the same document."""
    client = _FakeIam()
    provision_iam.provision(client=client, account_id=ACCOUNT)
    rendered = {
        purpose: json.dumps(
            client.inline[f"grace-{purpose}-role"]["PolicyDocument"], sort_keys=True
        )
        for purpose in ALL_PURPOSES
    }
    assert len(set(rendered.values())) == len(ALL_PURPOSES), rendered
    # Only the runtime talks to Bedrock.
    for purpose in ALL_PURPOSES:
        if purpose == "runtime":
            assert ":bedrock:" in rendered[purpose]
        else:
            assert ":bedrock:" not in rendered[purpose], purpose


def test_every_role_is_tagged_for_cost_attribution_and_teardown():
    """`teardown.py` identifies what it owns, and Cost Explorer separates
    Grace's spend against a $50 credit budget."""
    captured: dict[str, list] = {}

    class _TagCapture(_FakeIam):
        def create_role(self, **kw):
            captured[kw["RoleName"]] = kw.get("Tags", [])
            return super().create_role(**kw)

    provision_iam.provision(client=_TagCapture(), account_id=ACCOUNT)
    assert len(captured) == len(ALL_PURPOSES)
    for name, tags in captured.items():
        assert {t["Key"]: t["Value"] for t in tags} == naming.TAGS, name
