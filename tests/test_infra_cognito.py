"""The pool's shape, asserted offline.

A user pool is easy to create with a permissive password policy and no MFA
consideration, and nothing about the running system says so afterwards. These
assertions are cheap and they pin the choices.
"""

from __future__ import annotations

from botocore.exceptions import ClientError

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


def test_the_hosted_ui_uses_the_dashboards_own_palette():
    """The sign-in page must not look like a different product than the app.

    Cognito's hosted UI is the first thing a caseworker sees, and by default it
    looks nothing like the dashboard it guards. These five values are Grace's
    own, read from `web/app/globals.css`; asserting them here is what keeps the
    two from drifting apart silently — a colour changed in one place and not the
    other is invisible until someone looks at both pages side by side.

    This is the **fallback** page now: the classic hosted UI's
    `*-customizable` classes (`ManagedLoginVersion: 1`), which is what the
    `grace-caseworkers` prefix domain still serves. `branding_settings()` styles
    managed login v2 on the custom domain. Both must carry the same palette, or
    a caseworker who falls back to the prefix domain lands on a page that looks
    like a different product.
    """
    css = provision_cognito.HOSTED_UI_CSS
    palette = {
        "#FAF9F7": "paper",
        "#1C1F23": "ink",
        "#6B7280": "muted",
        "#E5E3DF": "rule",
        "#B4530A": "escalate",
        "#9B2C2C": "error",
    }
    for value, name in palette.items():
        assert value in css, f"the {name} colour ({value}) is missing from the hosted UI CSS"
    # And the classes it targets must be the ones Cognito actually honours — a
    # typo here is silently ignored rather than rejected.
    for cls in (
        ".background-customizable",
        ".inputField-customizable",
        ".submitButton-customizable",
        ".errorMessage-customizable",
    ):
        assert cls in css, f"{cls} is not styled, so that element keeps Cognito's default"


# ---------------------------------------------------------------------------
# Managed login v2 on `auth.rosettacloud.app` (Plan 4, Task 1).
#
# The property under all of this: **a caseworker signs in on Grace's own domain
# and the password still never touches Grace's servers.** The alternative — a
# Next.js login page posting credentials to a route handler — would have been
# less code and a larger credential surface.
# ---------------------------------------------------------------------------


def test_the_custom_domain_is_graces_own_and_nobody_elses():
    """Every resource this project creates is `grace-*` or this hostname.

    The account holds `astrolabe-paper-auth` and `rosettaclaw-live-auth`, and a
    domain constant is the one name in this file that is not prefixed `grace`.
    Pinning it means a change to some other project's hostname cannot arrive as
    a one-line edit."""
    assert provision_cognito.CUSTOM_DOMAIN == "auth.rosettacloud.app"


def test_the_prefix_domain_survives_the_switch():
    """**The fallback must not be tidied away.**

    A pool may hold one prefix domain and one custom domain at once. Keeping
    `grace-caseworkers` means a certificate problem, a CloudFront propagation
    delay, or a DNS mistake does not leave the demo with no working sign-in —
    and the demo is eight days away.

    Two things to keep: the constant, and the absence of any deletion. A future
    "we're on the custom domain now" cleanup would most naturally be a call to
    `delete_user_pool_domain`, so its absence is asserted from the module's own
    source rather than trusted to a comment.
    """
    import inspect

    assert provision_cognito.DOMAIN_PREFIX == "grace-caseworkers"
    source = inspect.getsource(provision_cognito)
    assert "delete_user_pool_domain" not in source, (
        "the prefix domain is the fallback sign-in page; deleting it removes "
        "the only recovery path if the custom domain has trouble"
    )
    # And `HOSTED_UI_CSS` is what that fallback renders, so it is live code.
    assert provision_cognito.HOSTED_UI_CSS.strip()


def test_the_custom_domain_asks_for_managed_login_version_2():
    """Version 1 is the classic hosted UI, which has no settings document — so a
    v1 domain would ignore `branding_settings()` entirely and serve AWS's own
    layout on Grace's hostname. The two versions are named constants so the
    difference is legible at the call site."""
    config = provision_cognito.custom_domain_config("arn:aws:acm:us-east-1:1:certificate/x")
    assert config["ManagedLoginVersion"] == provision_cognito.MANAGED_LOGIN_VERSION == 2
    assert provision_cognito.CLASSIC_LOGIN_VERSION == 1
    assert config["Domain"] == provision_cognito.CUSTOM_DOMAIN


def test_the_custom_domain_attaches_a_certificate():
    """A custom domain is a CloudFront distribution, and CloudFront will not
    serve HTTPS without one. `CreateUserPoolDomain` refuses the call rather than
    serving plaintext, which is the right direction — this pins that the
    argument is built at all."""
    arn = "arn:aws:acm:us-east-1:339712964409:certificate/abc"
    config = provision_cognito.custom_domain_config(arn)
    assert config["CustomDomainConfig"]["CertificateArn"] == arn


def test_the_certificate_region_is_pinned_even_when_the_pools_region_moves():
    """**A region bug here would be invisible in this account.**

    The certificate attaches to a CloudFront distribution, which is global, and
    CloudFront reads certificates only from `us-east-1`. Grace's pool happens to
    live in `us-east-1` too — so `region_name=naming.REGION` at the call site
    would work here and fail in any account that moved the pool, with an error
    naming the certificate rather than the region.

    Driven by moving `naming.REGION` and asserting the ACM client is still asked
    for `us-east-1`. Rewriting the call site to `naming.REGION` fails this.
    """
    seen: dict[str, str] = {}

    class _Recorder:
        def __getattr__(self, name):
            raise AssertionError(f"no AWS call expected, got {name}")

    def fake_boto3_client(service, **kwargs):
        seen[service] = kwargs.get("region_name", "")
        if service == "acm":
            return _IssuedCertificates()
        return _Recorder()

    original_client = provision_cognito.boto3.client
    original_region = provision_cognito.naming.REGION
    try:
        provision_cognito.boto3.client = fake_boto3_client
        provision_cognito.naming.REGION = "eu-west-1"
        provision_cognito.ensure_certificate()
    finally:
        provision_cognito.boto3.client = original_client
        provision_cognito.naming.REGION = original_region

    assert seen["acm"] == "us-east-1", seen
    assert provision_cognito.CERTIFICATE_REGION == "us-east-1"


# --- the palette, on both sign-in pages ------------------------------------


def test_the_palette_is_the_dashboards_own_stylesheet():
    """The external anchor for both sign-in pages.

    `HOSTED_UI_CSS` and `branding_settings()` are both checked against
    `PALETTE` below, which would be satisfied by any six colours agreeing with
    each other. This is the assertion that says they are *Grace's* six: read
    straight out of `web/app/globals.css`, the stylesheet the dashboard itself
    renders from. A colour changed in the app and not here fails here.
    """
    from pathlib import Path

    css = (
        Path(__file__).resolve().parent.parent / "web" / "app" / "globals.css"
    ).read_text()
    for name, value in provision_cognito.PALETTE.items():
        assert value in css, (
            f"{name} ({value}) is not in web/app/globals.css — the sign-in page "
            f"and the dashboard have drifted apart"
        )


def test_both_sign_in_pages_render_the_same_palette():
    """The prefix domain serves v1 (CSS classes) and the custom domain serves v2
    (a settings document). They are configured through two entirely different
    APIs, which is exactly how one gets restyled and the other does not — and a
    caseworker who falls back to the prefix domain would then land on a page that
    looks like a different product at the moment they are already confused."""
    import json

    v1 = provision_cognito.HOSTED_UI_CSS
    v2 = json.dumps(provision_cognito.branding_settings())
    for name, value in provision_cognito.PALETTE.items():
        assert value in v1, f"{name} is missing from the v1 fallback's CSS"
        assert value.lstrip("#").lower() in v2, (
            f"{name} is missing from the v2 managed login branding"
        )


def test_managed_login_colours_carry_an_alpha_byte():
    """**Eight hex digits, or the component silently keeps AWS blue.**

    Every colour in Cognito's own default document is `rrggbbaa` — measured off
    the `UseCognitoProvidedValues` defaults, which are uniformly `0972d3ff`
    shaped. A six-digit value is not rejected by the API; it is ignored, so the
    provisioning run reports success and the button stays blue. `rgba()` is the
    single place that appends the byte, and this walks the whole document to
    prove nothing bypassed it.
    """
    import re

    hex_colour = re.compile(r"^[0-9a-f]{6,8}$")

    def walk(node, path="") -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                found += walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                found += walk(value, f"{path}[{i}]")
        elif isinstance(node, str) and hex_colour.match(node):
            found.append(f"{path}={node}")
        return found

    colours = walk(provision_cognito.branding_settings())
    assert colours, "no colours found at all — the document is not what it was"
    for entry in colours:
        assert len(entry.split("=")[1]) == 8, (
            f"{entry} has no alpha byte; Cognito ignores it and keeps its default"
        )


def test_an_unknown_colour_name_is_a_provisioning_failure():
    """A typo'd component colour is otherwise invisible: the document is accepted
    and that one part of the page keeps Cognito's default."""
    import pytest

    with pytest.raises(KeyError):
        provision_cognito.rgba("escalte")


def test_the_sign_in_page_is_not_browser_adaptive():
    """The dashboard has one palette and no dark mode. Left adaptive, a
    caseworker on a dark-mode laptop gets a dark sign-in page in front of a
    light app — which reads as a broken deployment rather than a preference."""
    assert (
        provision_cognito.branding_settings()["categories"]["global"]["colorSchemeMode"]
        == "LIGHT"
    )


# --- the sign-in page's images ----------------------------------------------


def test_an_uploaded_image_is_also_switched_on():
    """**The defect this exists to catch: an asset stored and never drawn.**

    `pageBackground.image` and `form.logo` both default to `enabled: False` in
    Cognito's own document. Uploading a background and leaving the flag off
    succeeds, reports success, and changes nothing a caseworker can see — which
    is exactly how the page came to be described as having no background while
    the branding call had been returning 200 all along. Same shape as a
    six-digit colour: accepted, ignored.

    So every asset category uploaded must have its switch on, checked as a pair
    rather than one side at a time.
    """
    settings = provision_cognito.branding_settings()
    categories = {asset["Category"] for asset in provision_cognito.branding_assets()}

    assert "PAGE_BACKGROUND" in categories
    assert settings["components"]["pageBackground"]["image"]["enabled"] is True

    assert "FORM_LOGO" in categories
    assert settings["components"]["form"]["logo"]["enabled"] is True

    assert "FAVICON_SVG" in categories
    assert settings["components"]["favicon"]["enabledTypes"] == ["SVG"]


def test_every_asset_is_uploaded_for_the_mode_the_page_actually_renders():
    """An asset's `ColorMode` must match `colorSchemeMode`, or it is stored
    against a mode the page never enters and the component falls back to
    Cognito's default with no error anywhere."""
    mode = provision_cognito.branding_settings()["categories"]["global"][
        "colorSchemeMode"
    ]
    assets = provision_cognito.branding_assets()
    assert assets, "no assets means the page renders Cognito's own images"
    for asset in assets:
        assert asset["ColorMode"] == mode, (
            f"{asset['Category']} is uploaded for {asset['ColorMode']} while the "
            f"page renders {mode}"
        )
        assert asset["Extension"] == "SVG"
        assert isinstance(asset["Bytes"], bytes)


def test_the_sign_in_images_are_drawn_in_the_dashboards_palette():
    """The images are the one part of the sign-in page that is not a colour
    field, so nothing else would notice them drifting. They are generated from
    `PALETTE` rather than hand-authored for exactly that reason, and this reads
    the rendered markup back to prove it."""
    background = provision_cognito.login_background_svg()
    logo = provision_cognito.login_logo_svg()
    for name in ("paper", "rule", "ink"):
        assert provision_cognito.PALETTE[name] in background, (
            f"{name} is missing from the sign-in background"
        )
    assert provision_cognito.PALETTE["escalate"] in background
    assert provision_cognito.PALETTE["ink"] in logo
    assert provision_cognito.PALETTE["muted"] in logo


def test_the_background_is_graces_own_split_and_not_a_decoration():
    """Nine quiet rows and three accented ones — the same 9/3 the dashboard
    headline reports. A background that drifted to some other count would be
    telling a caseworker a number that is not Grace's."""
    background = provision_cognito.login_background_svg()
    escalating = background.count(
        f'fill="{provision_cognito.PALETTE["escalate"]}"'
    )
    assert escalating == provision_cognito.BACKGROUND_ESCALATIONS == 3
    assert provision_cognito.BACKGROUND_ROWS == 12
    # Every row draws its own hairline, so the rule count is the row count plus
    # the one under the masthead.
    assert background.count('height="1"') == provision_cognito.BACKGROUND_ROWS + 1


def test_the_sign_in_images_are_well_formed_xml():
    """An SVG Cognito cannot parse is an image slot that silently stays empty.
    Parsing is the cheap check that a generated string is still a document."""
    import xml.etree.ElementTree as ET

    for svg in (
        provision_cognito.login_background_svg(),
        provision_cognito.login_logo_svg(),
        provision_cognito.login_favicon_svg(),
    ):
        root = ET.fromstring(svg)
        assert root.tag.endswith("svg")
        assert root.get("viewBox")


def test_the_converge_path_re_sends_the_images_it_switched_on():
    """A second run must not leave the page asking for images it no longer
    uploads. `branding_settings()` turns the background and the logo on
    unconditionally, so `Assets` belongs on the update call as much as on the
    create — enabled-and-empty is worse than the flat page this replaced."""
    for existing in (None, "style-123"):
        idp = _FakeIdp(branding=existing)
        provision_cognito.ensure_branding(idp, "us-east-1_POOL", "client-1")
        _, kwargs = idp.calls[-1]
        assert kwargs["Assets"] == provision_cognito.branding_assets(), (
            f"assets missing from the {'create' if existing is None else 'update'} path"
        )


# --- certificates -----------------------------------------------------------


class _FakeAcm:
    """An ACM that pages, so the pagination loop genuinely iterates."""

    def __init__(self, pages, detail=None):
        self._pages = pages
        self._detail = detail or {}
        self.requested: list[dict] = []
        self.waited: list[str] = []

    def list_certificates(self, **kwargs):
        index = 0 if "NextToken" not in kwargs else int(kwargs["NextToken"])
        page = {"CertificateSummaryList": self._pages[index]}
        if index + 1 < len(self._pages):
            page["NextToken"] = str(index + 1)
        return page

    def request_certificate(self, **kwargs):
        self.requested.append(kwargs)
        return {"CertificateArn": "arn:requested"}

    def describe_certificate(self, CertificateArn):
        return {"Certificate": self._detail}

    def get_waiter(self, name):
        waited = self.waited

        class _Waiter:
            def wait(self, CertificateArn):
                waited.append(CertificateArn)

        return _Waiter()


class _IssuedCertificates(_FakeAcm):
    def __init__(self):
        super().__init__(
            [[{
                "CertificateArn": "arn:issued",
                "DomainName": provision_cognito.CUSTOM_DOMAIN,
                "Status": "ISSUED",
            }]]
        )


class _FakeRoute53:
    def __init__(self):
        self.changes: list[dict] = []

    def change_resource_record_sets(self, **kwargs):
        self.changes.append(kwargs)
        return {"ChangeInfo": {"Id": "/change/X", "Status": "PENDING"}}


def test_a_certificate_on_a_later_page_is_not_a_second_certificate():
    """**The consequence of a missed page is not a failure — it is a duplicate.**

    `list_certificates` paginates. A single-page lookup finds nothing, requests
    a *second* certificate for the same name, and leaves one attached and one
    orphaned that nobody can tell apart afterwards. Plan 2 hit the single-page
    version of this bug three times on other APIs; the fake pages so the loop has
    to run.
    """
    acm = _FakeAcm([
        [{"CertificateArn": "arn:other", "DomainName": "example.com", "Status": "ISSUED"}],
        [{"CertificateArn": "arn:ours", "DomainName": provision_cognito.CUSTOM_DOMAIN,
          "Status": "ISSUED"}],
    ])
    route53 = _FakeRoute53()
    assert provision_cognito.ensure_certificate(acm, route53) == "arn:ours"
    assert acm.requested == [], "a second certificate was requested for a name already covered"


def test_a_subject_alternative_name_counts_as_coverage():
    """`auth.rosettacloud.app` may perfectly well arrive as a SAN on a later
    certificate for the parent domain. Matching the subject alone would then
    request a duplicate for a name that is already covered."""
    acm = _FakeAcm([[{
        "CertificateArn": "arn:wildcard",
        "DomainName": "rosettacloud.app",
        "SubjectAlternativeNameSummaries": ["www.rosettacloud.app",
                                            provision_cognito.CUSTOM_DOMAIN],
        "Status": "ISSUED",
    }]])
    assert provision_cognito.ensure_certificate(acm, _FakeRoute53()) == "arn:wildcard"
    assert acm.requested == []


def test_an_unusable_certificate_is_not_reused():
    """`CreateUserPoolDomain` refuses a FAILED or EXPIRED certificate with an
    error that names the certificate rather than its state, so reusing one sends
    the next person looking in the wrong place. A fresh request is the right
    answer, and the *waiter* is what makes this fail closed rather than returning
    a PENDING arn the domain call would then reject."""
    for status in ("FAILED", "EXPIRED", "REVOKED", "INACTIVE"):
        acm = _FakeAcm(
            [[{"CertificateArn": "arn:dead",
               "DomainName": provision_cognito.CUSTOM_DOMAIN, "Status": status}]],
            detail={
                "Status": "PENDING_VALIDATION",
                "DomainValidationOptions": [
                    {"ResourceRecord": {"Name": "_x.auth.rosettacloud.app.",
                                        "Type": "CNAME", "Value": "_y.acm-validations.aws."}}
                ],
            },
        )
        route53 = _FakeRoute53()
        arn = provision_cognito.ensure_certificate(acm, route53, poll_seconds=0)
        assert arn == "arn:requested", status
        assert acm.waited == ["arn:requested"], f"{status}: returned without waiting for ISSUED"
        assert route53.changes, f"{status}: no validation record was written"


def test_an_issued_certificate_wins_over_a_pending_one():
    """A previous partial run leaves a PENDING certificate behind. Picking it
    over an ISSUED one would block the domain on a validation that has already
    happened elsewhere."""
    acm = _FakeAcm([[
        {"CertificateArn": "arn:pending", "DomainName": provision_cognito.CUSTOM_DOMAIN,
         "Status": "PENDING_VALIDATION"},
        {"CertificateArn": "arn:issued", "DomainName": provision_cognito.CUSTOM_DOMAIN,
         "Status": "ISSUED"},
    ]])
    assert provision_cognito.ensure_certificate(acm, _FakeRoute53()) == "arn:issued"
    assert acm.waited == [], "an already-issued certificate should not be waited on"


def test_a_pending_certificate_is_validated_rather_than_returned():
    """The one path that writes DNS. A run that returned the PENDING arn would
    fail at `CreateUserPoolDomain` instead, which is a worse place to learn it."""
    acm = _FakeAcm(
        [[{"CertificateArn": "arn:pending", "DomainName": provision_cognito.CUSTOM_DOMAIN,
           "Status": "PENDING_VALIDATION"}]],
        detail={
            "Status": "PENDING_VALIDATION",
            "DomainValidationOptions": [
                {"ResourceRecord": {"Name": "_abc.auth.rosettacloud.app.", "Type": "CNAME",
                                    "Value": "_def.acm-validations.aws."}}
            ],
        },
    )
    route53 = _FakeRoute53()
    assert provision_cognito.ensure_certificate(acm, route53, poll_seconds=0) == "arn:pending"
    assert acm.waited == ["arn:pending"]
    change = route53.changes[0]
    assert change["HostedZoneId"] == provision_cognito.HOSTED_ZONE_ID
    record = change["ChangeBatch"]["Changes"][0]["ResourceRecordSet"]
    assert record["Type"] == "CNAME"
    # UPSERT, not CREATE: a re-run must converge rather than fail on a record a
    # previous run already wrote.
    assert change["ChangeBatch"]["Changes"][0]["Action"] == "UPSERT"


def test_a_certificate_with_no_validation_record_raises():
    """ACM publishes the record asynchronously, so an immediate `describe` has
    `DomainValidationOptions` present and `ResourceRecord` absent. Polling
    forever would hang a provisioning run; writing nothing and waiting would hang
    on the waiter instead. Raise, and name what is missing."""
    import pytest

    acm = _FakeAcm(
        [[]],
        detail={"Status": "PENDING_VALIDATION", "DomainValidationOptions": [{}]},
    )
    with pytest.raises(RuntimeError, match="validation record"):
        provision_cognito.ensure_certificate(acm, _FakeRoute53(), poll_seconds=0)


# --- DNS --------------------------------------------------------------------


def test_the_alias_points_at_cloudfronts_zone_not_graces():
    """**The single easiest mistake in this whole task.**

    A Route 53 alias record carries two hosted-zone ids: the zone the record
    lives in (`HOSTED_ZONE_ID`, Grace's) and the zone of the thing it points at
    (CloudFront's fixed global `Z2FDTNDATAQYW2`). Putting Grace's in both
    positions is accepted by nothing helpful — and the two values sit four lines
    apart. Keeping the batch as data is what lets this be asserted at all.
    """
    batch = provision_cognito.alias_change_batch("d123.cloudfront.net")
    target = batch["Changes"][0]["ResourceRecordSet"]["AliasTarget"]
    assert target["HostedZoneId"] == "Z2FDTNDATAQYW2"
    assert target["HostedZoneId"] != provision_cognito.HOSTED_ZONE_ID
    assert target["DNSName"] == "d123.cloudfront.net"


def test_the_alias_does_not_evaluate_target_health():
    """Cognito's distribution publishes no health check, so an alias that
    evaluated one resolves to NXDOMAIN — no sign-in page at all, and a DNS
    failure is the hardest kind to read as a configuration choice."""
    batch = provision_cognito.alias_change_batch("d123.cloudfront.net")
    assert batch["Changes"][0]["ResourceRecordSet"]["AliasTarget"][
        "EvaluateTargetHealth"
    ] is False


def test_the_alias_names_the_custom_domain_and_upserts():
    """Named for `auth.` specifically: an alias written at the zone apex would
    take `rosettacloud.app` itself away from whatever serves it now."""
    batch = provision_cognito.alias_change_batch("d123.cloudfront.net")
    change = batch["Changes"][0]
    assert change["Action"] == "UPSERT"
    assert change["ResourceRecordSet"]["Name"] == "auth.rosettacloud.app."
    assert change["ResourceRecordSet"]["Type"] == "A"


def test_the_dns_write_targets_graces_zone():
    route53 = _FakeRoute53()
    provision_cognito.ensure_dns_alias(route53, "d123.cloudfront.net")
    assert route53.changes[0]["HostedZoneId"] == provision_cognito.HOSTED_ZONE_ID


# --- the domain itself ------------------------------------------------------


class _FakeIdp:
    """Enough of `cognito-idp` to drive the domain and branding paths."""

    def __init__(self, domains=None, branding=None):
        self._domains = dict(domains or {})
        self._branding = branding
        self.calls: list[tuple[str, dict]] = []
        self.describe_domain_results: list[dict] = []

    def _record(self, name, kwargs):
        self.calls.append((name, kwargs))

    def describe_user_pool_domain(self, Domain):
        self._record("describe_user_pool_domain", {"Domain": Domain})
        if self.describe_domain_results:
            return {"DomainDescription": self.describe_domain_results.pop(0)}
        return {"DomainDescription": self._domains.get(Domain, {})}

    def create_user_pool_domain(self, **kwargs):
        self._record("create_user_pool_domain", kwargs)
        return {"CloudFrontDomain": "created.cloudfront.net"}

    def update_user_pool_domain(self, **kwargs):
        self._record("update_user_pool_domain", kwargs)
        return {}

    def describe_managed_login_branding_by_client(self, **kwargs):
        self._record("describe_managed_login_branding_by_client", kwargs)
        if self._branding is None:
            raise ClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "no"}},
                "DescribeManagedLoginBrandingByClient",
            )
        return {"ManagedLoginBranding": {"ManagedLoginBrandingId": self._branding}}

    def create_managed_login_branding(self, **kwargs):
        self._record("create_managed_login_branding", kwargs)
        return {"ManagedLoginBranding": {"ManagedLoginBrandingId": "brand-new"}}

    def update_managed_login_branding(self, **kwargs):
        self._record("update_managed_login_branding", kwargs)
        return {}


def test_the_domain_is_created_with_the_v2_config_when_absent():
    idp = _FakeIdp()
    target = provision_cognito.ensure_custom_domain(idp, "us-east-1_POOL", "arn:cert")
    assert target == "created.cloudfront.net"
    name, kwargs = idp.calls[-1]
    assert name == "create_user_pool_domain"
    assert kwargs["ManagedLoginVersion"] == 2
    assert kwargs["UserPoolId"] == "us-east-1_POOL"


def test_the_domain_converges_and_re_reads_its_alias_target():
    """**The alias target is read back, never remembered.**

    `UpdateUserPoolDomain` does not return the distribution on every path, and
    an alias record pointing at a distribution that is no longer the domain's
    resolves to a stranger's CloudFront — a sign-in page that is not Grace's.
    The fake hands back a *different* distribution on the second describe, so a
    version that reused the first value fails here.
    """
    idp = _FakeIdp()
    idp.describe_domain_results = [
        {"UserPoolId": "us-east-1_POOL", "CloudFrontDistribution": "stale.cloudfront.net"},
        {"UserPoolId": "us-east-1_POOL", "CloudFrontDistribution": "fresh.cloudfront.net"},
    ]
    target = provision_cognito.ensure_custom_domain(idp, "us-east-1_POOL", "arn:cert")
    assert target == "fresh.cloudfront.net"
    assert [c[0] for c in idp.calls].count("update_user_pool_domain") == 1
    assert "create_user_pool_domain" not in [c[0] for c in idp.calls]


def test_a_domain_belonging_to_another_pool_is_refused():
    """**The one that protects the other two projects in this account.**

    `astrolabe-paper-auth` and `rosettaclaw-live-auth` live here. An
    `UpdateUserPoolDomain` against a domain attached to one of them would
    repoint that project's sign-in at Grace's certificate — a change nothing in
    Grace's own verification would notice, because Grace's own sign-in would
    keep working.
    """
    import pytest

    idp = _FakeIdp({provision_cognito.CUSTOM_DOMAIN: {
        "UserPoolId": "us-east-1_SOMEONEELSE",
        "CloudFrontDistribution": "theirs.cloudfront.net",
    }})
    with pytest.raises(RuntimeError, match="us-east-1_SOMEONEELSE"):
        provision_cognito.ensure_custom_domain(idp, "us-east-1_POOL", "arn:cert")
    assert [c[0] for c in idp.calls] == ["describe_user_pool_domain"]


def test_an_absent_domain_reported_as_an_exception_reads_as_absent():
    """Cognito reports a missing domain two ways depending on the path — an
    empty `DomainDescription` on some, `ResourceNotFoundException` on others.
    Treating only one as "absent" makes the create path unreachable half the
    time, and the failure surfaces as an unhandled `ClientError` in a
    provisioning script."""
    class _Raising(_FakeIdp):
        def describe_user_pool_domain(self, Domain):
            raise ClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "no"}},
                "DescribeUserPoolDomain",
            )

    idp = _Raising()
    assert provision_cognito.ensure_custom_domain(idp, "us-east-1_POOL", "arn:cert") == (
        "created.cloudfront.net"
    )


# --- branding ---------------------------------------------------------------


def test_branding_is_created_when_absent_and_never_asks_for_awss_own_values():
    """`UseCognitoProvidedValues` is mutually exclusive with `Settings`. Passing
    it means "AWS blue", and the call succeeds either way — so the run reports
    success and the sign-in page is not Grace's."""
    idp = _FakeIdp(branding=None)
    assert provision_cognito.ensure_branding(idp, "us-east-1_POOL", "client-1") == "brand-new"
    name, kwargs = idp.calls[-1]
    assert name == "create_managed_login_branding"
    assert "UseCognitoProvidedValues" not in kwargs
    assert kwargs["Settings"] == provision_cognito.branding_settings()


def test_branding_converges_rather_than_failing_on_a_second_run():
    """Idempotence is the recovery path in this project. A second run must
    update the style that exists, not raise `ConcurrentModification` on a create
    — otherwise re-running to fix something else fails on branding."""
    idp = _FakeIdp(branding="style-123")
    assert provision_cognito.ensure_branding(idp, "us-east-1_POOL", "client-1") == "style-123"
    name, kwargs = idp.calls[-1]
    assert name == "update_managed_login_branding"
    assert kwargs["ManagedLoginBrandingId"] == "style-123"
    assert "UseCognitoProvidedValues" not in kwargs
    assert "create_managed_login_branding" not in [c[0] for c in idp.calls]


def test_a_branding_error_that_is_not_absence_is_raised():
    """Fail closed on an unexpected error rather than creating a second style.
    `except ClientError: create` would turn a throttle into a duplicate."""
    import pytest

    class _Throttled(_FakeIdp):
        def describe_managed_login_branding_by_client(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "TooManyRequestsException", "Message": "slow down"}},
                "DescribeManagedLoginBrandingByClient",
            )

    with pytest.raises(ClientError):
        provision_cognito.ensure_branding(_Throttled(), "us-east-1_POOL", "client-1")


# --- what `provision` hands to the dashboard -------------------------------


class _WholeIdp(_FakeIdp):
    """A pool and client that already exist, so `provision` takes its converge
    path — the one a re-run actually follows."""

    def list_user_pools(self, **kwargs):
        return {"UserPools": [{"Name": provision_cognito.POOL_NAME, "Id": "us-east-1_POOL"}]}

    def list_user_pool_clients(self, **kwargs):
        return {"UserPoolClients": [
            {"ClientName": provision_cognito.CLIENT_NAME, "ClientId": "client-1"}
        ]}

    def update_user_pool_client(self, **kwargs):
        self._record("update_user_pool_client", kwargs)
        return {}

    def admin_create_user(self, **kwargs):
        raise ClientError(
            {"Error": {"Code": "UsernameExistsException", "Message": "exists"}},
            "AdminCreateUser",
        )

    def set_ui_customization(self, **kwargs):
        self._record("set_ui_customization", kwargs)
        return {}


def test_provision_hands_the_dashboard_the_custom_domain_and_keeps_the_fallback():
    """`provision_amplify` reads `cognito["domain"]` straight into
    `COGNITO_DOMAIN`, which is what `/login` builds its redirect from. So the
    switch to `auth.rosettacloud.app` propagates through this one key, and
    `fallback_domain` is what makes the recovery edit a value someone can read
    rather than a hostname they have to reconstruct."""
    # Seeded as a re-run: pool, client, custom domain, and branding all exist,
    # which is the path an operator actually takes.
    idp = _WholeIdp(
        domains={provision_cognito.CUSTOM_DOMAIN: {
            "UserPoolId": "us-east-1_POOL",
            "CloudFrontDistribution": "d123.cloudfront.net",
        }},
        branding="style-123",
    )
    route53 = _FakeRoute53()
    result = provision_cognito.provision(
        client=idp,
        callback_urls=["https://grace.rosettacloud.app/api/auth/callback"],
        acm=_IssuedCertificates(),
        route53=route53,
    )
    assert result["domain"] == "https://auth.rosettacloud.app"
    assert result["fallback_domain"] == (
        "https://grace-caseworkers.auth.us-east-1.amazoncognito.com"
    )
    names = [c[0] for c in idp.calls]
    # **The prefix domain is created on every run and never deleted** — that is
    # what keeps the fallback there.
    assert "create_user_pool_domain" in names
    # The custom one converges rather than being recreated, and both sign-in
    # pages get restyled in the same run.
    assert "update_user_pool_domain" in names
    assert "set_ui_customization" in names
    assert "update_managed_login_branding" in names
    # And DNS follows the domain, pointing at the distribution just read back.
    assert route53.changes[-1]["ChangeBatch"]["Changes"][0]["ResourceRecordSet"][
        "AliasTarget"
    ]["DNSName"] == "d123.cloudfront.net"


def test_the_issuer_does_not_move_with_the_sign_in_domain():
    """**Why `verifySession` is unaffected by any of this.**

    The issuer is the pool's API URL. `verifySession` fetches its JWKS from
    `${issuer}/.well-known/jwks.json`, which lives on
    `cognito-idp.<region>.amazonaws.com` — not on either sign-in host. So a
    token minted through `auth.rosettacloud.app` verifies against exactly the
    same key set as one minted through the prefix domain, and the `iss` claim it
    carries is unchanged.

    Measured live as well: a real ID token obtained through the new domain
    carries this issuer. This pins the code side of that.
    """
    idp = _WholeIdp(branding="style-123")
    result = provision_cognito.provision(
        client=idp,
        callback_urls=["https://grace.rosettacloud.app/api/auth/callback"],
        acm=_IssuedCertificates(),
        route53=_FakeRoute53(),
    )
    assert result["issuer"] == (
        "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_POOL"
    )
    assert provision_cognito.CUSTOM_DOMAIN not in result["issuer"]
    assert provision_cognito.DOMAIN_PREFIX not in result["issuer"]
