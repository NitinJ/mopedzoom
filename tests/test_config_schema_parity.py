"""Regression: ~/.mopedzoom/config.yaml field names must match config.py.

The init wizard had drifted: ``telegram`` vs ``channel``, repos-as-list vs
repos-as-dict, ``reviewers`` vs ``pr_reviewers``. Pin the current schema so a
future refactor that silently renames a field is caught.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mopedzoomd.config import Config


GOOD_CONFIG = {
    "channel": {"bot_token": "tok", "chat_id": -100, "mode": "header"},
    "repos": {
        "trialroomai": {
            "path": "/home/nitin/workspace/trialroomai",
            "default_branch": "main",
            "aliases": ["trial"],
            "pr_reviewers": ["nitin"],
        }
    },
    "default_repo": "trialroomai",
    "permissions": {"default_mode": "bypass", "allowlist": []},
    "dashboard": {"enabled": True, "port": 9876},
}


def test_good_config_validates():
    c = Config.model_validate(GOOD_CONFIG)
    assert c.channel.bot_token == "tok"
    assert c.channel.chat_id == -100
    assert "trialroomai" in c.repos
    assert c.repos["trialroomai"].pr_reviewers == ["nitin"]
    assert c.default_repo == "trialroomai"
    assert c.dashboard.port == 9876


def test_old_broken_shape_telegram_section_rejected():
    bad = dict(GOOD_CONFIG)
    del bad["channel"]
    bad["telegram"] = {"bot_token": "t", "chat_id": -1}
    with pytest.raises(ValidationError):
        Config.model_validate(bad)


def test_old_broken_shape_repos_as_list_rejected():
    bad = dict(GOOD_CONFIG)
    bad["repos"] = [
        {"name": "trialroomai", "path": "/x", "default_branch": "main"}
    ]
    with pytest.raises(ValidationError):
        Config.model_validate(bad)


def test_old_broken_shape_reviewers_key_ignored_silently():
    # ``reviewers`` is not a RepoConfig field; pydantic default is to ignore
    # extra keys, so the list ends up empty (silent regression). This test
    # makes that behavior explicit so anyone tightening it later knows what
    # changes.
    cfg = {
        **GOOD_CONFIG,
        "repos": {
            "trialroomai": {
                "path": "/x",
                "default_branch": "main",
                "reviewers": ["nitin"],  # old key
            }
        },
    }
    c = Config.model_validate(cfg)
    assert c.repos["trialroomai"].pr_reviewers == []
