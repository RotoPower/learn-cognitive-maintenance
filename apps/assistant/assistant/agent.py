"""Claude Agent SDK wiring: the four plant tools as an in-process MCP server, one
ClaudeSDKClient per chat session, streamed text out.

Locked down on purpose: no built-in tools (`tools=[]`: no files, shell or web), no
filesystem settings (`setting_sources=[]`: this repo's CLAUDE.md, hooks and agents do
not load), an empty working directory, and a permission callback that refuses any tool
that is not one of the four. Auth: CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`).
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import tempfile
import time
import warnings
from pathlib import Path
from collections.abc import AsyncIterator, Callable
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    CanUseToolShadowedWarning,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)

from assistant import tools as T

# The four plant tools are auto-approved on purpose (allowed_tools); can_use_tool is the
# backstop that refuses every other tool, so the SDK's "callback shadowed" notice is expected.
warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)

SERVER = "plant"
TOOL_NAMES = ("get_asset_status", "get_events", "get_recommendations", "create_workorder")
ALLOWED = [f"mcp__{SERVER}__{n}" for n in TOOL_NAMES]

SYSTEM_PROMPT = """You are the maintenance assistant for a small combined-cycle power plant (simulated for a demo). \
You talk with plant operators and managers.

Assets and the failure modes watched on each: {assets}. Times are plant (simulation) time. \
The risk model scores GT1, BFP1, BFP2 and CTF1 only so far; for the other assets rely on anomaly alerts and readings.

Rules:
- Never guess a number, time, or status. Every fact you state comes from a tool result in this conversation. \
If a tool did not give it, say you don't have it.
- Name the failure mode and cite the evidence for any judgement: tag, value with unit, and time, compared with \
its baseline when the tool gives one (for example "BFP2.VIB_DE 3.1 mm/s at 2024-09-20 13:00, baseline 1.8").
- Recommendations come from get_recommendations (the operator playbook). Say they come from the playbook; do not \
add procedures of your own.
- Anomaly alerts give a peak z-score; the risk model gives a probability of failure within its horizon. Keep the two apart.
- Work orders: draft one only when the operator asks for a work order; otherwise offer to. create_workorder only \
drafts: after drafting, tell the operator to press Confirm in the chat panel. Never say a work order was created; \
you cannot confirm it.
- Be brief: a short answer first, then the evidence as a few bullets. Reply in the operator's language.""".format(
    assets="; ".join(f"{a} ({', '.join(ms)})" for a, ms in T.ASSET_MODES.items()),
)


def build_server(api: T.PlantApi, store: T.DraftStore, session_id: str):
    """The four tools, bound to one session (drafts belong to the session that made them)."""

    def result(fn: Callable[[], Any]) -> dict[str, Any]:
        try:
            return {"content": [{"type": "text", "text": json.dumps(fn(), default=str)}]}
        except T.ToolError as e:
            return {"content": [{"type": "text", "text": f"error: {e}"}], "is_error": True}
        except Exception as e:  # plant API down, D1 limit, ...: tell the model, don't crash the turn
            return {"content": [{"type": "text", "text": f"error: plant data unavailable ({str(e)[:200]})"}], "is_error": True}

    asset = {"type": "string", "enum": list(T.ASSETS)}

    @tool("get_asset_status",
          "Current status of one asset: latest readings with units and baselines, plant load, open alerts, "
          "the latest failure-risk score with its drivers, last repair, open work orders.",
          {"type": "object", "properties": {"asset_id": asset}, "required": ["asset_id"]})
    async def get_asset_status(args: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(result, lambda: T.get_asset_status(api, args["asset_id"]))

    @tool("get_events",
          "What happened to one asset in a time window (default: the last 7 days of plant time): alerts, risk-score "
          "changes and alert days, repairs and work orders, and a 6-hourly trend (start, end, max and when) per tag.",
          {"type": "object", "properties": {
              "asset_id": asset,
              "from": {"type": "string", "description": "ISO time, e.g. 2024-09-13T00:00:00"},
              "to": {"type": "string", "description": "ISO time; defaults to now"},
          }, "required": ["asset_id"]})
    async def get_events(args: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(result, lambda: T.get_events(api, args["asset_id"], args.get("from"), args.get("to")))

    @tool("get_recommendations",
          "The operator playbook for a failure mode: symptoms, confirming checks, immediate actions, spare parts, "
          "typical lead time. Optional keyword query narrows it to matching lines.",
          {"type": "object", "properties": {
              "failure_mode": {"type": "string", "enum": list(T.MODES)},
              "severity": {"type": "string", "enum": list(T.SEVERITIES)},
              "query": {"type": "string"},
          }, "required": ["failure_mode"]})
    async def get_recommendations(args: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(result, lambda: T.get_recommendations(api, args["failure_mode"], args.get("severity", "warning"), args.get("query")))

    @tool("create_workorder",
          "Draft a work order for the operator to confirm. It is NOT created until the operator presses Confirm.",
          {"type": "object", "properties": {
              "asset_id": asset,
              "type": {"type": "string", "enum": list(T.WORKORDER_TYPES)},
              "description": {"type": "string", "description": "What the crew should do and why (tag, value, time)."},
              "scheduled_for": {"type": "string", "description": "optional ISO time"},
          }, "required": ["asset_id", "type", "description"]})
    async def create_workorder(args: dict[str, Any]) -> dict[str, Any]:
        return result(lambda: T.create_workorder(store, session_id, args["asset_id"], args["type"], args["description"], args.get("scheduled_for")))

    return create_sdk_mcp_server(name=SERVER, version="0.1.0",
                                 tools=[get_asset_status, get_events, get_recommendations, create_workorder])


async def only_plant_tools(tool_name: str, _input: dict[str, Any], _ctx) -> PermissionResultAllow | PermissionResultDeny:
    if tool_name in ALLOWED:
        return PermissionResultAllow(behavior="allow")
    return PermissionResultDeny(behavior="deny", message="Only the four plant tools are available.")


def find_claude_cli() -> str | None:
    """The Claude Code executable for the SDK. None = let the SDK search (Linux/macOS, or a
    native Windows install). On Windows an npm install puts a claude.cmd shim on PATH, which
    the SDK refuses to spawn; the same npm package ships the native claude.exe next to it."""
    if os.environ.get("ASSISTANT_CLAUDE_CLI"):
        return os.environ["ASSISTANT_CLAUDE_CLI"]
    if platform.system() != "Windows":
        return None
    shim = shutil.which("claude")
    if shim:
        exe = Path(shim).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if exe.is_file():
            return str(exe)
    return None


def options(api: T.PlantApi, store: T.DraftStore, session_id: str, model: str, effort: str | None, env: dict[str, str],
            cli_path: str | None = None) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cli_path=cli_path,
        system_prompt=SYSTEM_PROMPT,
        tools=[],                      # no built-in tools at all
        mcp_servers={SERVER: build_server(api, store, session_id)},
        allowed_tools=ALLOWED,
        can_use_tool=only_plant_tools,
        setting_sources=[],            # never load this repo's CLAUDE.md / hooks / agents
        cwd=tempfile.mkdtemp(prefix="plant-assistant-"),
        model=model,
        effort=effort,
        max_turns=12,
        include_partial_messages=True,
        env=env,
    )


class ChatSession:
    """One operator conversation: a connected ClaudeSDKClient, one turn at a time."""

    def __init__(self, session_id: str, make_options: Callable[[str], ClaudeAgentOptions], store: T.DraftStore):
        self.session_id = session_id
        self.store = store
        self._make_options = make_options
        self._client: ClaudeSDKClient | None = None
        self.lock = asyncio.Lock()
        self.last_used = time.monotonic()

    async def turn(self, message: str) -> AsyncIterator[dict[str, Any]]:
        """Yield NDJSON-able events: text deltas, tool calls, drafts, then done."""
        self.last_used = started = time.monotonic()
        if self._client is None:
            self._client = ClaudeSDKClient(self._make_options(self.session_id))
            await self._client.connect()
        await self._client.query(message)
        streamed = False
        gap = False  # text that resumes after a tool call starts a new paragraph
        async for msg in self._client.receive_response():
            if isinstance(msg, StreamEvent):
                ev = msg.event or {}
                delta = ev.get("delta") or {}
                if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    streamed = True
                    text = delta.get("text", "")
                    if gap and text.strip():
                        text, gap = "\n\n" + text.lstrip(), False
                    yield {"type": "text", "text": text}
            elif isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        gap = True
                        yield {"type": "tool", "name": block.name.removeprefix(f"mcp__{SERVER}__")}
                    elif isinstance(block, TextBlock) and not streamed:
                        text = "\n\n" + block.text.lstrip() if gap else block.text
                        gap = False
                        yield {"type": "text", "text": text}
                streamed = False
            elif isinstance(msg, ResultMessage):
                for d in self.store.created_since(self.session_id, started):
                    yield {"type": "draft", "draft_id": d.draft_id, "asset_id": d.asset_id, "work_type": d.type,
                           "description": d.description, "scheduled_for": d.scheduled_for}
                yield {"type": "done", "is_error": msg.is_error, "turns": msg.num_turns}

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


class Sessions:
    """At most `max_sessions` live conversations; idle ones are closed after `idle_s`."""

    def __init__(self, factory: Callable[[str], ChatSession], max_sessions: int = 20, idle_s: float = 1800):
        self.factory, self.max, self.idle = factory, max_sessions, idle_s
        self._s: dict[str, ChatSession] = {}

    async def get(self, session_id: str) -> ChatSession:
        now = time.monotonic()
        for sid, s in list(self._s.items()):
            if now - s.last_used > self.idle and not s.lock.locked():
                await self._drop(sid)
        if session_id not in self._s:
            if len(self._s) >= self.max:
                oldest = min((s for s in self._s.values() if not s.lock.locked()), key=lambda s: s.last_used, default=None)
                if oldest is None:
                    raise RuntimeError("assistant busy: too many conversations in progress")
                await self._drop(oldest.session_id)
            self._s[session_id] = self.factory(session_id)
        return self._s[session_id]

    async def _drop(self, sid: str) -> None:
        s = self._s.pop(sid, None)
        if s is not None:
            await s.close()

    async def close_all(self) -> None:
        for sid in list(self._s):
            await self._drop(sid)
