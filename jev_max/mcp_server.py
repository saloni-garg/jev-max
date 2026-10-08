"""jev-max MCP server: the fast Jev browser loop for any agent host.

Claude Code, Cursor, Windsurf, Codex, or anything speaking MCP gets the
same two-tier loop: Jev picks operations from a pierced element table,
a small LLM writes text only when needed, canvas fallback covers the rest.

Tools:
  jev_start(url, goal, max_steps) -> session_id
  jev_tick(session_id)            -> one decision cycle, JSON state
  jev_status(session_id)          -> current state without acting
  jev_close(session_id)           -> release the browser
"""

from __future__ import annotations

import json
import os
import uuid

from mcp.server.fastmcp import FastMCP

from .agent import Agent

mcp = FastMCP("jev-max")

_sessions: dict[str, Agent] = {}


def _state_json(agent: Agent) -> str:
    s = agent._snapshot_state()
    return json.dumps(s, default=str)


@mcp.tool()
def jev_start(url: str, goal: str, max_steps: int = 25) -> str:
    """Start a browser task. Returns a session_id for jev_tick/jev_status/jev_close."""
    sid = uuid.uuid4().hex[:12]
    headless = os.getenv("JEV_HEADLESS", "1") != "0"
    agent = Agent(
        url, goal,
        provider=os.getenv("JEV_PROVIDER") or None,
        headless=headless,
        max_steps=max_steps,
        record_dir=os.getenv("JEV_RECORD_DIR") or None,
        upload_file=os.getenv("JEV_UPLOAD_FILE") or None,
    )
    _sessions[sid] = agent
    out = {"session_id": sid, "state": json.loads(_state_json(agent))}
    return json.dumps(out, default=str)


@mcp.tool()
def jev_tick(session_id: str) -> str:
    """Run one observe-decide-act cycle. Call until status is done/blocked."""
    agent = _sessions.get(session_id)
    if agent is None:
        return json.dumps({"error": f"unknown session: {session_id}"})
    try:
        agent.tick()
    except Exception as e:  # never kill the host on a browser hiccup  # noqa: BLE001
        return json.dumps({"error": f"tick failed: {e}", "state": json.loads(_state_json(agent))})
    return _state_json(agent)


@mcp.tool()
def jev_status(session_id: str) -> str:
    """Current state without acting."""
    agent = _sessions.get(session_id)
    if agent is None:
        return json.dumps({"error": f"unknown session: {session_id}"})
    return _state_json(agent)


@mcp.tool()
def jev_close(session_id: str) -> str:
    """Close the session and release its browser."""
    agent = _sessions.pop(session_id, None)
    if agent is None:
        return json.dumps({"error": f"unknown session: {session_id}"})
    agent.close()
    return json.dumps({"closed": session_id})


def main():
    mcp.run()


if __name__ == "__main__":
    main()
