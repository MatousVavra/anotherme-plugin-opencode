"""opencode plugin integration smoke — runs inside the released app image.

No sidecar is present in the image, so the proxy endpoints must answer 502
while the status endpoint degrades gracefully to connected=false.
"""


def test_plugin_loaded_and_status_reports_disconnected(make_client):
    client = make_client()
    resp = client.get("/plugins/opencode/status")
    assert resp.status_code == 200
    assert resp.json() == {"connected": False, "version": None, "projects": []}


def test_sessions_without_sidecar_returns_502(make_client):
    client = make_client()
    resp = client.get("/plugins/opencode/sessions")
    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"]


def test_opencode_ui_fragment_served(make_client):
    client = make_client()
    resp = client.get("/plugins/opencode/ui/index.html")
    assert resp.status_code == 200
    assert b"opencodeTab" in resp.content
    resp = client.get("/plugins/opencode/ui/index.js")
    assert resp.status_code == 200
