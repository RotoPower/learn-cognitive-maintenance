"""FastAPI front of the assistant (module E3.2/E3.3).

    POST /chat               {"message": "...", "session_id": "..."} -> NDJSON stream of events:
                             {"type":"text","text":...} | {"type":"tool","name":...} |
                             {"type":"draft","draft_id":...,...} | {"type":"done",...} | {"type":"error",...}
    POST /workorders/confirm {"session_id": "...", "draft_id": "..."} -> the created work order
    GET  /health

The dashboard Worker is the only client: every request needs the X-Demo-Secret header when
ASSISTANT_SECRET is set (always, on the VM). Work orders are created only by /workorders/confirm,
the operator's Confirm button, never by the model. Per-IP limits guard the Max-plan quota.

    uv run plant-assistant            # 127.0.0.1:8100, PLANT_API_URL / PLANT_READ_TOKEN from .env
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from assistant import tools as T
from assistant.agent import ChatSession, Sessions, find_claude_cli, options

SESSION_RE = r"^[A-Za-z0-9_-]{8,64}$"
log = logging.getLogger("plant-assistant")


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(pattern=SESSION_RE)


class ConfirmBody(BaseModel):
    session_id: str = Field(pattern=SESSION_RE)
    draft_id: str = Field(min_length=4, max_length=64)


class RateLimit:
    """Sliding window per key: at most `n` events per `window_s`."""

    def __init__(self, n: int, window_s: float):
        self.n, self.window = n, window_s
        self._hits: dict[str, deque] = defaultdict(deque)

    def hit(self, key: str) -> bool:
        now, q = time.monotonic(), self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.n:
            return False
        q.append(now)
        return True


def create_app(sessions: Sessions, api: T.PlantApi, store: T.DraftStore, secret: str | None,
               messages_per_hour: int = 20, confirms_per_hour: int = 10) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await sessions.close_all()

    app = FastAPI(title="plant assistant", lifespan=lifespan)
    chat_limit, confirm_limit = RateLimit(messages_per_hour, 3600), RateLimit(confirms_per_hour, 3600)

    def guard(request: Request) -> str:
        if secret and not secrets.compare_digest(request.headers.get("x-demo-secret", ""), secret):
            raise HTTPException(401, "missing or wrong X-Demo-Secret")
        # the dashboard Worker forwards the viewer's IP; direct callers are rejected above
        return request.headers.get("x-viewer-ip") or (request.client.host if request.client else "unknown")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"service": "plant-assistant", "auth": "X-Demo-Secret" if secret else "none (local dev)"}

    @app.post("/chat")
    async def chat(body: ChatBody, viewer: str = Depends(guard)) -> StreamingResponse:
        if not chat_limit.hit(viewer):
            raise HTTPException(429, f"limit reached: {messages_per_hour} messages per hour")
        try:
            session = await sessions.get(body.session_id)
        except RuntimeError as e:
            raise HTTPException(503, str(e)) from None
        if session.lock.locked():
            raise HTTPException(409, "this conversation is still answering the previous message")

        async def stream() -> AsyncIterator[bytes]:
            async with session.lock:
                try:
                    async for ev in session.turn(body.message):
                        yield (json.dumps(ev) + "\n").encode()
                except Exception as e:  # the stream has started: report in-band, never leak internals
                    log.exception("chat turn failed (session %s)", body.session_id)
                    msg = "the assistant is unavailable right now"
                    if re.search(r"rate.?limit|usage limit|quota", str(e), re.I):
                        msg = "the assistant has hit its usage limit; try again later"
                    yield (json.dumps({"type": "error", "message": msg}) + "\n").encode()
                    await session.close()

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.post("/workorders/confirm")
    def confirm(body: ConfirmBody, viewer: str = Depends(guard)) -> dict[str, Any]:
        if not confirm_limit.hit(viewer):
            raise HTTPException(429, f"limit reached: {confirms_per_hour} work orders per hour")
        try:
            return T.confirm_workorder(store, api, body.session_id, body.draft_id)
        except T.ToolError as e:
            raise HTTPException(404, str(e)) from None

    return app


def build_from_env() -> FastAPI:
    from plant.cli import Client, _load_dotenv  # the repo's plant API client (User-Agent, errors)

    _load_dotenv()
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if not token:
        raise SystemExit("CLAUDE_CODE_OAUTH_TOKEN is not set: run `claude setup-token` and put it in .env")
    api = Client(os.environ.get("PLANT_API_URL", "http://127.0.0.1:8000"), os.environ.get("PLANT_READ_TOKEN"))
    store = T.DraftStore()
    model = os.environ.get("ASSISTANT_MODEL", "claude-opus-5")
    effort = os.environ.get("ASSISTANT_EFFORT") or None
    # An API key in the environment would take precedence over the subscription token.
    env = {"CLAUDE_CODE_OAUTH_TOKEN": token, "ANTHROPIC_API_KEY": ""}
    cli = find_claude_cli()
    sessions = Sessions(lambda sid: ChatSession(sid, lambda s: options(api, store, s, model, effort, env, cli), store))
    return create_app(sessions, api, store, os.environ.get("ASSISTANT_SECRET") or None,
                      int(os.environ.get("ASSISTANT_MESSAGES_PER_HOUR", 20)), int(os.environ.get("ASSISTANT_CONFIRMS_PER_HOUR", 10)))


def main() -> None:
    import uvicorn

    uvicorn.run(build_from_env(), host=os.environ.get("ASSISTANT_HOST", "127.0.0.1"), port=int(os.environ.get("ASSISTANT_PORT", 8100)))


if __name__ == "__main__":
    main()
