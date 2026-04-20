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


@pytest.mark.asyncio
async def test_telegram_inbound_text_submits_task(bot_and_channel, tmp_path):
    """Inbound text matching a playbook trigger → task inserted in DB + ack sent."""
    bot, channel = bot_and_channel

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    root = Path(__file__).resolve().parents[2]
    registry = load_playbooks(builtin_dir=root / "playbooks", user_dir=None)
    router = Router(registry=registry, claude_client=None)
    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=StageRunner(),
        playbook_registry=registry,
        channels={"telegram": channel},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )

    async def handler(msg):
        await handle_inbound(
            msg,
            db=db,
            router=router,
            tm=tm,
            channels={"telegram": channel},
            registry=registry,
        )

    channel.set_handler(handler)

    update = _make_message_update("research AI trends")
    await channel._on_message(update, None)

    await asyncio.wait_for(bot._msg_event.wait(), timeout=2.0)

    tasks = await db.list_tasks(limit=10)
    assert len(tasks) >= 1
    assert tasks[0].status == TaskStatus.QUEUED
    assert tasks[0].playbook_id == "research"
    assert len(bot.sent_messages) >= 1
    # The ack message should contain the task ID.
    ack_text = bot.sent_messages[0]["text"]
    assert str(tasks[0].id) in ack_text

    await db.close()


@pytest.mark.asyncio
async def test_telegram_approval_button_resolves_interaction(bot_and_channel, tmp_path):
    """Approval button callback → resolve_interaction sets task status correctly."""
    bot, channel = bot_and_channel

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    tid = await db.insert_task(
        Task(channel="telegram", user_ref="chat:-100123", playbook_id="research", inputs={})
    )
    await db.set_task_status(tid, TaskStatus.AWAITING_APPROVAL)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.APPROVAL,
            prompt="Approve stage?",
            posted_to_channel_ref="tg:-100123:42:1",
        )
    )

    async def handler(msg):
        await resolve_interaction(db, task_id=msg.task_id, answer=msg.text)

    channel.set_handler(handler)

    update = _make_callback_update(tid, "approve")
    await channel._on_callback(update, None)

    task = await db.get_task(tid)
    assert task.status == TaskStatus.RUNNING

    events = await db.list_events(tid)
    assert any(e.kind == "resolved_approve" for e in events)

    await db.close()


@pytest.mark.asyncio
async def test_telegram_post_sends_to_bound_topic(bot_and_channel):
    """create_topic + bind_task_topic + post sends message to the right thread."""
    bot, channel = bot_and_channel

    thread_id = await channel.create_topic(title="Task #1 — research")
    assert thread_id == 42
    assert len(bot.created_topics) == 1
    assert bot.created_topics[0]["name"] == "Task #1 — research"

    channel.bind_task_topic(
        task_id=1, thread_id=thread_id, playbook_id="research", repo="ml"
    )

    ref = await channel.post(OutboundMessage(task_id=1, body="Stage done"))

    assert len(bot.sent_messages) == 1
    assert bot.sent_messages[0]["text"] == "Stage done"
    assert bot.sent_messages[0]["thread_id"] == 42
    assert ref.startswith("tg:")
    assert ":42:" in ref
