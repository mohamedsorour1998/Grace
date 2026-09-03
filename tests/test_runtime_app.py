"""The container's entrypoint. Thin on purpose.

All logic lives in `grace.entrypoint`; this asserts the wiring and the one
environment invariant that must hold in the deployed process.

**Importing this module is itself a test.** `runtime_app` raises at import time
when span redaction is not configured (hard rule 8), so a collection error here
means the token is missing from the environment — see `tests/conftest.py`, which
sets it at conftest *import* time rather than in a fixture. A session-scoped
autouse fixture is too late: pytest imports test modules during collection,
which happens before any fixture runs. Measured, not assumed — the plan's draft
used a fixture, and `import runtime_app` raised during collection.
"""

from __future__ import annotations

import runtime_app


def test_the_entrypoint_delegates_to_process_case(monkeypatch):
    seen = {}

    def fake_process_case(payload):
        seen.update(payload)
        return {"status": "acted", "case_id": payload["case_id"], "filed": True}

    monkeypatch.setattr(runtime_app, "process_case", fake_process_case)
    out = runtime_app.invoke({"case_id": "c-001", "today": "2026-10-01"}, None)
    assert out["status"] == "acted"
    assert seen["case_id"] == "c-001"


def test_the_entrypoint_never_raises_out_of_the_container(monkeypatch):
    """An unhandled exception inside Runtime is an opaque 500 to the caller.
    Step Functions can branch on `{"status": "error"}`; it cannot branch on a
    stack trace it never receives. Fail closed, and stay reportable."""

    def boom(payload):
        raise RuntimeError("something deep failed")

    monkeypatch.setattr(runtime_app, "process_case", boom)
    out = runtime_app.invoke({"case_id": "c-001"}, None)
    assert out["status"] == "error"
    assert "something deep failed" in out["detail"]
    # The case must stay identifiable, or the escalation row Step Functions'
    # Catch branch writes cannot name the family.
    assert out["case_id"] == "c-001"


def test_a_non_dict_payload_is_refused_rather_than_forwarded(monkeypatch):
    """`BedrockAgentCoreApp` passes payloads through **unchanged** — its own
    docstring says the application must validate before forwarding. A string
    payload would otherwise reach `.get()` and raise an AttributeError deep in
    `process_case`."""
    out = runtime_app.invoke("not a dict", None)
    assert out["status"] == "error"


def test_the_app_is_a_bedrock_agentcore_app():
    """The wiring itself: a plain function that Runtime never calls would look
    identical to a working agent until the first invocation returns nothing."""
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    assert isinstance(runtime_app.app, BedrockAgentCoreApp)


def test_the_entrypoint_is_the_registered_handler():
    """`@app.entrypoint` only records the function in `app.handlers["main"]` —
    verified by reading the decorator's source in the installed SDK. So a
    module that defines `invoke` but forgets the decorator still exports a
    callable `invoke` that every other test in this file passes against, while
    Runtime has no handler to call and every deployed invocation fails. The
    registration is the deployable property; `invoke` being importable is not.
    """
    assert runtime_app.app.handlers.get("main") is runtime_app.invoke


def test_invoke_is_callable_with_context_omitted():
    """Runtime supplies a context object, but nothing in the SDK guarantees the
    arity. Defaulting it keeps the local smoke test (`invoke({...})`) and the
    deployed call (`invoke(payload, context)`) the same function."""
    out = runtime_app.invoke("not a dict")
    assert out["status"] == "error"


def test_the_redaction_guard_rejects_a_missing_token():
    """Hard rule 8, checked where it matters — in the deployed process, not in
    `.env.example`. Absence of `gen_ai_unredacted_attributes=` disables span
    redaction entirely and exports the full household record to CloudWatch."""
    from grace.observability import redaction_is_configured

    assert not redaction_is_configured(
        {"OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental"}
    )
    assert redaction_is_configured(
        {"OTEL_SEMCONV_STABILITY_OPT_IN":
         "gen_ai_latest_experimental,gen_ai_unredacted_attributes="}
    )


def test_the_module_refuses_to_import_without_the_redaction_token(monkeypatch):
    """The guard is a *startup refusal*, and the tests above cannot see that.

    Every other test in this file runs against an already-imported module, so
    they pass identically whether the `raise` is present or deleted — the
    import succeeded either way, because `tests/conftest.py` set the token.
    This re-executes the module's source with the token stripped and asserts it
    raises, which is the only thing that distinguishes a guard from a comment.

    `exec` of the real source rather than `importlib.reload`: reload would
    rebind `sys.modules["runtime_app"]` to a half-initialized module if the
    raise fires, leaving whatever test runs next importing a broken object.
    """
    from pathlib import Path

    monkeypatch.setenv("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")
    source = Path(runtime_app.__file__).read_text(encoding="utf-8")

    import pytest

    with pytest.raises(RuntimeError) as excinfo:
        exec(compile(source, runtime_app.__file__, "exec"), {"__name__": "_probe"})

    assert "gen_ai_unredacted_attributes=" in str(excinfo.value)


def test_the_container_dockerfile_sets_the_redaction_token():
    """The guard refuses to start without the token, so the image must supply
    it — otherwise a deploy that passes every test here dies at container
    start. Asserted against the empty value specifically: hard rule 8 is
    defeated by a *non-empty* allowlist just as surely as by absence, and the
    two failures look identical in a `grep` for the key name.
    """
    from pathlib import Path

    from grace.observability import redaction_is_configured

    dockerfile = Path(__file__).parent.parent / "Dockerfile"
    prefix = "ENV OTEL_SEMCONV_STABILITY_OPT_IN="
    values = [
        line.strip().removeprefix(prefix).strip()
        for line in dockerfile.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith(prefix)
    ]
    assert values, "the Dockerfile must set OTEL_SEMCONV_STABILITY_OPT_IN"
    for value in values:
        assert redaction_is_configured({"OTEL_SEMCONV_STABILITY_OPT_IN": value}), (
            f"the Dockerfile's redaction token does not redact everything: {value!r}"
        )


def test_the_container_sets_the_bind_host_signal():
    """Without this the runtime deploys cleanly and cannot serve.

    `BedrockAgentCoreApp.run()` auto-detects its bind host — read its source in
    the installed SDK: `0.0.0.0` if `/.dockerenv` exists OR `DOCKER_CONTAINER`
    is set, else `127.0.0.1`. **Podman creates neither.** Measured against the
    real image: the container started, logged normally, reported `Up` to
    `podman ps`, and `curl` to the published port returned HTTP 000 — a timeout
    with nothing in the log, because uvicorn was bound to loopback inside the
    container. Adding `ENV DOCKER_CONTAINER=1` returned HTTP 200 from `/ping`
    on the next build.

    The CLI's own reference template sets this variable too, so relying on the
    `/.dockerenv` fallback is the deviation, not setting it explicitly. This is
    the worst failure shape available here — Runtime would report READY and fail
    every invocation with a connection error — so it gets its own assertion
    rather than being left to the smoke test someone may not re-run.
    """
    from pathlib import Path

    dockerfile = (Path(__file__).parent.parent / "Dockerfile").read_text(encoding="utf-8")
    assert any(
        line.strip().startswith("ENV DOCKER_CONTAINER=")
        and line.strip().removeprefix("ENV DOCKER_CONTAINER=").strip()
        for line in dockerfile.splitlines()
    ), (
        "the Dockerfile must set a non-empty ENV DOCKER_CONTAINER, or "
        "BedrockAgentCoreApp.run() binds 127.0.0.1 and nothing can reach it"
    )


def test_the_container_image_carries_what_the_package_imports():
    """`infra/` is a real requirement, not a tidy-up: `grace/cases/dynamo_store.py`
    and `grace/memory.py` both import `infra.naming`, and `grace/cases/store.py`
    reads `fixtures/households.yaml` at a path relative to the package. A
    missing COPY fails at *container start*, after a green build and a green
    test suite — the most expensive place to find it.
    """
    from pathlib import Path

    dockerfile = (Path(__file__).parent.parent / "Dockerfile").read_text(encoding="utf-8")
    for required in ("grace/", "infra/", "fixtures/", "runtime_app.py"):
        assert f"COPY {required}" in dockerfile, f"the Dockerfile must COPY {required}"


def test_the_image_does_not_use_the_templates_forbidden_defaults():
    """Two defaults the CLI's template ships and CLAUDE.md forbids, because
    AgentCore Runtime instruments itself: the `aws-opentelemetry-distro`
    package, and running under `opentelemetry-instrument`. Both would be a
    silent regression — the container still starts, and traces still appear.

    Checked against the `CMD` line and the declared dependencies specifically,
    not against the whole file: both documents *mention* the forbidden names in
    comments explaining the decision, and a substring search over the file
    would fail on the explanation rather than on a violation.
    """
    from pathlib import Path

    root = Path(__file__).parent.parent

    cmds = [
        line.strip()
        for line in (root / "Dockerfile").read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("CMD")
    ]
    assert cmds == ['CMD ["python", "-m", "runtime_app"]'], cmds

    import tomllib

    with (root / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    declared = list(pyproject["project"]["dependencies"])
    for extra in pyproject["project"].get("optional-dependencies", {}).values():
        declared.extend(extra)
    for forbidden in ("aws-opentelemetry-distro", "strands-agents-tools",
                      "strands-agents-evals"):
        assert not any(forbidden in spec for spec in declared), forbidden
