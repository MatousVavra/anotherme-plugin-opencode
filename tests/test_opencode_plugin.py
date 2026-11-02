"""Unit tests for the opencode plugin — no host, no live server.

Uses FakePluginContext (plugin contract source of truth) plus an
httpx.MockTransport injected through Plugin._new_client to fake the
opencode server.
"""
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fake_plugin_context import FakePluginContext  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "opencode_plugin", HERE.parent / "plugin" / "plugin" / "__init__.py"
)
opencode_plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(opencode_plugin)


@pytest.fixture
def fake_server():
    """Records requests; answers from a route table."""
    state = {"requests": [], "routes": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append((request.method, request.url.path))
        for (method, path), payload in state["routes"].items():
            if request.method == method and request.url.path == path:
                if isinstance(payload, bool):
                    return httpx.Response(200, json=payload)
                if isinstance(payload, int):
                    return httpx.Response(payload)
                if isinstance(payload, tuple):
                    return httpx.Response(payload[0], text=payload[1])
                if isinstance(payload, Exception):
                    raise payload
                return httpx.Response(200, json=payload)
        return httpx.Response(404, text="not found")

    state["handler"] = handler
    return state


@pytest.fixture
def plugin(fake_server):
    p = opencode_plugin.Plugin()
    ctx = FakePluginContext(config={"base_url": "http://testserver"})
    p.on_load(ctx)
    # Inject the mock transport.
    p._new_client = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(fake_server["handler"]),
        base_url="http://testserver",
    )
    return p


@pytest.fixture
def client(plugin):
    app = FastAPI()
    for name, router in plugin._ctx.registry.routers:
        app.include_router(router, prefix=f"/plugins/{name}")
    return TestClient(app)


def test_on_load_registers_router(plugin):
    assert len(plugin._ctx.registry.routers) == 1
    name, router = plugin._ctx.registry.routers[0]
    assert name == "fake"
    paths = {r.path for r in router.routes}
    assert "/status" in paths
    assert "/sessions" in paths
    assert "/sessions/{session_id}/prompt" in paths


def test_status_connected(client, fake_server):
    fake_server["routes"][("GET", "/global/health")] = {"healthy": True, "version": "1.2.3"}
    fake_server["routes"][("GET", "/project")] = [{"id": "p1", "worktree": "/workspace"}]
    resp = client.get("/plugins/fake/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["version"] == "1.2.3"
    assert body["projects"] == [{"id": "p1", "worktree": "/workspace"}]


def test_status_offline_is_not_an_error():
    """Connection refused surfaces as connected=false, not a 502."""
    def refused(request):
        raise httpx.ConnectError("refused")

    p = opencode_plugin.Plugin()
    p.on_load(FakePluginContext(config={"base_url": "http://testserver"}))
    p._new_client = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(refused), base_url="http://testserver"
    )
    app = FastAPI()
    for name, router in p._ctx.registry.routers:
        app.include_router(router, prefix=f"/plugins/fake")
    c = TestClient(app)
    resp = c.get("/plugins/fake/status")
    assert resp.status_code == 200
    assert resp.json() == {"connected": False, "version": None, "projects": []}


def test_list_sessions(client, fake_server):
    fake_server["routes"][("GET", "/session")] = [{"id": "ses_1", "title": "build"}]
    resp = client.get("/plugins/fake/sessions")
    assert resp.status_code == 200
    assert resp.json() == [{"id": "ses_1", "title": "build"}]


def test_create_session_passes_title(client, fake_server):
    fake_server["routes"][("POST", "/session")] = {"id": "ses_2", "title": "t"}
    resp = client.post("/plugins/fake/sessions", json={"title": "t"})
    assert resp.status_code == 200
    assert resp.json()["id"] == "ses_2"


def test_delete_session(client, fake_server):
    fake_server["routes"][("DELETE", "/session/ses_9")] = True
    resp = client.delete("/plugins/fake/sessions/ses_9")
    assert resp.status_code == 200


def test_messages_passthrough(client, fake_server):
    fake_server["routes"][("GET", "/session/ses_1/message")] = [
        {"info": {"id": "msg_1", "role": "user"}, "parts": [{"type": "text", "text": "hi"}]}
    ]
    resp = client.get("/plugins/fake/sessions/ses_1/messages")
    assert resp.status_code == 200
    assert resp.json()[0]["info"]["role"] == "user"


def test_prompt_sends_async_with_parts(client, fake_server):
    fake_server["routes"][("POST", "/session/ses_1/prompt_async")] = 204
    resp = client.post("/plugins/fake/sessions/ses_1/prompt", json={"text": "build it"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_prompt_rejects_empty_text(client):
    resp = client.post("/plugins/fake/sessions/ses_1/prompt", json={"text": "   "})
    assert resp.status_code == 400


def test_prompt_model_parsing(client, fake_server):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(204)

    plugin = opencode_plugin.Plugin()
    plugin.on_load(FakePluginContext(config={"base_url": "http://testserver"}))
    plugin._new_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://testserver")
    app = FastAPI()
    for name, router in plugin._ctx.registry.routers:
        app.include_router(router, prefix=f"/plugins/fake")
    c = TestClient(app)
    resp = c.post("/plugins/fake/sessions/ses_1/prompt",
                  json={"text": "go", "agent": "build", "model": "anthropic/claude-sonnet"})
    assert resp.status_code == 200
    assert seen["path"] == "/session/ses_1/prompt_async"
    assert seen["body"]["agent"] == "build"
    assert seen["body"]["model"] == {"providerID": "anthropic", "modelID": "claude-sonnet"}
    assert seen["body"]["parts"] == [{"type": "text", "text": "go"}]


def test_invalid_session_id_rejected(client, fake_server):
    # Control: a valid id routes through.
    fake_server["routes"][("GET", "/session/ses_1/message")] = []
    assert client.get("/plugins/fake/sessions/ses_1/messages").status_code == 200
    # Traversal / garbage ids must never reach the opencode server.
    for bad in ("..%2Fstatus", "ses%2F1", "x" * 200, "bad;id"):
        resp = client.post(f"/plugins/fake/sessions/{bad}/prompt", json={"text": "x"})
        assert resp.status_code in (400, 404), bad
    upstream = [p for _, p in fake_server["requests"]]
    assert all("../" not in p and ";" not in p for p in upstream)


def test_statuses_passthrough(client, fake_server):
    fake_server["routes"][("GET", "/session/status")] = {"ses_1": {"event": "message.updated"}}
    resp = client.get("/plugins/fake/statuses")
    assert resp.status_code == 200
    assert resp.json() == {"ses_1": {"event": "message.updated"}}


def test_abort(client, fake_server):
    fake_server["routes"][("POST", "/session/ses_1/abort")] = True
    resp = client.post("/plugins/fake/sessions/ses_1/abort")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_upstream_error_surfaced(client, fake_server):
    fake_server["routes"][("GET", "/session")] = (500, "boom")
    resp = client.get("/plugins/fake/sessions")
    assert resp.status_code == 500
    assert "boom" in resp.json()["detail"]
