"""End-to-end smoke test: daemon + CLI socket + fake claude + task lifecycle."""

from __future__ import annotations

from pathlib import Path

from mopedzoomd.channels.cli_socket import CLISocketChannel
from mopedzoomd.daemon import TaskManager
from mopedzoomd.models import Task, TaskStatus
from mopedzoomd.playbooks import load_playbooks
from mopedzoomd.stage_runner import StageRunner
from mopedzoomd.state import StateDB


async def test_research_end_to_end(fake_claude, tmp_path):
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    root = Path(__file__).resolve().parents[2]
    registry = load_playbooks(builtin_dir=root / "playbooks", user_dir=None)
    channel = CLISocketChannel(str(tmp_path / "sock"))
    await channel.start()

    async def noop_handler(m):
        pass

    channel.set_handler(noop_handler)

    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=StageRunner(),
        playbook_registry=registry,
        channels={"cli": channel},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )
    # Force-auto-approve every stage by switching to `none` (monkeypatch the registry in-place).
    for st in registry["research"].stages:
        st.approval = "none"
    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="research", inputs={"topic": "x"})
    )
    await tm.run_task(tid)
    task = await db.get_task(tid)
    assert task.status == TaskStatus.DELIVERED
    await channel.stop()
    await db.close()
