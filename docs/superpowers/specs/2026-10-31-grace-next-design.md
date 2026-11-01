# Grace after the hackathon: design

Date: 2026-10-31. Status: agreed with the owner in conversation on 2026-10-31; three plans implement it.

## Why

Grace was built for a hackathon and submitted on 2026-09-14. It runs on AWS every day and its two
safety invariants have held on every run. The repository is still shaped like a hackathon entry:

- `CLAUDE.md` is 2,114 lines, most of it per-task lessons;
- `docs/` is flat, mixing runbooks, evidence and submission material;
- the README ends with a submission checklist.

The project's next life is as a maintained open-source agent that anyone can deploy into their own
AWS account and that other agents can call. That needs three changes:

1. a repository a newcomer can find their way around;
2. configuration instead of constants, plus a standard agent-to-agent (A2A) interface;
3. CI that proves each change.

The dashboard also moves to its long-term address.

## Goals

1. **Baseline.** A reproducible environment from `uv.lock`, measured test counts for both suites, and a
   tagged release of the state that was public at the hackathon deadline.
2. **Restructure.**
   - `CLAUDE.md` cut to about 300 lines, with the lessons moved verbatim to `docs/lessons/`;
   - `docs/` split into `runbooks/`, `evidence/`, `hackathon/` and `lessons/`;
   - a product README;
   - `SECURITY.md` and `CONTRIBUTING.md`;
   - GitHub repository settings to match.
3. **Fixes** from the 2026-10-31 full read of the codebase, each one test-first.
4. **Deploy anywhere.** The table name and region come from the environment. Regions outside
   `us-east-1` and `us-west-2` refuse to start. `seed_cases --file` loads households from any file.
5. **A2A server.** An A2A server on port 9000 runs next to the HTTP runtime. It serves an agent card,
   `/ping`, and JSON-RPC `message/send`.
6. **CI.** It runs the Python tests, the four web gates, an arm64 build of the A2A image with a smoke
   run, a vulnerability scan, and a content check.
7. **New address.** The dashboard moves to `https://grace.rosettacloud.ai` and sign-in moves to
   `https://auth.rosettacloud.ai`. Both are DNS-only records in Cloudflare.

## Non-goals

- The e-scrap domain. It gets its own brainstorm, spec and plan, approved before any code.
- Deploying the A2A image to Runtime. CI builds and scans it, and the deployed HTTP runtime is unchanged.
- An AgentCore Gateway, and traces in Transaction Search. Both stay as documented in `CLAUDE.md`.
- Retiring the old `.app` addresses. The owner decides that after the move.

## Anti-goals: failure even if everything technically works

- Any change that weakens the escalation boundary: capability absence, identity bound at
  construction, and the pure gate.
- Public text naming an organisation, a programme, a commercial channel, or anything about selling.
  Domain names under `rosettacloud.ai` and `rosettacloud.app` are allowed; prose naming the company is not.
- Rewriting history. `docs/superpowers/specs/` and `docs/superpowers/plans/` stay as dated snapshots.
- An A2A caller gaining a capability that a direct invocation does not have. The worst case would be a
  path that carries a caseworker's approval.

## Constraints

- Grace's hard rules, unchanged (`CLAUDE.md`, "Hard rules"):
  - Nova only, with three different models for advocate, verifier and referee;
  - synthetic data;
  - `grace/authority.py` pure;
  - the span-redaction token present and empty;
  - no household identity anywhere a model or a log can reach.
- Dependencies stay minimal.
  - The only new dependency is `bedrock-agentcore[a2a]`, which brings `a2a-sdk[http-server]`.
  - Its measured transitive cost goes into `CLAUDE.md`.
  - Every new pin is a release at least two weeks old (on or before 2026-10-17).
- SDK signatures are verified by introspection. Tests are watched failing before the code is written.
- A fact the work depends on is either verified here or cited from a source.
- Outward-facing actions need the owner's explicit go at the moment of execution. These are:
  - pushes, tags and releases;
  - GitHub settings;
  - AWS changes;
  - Cloudflare DNS changes.

## Decisions

Calls made by Claude rather than the owner are marked *(Claude's call)*.

| Topic | Decision |
|---|---|
| Table variable | `GRACE_TABLE_NAME` everywhere, matching the dashboard and Amplify *(Claude's call)* |
| Region check | `naming.require_supported_region()` runs at import in both entrypoints, after the redaction check |
| A2A server | wraps `bedrock_agentcore.runtime.build_a2a_app` / `serve_a2a` from bedrock-agentcore **1.23.0** (released 2026-09-11), the newest release compatible with strands-agents 1.54.0: 1.23.1 and later import `strands.experimental.bidi` and declare `strands-agents>=1.56.0` |
| A2A SDK | `a2a-sdk[http-server]==0.3.26`, the last 0.3.x, released 2026-04-09 (`bedrock-agentcore[a2a]` requires `>=0.3,<0.4`) |
| Packaging | an optional `a2a` extra; the HTTP image does not install it *(Claude's call)* |
| A2A image | a separate `Dockerfile.a2a`, plus a test that its `ENV` and `USER` lines match `Dockerfile` *(Claude's call)* |
| A2A reply | one agent `Message`: a data part holding the outcome, and a text part with status and case id only *(Claude's call)* |
| A2A payload | allowlist of `case_id` and `today`; any other key is refused, `caseworker_approved` included *(Claude's call)* |
| Live tests | a registered `live` marker, deselected in CI *(Claude's call)* |
| Workflow | feature branches merged by pull request; `main` protected |
| GitHub | Wiki and Projects off; Dependabot security alerts on, version-update PRs off; private vulnerability reporting on |
| Content check | a script reads the forbidden-word list from outside the repository (a file locally, a secret in CI), so the list is never committed *(Claude's call)* |
| Domains | the new addresses are added beside the old ones; Cloudflare records are DNS-only (not proxied) |
| DNS in code | provisioning emits the records it needs; Route 53 writes them directly, any other provider receives them as a list *(Claude's call)* |
| History | dated specs and plans stay as they are, under a note that their paths are as of their date |

## Order

1. **Plan A**: `docs/superpowers/plans/2026-10-31-grace-baseline-restructure.md` (Phases 0–2).
2. **Plan B**: `docs/superpowers/plans/2026-10-31-grace-deploy-anywhere-a2a-ci.md` (Phase 3).
3. **Plan C**: `docs/superpowers/plans/2026-10-31-grace-domain-move.md` (Phase 4).

Plan C depends only on Plan A. Plan B's branch-protection step needs Plan B's CI to exist.

## Facts this design relies on, with sources

- **AgentCore A2A contract:** linux/arm64; `0.0.0.0:9000`; JSON-RPC 2.0 on `POST /`; `GET /ping`;
  `GET /.well-known/agent-card.json`; errors returned as JSON-RPC errors with HTTP 200. Source: the
  Bedrock AgentCore Runtime service-contract documentation, and `bedrock_agentcore/runtime/a2a.py` in
  1.23.0 (`A2A_CONTRACT_PORT = 9000`, `AGENTCORE_RUNTIME_URL` overrides the card URL).
- **Cognito custom domains**
  (https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-add-custom-domain.html):
  - the parent domain must resolve to an A record;
  - the ACM certificate must be in us-east-1;
  - a pool has at most one custom domain, so the old one is deleted before the new one is created;
  - a prefix domain can coexist with it;
  - with a non-Route 53 provider, the alias target is added in that provider's DNS.
- **Amplify with a third-party DNS provider**
  (https://docs.aws.amazon.com/amplify/latest/userguide/to-add-a-custom-domain-managed-by-a-third-party-dns-provider.html,
  and the Cloudflare page,
  https://docs.aws.amazon.com/amplify/latest/userguide/to-add-a-custom-domain-managed-by-cloudflare.html):
  - two CNAMEs are needed, one for certificate validation and one from the subdomain to CloudFront;
  - setup can take up to 48 hours;
  - Amplify cannot renew its certificate if the validation CNAME is changed or proxied.
- **Cloudflare**
  (https://developers.cloudflare.com/dns/manage-dns-records/reference/vendor-specific-records/):
  validation and CloudFront CNAMEs must be DNS-only.
- **The `rosettacloud.ai` zone, read through the Cloudflare API on 2026-10-31:**
  - Free plan, active;
  - no `grace` or `auth` records;
  - no CAA records;
  - the apex is a CNAME to CloudFront, and the zone flattens CNAMEs at the root, so the apex answers A queries.
