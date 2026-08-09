"""Regression tests for TaskManager.submit_task (added during Telegram bring-up).

Before the fix there was no public submit path — inbound Telegram messages had
nowhere to land. This test pins the contract: submit_task must insert a QUEUED
task, log a ``task_submitted`` event, schedule run_task as a background task,
and return the id synchronously.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from mopedzoomd.daemon import TaskManager
from mopedzoomd.models import TaskStatus
from mopedzoomd.playbooks import Playbook, StageSpec
from mopedzoomd.state import StateDB


@pytest.fixture
async def db(tmp_path):
    d = StateDB(str(tmp_path / "s.db"))
    await d.connect()
    await d.migrate()
    yield d
    await d.close()


def _pb():
    return Playbook(
        id="pb",
        summary="s",
        triggers=["t"],
        stages=[StageSpec(name="a", requires="r", produces="a.md", approval="none")],
    )


async def test_submit_task_inserts_queued_and_schedules_run(db, tmp_path, monkeypatch):
    pb = _pb()
    runner = AsyncMock()
    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=runner,
        playbook_registry={"pb": pb},
        channels={"cli": AsyncMock()},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )

    # Stub run_task so we observe scheduling without executing the stage loop.
    called = asyncio.Event()
    seen = {}

    async def fake_run(tid):
        seen["tid"] = tid
        called.set()

    monkeypatch.setattr(tm, "run_task", fake_run)

    tid = await tm.submit_task(
        channel="cli", user_ref="u", text="research OAuth", playbook=pb
    )

    assert isinstance(tid, int) and tid > 0

    # Task inserted with QUEUED status and correct fields.
    t = await db.get_task(tid)
    assert t is not None
    assert t.status == TaskStatus.QUEUED
    assert t.channel == "cli"
    assert t.user_ref == "u"
    assert t.playbook_id == "pb"
    assert t.inputs == {"request": "research OAuth"}

    # task_submitted event logged with truncated text.
    events = await db.list_events(tid)
    kinds = [e.kind for e in events]
    assert "task_submitted" in kinds
    submitted = next(e for e in events if e.kind == "task_submitted")
    assert submitted.detail["text"] == "research OAuth"

    # run_task scheduled and actually executed.
    await asyncio.wait_for(called.wait(), timeout=1.0)
    assert seen["tid"] == tid


async def test_submit_task_truncates_long_text_in_event(db, tmp_path, monkeypatch):
    pb = _pb()
    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=AsyncMock(),
        playbook_registry={"pb": pb},
        channels={"cli": AsyncMock()},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )

    async def noop(_):
        pass

    monkeypatch.setattr(tm, "run_task", noop)

    long_text = "x" * 500
    tid = await tm.submit_task(channel="cli", user_ref="u", text=long_text, playbook=pb)
    events = await db.list_events(tid)
    submitted = next(e for e in events if e.kind == "task_submitted")
    assert len(submitted.detail["text"]) == 200
