"""Sweeper: grace-period worktree cleanup."""

from __future__ import annotations

import datetime as dt

from .models import WorktreeState
from .state import StateDB


async def sweep_once(db: StateDB, *, worktree_mgr, grace_days: int) -> None:
    rows = await db.fetch_all(
        "SELECT task_id,repo,path,branch,created_at FROM worktrees WHERE state=?",
        (WorktreeState.GRACE.value,),
    )
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=grace_days)
    for r in rows:
        created = dt.datetime.fromisoformat(r["created_at"])
        if created < cutoff:
            worktree_mgr.destroy(
                task_id=r["task_id"],
                repo_name=r["repo"],
                path=r["path"],
                branch=r["branch"],
                delete_branch=True,
            )
            await db.set_worktree_state(r["task_id"], WorktreeState.SWEPT)
