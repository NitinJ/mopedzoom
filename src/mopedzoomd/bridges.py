"""Scratch-dir bridge watcher for question/approval/permission files."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from .scratch import ScratchDir

FILES = {
    "question": "question.json",
    "approval": "approval.json",
    "permission": "permission.json",
}


@dataclass
class BridgeEvent:
    kind: str  # "question" | "approval" | "permission"
    payload: dict[str, Any]


async def watch_scratch(
    scratch: ScratchDir, interval_s: float = 0.25
) -> AsyncIterator[BridgeEvent]:
    """Poll scratch dir for bridge files; yield a BridgeEvent once per kind seen."""
    seen: set[str] = set()
    while True:
        for kind, fname in FILES.items():
            if kind in seen:
                continue
            p = scratch.dir / fname
            if p.exists():
                try:
                    payload = json.loads(p.read_text())
                except json.JSONDecodeError:
                    continue
                seen.add(kind)
                yield BridgeEvent(kind=kind, payload=payload)
        await asyncio.sleep(interval_s)
