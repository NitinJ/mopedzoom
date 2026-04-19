from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import aiosqlite

from .models import (
    AgentPick,
    Interaction,
    InteractionKind,
    Stage,
    StageStatus,
    Task,
    TaskEvent,
    TaskStatus,
    Worktree,
    WorktreeState,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel TEXT NOT NULL,
  user_ref TEXT NOT NULL,
  playbook_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  inputs_json TEXT NOT NULL DEFAULT '{}',
  parent_task_id INTEGER,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(parent_task_id) REFERENCES tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS stages (
  task_id INTEGER NOT NULL,
  idx INTEGER NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  session_id TEXT,
  agent_used TEXT,
  deliverable_path TEXT,
  transcript_path TEXT,
  started_at TIMESTAMP,
  ended_at TIMESTAMP,
  PRIMARY KEY(task_id, idx),
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS pending_interactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  stage_idx INTEGER NOT NULL,
  kind TEXT NOT NULL,
  prompt TEXT NOT NULL,
  posted_to_channel_ref TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS worktrees (
  task_id INTEGER PRIMARY KEY,
  repo TEXT NOT NULL,
  path TEXT NOT NULL,
  branch TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS agent_picks (
  task_id INTEGER NOT NULL,
  stage_idx INTEGER NOT NULL,
  agent_name TEXT NOT NULL,
  from_transcript_parse INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(task_id, stage_idx),
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS task_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  kind TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_task_events_task_id ON task_events(task_id);
"""


class StateDB:
    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")

    async def migrate(self) -> None:
        assert self._conn
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        assert self._conn
        async with self._conn.execute(sql, params) as cur:
            return await cur.fetchall()

    async def fetch_one(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        assert self._conn
        async with self._conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def execute(self, sql: str, params: tuple = ()) -> int:
        assert self._conn
        cur = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cur.lastrowid or 0


def _row_to_task(r) -> Task:
    return Task(
        id=r["id"],
        channel=r["channel"],
        user_ref=r["user_ref"],
        playbook_id=r["playbook_id"],
        inputs=json.loads(r["inputs_json"]),
        status=TaskStatus(r["status"]),
        parent_task_id=r["parent_task_id"],
        created_at=datetime.fromisoformat(r["created_at"]),
    )


def _row_to_stage(r) -> Stage:
    return Stage(
        task_id=r["task_id"],
        idx=r["idx"],
        name=r["name"],
        status=StageStatus(r["status"]),
        session_id=r["session_id"],
        agent_used=r["agent_used"],
        deliverable_path=r["deliverable_path"],
        transcript_path=r["transcript_path"],
        started_at=datetime.fromisoformat(r["started_at"]) if r["started_at"] else None,
        ended_at=datetime.fromisoformat(r["ended_at"]) if r["ended_at"] else None,
    )


class _TaskMixin:
    async def insert_task(self, t: Task) -> int:
        return await self.execute(
            "INSERT INTO tasks(channel,user_ref,playbook_id,status,inputs_json,parent_task_id) "
            "VALUES (?,?,?,?,?,?)",
            (
                t.channel,
                t.user_ref,
                t.playbook_id,
                t.status.value,
                json.dumps(t.inputs),
                t.parent_task_id,
            ),
        )

    async def get_task(self, tid: int) -> Task | None:
        r = await self.fetch_one("SELECT * FROM tasks WHERE id=?", (tid,))
        return _row_to_task(r) if r else None

    async def set_task_status(self, tid: int, status: TaskStatus) -> None:
        await self.execute("UPDATE tasks SET status=? WHERE id=?", (status.value, tid))

    async def list_tasks(
        self, statuses: list[TaskStatus] | None = None, limit: int = 100
    ) -> list[Task]:
        if statuses:
            placeholders = ",".join("?" * len(statuses))
            rows = await self.fetch_all(
                f"SELECT * FROM tasks WHERE status IN ({placeholders}) ORDER BY id DESC LIMIT ?",
                tuple(s.value for s in statuses) + (limit,),
            )
        else:
            rows = await self.fetch_all("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,))
        return [_row_to_task(r) for r in rows]

    async def insert_stage(self, s: Stage) -> None:
        await self.execute(
            "INSERT INTO stages(task_id,idx,name,status) VALUES (?,?,?,?)",
            (s.task_id, s.idx, s.name, s.status.value),
        )

    async def get_stages(self, tid: int) -> list[Stage]:
        rows = await self.fetch_all("SELECT * FROM stages WHERE task_id=? ORDER BY idx", (tid,))
        return [_row_to_stage(r) for r in rows]

    async def update_stage(self, tid: int, idx: int, **fields) -> None:
        if not fields:
            return
        sets, vals = [], []
        for k, v in fields.items():
            if isinstance(v, StageStatus):
                v = v.value
            if isinstance(v, datetime):
                v = v.isoformat()
            sets.append(f"{k}=?")
            vals.append(v)
        vals += [tid, idx]
        await self.execute(
            f"UPDATE stages SET {','.join(sets)} WHERE task_id=? AND idx=?",
            tuple(vals),
        )


for _name in dir(_TaskMixin):
    if not _name.startswith("_"):
        setattr(StateDB, _name, getattr(_TaskMixin, _name))


def _row_to_int(r) -> Interaction:
    return Interaction(
        id=r["id"],
        task_id=r["task_id"],
        stage_idx=r["stage_idx"],
        kind=InteractionKind(r["kind"]),
        prompt=r["prompt"],
        posted_to_channel_ref=r["posted_to_channel_ref"],
        created_at=datetime.fromisoformat(r["created_at"]),
    )


def _row_to_worktree(r) -> Worktree:
    return Worktree(
        task_id=r["task_id"],
        repo=r["repo"],
        path=r["path"],
        branch=r["branch"],
        state=WorktreeState(r["state"]),
        created_at=datetime.fromisoformat(r["created_at"]),
    )


class _MiscMixin:
    async def insert_interaction(self, i: Interaction) -> int:
        return await self.execute(
            "INSERT INTO pending_interactions(task_id,stage_idx,kind,prompt,posted_to_channel_ref) "
            "VALUES (?,?,?,?,?)",
            (i.task_id, i.stage_idx, i.kind.value, i.prompt, i.posted_to_channel_ref),
        )

    async def list_pending_interactions(self, tid: int) -> list[Interaction]:
        rows = await self.fetch_all(
            "SELECT * FROM pending_interactions WHERE task_id=? ORDER BY id", (tid,)
        )
        return [_row_to_int(r) for r in rows]

    async def resolve_interaction(self, iid: int) -> None:
        await self.execute("DELETE FROM pending_interactions WHERE id=?", (iid,))

    async def insert_worktree(self, w: Worktree) -> None:
        await self.execute(
            "INSERT INTO worktrees(task_id,repo,path,branch,state) VALUES (?,?,?,?,?)",
            (w.task_id, w.repo, w.path, w.branch, w.state.value),
        )

    async def get_worktree(self, tid: int) -> Worktree | None:
        r = await self.fetch_one("SELECT * FROM worktrees WHERE task_id=?", (tid,))
        return _row_to_worktree(r) if r else None

    async def set_worktree_state(self, tid: int, state: WorktreeState) -> None:
        await self.execute("UPDATE worktrees SET state=? WHERE task_id=?", (state.value, tid))

    async def record_agent_pick(self, p: AgentPick) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO agent_picks(task_id,stage_idx,agent_name,from_transcript_parse) "
            "VALUES (?,?,?,?)",
            (p.task_id, p.stage_idx, p.agent_name, int(p.from_transcript_parse)),
        )

    async def list_agent_picks(self, tid: int) -> list[AgentPick]:
        rows = await self.fetch_all(
            "SELECT * FROM agent_picks WHERE task_id=? ORDER BY stage_idx", (tid,)
        )
        return [
            AgentPick(
                task_id=r["task_id"],
                stage_idx=r["stage_idx"],
                agent_name=r["agent_name"],
                from_transcript_parse=bool(r["from_transcript_parse"]),
            )
            for r in rows
        ]

    async def log_event(self, e: TaskEvent) -> None:
        await self.execute(
            "INSERT INTO task_events(task_id,kind,detail_json) VALUES (?,?,?)",
            (e.task_id, e.kind, json.dumps(e.detail)),
        )

    async def list_events(self, tid: int) -> list[TaskEvent]:
        rows = await self.fetch_all("SELECT * FROM task_events WHERE task_id=? ORDER BY id", (tid,))
        return [
            TaskEvent(
                id=r["id"],
                task_id=r["task_id"],
                kind=r["kind"],
                detail=json.loads(r["detail_json"]),
                ts=datetime.fromisoformat(r["ts"]),
            )
            for r in rows
        ]


for _n in dir(_MiscMixin):
    if not _n.startswith("_"):
        setattr(StateDB, _n, getattr(_MiscMixin, _n))
