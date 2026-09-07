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
import time

import boto3
from botocore.exceptions import ClientError

from infra import naming

POOL_NAME = "grace-caseworkers"
CLIENT_NAME = "grace-dashboard"
DOMAIN_PREFIX = "grace-caseworkers"

# Grace's own sign-in host. The alternative considered and rejected was a
# hand-built Next.js login page: that would post the caseworker's password to a
# Grace route handler and call `InitiateAuth` server-side, which moves the
# credential through Grace's servers and turns a page into a password-guessing
# surface. A custom *domain* buys the same thing — a sign-in page that looks like
# Grace — while leaving the credential exchange entirely inside Cognito. Prefer
# the option that does not enlarge the credential surface.
CUSTOM_DOMAIN = "auth.rosettacloud.app"

# **The prefix domain is kept, deliberately.** A pool may hold one prefix domain
# and one custom domain at the same time, and keeping `grace-caseworkers` means a
# problem with the custom domain — a certificate, a CloudFront propagation delay,
# a DNS mistake — does not leave the demo with no working sign-in. It still
# serves `ManagedLoginVersion: 1`, which is what `HOSTED_UI_CSS` styles, so that
# constant is load-bearing rather than dead code.
#
# One documented consequence, so nobody reads it as a fault: with both domains
# present Cognito serves `/.well-known/openid-configuration` only on the custom
# one. That does not touch `verifySession`, whose JWKS lives on the API host
# (`cognito-idp.<region>.amazonaws.com/<pool>/.well-known/jwks.json`) rather than
# on either sign-in domain — measured, not assumed; see the docstring on
# `custom_domain_config`.

# `1` is the classic hosted UI (fixed `*-customizable` CSS classes and nothing
# else). `2` is managed login: a real settings document, so the sign-in page can
# carry Grace's palette rather than approximate it.
MANAGED_LOGIN_VERSION = 2
CLASSIC_LOGIN_VERSION = 1

# The certificate must live in **`us-east-1`** regardless of where the pool is.
# Cognito attaches it to a CloudFront distribution, which is global, and CloudFront
# reads certificates only from `us-east-1`. The pool happens to be there too, which
# is exactly what would make a region bug invisible in this account and fatal in
# another — so the constant is explicit rather than `naming.REGION`.
CERTIFICATE_REGION = "us-east-1"

# `rosettacloud.app`, which already resolves. A Cognito custom domain requires the
# *parent* domain to have an A record; without one `CreateUserPoolDomain` fails
# with a message about the domain rather than about DNS.
HOSTED_ZONE_ID = "Z08385903PVEGWMREU7F7"

# The fixed hosted-zone id every CloudFront alias target uses, in every account and
# every region. It is not the distribution's zone and it is not lookup-able — this
# literal is the documented value.
CLOUDFRONT_ALIAS_ZONE_ID = "Z2FDTNDATAQYW2"

# Grace's palette, named once. `HOSTED_UI_CSS` (v1) and `branding_settings()` (v2)
# both render from these, so the two sign-in pages cannot drift apart — and a test
# reads the same six values out of `web/app/globals.css`, which is the only check
# that catches the app itself moving.
PALETTE = {
    "paper": "#FAF9F7",
    "ink": "#1C1F23",
    "muted": "#6B7280",
    "rule": "#E5E3DF",
    "escalate": "#B4530A",
    "error": "#9B2C2C",
}

# The form's own surface: a plain white card on the paper ground. Kept *out* of
# `PALETTE` because it is not one of the dashboard's brand tokens —
# `web/app/globals.css` declares no `--color-white`, and the test that reads the
# six brand colours out of that stylesheet would fail on a value it never had.
FORM_WHITE = "#FFFFFF"

COLOURS = {**PALETTE, "white": FORM_WHITE}

# The claim `verifySession` requires. Declared in the pool schema, set on the
# user at creation, and asserted in the ID token — a user who signs in without
# exactly this value gets no session at all, not a lesser one.
ROLE_CLAIM = "custom:role"
ROLE_VALUE = "caseworker"

# A seeded account for the demo. The username is opaque on purpose: Cognito puts
# `sub` (a UUID) in the token and that is what reaches a decision row, but a
# username that looked like a person would invite someone to read it as one.
SEED_USERNAME = "caseworker-01"


# The **fallback** sign-in page's palette, so neither sign-in page looks like a
# different product than the dashboard it guards. Values are Grace's own, from
# `web/app/globals.css` — keep the two in step, or the login page drifts away
# from the app again.
#
# This is the **classic hosted UI** (`ManagedLoginVersion: 1`), which exposes a
# fixed set of `*-customizable` classes and nothing more: colours, the logo, and
# button styling. It is what the `grace-caseworkers` prefix domain still serves.
# **Do not delete it because managed login v2 shipped** — the prefix domain is the
# fallback if the custom domain has trouble, and an unstyled fallback is one a
# caseworker would not recognise. `branding_settings()` is the v2 equivalent, and
# both render from `PALETTE`.
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


def rgba(name: str, alpha: str = "ff") -> str:
    """A `PALETTE` colour in the form managed login wants: `rrggbbaa`, no `#`.

    Every colour in the branding document is eight hex digits with an alpha byte
    — measured off the Cognito-provided defaults, which are uniformly
    `0972d3ff`-shaped. A six-digit value is not rejected; it is *ignored*, and
    the component silently keeps Cognito's default colour. So the conversion
    lives in one function with one test rather than in twenty string literals,
    for the same reason `_most_recent` is imported rather than reimplemented.

    Raises on an unknown name so a typo is a provisioning failure rather than a
    component that quietly stays blue.
    """
    value = COLOURS[name]
    return f"{value.lstrip('#').lower()}{alpha}"


def login_logo_svg() -> str:
    """The dashboard's own masthead lockup, as the form's logo.

    `web/app/layout.tsx` sets "Grace" in semibold sans beside "caseworker queue"
    in mono, uppercase, letter-spaced. This is that lockup — the same two faces
    from `globals.css`, the same two colours — so the sign-in card opens with the
    exact wordmark the header shows a second later.

    The font stacks are the app's, not a web font: an SVG asset cannot load one,
    and both stacks resolve to whatever the caseworker's own system UI face is —
    which is precisely what the dashboard renders too.
    """
    sans = (
        "ui-sans-serif, system-ui, -apple-system, &apos;Segoe UI&apos;, "
        "Helvetica, Arial, sans-serif"
    )
    mono = (
        "ui-monospace, &apos;SF Mono&apos;, SFMono-Regular, Menlo, "
        "Consolas, monospace"
    )
    return (
        # 360×96 is 3.75:1. Managed login refuses a logo outside 1:1–4:1
        # outright — measured: a 360×54 lockup (6.67:1) came back
        # `Invalid file dimension` from `UpdateManagedLoginBranding`, so the
        # ratio is a validated constraint rather than a rendering hint.
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 96" '
        'width="360" height="96">'
        f'<text x="180" y="52" text-anchor="middle" font-family="{sans}" '
        f'font-size="31" font-weight="600" letter-spacing="-0.6" '
        f'fill="{PALETTE["ink"]}">Grace</text>'
        f'<text x="180" y="76" text-anchor="middle" font-family="{mono}" '
        f'font-size="11" letter-spacing="1.9" '
        f'fill="{PALETTE["muted"]}">CASEWORKER QUEUE</text>'
        "</svg>"
    )


def login_favicon_svg() -> str:
    """An ink tile with a paper G. The browser tab a caseworker signs in from
    should not be the only Cognito-blue surface left in the product."""
    sans = (
        "ui-sans-serif, system-ui, -apple-system, &apos;Segoe UI&apos;, "
        "Helvetica, Arial, sans-serif"
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" '
        'width="64" height="64">'
        f'<rect width="64" height="64" rx="14" fill="{PALETTE["ink"]}"/>'
        f'<text x="32" y="45" text-anchor="middle" font-family="{sans}" '
        f'font-size="38" font-weight="600" '
        f'fill="{PALETTE["paper"]}">G</text>'
        "</svg>"
    )


# The caseload the sign-in page is a door to. Twelve rows, nine quiet and three
# accented — the demo's own split, drawn at texture opacity.
BACKGROUND_ROWS = 12
BACKGROUND_ESCALATIONS = 3


def login_background_svg() -> str:
    """The page background: Grace's own case table, ghosted back to a texture.

    The complaint this answers is that the sign-in page had *no* background — a
    flat `#FAF9F7` field behind a white card, which reads as an unstyled page
    rather than a quiet one. Adding an unrelated stock image would have been the
    easy fix and the wrong one: this page guards a benefits caseload, and
    `branding_settings()` already turns Cognito's own illustration off for that
    reason.

    So the background *is* the after-login screen. Twelve rows of blocks in the
    dashboard's column rhythm, a hairline under each, and a status pill at the
    right of every one — nine in `muted`, three in `escalate`, which is the
    9-acted/3-escalated split the whole product is about. Every colour comes from
    `PALETTE`, so the test that reads `web/app/globals.css` covers this too.

    Drawn at 1600×1000 and sliced rather than stretched (`xMidYMid slice`), so
    the rows stay horizontal at any window shape. The centred form card covers
    the middle columns; what a caseworker actually sees is the ruled structure
    down both edges.
    """
    ink, muted, rule = PALETTE["ink"], PALETTE["muted"], PALETTE["escalate"]
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 1000" '
        'width="1600" height="1000" preserveAspectRatio="xMidYMid slice">',
        '<defs><linearGradient id="ground" x1="0" y1="0" x2="0.35" y2="1">',
        f'<stop offset="0" stop-color="{PALETTE["paper"]}"/>',
        f'<stop offset="1" stop-color="{PALETTE["rule"]}"/>',
        "</linearGradient></defs>",
        '<rect width="1600" height="1000" fill="url(#ground)"/>',
        # The masthead: a wordmark block and its label, over the same rule the
        # dashboard's header sits on.
        f'<rect x="150" y="60" width="92" height="15" rx="3" fill="{ink}" '
        'fill-opacity="0.16"/>',
        f'<rect x="256" y="64" width="132" height="10" rx="3" fill="{muted}" '
        'fill-opacity="0.13"/>',
        f'<rect x="150" y="112" width="1300" height="1" fill="{ink}" '
        'fill-opacity="0.10"/>',
    ]
    for index in range(BACKGROUND_ROWS):
        y = 168 + index * 62
        escalating = index >= BACKGROUND_ROWS - BACKGROUND_ESCALATIONS
        pill_colour = rule if escalating else muted
        pill_alpha = "0.18" if escalating else "0.09"
        parts += [
            f'<rect x="150" y="{y}" width="104" height="11" rx="3" fill="{ink}" '
            'fill-opacity="0.12"/>',
            f'<rect x="292" y="{y}" width="176" height="11" rx="3" '
            f'fill="{ink}" fill-opacity="0.06"/>',
            f'<rect x="508" y="{y}" width="112" height="11" rx="3" '
            f'fill="{ink}" fill-opacity="0.06"/>',
            f'<rect x="1246" y="{y - 7}" width="204" height="25" rx="12" '
            f'fill="{pill_colour}" fill-opacity="{pill_alpha}"/>',
            f'<rect x="150" y="{y + 32}" width="1300" height="1" fill="{ink}" '
            'fill-opacity="0.07"/>',
        ]
    parts.append("</svg>")
    return "".join(parts)


def branding_assets() -> list[dict]:
    """The three images managed login will actually serve.

    Colours alone left the page looking unstyled — `pageBackground` and
    `form.logo` are both *disabled by default*, so a settings document that only
    names colours has nothing to show. These are what `branding_settings()`
    switches on.

    `ColorMode` is `LIGHT` on every one, matching `colorSchemeMode` below. An
    asset uploaded under a mode the page never enters is stored and never
    rendered — the same shape of failure as a six-digit colour.
    """
    return [
        {
            "Category": "PAGE_BACKGROUND",
            "ColorMode": "LIGHT",
            "Extension": "SVG",
            "Bytes": login_background_svg().encode("utf-8"),
        },
        {
            "Category": "FORM_LOGO",
            "ColorMode": "LIGHT",
            "Extension": "SVG",
            "Bytes": login_logo_svg().encode("utf-8"),
        },
        {
            "Category": "FAVICON_SVG",
            "ColorMode": "LIGHT",
            "Extension": "SVG",
            "Bytes": login_favicon_svg().encode("utf-8"),
        },
    ]


def branding_settings() -> dict:
    """Managed login v2's settings document, in Grace's palette.

    v2 replaces v1's `*-customizable` CSS classes with a JSON document over three
    namespaces — `components` (the named parts of the page), `componentClasses`
    (things that recur, like every input), and `categories` (layout and which
    chrome is on). Anything omitted falls back to Cognito's default, which is why
    this is a partial document rather than the full 449-line merged one:
    re-stating a default would freeze it, and the only values worth pinning are
    the ones that are Grace's rather than AWS's.

    `colorSchemeMode: "LIGHT"` is set explicitly. The dashboard has one palette
    and no dark mode, so leaving the sign-in page browser-adaptive would give a
    caseworker on a dark-mode laptop a dark sign-in page in front of a light app.
    """
    return {
        "categories": {
            "global": {
                "colorSchemeMode": "LIGHT",
                "pageHeader": {"enabled": False},
                "pageFooter": {"enabled": False},
                "spacingDensity": "REGULAR",
            },
            "form": {
                "location": {"horizontal": "CENTER", "vertical": "CENTER"},
                "sessionTimerDisplay": "NONE",
                "languageSelector": {"enabled": False},
                # Cognito's stock illustration, on by default. It is a stock
                # illustration of nothing in particular, and this page guards a
                # benefits caseload.
                "displayGraphics": False,
            },
        },
        "components": {
            # **`enabled: True`, or the asset is stored and never drawn.** Both
            # of these default to off, which is why a settings document made
            # only of colours produced a page a caseworker described as having
            # no background at all: `#FAF9F7` behind a white card is a 2% step,
            # and the form's own identity was a heading Cognito wrote.
            "pageBackground": {
                "image": {"enabled": True},
                "lightMode": {"color": rgba("paper")},
            },
            "pageText": {
                "lightMode": {
                    "headingColor": rgba("ink"),
                    "bodyColor": rgba("ink"),
                    "descriptionColor": rgba("muted"),
                },
            },
            "form": {
                "lightMode": {
                    "backgroundColor": rgba("white"),
                    "borderColor": rgba("rule"),
                },
                "borderRadius": 8.0,
                # The card keeps a flat surface — the texture belongs to the
                # ground behind it, and two competing textures is how a quiet
                # page stops being quiet.
                "backgroundImage": {"enabled": False},
                # Grace's wordmark, inside the card and above the fields, so the
                # first thing on the page is the product rather than a generic
                # "Sign in". `IN` keeps it on the card's white; `OUT` would put
                # ink text over the ghosted table and lose contrast.
                "logo": {
                    "enabled": True,
                    "location": "CENTER",
                    "position": "TOP",
                    "formInclusion": "IN",
                },
            },
            # Cognito serves both types from one upload slot each; naming only
            # SVG here means the ICO slot is not advertised for an asset that
            # was never uploaded.
            "favicon": {"enabledTypes": ["SVG"]},
            "primaryButton": {
                "lightMode": {
                    "defaults": {
                        "backgroundColor": rgba("ink"),
                        "textColor": rgba("paper"),
                    },
                    # Escalate-orange on hover, the same accent the dashboard
                    # uses for the three households waiting on a human.
                    "hover": {
                        "backgroundColor": rgba("escalate"),
                        "textColor": rgba("white"),
                    },
                    "active": {
                        "backgroundColor": rgba("escalate"),
                        "textColor": rgba("white"),
                    },
                },
            },
            "secondaryButton": {
                "lightMode": {
                    "defaults": {
                        "backgroundColor": rgba("white"),
                        "borderColor": rgba("rule"),
                        "textColor": rgba("ink"),
                    },
                    "hover": {
                        "backgroundColor": rgba("paper"),
                        "borderColor": rgba("escalate"),
                        "textColor": rgba("escalate"),
                    },
                    "active": {
                        "backgroundColor": rgba("paper"),
                        "borderColor": rgba("escalate"),
                        "textColor": rgba("escalate"),
                    },
                },
            },
            "alert": {
                "lightMode": {
                    "error": {
                        "backgroundColor": rgba("white"),
                        "borderColor": rgba("error"),
                    },
                },
                "borderRadius": 6.0,
            },
        },
        "componentClasses": {
            # **`statusIndicator`, not just `alert`.** Measured off the served
            # theme stylesheet: `alert` sets the box around a failed sign-in, but
            # `--color-text-status-error` — the message a caseworker who mistyped
            # their password actually reads — comes from here. Styling only
            # `alert` leaves Cognito's own red on the one element the page exists
            # to show when something goes wrong.
            "statusIndicator": {
                "lightMode": {
                    "error": {
                        "backgroundColor": rgba("white"),
                        "borderColor": rgba("error"),
                        "indicatorColor": rgba("error"),
                    },
                },
            },
            # The "remember this device" checkbox. Left alone it is the last
            # AWS blue on an otherwise Grace-coloured page — verified by
            # grepping the served stylesheet for `0972d3` after a first pass
            # that omitted this and `secondaryButton`.
            "optionControls": {
                "lightMode": {
                    "defaults": {
                        "backgroundColor": rgba("white"),
                        "borderColor": rgba("rule"),
                    },
                    "selected": {
                        "backgroundColor": rgba("ink"),
                        "foregroundColor": rgba("paper"),
                    },
                },
            },
            "divider": {"lightMode": {"borderColor": rgba("rule")}},
            "inputDescription": {"lightMode": {"textColor": rgba("muted")}},
            "input": {
                "lightMode": {
                    "defaults": {
                        "backgroundColor": rgba("white"),
                        "borderColor": rgba("rule"),
                    },
                    "placeholderColor": rgba("muted"),
                },
                "borderRadius": 6.0,
            },
            "inputLabel": {"lightMode": {"textColor": rgba("ink")}},
            "focusState": {"lightMode": {"borderColor": rgba("escalate")}},
            "link": {
                "lightMode": {
                    "defaults": {"textColor": rgba("escalate")},
                    "hover": {"textColor": rgba("ink")},
                },
            },
            "buttons": {"borderRadius": 6.0},
        },
    }


def custom_domain_config(certificate_arn: str) -> dict:
    """The `CreateUserPoolDomain` arguments for the custom domain.

    Kept as data so the two facts a test can actually pin — that the branding
    version is 2 and that a certificate is attached — are checkable without AWS.
    """
    return {
        "Domain": CUSTOM_DOMAIN,
        "ManagedLoginVersion": MANAGED_LOGIN_VERSION,
        "CustomDomainConfig": {"CertificateArn": certificate_arn},
    }


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


def find_certificate(acm) -> dict | None:
    """The usable certificate for `CUSTOM_DOMAIN`, or `None`.

    Paginates, for the reason this file already pages twice: a missed page here
    does not fail, it *requests a second certificate* — and then a later run
    could attach whichever one it found first, leaving an unattached certificate
    behind that nobody can tell from the live one.

    Matches on the subject **and** on the subject-alternative names, because
    `auth.rosettacloud.app` may perfectly well arrive as a SAN on some later
    certificate for the parent domain. Matching the subject alone would then
    request a duplicate for a name that is already covered.

    An `ISSUED` certificate wins over a `PENDING_VALIDATION` one; anything
    `FAILED`, `EXPIRED`, `REVOKED`, or `INACTIVE` is ignored rather than
    returned, because `CreateUserPoolDomain` refuses those with an error that
    names the certificate and not its state.
    """
    usable: dict[str, dict] = {}
    token: str | None = None
    while True:
        kwargs: dict = {"MaxItems": 100}
        if token:
            kwargs["NextToken"] = token
        page = acm.list_certificates(**kwargs)
        for summary in page.get("CertificateSummaryList", []):
            names = {summary.get("DomainName")}
            names.update(summary.get("SubjectAlternativeNameSummaries") or [])
            if CUSTOM_DOMAIN not in names:
                continue
            status = str(summary.get("Status") or "")
            if status in {"ISSUED", "PENDING_VALIDATION"}:
                usable.setdefault(status, summary)
        token = page.get("NextToken")
        if not token:
            break
    return usable.get("ISSUED") or usable.get("PENDING_VALIDATION")


def validation_change_batch(record: dict) -> dict:
    """The Route 53 change that proves domain control to ACM."""
    return {
        "Comment": f"ACM DNS validation for {CUSTOM_DOMAIN}",
        "Changes": [
            {
                # UPSERT, not CREATE: re-running must converge rather than fail
                # on a record a previous run already wrote.
                "Action": "UPSERT",
                "ResourceRecordSet": {
                    "Name": record["Name"],
                    "Type": record["Type"],
                    "TTL": 300,
                    "ResourceRecords": [{"Value": record["Value"]}],
                },
            }
        ],
    }


def ensure_certificate(acm=None, route53=None, poll_seconds: float = 5.0) -> str:
    """Find or request the `auth.rosettacloud.app` certificate. Returns its ARN.

    Only returns on `ISSUED`. **Do not relax this to accept
    `PENDING_VALIDATION`:** `CreateUserPoolDomain` refuses an unissued
    certificate, and the error it raises names the certificate rather than the
    validation state, so the failure reads as "wrong certificate" and sends the
    next person looking in the wrong place.
    """
    acm = acm or boto3.client("acm", region_name=CERTIFICATE_REGION)
    route53 = route53 or boto3.client("route53")

    found = find_certificate(acm)
    if found is None:
        arn = acm.request_certificate(
            DomainName=CUSTOM_DOMAIN,
            ValidationMethod="DNS",
            Tags=[{"Key": k, "Value": v} for k, v in naming.TAGS.items()],
        )["CertificateArn"]
    else:
        arn = found["CertificateArn"]
        if found.get("Status") == "ISSUED":
            return arn

    # ACM computes the validation record asynchronously, so a `describe` issued
    # immediately after `request` comes back with `DomainValidationOptions`
    # present and `ResourceRecord` absent. Poll for it rather than reading a
    # `KeyError` as a service failure.
    record: dict | None = None
    for _ in range(24):
        detail = acm.describe_certificate(CertificateArn=arn)["Certificate"]
        if detail.get("Status") == "ISSUED":
            return arn
        for option in detail.get("DomainValidationOptions", []):
            if option.get("ResourceRecord"):
                record = option["ResourceRecord"]
                break
        if record:
            break
        time.sleep(poll_seconds)
    if record is None:
        raise RuntimeError(
            f"ACM never published a validation record for {CUSTOM_DOMAIN} "
            f"({arn}); nothing to write to Route 53."
        )

    route53.change_resource_record_sets(
        HostedZoneId=HOSTED_ZONE_ID, ChangeBatch=validation_change_batch(record)
    )
    acm.get_waiter("certificate_validated").wait(CertificateArn=arn)
    return arn


def ensure_custom_domain(client, pool_id: str, certificate_arn: str) -> str:
    """Create-or-converge `auth.rosettacloud.app`. Returns the alias target.

    The alias target is a CloudFront hostname; `Step 3` points DNS at it. Read
    back from `DescribeUserPoolDomain` on the converge path rather than
    remembered, because `UpdateUserPoolDomain` does not return it on every path
    and a stale value would produce an alias record pointing at a distribution
    that is no longer the domain's.
    """
    config = custom_domain_config(certificate_arn)
    existing = _describe_domain(client, CUSTOM_DOMAIN)

    if existing.get("UserPoolId"):
        # **Refuse rather than converge if it belongs to somewhere else.** This
        # account holds `astrolabe-paper-auth` and `rosettaclaw-live-auth`, and
        # `UpdateUserPoolDomain` against another project's pool would repoint
        # that project's sign-in at Grace's certificate.
        if existing["UserPoolId"] != pool_id:
            raise RuntimeError(
                f"{CUSTOM_DOMAIN} is already attached to user pool "
                f"{existing['UserPoolId']}, not {pool_id}."
            )
        client.update_user_pool_domain(UserPoolId=pool_id, **config)
        existing = _describe_domain(client, CUSTOM_DOMAIN)
        return str(existing["CloudFrontDistribution"])

    return str(
        client.create_user_pool_domain(UserPoolId=pool_id, **config)["CloudFrontDomain"]
    )


def _describe_domain(client, domain: str) -> dict:
    """`DescribeUserPoolDomain`, flattened to a plain dict.

    Cognito reports an absent domain two different ways depending on the call —
    an empty `DomainDescription` on some paths and `ResourceNotFoundException`
    on others — so both mean the same thing here.
    """
    try:
        return dict(client.describe_user_pool_domain(Domain=domain).get(
            "DomainDescription"
        ) or {})
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            return {}
        raise


def alias_change_batch(alias_target: str) -> dict:
    """The Route 53 change that points `auth.rosettacloud.app` at CloudFront.

    An **A alias**, not a CNAME: `auth.` is a subdomain so a CNAME would work,
    but an alias costs nothing to resolve and follows the distribution if its
    address changes.

    `HostedZoneId` here is CloudFront's fixed global zone, never the zone the
    record lives in. Putting `HOSTED_ZONE_ID` in both places is the mistake this
    function exists to make impossible to write twice.
    """
    return {
        "Comment": f"Cognito managed login for {CUSTOM_DOMAIN}",
        "Changes": [
            {
                "Action": "UPSERT",
                "ResourceRecordSet": {
                    "Name": f"{CUSTOM_DOMAIN}.",
                    "Type": "A",
                    "AliasTarget": {
                        "HostedZoneId": CLOUDFRONT_ALIAS_ZONE_ID,
                        "DNSName": alias_target,
                        # Cognito's distribution publishes no health check, and
                        # an alias that evaluated one would fail closed to
                        # NXDOMAIN — i.e. no sign-in page at all.
                        "EvaluateTargetHealth": False,
                    },
                },
            }
        ],
    }


def ensure_dns_alias(route53, alias_target: str) -> None:
    """Point the custom domain at its CloudFront distribution."""
    route53.change_resource_record_sets(
        HostedZoneId=HOSTED_ZONE_ID, ChangeBatch=alias_change_batch(alias_target)
    )


def ensure_branding(client, pool_id: str, client_id: str) -> str:
    """Apply Grace's palette to managed login. Returns the branding style id.

    A branding style is bound to an **app client**, not to a domain — so this one
    style is what the v2 custom domain renders, while the v1 prefix domain keeps
    reading `HOSTED_UI_CSS`. Both come from `PALETTE`, which is what keeps the
    fallback from looking like a different product.

    `UseCognitoProvidedValues` is never passed: it is mutually exclusive with
    `Settings`, and passing it would silently mean "AWS blue".
    """
    settings = branding_settings()
    assets = branding_assets()
    try:
        existing = client.describe_managed_login_branding_by_client(
            UserPoolId=pool_id, ClientId=client_id
        )["ManagedLoginBranding"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        return str(
            client.create_managed_login_branding(
                UserPoolId=pool_id, ClientId=client_id,
                Settings=settings, Assets=assets,
            )["ManagedLoginBranding"]["ManagedLoginBrandingId"]
        )

    branding_id = str(existing["ManagedLoginBrandingId"])
    # `Assets` is sent on the converge path too. `branding_settings()` switches
    # the background and the logo on, so a run that updated only `Settings`
    # would leave the page asking for two images that were never uploaded —
    # enabled and empty, which is worse than the flat page it replaced.
    client.update_managed_login_branding(
        UserPoolId=pool_id, ManagedLoginBrandingId=branding_id,
        Settings=settings, Assets=assets,
    )
    return branding_id


def provision(
    client=None,
    callback_urls: list[str] | None = None,
    acm=None,
    route53=None,
) -> dict:
    """Create the pool, client, both domains, and one caseworker. Idempotent.

    Returns the values the dashboard needs as environment variables. `domain` is
    the **custom** domain, because that is what `COGNITO_DOMAIN` should be — and
    `provision_amplify` reads exactly that key, so the switch propagates without
    a second module having to know the hostname. `fallback_domain` is the prefix
    domain, kept so the one edit that restores sign-in during a CloudFront
    problem is visible rather than something to go and look up.
    """
    client = client or boto3.client("cognito-idp", region_name=naming.REGION)
    acm = acm or boto3.client("acm", region_name=CERTIFICATE_REGION)
    route53 = route53 or boto3.client("route53")
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

    # The prefix hosted UI domain. One API call, and it saves building sign-in
    # forms. **Still created, and never deleted** — see `CUSTOM_DOMAIN`: it is
    # the fallback, and a pool may hold one of each.
    try:
        client.create_user_pool_domain(Domain=DOMAIN_PREFIX, UserPoolId=pool_id)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {
            "InvalidParameterException",  # already exists on this pool
            "AliasExistsException",
        }:
            raise

    # And Grace's own. Three steps that must happen in this order: a certificate
    # that is already `ISSUED`, then the domain (which mints a CloudFront
    # distribution), then DNS pointing at that distribution. Reversing any two
    # produces an error naming the wrong thing.
    certificate_arn = ensure_certificate(acm, route53)
    alias_target = ensure_custom_domain(client, pool_id, certificate_arn)
    ensure_dns_alias(route53, alias_target)

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

    # And managed login's, for the same reason and with the same discipline: not
    # wrapped in a `try`, because a run that reports success while the sign-in
    # page is AWS blue is a run nobody re-does.
    ensure_branding(client, pool_id, client_id)

    return {
        "pool_id": pool_id,
        "client_id": client_id,
        "domain": f"https://{CUSTOM_DOMAIN}",
        "fallback_domain": (
            f"https://{DOMAIN_PREFIX}.auth.{naming.REGION}.amazoncognito.com"
        ),
        # **Unchanged by the domain switch, and that is the point.** The issuer
        # is the pool's API URL, so the JWKS `verifySession` fetches lives on
        # `cognito-idp.<region>.amazonaws.com` rather than on either sign-in
        # host — a token minted through the new domain verifies against exactly
        # the same key set as one minted through the old. Measured live rather
        # than reasoned about: the `iss` claim in a real ID token obtained via
        # `auth.rosettacloud.app` is this string.
        "issuer": f"https://cognito-idp.{naming.REGION}.amazonaws.com/{pool_id}",
    }


if __name__ == "__main__":
    for key, value in provision().items():
        print(f"{key}: {value}")
