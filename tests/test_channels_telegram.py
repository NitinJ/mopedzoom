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


async def test_post_document_calls_send_document(monkeypatch, tmp_path):
    from pathlib import Path
    from mopedzoomd.channels.base import OutboundMessage

    doc = tmp_path / "brief.md"
    doc.write_text("# Pre-brief\n\nContent here.")

    bot = AsyncMock()
    bot.send_document = AsyncMock(
        return_value=type(
            "M", (), {"message_id": 200, "chat_id": -100, "message_thread_id": None}
        )()
    )
    ch = TelegramChannel(bot_token="x", chat_id=-100, mode="header", _bot=bot)
    ref = await ch.post(
        OutboundMessage(task_id=1, body="review this please", document_path=doc)
    )
    bot.send_document.assert_awaited_once()
    kwargs = bot.send_document.await_args.kwargs
    assert kwargs["document"] == doc
    assert "review this please" in kwargs["caption"]
    assert ref == "tg:-100:0:200"
    bot.send_message.assert_not_called()


async def test_post_without_document_still_uses_send_message():
    from mopedzoomd.channels.base import OutboundMessage

    bot = AsyncMock()
    bot.send_message = AsyncMock(
        return_value=type(
            "M", (), {"message_id": 5, "chat_id": -1, "message_thread_id": None}
        )()
    )
    ch = TelegramChannel(bot_token="x", chat_id=-1, mode="header", _bot=bot)
    await ch.post(OutboundMessage(task_id=1, body="hello"))
    bot.send_message.assert_awaited_once()
    bot.send_document.assert_not_called()
