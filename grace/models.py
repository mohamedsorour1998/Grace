"""Amazon Nova model assignments.

All Grace agents run on Nova via Bedrock: no third-party LLMs in the request
path. Every profile below was verified ACTIVE in us-east-1.

Model IDs live only here. Call sites reference a *role* (`nova("verifier")`),
never an ID string, so switching a model is a one-line edit and the "Nova only"
constraint is checkable in one file rather than grepped across the codebase.

The advocate, verifier, and referee deliberately run on THREE DIFFERENT models.
Two instances of the same model agreeing proves nothing, and nothing should
referee its own argument.
"""

from __future__ import annotations

from strands.models.bedrock import BedrockModel

REGION = "us-east-1"

# Argues the family qualifies. Nova 2 Lite reasons well enough to make the case
# and is cheap enough to run on every ambiguous household.
ADVOCATE = "global.amazon.nova-2-lite-v1:0"
# Adversarial check — a different model than the advocate, on purpose, and the
# strongest one available. Nova Pro because nova-premier-v1:0 is Legacy and
# blocked by the provider (verified against the live account); there is no
# nova-2-pro.
VERIFIER = "us.amazon.nova-pro-v1:0"
# Tie-break: a narrow AMBIGUOUS/CLEAR call. Distinct from both debaters, so no
# model ever referees its own argument.
REFEREE = "us.amazon.nova-micro-v1:0"
# High volume, cheap; `global.` for cross-region throttle resilience.
CLASSIFIER = "global.amazon.nova-2-lite-v1:0"
# Short multilingual SMS.
OUTREACH = "us.amazon.nova-2-lite-v1:0"
# Must be genuinely clear to a human under time pressure.
BRIEFER = "us.amazon.nova-pro-v1:0"
# Bounded-retry output review.
JUDGE = "us.amazon.nova-2-lite-v1:0"

# Never assign this to any role. Under test, told "NEVER submit a renewal when a
# required document is missing", nova-lite-v1:0 read the case, saw
# proof_of_income was missing, filed the renewal anyway, and then said "I made
# the same mistake again." Nova Pro, Nova 2 Lite, and Nova Micro all correctly
# escalated on the identical prompt. Recorded as a constant so a test can assert
# no role uses it, rather than leaving the finding in a comment nobody greps.
BANNED_MODEL_IDS = frozenset({"us.amazon.nova-lite-v1:0", "global.amazon.nova-lite-v1:0"})

_ROLES = {
    "advocate": ADVOCATE,
    "verifier": VERIFIER,
    "referee": REFEREE,
    "classifier": CLASSIFIER,
    "outreach": OUTREACH,
    "briefer": BRIEFER,
    "judge": JUDGE,
}

# The three roles that must never share a model — see the module docstring and
# CLAUDE.md hard rule 2. Named here so the constraint is one list a test reads,
# not three separate assertions that can drift out of agreement.
ADVERSARIAL_ROLES = ("advocate", "verifier", "referee")


def nova(role: str, *, temperature: float = 0.2) -> BedrockModel:
    """Build a BedrockModel for a named Grace role.

    Raises on an unknown role rather than falling back to a default: a typo
    must not silently route a verifier to a cheap model. A verifier that is
    quietly a Nova Micro still returns confident-looking output, so the failure
    would be invisible in every place it matters.
    """
    if role not in _ROLES:
        raise KeyError(f"Unknown Grace role: {role!r}. Known: {sorted(_ROLES)}")
    return BedrockModel(
        model_id=_ROLES[role],
        region_name=REGION,
        temperature=temperature,
    )
