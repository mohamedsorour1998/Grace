"""The caseworker user pool. Idempotent: re-running is the recovery path.

Cognito rather than a self-hosted auth library, for a reason that is not
fashion: Better Auth ships adapters for drizzle/kysely/memory/mongodb/prisma and
**no DynamoDB**, and this account has no RDS — so self-hosting auth would have
meant either a SQLite file that cannot survive a hosted deployment or a 0.1.0
community adapter holding the authentication layer of a benefits dashboard.
Cognito is a managed directory, so the question disappears.

This also un-defers AgentCore **Identity** from Plan 2, which is why Grace can
honestly claim four surfaces rather than three. Not five: Gateway stays deferred
with its written reason.
"""

from __future__ import annotations

import secrets

import boto3
from botocore.exceptions import ClientError

from infra import naming

POOL_NAME = "grace-caseworkers"
CLIENT_NAME = "grace-dashboard"
DOMAIN_PREFIX = "grace-caseworkers"

# The claim `verifySession` requires. Declared in the pool schema, set on the
# user at creation, and asserted in the ID token — a user who signs in without
# exactly this value gets no session at all, not a lesser one.
ROLE_CLAIM = "custom:role"
ROLE_VALUE = "caseworker"

# A seeded account for the demo. The username is opaque on purpose: Cognito puts
# `sub` (a UUID) in the token and that is what reaches a decision row, but a
# username that looked like a person would invite someone to read it as one.
SEED_USERNAME = "caseworker-01"


# The hosted UI's palette, so sign-in does not look like a different product than
# the dashboard it guards. Values are Grace's own, from `web/app/globals.css` —
# keep the two in step, or the login page drifts away from the app again.
#
# This is the **classic hosted UI** (`ManagedLoginVersion: 1`), which exposes a
# fixed set of `*-customizable` classes and nothing more: colours, the logo, and
# button styling. Managed login v2 offers real layout control, but it changes the
# sign-in URL shape, so `hostedUiUrl`'s `/login` path and the whole OAuth round
# trip would need re-testing. Not worth it for a cosmetic gain.
#
# Verified served rather than merely stored: the page links
# `.../<pool>/<client>/<cssVersion>/assets/CSS/custom-css.css` from CloudFront,
# and that file comes back with these exact values. Checking the HTML for the hex
# codes finds nothing — they are only ever in the linked stylesheet.
HOSTED_UI_CSS = """
.background-customizable {
  background: #FAF9F7;
}
.banner-customizable {
  background: #FAF9F7;
  border-bottom: 1px solid #E5E3DF;
  padding: 32px 0 16px 0;
}
.label-customizable {
  color: #1C1F23;
  font-weight: 500;
  font-size: 13px;
}
.textDescription-customizable {
  color: #6B7280;
  font-size: 13px;
  padding-top: 10px;
  padding-bottom: 14px;
}
.legalText-customizable {
  color: #6B7280;
  font-size: 11px;
}
.inputField-customizable {
  background: #FFFFFF;
  border: 1px solid #E5E3DF;
  border-radius: 6px;
  color: #1C1F23;
  font-size: 14px;
  padding: 10px 12px;
  width: 100%;
}
.inputField-customizable:focus {
  border-color: #B4530A;
  outline: 2px solid rgba(180, 83, 10, 0.18);
  outline-offset: 1px;
}
.submitButton-customizable {
  background: #1C1F23;
  border: none;
  border-radius: 6px;
  color: #FAF9F7;
  font-size: 14px;
  font-weight: 500;
  height: 42px;
  margin-top: 18px;
  width: 100%;
}
.submitButton-customizable:hover {
  background: #B4530A;
  color: #FFFFFF;
}
.errorMessage-customizable {
  background: #FFFFFF;
  border: 1px solid #9B2C2C;
  border-left: 3px solid #9B2C2C;
  border-radius: 6px;
  color: #9B2C2C;
  font-size: 13px;
  padding: 10px 12px;
}
.idpDescription-customizable {
  color: #6B7280;
  font-size: 13px;
}
.socialButton-customizable {
  border-radius: 6px;
}
.redirect-customizable {
  padding-top: 12px;
}
"""


CLIENT_SPEC: dict = {
    "ClientName": CLIENT_NAME,
    # Public client. The code exchange happens server-side in a route handler,
    # so a secret buys nothing — and a client secret in an Amplify build
    # environment is a credential one `echo` away from a log.
    "GenerateSecret": False,
    # The authorization-code flow, never implicit: implicit returns the token in
    # the URL fragment, which lands in browser history and any referrer header.
    "AllowedOAuthFlows": ["code"],
    "AllowedOAuthFlowsUserPoolClient": True,
    # `openid` alone. `profile` would carry name and email into a token that
    # CloudTrail logs, and nothing here needs either (Appendix D.4).
    "AllowedOAuthScopes": ["openid"],
    "SupportedIdentityProviders": ["COGNITO"],
    "ExplicitAuthFlows": ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    # **`ReadAttributes` must name `custom:role` explicitly.** Verified against
    # the live API docs: when `ReadAttributes` is omitted, the client can read
    # only `email_verified`, `phone_number_verified`, and the pool's *standard*
    # attributes — a custom attribute is not among them. So leaving this out
    # would keep `custom:role` out of the ID token, `verifySession` would refuse
    # every legitimate caseworker, and the failure would look like "auth is
    # broken" rather than "one attribute is unreadable". Fails closed, which is
    # the right direction and still unusable.
    "ReadAttributes": ["email", ROLE_CLAIM],
    # **`WriteAttributes` must be set, and must NOT contain `custom:role`.**
    # An earlier draft omitted it entirely and called that capability absence.
    # That is backwards, and it was measured on a throwaway pool on 2026-09-04:
    # with `WriteAttributes` omitted, a signed-in user's `UpdateUserAttributes`
    # call against an ungranted **mutable** custom attribute **SUCCEEDED**.
    # Omission grants every attribute, exactly as the AWS docs say ("When you
    # create an app client and don't customize attribute read and write
    # permissions, Amazon Cognito grants read and write permissions to all user
    # pool attributes"). `custom:role` survived that draft only because the
    # schema marks it `Mutable: False` — the plan claimed two guards and shipped
    # one, with the comment asserting the opposite of the behaviour.
    #
    # Setting the list is what makes the refusal a *permission* refusal. Probed
    # both ways on the same pool:
    #   WriteAttributes omitted, write custom:scratch (mutable) -> SUCCEEDED
    #   WriteAttributes: ["custom:scratch"], write custom:role  ->
    #       NotAuthorizedException: A client attempted to write unauthorized attribute
    #   WriteAttributes omitted, write custom:role (immutable)   ->
    #       InvalidParameterException: user.custom:role: Attribute cannot be updated.
    # The third is the immutability guard, not an authorisation one, which is why
    # it could not be read as evidence that omission withholds anything.
    #
    # `email` alone: nothing in the dashboard writes it, but a client with an
    # empty `WriteAttributes` cannot be updated later without a full replace
    # (see the converge note in `provision`), and a required attribute must be
    # writable. The role is set once by `admin_create_user`, an admin API that
    # this list does not bind, so nothing legitimate needs write access to it.
    "WriteAttributes": ["email"],
    # An hour. Long enough for a caseworker's session, short enough that a
    # leaked token expires before it is useful.
    "IdTokenValidity": 60,
    "AccessTokenValidity": 60,
    "TokenValidityUnits": {"IdToken": "minutes", "AccessToken": "minutes"},
}


def pool_spec() -> dict:
    """The pool's configuration, as data so it is testable without AWS."""
    return {
        "PoolName": POOL_NAME,
        "Policies": {
            "PasswordPolicy": {
                # Cognito's default is 8 with no symbol requirement. This
                # account can file benefit renewals.
                "MinimumLength": 12,
                "RequireUppercase": True,
                "RequireLowercase": True,
                "RequireNumbers": True,
                "RequireSymbols": True,
            }
        },
        # Admin-create-only. Anyone who could sign themselves up would reach the
        # decide endpoint; a caseworker account is issued, not requested.
        "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
        "Schema": [
            {
                "Name": "role",
                "AttributeDataType": "String",
                "Mutable": False,
                "Required": False,
                "StringAttributeConstraints": {"MinLength": "1", "MaxLength": "32"},
            }
        ],
        "UserPoolTags": naming.TAGS,
    }


def provision(client=None, callback_urls: list[str] | None = None) -> dict:
    """Create the pool, client, domain, and one caseworker. Idempotent.

    Returns the four values the dashboard needs as environment variables.
    """
    client = client or boto3.client("cognito-idp", region_name=naming.REGION)
    callback_urls = callback_urls or ["http://localhost:3000/api/auth/callback"]

    # Find an existing Grace pool before creating one. `ListUserPools`
    # paginates, and this account holds other projects' pools — Plan 2 hit the
    # single-page version of this bug three separate times.
    pool_id: str | None = None
    token: str | None = None
    while True:
        kwargs = {"MaxResults": 60}
        if token:
            kwargs["NextToken"] = token
        page = client.list_user_pools(**kwargs)
        for pool in page.get("UserPools", []):
            if pool["Name"] == POOL_NAME:
                pool_id = pool["Id"]
                break
        token = page.get("NextToken")
        if pool_id or not token:
            break

    if pool_id is None:
        pool_id = client.create_user_pool(**pool_spec())["UserPool"]["Id"]

    # The app client, likewise found-or-created. **Paginates for the same reason
    # the pool lookup does:** `ListUserPoolClients` returns `NextToken`, and a
    # missed page here would create a *second* `grace-dashboard` client. Two
    # clients means two client ids, and `verifySession` checks `aud` against the
    # one in the environment — so a token minted by the other client is refused
    # with a valid signature, which reads as "auth is broken" rather than
    # "there are two clients". Cheaper to page than to diagnose.
    client_id: str | None = None
    token = None
    while True:
        kwargs = {"UserPoolId": pool_id, "MaxResults": 60}
        if token:
            kwargs["NextToken"] = token
        page = client.list_user_pool_clients(**kwargs)
        for existing in page.get("UserPoolClients", []):
            if existing["ClientName"] == CLIENT_NAME:
                client_id = existing["ClientId"]
                break
        token = page.get("NextToken")
        if client_id or not token:
            break

    spec = {**CLIENT_SPEC, "UserPoolId": pool_id, "CallbackURLs": callback_urls,
            "LogoutURLs": [u.replace("/api/auth/callback", "/login") for u in callback_urls]}
    if client_id is None:
        client_id = client.create_user_pool_client(**spec)["UserPoolClient"]["ClientId"]
    else:
        # Converge: a re-run must apply the intended callback URLs, not leave
        # whatever a previous run wrote.
        #
        # **`UpdateUserPoolClient` is a FULL REPLACE, not a patch** — measured on
        # a throwaway pool on 2026-09-04. A minimal update naming only
        # `ClientName` left `ReadAttributes`, `CallbackURLs`, and
        # `AllowedOAuthFlows` all **absent** from the subsequent
        # `DescribeUserPoolClient`. So this call must send every field it wants
        # to keep, which is why it reuses the whole `spec` rather than sending a
        # delta. If someone later "tidies" this into a two-key update, the
        # deployed client silently loses its OAuth flows and `custom:role` read
        # permission, and every caseworker's sign-in starts failing closed with
        # no error at provisioning time.
        #
        # `GenerateSecret` must be stripped: it is a create-only parameter and
        # botocore raises `ParamValidationError` (not a `ClientError`, so no
        # `except ClientError` would catch it) when it appears in an update.
        # Verified — the error names the exact allowed parameter list.
        update = {k: v for k, v in spec.items() if k != "GenerateSecret"}
        client.update_user_pool_client(**update, ClientId=client_id)

    # The hosted UI domain. One API call, and it saves building sign-in forms.
    try:
        client.create_user_pool_domain(Domain=DOMAIN_PREFIX, UserPoolId=pool_id)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {
            "InvalidParameterException",  # already exists on this pool
            "AliasExistsException",
        }:
            raise

    # One caseworker, with the role claim. A generated password printed once.
    try:
        password = f"Gr{secrets.token_urlsafe(16)}!7"
        client.admin_create_user(
            UserPoolId=pool_id,
            Username=SEED_USERNAME,
            MessageAction="SUPPRESS",
            UserAttributes=[{"Name": ROLE_CLAIM, "Value": ROLE_VALUE}],
            TemporaryPassword=password,
        )
        client.admin_set_user_password(
            UserPoolId=pool_id, Username=SEED_USERNAME,
            Password=password, Permanent=True,
        )
        print(f"seeded {SEED_USERNAME} with password: {password}")
        print("record it now — it is not recoverable")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "UsernameExistsException":
            raise

    # The hosted UI's palette. Applied on every run, because it is idempotent by
    # nature — `set_ui_customization` replaces whatever CSS is there, and a
    # re-run should converge the sign-in page's appearance the same way it
    # converges the client's callback URLs.
    #
    # Deliberately NOT wrapped in a `try`. If styling fails, the sign-in page
    # still works, so this is not a fail-closed concern — but a provisioning
    # script that silently skips a step reports success while the thing is
    # absent, which is the Plan 2 lesson about swallowing a "not ready yet"
    # error. A loud failure here means someone re-runs, which is what
    # idempotence is for.
    client.set_ui_customization(
        UserPoolId=pool_id, ClientId=client_id, CSS=HOSTED_UI_CSS
    )

    return {
        "pool_id": pool_id,
        "client_id": client_id,
        "domain": f"https://{DOMAIN_PREFIX}.auth.{naming.REGION}.amazoncognito.com",
        "issuer": f"https://cognito-idp.{naming.REGION}.amazonaws.com/{pool_id}",
    }


if __name__ == "__main__":
    for key, value in provision().items():
        print(f"{key}: {value}")
