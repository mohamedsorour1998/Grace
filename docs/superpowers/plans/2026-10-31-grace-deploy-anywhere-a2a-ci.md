# Grace deploy-anywhere, A2A server, and CI: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Anyone can deploy Grace into their own AWS account, under their own table name, in
`us-east-1` or `us-west-2`. They can load their own households from a file. Other agents can call
Grace over A2A on port 9000. CI proves every change: Python tests, the web gates, an arm64 A2A image
with a smoke run, a vulnerability scan, and a content check.

**Architecture:** Configuration moves from constants to the environment through `infra/naming.py`,
which every provisioner and the store already read.

The A2A server is a thin adapter:

- `grace/a2a.py` parses a request into the same payload the HTTP runtime takes;
- it calls `grace.entrypoint.process_case`, which never raises, on a worker thread;
- it replies with one agent message.

`a2a_app.py` hands that executor to `bedrock_agentcore.runtime.build_a2a_app` / `serve_a2a`. It runs
the same import-time checks as `runtime_app.py`.

`Dockerfile.a2a` mirrors `Dockerfile`, and a test keeps the two in step.

**Tech Stack:** Python 3.12; strands-agents 1.54.0; bedrock-agentcore 1.23.0 with the `a2a` extra
(a2a-sdk 0.3.26, Starlette, uvicorn); Docker on linux/arm64; GitHub Actions (`ubuntu-24.04`,
`ubuntu-24.04-arm`); the Trivy CLI.

**Spec:** `docs/superpowers/specs/2026-10-31-grace-next-design.md`

## Global Constraints

- Grace's hard rules apply unchanged. In particular:
  - the A2A reply and every log line name a case only by `case_id`;
  - `a2a_app.py` refuses to start without the empty span-redaction token.
- The only new dependency is `bedrock-agentcore[a2a]`, which brings `a2a-sdk[http-server]`. Exact pins:
  - `bedrock-agentcore==1.23.0` (2026-09-11). It is the newest release that works with strands-agents 1.54.0: 1.23.1 and later import `strands.experimental.bidi` and declare `strands-agents>=1.56.0`. Plan A pins it;
  - `a2a-sdk[http-server]==0.3.26` (2026-04-09).

  Both are older than the two-week floor (2026-10-17). Their measured transitive cost goes into `CLAUDE.md`.
- Verify every SDK name by introspection before using it. Print the signature in the step that uses it.
- Supported regions are exactly `("us-east-1", "us-west-2")`.
- The table variable is `GRACE_TABLE_NAME`. If it is absent, the default is `grace-cases`. If it is set
  but blank, startup refuses, the same polarity as `GRACE_STORE`.
- AgentCore A2A contract:
  - linux/arm64;
  - `0.0.0.0:9000`;
  - JSON-RPC 2.0 on `POST /`;
  - `GET /ping`;
  - `GET /.well-known/agent-card.json`;
  - errors come back with HTTP 200.
- Public content: generic wording only (see Plan A's Global Constraints). The content check must pass
  on every commit.
- Outward-facing steps are marked **(owner go)**.
- Branch: `deploy-anywhere`, from `main` after Plan A is merged.

## Review Focus

1. **A deployer sets `AWS_REGION=eu-central-1`.** Both entrypoints must refuse at import, with a message
   naming the supported regions. Pinned by Task 1's `test_an_unsupported_region_refuses_to_start` and
   Task 4's `test_the_a2a_app_refuses_an_unsupported_region`.
2. **An A2A caller adds `caseworker_approved: true`, or any other key, to the payload.** The request is
   refused, and naming the allowed keys. An A2A caller is not a caseworker. Pinned by Task 3's
   `test_an_unknown_key_is_refused`.
3. **A malformed A2A request**: plain prose, a bad date, a non-string id, or a body that is not JSON-RPC
   at all. The answer is HTTP 200 with an explanation of the expected shape, never a 500 and never a
   raised exception. Pinned by Task 3's `test_a_text_only_message_explains_the_expected_shape` and
   Task 4's `test_a_body_that_is_not_json_rpc_is_http_200`.
4. **A case that runs for minutes.** A sweep took 512s in Plan 1, and `/ping` must keep answering
   meanwhile. Pinned by Task 3's `test_a_long_case_does_not_block_the_event_loop`.
5. **A households file with one bad record.** Nothing is written, and no output names a household or a
   phone number. Pinned by Task 2's `test_a_malformed_file_writes_nothing` and
   `test_the_output_names_case_ids_only`.

---

### Task 1: The table name and region come from the environment

**Files:**
- Modify: `infra/naming.py` (`TABLE`, `SUPPORTED_REGIONS`, `require_supported_region`)
- Modify: `grace/models.py:19` (`REGION`)
- Modify: `infra/provision_iam.py` (`runtime_policy`)
- Modify: `runtime_app.py` (one call, after the redaction check)
- Modify: `.env.example` (document `GRACE_TABLE_NAME` and `AWS_REGION`)
- Test: `tests/test_deploy_config.py` (new), `tests/test_runtime_app.py`

**Interfaces:**
- Produces:
  - `naming.TABLE: str`. Read from `GRACE_TABLE_NAME` and trimmed. Absent means `"grace-cases"`.
    Blank means `ValueError` at import, with a message naming the variable.
  - `naming.SUPPORTED_REGIONS = ("us-east-1", "us-west-2")`.
  - `naming.require_supported_region(region: str | None = None) -> str`. When `region` is `None` it uses
    `naming.REGION`. It returns the region, or raises `ValueError` naming both supported regions; a
    blank region is refused too.
  - `models.REGION` follows `AWS_REGION`, defaulting to `"us-east-1"`.
  - `provision_iam.runtime_policy(account_id: str, region: str | None = None, table: str | None = None) -> dict`.
    The defaults are `naming.REGION` and `naming.TABLE`, so existing callers are unchanged.

- [ ] **Step 1: Write the failing tests.** Use a fixture that reloads `infra.naming` and `grace.models`
  in a `finally` block, so later tests see the defaults.

  ```python
  def test_the_table_name_comes_from_the_environment(monkeypatch):
      monkeypatch.setenv("GRACE_TABLE_NAME", "acme-renewals")
      assert importlib.reload(naming).TABLE == "acme-renewals"

  def test_an_absent_table_name_keeps_the_default(monkeypatch):
      monkeypatch.delenv("GRACE_TABLE_NAME", raising=False)
      assert importlib.reload(naming).TABLE == "grace-cases"

  def test_a_blank_table_name_refuses(monkeypatch):
      monkeypatch.setenv("GRACE_TABLE_NAME", "  ")
      with pytest.raises(ValueError, match="GRACE_TABLE_NAME"):
          importlib.reload(naming)

  def test_an_unsupported_region_refuses_to_start():
      with pytest.raises(ValueError, match="us-east-1.*us-west-2"):
          naming.require_supported_region("eu-central-1")
      with pytest.raises(ValueError):
          naming.require_supported_region("")
      assert naming.require_supported_region("us-west-2") == "us-west-2"

  def test_the_model_region_follows_aws_region(monkeypatch):
      monkeypatch.setenv("AWS_REGION", "us-west-2")
      assert importlib.reload(models).REGION == "us-west-2"

  def test_the_runtime_policy_takes_a_region_and_table():
      text = json.dumps(provision_iam.runtime_policy("123456789012", region="us-west-2", table="acme-renewals"))
      assert "arn:aws:dynamodb:us-west-2:123456789012:table/acme-renewals" in text
      assert "arn:aws:bedrock:us-west-2:123456789012:inference-profile/us.amazon.nova-pro-v1:0" in text
      assert "GetWorkloadAccessTokenForUserId" in text      # the explicit Deny stays
  ```

  In `tests/test_runtime_app.py`, add `test_the_module_refuses_to_import_in_an_unsupported_region`. It
  follows `test_the_module_refuses_to_import_without_the_redaction_token`: the token is set correctly
  and `AWS_REGION=eu-central-1`, so it expects a `ValueError` naming both regions.
- [ ] **Step 2: Run them.**
  Run: `.venv/bin/python -m pytest tests/test_deploy_config.py tests/test_runtime_app.py`
  Expected: the new tests fail. Before writing the policy test, read `runtime_policy` to confirm the
  inference-profile ARN shape, and adjust only the expected string if the shape differs.
- [ ] **Step 3: Implement the interfaces.** `runtime_app.py` calls `naming.require_supported_region()`
  right after the redaction check, so the redaction failure, being the more dangerous one, is reported
  first. Read `os.environ` once, at import, as `naming.py` does today.
- [ ] **Step 4: Run the whole suite.**
  Run: `.venv/bin/python -m pytest -m "not live"`
  Expected: all pass, `test_infra_naming.py` and `test_infra_iam.py` unchanged.
- [ ] **Step 5: Commit.**
  `git commit -am "feat: the table name and region come from the environment, and unsupported regions refuse to start"`

### Task 2: Load households from any file

**Files:**
- Modify: `infra/seed_cases.py:114-140` (`main`)
- Test: `tests/test_seed_cases.py`

**Interfaces:**
- Consumes: `naming.TABLE` (Task 1), and `grace.cases.store.load_fixture_cases(path: Path | None = None)`.
- Produces: `python -m infra.seed_cases --file PATH [--table NAME] [--verify]`.
  - Without `--file`, it seeds the bundled fixtures, as today.
  - The whole file is loaded and validated before the first write.
  - Exit 0 when every case reads back equal.
  - Exit 1 on `InvalidFixtureData` or on a verify mismatch.
  - Exit 2 on a missing file.

- [ ] **Step 1: Write the failing tests.** Reuse the file's `FakeTable`.

  ```python
  def test_cases_load_from_a_given_file(tmp_path, monkeypatch): ...
      # one household, copied in the fixtures' shape -> main([...]) == 0; its RECORD#v1 row and its directory row exist
  def test_a_malformed_file_writes_nothing(tmp_path, monkeypatch, capsys): ...
      # second household carries an unquoted phone (an int) -> exit 1; FakeTable has zero rows; stderr names the file
  def test_a_missing_file_refuses(tmp_path, capsys): ...
      # exit 2; message names the path
  def test_the_output_names_case_ids_only(tmp_path, monkeypatch, capsys): ...
      # out+err contain the case id and neither the display name nor the phone number
  ```

- [ ] **Step 2: Run them.**
  Run: `.venv/bin/python -m pytest tests/test_seed_cases.py`
  Expected: the four new tests fail (`--file` is unrecognised).
- [ ] **Step 3: Implement.** Add `--file` with `type=Path`, and check `exists()` before loading. Load
  with `load_fixture_cases(path)` before building the store. Print ids only.
- [ ] **Step 4: Run.**
  Run: `.venv/bin/python -m pytest -m "not live"`
  Expected: all pass.
- [ ] **Step 5: Commit.**
  `git commit -am "feat: seed_cases loads households from any file and validates all of it before writing"`

### Task 3: The A2A executor

**Files:**
- Modify: `pyproject.toml`:
  - keep `bedrock-agentcore==1.23.0` (pinned by Plan A);
  - add `[project.optional-dependencies] a2a = ["bedrock-agentcore[a2a]==1.23.0", "a2a-sdk[http-server]==0.3.26"]`.
- Modify: `uv.lock` (`uv lock`)
- Create: `grace/a2a.py`
- Modify: `CLAUDE.md`, the "Dependencies" section
- Test: `tests/test_a2a.py`

**Interfaces:**
- Consumes: `grace.entrypoint.process_case(payload: dict, store=None, channel=None) -> CaseOutcome`, which never raises.
- Produces:
  - `class RequestError(ValueError)`. Its message states the expected shape:
    `{"case_id": "<id>", "today": "YYYY-MM-DD"}`.
  - `parse_request(message: a2a.types.Message) -> dict`. It takes the first `DataPart`, or else a
    `TextPart` whose whole text is a JSON object.
    - The keys must be exactly `case_id` (a non-empty `str`) and `today` (`date.fromisoformat`-valid).
    - It returns `{"case_id": ..., "today": ...}`.
    - Anything else raises `RequestError`, including an unknown key.
  - `CaseExecutor(process: Callable[[dict], CaseOutcome] = process_case, on_outcome: Callable[[CaseOutcome], None] | None = None)`,
    an `a2a.server.agent_execution.AgentExecutor`.
  - `execute(context, event_queue) -> None` never raises. It enqueues exactly one agent `Message`, with
    two parts:
    - a `DataPart` whose `data` is the outcome dict;
    - a `TextPart` reading `"<case_id>: <status>"`.

    A refused request gets an outcome of `{"status": "error", "reason": str(RequestError)}`.
    `process` runs with `asyncio.to_thread`. `on_outcome` is called once per valid request; an exception
    from it is logged with the case id only, and the reply is still sent.
  - `cancel(...)` raises a2a's unsupported-operation error. Find its exact type by introspection.
  - `build_agent_card(url: str) -> a2a.types.AgentCard`:
    - name `"Grace"`;
    - one `AgentSkill`, with `id="check-case"` and examples of `['{"case_id": "c-001", "today": "2026-10-01"}']`;
    - `capabilities.streaming` false;
    - input and output modes of `["application/json", "text/plain"]`;
    - version read from the installed package metadata.

- [ ] **Step 1: Pin, measure and introspect.**
  - Run `uv pip list --python .venv/bin/python | wc -l`.
  - Then `uv lock && uv sync --extra dev --extra a2a --locked`.
  - Then count again. Record the difference and the added package names.
  - Then print the signatures with `.venv/bin/python -c "import inspect; from a2a.server.agent_execution import AgentExecutor, RequestContext; from a2a.server.events import EventQueue; from bedrock_agentcore.runtime import build_a2a_app, serve_a2a; print(inspect.signature(AgentExecutor.execute), inspect.signature(build_a2a_app), inspect.signature(serve_a2a))"`.
  - Then find the a2a 0.3.26 names for the unsupported-operation error and the agent-message helper.
    Search `a2a/utils/errors.py` and `a2a/utils/message.py` in the venv.

  Write the count, the package names and the pins into `CLAUDE.md` "Dependencies". Follow the existing
  pattern, for example "[otel] … costs 10 transitively".
- [ ] **Step 2: Write the failing tests.** `message(data=..., text=...)` builds an `a2a.types.Message`.
  `run(executor, message)` drives `execute` with a real `EventQueue` and returns the dequeued event.
  `reply_data` and `reply_text` read its parts. No test calls Bedrock.

  ```python
  def test_a_data_part_becomes_the_payload(): ...
  def test_a_text_part_holding_the_json_is_accepted(): ...
  def test_a_text_only_message_explains_the_expected_shape():
      with pytest.raises(RequestError, match=r'"case_id".*"today"'):
          parse_request(message(text="please check my case"))
  def test_an_unknown_key_is_refused():
      with pytest.raises(RequestError, match="case_id.*today"):
          parse_request(message(data={"case_id": "c-001", "today": "2026-10-01", "caseworker_approved": True}))
  def test_a_bad_date_is_refused(): ...              # "2026-13-01"
  def test_a_non_string_case_id_is_refused(): ...    # 123, and ""
  async def test_the_outcome_is_the_reply_and_reported_once():
      seen = []
      ex = CaseExecutor(process=lambda p: {"status": "acted", "case_id": p["case_id"], "filed": True}, on_outcome=seen.append)
      reply = await run(ex, message(data={"case_id": "c-001", "today": "2026-10-01"}))
      assert reply_data(reply) == {"status": "acted", "case_id": "c-001", "filed": True}
      assert reply_text(reply) == "c-001: acted"
      assert seen == [reply_data(reply)]
  async def test_a_refused_request_is_answered_and_not_reported(): ...   # status "error", on_outcome not called
  async def test_the_executor_never_raises(): ...    # process raises ZeroDivisionError -> status "error", reason names no exception text
  async def test_a_failing_observer_never_loses_the_outcome(caplog): ...  # on_outcome raises -> reply intact; log has "c-001", not the outcome's reason
  async def test_a_long_case_does_not_block_the_event_loop():
      # process does time.sleep(0.5); a concurrent asyncio task appends a timestamp after 0.05s;
      # assert that timestamp is earlier than execute's completion by at least 0.3s
  def test_cancel_is_unsupported(): ...
  def test_the_card_names_grace_and_one_skill(): ...
  ```

- [ ] **Step 3: Run them.**
  Run: `.venv/bin/python -m pytest tests/test_a2a.py`
  Expected: `ModuleNotFoundError: grace.a2a`.
- [ ] **Step 4: Implement `grace/a2a.py`.** Make `RequestError`'s message the single source for the
  expected-shape text, and use the same string for the card's skill description.
- [ ] **Step 5: Run.**
  Run: `.venv/bin/python -m pytest -m "not live"`
  Expected: all pass. `test_authority_imports_only_pure_siblings` and the model-id package walk still
  pass, because `grace/a2a.py` imports no model.
- [ ] **Step 6: Commit.**
  `git add -A && git commit -m "feat: an A2A executor that turns message/send into a case run, never raising"`

### Task 4: The A2A app and its image

**Files:**
- Create: `a2a_app.py`, `Dockerfile.a2a`
- Modify: `.dockerignore` (add `web/` and `scripts/`)
- Modify: `CLAUDE.md`:
  - "Architecture": one paragraph on the A2A entrypoint;
  - "Working here": how to run it locally.
- Test: `tests/test_a2a_app.py`

**Interfaces:**
- Consumes: `CaseExecutor` and `build_agent_card` (Task 3); `naming.require_supported_region` (Task 1);
  `grace.observability.redaction_is_configured`.
- Produces:
  - `a2a_app.build_app(executor: CaseExecutor | None = None) -> starlette.applications.Starlette`. It
    returns `build_a2a_app(executor or CaseExecutor(), build_agent_card(CARD_URL))`, where `CARD_URL`
    is `"http://127.0.0.1:9000/"`. The SDK replaces that URL with `AGENTCORE_RUNTIME_URL` when it is
    set; confirm this from the source read in Task 3 Step 1.
  - `a2a_app.main() -> None` calls `serve_a2a(CaseExecutor(), build_agent_card(CARD_URL), port=9000)`.
    The SDK binds `0.0.0.0` when `DOCKER_CONTAINER` is set.
  - At import, in this order: refuse without redaction, then refuse an unsupported region. Same
    messages as `runtime_app.py`.
  - `Dockerfile.a2a` is the same as `Dockerfile` except:
    - it installs `".[a2a]"`;
    - it copies `a2a_app.py` instead of `runtime_app.py`;
    - `EXPOSE 9000`;
    - `CMD ["python", "-m", "a2a_app"]`.

- [ ] **Step 1: Write the failing tests.** Use `starlette.testclient.TestClient(build_app(CaseExecutor(process=fake)))`.

  ```python
  def test_the_server_answers_ping(): ...                       # 200, JSON with "status"
  def test_the_card_is_served_at_the_well_known_path(): ...     # name "Grace", skills[0].id "check-case"
  def test_message_send_returns_the_outcome(): ...              # JSON-RPC result carries the data part {"status": "acted", ...}
  def test_a_malformed_request_is_http_200_with_an_explanation(): ...   # text "not json" -> 200, data part status "error", reason names "case_id"
  def test_a_body_that_is_not_json_rpc_is_http_200(): ...       # b"{oops" -> 200 and an "error" member (JSON-RPC parse error)
  def test_the_a2a_app_refuses_without_redaction(monkeypatch): ...
  def test_the_a2a_app_refuses_an_unsupported_region(monkeypatch): ...
  def test_the_a2a_image_matches_the_http_image():
      a, b = dockerfile_lines("Dockerfile"), dockerfile_lines("Dockerfile.a2a")
      assert env_lines(a) == env_lines(b) and user_line(a) == user_line(b) and from_line(a) == from_line(b)
      assert "EXPOSE 9000" in b and 'CMD ["python", "-m", "a2a_app"]' in b and '".[a2a]"' in "\n".join(b)
  ```

- [ ] **Step 2: Run them.**
  Run: `.venv/bin/python -m pytest tests/test_a2a_app.py`
  Expected: failures, because there is no `a2a_app` and no `Dockerfile.a2a`. If
  `test_a_body_that_is_not_json_rpc_is_http_200` still fails after Step 3, the SDK as shipped breaks
  the contract. In that case, add a Starlette exception handler that returns the JSON-RPC parse error
  `-32700` with HTTP 200, and record the finding in `CLAUDE.md`.
- [ ] **Step 3: Implement.** Copy the `Dockerfile` comments that explain each line into
  `Dockerfile.a2a`, so the redaction and bind-host reasoning travels with them.
- [ ] **Step 4: Run it locally without a container.**
  Run: `OTEL_SEMCONV_STABILITY_OPT_IN='gen_ai_latest_experimental,gen_ai_unredacted_attributes=' GRACE_STORE=memory AWS_REGION=us-east-1 .venv/bin/python -m a2a_app &`.
  Then run `curl -fsS localhost:9000/ping` and `curl -fsS localhost:9000/.well-known/agent-card.json`.
  Expected: 200 and the card. Then kill the process.
- [ ] **Step 5: Run.**
  Run: `.venv/bin/python -m pytest -m "not live"`
  Expected: all pass.
- [ ] **Step 6: Commit.**
  `git add -A && git commit -m "feat: an A2A server on port 9000 beside the HTTP runtime, with its own image"`

### Task 5: CI

**Files:**
- Create: `.github/workflows/ci.yml`
- Test: `tests/test_ci_workflow.py`

**Interfaces:**
- Consumes:
  - `pytest -m "not live"` (Plan A Task 4);
  - `scripts/content_check.sh` (Plan A Task 10);
  - `Dockerfile.a2a` (Task 4);
  - whether `npm run build` needs environment variables (Plan A Task 1 Step 4).
- Produces: four jobs whose names become required checks in Task 6: `python`, `web`, `a2a-image` and
  `content-check`.

- [ ] **Step 1: Write the failing tests.**

  ```python
  def test_every_action_is_pinned_to_a_commit(): ...      # every `uses:` matches @[0-9a-f]{40}
  def test_the_workflow_can_only_read(): ...              # top-level permissions == {"contents": "read"}
  def test_ci_deselects_live_tests(): ...                 # the python job runs pytest with -m "not live"
  def test_the_image_is_built_on_arm(): ...               # a2a-image runs-on ubuntu-24.04-arm
  def test_the_scan_fails_the_build(): ...                # a trivy step has --exit-code 1 and --severity HIGH,CRITICAL
  def test_the_four_jobs_exist(): ...                     # set(jobs) == {"python", "web", "a2a-image", "content-check"}
  ```

- [ ] **Step 2: Run them.**
  Run: `.venv/bin/python -m pytest tests/test_ci_workflow.py`
  Expected: FAIL (no workflow).
- [ ] **Step 3: Resolve the pins.** For `actions/checkout`, `actions/setup-node` and `astral-sh/setup-uv`:
  - list releases with `gh api repos/<owner>/<repo>/releases --jq '.[] | [.tag_name, .published_at] | @tsv'`;
  - take the newest release published on or before 2026-10-17;
  - resolve its commit with `gh api repos/<owner>/<repo>/git/ref/tags/<tag> --jq .object.sha`. If that
    returns a tag object, dereference it with `gh api repos/<owner>/<repo>/git/tags/<sha> --jq .object.sha`.

  Do the same for the Trivy CLI release, `aquasecurity/trivy`, and record the version. Write each pin as
  `uses: owner/repo@<sha>  # <tag>`.
- [ ] **Step 4: Write the workflow.**
  - Triggers: `push` to `main`, and `pull_request`.
  - `concurrency`: per ref, with `cancel-in-progress` for pull requests.
  - **`python`** (`ubuntu-24.04`):
    - checkout;
    - setup-uv with a pinned uv version;
    - `uv sync --extra dev --extra a2a --locked`;
    - `uv run pytest -m "not live"`, with `AWS_EC2_METADATA_DISABLED: "true"` and no credentials;
    - `uv run pytest evals/ --collect-only -q`.
  - **`web`** (`ubuntu-24.04`, `working-directory: web`):
    - setup-node with `node-version: 22`, `cache: npm` and `cache-dependency-path: web/package-lock.json`;
    - `npm ci`, then `typecheck`, `lint`, `test` and `build`. If Plan A Task 1 found that `build` needs
      variables, set placeholder values in this job's `env`, and say so in a comment.
  - **`a2a-image`** (`ubuntu-24.04-arm`):
    1. `docker build -f Dockerfile.a2a -t grace-a2a:ci .`
    2. `docker run -d --name a2a -p 9000:9000 -e GRACE_STORE=memory -e AWS_REGION=us-east-1 grace-a2a:ci`
    3. Poll `curl -fsS localhost:9000/ping` for up to 30 seconds.
    4. Fetch the card, and assert that its `name` is `Grace` using `jq`.
    5. POST a `message/send` whose text part is `not json`. Assert HTTP 200 and a body containing `case_id`.
    6. Print `docker logs a2a` if any step fails.
    7. Download the pinned Trivy `Linux-ARM64` tarball and its checksums file from the release, and
       verify the archive with `sha256sum -c --ignore-missing`.
    8. Run `trivy image --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed grace-a2a:ci`.
  - **`content-check`** (`ubuntu-24.04`): write `${{ secrets.CONTENT_DENYLIST }}` to
    `$RUNNER_TEMP/denylist` and run `scripts/content_check.sh "$RUNNER_TEMP/denylist"`. A missing
    secret makes the script exit 2, so the job fails rather than passing blind.
- [ ] **Step 5: Run.**
  Run: `.venv/bin/python -m pytest tests/test_ci_workflow.py`
  Expected: pass.
- [ ] **Step 6: Commit.**
  `git add -A && git commit -m "ci: tests, web gates, an arm64 A2A image with a smoke run and a CVE scan, and a content check"`

### Task 6: First green run, and required checks (owner go)

- [ ] **Step 1 (owner go): Create the secret.**
  Run: `gh secret set CONTENT_DENYLIST -R mohamedsorour1998/Grace < ~/.config/grace/content-denylist`
- [ ] **Step 2 (owner go): Push and open the pull request.**
  Run: `git push -u origin deploy-anywhere && gh pr create --fill`
  Then watch it with `gh pr checks --watch`.
  Expected: all four jobs green.
  - A Trivy finding is fixed by upgrading the base image or the package, never by an ignore file.
  - Re-run only a job that failed for infrastructure reasons, and say so in the PR.
- [ ] **Step 3 (owner go): Make the four jobs required.**
  Run:
  `gh api -X PATCH repos/mohamedsorour1998/Grace/branches/main/protection/required_status_checks -f strict=true -f 'contexts[]=python' -f 'contexts[]=web' -f 'contexts[]=a2a-image' -f 'contexts[]=content-check'`.
  If PATCH returns 404 because no checks exist yet, use `PUT` on the same path.
  Verify with `gh api repos/mohamedsorour1998/Grace/branches/main/protection/required_status_checks --jq .contexts`.
- [ ] **Step 4 (owner go): Merge** after review. Re-measure both suites on `main` and update `CLAUDE.md`
  "Current state" in the same PR if the counts moved.
