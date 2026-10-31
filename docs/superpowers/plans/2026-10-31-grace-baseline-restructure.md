# Grace baseline, restructure, and fixes: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the hackathon repository into a maintained open-source project. That means:

- a measured baseline and a tagged release;
- a short `CLAUDE.md` with its lessons kept verbatim in `docs/lessons/`;
- a docs tree a newcomer can navigate, a product README, and GitHub settings to match;
- every defect found in the 2026-10-31 full read fixed test-first.

**Architecture:** No runtime behaviour changes except four fixes:

- the capped ledger read;
- the caseload denominator;
- the build-spec agreement;
- the PII-guard lists.

Everything else is moving text and adding guards: a content check, a link check and a `live` marker.
Work happens on one branch, `restructure`, with one commit per task, and lands as one pull request.

**Tech Stack:** Python 3.12 + uv + pytest; Next.js 16 / vitest in `web/`; `gh` CLI; bash.

**Spec:** `docs/superpowers/specs/2026-10-31-grace-next-design.md`

## Global Constraints

- Grace's hard rules apply unchanged (`CLAUDE.md`, "Hard rules"). This plan touches no gate logic.
- Public content: describe features generically. Never name an organisation, a programme or a commercial
  channel in code, docs, test fixtures or commit messages. Hostnames under `rosettacloud.ai` and
  `rosettacloud.app` are allowed; prose naming the company is not.
- `docs/superpowers/specs/` and `docs/superpowers/plans/` are dated history. Never edit them, except
  to add `docs/superpowers/README.md` and this plan's checkboxes.
- Write each test first and watch it fail before writing the code (`superpowers:test-driven-development`).
- Outward-facing steps are marked **(owner go)**: pushes, tags, releases, GitHub settings and pull
  requests. Stop and ask at each one, even if an earlier one was approved.
- Never quote a test count from memory. Use the numbers Task 1 measures, re-measured after the last fix.
- Conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`). Every commit message ends with the
  attribution line the session provides.

## Review Focus

1. **A reader follows a link or a backticked path into a moved document.** It must resolve. Pinned by
   Task 11's `test_every_relative_link_resolves` and `test_every_docs_path_named_in_backticks_exists`.
2. **A ledger read that never stops paging.** It must raise rather than hang or truncate, and
   `process_case` must never report `acted` for that case. Pinned by Task 5's
   `test_an_endless_ledger_raises_instead_of_hanging` and `test_a_ledger_that_cannot_be_read_is_never_acted`.
3. **A caseworker adds a thirteenth household through `/new`.** The home page must not say "13 of 12
   households read". Pinned by Task 8's `readCaseload counts every listed household, including one that could not be read`.
4. **The content check passes because its word list is missing or empty.** It must refuse (exit 2)
   rather than report clean. Pinned by Task 10's `test_a_missing_denylist_refuses` and
   `test_an_empty_denylist_refuses`.
5. **CI runs the suite without AWS credentials.** Every test that needs AWS must be marked `live`, and
   an unknown marker must fail collection. Pinned by Task 4's no-credentials run and `--strict-markers`.

---

### Task 1: Measure the baseline

**Files:**
- Modify: none (`uv.lock` is committed in Task 2)

**Interfaces:**
- Produces: four numbers that Tasks 4 and 12 use:
  - the Python test count with credentials;
  - the Python test count without credentials;
  - the vitest count;
  - whether `npm run build` passes with no environment variables.

- [ ] **Step 1: Recreate the venv from the lockfile.**
  Run: `uv sync --extra dev --locked`
  Expected: `.venv/` exists and the command exits 0. If it reports the lock is out of date, run
  `uv lock`, review `git diff uv.lock` (it should change only the resolution metadata, not
  `strands-agents` or `bedrock-agentcore` versions), and stop to report if any pinned version moved.
- [ ] **Step 2: Run the Python suite with credentials.**
  Run: `.venv/bin/python -m pytest`
  Expected: all pass. Record `N_with`.
- [ ] **Step 3: Run it without credentials.**
  Run: `env -u AWS_PROFILE -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_EC2_METADATA_DISABLED=true .venv/bin/python -m pytest -rf`
  Expected: failures only in tests that reach AWS. At minimum that is
  `tests/test_ledger_trace_correlation.py`, which runs a real graph against Bedrock.
  - Confirm each failure's traceback ends in a credentials or endpoint error, such as
    `NoCredentialsError`. A failure for any other reason is a defect: stop and report.
  - Record the full list of failing test ids. Task 4 marks exactly these.
- [ ] **Step 4: Run the web gates.**
  Run: `cd web && npm ci && npm run typecheck && npm run lint && npm run test && env -i PATH="$PATH" HOME="$HOME" npm run build`
  Expected: all four pass. Record the vitest count, and whether `build` passed with an empty
  environment. Plan B's CI needs to know this.
- [ ] **Step 5: Write the numbers into the session's work state** (not the repository), so Task 12 can quote them.

### Task 2: Publish the hackathon state (owner go)

**Files:**
- Add: `uv.lock`

**Interfaces:**
- Produces:
  - tag `v0.1.0` at `f88b548`, the commit that was on GitHub's `main` at the 2026-09-14 deadline;
  - local `main` pushed (it carries `25cd07e`);
  - branch `restructure`, which every later task commits to.

- [ ] **Step 1: Confirm what was public at the deadline.**
  Run: `git fetch origin && git log --oneline origin/main -1`
  Expected: `f88b548 docs: architecture diagram redrawn in draw.io with official AWS icons`. If not, stop and report.
- [ ] **Step 2 (owner go): Tag and release.**
  Run:

  ```bash
  git tag -a v0.1.0 f88b548 -m "Public state at the hackathon deadline, 2026-09-14"
  git push origin v0.1.0
  gh release create v0.1.0 --title "v0.1.0: hackathon submission" --notes "The repository as it stood at the submission deadline. One later docs commit (25cd07e), written before the deadline and pushed afterwards, is on main."
  ```

  Expected: `gh release view v0.1.0` shows the tag at `f88b548`.
- [ ] **Step 3 (owner go): Push `main`.**
  Run: `git push origin main`
  Expected: `origin/main` is `25cd07e`.
- [ ] **Step 4: Branch, then commit the lockfile and the plans.** These files are untracked on `main`,
  so they travel to the branch.
  Run:

  ```bash
  git switch -c restructure
  git add uv.lock && git commit -m "chore: commit uv.lock so the environment is reproducible"
  git add docs/superpowers/specs/2026-10-31-grace-next-design.md docs/superpowers/plans/2026-10-31-*.md
  git commit -m "docs: the post-hackathon design and its three implementation plans"
  ```

### Task 3: Repository settings (owner go)

**Files:**
- Modify: none (GitHub settings only)

**Interfaces:**
- Produces:
  - `main` protected: pull request required, no force-push, no deletion;
  - required status checks are added by Plan B Task 6 once CI exists.

- [ ] **Step 1: Read the current settings** so the change can be reverted.
  Run: `gh repo view mohamedsorour1998/Grace --json hasWikiEnabled,hasProjectsEnabled,homepageUrl,deleteBranchOnMerge > "$SCRATCH/repo-settings-before.json"`
  `$SCRATCH` is the session scratchpad.
- [ ] **Step 2 (owner go): Apply the settings.**
  Run:

  ```bash
  gh repo edit mohamedsorour1998/Grace --enable-wiki=false --enable-projects=false --delete-branch-on-merge
  gh api -X PUT repos/mohamedsorour1998/Grace/vulnerability-alerts
  gh api -X DELETE repos/mohamedsorour1998/Grace/automated-security-fixes
  gh api -X PUT repos/mohamedsorour1998/Grace/private-vulnerability-reporting
  gh api -X PUT repos/mohamedsorour1998/Grace/branches/main/protection --input - <<'JSON'
  {"required_status_checks": null, "enforce_admins": true,
   "required_pull_request_reviews": {"required_approving_review_count": 0},
   "restrictions": null, "allow_force_pushes": false, "allow_deletions": false}
  JSON
  ```

  `enforce_admins: true` was Claude's call: the owner is the only admin, so without it the
  pull-request rule binds nobody. Name this in the pull request for the owner to confirm.
  Security alerts stay on and automatic security-fix PRs stay off; that is the owner's decision.
- [ ] **Step 3: Verify.**
  Run: `gh api repos/mohamedsorour1998/Grace/branches/main/protection --jq '.required_pull_request_reviews.required_approving_review_count, .allow_force_pushes.enabled'`
  Expected: `0` then `false`.

### Task 4: A `live` marker for tests that call AWS

**Files:**
- Modify: `pyproject.toml` (`[tool.pytest.ini_options]`)
- Modify: `tests/test_ledger_trace_correlation.py`, plus any other module from Task 1 Step 3's list

**Interfaces:**
- Produces: `pytest -m "not live"` passes with no AWS credentials. Plan B's CI runs exactly that.

- [ ] **Step 1: Add strict markers first, and watch the suite fail.** Set
  `addopts = "-q --strict-markers"`. Add `pytestmark = pytest.mark.live` to
  `tests/test_ledger_trace_correlation.py`, and to each module from Task 1's list.
  Run: `.venv/bin/python -m pytest --co -q 2>&1 | tail -3`
  Expected: collection error `'live' not found in markers configuration option`.
- [ ] **Step 2: Register the marker** in `[tool.pytest.ini_options]`:
  `markers = ["live: calls real AWS services (Bedrock, DynamoDB); deselected in CI with -m 'not live'"]`.
- [ ] **Step 3: Run the no-credentials command from Task 1 Step 3, adding `-m "not live"`.**
  Expected: 0 failures. The deselected count equals Task 1's failing list.
- [ ] **Step 4: Run with credentials and no filter.** Expected: `N_with` passed, unchanged.
- [ ] **Step 5: Document it.** Add one line to the `Testing` section of `CLAUDE.md`:
  ``pytest -m "not live"`` is what CI runs.
- [ ] **Step 6: Commit.**
  `git commit -am "test: mark AWS-calling tests live and make markers strict, so CI runs without credentials"`

### Task 5: Cap the ledger read

**Files:**
- Modify: `grace/cases/dynamo_store.py:355-395` (`DynamoDBCaseStore.ledger`)
- Test: `tests/test_dynamo_store.py`, `tests/test_entrypoint.py`

**Interfaces:**
- Consumes: `_MAX_PAGES = 100` (already in `dynamo_store.py:70`, used by `_directory_ids`).
- Produces: `ledger(case_id)` raises `RuntimeError` after `_MAX_PAGES` pages that all carry
  `LastEvaluatedKey`. It never returns a truncated list.

- [ ] **Step 1: Write the failing tests.**

  ```python
  def test_an_endless_ledger_raises_instead_of_hanging():
      client = EndlessQueryClient()   # every query returns one ledger item plus a LastEvaluatedKey
      store = DynamoDBCaseStore(table_name="t", client=client)
      with pytest.raises(RuntimeError, match=r"ledger for c-001 did not finish within 100 pages"):
          store.ledger("c-001")
      assert client.calls == 100

  def test_a_ledger_that_cannot_be_read_is_never_acted():   # tests/test_entrypoint.py
      outcome = process_case({"case_id": "c-001", "today": "2026-10-01"},
                             store=StoreWhoseLedgerRaises(), channel=TranscriptChannel())
      assert outcome["status"] != "acted"
  ```

  `EndlessQueryClient` and `StoreWhoseLedgerRaises` are small fakes in the test files.
  - `EndlessQueryClient` raises `AssertionError("query called 1000 times: the loop is unbounded")` on
    its 1000th call. Today's unbounded loop then fails the test instead of hanging the run.
  - `StoreWhoseLedgerRaises` subclasses the in-memory store used by that file's other tests, and
    overrides `ledger` to raise `RuntimeError`.
- [ ] **Step 2: Run them.**
  Run: `.venv/bin/python -m pytest tests/test_dynamo_store.py -k endless tests/test_entrypoint.py -k "endless or cannot_be_read"`
  Expected: the first test fails with the `AssertionError` above, which is the defect. The second
  should already pass. If it fails, stop and report: that is a hard-rule-6 regression, and outside this
  task's scope.
- [ ] **Step 3: Implement.** Replace `while True` with `for _ in range(_MAX_PAGES)` plus the `else:` raise.
  Use the same message shape as `_directory_ids`, and keep the docstring's reasoning: truncation drops
  the newest rows, which is where `renewal_submitted` lives.
- [ ] **Step 4: Run.**
  Run: `.venv/bin/python -m pytest tests/test_dynamo_store.py tests/test_entrypoint.py`
  Expected: all pass.
- [ ] **Step 5: Commit.**
  `git commit -am "fix: the ledger read is capped and raises, so a repeating page key cannot hang a sweep"`

### Task 6: The committed `amplify.yml` is the generated build spec

**Files:**
- Modify: `infra/provision_amplify.py:223-321` (`build_spec_commands`, `build_spec`)
- Test: `tests/test_infra_amplify.py`

**Interfaces:**
- Produces:
  - `INSTALL_COMMAND = "npm ci --cache .npm --prefer-offline"` (module constant);
  - `CACHE_PATHS = (".next/cache/**/*", ".npm/**/*")`;
  - `build_spec()` parses to the same YAML as the committed `amplify.yml`.

- [ ] **Step 1: Write the failing test.**

  ```python
  def test_the_committed_amplify_yml_is_the_generated_build_spec():
      committed = yaml.safe_load((ROOT / "amplify.yml").read_text())
      assert committed == yaml.safe_load(provision_amplify.build_spec())
  ```

- [ ] **Step 2: Run it.** Expected: FAIL. The committed file installs with
  `npm ci --cache .npm --prefer-offline` and caches `.next/cache` and `.npm`; the generator says
  `npm ci` and `node_modules`.
- [ ] **Step 3: Make the generator match the committed file.** The committed file wins, because it is
  what Amplify runs and its cache paths are the ones AWS documents for Next.js. Caching `node_modules`
  is pointless when `npm ci` deletes it. Split `pre` and `rest` on `INSTALL_COMMAND`, not on the
  literal `"npm ci"`. Update any existing assertion that names the old literal.
- [ ] **Step 4: Run.**
  Run: `.venv/bin/python -m pytest tests/test_infra_amplify.py`
  Expected: all pass.
- [ ] **Step 5: Commit.**
  `git commit -am "fix: the generated Amplify build spec is the committed one, and a test keeps them equal"`

### Task 7: Every PII guard names every fixture household

**Files:**
- Create: `tests/test_pii_guard_lists.py`
- Modify: `tests/test_infra_amplify.py:1202-1205`, `web/__tests__/decide.test.ts:190,205`, and any
  other file the new test reports

**Interfaces:**
- Produces: a test that finds the guard lists on disk. Any test file under `tests/` or `web/__tests__/`
  that names six or more fixture surnames is a guard list, and must name all twelve.

- [ ] **Step 1: Write the failing test.**

  ```python
  def test_every_surname_guard_names_every_fixture_household():
      surnames = fixture_surnames()             # from fixtures/households.yaml: "The X Household" -> "X"
      assert len(surnames) == 12
      guards = guard_files(surnames)            # tracked files with >= 6 distinct surnames
      assert len(guards) >= 5, guards           # so it cannot pass by finding nothing
      missing = {f: sorted(set(surnames) - names_in(f)) for f in guards}
      assert not {f: m for f, m in missing.items() if m}
  ```

- [ ] **Step 2: Run it.** Expected: FAIL. `tests/test_infra_amplify.py` lacks Torres, Abebe and
  Kowalski; `web/__tests__/decide.test.ts` lacks Delacroix and Torres.
- [ ] **Step 3: Complete each reported list.** Add the missing surnames. Leave extra non-fixture names
  in place; they are harmless.
- [ ] **Step 4: Run.**
  Run: `.venv/bin/python -m pytest tests/test_pii_guard_lists.py tests/test_infra_amplify.py && (cd web && npm run test)`
  Expected: all pass.
- [ ] **Step 5: Commit.**
  `git commit -am "test: every household-name guard lists all twelve fixture surnames, found from disk"`

### Task 8: The caseload count's denominator is the directory, not twelve

**Files:**
- Modify: `web/lib/cases.ts:305-318` (add `readCaseload`; `listCases` delegates)
- Modify: `web/app/page.tsx:20-24,65` (the comment, and `of 12`)
- Test: `web/__tests__/cases.test.ts`, `web/__tests__/render.test.ts`

**Interfaces:**
- Produces: `export async function readCaseload(client?: DynamoDBClient): Promise<{ cases: CaseSummary[]; listed: number }>`.
  - `listed` is `listCaseIds(client).length`.
  - `cases` is what `listCases` returns today.
  - `listCases(client)` becomes `(await readCaseload(client)).cases`.

- [ ] **Step 1: Write the failing tests.**
  - `cases.test.ts`, "readCaseload counts every listed household, including one that could not be
    read". The setup is a directory listing `c-900` beside the twelve seeded ids, where reading
    `c-900` fails. Expect `listed` to be 13 and `cases.length` to be 12.
  - `render.test.ts`, "the home page names the listed count, not a constant". Render with 12 cases and
    `listed: 13`. Expect the markup to contain `12 of 13 households read`.
- [ ] **Step 2: Run them.**
  Run: `cd web && npm run test`
  Expected: FAIL (`readCaseload` is not exported; the page renders `of 12`).
- [ ] **Step 3: Implement** the interface, and make `page.tsx` render `{sweep.total} of {listed}`.
  Rewrite the comment at `page.tsx:20` to say the caseload is the twelve seeded ids plus the
  `CASE_DIRECTORY` partition (`listCaseIds`).
- [ ] **Step 4: Run all four gates.**
  Run: `cd web && npm run typecheck && npm run lint && npm run test && npm run build`
  Expected: all pass.
- [ ] **Step 5: Commit.**
  `git commit -am "fix: the home page counts households against the directory, not a fixed twelve"`

### Task 9: Stale comments and leftovers

**Files:**
- Modify:
  - `fixtures/households.yaml:9-12`
  - `Dockerfile:26-28`
  - `grace/run.py:123`
  - `web/lib/cognito.ts:159-163`
  - `evals/test_gate_trajectory.py:173-176`
  - `amplify.yml` (the `__live__/` sentence)
- Delete: `web/__live__/live-render.test.ts`, `web/vitest.live.mts`

**Interfaces:**
- Produces: no behaviour change. Each comment states what the code does today.

- [ ] **Step 1: Correct each comment to the verified fact.**
  - **`households.yaml`:** an omitted `reported_*` field stays `None`, meaning "not reported". It is
    not defaulted to the on-file value; see `CLAUDE.md`, Task 2.
  - **`Dockerfile`:** the table holds `RECORD#v1` case records and the directory as well as ledger and
    escalation rows. `fixtures/` is still copied because `load_fixture_cases` is the in-memory store's
    default.
  - **`run.py`:** refer to the graph's node-execution cap by name (`graph.py`,
    `set_max_node_executions`) rather than restating the number, which is now 20.
  - **`cognito.ts`:** the custom domain runs managed login version 2 (`MANAGED_LOGIN_VERSION` in
    `infra/provision_cognito.py`), and `/login` is the path verified live against it.
- [ ] **Step 2: Remove the duplicated ledger read** after the `finally` block in `_Run.__init__`. The
  read inside `finally` is the one that matters.
  Run: `.venv/bin/python -m pytest evals/ --collect-only -q | tail -1`
  Expected: 23 tests collected, no import error. The evals call Bedrock and are not run here.
- [ ] **Step 3: Delete the two files labelled `TEMPORARY … Deleted after the run`.** In `amplify.yml`,
  delete the clause saying `__live__/` is excluded, because the directory no longer exists. Then run
  `.venv/bin/python -m pytest tests/test_infra_amplify.py` (Task 6's equality test re-checks the
  YAML) and `cd web && npm run test`.
  Expected: all pass.
- [ ] **Step 4: Commit.**
  `git commit -am "chore: correct five comments that no longer describe the code, and delete two temporary files"`

### Task 10: A content check, and a sweep of other projects' names

**Files:**
- Create: `scripts/content_check.sh`, `tests/test_content_check.py`
- Modify: every current (non-history) file the check reports. As of 2026-10-31 that is:
  - `infra/provision_memory.py`, `infra/teardown.py`, `infra/provision_all.py`, `infra/provision_cognito.py`;
  - `tests/test_infra_cognito.py`, `tests/test_infra_alarm.py`, `tests/test_memory.py`;
  - `docs/dashboard-runbook.md`, `docs/plan3-live-data-findings.md`, and `CLAUDE.md` (the
    "Newly created work only" line).

**Interfaces:**
- Produces: `scripts/content_check.sh DENYLIST_FILE`.
  - The denylist file has `deny:<word>` lines (case-insensitive whole words) and `allow:<ERE>` lines
    (stripped from each line before matching).
  - It scans `git ls-files`, minus `docs/superpowers/`, `uv.lock` and `web/package-lock.json`.
  - Exit 0 means clean. Exit 1 means hits were found, printed as `path:line:word`. Exit 2 means the
    file is missing, or has no `deny:` line.
  - The script itself contains no names; the owner keeps the word list outside the repository
    (`~/.config/grace/content-denylist`), and Plan B gives CI a copy as a secret.

- [ ] **Step 1: Write the failing tests.** Each test runs the script in a fresh `git init` repository
  under `tmp_path`, using invented words only.

  ```python
  def test_a_denied_word_is_reported_with_its_line(): ...          # "zebracorp" in a.md:3 -> exit 1, "a.md:3"
  def test_an_allowed_hostname_is_not_a_hit(): ...                  # allow:([a-z0-9-]+\.)*zebracorp\.example -> exit 0
  def test_history_is_exempt(): ...                                 # docs/superpowers/x.md -> exit 0
  def test_a_missing_denylist_refuses(): ...                        # exit 2
  def test_an_empty_denylist_refuses(): ...                         # only allow: lines -> exit 2
  ```

- [ ] **Step 2: Run them.** Expected: FAIL (no script).
- [ ] **Step 3: Implement the script.** Use bash with `set -euo pipefail`. Strip the allowed patterns
  with `sed -E`, then match with `grep -niwF -f` (portable to BSD and GNU grep). Then run
  `.venv/bin/python -m pytest tests/test_content_check.py`. Expected: pass.
- [ ] **Step 4: Ask the owner to create the word list (owner step).** It lives at
  `~/.config/grace/content-denylist`, containing their `deny:` words and these two allow lines:
  `allow:([a-z0-9-]+\.)*rosettacloud\.ai` and `allow:([a-z0-9-]+\.)*rosettacloud\.app`.
  Then run: `scripts/content_check.sh ~/.config/grace/content-denylist`
  Expected: exit 1, listing the files named above.
- [ ] **Step 5: Replace each hit with a generic placeholder that keeps the test's shape.** If a fixture
  tested that `name` and `name_v2` are both present, the placeholder must keep that relation, for
  example `other_project_memory-…` and `other_project_memory_v2-…`. Comments say "another project's
  memory" or "another project's user pool". Re-run the check until it exits 0.
- [ ] **Step 6: Run both suites.**
  Run: `.venv/bin/python -m pytest -m "not live" && (cd web && npm run test)`
  Expected: the same counts as before this task.
- [ ] **Step 7: Commit.** The message must contain no denied word:
  `git commit -am "chore: a content check for public text, and generic names for other projects' resources"`.

### Task 11: The docs tree, and a link check

**Files:**
- Move, with `git mv`:

  | From | To |
  |---|---|
  | `docs/runbook-deploy.md` | `docs/runbooks/deploy.md` |
  | `docs/dashboard-runbook.md` | `docs/runbooks/dashboard.md` |
  | `docs/architecture-drawio-guide.md` | `docs/runbooks/architecture-diagram.md` |
  | `docs/deployed-verification.md` | `docs/evidence/` |
  | `docs/dashboard-verification.md` | `docs/evidence/` |
  | `docs/plan3-live-data-findings.md` | `docs/evidence/live-data-findings.md` |
  | `docs/demo-video-handout.md` | `docs/hackathon/` |
  | `docs/devpost-description.md` | `docs/hackathon/` |
  | `docs/devpost-submission-form.md` | `docs/hackathon/` |
  | `docs/builder-blog-post*.md` (3 files) | `docs/hackathon/`, names unchanged |

  `docs/architecture.{md,drawio,png}` stay where they are.
- Create: `docs/README.md` (one line per document, grouped by folder) and `docs/superpowers/README.md`
  (two sentences: these are dated snapshots, and paths inside them are as of their date).
- Modify: every reference the new tests report.
- Test: `tests/test_docs_links.py`

**Interfaces:**
- Produces: `CURRENT_DOCS`, the list of files whose links must resolve:
  - `README.md`, `CLAUDE.md`, `SECURITY.md` and `CONTRIBUTING.md`, when they exist;
  - every `docs/**/*.md` outside `docs/superpowers/`.

- [ ] **Step 1: Write the failing tests**, then do the moves.

  ```python
  def test_every_relative_link_resolves():
      checked = 0
      for doc in CURRENT_DOCS:
          for target in relative_links(doc):        # [text](path) with no scheme; anchors stripped
              checked += 1
              assert (doc.parent / target).exists(), f"{doc}: {target}"
      assert checked >= 10

  def test_every_docs_path_named_in_backticks_exists():
      # `docs/....md|png|drawio` named in CURRENT_DOCS and in *.py/*.ts/*.tsx/amplify.yml
      ...
  ```

- [ ] **Step 2: Run them after the moves.**
  Run: `.venv/bin/python -m pytest tests/test_docs_links.py`
  Expected: FAIL, listing every stale reference.
- [ ] **Step 3: Update each reference.** Write `docs/README.md` and `docs/superpowers/README.md`.
- [ ] **Step 4: Run.**
  Run: `.venv/bin/python -m pytest -m "not live"`
  Expected: all pass.
- [ ] **Step 5: Commit.**
  `git commit -am "docs: runbooks, evidence and hackathon material in their own folders, with a link check"`

### Task 12: Split `CLAUDE.md` into a guide and `docs/lessons/`

**Files:**
- Create in `docs/lessons/`:
  - `README.md`
  - `01-gate-and-tools.md`
  - `02-graph-and-swarm.md`
  - `03-evals-and-ledger.md`
  - `04-agentcore-deploy.md`
  - `05-dashboard.md`
  - `06-dashboard-deploy-and-sign-in.md`
  - `07-caseload-audits-and-memory.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Produces: a `CLAUDE.md` of at most 350 lines. A lesson paragraph that leaves it lands verbatim in
  exactly one `docs/lessons/` file.

Section mapping, verbatim, keeping each heading:

| `CLAUDE.md` section | Destination |
|---|---|
| "What Task 1 … Task 5 established" | `01` |
| "What Task 6 / Task 7 established" | `02` |
| "What Task 8 / Task 9 established" (Plan 1) | `03` |
| "What Plan 2 established" (with the runtime-instrumentation and PII findings) | `04` |
| "What Plan 3 established" through its Task 6 material | `05` |
| "The dashboard is deployed…", Plan 3's "What Task 8 established", hosted UI, case intake, sign-in | `06` |
| The narrative paragraphs of "Current state" (frozen caseload, the six audit defects, `awaitingDecision`, the four overclaims, Memory, rule-pack citations) | `07` |

What stays in `CLAUDE.md`:

- "What this is" (rewritten, generic);
- "Current state", rewritten in at most 30 lines:
  - the deployed resources;
  - the two invariants;
  - four AgentCore surfaces, and the narrow Identity claim;
  - `trace_id` is NULL;
  - the measured counts from Task 1, re-measured;
- "The one idea that matters", "Hard rules" and "Working here", verbatim;
- "Architecture" and "Testing";
- a new "Lessons" section: one line per lessons file, plus at most ten one-line cross-cutting rules,
  each linking to its source paragraph. Examples:
  - never hand a model two dates;
  - the unrecognised value must be the safe one;
  - a test that cannot fail proves nothing;
  - deployed is not written;
  - fail closed on verification, open on observability;
- "Known infrastructure limits" and "Style".

"Hackathon constraints" moves to `docs/hackathon/README.md` in Task 13.

- [ ] **Step 1: Save the old file** to `$SCRATCH/CLAUDE.before.md`.
- [ ] **Step 2: Move the sections** per the table, and write the kept sections.
- [ ] **Step 3: Check that nothing was lost.** Split the old file and the new set
  (`CLAUDE.md`, `docs/lessons/*.md`, `docs/hackathon/README.md`) into blank-line-separated
  paragraphs. Print every old paragraph that appears in none of the new files.
  Expected: only paragraphs from "What this is", "Current state" and "Hackathon constraints", which
  were rewritten on purpose. Read each one, and confirm its facts survive in the rewrite or in `07`.
- [ ] **Step 4: Check the size.** Run `wc -l CLAUDE.md`. Expected: at most 350. Then run
  `.venv/bin/python -m pytest tests/test_docs_links.py`. Expected: pass.
- [ ] **Step 5: Commit.**
  `git commit -am "docs: CLAUDE.md becomes a working guide; the lessons move verbatim to docs/lessons"`

### Task 13: Product README, `SECURITY.md`, `CONTRIBUTING.md`, hackathon record

**Files:**
- Modify: `README.md`
- Create: `SECURITY.md`, `CONTRIBUTING.md`, `docs/hackathon/README.md`

**Interfaces:**
- Consumes: the docs tree from Task 11, and the measured counts.
- Produces: a README that a person deploying Grace can follow without hackathon context.

README sections, in order:

1. what Grace is and who it is for;
2. how the escalation boundary works (three layers);
3. the twelve-household demonstration;
4. what runs where: four AgentCore surfaces, with the narrow Identity wording and Gateway deferred;
5. quick start: `uv sync --extra dev`, tests, `grace sweep --auto escalate`, and the web gates;
6. deploy (links into `docs/runbooks/`);
7. the live dashboard (`https://grace.rosettacloud.app`, which needs a sign-in);
8. repository layout;
9. documentation (`docs/README.md`);
10. known limitations;
11. security, contributing, licence and credits;
12. history: "Built for the AWS Agents for Humans Hackathon (September 2026); see `docs/hackathon/`."

Known limitations to list:

- a household that ever escalated keeps the `escalated` status even after a later filing, because
  `web/lib/cases.ts` checks `pending` before `filed`;
- the escalation alarm has no `AlarmActions`, so it changes state but notifies no one;
- SMS goes to a transcript channel, not a carrier;
- no traces exist, and `trace_id` is NULL on deployed ledger rows.

Credits say patterns were reused as knowledge from the author's earlier projects, with no code copied.
Name no project or company.

- [ ] **Step 1: Write `docs/hackathon/README.md`.**
  - What was submitted, and when: 2026-09-14.
  - The tag, `v0.1.0`.
  - The rules the build followed: newly created work, and knowledge reuse disclosed generically.
  - Links to the moved submission documents.
  - Ask the owner for the demo video URL. Include it if they give one; otherwise write "submitted" with
    no link.
- [ ] **Step 2: Rewrite `README.md`** to the section list above. Facts come from the current README and
  `CLAUDE.md`; none are invented.
- [ ] **Step 3: Write `SECURITY.md`.**
  - Report through GitHub private vulnerability reporting (enabled in Task 3).
  - In scope: a bypass of the authority gate, household identity reaching a model or a log, and
    dashboard authentication.
  - Synthetic data only: never send real household data.
  - Supported version: `main`.
- [ ] **Step 4: Write `CONTRIBUTING.md`.**
  - Setup.
  - Both suites, with the `live` marker explained.
  - Test-first, and a link to the hard rules.
  - Branch, then pull request.
  - Conventional commits.
  - The dependency rule.
  - "Never add a household identity field".
  - Run the content check before pushing.
- [ ] **Step 5: Run the checks.**
  Run: `.venv/bin/python -m pytest tests/test_docs_links.py && scripts/content_check.sh ~/.config/grace/content-denylist`
  Expected: pass, and exit 0.
- [ ] **Step 6: Commit.**
  `git add -A && git commit -m "docs: a product README, security and contributing guides, and the hackathon record"`

### Task 14: Final verification and pull request (owner go)

- [ ] **Step 1: Run the full verification.**
  Run: `.venv/bin/python -m pytest -m "not live"`, the no-credentials run from Task 1 Step 3 with
  `-m "not live"`, `cd web && npm run typecheck && npm run lint && npm run test && npm run build`,
  and the content check.
  Expected: all green. Record the counts in `CLAUDE.md` "Current state" if they differ from Task 12's.
- [ ] **Step 2 (owner go): Push and open the pull request.**
  Run: `git push -u origin restructure`, then
  `gh pr create --title "Restructure the repository and fix defects from a full read" --body-file "$SCRATCH/pr-body.md"`.
  The PR body:
  - one line per task;
  - the `enforce_admins` call from Task 3;
  - the counts before and after;
  - a statement that history docs were not edited.
- [ ] **Step 3 (owner go): Merge** after the owner reviews it. Then run `git switch main && git pull`.
