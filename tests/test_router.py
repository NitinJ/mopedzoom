from unittest.mock import AsyncMock

import pytest

from mopedzoomd.playbooks import Playbook, StageSpec
from mopedzoomd.router import Router


def make_pb(pid, summary, triggers):
    return Playbook(
        id=pid,
        summary=summary,
        triggers=triggers,
        stages=[
            StageSpec(name="x", requires="do", produces="x.md", approval="none")
        ],
    )


@pytest.fixture
def reg():
    return {
        "bug-fix": make_pb("bug-fix", "Fix a bug", ["fix", "bug"]),
        "research": make_pb(
            "research", "Research a topic", ["research", "investigate"]
        ),
    }


async def test_deterministic_match_wins(reg):
    r = Router(reg, claude_client=None)
    pb = await r.pick("please fix the login bug")
    assert pb.id == "bug-fix"


async def test_llm_fallback_on_ambiguity(reg):
    fake = AsyncMock()
    fake.messages.create = AsyncMock(
        return_value=type(
            "X",
            (),
            {
                "content": [
                    type(
                        "Y",
                        (),
                        {"text": '{"pick":"research","confidence":0.9}'},
                    )()
                ]
            },
        )()
    )
    r = Router(reg, claude_client=fake)
    pb = await r.pick("I want to look into how OAuth tokens expire")
    assert pb.id == "research"
    fake.messages.create.assert_awaited_once()


async def test_unresolvable_returns_none(reg):
    fake = AsyncMock()
    fake.messages.create = AsyncMock(
        return_value=type(
            "X",
            (),
            {
                "content": [
                    type(
                        "Y",
                        (),
                        {"text": '{"pick":null,"confidence":0.1}'},
                    )()
                ]
            },
        )()
    )
    r = Router(reg, claude_client=fake)
    assert await r.pick("hello") is None
