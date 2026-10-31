# Security

## Reporting a vulnerability

Please report security issues privately, through GitHub's
[private vulnerability reporting](https://github.com/mohamedsorour1998/Grace/security/advisories/new)
for this repository. Do not open a public issue for a security problem.

Include what you found, how to reproduce it, and what you think the impact is. Reports are
acknowledged as soon as they are read, and the fix is discussed with you before anything is
published.

## What matters most here

Grace decides whether a family's benefit renewal is filed without a human. The issues that matter
most are the ones that would let it act when it should have escalated, or expose a household:

- **A bypass of the escalation boundary**: a path to `submit_renewal` for a case that
  `grace/authority.py` would escalate. That includes through a caseworker approval, a resumed
  run, an invocation payload, or a tool argument.
- **Household identity reaching a model or a log**: a name, phone number, or address in a tool's
  returned text, a span attribute, an escalation reason, or any log line.
- **The dashboard's authentication**: a page or route that serves case data, or records a
  decision, without a verified Cognito ID token carrying `custom:role = caseworker`.
- **The span-redaction guard**: a way to run the agent with
  `gen_ai_unredacted_attributes` absent or non-empty.

## Data

Every household in this repository is synthetic. Phone numbers use the reserved `+1555` range and
names are fictional. **Never send real household data** in a report, an issue, a pull request, or a
test fixture.

## Supported versions

Only `main` is supported. Fixes are not backported to tagged releases.
