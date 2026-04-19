import pytest
from unittest.mock import AsyncMock

from mopedzoomd.daemon import TaskManager
from mopedzoomd.state import StateDB
from mopedzoomd.models import Task, TaskStatus
from mopedzoomd.playbooks import Playbook, StageSpec
from mopedzoomd.stage_runner import StageResult


@pytest.fixture
async def setup(tmp_path):
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    runs = tmp_path / "runs"
    runs.mkdir()
    pb = Playbook(
        id="pb",
        summary="s",
        triggers=["t"],
        requires_worktree=False,
        stages=[
            StageSpec(name="a", requires="r", produces="a.md", approval="none"),
            StageSpec(name="b", requires="r", produces="b.md", approval="none"),
        ],
    )
    yield db, runs, pb
    await db.close()


async def test_happy_path_two_stages(setup):
    db, runs, pb = setup
    runner = AsyncMock()
    runner.run = AsyncMock(
        side_effect=[
            StageResult(
                exit_code=0,
                session_id="s1",
                deliverable={"stage": "a", "status": "ok", "artifacts": [], "notes": ""},
                transcript_path="/t/a",
            ),
            StageResult(
                exit_code=0,
                session_id="s2",
                deliverable={"stage": "b", "status": "ok", "artifacts": [], "notes": ""},
                transcript_path="/t/b",
            ),
        ]
    )
    channel = AsyncMock()
    channel.post = AsyncMock(return_value="ref")
    tm = TaskManager(
        db=db,
        runs_root=str(runs),
        stage_runner=runner,
        playbook_registry={"pb": pb},
        channels={"cli": channel},
        worktree_mgr=None,
        agent_discoverer=lambda: ["coder"],
    )
    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="pb", inputs={})
    )
    await tm.run_task(tid)
    final = await db.get_task(tid)
    assert final.status == TaskStatus.DELIVERED
    assert runner.run.await_count == 2
