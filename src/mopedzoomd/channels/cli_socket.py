from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from .base import Channel, InboundMessage, OutboundMessage


class CLISocketChannel(Channel):
    def __init__(self, path: str):
        self.path = path
        self._server: asyncio.AbstractServer | None = None
        self._handler = None

    def set_handler(self, handler) -> None:
        self._handler = handler

    async def start(self) -> None:
        if os.path.exists(self.path):
            os.unlink(self.path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(self._serve, path=self.path)
        os.chmod(self.path, 0o600)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if os.path.exists(self.path):
            os.unlink(self.path)

    async def post(self, msg: OutboundMessage) -> str:
        return ""  # CLI output goes directly to the live client via live-TUI in v1

    async def _serve(self, reader, writer):
        addr = id(writer)
        try:
            data = await reader.readline()
            if not data:
                return
            cmd = json.loads(data.decode())
            op = cmd.get("op")
            reply: dict = {"ack": True, "op": op}
            if op == "submit":
                inbound = InboundMessage(
                    channel="cli",
                    user_ref=f"socket:{addr}",
                    text=cmd.get("text", ""),
                    reply_to_ref=None,
                    raw=cmd,
                )
                if self._handler:
                    await self._handler(inbound)
            elif op in {
                "status",
                "tasks",
                "cancel",
                "resume",
                "edit",
                "logs",
                "ui",
                "show-playbook",
            }:
                # v1: minimal ack; full dispatcher is wired by the daemon.
                reply["ok"] = True
                for k in ("id", "stage"):
                    if k in cmd:
                        reply[k] = cmd[k]
            else:
                reply = {"ack": False, "error": f"unknown op: {op}"}
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
