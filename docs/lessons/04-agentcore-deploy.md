# Lessons: deploying to AgentCore (Plan 2)

Moved verbatim from `CLAUDE.md` on 2026-10-31. Each lesson was learned on a real defect; paths and counts are as of when it was written.

### What Plan 2 established — follow these

**The container engine is Podman, not Docker.** Docker's binary is installed but its daemon is
not running and is not used. `podman machine start`, then
`export DOCKER_HOST="$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}'
podman-machine-default)"` — that export is mandatory for any Docker-API client, including the
`agentcore` CLI, because `podman-mac-helper` is not installed so `/var/run/docker.sock` does not
exist. The VM is native `linux/arm64`, which is what Runtime requires, so `--platform linux/arm64`
is a no-op rather than an emulated cross-build. Note the socket path lives under `$TMPDIR` and
changes if the machine is recreated — read it, never hardcode it.

**The `agentcore` CLI is 0.28.1 and the appendices were written against 0.24.2. Four command
shapes differ, all verified against `--help`:** `deploy` deploys **via CDK** (already bootstrapped
in this account, `CDKToolkit` from Feb 2026); `create` takes `--name`/`--project-name` rather than
a positional and **scaffolds a new project** rather than registering existing code; `add` is
subcommand-based (`agent`, `harness`, `memory`, `gateway`, …); and Grace needs
`add agent --type byo --code-location . --entrypoint runtime_app.py` — `--type create` would
generate a fresh agent and ignore `grace/` entirely. `docs/runbooks/deploy.md` is authoritative over
the appendices wherever they disagree.

**The Runtime entrypoint is `BedrockAgentCoreApp`, not a bare handler.** Verified by scaffolding a
template agent and reading it: `app = BedrockAgentCoreApp()`, `@app.entrypoint` on
`invoke(payload, context)`, `app.run()` under `__main__`. It is a Starlette app on **port 8080**
(8000 MCP, 9000 A2A per the Runtime service contract). Its own docstring says payloads are passed
through **unchanged**, so the app must validate the payload shape itself. Grace's entrypoint is a
plain `def` returning a dict; the template's is an async generator only because it is a chat agent.
**Two template defaults Grace rejects:** `aws-opentelemetry-distro` and
`CMD ["opentelemetry-instrument", ...]` — both forbidden here because Runtime instruments itself.

**A sort key built from `isoformat()` is only correctly ordered if the datetime is UTC.**
`LedgerEntry` requires an *aware* datetime and nothing more, so any offset reaches the key builder,
and DynamoDB compares a range key **bytewise**: `2026-10-01T08:00:00-05:00` is one hour *after*
`2026-10-01T12:00:00+00:00` in real time but sorts *before* it as a string. That silently inverts
`ScanIndexForward=True` — the ordering Task 8's evals read to assert reads precede actions — with no
error anywhere. `infra/naming.py`'s `_utc_stamp` normalizes first. **The naive-datetime `raise` must
stay ahead of the conversion:** a naive `.astimezone()` silently assumes the local clock, which
looks correct on a UTC host and is wrong where the code was written. It also refuses
`utcoffset() is None` — verified reachable, not defensive padding: a `tzinfo` subclass may return no
offset, `LedgerEntry` *accepts* such a value, and `isoformat()` then emits no offset at all.

**A provisioning script that swallows a "not ready yet" error reports success while the control is
absent.** `provision_dynamodb` originally caught `ContinuousBackupsUnavailableException` and moved
on; measured across three runs on throwaway tables, point-in-time recovery came out
`ENABLED, ENABLED, DISABLED` — one run in three left the ledger table unrecoverable while the script
exited 0. The fix is a bounded retry on that error code only **plus a read-back that is the sole
arbiter of success**: "the API call returned" and "the control is on" are different claims, and only
the second one matters. Non-transient errors re-raise immediately rather than burning the retry
budget. **Raising is correct here even though Grace's observability paths deliberately fail open** —
`infra/` is a provisioning script, not the request path, so a loud failure blocks a deploy and the
operator re-runs, which is exactly what idempotence exists for.

**Prove a new test fails against the code it was written to catch.** Both Task 1 defects were
invisible to the plan's own tests: the sort-key tests used only UTC inputs, so they passed against
the buggy implementation. The fix was confirmed by reverting `naming.py` in a throwaway copy and
watching the two new tests fail there while the five original ones passed either way. This is Task
8's vacuity lesson applied forward — a test that cannot fail is indistinguishable from a passing one
in every report.

**Task 8's state machine definition is pre-verified.** It was validated against the real
`stepfunctions:validate_state_machine_definition` API (`result: OK`, zero diagnostics) including the
`States.Format` intrinsics, `$$.State.EnteredTime`, and the `dynamodb:putItem` parameters nested
inside the Map's `ItemProcessor`. If it stops validating, re-run the validator rather than guessing
— it reports the exact path of the offending field.

**`namespaces` is a legacy parameter on a memory strategy; use `namespaceTemplates`.** The live
`CreateMemory` model documents it as *"a legacy parameter, use `namespaceTemplates`"*. Both exist
and both accept the same list, which is what makes it dangerous — writing the legacy field succeeds,
and a retrieval namespace that does not match what was set at creation **retrieves nothing,
silently**. Any agreement check must read both spellings and assert the result is non-empty, or it
passes vacuously when the service echoes the other field.

**Ledger rows prefix every `detail` key with `d_`, and that is structural, not cosmetic.** A row
carries its own `pk`/`sk`/`case_id`/`at`/`kind` columns, and `detail` is caller-supplied. Merging
`detail` in unprefixed lets `detail={"kind": ...}` overwrite the row's own `kind` — the field
`sweep` classifies a case from (`renewal_submitted`). Verified: the naive merge destroys it silently.
`ledger()` also filters on the `LEDGER#` sort-key prefix, or an escalation row surfaces as a ledger
entry and fails on a missing `at`.

**`ledger()` paginates, because a DynamoDB Query caps at 1MB and signals more via
`LastEvaluatedKey`.** Truncation drops the *newest* rows, which is exactly where
`renewal_submitted` lives, so a single-page read would report a filed renewal as unfiled with no
error. The test fake pages at 3 rows so the loop genuinely iterates — a pagination loop that never
iterates in any test is not tested.

**`os.getenv(name, default)` only defaults on *absence*, not on an empty value.** `GRACE_STORE=`
(set but blank) bypassed the in-memory default and would have had a deployed runtime write its
ledger to memory and discard it at process exit, with the dashboard showing an empty ledger and
nothing saying why. Both stores now raise on an unrecognized value, including blank — the same
allowlist polarity as `APPROVE_DECISIONS`.

**Non-finite floats never reach the ledger.** DynamoDB rejects Infinity and NaN, and
`Decimal("Infinity")` serializes cleanly then raises out of the *read* path later. The sharper
reason is the Task 1 finding above: a NaN disables every comparison it appears in, so it reads back
as a number and behaves like nothing. `math.isfinite()` guards it at the boundary. Real DynamoDB
additionally rejects >38 significant digits, which is what catches `Decimal(1.1)`'s 52-digit binary
noise if anyone reintroduces it.

**A test fake must be able to fail the way the real service fails.** `FakeTable`'s original float
check could never fire, because the serializer stringifies every number before it gets there. It now
enforces the three things the live table enforces (non-finite, >38 digits, exponent overflow) and
pages its queries. A fake that only ever succeeds is worse than no fake, because it makes the suite
look like it covers the boundary.

**Parametrizing over two implementations proves nothing unless you check both ran.**
`tests/test_dynamo_store.py` records the store types a run actually exercised and asserts the set.
Verified by sabotage: making the fixture return the in-memory store for both parameters leaves 41
tests passing and fails only that guard.

**A test that asserts by *raising* inside code that catches `Exception` asserts nothing.**
`AssertionError` is an `Exception`. The Task 3 draft test proved the Runtime guard existed by
monkeypatching `StrandsTelemetry` to raise — but `setup_telemetry` wraps construction in
`except Exception`, so the raise was swallowed and logged, and the test passed with the guard
deleted. Verified both ways. Record the attempt in a sentinel list and assert the list is empty
instead; no `except` can undo an append.

**`StrandsTelemetry.__init__` sets the global tracer provider unconditionally** —
`_initialize_tracer()` calls `trace_api.set_tracer_provider` (`telemetry/config.py:114`). So
*constructing* it is the damage; there is no later call to intercept. A second construction logs
"Overriding of current TracerProvider is not allowed", leaves the first provider global, and orphans
the second with a console exporter attached to nothing. `setup_telemetry` therefore latches on a
module-level flag — set even on failure, so a broken exporter is not retried on every invocation
(Task 4 calls it per case).

**`strands` invokes Bedrock through `converse`/`converse_stream`, not `InvokeModel`.**
`strands/models/bedrock.py:1397` chooses between them. A runtime policy granting only
`bedrock:InvokeModel` leaves every Nova call `implicitDeny` — verified with the IAM simulator against
the real role — and the symptom is an AccessDenied on the first model call of the first deployed
sweep, long after every test has passed. Grant all four actions, still scoped to the three Nova
profiles.

**A Container-build runtime needs ECR pull, or it cannot start.** `ecr:GetAuthorizationToken` is
account-level and must be `Resource: "*"`; the layer-pull actions can be scoped to the account's
registry. Runtime also creates its own log group under `/aws/bedrock-agentcore/`, so the role needs
`CreateLogGroup` on that path. **Another project's shared AgentCore runtime role in this account is a
known-working reference** for what Runtime actually requires — check it before inventing
permissions, and add only what is missing.

**IAM policy shape is checkable without deploying: use `simulate-principal-policy`.** It distinguishes
`explicitDeny` from `implicitDeny`, which is exactly the difference between "this action is blocked
forever" and "this action is simply not granted yet". Grace's runtime role returns `explicitDeny` for
`GetWorkloadAccessTokenForUserId` and `implicitDeny` for the safe JWT path — so Appendix D.1 is
enforced and AgentCore Identity can still be added later without fighting the statement. The
simulator reflects the identity policy only: it says nothing about resource policies, SCPs, or
whether a real `Converse` call succeeds.

**`aws:SourceAccount` in a Lambda trust policy is undocumented, and a condition on an unpopulated key
would make a role permanently unassumable.** Probed with a throwaway role rather than assumed: a
wrong account value was refused with "The role defined for the function cannot be assumed by
Lambda", and the identical call succeeded once corrected — so Lambda does populate the key and the
condition is genuinely evaluated. Both halves of that probe mattered; success alone would not
distinguish "satisfied" from "ignored".

**Renaming a function whose name is also a local variable inside a caller breaks the caller.**
`sweep` binds `gate_reason = _gate_reason(...)`. Rewriting that call site to the promoted public name
gives `gate_reason = gate_reason(...)`, and Python then treats the name as local for the whole
function — `UnboundLocalError` on every case with a non-clean verdict, i.e. all three demo
escalations, from a change intended as a no-op. The aliases (`_gate_reason = gate_reason`) make every
existing call site correct, so **promote the definition and leave the call sites alone.**

**The deployed entrypoint invokes the graph exactly once and carries no resume vocabulary.** Two
assertions, because one is not enough: a call count on a counting fake, *and* an AST check that
`interruptResponse` / `APPROVE_DECISIONS` / `MAX_RESUME_ROUNDS` appear nowhere in the module. A
call-count assertion is only as good as the fake it counts. Verified by sabotage — adding a bounded
resume loop fails four tests, and an *unbounded* one hangs the suite, reproducing Task 6's finding 3
live.

**A failed escalation-row write is recorded in the reason, not swallowed and not raised.** Swallowing
keeps the outcome payload but makes the *missing* durable row invisible — Plan 3's dashboard reads
the escalation GSI, so it would show two escalations while the payload claimed three. Raising is
worse: a returned `{"status": "error"}` does **not** trip Step Functions' `Catch`, so no replacement
row gets written by anyone. Appending the failure to the reason keeps the family escalating and makes
the gap explicit.

**`PersistenceMode` is `FULL`/`NONE` only, so "write to memory selectively" (spec §3.7) is not
expressible as configuration.** It is handled by *scope*: `build_session_manager` is a factory the
caller attaches to the orchestrator, and it is never wired into `build_case_graph` — so the nodes
carrying raw tool output and model errors have nothing to persist through. Pass `FULL` explicitly
rather than by default, because `NONE` leaves retrieval working while writing nothing, which is
indistinguishable from `FULL` in any test that only reads.

**A `Swarm` carrying its own `session_manager` is accepted as a graph node — an SDK gap, not a Grace
bug.** `Graph._validate_node_executor` guards only `isinstance(executor, Agent)`;
`Swarm._validate_swarm` guards only each *member's* `_session_manager`. Neither covers a Swarm
holding its own, and a Swarm inside a Graph is exactly Grace's topology. Grace never attaches one
there, so this is asserted structurally in `tests/test_memory.py` rather than defended against in
`graph.py`. A second test pins the gap so it fails (and can be deleted) if a future SDK closes it.

**Memory strategy names reject hyphens** (`[a-zA-Z][a-zA-Z0-9_]{0,47}`) — `household-facts` was
refused live, which matters because every other Grace resource is `grace-something`. Also verified:
`ListMemories` paginates, `CreateMemory` returns `CREATING` (use the `memory_created` waiter — real
creation took 2m31s), `ValidationException` must not be treated as "already exists" (it would report
success while silently dropping an invalid strategy), and an id prefix match needs anchoring because
this account holds `name` and `name_v2` pairs.


**"Runtime instruments itself" is wrong in a specific and consequential way — verified on the
deployed runtime.** Runtime injects the OTEL environment variables and creates a log group, but it
does **not** install an in-process tracer provider. So `setup_telemetry()` correctly skips (it gates
on `AGENT_OBSERVABILITY_ENABLED`, which Runtime sets), nothing else fills the gap,
`_current_trace_id()` returns `None`, and every deployed ledger row carries `trace_id: NULL`.
Measured after a successful deployed invocation: **zero traces in the account**, no `aws/spans` log
group, and no other deployed project in this account exports spans either — so this is not something
Grace broke. Transaction Search is ACTIVE, so the destination exists; nothing is producing spans to
send to it.

**Do not "fix" this by adding `aws-opentelemetry-distro`, switching the CMD to
`opentelemetry-instrument`, or removing the `AGENT_OBSERVABILITY_ENABLED` guard.** Task 9 of Plan 1
established that losing the trace ID must never cost a ledger row, and the deployed rows are all
present with correct sequence numbers and UTC-normalized sort keys. `trace_id: NULL` is *honest* —
tracing genuinely was not configured for that run. The consequence to be honest about instead: a
Transaction Search query on `grace.gate_decision` returns nothing until spans exist, so that claim
cannot be made in the README as written.

**`DOCKER_CONTAINER=1` is required in the Dockerfile.** `BedrockAgentCoreApp.run()` binds `0.0.0.0`
only if `/.dockerenv` exists *or* `DOCKER_CONTAINER` is set, else `127.0.0.1`. Podman creates no
`/.dockerenv`, so without it the server binds loopback inside the container, `/ping` returns nothing,
and the container still reports healthy. The CLI's own template sets this variable — treat it as the
explicit portable signal and `/.dockerenv` as a Docker-only fallback.


**`invoke_agent_runtime` is not idempotent, and boto3's default client retries it five times.**
Measured with a black-hole socket (accepts, never replies, so the accept count *is* the number of HTTP
attempts): default config **5**, `{"mode": "standard", "max_attempts": 1}` still **2**, and only
`{"total_max_attempts": 1}` gives **1**. `ReadTimeoutError` maps to `GENERAL_CONNECTION_ERROR`, so a
slow runtime looks like a dropped connection — and each retry re-runs the whole graph against the same
case, which could file one renewal five times. Reachable, not theoretical: the default `read_timeout`
is 60s and Plan 1 measured a real run at 512s. `infra/lambda_src/handler.py` sets
`total_max_attempts=1` and `read_timeout=870`, so the **Lambda's** 900s deadline binds first — that
ordering matters, because a Lambda timeout trips Step Functions' `Catch` while a returned error does
not.

**Step Functions' `Catch` cannot see a *returned* `{"status": "error"}`.** Grace's entrypoint and
Lambda handler never raise, so a politely-reported failure looked to Step Functions like a successful
task. The two failure paths were exactly inverted: a killed Lambda got an escalation row, a handled
failure got none — the family who disappeared was the one whose failure was handled *better*. Fixed
with a `CheckOutcome` Choice state routing `status == "error"` to `RecordEscalation`. **When a
component reports failure in its payload rather than by raising, the orchestrator needs an explicit
branch — error handling that only catches exceptions catches the wrong half.**

**A CloudWatch metric filter on Step Functions logs needs `$.type` anchoring and cannot read a
top-level field that does not exist.** Measured against a real sweep's events:
`{ $.status = "escalated" }` matches **0** (there is no top-level `status`; the outcome payload is an
embedded JSON *string* at `$.details.output`), the unanchored
`{ $.details.output = "*escalated*" }` matches **14** (the same outcome counted once per event type
the case passes through), and only
`{ $.type = "TaskStateExited" && $.details.output = "*\"status\":\"escalated\"*" }` matches **3**.
Against a `Threshold: 3` the unanchored version would keep the alarm permanently quiet — the exact
failure an escalation-count alarm exists to avoid. Check a pattern with `logs:test_metric_filter`
(caps at 50 messages per call, so batch); **`filter_log_events` returns 0 for every pattern, including
the empty one, for minutes after a run**, so it cannot be used to validate one promptly.


**A household name reached CloudWatch, and hard rule 8's redaction does not cover the path it took.**
Found by scanning real log events across a 24-hour window: `"Mensah"` appeared **16 times in 302
events** in `/aws/vendedlogs/states/grace-sweep-Logs`, and **zero times in 360 events** in the
runtime's own log group `/aws/bedrock-agentcore/runtimes/grace_grace-oTyyvo8stE-DEFAULT` — so the
carrier is the Step Functions payload, not the agent's stdout. Zero `+1555` hits anywhere. No Grace
code logged it. The chain was
`read_case` handing `display_name` to every model → the **referee quoting it in its deliberation
prose** → `_deliberation_note` appending that conclusion to the escalation reason → the reason being
the Lambda's return payload → Step Functions logging the payload. Span redaction protects `gen_ai.*`
span content; a Step Functions execution payload is not span content.

**The fix is capability absence, not filtering:** `read_case` no longer returns `display_name` at
all. Nothing needed it — `authority.py` never reads it, the action tools never use it, and the
outreach SMS does not address the family by name. Removing it at the source closes every downstream
path at once (model prose, escalation reasons, Step Functions logs, the ledger, future spans) rather
than scrubbing each consumer, which is the same reasoning as layer 1 of the escalation boundary.
**Never put a household's name, phone, or address into a tool's returned text** — a model will
eventually quote it somewhere you are not redacting, and hard rule 9's "never in a span attribute" is
necessary but not sufficient.

**Two consequences to keep straight.** First, the fix is in the repository but **not in the deployed
image**: runtime `grace_grace-oTyyvo8stE` is version 1, built `2026-09-03T03:04:55Z`, from code where
`read_case` still leaked. Pre-fix log events cannot be unwritten and remain until retention expires,
so "no household identity reaches CloudWatch" is true of the repo and needs a redeploy to be true of
the running system. Second, **two existing tests used the surname as their marker** for "the bound
case was read" (`test_read_case_returns_the_bound_case_only` and, more importantly,
`test_an_injected_identity_argument_cannot_redirect_a_read` — the load-bearing layer-2 test). Both now
discriminate on `case_id`, which tests the identical property with an opaque value. If you add a test
that needs to prove *which* case was read, use `case_id`; never reintroduce a name as a fixture marker.
