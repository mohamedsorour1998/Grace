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

---

## Deploy sequence

Filled in as tasks complete. See `docs/deployed-verification.md` for the evidence that the deployed
system behaves correctly.
