"""Telegram channel adapter integration tests.

Uses FakeBot injected via TelegramChannel(_bot=...) to intercept all
Telegram API calls. No network calls are made.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mopedzoomd.channels.base import OutboundMessage
from mopedzoomd.channels.telegram import TelegramChannel
from mopedzoomd.daemon import TaskManager, handle_inbound, resolve_interaction
from mopedzoomd.models import Interaction, InteractionKind, Task, TaskStatus
from mopedzoomd.playbooks import load_playbooks
from mopedzoomd.router import Router
from mopedzoomd.stage_runner import StageRunner
from mopedzoomd.state import StateDB


class FakeBot:
    """Records all Telegram API calls; no network I/O."""

    def __init__(self):
        self.sent_messages: list[dict] = []
        self.created_topics: list[dict] = []
        self._msg_event: asyncio.Event = asyncio.Event()

    async def send_message(
        self, *, chat_id, text, reply_markup=None, message_thread_id=None, **kwargs
    ):
        self.sent_messages.append(
            {"chat_id": chat_id, "text": text, "thread_id": message_thread_id}
        )
        self._msg_event.set()
        m = MagicMock()
        m.chat_id = chat_id
        m.message_thread_id = message_thread_id or 0
        m.message_id = len(self.sent_messages)
        return m

    async def create_forum_topic(self, *, chat_id, name, **kwargs):
        self.created_topics.append({"chat_id": chat_id, "name": name})
        ft = MagicMock()
        ft.message_thread_id = 42
        return ft

    async def close_forum_topic(self, *, chat_id, message_thread_id, **kwargs):
        pass

    async def answer_callback_query(self, callback_query_id, **kwargs):
        pass

    async def pin_message(self, *, chat_id, message_id, **kwargs):
        pass


def _make_message_update(text: str, chat_id: int = -100123, thread_id: int | None = None):
    """Build a fake telegram.Update carrying a text message."""
    from telegram import Update

    update = MagicMock(spec=Update)
    update.message = MagicMock()
    update.message.text = text
    update.message.chat_id = chat_id
    update.message.message_thread_id = thread_id
    update.message.reply_to_message = None
    update.callback_query = None
    return update


def _make_callback_update(task_id: int, action: str, chat_id: int = -100123):
    """Build a fake telegram.Update carrying an inline-button callback."""
    from telegram import Update

    update = MagicMock(spec=Update)
    update.message = None
    cq = MagicMock()
    cq.id = "cq_id_1"
    cq.data = f"{task_id}:{action}"
    cq.answer = AsyncMock()
    cq.message = MagicMock()
    cq.message.chat_id = chat_id
    update.callback_query = cq
    return update


@pytest.fixture
def bot_and_channel():
    """TelegramChannel with a FakeBot injected; no real Application built."""
    bot = FakeBot()
    channel = TelegramChannel(
        bot_token="fake-token",
        chat_id=-100123,
        mode="topics",
        _bot=bot,
        _app=MagicMock(),
    )
    return bot, channel
