from __future__ import annotations

from pathlib import Path

import aiosqlite

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
