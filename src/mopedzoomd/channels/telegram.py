from __future__ import annotations

from dataclasses import dataclass

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

from .base import Channel, InboundMessage, OutboundMessage


def _format_header(task_id: int, playbook_id: str, repo: str, mode: str) -> str:
    if mode == "topics":
        return ""
    return f"[#{task_id} \u00b7 {playbook_id} \u00b7 {repo}] "


@dataclass
class _TopicBinding:
    thread_id: int
    playbook_id: str
    repo: str


class TelegramChannel(Channel):
    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: int,
        mode: str,
        _bot: Bot | None = None,
        _app: Application | None = None,
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.mode = mode  # "topics" | "header" | "auto"
        self._bot = _bot or Bot(bot_token)
        self._app = _app
        self._handler = None
        self._topics: dict[int, _TopicBinding] = {}

    def set_handler(self, handler) -> None:
        self._handler = handler

    def bind_task_topic(
        self, *, task_id: int, thread_id: int, playbook_id: str, repo: str
    ) -> None:
        self._topics[task_id] = _TopicBinding(thread_id, playbook_id, repo)

    async def start(self) -> None:
        if self._app is None:
            self._app = Application.builder().bot(self._bot).build()
            self._app.add_handler(MessageHandler(filters.ALL, self._on_message))
            self._app.add_handler(CallbackQueryHandler(self._on_callback))
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling()

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def post(self, msg: OutboundMessage) -> str:
        tb = self._topics.get(msg.task_id)
        header = _format_header(
            msg.task_id or 0,
            tb.playbook_id if tb else "?",
            tb.repo if tb else "?",
            self.mode,
        )
        kb = None
        if msg.buttons:
            rows = [
                [
                    InlineKeyboardButton(
                        b.label, callback_data=f"{msg.task_id}:{b.callback}"
                    )
                    for b in msg.buttons
                ]
            ]
            kb = InlineKeyboardMarkup(rows)
        sent = await self._bot.send_message(
            chat_id=self.chat_id,
            text=header + msg.body,
            reply_markup=kb,
            message_thread_id=tb.thread_id if (tb and self.mode == "topics") else None,
        )
        return f"tg:{sent.chat_id}:{sent.message_thread_id or 0}:{sent.message_id}"

    async def _on_message(self, update: Update, context) -> None:
        if not self._handler or not update.message:
            return
        msg = update.message
        task_id = None
        # In topics mode, derive task from message_thread_id
        if self.mode == "topics" and msg.message_thread_id is not None:
            for tid, tb in self._topics.items():
                if tb.thread_id == msg.message_thread_id:
                    task_id = tid
                    break
        inbound = InboundMessage(
            channel="telegram",
            user_ref=f"chat:{msg.chat_id}",
            text=msg.text or "",
            reply_to_ref=(
                f"tg:{msg.chat_id}:{msg.message_thread_id or 0}:{msg.reply_to_message.message_id}"
                if msg.reply_to_message
                else None
            ),
            raw={},
            task_id=task_id,
        )
        await self._handler(inbound)

    async def _on_callback(self, update: Update, context) -> None:
        if not self._handler:
            return
        q = update.callback_query
        await q.answer()
        task_id_s, action = q.data.split(":", 1)
        inbound = InboundMessage(
            channel="telegram",
            user_ref=f"chat:{q.message.chat_id}",
            text=action,
            reply_to_ref=None,
            raw={"callback": True},
            task_id=int(task_id_s),
        )
        await self._handler(inbound)

    async def create_topic(self, *, title: str) -> int:
        """Creates a forum topic; returns its message_thread_id."""
        ft = await self._bot.create_forum_topic(chat_id=self.chat_id, name=title)
        return ft.message_thread_id

    async def close_topic(self, thread_id: int) -> None:
        await self._bot.close_forum_topic(
            chat_id=self.chat_id, message_thread_id=thread_id
        )
