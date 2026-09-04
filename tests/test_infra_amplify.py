"""The Amplify app's shape, asserted offline.

Three assertions here are the ones that matter, and each guards a failure that
produces a **green build and a wrong app** rather than an error:

1. `PLATFORM == "WEB_COMPUTE"`. `WEB` is a static host with no route handlers and
   no middleware, so deploying onto it would silently remove the Cognito gate and
   the decide endpoint.
2. The buildspec writes `web/.env.production`. Amplify's `environmentVariables`
   reach the **build** environment only — AWS documents this as intentional — so
   without that step every `process.env` read in a server component returns
   `undefined` at request time. `readEnv()` then throws, `readCase` catches, and
   all twelve households render as an empty caseload with nothing logged.
3. The compute policy names the GSI's own ARN. Measured with
   `simulate-principal-policy`: a table-only grant leaves `Query` on
   `table/grace-cases/index/escalation-queue` at `implicitDeny` while the table
   itself is `allowed`, so `/case/c-010` renders perfectly and `/queue` — the one
   page the product exists for — comes back empty.
"""

from __future__ import annotations

import json
import pathlib

import yaml

from infra import naming, provision_amplify


# --------------------------------------------------------------------------
# The app's own shape
# --------------------------------------------------------------------------


def test_the_platform_is_the_ssr_one():
    """WEB_COMPUTE, verified against the live enum
    ['WEB', 'WEB_DYNAMIC', 'WEB_COMPUTE']. WEB is static."""
    assert provision_amplify.PLATFORM == "WEB_COMPUTE"


def test_the_app_is_named_for_grace():
    assert provision_amplify.APP_NAME.startswith("grace")


def test_every_resource_this_module_names_is_a_grace_resource():
    """This account holds two other projects' Cognito pools and 16 AgentCore
    runtimes. A name without the prefix is a name that could collide with, or be
    mistaken for, someone else's resource."""
    for value in (
        provision_amplify.APP_NAME,
        provision_amplify.COMPUTE_ROLE_NAME,
        provision_amplify.COMPUTE_POLICY_NAME,
    ):
        assert value.startswith("grace-"), value


# --------------------------------------------------------------------------
# The buildspec
# --------------------------------------------------------------------------


def _frontend(spec: dict) -> dict:
    """The `frontend` block, from the monorepo `applications:` form."""
    return spec["applications"][0]["frontend"]


def test_the_build_spec_is_valid_yaml_and_builds_web():
    spec = yaml.safe_load(provision_amplify.build_spec())
    frontend = _frontend(spec)
    assert "npm ci" in " ".join(frontend["phases"]["preBuild"]["commands"])
    assert "npm run build" in " ".join(frontend["phases"]["build"]["commands"])
    # `.next` is the SSR output. A `baseDirectory` of `out` would mean a static
    # export, which is the same mistake as the wrong platform.
    assert frontend["artifacts"]["baseDirectory"] == ".next"


def test_the_build_spec_is_the_monorepo_form_and_names_the_app_root():
    """**This overturns an earlier version of this test, and two live builds are
    why.** The repository has no root `package.json` — `web/` holds the only one —
    so Amplify cannot find Next at the repository root:

        job 2  AMPLIFY_MONOREPO_APP_ROOT absent, top-level `frontend:`
               -> CustomerError: Cannot read 'next' version in package.json.
        job 3  AMPLIFY_MONOREPO_APP_ROOT = web, top-level `frontend:`
               -> CustomerError: Monorepo spec provided without "applications" key

    Both died at clone time, about a second in, before any phase ran. The variable
    is mandatory, and once it is set an `applications:` key is mandatory too — so
    the documented `cd web` single-app form is not available here at all.

    `appRoot` and `AMPLIFY_MONOREPO_APP_ROOT` must agree; AWS says the variable
    "must exist, and have the same value as" the key.
    """
    spec = yaml.safe_load(provision_amplify.build_spec())
    assert "frontend" not in spec, "a top-level frontend: is refused with appRoot set"
    assert spec["applications"][0]["appRoot"] == provision_amplify.APP_ROOT
    assert (
        provision_amplify.ENVIRONMENT_VARIABLES["AMPLIFY_MONOREPO_APP_ROOT"]
        == provision_amplify.APP_ROOT
    )


def test_the_app_root_is_a_directory_that_actually_holds_the_next_app():
    """**An independent anchor, because the test above cannot fail on its own.**

    That test compares `appRoot` and `AMPLIFY_MONOREPO_APP_ROOT` to the *same*
    constant, so changing `APP_ROOT` changes both sides and it stays green —
    measured by sabotage (`APP_ROOT = "webb"` survived). Agreement between two
    values derived from one source is not a fact about the repository.

    The repository is the anchor: `appRoot` must name a directory that exists and
    contains the `package.json` declaring `next`. Amplify reads exactly that file
    to decide the app is a Next.js app, and job 2 died at clone time because it
    looked in the wrong place.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    app_root = root / provision_amplify.APP_ROOT
    assert app_root.is_dir(), f"{provision_amplify.APP_ROOT} is not a directory"
    manifest = app_root / "package.json"
    assert manifest.is_file(), f"no package.json in {provision_amplify.APP_ROOT}"
    assert "next" in json.loads(manifest.read_text())["dependencies"]
    # And the repository root must NOT have one, which is *why* the monorepo form
    # is mandatory rather than merely tidier. If a root `package.json` ever
    # appears, this whole choice is worth revisiting.
    assert not (root / "package.json").exists(), (
        "a root package.json now exists — Amplify may detect Next at the "
        "repository root, so re-derive whether the monorepo form is still needed"
    )


def test_the_build_spec_does_not_cd_into_the_app_root():
    """Amplify enters `appRoot` itself in the monorepo form, so a `cd web` would
    land in `web/web`. Every path in the commands is relative to `appRoot`."""
    for command in provision_amplify.build_spec_commands():
        assert not command.startswith("cd "), command


def test_the_base_directory_is_relative_to_the_app_root():
    """`baseDirectory` resolves against `buildPath`, which defaults to `appRoot`
    when omitted — so `.next` already means `web/.next`. Naming `web/.next` here
    would resolve to `web/web/.next` and fail on `required-server-files.json`.
    `buildPath` must therefore stay **absent**: setting it to `/` would silently
    change what `baseDirectory` means."""
    spec = yaml.safe_load(provision_amplify.build_spec())
    frontend = _frontend(spec)
    assert "buildPath" not in frontend
    assert frontend["artifacts"]["baseDirectory"] == ".next"


def test_the_build_spec_runs_the_tests_before_building():
    """A deploy that skips the tests can ship a broken auth gate. The gate's
    tests are the reason this is not merely tidy."""
    commands = " ".join(provision_amplify.build_spec_commands())
    assert "npm run test" in commands
    assert "npm run typecheck" in commands


def test_the_tests_run_before_the_build_not_after():
    """Order, not mere presence. `npm run test` listed *after* `npm run build`
    still satisfies a substring check while letting a broken authorisation gate
    reach the artifact — the build has already produced it by then."""
    commands = provision_amplify.build_spec_commands()
    assert commands.index("npm run test") < commands.index("npm run build")
    assert commands.index("npm run typecheck") < commands.index("npm run build")


def test_the_build_spec_pins_the_node_version():
    """Amplify's build image default is contradictory in AWS's own documentation
    (one paragraph says Node 20, the next says 22), and the *build* Node version
    also selects the SSR *runtime* version. Next 16.3.4 requires >=20.9.0. So the
    version is pinned in the buildspec rather than inherited — and `nvm use` in
    `preBuild` is documented to override live package updates, which run first."""
    commands = provision_amplify.build_spec_commands()
    assert f"nvm use {provision_amplify.NODE_VERSION}" in commands
    assert int(provision_amplify.NODE_VERSION) >= 20


def test_the_node_version_is_pinned_before_anything_uses_node():
    """`nvm use` after `npm ci` installs against the wrong Node. Ordering is the
    property; presence is not."""
    commands = provision_amplify.build_spec_commands()
    node_at = commands.index(f"nvm use {provision_amplify.NODE_VERSION}")
    for consumer in ("npm ci", "npm run build"):
        assert node_at < commands.index(consumer), consumer


def test_the_build_spec_writes_the_runtime_env_file():
    """**The single most consequential line in the buildspec.**

    Amplify injects `environmentVariables` into the *build* environment only, and
    AWS documents that a Next.js server component "doesn't have access to those
    environment variables by default … This behavior is intentional". The
    documented remedy is to write them into `.env.production` during the build,
    which Next loads at request time.

    Without it: a green build, a successful deploy, and `readEnv()` throwing on
    every request. `readCase` catches, so all twelve households read back `null`
    and `/` renders an empty caseload with nothing in any log saying why — the
    exact failure `lib/cases.ts`'s own header comment describes for a missing
    `GRACE_TABLE_NAME`.
    """
    commands = provision_amplify.build_spec_commands()
    writes = [c for c in commands if provision_amplify.RUNTIME_ENV_FILE_IN_WEB in c]
    assert writes, f"nothing writes {provision_amplify.RUNTIME_ENV_FILE_IN_WEB}"
    # Every runtime variable the app reads must be in the writes, or that one
    # variable alone is missing at request time.
    joined = " ".join(writes)
    for name in provision_amplify.RUNTIME_VARIABLES:
        assert name in joined, name


def test_the_env_file_commands_are_relative_to_the_app_root():
    """The build has two frames of reference. Every command runs inside `appRoot`
    (Amplify enters it in the monorepo form), so a command naming
    `web/.env.production` would create `web/web/.env.production` — which Next
    never loads, on a green build. `RUNTIME_ENV_FILE` keeps the repo-relative
    spelling because the artifact-containment check needs it. Both must exist and
    agree."""
    assert (
        provision_amplify.RUNTIME_ENV_FILE
        == "web/" + provision_amplify.RUNTIME_ENV_FILE_IN_WEB
    )
    for command in provision_amplify.build_spec_commands():
        if provision_amplify.RUNTIME_ENV_FILE_IN_WEB in command:
            assert "web/" not in command, command


def test_the_runtime_env_file_is_written_before_the_build():
    """`next build` prerenders, and `/login` is `force-dynamic` precisely because
    that evaluation would otherwise happen at build time. Writing the file after
    the build would leave the artifact correct and the *build* reading nothing."""
    commands = provision_amplify.build_spec_commands()
    first_write = min(
        i for i, c in enumerate(commands)
        if provision_amplify.RUNTIME_ENV_FILE_IN_WEB in c
    )
    assert first_write < commands.index("npm run build")


def test_the_runtime_env_file_is_truncated_before_it_is_appended_to():
    """Amplify caches `node_modules` between builds and a `>>` redirect appends.
    A stale `.env.production` surviving into a later build would leave *two*
    values for one variable — and `dotenv` keeps the first, so a rotated Cognito
    client id would be silently ignored on every rebuild while the console showed
    the new one."""
    commands = provision_amplify.build_spec_commands()
    truncate_at = min(
        i for i, c in enumerate(commands)
        if c.startswith("rm -f") and provision_amplify.RUNTIME_ENV_FILE_IN_WEB in c
    )
    append_at = min(
        i for i, c in enumerate(commands)
        if ">>" in c and provision_amplify.RUNTIME_ENV_FILE_IN_WEB in c
    )
    assert truncate_at < append_at


def test_the_runtime_env_file_is_not_inside_the_artifact_directory():
    """`baseDirectory` is `web/.next` and Amplify's build artifacts are
    downloadable via `get-job`. Measured: a real `next build` leaves
    `.env.production` in `web/`, outside `.next`, and `required-server-files.json`
    reports `config.env: {}` — so the values are read from the file at request
    time rather than baked into a downloadable chunk. A path under `.next` would
    reverse that."""
    env_file = provision_amplify.RUNTIME_ENV_FILE
    assert not env_file.startswith("web/.next")
    assert ".next" not in env_file


def test_the_cache_does_not_include_the_runtime_env_file():
    """Caching the file would defeat the truncation above by restoring it before
    `rm -f` had anything to do with the *current* build's values."""
    spec = yaml.safe_load(provision_amplify.build_spec())
    for path in _frontend(spec)["cache"]["paths"]:
        assert ".env" not in path, path


# --------------------------------------------------------------------------
# Environment variables
# --------------------------------------------------------------------------


def test_no_secret_is_in_the_environment_variables():
    """Amplify env vars are visible in the console and in build logs — and the
    `.env.production` this buildspec writes ships inside a build artifact any
    caller of `get-job` can download. Resource names are fine; a credential is
    not — the compute role supplies those."""
    env = provision_amplify.ENVIRONMENT_VARIABLES
    joined = " ".join(f"{k}={v}" for k, v in env.items()).lower()
    for forbidden in ["secret", "password", "aws_access_key", "private", "token"]:
        assert forbidden not in joined, forbidden


def test_no_public_variable_carries_backend_detail():
    """A NEXT_PUBLIC_ variable is compiled into the client bundle. The table
    name and runtime ARN are a map of the backend and nothing in the browser
    needs them."""
    for name in provision_amplify.ENVIRONMENT_VARIABLES:
        assert not name.startswith("NEXT_PUBLIC_"), name


def test_no_variable_uses_amplifys_reserved_aws_prefix():
    """Measured against the live API: `AWS_REGION`, `AWS_DEFAULT_REGION`, and
    `AWS_ACCESS_KEY_ID` are each refused with
    `BadRequestException: Environment variables cannot start with the reserved
    prefix "AWS".` So the plan's draft `AWS_REGION` entry would have made
    `provision` fail outright — the loud failure, fortunately, not the quiet one.
    `readEnv()` already defaults `region` to `us-east-1` when `AWS_REGION` is
    absent, so nothing needs it."""
    for name in provision_amplify.ENVIRONMENT_VARIABLES:
        assert not name.startswith("AWS"), name


def test_every_variable_the_app_reads_at_request_time_is_provisioned():
    """**Discovery from disk, not a list someone remembered.**

    `web/` is grepped for `process.env.X` reads and every one that is neither
    Node's own nor a test-only injection point must be provisioned — the same
    discipline as Task 4's `pkgutil` model-id walk and Task 9's ledger-writer
    walk, for the same reason: a variable added to `web/` and forgotten here is
    `undefined` in production, and `readEnv()` then fails closed into an empty
    caseload with nothing logged.

    An earlier version of this test iterated `RUNTIME_VARIABLES` and asserted its
    members were in `ENVIRONMENT_VARIABLES`. That is a tautology — both are
    written here, so deleting `DASHBOARD_URL` from the tuple satisfied it while
    removing the variable from the deployed app. Caught by sabotage; the fix is to
    read the requirement from `web/` instead of from this module.
    """
    read_in_web = _env_reads_in_web()
    assert read_in_web, "the grep found nothing — it is no longer discovering"
    for name in sorted(read_in_web):
        assert name in provision_amplify.ENVIRONMENT_VARIABLES, (
            f"{name} is read by web/ at request time and is not provisioned"
        )
        assert name in provision_amplify.RUNTIME_VARIABLES, (
            f"{name} is read by web/ and is not written to .env.production"
        )


def test_the_grep_finds_the_variables_it_is_supposed_to_find():
    """A guard on the guard. If `_env_reads_in_web` silently stopped matching,
    the test above would pass having checked nothing — the Task 8 vacuity lesson.
    These three are read by `lib/env.ts`, `lib/cognito.ts`, and
    `app/login/page.tsx` respectively, and the first is found only by the
    *indirect* pattern."""
    found = _env_reads_in_web()
    for expected in ("GRACE_TABLE_NAME", "COGNITO_ISSUER", "DASHBOARD_URL"):
        assert expected in found, expected


def test_the_region_is_the_one_variable_that_may_be_absent():
    """`lib/cases.ts` reads `AWS_REGION`, and Amplify refuses to store any
    variable with the reserved `AWS` prefix. That collision is only safe because
    `readEnv()` gives this one variable a **default** rather than a throw:

        region: source.AWS_REGION?.trim() || "us-east-1"

    Every other variable calls `required()` and throws. If someone ever converts
    the region to `required()`, this test fails and says why — otherwise the
    unprovisionable variable would become a startup requirement that cannot be
    satisfied, and every page would fail closed on the deployed app.
    """
    env_source = (
        pathlib.Path(__file__).resolve().parent.parent / "web" / "lib" / "env.ts"
    ).read_text()
    assert 'required(source, "AWS_REGION")' not in env_source
    assert 'source.AWS_REGION?.trim() || "us-east-1"' in env_source


def test_the_test_only_jwks_override_is_never_provisioned():
    """`COGNITO_TEST_JWKS` replaces Cognito's published key set. `lib/cognito.ts`
    gates reading it on `NODE_ENV === "test"`, but setting it on the deployed app
    at all would be one `NODE_ENV` mistake away from every forged token
    verifying with a valid signature."""
    assert "COGNITO_TEST_JWKS" not in provision_amplify.ENVIRONMENT_VARIABLES
    assert "COGNITO_TEST_JWKS" not in provision_amplify.RUNTIME_VARIABLES
    assert "COGNITO_TEST_JWKS" not in provision_amplify.build_spec()


# --------------------------------------------------------------------------
# The SSR compute role — the thing that makes reads work at all
# --------------------------------------------------------------------------


def test_the_compute_role_trusts_amplify_and_nothing_else():
    """Probed both ways on 2026-09-04, because acceptance alone would not prove
    the parameter is validated rather than ignored:

        lambda.amazonaws.com  -> BadRequestException: The compute role provided
                                 cannot be assumed by Amplify.
        amplify.amazonaws.com -> accepted, computeRoleArn echoed back
    """
    trust = provision_amplify.compute_trust_policy("339712964409")
    principals = [s["Principal"]["Service"] for s in trust["Statement"]]
    assert principals == ["amplify.amazonaws.com"]
    assert all(s["Action"] == "sts:AssumeRole" for s in trust["Statement"])


def test_the_compute_role_trust_is_scoped_to_this_account():
    """A confused-deputy guard, and genuinely evaluated rather than decorative —
    probed both halves against `CreateApp`:

        aws:SourceAccount 111122223333 -> REFUSED (cannot be assumed by Amplify)
        aws:SourceAccount <this acct>  -> ACCEPTED

    The refusal is what distinguishes "condition satisfied" from "condition
    ignored"; Plan 2 established the same discipline for Lambda's trust policy.
    """
    trust = provision_amplify.compute_trust_policy("339712964409")
    condition = trust["Statement"][0]["Condition"]
    assert condition["StringEquals"]["aws:SourceAccount"] == "339712964409"


def test_the_compute_policy_names_the_index_arn_as_well_as_the_table():
    """Measured with `simulate-principal-policy` against a real role, both ways:

        grant [table]         -> Query table: allowed   Query index: implicitDeny
        grant [table, index]  -> Query table: allowed   Query index: allowed

    `readCase` reads the table; `listQueue` reads the index. A table-only grant
    therefore renders `/case/c-010` perfectly and `/queue` empty — the one page
    the product exists for, failing on the households who need a human, with a
    green deploy and no error visible outside a server log.
    """
    policy = provision_amplify.compute_policy("339712964409")
    resources = {r for s in policy["Statement"] for r in _as_list(s["Resource"])}
    table = f"arn:aws:dynamodb:{naming.REGION}:339712964409:table/{naming.TABLE}"
    assert table in resources
    assert f"{table}/index/{naming.ESCALATION_GSI}" in resources


def test_the_compute_policy_does_not_grant_a_wildcard_index():
    """`table/grace-cases/index/*` would also reach any index added later — by a
    future task, or by someone debugging. The queue is the only index this app
    reads, and it is named."""
    policy = provision_amplify.compute_policy("339712964409")
    resources = {r for s in policy["Statement"] for r in _as_list(s["Resource"])}
    for resource in resources:
        assert not resource.endswith("/index/*"), resource


def test_the_compute_policy_grants_no_scan_and_no_delete():
    """No `Scan`: `lib/cases.ts` queries, and `Scan` would let a bug read every
    ledger row for all twelve households in one call. No `DeleteItem` and no
    `UpdateItem`: the ledger is the audit trail for every autonomous benefits
    decision, and a dashboard that can delete a row can destroy the
    `renewal_submitted` evidence hard rule 6 depends on. Append and read only.
    """
    policy = provision_amplify.compute_policy("339712964409")
    actions = {a for s in policy["Statement"] for a in _as_list(s["Action"])}
    for forbidden in (
        "dynamodb:Scan",
        "dynamodb:DeleteItem",
        "dynamodb:UpdateItem",
        "dynamodb:BatchWriteItem",
        "dynamodb:DeleteTable",
    ):
        assert forbidden not in actions, forbidden


def test_the_compute_policy_grants_no_wildcard_action_anywhere():
    """`dynamodb:*` would satisfy every "grants Query" assertion above while also
    granting `Scan` and `DeleteItem`. A wildcard makes the absence tests
    unfalsifiable, so the wildcard itself is what is refused."""
    policy = provision_amplify.compute_policy("339712964409")
    for statement in policy["Statement"]:
        for action in _as_list(statement["Action"]):
            assert "*" not in action, action


def test_the_compute_policy_grants_exactly_the_three_dynamodb_actions_used():
    """`web/lib` sends `QueryCommand` and `PutItemCommand` and nothing else, and
    `readCase`/`listQueue` both go through `Query`. `GetItem` is granted because
    a single-row read is the obvious next reader; anything beyond these three is
    a capability with no caller."""
    policy = provision_amplify.compute_policy("339712964409")
    dynamo = {
        a
        for s in policy["Statement"]
        for a in _as_list(s["Action"])
        if a.startswith("dynamodb:")
    }
    assert dynamo == {"dynamodb:Query", "dynamodb:GetItem", "dynamodb:PutItem"}


def test_the_compute_policy_can_invoke_only_the_grace_runtime():
    """The decide route re-invokes so the gate re-evaluates. Scoped to Grace's
    own runtime ARN prefix, so a compromised dashboard cannot drive the other 15
    runtimes in this account."""
    policy = provision_amplify.compute_policy("339712964409")
    invoke = [
        s
        for s in policy["Statement"]
        if "bedrock-agentcore:InvokeAgentRuntime" in _as_list(s["Action"])
    ]
    assert len(invoke) == 1
    for resource in _as_list(invoke[0]["Resource"]):
        assert f":runtime/{naming.RUNTIME}" in resource, resource


def test_the_compute_policy_denies_the_unverified_token_path():
    """Hard rule / Appendix D.1, carried across to the fourth role that can now
    reach AgentCore. `GetWorkloadAccessTokenForUserId` performs no verification of
    the user id it is handed, so an authenticated caseworker could obtain a token
    scoped to any household. Nothing here grants it — the Deny exists so that
    copying AWS's own example execution policy in later stays harmless."""
    policy = provision_amplify.compute_policy("339712964409")
    denies = [s for s in policy["Statement"] if s["Effect"] == "Deny"]
    denied = {a for s in denies for a in _as_list(s["Action"])}
    assert "bedrock-agentcore:GetWorkloadAccessTokenForUserId" in denied


def test_the_compute_policy_grants_no_cognito_access():
    """`verifySession` verifies a JWT against Cognito's *public* JWKS over plain
    HTTPS. It needs no AWS credentials, so a grant here would be a capability
    with no caller — and `cognito-idp:AdminCreateUser` on this pool would let the
    dashboard mint itself a caseworker."""
    policy = provision_amplify.compute_policy("339712964409")
    actions = {a for s in policy["Statement"] for a in _as_list(s["Action"])}
    assert not [a for a in actions if a.startswith("cognito")], actions


# --------------------------------------------------------------------------
# Provisioning behaviour, against a fake client
# --------------------------------------------------------------------------


class _FakeAmplify:
    """Records calls. Deliberately able to *fail* the way the real service fails
    — a fake that only ever succeeds makes the suite look like it covers the
    boundary (Plan 2's `FakeTable` lesson)."""

    def __init__(self, apps=None, pages=None, fail_on: str | None = None,
                 known_roles: set[str] | None = None, repository: str = "",
                 environment: dict | None = None):
        self.apps = list(apps or [])
        self.repository = repository
        # What `get_app` reports as already set. The console writes keys of its
        # own into this map, which is what makes a full-replace update dangerous.
        self.environment = dict(environment or {})
        # Lets a test make `get_branch` fail the way the real service can with
        # something other than a not-found.
        self.branch_error: Exception | None = None
        self.pages = pages
        self.calls: list[tuple[str, dict]] = []
        self.fail_on = fail_on
        self.branches: set[str] = set()
        # A live view of which roles exist, so the fake can refuse a role that
        # has not been created yet — exactly as `CreateApp` does, and with the
        # same `BadRequestException` message.
        self.known_roles = known_roles

    def _check_role(self, kwargs):
        arn = kwargs.get("computeRoleArn")
        if arn is None or self.known_roles is None:
            return
        if arn.rsplit("/", 1)[-1] not in self.known_roles:
            raise _client_error(
                "BadRequestException",
                "The compute role provided cannot be assumed by Amplify.",
            )

    # `list_apps` paginates: this account holds other projects' apps.
    def get_paginator(self, name):
        assert name == "list_apps"
        pages = self.pages or [{"apps": self.apps}]
        fake = self

        class _P:
            def paginate(self, **_kwargs):
                fake.calls.append(("paginate", {}))
                return iter(pages)

        return _P()

    def create_app(self, **kwargs):
        self.calls.append(("create_app", kwargs))
        if self.fail_on == "create_app":
            raise _client_error("BadRequestException", "The compute role provided cannot be assumed by Amplify.")
        self._check_role(kwargs)
        return {"app": {"appId": "abc123", "defaultDomain": "abc123.amplifyapp.com"}}

    def update_app(self, **kwargs):
        self.calls.append(("update_app", kwargs))
        self._check_role(kwargs)
        # **A full replace, exactly as measured on the real service**: the map
        # sent becomes the whole map, so anything omitted is deleted. A fake that
        # merged here could never reproduce the live failure.
        if "environmentVariables" in kwargs:
            self.environment = dict(kwargs["environmentVariables"])
        return {"app": {"appId": kwargs["appId"]}}

    def get_app(self, **kwargs):
        self.calls.append(("get_app", kwargs))
        app = {"appId": kwargs["appId"], "defaultDomain": "abc123.amplifyapp.com",
               "platform": provision_amplify.PLATFORM,
               "environmentVariables": dict(self.environment)}
        # Only present when a repository is actually connected, exactly as the
        # real API behaves: `get_app` omits the key entirely otherwise.
        if self.repository:
            app["repository"] = self.repository
        return {"app": app}

    def create_branch(self, **kwargs):
        self.calls.append(("create_branch", kwargs))
        if self.fail_on == "create_branch":
            raise _client_error(
                "BadRequestException",
                "The compute role provided cannot be assumed by Amplify.",
            )
        if kwargs["branchName"] in self.branches:
            raise _client_error(
                "BadRequestException",
                f"Failed to create branch. The branch {kwargs['branchName']} already exists",
            )
        self.branches.add(kwargs["branchName"])
        return {"branch": {"branchName": kwargs["branchName"]}}

    def update_branch(self, **kwargs):
        self.calls.append(("update_branch", kwargs))
        return {"branch": {"branchName": kwargs["branchName"]}}

    def tag_resource(self, **kwargs):
        self.calls.append(("tag_resource", kwargs))
        return {}

    def get_branch(self, **kwargs):
        self.calls.append(("get_branch", kwargs))
        if self.branch_error is not None:
            raise self.branch_error
        if kwargs["branchName"] not in self.branches:
            raise _client_error("NotFoundException", "no branch")
        return {"branch": {"branchName": kwargs["branchName"]}}


class _FakeIam:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.calls: list[tuple[str, dict]] = []

    def create_role(self, **kwargs):
        self.calls.append(("create_role", kwargs))
        if kwargs["RoleName"] in self.existing:
            raise _client_error("EntityAlreadyExists", "role exists")
        self.existing.add(kwargs["RoleName"])

    def update_assume_role_policy(self, **kwargs):
        self.calls.append(("update_assume_role_policy", kwargs))

    def put_role_policy(self, **kwargs):
        self.calls.append(("put_role_policy", kwargs))

    def get_role(self, **kwargs):
        self.calls.append(("get_role", kwargs))
        return {"Role": {"Arn": f"arn:aws:iam::339712964409:role/{kwargs['RoleName']}"}}


def _client_error(code: str, message: str):
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": message}}, "Op")


def _as_list(value):
    return value if isinstance(value, list) else [value]


# Read by `web/` but deliberately NOT provisioned, each for its own reason.
#
# `NODE_ENV` is set by the runtime. `X` is a placeholder inside a test fixture.
# `COGNITO_TEST_JWKS` swaps out a trust anchor and must never be deployed.
#
# `AWS_REGION` is the interesting one: `lib/cases.ts` reads it, and Amplify
# **refuses** to store any variable with the reserved `AWS` prefix (measured).
# The two guards genuinely collide, and the resolution is not a silent exclusion —
# `readEnv()` reads it as `source.AWS_REGION?.trim() || "us-east-1"`, the only
# variable in the app with a default rather than a throw, and Amplify's compute
# runtime resolves a region ambiently for the SDK. So it is *unprovisionable* and
# *not required*, which is why it is listed here with a test of its own
# (`test_the_region_is_the_one_variable_that_may_be_absent`) rather than dropped.
_NOT_PROVISIONED = {"NODE_ENV", "COGNITO_TEST_JWKS", "X", "AWS_REGION"}


def _env_reads_in_web() -> set[str]:
    """Every environment variable the app reads, read off disk.

    Scans `web/app`, `web/lib`, and `web/proxy.ts` — the request path. Test files
    are excluded: they read `COGNITO_TEST_JWKS` deliberately, and provisioning it
    is exactly what must not happen.

    **Three patterns, not one.** A `process.env.NAME` grep alone finds the Cognito
    variables and misses `GRACE_TABLE_NAME` entirely — `lib/env.ts` reads it
    *indirectly*, as `required(source, "GRACE_TABLE_NAME")` against an injected
    `EnvSource`, which is what makes that module testable. So the three most
    load-bearing variables in the app were invisible to the obvious grep, and a
    discovery test that cannot see them is worse than no discovery test.
    """
    import re

    patterns = (
        r"process\.env\.([A-Z_][A-Z0-9_]*)",
        r'required\(\s*\w+,\s*"([A-Z_][A-Z0-9_]*)"\s*\)',
        r"source\.([A-Z_][A-Z0-9_]*)",
    )
    root = pathlib.Path(__file__).resolve().parent.parent / "web"
    sources = [root / "proxy.ts"]
    for directory in ("app", "lib"):
        sources.extend(sorted((root / directory).rglob("*.ts")))
        sources.extend(sorted((root / directory).rglob("*.tsx")))
    names: set[str] = set()
    for source in sources:
        if not source.is_file():
            continue
        text = source.read_text()
        for pattern in patterns:
            names.update(re.findall(pattern, text))
    return names - _NOT_PROVISIONED


_COGNITO = {
    "pool_id": "us-east-1_HXs3b0APR",
    "client_id": "11ejmthb9mdrfkm5s2dm51jdiv",
    "domain": "https://grace-caseworkers.auth.us-east-1.amazoncognito.com",
    "issuer": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_HXs3b0APR",
}


def _provision(client, iam=None, **kwargs):
    return provision_amplify.provision(
        client=client,
        iam_client=iam or _FakeIam(),
        account_id="339712964409",
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:339712964409:runtime/grace_grace-oTyyvo8stE",
        cognito=dict(_COGNITO),
        update_cognito=False,
        **kwargs,
    )


def test_provision_creates_the_app_with_the_compute_role_attached():
    fake = _FakeAmplify()
    result = _provision(fake)
    created = dict(next(kw for name, kw in fake.calls if name == "create_app"))
    assert created["platform"] == provision_amplify.PLATFORM
    assert created["computeRoleArn"].endswith(provision_amplify.COMPUTE_ROLE_NAME)
    assert result["app_id"] == "abc123"


def test_provision_finds_an_existing_app_across_pages():
    """`list_apps` paginates and this account holds other projects' apps. A
    single-page read would create a *second* `grace-dashboard`, and the second
    one has a different app id — so `DASHBOARD_URL` and the Cognito callback URL
    would point at an app nobody is deploying to. Plan 2 hit the single-page
    version of this bug three times."""
    fake = _FakeAmplify(pages=[
        {"apps": [{"name": "someone-elses-app", "appId": "zzz"}]},
        {"apps": [{"name": provision_amplify.APP_NAME, "appId": "found99"}]},
    ])
    result = _provision(fake)
    assert result["app_id"] == "found99"
    assert not [name for name, _ in fake.calls if name == "create_app"]


def test_provision_does_not_stop_scanning_at_the_end_of_a_page():
    """The plan's draft `break`s out of the inner loop only, so a match on page
    one is not returned early *and* a later page can overwrite it. Here the Grace
    app is on page one and a decoy follows: a loop that keeps assigning would
    return the decoy."""
    fake = _FakeAmplify(pages=[
        {"apps": [{"name": provision_amplify.APP_NAME, "appId": "correct"}]},
        {"apps": [{"name": "grace-dashboard-old", "appId": "decoy"}]},
    ])
    assert _provision(fake)["app_id"] == "correct"


def test_provision_converges_an_existing_app_onto_the_intended_settings():
    """Re-running is the recovery path. Measured on a throwaway app: `UpdateApp`
    is a **patch**, not the full replace `UpdateUserPoolClient` turned out to be
    — a minimal `update_app(description=...)` left `platform`, `computeRoleArn`,
    `environmentVariables`, and `buildSpec` all intact. So a delta would be safe;
    sending the full intended state is still what makes the *converge* claim
    true, because a previous version's weaker setting would otherwise survive
    forever with nothing in the output saying so."""
    fake = _FakeAmplify(apps=[{"name": provision_amplify.APP_NAME, "appId": "abc123"}])
    _provision(fake)
    updates = [kw for name, kw in fake.calls if name == "update_app"]
    assert updates
    assert any(kw.get("platform") == provision_amplify.PLATFORM for kw in updates)
    assert any("computeRoleArn" in kw for kw in updates)
    assert any("buildSpec" in kw for kw in updates)


def test_provision_replaces_the_environment_variables_wholesale():
    """Measured: `update_app(environmentVariables={'A': 'changed'})` left the map
    as exactly `{'A': 'changed'}` — `B` was **dropped**. So the map is replaced,
    not merged, and a partial update silently deletes every variable it omits.
    Every call must therefore carry the complete set."""
    fake = _FakeAmplify(apps=[{"name": provision_amplify.APP_NAME, "appId": "abc123"}])
    _provision(fake)
    for _name, kwargs in fake.calls:
        if "environmentVariables" in kwargs:
            assert set(kwargs["environmentVariables"]) >= set(
                provision_amplify.RUNTIME_VARIABLES
            ), kwargs["environmentVariables"].keys()


def test_a_converge_preserves_console_written_variables_it_does_not_own():
    """**Measured on the live app, and the symptom pointed somewhere else.**

    `update_app(environmentVariables=...)` is a full replace, and the Amplify
    *console* writes keys of its own into that same map. A converge that sent a
    map rebuilt from a stale read deleted three keys nobody intended to touch —
    `AMPLIFY_MONOREPO_APP_ROOT`, `AMPLIFY_DIFF_DEPLOY`, and `_LIVE_UPDATES` — and
    the next build failed 59 seconds in, at clone time, before any phase ran:

        !!! CustomerError: Cannot read 'next' version in package.json.
            If you are using monorepo, please ensure that
            AMPLIFY_MONOREPO_APP_ROOT is set correctly.

    That reads like a repository or packaging problem, not like a variable someone
    removed, which is exactly why it needs a test rather than care.
    """
    fake = _FakeAmplify(
        apps=[{"name": provision_amplify.APP_NAME, "appId": "abc123"}],
        environment={
            "AMPLIFY_DIFF_DEPLOY": "false",
            "AMPLIFY_MONOREPO_APP_ROOT": "web",
            "_LIVE_UPDATES": '[{"name":"Node.js version","pkg":"node","type":"nvm","version":"22"}]',
            "GRACE_TABLE_NAME": "grace-cases",
        },
    )
    result = _provision(fake)
    sent = [kw for _n, kw in fake.calls if "environmentVariables" in kw][-1]
    env = sent["environmentVariables"]
    for preserved in ("AMPLIFY_DIFF_DEPLOY", "AMPLIFY_MONOREPO_APP_ROOT", "_LIVE_UPDATES"):
        assert preserved in env, preserved
    assert env["AMPLIFY_DIFF_DEPLOY"] == "false"
    assert "nvm" in env["_LIVE_UPDATES"]
    # And it says out loud what it carried forward.
    assert "AMPLIFY_DIFF_DEPLOY" in result["preserved"]
    assert "_LIVE_UPDATES" in result["preserved"]


def test_the_converge_reads_the_environment_fresh_before_merging():
    """A merge is only safe over a *current* read. `get_app` must be called on the
    converge path, and before the `update_app` that replaces the map — a merge over
    a remembered map reintroduces the deletion it exists to prevent."""
    fake = _FakeAmplify(
        apps=[{"name": provision_amplify.APP_NAME, "appId": "abc123"}],
        environment={"AMPLIFY_DIFF_DEPLOY": "false"},
    )
    _provision(fake)
    names = [name for name, _ in fake.calls]
    assert "get_app" in names
    assert names.index("get_app") < names.index("update_app")


def test_this_modules_values_win_over_a_stale_console_value():
    """The other direction. Preservation must not become "never change anything":
    a `GRACE_TABLE_NAME` left pointing at an old table has to be corrected, or the
    dashboard reads the wrong caseload."""
    fake = _FakeAmplify(
        apps=[{"name": provision_amplify.APP_NAME, "appId": "abc123"}],
        environment={"GRACE_TABLE_NAME": "some-old-table"},
    )
    _provision(fake)
    sent = [kw for _n, kw in fake.calls if "environmentVariables" in kw][-1]
    assert sent["environmentVariables"]["GRACE_TABLE_NAME"] == naming.TABLE


def test_a_custom_domain_is_not_overwritten_by_the_amplifyapp_hostname():
    """`DASHBOARD_URL` is what `/login` builds its OAuth `redirect_uri` from, so it
    must match both a registered Cognito callback and the host the caseworker is
    actually on. The live app has a custom domain (`grace.rosettacloud.app`);
    recomputing `DASHBOARD_URL` from `defaultDomain` would send every sign-in to
    the wrong origin while the app itself looked fine."""
    fake = _FakeAmplify(
        apps=[{"name": provision_amplify.APP_NAME, "appId": "abc123"}],
        environment={"DASHBOARD_URL": "https://grace.rosettacloud.app"},
    )
    result = _provision(fake)
    assert result["url"] == "https://grace.rosettacloud.app"
    sent = [kw for _n, kw in fake.calls if "environmentVariables" in kw][-1]
    assert sent["environmentVariables"]["DASHBOARD_URL"] == "https://grace.rosettacloud.app"
    # The amplifyapp hostname is still reported, so a callback can be registered
    # for it as a fallback.
    assert result["amplify_url"] == "https://main.abc123.amplifyapp.com"


def test_the_callback_urls_cover_the_custom_domain_the_amplify_host_and_localhost():
    """All three, because `UpdateUserPoolClient` is a full replace and a
    certificate problem on the custom domain must not leave the demo with no
    working URL. Duplicates are collapsed — the same URL twice is a validation
    error at Cognito, not a no-op."""
    urls = provision_amplify.callback_urls(
        "https://grace.rosettacloud.app", "https://main.abc123.amplifyapp.com"
    )
    assert urls == [
        "https://grace.rosettacloud.app/api/auth/callback",
        "https://main.abc123.amplifyapp.com/api/auth/callback",
        "http://localhost:3000/api/auth/callback",
    ]
    # Same value twice must not produce a duplicate entry.
    assert len(provision_amplify.callback_urls("https://x.example", "https://x.example")) == 2


def test_provision_sets_every_runtime_variable_to_a_non_empty_value():
    """The draft's `ENVIRONMENT_VARIABLES` carried `""` placeholders for five
    variables and filled four of them. `DASHBOARD_URL` stayed empty until a
    second `update_app` at the very end, so an interrupted run left the app
    with a blank one — and `/login` falls back to `http://localhost:3000`,
    silently sending every caseworker's sign-in redirect to their own machine."""
    fake = _FakeAmplify()
    _provision(fake)
    final = [kw for _n, kw in fake.calls if "environmentVariables" in kw][-1]
    for name in provision_amplify.RUNTIME_VARIABLES:
        assert final["environmentVariables"].get(name, "").strip(), name


def test_provision_never_creates_a_branch_before_the_repository_is_connected():
    """**Measured, and it inverts the draft's ordering.** A manually created
    branch *blocks* the repository connection:

        0 branches  -> UpdateApp(repository=) : "You should at least provide one valid token"
        1 branch    -> UpdateApp(repository=) : "Cannot connect your app to
                       repository while manually deployed branch still exists.
                       Please delete all branches and try again."

    So the draft's `create_branch` would have made the browser step — the one
    thing this task ends at — impossible without first deleting the branch it had
    just created. The branch is created by Amplify when the repository is
    connected, and `provision` must leave that alone.
    """
    fake = _FakeAmplify()
    _provision(fake)
    assert not [name for name, _ in fake.calls if name == "create_branch"]


def test_provision_converges_a_branch_that_already_exists():
    """Once the operator has connected the repository, Amplify owns the branch —
    and a re-run must still apply the branch-level settings rather than skipping
    them because creation is not needed. `framework` is what makes Amplify treat
    the branch as Next.js SSR."""
    fake = _FakeAmplify(apps=[{"name": provision_amplify.APP_NAME, "appId": "abc123"}])
    fake.branches.add(provision_amplify.BRANCH)
    _provision(fake)
    updates = [kw for name, kw in fake.calls if name == "update_branch"]
    assert updates, "an existing branch was left unconverged"
    assert updates[0]["framework"] == provision_amplify.FRAMEWORK
    assert updates[0]["computeRoleArn"].endswith(provision_amplify.COMPUTE_ROLE_NAME)


def test_a_rejected_compute_role_is_never_swallowed():
    """**The draft's real defect.** Its `create_branch` wrapper reads

        except ClientError as exc:
            if exc.response["Error"]["Code"] != "BadRequestException":
                raise

    and a rejected compute role raises **exactly** `BadRequestException` — the
    same code as "the branch already exists". Measured on a throwaway app, all
    three ways:

        CreateApp   (bad role) -> BadRequestException: The compute role provided
                                  cannot be assumed by Amplify.
        CreateBranch(bad role) -> BadRequestException: (identical message)
        UpdateApp   (bad role) -> BadRequestException: (identical message)

    So that `except` would have reported success with no credentials in the SSR
    runtime — every DynamoDB read failing AccessDenied after a green deploy, and
    the queue page empty. Plan 2's point-in-time-recovery finding, exactly: a
    provisioning script that swallows an error reports success while the control
    is absent.
    """
    fake = _FakeAmplify(fail_on="create_app")
    try:
        _provision(fake)
    except Exception as exc:  # noqa: BLE001 - the point is that it escapes
        assert "compute role" in str(exc)
    else:
        raise AssertionError("a rejected compute role was swallowed")


def test_only_an_already_exists_message_is_tolerated_on_a_branch():
    """Discriminating on the *message* rather than the code, because the code
    cannot tell the two apart. An unrecognised `BadRequestException` must
    propagate."""
    assert provision_amplify.is_already_exists(
        _client_error("BadRequestException", "Failed to create branch. The branch main already exists for the app d29")
    )
    assert not provision_amplify.is_already_exists(
        _client_error("BadRequestException", "The compute role provided cannot be assumed by Amplify.")
    )


def test_provision_creates_the_compute_role_before_the_app_that_references_it():
    """`CreateApp` validates the role by attempting to assume it, so a role that
    does not exist yet is refused with the same message a wrong-principal role
    gets. Order is the property.

    **The fake refuses like the real service.** An earlier version merely checked
    `iam.calls[0][0] == "create_role"`, which is true even when the app is created
    first, because the two clients record separately. It scored SURVIVED against a
    reordering sabotage. `_FakeAmplify` now consults the role's existence, so the
    ordering is enforced by a failure rather than observed by a bystander — Plan
    2's "a test fake must be able to fail the way the real service fails".
    """
    iam = _FakeIam()
    fake = _FakeAmplify(known_roles=iam.existing)
    _provision(fake, iam=iam)
    assert iam.calls, "no role was created"
    assert iam.calls[0][0] == "create_role"


def test_provision_converges_the_role_of_a_previous_run():
    """A role created before the source-account condition existed must not keep
    its weaker trust policy. Same reasoning as `provision_iam`'s
    `update_assume_role_policy` on the already-exists path."""
    iam = _FakeIam(existing={provision_amplify.COMPUTE_ROLE_NAME})
    _provision(_FakeAmplify(), iam=iam)
    names = [name for name, _ in iam.calls]
    assert "update_assume_role_policy" in names
    assert "put_role_policy" in names


def test_provision_never_starts_a_build():
    """`StartJob` is refused on an app with no repository — measured:
    `BadRequestException: Operation not supported for app that was not connected
    to a repository provider.` More importantly, a build must not be triggered by
    a provisioning script: the operator connects the repository in the console,
    which starts the first build itself. A script that also called `StartJob`
    would race it."""
    fake = _FakeAmplify()
    _provision(fake)
    assert not [name for name, _ in fake.calls if name == "start_job"]


def test_provision_reports_the_remaining_manual_step():
    """The task genuinely cannot finish unattended, so the return value says so
    rather than exiting 0 as though it had. A script that looks complete when it
    is not is how the browser step gets forgotten."""
    result = _provision(_FakeAmplify())
    assert result["repository_connected"] is False
    assert "console" in result["next_step"].lower()


def test_repository_connected_is_read_not_inferred_from_a_branch():
    """**Found by running it for real.** An earlier version returned
    `repository_connected: branch_converged`, and on a live app that had a branch
    it printed `repository_connected: True` — a claim it had no evidence for.

    The two are genuinely different states, and the difference matters: measured,
    `CreateBranch` **succeeds** on an app with no repository, and such a branch
    then *blocks* the connection outright (`Cannot connect your app to repository
    while manually deployed branch still exists`). So "a branch exists" can mean
    the opposite of "the repository is connected". Reporting success from the
    weaker signal is the unconfirmed-success claim hard rule 6 forbids, aimed at
    the operator who still has to do the manual step.
    """
    fake = _FakeAmplify(apps=[{"name": provision_amplify.APP_NAME, "appId": "abc123"}])
    fake.branches.add(provision_amplify.BRANCH)  # a branch, but no repository
    result = _provision(fake)
    assert result["branch_converged"] is True
    assert result["repository_connected"] is False
    assert result["repository"] == ""
    assert "console" in result["next_step"].lower()


def test_a_connected_repository_is_reported_as_connected():
    """The other half. Without this the test above is satisfied by a function that
    always reports `False`, which would be equally useless."""
    fake = _FakeAmplify(
        apps=[{"name": provision_amplify.APP_NAME, "appId": "abc123"}],
        repository="https://github.com/mohamedsorour1998/Grace",
    )
    result = _provision(fake)
    assert result["repository_connected"] is True
    assert result["repository"].endswith("/Grace")


def test_an_unexpected_error_reading_the_branch_is_not_swallowed():
    """A bare `except ClientError` around `get_branch` would treat an AccessDenied
    or a throttle as "no branch yet" and report `branch_converged: False` — which
    reads as "waiting for the browser step". The operator would then go and repeat
    a step already done while the branch silently kept a stale `framework` or no
    compute role. Only a genuine not-found may be tolerated."""
    fake = _FakeAmplify(apps=[{"name": provision_amplify.APP_NAME, "appId": "abc123"}])
    fake.branch_error = _client_error("AccessDeniedException", "not allowed")
    try:
        _provision(fake)
    except Exception as exc:  # noqa: BLE001 - escaping is the property
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("an AccessDenied on get_branch was swallowed")


def test_a_missing_branch_is_tolerated():
    """The other half, or the test above is satisfied by a function that always
    raises. A not-found genuinely means the operator has not connected the
    repository yet, and that is the expected state before the browser step."""
    fake = _FakeAmplify(apps=[{"name": provision_amplify.APP_NAME, "appId": "abc123"}])
    result = _provision(fake)
    assert result["branch_converged"] is False


def test_the_url_is_built_from_the_apps_own_default_domain():
    """`https://{branch}.{appId}.amplifyapp.com` is the draft's guess. The app's
    own `defaultDomain` is authoritative and is returned by `create_app` and
    `get_app` — measured: `dnf794h1quh1w.amplifyapp.com`. They agree today; a
    guessed hostname that stops agreeing would break the Cognito callback URL
    and the sign-in redirect together, and both fail closed in a way that reads
    as "auth is broken"."""
    fake = _FakeAmplify()
    result = _provision(fake)
    assert result["url"] == f"https://{provision_amplify.BRANCH}.abc123.amplifyapp.com"


def test_the_callback_urls_keep_localhost_alongside_the_deployed_one():
    """Cognito's callback list is a full replace (`UpdateUserPoolClient`), so
    passing only the deployed URL would break local development silently — and
    local development is how every remaining task is verified."""
    urls = provision_amplify.callback_urls("https://main.abc123.amplifyapp.com")
    assert any("localhost:3000" in u for u in urls)
    assert any("main.abc123.amplifyapp.com" in u for u in urls)
    assert all(u.endswith("/api/auth/callback") for u in urls)


def test_auto_branch_creation_and_pr_previews_stay_off():
    """A public repository with auto-branch creation would build any branch
    anyone opened — against the app-level compute role, which can write decision
    rows. App-level role attachment is only safe because these are off."""
    fake = _FakeAmplify()
    _provision(fake)
    created = dict(next(kw for name, kw in fake.calls if name == "create_app"))
    assert created["enableAutoBranchCreation"] is False
    assert created["enableBranchAutoDeletion"] is False


def test_basic_auth_is_not_used_as_the_gate():
    """Amplify's basic auth would put a shared password in front of the app and
    `basicAuthCredentials` into the API. The gate is Cognito plus
    `verifySession`; a second, weaker one invites someone to rely on it."""
    fake = _FakeAmplify()
    _provision(fake)
    created = dict(next(kw for name, kw in fake.calls if name == "create_app"))
    assert created.get("enableBasicAuth", False) is False
    assert "basicAuthCredentials" not in created


def test_the_cache_key_excludes_cookies():
    """`AMPLIFY_MANAGED_NO_COOKIES` "excludes all cookies from the cache key".
    The session is a cookie, so under that setting two different caseworkers'
    requests share a cache key — and a cached SSR page for one household could be
    served to another session. `AMPLIFY_MANAGED` keeps cookies in the key.
    It is also the service default, so this must be set explicitly."""
    fake = _FakeAmplify()
    _provision(fake)
    created = dict(next(kw for name, kw in fake.calls if name == "create_app"))
    assert created["cacheConfig"] == {"type": "AMPLIFY_MANAGED"}


def test_the_app_is_tagged_for_cost_and_teardown():
    fake = _FakeAmplify()
    _provision(fake)
    created = dict(next(kw for name, kw in fake.calls if name == "create_app"))
    assert created["tags"] == naming.TAGS


def test_an_app_that_already_existed_is_tagged_too():
    """**Found by running it for real.** `UpdateApp` has **no `tags` parameter**
    (checked against the live API model), so `create_app(tags=...)` covers only
    the path where this script creates the app. The first real run met an app the
    operator had just created in the console while connecting the repository, and
    `list-tags-for-resource` on it returned `{}` — untagged, while the script
    exited 0 looking as though it had tagged everything.

    Tags are what make Grace's spend separable in Cost Explorer against a $50
    credit budget and what lets `teardown` identify what it owns, so an untagged
    app is a resource nobody can attribute or reliably clean up. `TagResource` is
    a separate call and must run on **both** paths.
    """
    for label, apps in (
        ("fresh", None),
        ("existing", [{"name": provision_amplify.APP_NAME, "appId": "abc123"}]),
    ):
        fake = _FakeAmplify(apps=apps)
        _provision(fake)
        tagged = [kw for name, kw in fake.calls if name == "tag_resource"]
        assert tagged, f"{label}: nothing called tag_resource"
        assert tagged[0]["tags"] == naming.TAGS, label
        assert tagged[0]["resourceArn"].endswith("apps/abc123"), label


def test_no_household_identity_appears_anywhere_in_this_module():
    """Hard rule 9 at the last surface. Amplify environment variables are visible
    in the console, in build logs, and inside a downloadable artifact."""
    import inspect

    source = inspect.getsource(provision_amplify)
    names = (
        "Mensah|Rivera|Okonkwo|Fitzgerald|Yamamoto|Nakamura|Delacroix|"
        "Abubakar|Silva|Petrov|Haddad|Nguyen"
    ).split("|")
    for name in names:
        assert name not in source, name
    assert "+1555" not in source


def test_the_policy_documents_are_json_serialisable():
    """They are handed to `put_role_policy` as `json.dumps` output; a set or a
    tuple in there raises at provisioning time."""
    json.dumps(provision_amplify.compute_policy("339712964409"))
    json.dumps(provision_amplify.compute_trust_policy("339712964409"))
