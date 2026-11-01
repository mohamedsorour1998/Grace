"""The A2A server: AgentCore's A2A contract, served by the real SDK app.

`/ping`, the agent card, and JSON-RPC `message/send` on `/`, with errors as
HTTP 200 responses. The executor's `process` is a fake, so nothing here calls
Bedrock; the app, routing, and JSON-RPC handling are the SDK's own.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

import a2a_app
from grace.a2a import CaseExecutor

ROOT = Path(__file__).resolve().parents[1]
GOOD = {"case_id": "c-001", "today": "2026-10-01"}


def _client(process=lambda p: {"status": "acted", "case_id": p["case_id"], "filed": True}) -> httpx.AsyncClient:
    app = a2a_app.build_app(CaseExecutor(process=process))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://grace.test")


def _send(part: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "message/send",
        "params": {"message": {"role": "user", "messageId": "m-1", "kind": "message", "parts": [part]}},
    }


def _parts(body: dict) -> list[dict]:
    return body["result"]["parts"]


async def test_the_server_answers_ping():
    async with _client() as client:
        reply = await client.get("/ping")
    assert reply.status_code == 200
    assert "status" in reply.json()


async def test_the_card_is_served_at_the_well_known_path():
    async with _client() as client:
        reply = await client.get("/.well-known/agent-card.json")
    assert reply.status_code == 200
    card = reply.json()
    assert card["name"] == "Grace"
    assert [s["id"] for s in card["skills"]] == ["check-case"]


async def test_message_send_returns_the_outcome():
    async with _client() as client:
        reply = await client.post("/", json=_send({"kind": "data", "data": GOOD}))
    assert reply.status_code == 200
    data = next(p["data"] for p in _parts(reply.json()) if p["kind"] == "data")
    assert data == {"status": "acted", "case_id": "c-001", "filed": True}


async def test_a_malformed_request_is_http_200_with_an_explanation():
    async with _client() as client:
        reply = await client.post("/", json=_send({"kind": "text", "text": "not json"}))
    assert reply.status_code == 200
    data = next(p["data"] for p in _parts(reply.json()) if p["kind"] == "data")
    assert data["status"] == "error"
    assert "case_id" in data["reason"]


async def test_a_body_that_is_not_json_rpc_is_http_200():
    async with _client() as client:
        reply = await client.post("/", content=b"{oops", headers={"content-type": "application/json"})
    assert reply.status_code == 200
    assert "error" in reply.json()


def _exec_fresh(monkeypatch) -> None:
    """Re-execute a2a_app's source, as the runtime_app tests do: an already
    imported module would pass whether or not its startup checks exist."""
    source = Path(a2a_app.__file__).read_text(encoding="utf-8")
    exec(compile(source, a2a_app.__file__, "exec"), {"__name__": "_probe"})


def test_the_a2a_app_refuses_without_redaction(monkeypatch):
    monkeypatch.setenv("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")
    with pytest.raises(RuntimeError, match="gen_ai_unredacted_attributes="):
        _exec_fresh(monkeypatch)


def test_the_a2a_app_refuses_an_unsupported_region(monkeypatch):
    from infra import naming

    monkeypatch.setattr(naming, "REGION", "eu-central-1")
    with pytest.raises(ValueError, match="us-east-1.*us-west-2"):
        _exec_fresh(monkeypatch)


def _logical_lines(name: str) -> list[str]:
    """Dockerfile instructions with continuations joined and comments dropped."""
    text = (ROOT / name).read_text()
    joined = re.sub(r"\\\n\s*", " ", text)
    return [line.strip() for line in joined.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_the_a2a_image_matches_the_http_image():
    # The redaction token, the bind-host signal, the store, and the non-root
    # user are what make the HTTP image safe; the A2A image must carry the same.
    http, a2a = _logical_lines("Dockerfile"), _logical_lines("Dockerfile.a2a")
    pick = lambda lines, word: [l for l in lines if l.split()[0] == word]  # noqa: E731
    for word in ("FROM", "ENV", "USER"):
        assert pick(http, word) == pick(a2a, word), word
    assert "EXPOSE 9000" in a2a
    assert 'CMD ["python", "-m", "a2a_app"]' in a2a
    text = "\n".join(a2a)
    # Installed from the lock, with hashes, so the pins and the uv constraints in
    # pyproject.toml are what the container actually gets.
    assert "uv export --frozen" in text and "--extra a2a" in text and "--require-hashes" in text
    # Measured in CI's first run: without --no-config, `uv pip install` reads the
    # project's [tool.uv] constraint-dependencies, and hash mode rejects any
    # requirement that is not `==` — so the image never built. The exported file
    # already carries the constrained versions.
    install = next(l for l in a2a if "--require-hashes" in l)
    assert "--no-config" in install
    # uv builds the image and is not needed to run it. Measured in CI's third
    # run: the scan flagged uv's own bundled CA certificates (CRITICAL), so it is
    # removed from the final image rather than carried for nothing.
    uv_lines = [i for i, l in enumerate(a2a) if l.startswith("RUN ") and "uv " in l]
    assert "pip uninstall -y uv" in a2a[uv_lines[-1]]  # removed in the last step that uses it


async def test_sdk_logs_never_quote_a_malformed_request(caplog):
    # Hard rule 9. Measured 2026-11-01: the A2A SDK logs every request body at
    # DEBUG and a validation failure's input at WARNING/ERROR, so a caller that
    # put a name or a phone number in a malformed part had it written to the log.
    import logging

    caplog.set_level(logging.DEBUG)
    malformed = {"kind": "data", "data": "The Example Household +15550001"}
    async with _client() as client:
        reply = await client.post("/", json=_send(malformed))
    assert reply.status_code == 200
    assert "withheld" in caplog.text  # the SDK did log, so the check below is not vacuous
    assert "Example" not in caplog.text
    assert "+1555" not in caplog.text
