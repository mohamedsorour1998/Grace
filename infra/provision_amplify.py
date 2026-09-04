"""The Amplify app that hosts the caseworker dashboard. Idempotent.

Four things here are load-bearing, and each of them guards a failure that
produces a **green build and a wrong app** rather than an error.

**1. `WEB_COMPUTE`, not `WEB`.** The live enum is
`['WEB', 'WEB_DYNAMIC', 'WEB_COMPUTE']`, and `WEB` is a static host: no route
handlers, no middleware. Deploying onto it would remove the Cognito gate and the
decide endpoint while still building and serving successfully.

**2. The SSR compute role is what makes every read work.** Amplify's compute
functions get their AWS credentials from `computeRoleArn` and from nothing else.
With no role there are no credentials in the SSR runtime, so `lib/cases.ts`'s
DynamoDB calls and `lib/decide.ts`'s `InvokeAgentRuntime` fail AccessDenied
*after* a successful deploy. Probed both ways on 2026-09-04: a role trusting
`lambda.amazonaws.com` is refused at `CreateApp` with `BadRequestException: The
compute role provided cannot be assumed by Amplify.`, and the identical call with
`amplify.amazonaws.com` is accepted and echoes `computeRoleArn` back. Both halves
mattered — a refusal alone would not prove the correct principal works, and an
acceptance alone would not prove the parameter is validated rather than ignored.

**3. Amplify environment variables reach the BUILD only, never the request.**
AWS documents this as deliberate: "a Next.js server component doesn't have access
to those environment variables by default. This behavior is intentional to
protect any secrets stored in environment variables". The documented remedy is to
write them into `.env.production` during the build, which Next loads at request
time — and that is verified here rather than taken on faith: a real `next build`
leaves `required-server-files.json` reporting `config.env: {}`, the values appear
in **no** `.next` chunk, and `next start` picks up a value **edited after the
build finished**. So the file is read at request time and nothing is baked in.
Without that build step, `readEnv()` throws on every request, `readCase` catches,
and all twelve households render as an empty caseload with nothing logged.

**4. `provision` deliberately does NOT create the branch.** Measured: a manually
created branch *blocks* the repository connection with `Cannot connect your app
to repository while manually deployed branch still exists. Please delete all
branches and try again.` Connecting the repository is the one step that must
happen in a browser, so creating a branch here would make the very last step
impossible. Amplify creates the branch itself when the repository is connected;
this module converges its settings on a later run.

Repo-connected deploys need a GitHub app authorization performed in a browser,
which cannot be done unattended. `CreateDeployment`/`StartDeployment` accept a zip
instead — but a manual deployment does **not** build: it deploys pre-built
artifacts in the `.amplify-hosting/` layout (`static/`, `compute/default/` on port
3000, `deploy-manifest.json`), which Next.js does not emit. So Grace connects the
repository, which is the supported SSR path and the only one that runs the
buildspec's tests. `provision` reports `repository_connected: False` rather than
exiting as though it had finished.
"""

from __future__ import annotations

import json

import boto3
from botocore.exceptions import ClientError

from infra import naming

APP_NAME = "grace-dashboard"
BRANCH = "main"
PLATFORM = "WEB_COMPUTE"

# The subdirectory holding the Next.js app. Amplify needs this in two places that
# must agree — the buildspec's `appRoot` and the `AMPLIFY_MONOREPO_APP_ROOT`
# environment variable — and AWS's docs say the variable "must exist, and have the
# same value as" the key. One constant so they cannot drift.
APP_ROOT = "web"

# Amplify stores this per branch and uses it to decide how to host the artifact.
# An arbitrary string is accepted (measured: `Nonsense - NotAFramework` was
# stored verbatim), so this is not validated for us — it has to be right.
FRAMEWORK = "Next.js - SSR"

COMPUTE_ROLE_NAME = "grace-amplify-compute-role"
COMPUTE_POLICY_NAME = "grace-amplify-compute-policy"

# Pinned rather than inherited. AWS's own troubleshooting page contradicts itself
# in consecutive sentences about the AL2023 image's default ("Node.js version 20
# is supported by default" / "with a default Node.js version of 22"), and the
# *build* Node version also selects the SSR *runtime* version. Next 16.3.4
# declares `engines.node: ">=20.9.0"`. Supported runtimes are 20, 22, and 24 —
# 18 is blocked outright for SSR apps, which is why the plan's warning about the
# image shipping Node 18 matters even though the current image does not.
NODE_VERSION = "22"

# Where the buildspec writes the request-time variables. Inside `web/` and
# **outside** `web/.next`, which is the artifact directory: verified that a real
# build leaves the file in `web/` and copies nothing into `.next`.
#
# Two spellings, because the build has two frames of reference and conflating them
# is how the file ends up in the wrong place. `RUNTIME_ENV_FILE` is repo-relative,
# which is what `baseDirectory` and any artifact-containment check are written in.
# `RUNTIME_ENV_FILE_IN_WEB` is what the *commands* use, because they run after
# `cd web` — a command naming `web/.env.production` from inside `web/` would
# create `web/web/.env.production`, which Next would never load, on a green build.
RUNTIME_ENV_FILE = "web/.env.production"
RUNTIME_ENV_FILE_IN_WEB = RUNTIME_ENV_FILE.removeprefix("web/")

# Every variable `web/` reads from `process.env` at request time. Grepped from
# `web/app`, `web/lib`, and `web/proxy.ts` rather than remembered — `NODE_ENV` is
# Node's own and `COGNITO_TEST_JWKS` is a test-only injection point that must
# never be set on a deployed app.
#
# **No `AWS_REGION`.** Amplify refuses the entire `AWS` prefix — measured:
# `BadRequestException: Environment variables cannot start with the reserved
# prefix "AWS".` for `AWS_REGION`, `AWS_DEFAULT_REGION`, and `AWS_ACCESS_KEY_ID`
# alike. The plan's draft listed `AWS_REGION`, which would have made `provision`
# fail outright. `readEnv()` already defaults `region` to `us-east-1` on absence,
# and Amplify's compute runtime resolves a region ambiently, so nothing needs it.
RUNTIME_VARIABLES = (
    "GRACE_TABLE_NAME",
    "GRACE_ESCALATION_INDEX",
    "GRACE_RUNTIME_ARN",
    "COGNITO_ISSUER",
    "COGNITO_CLIENT_ID",
    "COGNITO_DOMAIN",
    "DASHBOARD_URL",
)

# Resource names only. Amplify environment variables are visible in the console,
# in build logs, and — because the buildspec writes them into `.env.production` —
# inside a build artifact anyone who can call `get-job` may download. A credential
# here would be a credential in three places at once. The compute role supplies
# AWS access; these only say which resources.
#
# The two known-at-import-time values are set here; the five that depend on
# deployed resources are filled by `environment_variables()`. Nothing is left as
# an empty string in a shipped map: the draft's `""` placeholders meant an
# interrupted run could leave `DASHBOARD_URL` blank, and `/login` then falls back
# to `http://localhost:3000` — silently sending every caseworker's sign-in
# redirect to their own machine.
ENVIRONMENT_VARIABLES: dict[str, str] = {
    # Must equal the buildspec's `appRoot`. AWS: "This key must exist, and have
    # the same value as the `AMPLIFY_MONOREPO_APP_ROOT` environment variable."
    # Not optional here — the repository has no root `package.json`, so without
    # this the build dies at clone time with `Cannot read 'next' version in
    # package.json` (measured, job 2).
    "AMPLIFY_MONOREPO_APP_ROOT": APP_ROOT,
    "GRACE_TABLE_NAME": naming.TABLE,
    "GRACE_ESCALATION_INDEX": naming.ESCALATION_GSI,
    "GRACE_RUNTIME_ARN": "",
    "COGNITO_ISSUER": "",
    "COGNITO_CLIENT_ID": "",
    "COGNITO_DOMAIN": "",
    "DASHBOARD_URL": "",
}

# Keys this module does not own and must never delete. The Amplify console writes
# its own into the *same* map `update_app` replaces wholesale, so a converge that
# sends a rebuilt map silently removes them.
#
# **Measured on the live app, twice.** A map rebuilt from a stale read dropped
# `AMPLIFY_MONOREPO_APP_ROOT`, `AMPLIFY_DIFF_DEPLOY`, and `_LIVE_UPDATES`, and the
# next build failed 59 seconds in, at clone time, before any phase ran — reporting
# `Cannot read 'next' version in package.json`, which reads like a repository or
# packaging fault rather than like a variable somebody removed. That gap between
# the symptom and the cause is what makes this worth a named constant.
PRESERVED_PREFIXES = ("AMPLIFY_", "_LIVE_UPDATES")


def environment_variables(
    runtime_arn: str, cognito: dict, dashboard_url: str
) -> dict[str, str]:
    """The variables this module owns, with every value non-empty.

    Not the *complete* map that reaches `update_app` — see `merged_environment`.
    """
    return {
        **ENVIRONMENT_VARIABLES,
        "GRACE_RUNTIME_ARN": runtime_arn,
        "COGNITO_ISSUER": cognito["issuer"],
        "COGNITO_CLIENT_ID": cognito["client_id"],
        "COGNITO_DOMAIN": cognito["domain"],
        "DASHBOARD_URL": dashboard_url,
    }


def merged_environment(
    existing: dict[str, str] | None,
    runtime_arn: str,
    cognito: dict,
    dashboard_url: str,
) -> dict[str, str]:
    """What actually reaches `update_app`: this module's variables **merged over a
    fresh read**, never a rebuilt map.

    `update_app(environmentVariables=...)` is a **full replace** of the entire map
    — measured twice. On a throwaway app, sending `{"A": "changed"}` left the map
    as exactly that and dropped `B`. On the *live* app, sending a map rebuilt from
    a stale read deleted `AMPLIFY_MONOREPO_APP_ROOT`, `AMPLIFY_DIFF_DEPLOY`, and
    `_LIVE_UPDATES` — keys the **console** had written, which this module neither
    set nor knew about. The next build then failed at clone time reporting
    `Cannot read 'next' version in package.json`, a message that points at the
    repository rather than at the deletion that caused it.

    So the read must be fresh and the merge must be one-directional: existing keys
    survive, this module's keys win where they overlap. That also means the
    ordering matters — `existing` first, ours second.

    A blank value is treated as absent for the keys we own, so an interrupted
    earlier run cannot pin `DASHBOARD_URL` to `""` forever.
    """
    merged = {k: v for k, v in (existing or {}).items() if v is not None}
    ours = environment_variables(runtime_arn, cognito, dashboard_url)
    merged.update({k: v for k, v in ours.items() if str(v).strip()})
    return merged


def unowned_keys(existing: dict[str, str] | None) -> set[str]:
    """Keys present on the app that this module does not own — reported so a
    converge says out loud what it is carrying forward rather than silently
    depending on it."""
    return {
        key
        for key in (existing or {})
        if key not in ENVIRONMENT_VARIABLES
        and any(key.startswith(prefix) for prefix in PRESERVED_PREFIXES)
    }


def build_spec_commands() -> list[str]:
    """Every command the build runs, in order — so a test can assert both that
    the tests are among them and that they come *before* the build.

    **No `cd web`.** This is the `applications:`/`appRoot:` monorepo form, where
    Amplify enters the app root itself — and that was forced by two real build
    failures rather than chosen for elegance:

        job 2  AMPLIFY_MONOREPO_APP_ROOT absent, top-level `frontend:`
               -> CustomerError: Cannot read 'next' version in package.json.
                  If you are using monorepo, please ensure that
                  AMPLIFY_MONOREPO_APP_ROOT is set correctly.
        job 3  AMPLIFY_MONOREPO_APP_ROOT = web, top-level `frontend:`
               -> CustomerError: Monorepo spec provided without "applications" key

    Both failed **at clone time, about one second in, before any phase ran**, so
    no command in this list had a chance to matter. The repository has **no root
    `package.json`** — `web/` holds the only one — so Amplify cannot find Next at
    the repository root and the monorepo variable is not optional; and once that
    variable is set, a buildspec without an `applications:` key is refused
    outright. The two constraints together leave exactly one legal shape.

    So the documented `cd web` form, whose cross-phase `cd` persistence AWS
    states explicitly, is simply not available to this repository. Its
    `baseDirectory` resolution was the undocumented part anyway.
    """
    env_file = RUNTIME_ENV_FILE_IN_WEB
    return [
        # Node before anything that uses Node. `nvm use` in preBuild is
        # documented to run *after* live package updates and to override them.
        f"nvm use {NODE_VERSION}",
        "npm ci",
        # Truncate before appending. Amplify caches `node_modules` between
        # builds and `>>` appends, so a surviving file would leave two values
        # for one variable — and dotenv keeps the first, so a rotated Cognito
        # client id would be ignored on every rebuild while the console showed
        # the new one.
        f"rm -f {env_file}",
        # The build-to-runtime bridge. Each variable named explicitly rather
        # than `env | grep GRACE_`: a prefix grep would also capture any
        # `GRACE_`-prefixed build variable someone adds later, and `env` in an
        # Amplify build carries values this app has no business writing into an
        # artifact.
        *[
            f'echo "{name}=${{{name}}}" >> {env_file}'
            for name in RUNTIME_VARIABLES
        ],
        # Tests before the build, deliberately: a deploy that skips them can
        # ship a broken authorisation gate, and this app can file benefit
        # renewals. `npm run test` after `npm run build` would still satisfy a
        # substring check while the artifact already existed.
        "npm run typecheck",
        "npm run lint",
        "npm run test",
        "npm run build",
    ]


def build_spec() -> str:
    """Amplify's buildspec, as YAML — the `applications:`/`appRoot:` monorepo
    form, which two live build failures proved is the only legal one here (see
    `build_spec_commands`).

    Built from `build_spec_commands()` rather than written twice, so the string
    Amplify runs and the list the tests assert against cannot drift.

    **`baseDirectory` is `.next`, not `web/.next`.** AWS documents it as relative
    to `buildPath`, which defaults to `appRoot` when omitted — so with
    `appRoot: web` and no `buildPath`, `.next` already resolves to `web/.next`.
    Naming `web/.next` here would resolve to `web/web/.next` and fail looking for
    `required-server-files.json`.
    """
    commands = build_spec_commands()
    install_ends = commands.index("npm ci") + 1
    pre, rest = commands[:install_ends], commands[install_ends:]

    def block(items: list[str]) -> str:
        return "\n".join(f"{' ' * 12}- {_quote(c)}" for c in items)

    return f"""version: 1
applications:
  - appRoot: {APP_ROOT}
    frontend:
      phases:
        preBuild:
          commands:
{block(pre)}
        build:
          commands:
{block(rest)}
      artifacts:
        baseDirectory: .next
        files:
          - '**/*'
      cache:
        paths:
          - node_modules/**/*
"""


def _quote(command: str) -> str:
    """YAML-quote a command that contains a colon or a leading brace.

    `echo "X=${{X}}"` is safe unquoted; a bare `-` item starting with a quote is
    not, because YAML would read it as a quoted scalar and choke on the rest.
    Single-quoting is enough here and keeps `${VAR}` literal for the shell.
    """
    if command.startswith(('"', "'", "{", "[")) or ": " in command:
        return "'" + command.replace("'", "''") + "'"
    return command


def callback_urls(*dashboard_urls: str) -> list[str]:
    """The Cognito callback list: every dashboard origin, plus localhost.

    `UpdateUserPoolClient` is a full replace (Task 4 measured this), so passing
    only one URL would break the others silently — and local development is how
    every remaining verification step is done.

    Both the custom domain **and** the amplifyapp hostname are registered, so a
    certificate problem on the custom domain cannot leave the demo with no working
    URL. Duplicates are collapsed while preserving order, because the same URL
    reaching Cognito twice is a validation error rather than a no-op.
    """
    seen: dict[str, None] = {}
    for base in (*dashboard_urls, "http://localhost:3000"):
        if base and base.strip():
            seen.setdefault(f"{base.strip().rstrip('/')}/api/auth/callback", None)
    return list(seen)


def compute_trust_policy(account_id: str) -> dict:
    """Who may assume the SSR compute role.

    `amplify.amazonaws.com` and nothing else — probed both ways (see the module
    docstring). The `aws:SourceAccount` condition is a confused-deputy guard and
    is genuinely evaluated rather than decorative, probed both halves against
    `CreateApp`: a wrong account value was **refused** with the same "cannot be
    assumed by Amplify" message, and the identical call with this account was
    accepted. The refusal is what distinguishes "condition satisfied" from
    "condition ignored" — the same discipline Plan 2 applied to Lambda's trust
    policy, where a condition on an unpopulated key would have made the role
    permanently unassumable.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "amplify.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": account_id}},
            }
        ],
    }


def compute_policy(account_id: str) -> dict:
    """What the SSR runtime may do. Three actions on one table, one index, and one
    runtime.

    **The index needs its own ARN.** Measured with `simulate-principal-policy`,
    both ways:

        grant [table]         -> Query table: allowed   Query index: implicitDeny
        grant [table, index]  -> Query table: allowed   Query index: allowed

    `readCase` reads the table; `listQueue` reads the index. A table-only grant
    therefore leaves `/case/c-010` rendering perfectly while `/queue` — the one
    page the product exists for — comes back **empty**, on a green deploy, with
    nothing visible outside a server log. `listQueue` is the only function that
    touches the index, so this is the single permission whose absence is invisible
    to every other page.

    The index is named rather than wildcarded: `index/*` would also reach any
    index a future task adds.

    **No `Scan`, no `DeleteItem`, no `UpdateItem`.** `lib/cases.ts` queries, so
    `Scan` would let a bug read every ledger row for all twelve households in one
    call. The ledger is the audit trail for every autonomous benefits decision,
    and a dashboard that can delete or rewrite a row can destroy the
    `renewal_submitted` evidence hard rule 6 depends on. Append and read only.
    """
    region = naming.REGION
    table = f"arn:aws:dynamodb:{region}:{account_id}:table/{naming.TABLE}"
    directory = (
        f"arn:aws:bedrock-agentcore:{region}:{account_id}:"
        "workload-identity-directory/default"
    )
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                # Appendix D.1, carried to the fourth role that can now reach
                # AgentCore. `GetWorkloadAccessTokenForUserId` performs no
                # verification of the user id it is handed, so it would let an
                # authenticated caseworker obtain a token scoped to any
                # household. Nothing below grants it — the Deny exists so that
                # copying AWS's own example execution policy in later stays
                # harmless, which is the realistic way the unsafe path returns.
                "Sid": "DenyUnverifiedUserIdPath",
                "Effect": "Deny",
                "Action": "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                "Resource": [directory, f"{directory}/workload-identity/*"],
            },
            {
                "Sid": "ReadTheCaseloadAndTheQueue",
                "Effect": "Allow",
                "Action": ["dynamodb:Query", "dynamodb:GetItem"],
                "Resource": [table, f"{table}/index/{naming.ESCALATION_GSI}"],
            },
            {
                "Sid": "WriteTheCaseworkersDecisionRow",
                "Effect": "Allow",
                # The decision row and the outcome row, both `PutItem` on the
                # table itself. No index ARN: a GSI is not written directly.
                "Action": "dynamodb:PutItem",
                "Resource": table,
            },
            {
                "Sid": "ReinvokeTheGraceRuntimeOnly",
                "Effect": "Allow",
                # The decide route re-invokes so the gate re-evaluates; it never
                # resumes a paused graph. Scoped to Grace's own runtime, so a
                # compromised dashboard cannot drive the other 15 runtimes in
                # this account. Both the bare ARN and the `/*` qualifier form,
                # matching `provision_iam`'s Lambda statement.
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:"
                    f"runtime/{naming.RUNTIME}*",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:"
                    f"runtime/{naming.RUNTIME}*/*",
                ],
            },
        ],
    }


def is_already_exists(exc: ClientError) -> bool:
    """Whether a `ClientError` means "the branch is already there".

    **Discriminates on the message, because the code cannot.** Measured on a
    throwaway app, three separate failures all raise `BadRequestException`:

        CreateBranch, duplicate  -> "Failed to create branch. The branch main
                                     already exists for the app d29…"
        CreateBranch, bad role    -> "The compute role provided cannot be assumed
                                     by Amplify."
        CreateApp,   bad role     -> (identical message)

    So the plan's draft `if code != "BadRequestException": raise` would have
    swallowed a rejected compute role and reported success with no credentials in
    the SSR runtime — Plan 2's point-in-time-recovery finding exactly: a
    provisioning script that swallows an error reports success while the control
    is absent. Anything unrecognised propagates.
    """
    error = exc.response.get("Error", {})
    if error.get("Code") != "BadRequestException":
        return False
    return "already exists" in str(error.get("Message", ""))


def _find_app(client) -> str | None:
    """The Grace app's id, or None.

    **Paginates, and scans every page to the end.** This account holds other
    projects' resources, and a single-page read would create a *second*
    `grace-dashboard` — with a different app id, so `DASHBOARD_URL` and the
    Cognito callback URL would both point at an app nobody is deploying to. Plan
    2 hit the single-page version of this bug three times. The first match wins
    and later pages cannot overwrite it.
    """
    for page in client.get_paginator("list_apps").paginate():
        for app in page.get("apps", []):
            if app.get("name") == APP_NAME:
                return str(app["appId"])
    return None


def _compute_role_arn(iam_client, account_id: str) -> str:
    """Create or converge the SSR compute role, and return its ARN.

    Created **before** the app that references it: `CreateApp` validates the role
    by attempting to assume it, and a role that does not exist yet is refused
    with the same message a wrong-principal role gets.

    Raising is correct here, as in `provision_iam`: this is a provisioning script,
    not the request path, so a loud failure blocks a deploy and the operator
    re-runs — which is what idempotence exists for.
    """
    trust = json.dumps(compute_trust_policy(account_id))
    try:
        iam_client.create_role(
            RoleName=COMPUTE_ROLE_NAME,
            AssumeRolePolicyDocument=trust,
            Description="Grace dashboard SSR compute role",
            Tags=[{"Key": k, "Value": v} for k, v in naming.TAGS.items()],
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        # Converge: a role created before the source-account condition existed
        # must not keep its weaker trust policy forever with nothing saying so.
        iam_client.update_assume_role_policy(
            RoleName=COMPUTE_ROLE_NAME, PolicyDocument=trust
        )
    # `put_role_policy` overwrites, so this is also how a tightened policy reaches
    # a role that already existed: re-run the script, do not delete the role.
    iam_client.put_role_policy(
        RoleName=COMPUTE_ROLE_NAME,
        PolicyName=COMPUTE_POLICY_NAME,
        PolicyDocument=json.dumps(compute_policy(account_id)),
    )
    return str(iam_client.get_role(RoleName=COMPUTE_ROLE_NAME)["Role"]["Arn"])


def provision(
    client=None,
    iam_client=None,
    runtime_arn: str | None = None,
    cognito: dict | None = None,
    account_id: str | None = None,
    update_cognito: bool = True,
) -> dict:
    """Create or converge the Amplify app and its compute role. Idempotent.

    Everything except connecting the repository, which needs a browser. Returns
    `repository_connected: False` and the remaining step, rather than exiting 0 as
    though the deploy were done.
    """
    client = client or boto3.client("amplify", region_name=naming.REGION)
    iam_client = iam_client or boto3.client("iam")
    if account_id is None:
        account_id = boto3.client("sts").get_caller_identity()["Account"]

    if runtime_arn is None:
        # Reuse `provision_all.runtime_arn` rather than a second paginated
        # lookup. It already refuses a runtime that is not READY, and a
        # duplicated implementation would be free to drift — the same reason
        # `read.py` imports `_most_recent` from `authority.py` instead of
        # reimplementing it.
        from infra import provision_all

        runtime_arn = provision_all.runtime_arn()

    if cognito is None:
        from infra import provision_cognito

        cognito = provision_cognito.provision()

    role_arn = _compute_role_arn(iam_client, account_id)
    app_id = _find_app(client)

    # `AMPLIFY_MANAGED`, not the `AMPLIFY_MANAGED_NO_COOKIES` service **default**.
    # AWS: the no-cookies type "is the same as AMPLIFY_MANAGED, except that it
    # excludes all cookies from the cache key. This is the default setting." The
    # session is a cookie, so under the default two different caseworkers' requests
    # share a cache key and a cached SSR page for one household could be served to
    # another session. Set explicitly because the safe value is not the default.
    shared = {
        "platform": PLATFORM,
        "computeRoleArn": role_arn,
        "buildSpec": build_spec(),
        "cacheConfig": {"type": "AMPLIFY_MANAGED"},
        # A public repository with auto-branch creation would build any branch
        # anyone opened, against a compute role that can write decision rows.
        # App-level role attachment is only safe because these are off.
        "enableAutoBranchCreation": False,
        "enableBranchAutoDeletion": False,
        "enableBasicAuth": False,
    }

    existing_env: dict[str, str] = {}
    if app_id is None:
        # A placeholder URL for the first pass: the real hostname is not knowable
        # until the app exists. Non-empty on purpose — a blank `DASHBOARD_URL`
        # makes `/login` fall back to `http://localhost:3000`, redirecting every
        # caseworker's sign-in to their own machine.
        created = client.create_app(
            name=APP_NAME,
            environmentVariables=environment_variables(
                runtime_arn, cognito, "https://pending.invalid"
            ),
            tags=naming.TAGS,
            **shared,
        )["app"]
        app_id = str(created["appId"])
        default_domain = str(created.get("defaultDomain") or f"{app_id}.amplifyapp.com")
        repository = str(created.get("repository") or "")
    else:
        # **A fresh read, immediately before the merge.** The console writes its
        # own keys into the same map `update_app` replaces, so a remembered or
        # stale map deletes them (measured live — see `merged_environment`).
        existing = client.get_app(appId=app_id)["app"]
        existing_env = dict(existing.get("environmentVariables") or {})
        default_domain = str(
            existing.get("defaultDomain") or f"{app_id}.amplifyapp.com"
        )
        repository = str(existing.get("repository") or "")

    # The app's own `defaultDomain` rather than a guessed
    # `{appId}.amplifyapp.com`. They agree today; a guess that stopped agreeing
    # would break the Cognito callback URL and the sign-in redirect together, and
    # both fail closed in a way that reads as "auth is broken".
    amplify_url = f"https://{BRANCH}.{default_domain}"

    # **A custom domain, once attached, is the canonical one.** `DASHBOARD_URL` is
    # what `/login` builds its OAuth `redirect_uri` from, and that value must match
    # a URL registered on the Cognito client *and* the host the caseworker is
    # actually on. Overwriting an operator-set custom domain with the amplifyapp
    # hostname would send every sign-in to the wrong origin. So an existing
    # `DASHBOARD_URL` is preserved rather than recomputed; the amplifyapp URL is
    # still registered as a callback so a certificate problem on the custom domain
    # cannot leave the demo with no working URL.
    url = (existing_env.get("DASHBOARD_URL") or "").strip() or amplify_url

    if update_cognito:
        # The callback URL is only knowable once the app exists, so Cognito is
        # updated afterwards. Without this the hosted UI refuses the redirect.
        from infra import provision_cognito

        provision_cognito.provision(callback_urls=callback_urls(url, amplify_url))

    # One update carrying the complete intended state. The variable map is this
    # module's keys **merged over the fresh read**, never a rebuilt map: the call
    # is a full replace, so anything omitted is deleted.
    client.update_app(
        appId=app_id,
        environmentVariables=merged_environment(
            existing_env, runtime_arn, cognito, url
        ),
        **shared,
    )

    # **Tag on the converge path too, via `TagResource`.** `UpdateApp` has no
    # `tags` parameter (checked against the live API model), so an app that
    # already existed — including one the operator created in the console while
    # connecting the repository, which is exactly what happened on the first real
    # run — would otherwise stay **untagged forever** while `create_app`'s `tags=`
    # made the script look like it tagged everything. Measured on the live app:
    # `list-tags-for-resource` returned `{}`. Tags are what make Grace's spend
    # separable in Cost Explorer against a $50 credit budget and what lets
    # teardown identify what it owns, so an untagged app is a resource nobody can
    # attribute or reliably clean up.
    client.tag_resource(
        resourceArn=f"arn:aws:amplify:{naming.REGION}:{account_id}:apps/{app_id}",
        tags=naming.TAGS,
    )

    # **No `create_branch`.** A manually created branch blocks the repository
    # connection outright: `Cannot connect your app to repository while manually
    # deployed branch still exists. Please delete all branches and try again.`
    # Amplify creates the branch when the operator connects the repository; a
    # later run of this script converges its settings.
    branch_converged = False
    try:
        client.get_branch(appId=app_id, branchName=BRANCH)
    except ClientError as exc:
        # **Only "not found" means "the operator has not connected it yet".** A
        # bare `except ClientError` here would swallow an AccessDenied or a
        # throttle and report `branch_converged: False`, which reads as "waiting
        # for the browser step" — so the operator would go and do a step that is
        # already done while the branch silently kept a stale `framework` or no
        # compute role. Same discipline as `is_already_exists`: discriminate, do
        # not catch broadly on a provisioning path.
        if exc.response["Error"]["Code"] not in {"NotFoundException", "ResourceNotFoundException"}:
            raise
    else:
        client.update_branch(
            appId=app_id,
            branchName=BRANCH,
            framework=FRAMEWORK,
            stage="PRODUCTION",
            computeRoleArn=role_arn,
            enablePullRequestPreview=False,
        )
        branch_converged = True

    return {
        "app_id": app_id,
        "url": url,
        "amplify_url": amplify_url,
        "branch": BRANCH,
        "compute_role_arn": role_arn,
        # Reported so a converge says out loud which keys it carried forward
        # rather than silently depending on them being there.
        "preserved": sorted(unowned_keys(existing_env)),
        # **Read from the app's own `repository` field, never inferred from the
        # branch's existence.** Those are different claims: a branch can exist on
        # an app with no repository (`CreateBranch` succeeds on one), and that
        # state is worse than having no branch at all, because it *blocks* the
        # repository connection. Reporting "connected" because a branch was found
        # would be the unconfirmed-success claim hard rule 6 exists to forbid,
        # aimed at the operator who still has to do the one manual step.
        "repository": repository,
        "repository_connected": bool(repository),
        "branch_converged": branch_converged,
        "next_step": (
            "Done — the repository is connected. Re-run this script after any "
            "settings change; it converges the app and the branch."
            if repository
            else (
                "Connect the repository in the Amplify console (App settings -> "
                "Git repository), authorizing the AWS Amplify GitHub app for "
                "https://github.com/mohamedsorour1998/Grace, monorepo root "
                f"`web`, branch `{BRANCH}`. There is no API for the GitHub App "
                "installation. Then re-run this script to converge the branch."
            )
        ),
    }


if __name__ == "__main__":
    for key, value in provision().items():
        print(f"{key}: {value}")
