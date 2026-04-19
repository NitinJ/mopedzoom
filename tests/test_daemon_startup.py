from mopedzoomd.daemon import build_daemon_from_config
from mopedzoomd.config import ChannelConfig, Config


async def test_build_daemon_without_starting(tmp_path, monkeypatch):
    monkeypatch.setenv("MOPEDZOOM_STATE", str(tmp_path))
    cfg = Config(
        channel=ChannelConfig(bot_token="x", chat_id=-1, mode="header"),
        repos={},
    )

    # Monkeypatch so Telegram Bot is never constructed live.
    from mopedzoomd.channels import telegram as tg

    monkeypatch.setattr(
        tg,
        "Bot",
        lambda *a, **kw: type(
            "B",
            (),
            {"create_forum_topic": None, "send_message": None},
        )(),
    )
    d = await build_daemon_from_config(cfg, start=False)
    assert d is not None
    assert "cli" in d.channels
    assert "telegram" in d.channels
    await d.db.close()
