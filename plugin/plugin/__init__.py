"""opencode plugin — drive an opencode server (AI coding agent) from AnotherMe.

Proxies a fixed, validated set of operations to a sidecar `opencode serve`
instance so the agent is reachable from the AnotherMe UI (phone-friendly,
behind the app's own auth). Only these exact operations are forwarded —
this is deliberately NOT a generic passthrough proxy.

Contract notes (Protocol v1): imports limited to stdlib, fastapi, pydantic
and httpx. Never imports src.* or kernel.*.
"""
import logging
import re

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://opencode:4096"
DEFAULT_TIMEOUT = 60.0

# opencode session ids: opaque tokens like "ses_..." — never trust them in
# URLs without validation (path-injection guard).
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class CreateSessionBody(BaseModel):
    title: str | None = None


class PromptBody(BaseModel):
    text: str
    agent: str | None = None
    model: str | None = None  # "provider/model" or "provider:model"


def _validate_session_id(session_id: str) -> str:
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="Invalid session id")
    return session_id


def _parse_model(model: str | None) -> dict | None:
    if not model:
        return None
    for sep in (":", "/"):
        if sep in model:
            provider, _, model_id = model.partition(sep)
            return {"providerID": provider.strip(), "modelID": model_id.strip()}
    return None


class Plugin:
    def __init__(self):
        self._ctx = None
        self._base_url = DEFAULT_BASE_URL
        self._auth = None
        self._timeout = DEFAULT_TIMEOUT

    def on_load(self, ctx):
        self._ctx = ctx
        cfg = {k.lower(): v for k, v in ctx.get_config().items()}
        self._base_url = str(cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        password = str(cfg.get("password") or "").strip()
        self._auth = ("opencode", password) if password else None

        router = APIRouter()
        router.add_api_route("/status", self.get_status, methods=["GET"])
        router.add_api_route("/statuses", self.get_statuses, methods=["GET"])
        router.add_api_route("/sessions", self.list_sessions, methods=["GET"])
        router.add_api_route("/sessions", self.create_session, methods=["POST"])
        router.add_api_route("/sessions/{session_id}", self.get_session, methods=["GET"])
        router.add_api_route("/sessions/{session_id}", self.delete_session, methods=["DELETE"])
        router.add_api_route("/sessions/{session_id}/messages", self.get_messages, methods=["GET"])
        router.add_api_route("/sessions/{session_id}/prompt", self.send_prompt, methods=["POST"])
        router.add_api_route("/sessions/{session_id}/abort", self.abort, methods=["POST"])
        ctx.register_router(router)
        logger.info("opencode plugin loaded (server: %s)", self._base_url)

    # --- transport -----------------------------------------------------

    def _new_client(self) -> httpx.AsyncClient:
        """Separate method so tests can inject an httpx.MockTransport."""
        return httpx.AsyncClient(
            base_url=self._base_url, auth=self._auth, timeout=self._timeout
        )

    async def _proxy(self, method: str, path: str, *, json: dict | None = None):
        try:
            async with self._new_client() as client:
                resp = await client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"opencode server unreachable: {exc}")
        if resp.status_code >= 400:
            detail = resp.text[:500] if resp.text else resp.reason_phrase
            raise HTTPException(status_code=resp.status_code, detail=detail)
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            raise HTTPException(status_code=502, detail="opencode server returned non-JSON response")

    # --- handlers ------------------------------------------------------

    async def get_status(self):
        """Health + project list. Returns connected=false (not an error)
        when the sidecar is down so the UI can render a friendly state."""
        try:
            health = await self._proxy("GET", "/global/health")
        except HTTPException:
            return {"connected": False, "version": None, "projects": []}
        projects = []
        try:
            projects = await self._proxy("GET", "/project")
        except HTTPException:
            pass
        return {
            "connected": bool(health.get("healthy")),
            "version": health.get("version"),
            "projects": projects,
        }

    async def get_statuses(self):
        """Session id -> running status, for the UI poll loop."""
        return await self._proxy("GET", "/session/status")

    async def list_sessions(self):
        return await self._proxy("GET", "/session")

    async def create_session(self, body: CreateSessionBody):
        payload = {}
        if body.title:
            payload["title"] = body.title
        return await self._proxy("POST", "/session", json=payload)

    async def get_session(self, session_id: str):
        sid = _validate_session_id(session_id)
        return await self._proxy("GET", f"/session/{sid}")

    async def delete_session(self, session_id: str):
        sid = _validate_session_id(session_id)
        return await self._proxy("DELETE", f"/session/{sid}")

    async def get_messages(self, session_id: str):
        sid = _validate_session_id(session_id)
        return await self._proxy("GET", f"/session/{sid}/message")

    async def send_prompt(self, session_id: str, body: PromptBody):
        """Fire a prompt asynchronously; the UI polls messages + status.

        prompt_async returns 204 quickly, which suits flaky mobile
        connections better than waiting on the full agent run.
        """
        sid = _validate_session_id(session_id)
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="Prompt text is empty")
        payload: dict = {"parts": [{"type": "text", "text": text}]}
        if body.agent:
            payload["agent"] = body.agent
        model = _parse_model(body.model)
        if model:
            payload["model"] = model
        await self._proxy("POST", f"/session/{sid}/prompt_async", json=payload)
        return {"ok": True}

    async def abort(self, session_id: str):
        sid = _validate_session_id(session_id)
        result = await self._proxy("POST", f"/session/{sid}/abort")
        return {"ok": bool(result)}
