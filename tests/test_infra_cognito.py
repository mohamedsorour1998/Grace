"""The pool's shape, asserted offline.

A user pool is easy to create with a permissive password policy and no MFA
consideration, and nothing about the running system says so afterwards. These
assertions are cheap and they pin the choices.
"""

from __future__ import annotations

from infra import provision_cognito


def test_the_pool_is_named_for_grace():
    """So `list-user-pools` output can be filtered, and so teardown cannot
    match another project's pool. This account already holds
    `astrolabe-paper-auth` and `rosettaclaw-live-auth`."""
    assert provision_cognito.POOL_NAME.startswith("grace")


def test_the_password_policy_is_not_the_default():
    """Cognito's default minimum is 8 with no symbol requirement. A benefits
    dashboard that can file renewals deserves better, and it costs nothing."""
    policy = provision_cognito.pool_spec()["Policies"]["PasswordPolicy"]
    assert policy["MinimumLength"] >= 12
    assert policy["RequireNumbers"] is True
    assert policy["RequireSymbols"] is True
    assert policy["RequireUppercase"] is True


def test_the_role_claim_is_declared_in_the_schema():
    """A custom attribute must exist in the pool's schema before a user can
    carry it. Setting `custom:role` on a user without declaring it fails at
    user-creation time, which is a confusing place to learn this."""
    names = {a["Name"] for a in provision_cognito.pool_spec()["Schema"]}
    assert "role" in names, names


def test_self_signup_is_disabled():
    """Anyone able to sign themselves up could reach the decide endpoint. The
    pool is admin-create-only: a caseworker account is issued, not requested."""
    cfg = provision_cognito.pool_spec()["AdminCreateUserConfig"]
    assert cfg["AllowAdminCreateUserOnly"] is True


def test_the_client_has_no_secret():
    """A public client. The dashboard runs the code exchange server-side, but a
    generated secret would then have to live in an Amplify env var for no gain —
    and a client secret in a build environment is a credential in a log waiting
    to happen."""
    assert provision_cognito.CLIENT_SPEC["GenerateSecret"] is False


def test_the_client_uses_the_authorization_code_flow():
    """Not implicit. The implicit flow returns the token in the URL fragment,
    which lands in browser history and any referrer; the code flow keeps it in a
    server-side exchange."""
    spec = provision_cognito.CLIENT_SPEC
    assert spec["AllowedOAuthFlows"] == ["code"]
    assert "implicit" not in spec["AllowedOAuthFlows"]
    assert spec["AllowedOAuthFlowsUserPoolClient"] is True


def test_the_scopes_do_not_include_anything_write_shaped():
    """openid gives the `sub`; profile is not needed and would carry name and
    email into a token that CloudTrail logs."""
    assert set(provision_cognito.CLIENT_SPEC["AllowedOAuthScopes"]) == {"openid"}


def test_the_role_attribute_is_explicitly_readable():
    """**The one that makes sign-in work at all.**

    Verified against a real ID token from a throwaway pool: with
    `ReadAttributes` naming `custom:role`, the claim arrives as
    `custom:role: caseworker`. When `ReadAttributes` is omitted, the client may
    read only `email_verified`, `phone_number_verified`, and the pool's
    *standard* attributes — a custom attribute is not among them. So without
    naming `custom:role` here it never reaches the ID token, `verifySession`
    refuses every legitimate caseworker, and the symptom reads as "auth is
    broken" rather than "one attribute is unreadable". It fails closed, which is
    the right direction and still means nobody can sign in.
    """
    assert provision_cognito.ROLE_CLAIM in provision_cognito.CLIENT_SPEC["ReadAttributes"]


def test_the_client_cannot_write_the_claim_that_authorises_it():
    """`WriteAttributes` must be PRESENT and must exclude `custom:role`.

    An earlier draft omitted the key entirely and called that capability
    absence. Measured on a throwaway pool, that is inverted: with
    `WriteAttributes` omitted, a signed-in user's `UpdateUserAttributes` against
    an ungranted *mutable* custom attribute **succeeded** — omission grants every
    attribute, as the AWS docs state outright. `custom:role` survived only
    because the schema marks it `Mutable: False`, so the draft claimed two
    guards and shipped one.

    Presence is therefore the assertion that matters, not absence. With the list
    set and `custom:role` excluded, the same write is refused with
    `NotAuthorizedException: A client attempted to write unauthorized attribute`
    — an authorisation refusal rather than an immutability one.
    """
    spec = provision_cognito.CLIENT_SPEC
    assert "WriteAttributes" in spec, "omitting this grants write access to everything"
    assert provision_cognito.ROLE_CLAIM not in spec["WriteAttributes"]
    # And the schema's immutability is the second guard, not the only one.
    role = next(a for a in provision_cognito.pool_spec()["Schema"] if a["Name"] == "role")
    assert role["Mutable"] is False
