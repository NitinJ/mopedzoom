"""Regression tests for the inbound-message router.

Exercises the ``handle_inbound`` helper (formerly a closure inside
``build_daemon_from_config.on_inbound``) to ensure:

  - replies to an existing task route to ``resolve_interaction``
  - unmatched text posts a "no matching playbook" ack
  - matching text calls ``tm.submit_task`` and posts a queued-ack
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from mopedzoomd.daemon import handle_inbound
from mopedzoomd.models import Interaction, InteractionKind, Task, TaskStatus
from mopedzoomd.playbooks import Playbook, StageSpec
from mopedzoomd.state import StateDB


@dataclass
class _Inbound:
    channel: str = "cli"
    user_ref: str = "socket:1"
    text: str = ""
    reply_to_ref: str | None = None
    task_id: int | None = None
    thread_id: int | None = None


def _pb(pid="research", triggers=("research",)):
    return Playbook(
        id=pid,
        summary="Research a topic",
        triggers=list(triggers),
        stages=[StageSpec(name="x", requires="r", produces="x.md", approval="none")],
    )


@pytest.fixture
async def db(tmp_path):
    d = StateDB(str(tmp_path / "s.db"))
    await d.connect()
    await d.migrate()
    yield d
    await d.close()


async def test_inbound_with_task_id_routes_to_resolve_interaction(db):
    # Seed a pending approval to be resolved.
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.AWAITING_APPROVAL)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.APPROVAL,
            prompt="go?",
            posted_to_channel_ref="x",
        )
    )

    router = AsyncMock()
    tm = AsyncMock()
    channel = AsyncMock()
    channels = {"cli": channel}

    msg = _Inbound(channel="cli", text="approve", task_id=tid)
    await handle_inbound(msg, db=db, router=router, tm=tm, channels=channels, registry={})

    # Interaction resolved, task now running.
    t = await db.get_task(tid)
    assert t.status == TaskStatus.RUNNING
    # Router + submit_task must NOT be consulted for a reply-type inbound.
    router.pick.assert_not_called()
    tm.submit_task.assert_not_called()
    channel.post.assert_not_called()


async def test_inbound_unknown_text_posts_no_match_ack(db):
    router = AsyncMock()
    router.pick = AsyncMock(return_value=None)
    tm = AsyncMock()
    channel = AsyncMock()
    channel.post = AsyncMock(return_value="ref")
    channels = {"cli": channel}
    registry = {"research": _pb()}

    msg = _Inbound(channel="cli", text="hello there")
    await handle_inbound(
        msg, db=db, router=router, tm=tm, channels=channels, registry=registry
    )

    router.pick.assert_awaited_once_with("hello there")
    tm.submit_task.assert_not_called()
    channel.post.assert_awaited_once()
    posted = channel.post.call_args.args[0]
    assert "No matching playbook" in posted.body
    assert "research" in posted.body  # available list mentioned


async def test_inbound_matching_text_submits_and_acks(db):
    pb = _pb("research", ("research",))
    router = AsyncMock()
    router.pick = AsyncMock(return_value=pb)
    tm = AsyncMock()
    tm.submit_task = AsyncMock(return_value=42)
    channel = AsyncMock()
    channel.post = AsyncMock(return_value="ref")
    channels = {"cli": channel}

    msg = _Inbound(channel="cli", text="please research OAuth")
    await handle_inbound(
        msg, db=db, router=router, tm=tm, channels=channels, registry={"research": pb}
    )

    tm.submit_task.assert_awaited_once_with(
        channel="cli", user_ref="socket:1", text="please research OAuth", playbook=pb
    )
    channel.post.assert_awaited_once()
    posted = channel.post.call_args.args[0]
    assert posted.task_id == 42
    assert "queued" in posted.body.lower()
    assert "research" in posted.body


async def test_inbound_empty_text_is_noop(db):
    router = AsyncMock()
    tm = AsyncMock()
    channel = AsyncMock()
    channels = {"cli": channel}

    msg = _Inbound(channel="cli", text="")
    await handle_inbound(msg, db=db, router=router, tm=tm, channels=channels, registry={})

    router.pick.assert_not_called()
    tm.submit_task.assert_not_called()
    channel.post.assert_not_called()


async def test_inbound_reply_to_ref_routes_to_revision_interaction(db):
    """reply_to_ref matching a pending REVISION interaction routes to resolve_interaction."""
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.AWAITING_INPUT)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.REVISION,
            prompt="revise?",
            posted_to_channel_ref="tg:100:0:999",
        )
    )

    router = AsyncMock()
    tm = AsyncMock()
    tm.runs_root = "/tmp/fake_runs"
    channel = AsyncMock()
    channels = {"cli": channel}

    msg = _Inbound(channel="cli", text="make it shorter", reply_to_ref="tg:100:0:999", task_id=None)
    await handle_inbound(msg, db=db, router=router, tm=tm, channels=channels, registry={})

    # resolve_interaction with free-text on REVISION sets AWAITING_INPUT
    t = await db.get_task(tid)
    assert t.status == TaskStatus.AWAITING_INPUT
    router.pick.assert_not_called()
    tm.submit_task.assert_not_called()


async def test_inbound_reply_to_ref_no_match_falls_through_to_new_task(db):
    """When reply_to_ref has no matching interaction, falls through to normal routing."""
    pb = _pb("research", ("research",))
    router = AsyncMock()
    router.pick = AsyncMock(return_value=pb)
    tm = AsyncMock()
    tm.runs_root = "/tmp/fake_runs"
    tm.submit_task = AsyncMock(return_value=42)
    channel = AsyncMock()
    channel.post = AsyncMock(return_value="ref")
    channels = {"cli": channel}

    msg = _Inbound(channel="cli", text="research oauth", reply_to_ref="tg:100:0:999", task_id=None)
    await handle_inbound(msg, db=db, router=router, tm=tm, channels=channels, registry={"research": pb})

    # No matching interaction → falls through to submit_task
    tm.submit_task.assert_awaited_once()


async def test_inbound_task_id_takes_priority_over_reply_to_ref(db):
    """task_id branch fires first; reply_to_ref lookup is never reached."""
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.AWAITING_APPROVAL)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.APPROVAL,
            prompt="approve?",
            posted_to_channel_ref="x",
        )
    )

    router = AsyncMock()
    tm = AsyncMock()
    tm.runs_root = "/tmp/fake_runs"
    channel = AsyncMock()
    channels = {"cli": channel}

    # reply_to_ref points to a non-existent ref — if it were consulted the lookup
    # would return None and routing would fall through (wrong outcome).
    msg = _Inbound(channel="cli", text="approve", task_id=tid, reply_to_ref="tg:100:0:999")
    await handle_inbound(msg, db=db, router=router, tm=tm, channels=channels, registry={})

    # task_id branch resolved the APPROVAL → RUNNING
    t = await db.get_task(tid)
    assert t.status == TaskStatus.RUNNING
    router.pick.assert_not_called()
    tm.submit_task.assert_not_called()
