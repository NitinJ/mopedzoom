from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ApprovalButton:
    callback: str
    label: str


@dataclass
class InboundMessage:
    channel: str
    user_ref: str  # opaque channel-specific id (chat:xxx, topic:yyy, socket:zzz)
    text: str
    reply_to_ref: str | None  # ref of the message being replied to
    raw: dict[str, Any] = field(default_factory=dict)
    task_id: int | None = None  # populated by channel if it can derive it


@dataclass
class OutboundMessage:
    body: str
    buttons: list[ApprovalButton] = field(default_factory=list)
    task_id: int | None = None
    channel_ref: str | None = None  # the exact thread/topic/socket to post into


class Channel(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def post(self, msg: OutboundMessage) -> str:
        """Post and return a channel_ref to correlate replies/callbacks."""

    @abstractmethod
    def set_handler(self, handler) -> None:
        """handler(inbound: InboundMessage) -> None coroutine."""
