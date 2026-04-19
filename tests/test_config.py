# tests/test_config.py
import pytest
from mopedzoomd.config import Config, RepoConfig, ChannelConfig, load_config, save_config

def test_config_roundtrip(tmp_path):
    c = Config(
        channel=ChannelConfig(bot_token="tok", chat_id=-123, mode="auto"),
        repos={"trial": RepoConfig(path="/tmp/x", default_branch="main", aliases=["t"])},
        default_repo="trial",
    )
    p = tmp_path / "config.yaml"
    save_config(c, p)
    c2 = load_config(p)
    assert c2.channel.bot_token == "tok"
    assert c2.repos["trial"].aliases == ["t"]
    assert c2.default_repo == "trial"

def test_config_rejects_bad_mode(tmp_path):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Config(channel=ChannelConfig(bot_token="t", chat_id=1, mode="bogus"))
