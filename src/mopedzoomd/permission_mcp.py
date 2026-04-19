"""Permission MCP file-based bridge.

Core logic used by the MCP ``mopedzoom_permission`` tool: write a
``permission.json`` into the scratch dir, wait for a ``permission_response.json``
from a human (or channel bot), then return a Claude Code permission-response
shape. The MCP server wrapping is added later in Task F21.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
from pathlib import Path
from typing import Any


def _allowlist_match(patterns: list[str], tool_name: str, input_json: dict[str, Any]) -> bool:
    candidate = input_json.get("command", "") or input_json.get("path", "") or ""
    probe = f"{tool_name} {candidate}".strip()
    return any(fnmatch.fnmatch(probe, p) or fnmatch.fnmatch(candidate, p) for p in patterns)


async def handle_permission_request(
    *,
    scratch_dir: Path,
    tool_name: str,
    input_json: dict[str, Any],
    allowlist: list[str],
    timeout_s: float = 300.0,
) -> dict[str, Any]:
    """File-based contract for the MCP tool -> Telegram/CLI bridge.

    Returns the Claude Code permission-response shape:
      {"behavior": "allow", "updatedInput": <dict>}
      {"behavior": "deny",  "message": <str>}
    """
    if _allowlist_match(allowlist, tool_name, input_json):
        return {"behavior": "allow", "updatedInput": input_json}

    scratch_dir = Path(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    req_path = scratch_dir / "permission.json"
    resp_path = scratch_dir / "permission_response.json"
    req_path.write_text(json.dumps({"tool_name": tool_name, "input": input_json}))
    try:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            if resp_path.exists():
                resp = json.loads(resp_path.read_text())
                decision = resp.get("decision")
                if decision in ("allow", "allow-and-remember"):
                    return {"behavior": "allow", "updatedInput": input_json}
                return {
                    "behavior": "deny",
                    "message": resp.get("message", "denied by user"),
                }
            await asyncio.sleep(0.1)
        return {"behavior": "deny", "message": "user did not respond in time"}
    finally:
        for p in (req_path, resp_path):
            if p.exists():
                p.unlink()
