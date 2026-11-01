# Grace domain move: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Caseworkers sign in at `https://auth.rosettacloud.ai` and use the dashboard at
`https://grace.rosettacloud.ai`. Both are served through DNS-only records in Cloudflare. Sign-in never
goes down during the move, and the old `.app` addresses keep working until the owner retires them.

**Architecture:** The provisioning scripts stop assuming Route 53. They say which DNS records they need,
as `infra.dns.DnsRecord`.

- Route 53 can still write those records directly.
- For any other provider, the records are returned to the operator. In this plan the operator writes
  them through the Cloudflare API, using the Cloudflare MCP.

The move runs in two operations, in this order:

1. **Sign-in.** A Cognito pool holds at most one custom domain, so the dashboard first falls back to the
   pool's prefix domain. Then the old custom domain is deleted, the new one is created, and the
   dashboard is pointed at it.
2. **Dashboard address.** An Amplify domain association adds only the `grace` subdomain. Both addresses
   stay registered as Cognito callbacks.

The ID token's issuer is the `cognito-idp` URL, not the hosted domain, so `verifySession` does not change.

**Tech Stack:** Python 3.12, boto3 (`cognito-idp`, `acm` in us-east-1, `amplify`, `route53`), the
Cloudflare API v4 through the Cloudflare MCP, `dig`, `curl`.

**Spec:** `docs/superpowers/specs/2026-10-31-grace-next-design.md`

## Global Constraints

- **Every Cloudflare record this plan creates:**
  - is a `CNAME` with `"proxied": false` (DNS-only);
  - carries a `comment` beginning `Grace:`;
  - is verified by reading it back.
- **This plan never modifies or deletes an existing record in the `rosettacloud.ai` zone.** Look up
  the zone id at run time with `GET /zones?name=rosettacloud.ai`.
- **Certificates for Cognito live in `us-east-1`.**
- **Amplify associates exactly one subdomain, `grace`.** Never the apex and never `www`: both belong to
  another CloudFront distribution.
- **Identifiers:**
  - Cognito pool `us-east-1_HXs3b0APR`, prefix domain `grace-caseworkers`;
  - Amplify app `dbi97xicbjbv8`, branch `main`.
- **Outward-facing steps are marked (owner go).** That covers every AWS write, every Cloudflare write,
  every redeploy, the GitHub homepage, pushes and pull requests. Ask at each one.
- **Rollback for sign-in at any point:** set `COGNITO_DOMAIN` back to
  `https://grace-caseworkers.auth.us-east-1.amazoncognito.com` and redeploy (Task 2's
  `set_environment`).
- **Public content:** generic wording (see Plan A). Hostnames under `rosettacloud.ai` and
  `rosettacloud.app` are allowed.
- **Branch:** `domain-move`, from `main` after Plan A. Tasks 1–2 land as one pull request before
  Tasks 3–4 run. Task 5 is a second pull request.

## Review Focus

1. **A record is created proxied** (orange cloud). Validation still passes today, but Amplify cannot
   renew its certificate later, and nothing reports it. Every write in Tasks 3–4 sends
   `"proxied": false`, and its read-back step asserts it.
2. **An Amplify association that claims the apex or `www`** would collide with the site already served
   there. Pinned by Task 2's `test_the_apex_and_www_are_never_associated`.
3. **The old Cognito domain is deleted while the dashboard still sends caseworkers to it**, which is a
   sign-in outage. Pinned by Task 1's `test_a_pool_with_another_custom_domain_is_refused_not_replaced`,
   and by Task 3's gate: the prefix-domain fallback is live and verified before the delete.
4. **The Cognito callbacks lose an address.** This can happen two ways:
   - the old dashboard address is dropped when `DASHBOARD_URL` changes;
   - every production address is dropped when the sign-in script runs with no arguments. Today's
     `__main__` calls `provision()`, which converges the client to `http://localhost:3000` alone.

   Pinned by Task 2's `test_the_previous_address_stays_registered_during_the_move` and Task 1's
   `test_a_run_without_callbacks_keeps_the_live_ones`.
5. **A validation record name already exists in the zone.** ACM can reuse a validation CNAME for a name
   it has validated before in the same account. Before each create, Tasks 3–4 look the name up:
   - identical content: skip it;
   - different content: stop and report. Never overwrite.

---

### Task 1: Sign-in provisioning that works with any DNS provider

**Files:**
- Create: `infra/dns.py`
- Modify: `infra/provision_cognito.py`:
  - `CUSTOM_DOMAIN`, `HOSTED_ZONE_ID`;
  - `ensure_certificate` (around line 714);
  - `ensure_custom_domain` (767);
  - `alias_change_batch` and `ensure_dns_alias` (815–853);
  - `provision` (894), and the CLI block.
- Test: `tests/test_dns.py`, `tests/test_infra_cognito.py`

**Interfaces:**
- Produces, in `infra/dns.py`:
  - `@dataclass(frozen=True) class DnsRecord: name: str; type: str; value: str`. Names and values carry
    no trailing dot.
  - `class DnsWriter(Protocol): def upsert(self, record: DnsRecord) -> None`.
  - `class Route53Writer(route53, zone_id: str)`. It `UPSERT`s a `CNAME` with TTL 300.
  - `class RecordList`. Its `.records: list[DnsRecord]` collects what it was given, for a provider this
    code does not write to.
  - `class PendingDns(Exception)`. Its `.records: list[DnsRecord]` holds the records the operator still
    has to create.
- Produces, in `infra/provision_cognito.py`:
  - `CUSTOM_DOMAIN = "auth.rosettacloud.ai"`. `HOSTED_ZONE_ID` is removed, because a `Route53Writer`
    is given its zone explicitly.
  - `ensure_certificate(acm, dns: DnsWriter, domain: str = CUSTOM_DOMAIN, wait: bool = True) -> str`.
    - It returns the ARN once the certificate is `ISSUED`.
    - Otherwise it upserts the validation record.
    - With `wait=False` it raises `PendingDns([record])`; with `wait=True` it blocks on ACM's waiter.
  - `ensure_custom_domain(client, pool_id: str, certificate_arn: str, domain: str = CUSTOM_DOMAIN) -> str`.
    - It keeps today's create-or-converge behaviour.
    - New: if `describe_user_pool` reports a different `CustomDomain` on this pool, it raises
      `RuntimeError` naming both domains and saying to delete the old one first. It never deletes.
  - `alias_record(alias_target: str, domain: str = CUSTOM_DOMAIN) -> DnsRecord`. The record is a
    `CNAME` from `domain` to `alias_target`.
  - `provision(client=None, callback_urls=None, acm=None, dns: DnsWriter | None = None, wait: bool = True) -> dict`.
    - The default `dns` is `RecordList()`.
    - The result gains `"dns_records": [asdict(r) for r in ...]`.
    - **`callback_urls=None` now means "keep the existing client's callback URLs"**, read with
      `describe_user_pool_client`. `http://localhost:3000/api/auth/callback` is used only when the
      client is being created. Logout URLs are still derived from the callbacks.
  - CLI: `python -m infra.provision_cognito [--no-wait]`.
    - It prints every entry in `dns_records`, one per line as `name CNAME value`.
    - It exits 3 on `PendingDns`, otherwise 0.

- [ ] **Step 1: Write the failing tests.**

  ```python
  def test_the_custom_domain_is_the_new_address():
      assert provision_cognito.CUSTOM_DOMAIN == "auth.rosettacloud.ai"

  def test_a_certificate_pending_dns_names_the_record_to_create():
      acm = FakeAcm(status="PENDING_VALIDATION", record={"Name": "_x.auth.rosettacloud.ai.", "Type": "CNAME", "Value": "_y.acm-validations.aws."})
      dns = RecordList()
      with pytest.raises(PendingDns) as exc:
          provision_cognito.ensure_certificate(acm, dns, wait=False)
      assert exc.value.records == [DnsRecord("_x.auth.rosettacloud.ai", "CNAME", "_y.acm-validations.aws")]
      assert dns.records == exc.value.records

  def test_route53_writer_upserts_a_cname(): ...            # one change, Action UPSERT, TTL 300, names end with "." in the batch only
  def test_the_alias_is_a_cname_to_the_cloudfront_target():
      assert provision_cognito.alias_record("d1.cloudfront.net") == DnsRecord("auth.rosettacloud.ai", "CNAME", "d1.cloudfront.net")
  def test_a_pool_with_another_custom_domain_is_refused_not_replaced(): ...
      # describe_user_pool -> CustomDomain "auth.rosettacloud.app"; ensure_custom_domain raises naming both; no create/delete/update called
  def test_an_issued_certificate_writes_no_record(): ...
  def test_a_run_without_callbacks_keeps_the_live_ones():
      # existing client has CallbackURLs [".../grace.rosettacloud.app/api/auth/callback"];
      # provision(callback_urls=None, ...) -> update_user_pool_client sent those same URLs, never localhost
  ```

  Update the existing tests that name `auth.rosettacloud.app` or `HOSTED_ZONE_ID` so they go through
  `Route53Writer` with an explicit zone id. Keep each test's assertion; change only its plumbing.
- [ ] **Step 2: Run them.**
  Run: `.venv/bin/python -m pytest tests/test_dns.py tests/test_infra_cognito.py`
  Expected: the new tests fail.
- [ ] **Step 3: Implement.** Keep the comments that explain the ordering (an `ISSUED` certificate,
  then the domain, then DNS) and the cross-project refusal. Delete the comments that describe a
  Route 53 zone as the only possibility.
- [ ] **Step 4: Run.**
  Run: `.venv/bin/python -m pytest -m "not live"`
  Expected: all pass.
- [ ] **Step 5: Commit.**
  `git commit -am "feat: sign-in provisioning names the DNS records it needs, so any DNS provider works"`

### Task 2: The dashboard's domain and environment

**Files:**
- Modify: `infra/provision_amplify.py`:
  - add `ensure_domain`, `parse_amplify_record`, `set_environment`;
  - give `provision` two new parameters.
- Test: `tests/test_infra_amplify.py`

**Interfaces:**
- Consumes: `DnsRecord` (Task 1).
- Produces:
  - `parse_amplify_record(text: str, domain: str) -> DnsRecord`. It accepts the two shapes Amplify
    documents:
    - `"<name> CNAME <value>"` with a fully qualified name;
    - `"<prefix> CNAME <value>"` / `" CNAME <value>"` relative to `domain`.

    It strips trailing dots, and raises `ValueError` on anything else.
  - `ensure_domain(client, app_id: str, domain: str = "rosettacloud.ai", prefix: str = "grace", branch: str = BRANCH) -> list[DnsRecord]`.
    - It refuses a `prefix` of `""` or `"www"` (`ValueError`).
    - It creates the association when none exists, with:
      - `subDomainSettings=[{"prefix": prefix, "branchName": branch}]`;
      - `enableAutoSubDomain=False`;
      - `certificateSettings={"type": "AMPLIFY_MANAGED"}`.
    - It refuses (`RuntimeError`) an existing association whose subdomains differ.
    - It polls `get_domain_association` until both the certificate-verification record and the
      subdomain record are present: at most 24 polls, 5 seconds apart, then `RuntimeError`.
    - It returns `[verification, subdomain]`.
  - `set_environment(client, app_id: str, values: dict[str, str], branch: str = BRANCH) -> str`.
    - Keys must be in `RUNTIME_VARIABLES` and values must be non-blank, or it raises `ValueError`.
    - It reads the app's variables fresh, merges `values` over them (preserving `PRESERVED_PREFIXES`
      keys, as `merged_environment` does), and calls `update_app(environmentVariables=...)`.
    - It then calls `start_job(jobType="RELEASE")` and returns the job id.
  - `provision(..., dashboard_url: str | None = None, also_allow: tuple[str, ...] = ())`.
    - `DASHBOARD_URL` becomes `dashboard_url` when given; otherwise it is today's preserved value.
    - The Cognito callbacks become `callback_urls(url, *also_allow, amplify_url)`.

- [ ] **Step 1: Write the failing tests.**

  ```python
  def test_only_the_grace_subdomain_is_associated(): ...      # create_domain_association kwargs exactly as above
  def test_the_apex_and_www_are_never_associated():
      for prefix in ("", "www"):
          with pytest.raises(ValueError):
              provision_amplify.ensure_domain(FakeAmplify(), "app", prefix=prefix)
  def test_both_cnames_come_back_as_records(): ...            # verification + "grace.rosettacloud.ai CNAME dxyz.cloudfront.net", no trailing dots
  def test_an_unrecognised_record_shape_raises(): ...
  def test_an_existing_association_with_other_subdomains_is_refused(): ...
  def test_set_environment_keeps_what_it_does_not_own(): ...  # AMPLIFY_MONOREPO_APP_ROOT and _LIVE_UPDATES survive; one RELEASE job started
  def test_set_environment_refuses_an_unknown_or_blank_value(): ...
  def test_the_previous_address_stays_registered_during_the_move():
      # provision(dashboard_url="https://grace.rosettacloud.ai", also_allow=("https://grace.rosettacloud.app",), ...)
      # -> provision_cognito.provision called with callbacks for .ai, .app and the amplifyapp URL;
      #    the env map sets DASHBOARD_URL to the .ai address
  ```

- [ ] **Step 2: Run them.**
  Run: `.venv/bin/python -m pytest tests/test_infra_amplify.py`
  Expected: the new tests fail.
- [ ] **Step 3: Implement.** `set_environment` reuses `merged_environment`'s preservation rule instead
  of reimplementing it.
- [ ] **Step 4: Run.**
  Run: `.venv/bin/python -m pytest -m "not live"`, then the content check.
  Expected: all pass.
- [ ] **Step 5: Commit and open the pull request (owner go).**
  `git commit -am "feat: the dashboard can take a second address, and environment changes merge rather than replace"`.
  Push, run `gh pr create --fill`, wait for green checks, and merge after review.

### Task 3: Move sign-in to `auth.rosettacloud.ai` (owner go at every write)

**Files:** none. These are operations, run from `main` after Task 2 merges.

- [ ] **Step 1: Run the read-only preflight.**
  - `dig +short A rosettacloud.ai`. Expected: at least one address. Cognito requires the parent
    domain to resolve.
  - `dig +short CAA rosettacloud.ai`. Expected: empty.
  - `aws cognito-idp describe-user-pool --user-pool-id us-east-1_HXs3b0APR --query 'UserPool.[CustomDomain,Domain]'`.
    Expected: `["auth.rosettacloud.app", "grace-caseworkers"]`.
  - Through the Cloudflare MCP: `GET /zones/{zone}/dns_records?name=auth.rosettacloud.ai`. Expected: none.

  Save every output to `$SCRATCH/domain-move/preflight.txt`.
- [ ] **Step 2 (owner go): Request the certificate.**
  Run: `.venv/bin/python -c "from infra import provision_cognito as p, dns; import boto3; p.ensure_certificate(boto3.client('acm', region_name='us-east-1'), dns.RecordList(), wait=False)"`
  Expected: `PendingDns`, naming one validation CNAME.
- [ ] **Step 3 (owner go): Create the validation record in Cloudflare.**
  1. Look the name up. If it exists with identical content, skip; if it exists with different content,
     stop.
  2. Otherwise send `POST /zones/{zone}/dns_records` with
     `{"type": "CNAME", "name": <name>, "content": <value>, "proxied": false, "ttl": 1, "comment": "Grace: ACM validation for auth"}`.
  3. Read it back and assert `proxied == false`.
  4. Re-run Step 2 with `wait=True` until it returns an ARN. ACM's waiter allows up to an hour.
- [ ] **Step 4 (owner go): Point the dashboard at the prefix domain**, so sign-in does not depend on the
  custom domain.
  Run:
  `set_environment(client, "dbi97xicbjbv8", {"COGNITO_DOMAIN": "https://grace-caseworkers.auth.us-east-1.amazoncognito.com"})`,
  then wait for the job to reach `SUCCEED` (`aws amplify get-job`).
- [ ] **Step 5: Verify the fallback before deleting anything.**
  Run: `curl -s -D - https://grace.rosettacloud.app/login | grep -o 'https://grace-caseworkers\.auth\.us-east-1\.amazoncognito\.com/login' | head -1`
  Expected: one match, from the redirect header or the page's sign-in link, whichever `/login` uses.
  Then the owner signs in through it in a browser and reaches `/`. **Stop here unless both hold.**
- [ ] **Step 6 (owner go): Delete the old custom domain.**
  Run: `aws cognito-idp delete-user-pool-domain --domain auth.rosettacloud.app --user-pool-id us-east-1_HXs3b0APR`.
  Poll `describe-user-pool-domain --domain auth.rosettacloud.app` until `DomainDescription` is empty.
- [ ] **Step 7 (owner go): Create the new custom domain.**
  First confirm the callbacks will survive: `aws cognito-idp describe-user-pool-client` lists the
  production callback URLs. Save them to `$SCRATCH/domain-move/callbacks-before.json`.

  Run: `.venv/bin/python -m infra.provision_cognito`
  Expected: exit 0, printing the CNAME from `auth.rosettacloud.ai` to its CloudFront target. Then check
  that `describe-user-pool-client` still lists exactly the saved callback URLs; if not, stop and restore
  them.

  Create the CNAME in Cloudflare the same way as in Step 3, with the comment
  `"Grace: sign-in (Cognito)"`, and read it back. Then poll until both hold:
  - `aws cognito-idp describe-user-pool-domain --domain auth.rosettacloud.ai --query DomainDescription.Status`
    returns `ACTIVE`;
  - `curl -fsS https://auth.rosettacloud.ai/.well-known/openid-configuration | jq -r .issuer` returns
    `https://cognito-idp.us-east-1.amazonaws.com/us-east-1_HXs3b0APR`.
- [ ] **Step 8 (owner go): Point the dashboard at the new sign-in domain.**
  Run: `set_environment(..., {"COGNITO_DOMAIN": "https://auth.rosettacloud.ai"})` and wait for `SUCCEED`.
  Expected: `/login` redirects to `https://auth.rosettacloud.ai/login?...`. The owner then signs in and
  signs out in a browser, and both work.
- [ ] **Step 9 (owner go): Remove the dangling Route 53 alias** for `auth.rosettacloud.app`, which now
  points at a distribution that no longer serves it.
  1. Read the exact record with `aws route53 list-resource-record-sets --hosted-zone-id Z08385903PVEGWMREU7F7 --query "ResourceRecordSets[?Name=='auth.rosettacloud.app.']"`.
  2. Delete it with a `DELETE` change carrying that exact record.
  3. Leave the old certificate and its validation record for the owner's retirement decision.
- [ ] **Step 10: Write the measurements** (times, statuses, redirect URLs, issuer) to
  `docs/evidence/domain-move.md`. Commit it in Task 5.

### Task 4: Add the dashboard address `grace.rosettacloud.ai` (owner go at every write)

**Files:** none. These are operations.

- [ ] **Step 1 (owner go): Create the association.**
  Run: `ensure_domain(boto3.client("amplify", region_name="us-east-1"), "dbi97xicbjbv8")`
  Expected: two `DnsRecord`s. A `ValueError` from the parser means stop: record the raw
  `get_domain_association` response, fix the parser in a pull request, and resume.
- [ ] **Step 2 (owner go): Create both records in Cloudflare.**
  1. Look up each name first; skip an identical record, and stop on a conflicting one.
  2. Use DNS-only, with the comments `"Grace: Amplify certificate validation"` and
     `"Grace: dashboard (Amplify)"`.
  3. Read both back and assert `proxied == false`.
  4. The zone's CNAME flattening is `flatten_at_root`, so these subdomain records are not flattened.
- [ ] **Step 3: Wait until the domain is available.**
  Poll `aws amplify get-domain-association --app-id dbi97xicbjbv8 --domain-name rosettacloud.ai --query domainAssociation.domainStatus`
  every five minutes until `AVAILABLE`. AWS allows up to 48 hours. On `FAILED`, record
  `statusReason` and stop.
- [ ] **Step 4 (owner go): Switch the dashboard address and keep the old one registered.**
  Run: `provision_amplify.provision(dashboard_url="https://grace.rosettacloud.ai", also_allow=("https://grace.rosettacloud.app",))`,
  then start a `RELEASE` job and wait for `SUCCEED`.
  Expected, from `aws cognito-idp describe-user-pool-client`: the callbacks include
  `/api/auth/callback` on both addresses, and the logout URLs include `/login` on both.
- [ ] **Step 5: Verify on the new address.**
  - `curl -sI https://grace.rosettacloud.ai/` returns `307`, with `location` ending in `/login`.
  - The same request with `Cookie: grace_session=forged.token` returns `307` and zero case ids in the body.
  - `curl -s -X POST https://grace.rosettacloud.ai/api/case/c-010/decide` returns `401`.
  - The owner signs in on `https://grace.rosettacloud.ai` and sees
    `9 handled alone, 3 waiting on you.`. If a caseworker has answered something since, the sentence
    includes the "you've answered" clause.

  Append all of this to `docs/evidence/domain-move.md`.

### Task 5: Documentation, homepage, and the second pull request

**Files:**
- Modify:
  - `README.md`, `CLAUDE.md`, `docs/README.md`;
  - `docs/runbooks/deploy.md`, `docs/runbooks/dashboard.md`;
  - `web/app/api/auth/logout/route.ts:13`;
  - `.env.example`, if it names an address.
- Add: `docs/evidence/domain-move.md` (from Tasks 3–4)

**Interfaces:**
- Produces: current docs that name the `.ai` addresses. Evidence documents keep the addresses they
  measured, because they are dated records.

- [ ] **Step 1: Replace the current-address mentions.** Change `https://grace.rosettacloud.app` to
  `https://grace.rosettacloud.ai`, and `auth.rosettacloud.app` to `auth.rosettacloud.ai`, in the files
  above.
  - `CLAUDE.md` "Current state" also says that the old addresses stay registered pending the owner's
    retirement decision.
  - Its Cognito paragraph says the custom domain is `auth.rosettacloud.ai`, in Cloudflare, with the
    prefix domain as the fallback.
- [ ] **Step 2: Update the diagram if possible.** If `drawio --version` succeeds, change the address
  label in `docs/architecture.drawio` and re-export `docs/architecture.png`. Otherwise list the PNG as
  outstanding in the PR body.
- [ ] **Step 3: Run the checks.**
  Run: `.venv/bin/python -m pytest -m "not live" && (cd web && npm run typecheck && npm run lint && npm run test && npm run build)`,
  then the content check. Then run:
  `git grep -n "grace.rosettacloud.app\|auth.rosettacloud.app" -- README.md CLAUDE.md docs/README.md docs/runbooks web/app`
  Expected: only lines that describe the old address as the previous one.
- [ ] **Step 4 (owner go): Set the homepage.**
  Run: `gh repo edit mohamedsorour1998/Grace --homepage https://grace.rosettacloud.ai`
- [ ] **Step 5 (owner go): Commit, push, open the pull request, and merge after review.**
  `git commit -am "docs: Grace now lives at grace.rosettacloud.ai, with sign-in at auth.rosettacloud.ai"`
