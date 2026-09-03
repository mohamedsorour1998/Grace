# Grace deploy runbook

The ordered, **verified** sequence for deploying Grace to AgentCore. Every command here was run
against CLI `0.28.1` and account `<AWS_ACCOUNT_ID>` / `us-east-1` on 2026-09-03. Where this file
disagrees with the plan or the appendices, this file is right — the appendices were written against
CLI `0.24.2`.

---

## Preflight

### Already satisfied — do not redo

| Check | Command | Observed |
|---|---|---|
| Identity | `aws sts get-caller-identity` | account `<AWS_ACCOUNT_ID>` (root session) |
| **Transaction Search** | `aws xray get-trace-segment-destination --region us-east-1` | `Destination: CloudWatchLogs`, `Status: ACTIVE` |
| **CDK bootstrap** | `aws cloudformation describe-stacks --stack-name CDKToolkit` | `CREATE_COMPLETE`, created 2026-02-24 |
| Nova profiles | `aws bedrock get-inference-profile --inference-profile-identifier <id>` | all three `ACTIVE` |
| Local toolchain | — | `uv` 0.12.5, `node` v24.19.0, `npm` 11.17.0 |

CloudWatch Transaction Search is a one-time account action that can take ten minutes to take effect.
**It is already ACTIVE.** Do not re-enable it, and do not leave it to demo day.

CDK bootstrap matters because `agentcore deploy` deploys through CDK (see below). It was bootstrapped
by an unrelated earlier project, so no `cdk bootstrap` step is needed.

The three Nova profiles are the advocate, verifier, and referee — three *different* models, per
CLAUDE.md hard rule 2:

```bash
for m in global.amazon.nova-2-lite-v1:0 us.amazon.nova-pro-v1:0 us.amazon.nova-micro-v1:0; do
  aws bedrock get-inference-profile --inference-profile-identifier "$m" \
    --region us-east-1 --query 'status' --output text
done
```

### Container engine: Podman, not Docker

Docker's binary is present but its daemon is not running and is not used. **This project builds with
Podman 6.1.0.**

```bash
podman machine start
export DOCKER_HOST="$(podman machine inspect \
  --format '{{.ConnectionInfo.PodmanSocket.Path}}' podman-machine-default)"
podman ps                                    # must print a table header
podman info --format '{{.Version.OsArch}}'   # must print linux/arm64
```

Two things to know:

- **The `DOCKER_HOST` export is mandatory** for any Docker-API client — including the `agentcore`
  CLI — to find Podman. `podman-mac-helper` is not installed, so the conventional
  `/var/run/docker.sock` does not exist. Installing the helper needs `sudo`; the env var is
  sufficient and needs no privilege escalation. **Export it in every shell that builds or deploys.**
- **The VM is native `linux/arm64`**, which is what AgentCore Runtime requires. So
  `--platform linux/arm64` is a no-op rather than a slow emulated cross-build.

The socket path lives under `$TMPDIR` and can change when the machine is recreated — read it from
`podman machine inspect` rather than hardcoding it.

### CLI install and the flags that actually exist

```bash
npm install -g @aws/agentcore
agentcore --version    # 0.28.1
```

`npm` warns that a postinstall script (`check-old-cli.mjs`) was not run. That is fine — it only
checks for the deprecated `bedrock-agentcore-starter-toolkit`, which is not installed here.

**Corrections to the appendices' assumed command shapes**, all verified via `--help`:

| The plan/appendices assumed | Reality on 0.28.1 |
|---|---|
| `agentcore create grace --region us-east-1` | `create` takes `--name` / `--project-name`, not a positional. It **scaffolds a new project**; it does not register existing code. |
| `agentcore add memory --name ...` | `add` has subcommands: `agent`, `harness`, `memory`, `gateway`, `policy-engine`, and more. |
| `agentcore deploy` pushes a container | **`deploy` deploys via CDK** (`"Deploy project infrastructure to AWS via CDK"`). It has `--dry-run` and `--diff`, which are worth using first. |
| a new agent is scaffolded | Grace needs `add agent --type byo` with `--code-location` and `--entrypoint` — bring-your-own existing code. `--type create` would scaffold a fresh agent and ignore `grace/`. |

Useful flags discovered:

- `agentcore add agent --type byo --build Container --language Python --framework Strands
  --model-provider Bedrock --code-location . --entrypoint runtime_app.py --protocol HTTP`
- `agentcore create --build Container --container <uri-or-path>` accepts a Dockerfile path directly.
- `agentcore deploy --dry-run` previews; `--diff` shows the CDK diff; `-y` auto-confirms and reads
  credentials from the environment (needed for unattended runs); `--json` for machine-readable output.

**Always confirm a flag against `--help` before running it.** The CLI moved four minor versions past
the documentation this project's research was based on.

### Baseline

```bash
.venv/bin/python -m pytest
```

Must be **360 passed** before Plan 2 begins. This is the number every later task is measured against.
Plan 2 finished at **622 passed** — Plan 1's 360 unchanged, plus 262 added by Plan 2's own tasks.

---

## Deploy sequence

Filled in as tasks complete. See `docs/deployed-verification.md` for the evidence that the deployed
system behaves correctly.

### Task 7 — the Runtime (verified end to end, 2026-09-03)

Deployed runtime: **`arn:aws:bedrock-agentcore:us-east-1:339712964409:runtime/grace_grace-oTyyvo8stE`**
(name `grace_grace`, version 1, status `READY`, execution role `grace-runtime-role`).

**`agentcore create` always creates a subdirectory named after the project, so
`--project-name grace` cannot be run from the repo root** — it collides with the `grace/` Python
package and fails with `A folder named 'grace' already exists in this directory`. There is no
in-place flag; verified that even running it from inside a directory already named `gracesvc` nests
a second `gracesvc/`. Scaffold in a throwaway directory and move the one directory that matters:

```bash
cd /tmp && mkdir acscaffold && cd acscaffold
agentcore create --project-name grace --no-agent --defaults
cp -R /tmp/acscaffold/grace/agentcore <repo>/agentcore
cd <repo> && agentcore validate     # prints "Valid" — the CLI accepts the relocated project
```

`create` also initializes its own git repo inside the scaffold; only `agentcore/` is copied, so that
does not follow. Then register Grace as **BYO code** (`--type create` would scaffold a fresh agent
and ignore `grace/`):

```bash
agentcore add agent --name grace --type byo --build Container --language Python \
  --framework Strands --model-provider Bedrock \
  --code-location . --entrypoint runtime_app.py --protocol HTTP
```

**`add agent` has no flag for the execution role or for environment variables** — but
`agentcore.json`'s runtime object accepts `executionRoleArn` and `envVars`, confirmed against the
published schema (`https://schema.agentcore.aws.dev/v1/agentcore.json`). Without
`executionRoleArn`, CDK would create its own role and Task 6's narrow, audited one would go unused.
Both were added by hand:

```json
"executionRoleArn": "arn:aws:iam::339712964409:role/grace-runtime-role",
"envVars": [
  { "name": "GRACE_STORE", "value": "dynamodb" },
  { "name": "OTEL_SERVICE_NAME", "value": "grace" },
  { "name": "OTEL_SEMCONV_STABILITY_OPT_IN",
    "value": "gen_ai_latest_experimental,gen_ai_unredacted_attributes=" }
]
```

The `envVars` deliberately duplicate the `Dockerfile`'s `ENV` lines. `runtime_app` refuses to start
without the redaction token (hard rule 8), and a runtime-level variable *overrides* the image's — so
setting it in only one place means a future manifest edit can silently strip it from the process
while the Dockerfile still looks correct.

**Confirm the synthesized template before deploying**, since this is the only point where the role
wiring is checkable without paying for a deploy:

```bash
agentcore deploy --dry-run
.venv/bin/python -c "
import json, glob
d = json.load(open(glob.glob('agentcore/cdk/cdk.out/*.template.json')[0]))
for lid, r in d['Resources'].items():
    if r['Type'] == 'AWS::BedrockAgentCore::Runtime':
        print('RoleArn:', r['Properties']['RoleArn'])
        print('Env:', r['Properties'].get('EnvironmentVariables'))
"
```

Observed: `RoleArn: arn:aws:iam::339712964409:role/grace-runtime-role`, and the three env vars
present. Only two IAM roles are created by the stack, both for the CodeBuild container build; no
runtime role is generated. Then:

```bash
agentcore deploy -y --verbose
```

13 resources, ~4 minutes, `CREATE_COMPLETE` on the first attempt — no IAM iteration was needed,
because Task 6 had already added `bedrock:Converse`/`ConverseStream` and the ECR pull grants. The
container is built in **CodeBuild on ARM64**, not locally, so Podman is needed only for Step 6's
smoke test.

### `ENV DOCKER_CONTAINER=1` is required, and its absence is silent

`BedrockAgentCoreApp.run()` auto-detects its bind host — read the source in the installed SDK: it
binds `0.0.0.0` when `/.dockerenv` exists **or** `DOCKER_CONTAINER` is set, and `127.0.0.1`
otherwise. **Podman creates neither.** Measured against the real image: the container started, logged
normally, reported `Up` to `podman ps`, and `curl` against the published port returned **HTTP 000** —
a timeout, with nothing in the log, because uvicorn was listening only on loopback inside the
container.

The CLI's own reference template sets this variable, so relying on the `/.dockerenv` fallback is the
deviation. `tests/test_runtime_app.py::test_the_container_sets_the_bind_host_signal` asserts it,
because the failure shape is the worst available here: the runtime deploys cleanly, reports READY,
and fails every invocation with a connection error.

### Step 6 — the local smoke test

```bash
export DOCKER_HOST="$(podman machine inspect \
  --format '{{.ConnectionInfo.PodmanSocket.Path}}' podman-machine-default)"
podman build --platform linux/arm64 -t grace-local-test .
podman run --rm grace-local-test python -c "import runtime_app; print('imports OK')"
```

Then prove the hard-rule-8 guard actually refuses. **Both of these must exit non-zero** — the second
is the one a presence-only check would let through, and it exports the full household record while
reading as "configured":

```bash
# Token absent.
podman run --rm -e OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental \
  grace-local-test python -c "import runtime_app"; echo "exit=$?"   # 1
# Token present with a NON-EMPTY allowlist.
podman run --rm -e 'OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,gen_ai_unredacted_attributes=gen_ai.input.messages;gen_ai.output.messages' \
  grace-local-test python -c "import runtime_app"; echo "exit=$?"   # 1
```

Observed `exit=1` for both. And the served endpoints, with the container's own `CMD`:

```bash
podman run --rm -d --name grace-smoke -p 18080:8080 grace-local-test
curl -s http://localhost:18080/ping
curl -s -X POST http://localhost:18080/invocations \
  -H 'Content-Type: application/json' -d '"not a dict"'
podman rm -f grace-smoke
```

Observed `{"status":"Healthy",...}` (HTTP 200) and
`{"status": "error", "case_id": "", "detail": "payload must be a JSON object, got str"}` — a
reportable outcome rather than an opaque 500, which is what Step Functions can branch on.

### Step 9 — invoking the deployed runtime

**`list_agent_runtimes` paginates, and Grace is not on the first page.** This account holds 16
runtimes across 10+ unrelated projects; the plan's snippet used a single un-paginated call inside
`next(...)`, which raised `StopIteration` with the runtime sitting `READY` the whole time. Page
through it:

```bash
.venv/bin/python -c "
import boto3, json, uuid
from infra import naming
ctl = boto3.client('bedrock-agentcore-control', region_name=naming.REGION)
runtimes, token = [], None
while True:
    page = ctl.list_agent_runtimes(**({'nextToken': token} if token else {}))
    runtimes.extend(page['agentRuntimes'])
    token = page.get('nextToken')
    if not token: break
arn = next(r['agentRuntimeArn'] for r in runtimes
           if r['agentRuntimeName'].startswith('grace'))
c = boto3.client('bedrock-agentcore', region_name=naming.REGION)
session = f'grace-c-010-{uuid.uuid4()}'
assert len(session) >= 33            # Runtime requires 33+ characters
r = c.invoke_agent_runtime(
    agentRuntimeArn=arn, runtimeSessionId=session,
    payload=json.dumps({'case_id': 'c-010', 'today': '2026-10-01'}).encode())
body = json.loads(r['response'].read())
print(body)
assert body['status'] == 'escalated' and body.get('filed') is not True
"
```

Observed, in 9.1s (Bedrock included):

```json
{"status": "escalated", "case_id": "c-010",
 "reason": "missing_document: proof_of_residency is not on file (Grace has already messaged the family.)",
 "deadline": "2026-10-18", "trace_id": null}
```

The escalation boundary holds in the deployed image: `c-010` is missing `proof_of_residency`, the
gate escalated it with its own typed reason, and no renewal was filed. `agentcore exec --runtime
grace 'python -c "..."'` runs a one-shot command in the live container and is the fastest way to
inspect the deployed process's environment.

### Step 10 — the ledger rows, and the one expectation that is NOT met

```bash
aws dynamodb query --table-name grace-cases --region us-east-1 \
  --key-condition-expression 'pk = :pk' \
  --expression-attribute-values '{":pk":{"S":"CASE#c-010"}}' \
  --query 'Items[].{sk:sk.S,kind:kind.S,trace:d_trace_id.S}' --output table
```

12 rows landed in real DynamoDB: five paired `tool_call`/`tool_result`, a `family_message_sent`, and
one `ESCALATION#` row, sort keys in UTC with correct `#00000N` sequence numbers.

**`d_trace_id` is `NULL` on every row, not a 32-hex value.** The plan's Step 10 expectation is not
met, and this is a documentation error rather than a code defect. Evidence, gathered inside the live
container:

- `AGENT_OBSERVABILITY_ENABLED=true` is set, so `setup_telemetry()` correctly skips — Appendix E.3's
  reason still stands, constructing `StrandsTelemetry()` there would hijack the provider.
- But **Runtime does not install an in-process tracer provider.** The deployed process reports
  `opentelemetry.trace.ProxyTracerProvider`, and a span started from it comes back
  `is_valid: False`, `trace_id: 00000...0`. Ports 4316/4317/4318 are all closed in the container.
- Runtime injects `OTEL_PYTHON_DISTRO=aws_distro` and `OTEL_PYTHON_CONFIGURATOR=aws_configurator`,
  which are read **only** by `opentelemetry-instrument` from `aws-opentelemetry-distro` — the
  package and the launcher CLAUDE.md forbids.
- Account-wide, `aws xray get-trace-summaries` returns zero traces for the period, so no other
  deployed project in this account exports spans either. Transaction Search is ACTIVE; nothing is
  producing spans for it to index.

So "Runtime instruments itself" is true of the log group and the OTEL *environment*, and false of the
tracer provider. `_current_trace_id()` returning `None` is honest: tracing genuinely was not
configured for that run, and Task 9 of Plan 1 established that losing the trace ID must never cost a
ledger row. **Nothing was changed to make the trace ID appear** — not `observability.py`, not the
`AGENT_OBSERVABILITY_ENABLED` guard, and `aws-opentelemetry-distro` was not added.

**This blocks Task 10's headline verification.** Filtering Transaction Search on
`grace.gate_decision = "escalate"` returns nothing while no spans exist, so Task 10 must either
resolve in-process tracing within the no-distro constraint or present the DynamoDB ledger as the
demo's evidence instead. The ledger is the stronger artifact anyway — CLAUDE.md already says a trace
can be dropped by sampling and a ledger row cannot.

### Task 8 — the scheduled sweep (verified end to end, 2026-09-03)

```text
lambda:        arn:aws:lambda:us-east-1:339712964409:function:grace-invoke-case
state machine: arn:aws:states:us-east-1:339712964409:stateMachine:grace-sweep
rule:          arn:aws:events:us-east-1:339712964409:rule/grace-daily-sweep
```

**The deployed sweep reports `{'acted': 9, 'escalated': 3}` in 61s** —
execution `arn:aws:states:us-east-1:339712964409:execution:grace-sweep:b756eb11-48e2-4d48-87e0-b1def55ed5dd`,
status `SUCCEEDED`. `c-010` escalated on `missing_document`, `c-011` on `material_income_change`
(30.0%), `c-012` on `source_conflict`, each with the gate's own typed reason plus the referee's
`AMBIGUOUS:` question. Confirmed on the ledger afterwards: `renewal_submitted` appears **0** times for
all three, and once each for the nine clean cases. Hard rule 6 holds against deployed infrastructure.

#### `invoke_agent_runtime` is not idempotent, and boto3 retries it five times by default

The most serious defect found in Plan 2. Measured with a black-hole socket server (accepts the
connection, reads the request, never replies) so every attempt ends in `ReadTimeoutError` and the
accept count *is* the number of HTTP attempts:

| Client config | Attempts |
|---|---|
| plan's draft (no `Config`) | **5** |
| `retries={"mode": "standard", "max_attempts": 1}` | **2** |
| `retries={"total_max_attempts": 1}` | **1** |

`ReadTimeoutError` maps to `GENERAL_CONNECTION_ERROR` in botocore's own retry table, so a runtime
that is merely *slow* is indistinguishable from a dropped connection. Each retry re-runs the entire
graph for the same case, so a slow case could file the same renewal up to five times — hard rule 6's
harm from the other direction. Plan 1 measured one real eval run at 512s against a typical 75s, and
the default `read_timeout` is **60s**, so this was reachable rather than theoretical.

`total_max_attempts` is the only setting that means "do not retry": `max_attempts` counts *retries*
in standard mode, so a test asserting `max_attempts == 1` passes while the client still retries.
`infra/lambda_src/handler.py` sets `total_max_attempts=1` and `read_timeout=870` (clearing the
graph's 420s node timeout, staying under the Lambda's 900s deadline).

#### `Catch` cannot see a handler that returns `{"status": "error"}`

The plan's definition had a `Catch` branch and nothing else. But `grace/entrypoint.py` and the Lambda
handler are both written *never to raise* — so Step Functions sees a **successful** task carrying an
error payload, and `Catch` does not fire. That made the two failure paths opposite: a Lambda killed at
its deadline got an escalation row, and a Lambda that reported the same failure politely got none.
**The family that disappeared was the one whose failure was handled better.**

Fixed with a `CheckOutcome` Choice state routing `$.status == "error"` to a second DynamoDB writer.
Both writers are built by one function so the row shape cannot drift. Re-validated against the real
`stepfunctions:validate_state_machine_definition` API after the change: **`result: OK`, zero
diagnostics**, intrinsics and `$$.State.EnteredTime` still parse. Proven live with a bogus
`c-nonexistent` case (no Bedrock cost): the history shows
`InvokeCase → CheckOutcome → RecordReportedFailure → ReportFailure` and a real `PENDING_CASEWORKER`
row landed. That row was deleted afterwards so it does not pollute the caseworker queue.

#### Task 9's draft metric-filter pattern matches zero events

Tested with `logs:test_metric_filter` against the real log events, which is ingestion-independent —
`filter_log_events` returns 0 for *every* pattern including the empty one for several minutes after a
run, and `storedBytes` reads 0, so that API cannot be used to check a pattern promptly.

| Pattern | Matches |
|---|---|
| Task 9 draft: `{ $.status = "escalated" }` | **0 of 3** |
| `{ $.details.output = "*escalated*" }` | 14 (six event types) |
| `{ $.type = "TaskStateExited" && $.details.output = "*\"status\":\"escalated\"*" }` | **3** ✅ |

There is no top-level `status` field. A Step Functions log event's keys are `type`, `details`,
`execution_arn`, `id`, `previous_event_id`, `redrive_count`, `event_timestamp` — the outcome payload
sits at `$.details.output` as an **embedded JSON string**, not a nested object. Without the `$.type`
anchor the same outcome is counted six times (`ChoiceStateExited`, `PassStateExited`,
`TaskStateExited`, `TaskSucceeded`, `MapStateExited`, `ExecutionSucceeded`), so the alarm's
`Threshold: 3` would compare against 14 and never fire. **Task 9 must use the type-anchored pattern
above**, and should assert the count is exactly 3 rather than non-zero.

#### Known cosmetic inconsistency: two `escalated_at` formats

The state machine writes `$$.State.EnteredTime`, which renders UTC as `2026-09-03T03:34:31.779Z`,
while `grace/cases/dynamo_store.py` writes `...+00:00` for the same instant. `Z` and `+` sort
inconsistently against each other bytewise, so the escalation GSI's range key is not globally ordered
across the two writers. **Deliberately not fixed:** the caseworker queue's meaningful order is by
`deadline`, rows from either writer sort correctly among themselves, and normalizing would mean
reformatting a timestamp inside a `States.Format` intrinsic — more risk than the defect.

### Task 10 — a redeploy is outstanding

**The deployed image predates the PII fix.** Runtime `grace_grace-oTyyvo8stE` is version 1, built
`2026-09-03T03:04:55Z`. Task 10's verification scan found a household name in
`/aws/vendedlogs/states/grace-sweep-Logs` (`Mensah`, 16 times in 302 events) and the fix —
`read_case` no longer returning `display_name` — landed in the repository afterwards. See
`docs/deployed-verification.md` §5 for the path and the reasoning.

So **the next deploy is not optional**, and it is the one step of this runbook still to run:

```bash
export DOCKER_HOST="$(podman machine inspect \
  --format '{{.ConnectionInfo.PodmanSocket.Path}}' podman-machine-default)"
agentcore deploy -y --verbose
```

Then re-run the scan from `docs/deployed-verification.md` §5 against events written *after* the
redeploy and confirm zero hits. Pre-fix events cannot be unwritten; they age out with the log group's
retention. Until the redeploy, "no household identity reaches CloudWatch" is true of the repository
and not of the running system — do not state it unqualified.


