import datetime as dt
from unittest.mock import MagicMock

from mopedzoomd.sweeper import sweep_once
from mopedzoomd.state import StateDB
from mopedzoomd.models import Task, Worktree, WorktreeState


async def test_sweeps_expired_grace(tmp_path):
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="p", inputs={})
    )
    await db.insert_worktree(
        Worktree(task_id=tid, repo="x", path="/tmp/x", branch="b")
    )
    await db.set_worktree_state(tid, WorktreeState.GRACE)
    await db.execute(
        "UPDATE worktrees SET created_at=? WHERE task_id=?",
        (
            (dt.datetime.utcnow() - dt.timedelta(days=10)).isoformat(),
            tid,
        ),
    )
    mgr = MagicMock()
    await sweep_once(db, worktree_mgr=mgr, grace_days=7)
    assert mgr.destroy.call_count == 1
    assert (await db.get_worktree(tid)).state == WorktreeState.SWEPT
    await db.close()


async def test_does_not_sweep_fresh_grace(tmp_path):
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="p", inputs={})
    )
    await db.insert_worktree(
        Worktree(task_id=tid, repo="x", path="/tmp/x", branch="b")
    )
    await db.set_worktree_state(tid, WorktreeState.GRACE)
    mgr = MagicMock()
    await sweep_once(db, worktree_mgr=mgr, grace_days=7)
    assert mgr.destroy.call_count == 0
    assert (await db.get_worktree(tid)).state == WorktreeState.GRACE
    await db.close()
