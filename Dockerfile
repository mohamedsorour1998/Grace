# From public.ecr.aws, not Docker Hub: the CLI's own template uses the ECR
# mirror to avoid Docker Hub rate limits in CodeBuild. Python 3.12 to match the
# local toolchain — `agentcore add agent` records runtimeVersion PYTHON_3_14 by
# default, but the image is what actually runs.
FROM public.ecr.aws/docker/library/python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1

# Dependencies before source, so a code change does not reinstall the world.
# README.md and grace/ are copied first because the build backend reads both:
# `readme = "README.md"` and `packages = ["grace"]`.
COPY pyproject.toml README.md ./
COPY grace/ ./grace/
RUN uv pip install --system --no-cache .

# `infra/` is required, not optional: grace/cases/dynamo_store.py and
# grace/memory.py both import infra.naming. `fixtures/` likewise —
# grace/cases/store.py reads fixtures/households.yaml at a path relative to the
# package, and the fixtures are the single source of household truth (the
# DynamoDB table holds only ledger and escalation rows). Omitting either fails
# at container start, not at build.
COPY infra/ ./infra/
COPY fixtures/ ./fixtures/
COPY runtime_app.py ./

# Hard rule 8. The trailing `=` is load-bearing: the value lists what to leave
# UNREDACTED, so an empty value means "redact everything". Without the token,
# redaction is off entirely and the full household record exports to CloudWatch;
# with a non-empty value it exports too, while still reading as "configured".
# `runtime_app` refuses to start unless this redacts everything, so a bad edit
# here fails loudly at container start rather than leaking silently.
ENV OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,gen_ai_unredacted_attributes=
# Groups Grace's agents under one name in the CloudWatch GenAI dashboard; the
# default `strands-agents` is indistinguishable from any other Strands app.
ENV OTEL_SERVICE_NAME=grace
ENV GRACE_STORE=dynamodb
# `BedrockAgentCoreApp.run()` auto-detects its bind host: `0.0.0.0` when it
# believes it is containerized, `127.0.0.1` otherwise. It decides by looking for
# `/.dockerenv` OR this variable — and **Podman does not create `/.dockerenv`**.
# Measured: without this the container starts, logs normally, reports healthy to
# `podman ps`, and binds only the loopback interface, so every request from
# outside the container times out with no error in the log. Setting the
# variable the SDK itself checks makes the bind host explicit instead of
# depending on which engine built the image. Binding all interfaces is correct
# *inside* the container — that is how Runtime reaches the process — and this is
# not set for local runs, which keeps the SDK's loopback default there.
ENV DOCKER_CONTAINER=1

RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

# AgentCore Runtime service contract: 8080 HTTP, 8000 MCP, 9000 A2A.
EXPOSE 8080

# NOT `opentelemetry-instrument` — that is the CLI template's default and
# requires aws-opentelemetry-distro, which CLAUDE.md forbids because Runtime
# instruments itself.
CMD ["python", "-m", "runtime_app"]
