import asyncio
import json

from unittest.mock import AsyncMock

from mopedzoomd.daemon import TaskManager
from mopedzoomd.state import StateDB
from mopedzoomd.stage_runner import StageResult
from mopedzoomd.models import Task, TaskStatus
from mopedzoomd.playbooks import Playbook, StageSpec


async def test_question_routed_to_channel(tmp_path):
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    runs = tmp_path / "runs"
    runs.mkdir()
    scratch = runs / "1"
    scratch.mkdir()
    (scratch / "question.json").write_text(json.dumps({"prompt": "mid or handler?"}))

    runner = AsyncMock()
    runner.run = AsyncMock(
        return_value=StageResult(
            exit_code=0,
            session_id="s",
            deliverable=None,
            transcript_path=str(scratch / "0-a.transcript"),
        )
    )
    channel = AsyncMock()
    channel.post = AsyncMock(return_value="ref")
    pb = Playbook(
        id="p",
        summary="s",
        triggers=["t"],
        stages=[StageSpec(name="a", requires="r", produces="a.md", approval="none")],
    )
    tm = TaskManager(
        db=db,
        runs_root=str(runs),
        stage_runner=runner,
        playbook_registry={"p": pb},
        channels={"cli": channel},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )
    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="p", inputs={})
    )
    task = asyncio.create_task(tm.run_task(tid))
    for _ in range(50):
        t = await db.get_task(tid)
        if t.status == TaskStatus.AWAITING_INPUT:
            break
        await asyncio.sleep(0.02)
    assert (await db.get_task(tid)).status == TaskStatus.AWAITING_INPUT
    channel.post.assert_awaited()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await db.close()
