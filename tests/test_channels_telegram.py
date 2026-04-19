from unittest.mock import AsyncMock

from mopedzoomd.channels.base import ApprovalButton, OutboundMessage
from mopedzoomd.channels.telegram import TelegramChannel, _format_header


def test_header_format():
    h = _format_header(task_id=47, playbook_id="bug-fix", repo="trialroomai", mode="header")
    assert h == "[#47 \u00b7 bug-fix \u00b7 trialroomai] "


def test_header_empty_in_topics_mode():
    assert _format_header(47, "bug-fix", "x", mode="topics") == ""


async def test_post_message_in_topic(monkeypatch):
    bot = AsyncMock()
    bot.send_message = AsyncMock(
        return_value=type("M", (), {"message_id": 100, "chat_id": -1, "message_thread_id": 7})()
    )
    ch = TelegramChannel(bot_token="x", chat_id=-1, mode="topics", _bot=bot)
    ch.bind_task_topic(task_id=47, thread_id=7, playbook_id="bug-fix", repo="x")
    ref = await ch.post(
        OutboundMessage(
            task_id=47,
            body="hello",
            buttons=[ApprovalButton("approve", "Approve")],
        )
    )
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["message_thread_id"] == 7
    assert "hello" in kwargs["text"]
    assert ref == "tg:-1:7:100"
