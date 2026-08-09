"""Regression tests derived from the wiring audit (AUDIT.md).

Each test pins a finding so the next person who tries to fix it has a green
bar to chase. Tests that describe CURRENT (buggy) behaviour are marked xfail
with a ``strict=True`` so that when the bug is fixed the test flips red and
demands attention.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from mopedzoomd.channels.cli_socket import CLISocketChannel
from mopedzoomd.daemon import TaskManager
from mopedzoomd.models import Task, TaskStatus
from mopedzoomd.playbooks import Playbook, StageSpec, load_playbooks
from mopedzoomd.scratch import ScratchDir
from mopedzoomd.stage_runner import StageResult
from mopedzoomd.state import StateDB


ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 1. Defined-but-unwired components
# ---------------------------------------------------------------------------


def test_sweeper_not_imported_by_daemon():
    """sweeper.sweep_once is now imported and wired in daemon.py (Batch 3 fix).

    Updated from the original pin that asserted the import was absent.
    """
    src = (ROOT / "src" / "mopedzoomd" / "daemon.py").read_text()
    assert "from .sweeper" in src
    assert "sweep_once" in src


def test_permission_mcp_not_wired_into_daemon():
    """handle_permission_request is now imported and wired in daemon.py (Batch 3 fix)."""
    src = (ROOT / "src" / "mopedzoomd" / "daemon.py").read_text()
    assert "permission_mcp" in src
    assert "handle_permission_request" in src


def test_bridges_not_imported_by_daemon():
    """bridges module is now imported by daemon.py (Batch 5 fix).

    _drain_scratch_bridges uses the FILES constant from bridges to do a
    single-pass poll of all three bridge files.
    """
    src = (ROOT / "src" / "mopedzoomd" / "daemon.py").read_text()
    assert "from .bridges" in src
    assert "_drain_scratch_bridges" in src


def test_scratch_approval_and_permission_readers_not_consulted():
    """_run_stage now drains approval.json via _drain_scratch_bridges (Batch 5 fix).

    Approval is logged as scratch_approval_seen and cleared; DB remains
    authoritative for state transitions.
    """
    src = (ROOT / "src" / "mopedzoomd" / "daemon.py").read_text()
    assert "_drain_scratch_bridges" in src
    assert "scratch_approval_seen" in src
    assert "clear_approval" in src


# ---------------------------------------------------------------------------
# CLI socket non-submit ops are no-ops (finding 1.6 / 6.1)
# ---------------------------------------------------------------------------


async def test_cli_socket_status_dispatches_to_handler(tmp_path):
    """status/tasks/cancel/... now hit an op_handler when wired (Batch 1 fix).

    Without a wired op_handler the channel still acks {ok:true} as a legacy
    fallback for any external callers. When the daemon wires an op_handler,
    responses carry the dispatched dict. This test pins the wired path.
    """
    import asyncio
    import json

    ch = CLISocketChannel(str(tmp_path / "sock"))
    await ch.start()
    ch.set_handler(AsyncMock())

    async def fake_op(op, payload):
        return {"ok": True, "dispatched": op, "payload_id": payload.get("id")}

    ch.set_op_handler(fake_op)

    reader, writer = await asyncio.open_unix_connection(str(tmp_path / "sock"))
    writer.write((json.dumps({"op": "status", "id": 1}) + "\n").encode())
    await writer.drain()
    buf = await reader.readline()
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    reply = json.loads(buf.decode())
    assert reply["ack"] is True
    assert reply["op"] == "status"
    assert reply["ok"] is True
    assert reply["dispatched"] == "status"
    assert reply["payload_id"] == 1
    # Legacy echo fields still preserved when not overridden by handler.
    assert reply["id"] == 1
    await ch.stop()


async def test_cli_socket_submit_actually_calls_handler(tmp_path):
    """Only `submit` is properly wired. Pins that contrast with status/tasks/..."""
    import asyncio
    import json

    ch = CLISocketChannel(str(tmp_path / "sock"))
    await ch.start()

    seen = {}

    async def handler(msg):
        seen["msg"] = msg

    ch.set_handler(handler)

    reader, writer = await asyncio.open_unix_connection(str(tmp_path / "sock"))
    writer.write((json.dumps({"op": "submit", "text": "research X"}) + "\n").encode())
    await writer.drain()
    await reader.readline()
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    assert seen["msg"].text == "research X"
    await ch.stop()


# ---------------------------------------------------------------------------
# 2. Worktree contract: playbooks with requires_worktree:true get cwd=scratch
# ---------------------------------------------------------------------------


async def test_requires_worktree_falls_back_to_scratch_when_no_wmgr(tmp_path):
    """When a playbook declares requires_worktree:true but no worktree_mgr is
    supplied, run_task falls back to the scratch dir as cwd (preserves legacy).
    """
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    pb = Playbook(
        id="bug-fix",
        summary="s",
        triggers=["fix"],
        requires_worktree=True,
        stages=[StageSpec(name="impl", requires="r", produces="x.md", approval="none")],
    )

    seen_cwd = {}

    class Runner:
        async def run(self, *, stage, stage_idx, agents, scratch, cwd, prompt, **kw):
            seen_cwd["cwd"] = cwd
            scratch.write_deliverable(stage_idx, stage.name, "done", [], "ok")
            return StageResult(
                exit_code=0,
                session_id="s",
                deliverable=scratch.read_deliverable(stage_idx, stage.name),
                transcript_path="/t",
            )

    channel = AsyncMock()
    channel.post = AsyncMock(return_value="ref")

    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=Runner(),
        playbook_registry={"bug-fix": pb},
        channels={"cli": channel},
        worktree_mgr=None,
        agent_discoverer=lambda: ["coder"],
    )
    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="bug-fix", inputs={})
    )
    await tm.run_task(tid)

    scratch = ScratchDir(str(tmp_path / "runs"), task_id=tid)
    assert seen_cwd["cwd"] == str(scratch.dir)
    await db.close()


async def test_requires_worktree_creates_worktree_and_uses_it_as_cwd(tmp_path):
    """When requires_worktree:true AND a wmgr is supplied AND inputs.repo is
    in the allowlist, run_task creates a worktree and uses it as cwd.
    """
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    pb = Playbook(
        id="bug-fix",
        summary="s",
        triggers=["fix"],
        requires_worktree=True,
        stages=[StageSpec(name="impl", requires="r", produces="x.md", approval="none")],
    )

    seen_cwd = {}

    class Runner:
        async def run(self, *, stage, stage_idx, agents, scratch, cwd, prompt, **kw):
            seen_cwd["cwd"] = cwd
            scratch.write_deliverable(stage_idx, stage.name, "done", [], "ok")
            return StageResult(
                exit_code=0,
                session_id="s",
                deliverable=scratch.read_deliverable(stage_idx, stage.name),
                transcript_path="/t",
            )

    channel = AsyncMock()
    channel.post = AsyncMock(return_value="ref")

    wt_path = str(tmp_path / "wt" / "123")
    wmgr = MagicMock()
    wmgr.allowed = {"myrepo": {"path": "/fake", "default_branch": "main"}}
    wmgr.create = MagicMock(return_value=(wt_path, "mopedzoom/123-bug-fix"))

    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=Runner(),
        playbook_registry={"bug-fix": pb},
        channels={"cli": channel},
        worktree_mgr=wmgr,
        agent_discoverer=lambda: ["coder"],
    )
    tid = await db.insert_task(
        Task(
            channel="cli",
            user_ref="u",
            playbook_id="bug-fix",
            inputs={"repo": "myrepo"},
        )
    )
    await tm.run_task(tid)

    wmgr.create.assert_called_once()
    assert seen_cwd["cwd"] == wt_path
    # Row inserted in worktrees table.
    wt = await db.get_worktree(tid)
    assert wt is not None
    assert wt.repo == "myrepo"
    assert wt.path == wt_path
    await db.close()


# ---------------------------------------------------------------------------
# 4. Schema mismatches: dashboard default port + init doc drift
# ---------------------------------------------------------------------------


def test_dashboard_port_default_is_9876():
    """Pins current default so docs drift away (currently 7777 in init.md/ui.md)
    is surfaced via this assertion paired with the ones below.
    """
    from mopedzoomd.config import DashboardConfig

    assert DashboardConfig().port == 9876


def test_init_md_dashboard_port_does_not_match_config_default():
    """Docs aligned (Batch 4 fix): init.md now correctly documents port 9876."""
    init_md = (ROOT / "commands" / "init.md").read_text()
    assert "9876" in init_md, "init.md should document the correct port 9876"


def test_ui_md_dashboard_port_does_not_match_config_default():
    """Docs aligned (Batch 4 fix): ui.md now correctly documents port 9876."""
    ui_md = (ROOT / "commands" / "ui.md").read_text()
    assert "9876" in ui_md, "ui.md should document the correct port 9876"


def test_init_md_stage_timeout_does_not_match_limits_default():
    """Docs aligned (Batch 4 fix): init.md now documents 30m (1800s) stage timeout."""
    from mopedzoomd.config import LimitsConfig

    assert LimitsConfig().default_stage_timeout_s == 1800  # 30 min
    init_md = (ROOT / "commands" / "init.md").read_text()
    assert "1800" in init_md or "30m" in init_md, \
        "init.md should document the correct 30m / 1800s stage timeout"


# ---------------------------------------------------------------------------
# 5. Error swallowing: submit_task fires-and-forgets run_task
# ---------------------------------------------------------------------------


async def test_submit_task_swallows_run_task_exceptions(tmp_path, caplog):
    """``asyncio.create_task(self.run_task(task_id))`` has no error handler.

    Verify the bug: a crash in run_task does NOT propagate out of submit_task.
    """
    import asyncio

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    pb = Playbook(
        id="p",
        summary="s",
        triggers=["t"],
        stages=[StageSpec(name="a", requires="r", produces="a.md", approval="none")],
    )
    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=AsyncMock(),
        playbook_registry={"p": pb},
        channels={"cli": AsyncMock()},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )

    async def crashing_run(tid):
        raise RuntimeError("boom")

    tm.run_task = crashing_run

    # submit_task returns happily even though run_task will crash.
    tid = await tm.submit_task(channel="cli", user_ref="u", text="t", playbook=pb)
    assert tid > 0
    # Give the background task a chance to run.
    await asyncio.sleep(0.05)
    await db.close()


# ---------------------------------------------------------------------------
# Research playbook contract: _build_prompt does NOT expose research_repo
# ---------------------------------------------------------------------------


def test_build_prompt_does_not_expose_research_repo(tmp_path):
    """Finding 2.1 fixed (Batch 2): when deliverables.research_repo is configured,
    _build_prompt now mentions the repo and path in publish stage prompts.

    This test verifies the fix: with no deliverables configured (None), no repo
    hint appears; this preserves backward-compatibility for unconfigured deploys.
    """
    from mopedzoomd.config import DeliverablesConfig

    # Without deliverables configured: no hint.
    tm_bare = TaskManager(
        db=None,
        runs_root=str(tmp_path),
        stage_runner=AsyncMock(),
        playbook_registry={},
        channels={},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
        deliverables=None,
    )
    publish = StageSpec(
        name="publish",
        requires="Commit report to the configured research repo/path",
        produces="commit_sha",
        approval="none",
    )
    pb = Playbook(id="research", summary="s", triggers=["research"], stages=[publish])
    task = Task(id=1, channel="cli", user_ref="u", playbook_id="research", inputs={})
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=1)
    prompt_bare = tm_bare._build_prompt(pb, publish, task, scratch, 0)
    assert "research_repo" not in prompt_bare
    assert "docs/research" not in prompt_bare

    # With deliverables configured: hint is present.
    tm_configured = TaskManager(
        db=None,
        runs_root=str(tmp_path),
        stage_runner=AsyncMock(),
        playbook_registry={},
        channels={},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
        deliverables=DeliverablesConfig(research_repo="my-repo", research_path="docs/research"),
    )
    prompt_full = tm_configured._build_prompt(pb, publish, task, scratch, 0)
    assert "my-repo" in prompt_full
    assert "docs/research" in prompt_full


# ---------------------------------------------------------------------------
# StageSpec timeout is declared but never enforced
# ---------------------------------------------------------------------------


def test_stage_runner_ignores_stage_timeout():
    """StageRunner.run now accepts timeout_s kwarg (Batch 2 fix).

    Updated from the original pin that asserted the gap, to assert the fix.
    """
    import inspect

    from mopedzoomd.stage_runner import StageRunner

    sig = inspect.signature(StageRunner.run)
    # timeout_s is now accepted — confirming the fix.
    assert "timeout_s" in sig.parameters


# ---------------------------------------------------------------------------
# Playbook YAML shipping integrity (sanity)
# ---------------------------------------------------------------------------


def test_shipped_bug_fix_requires_worktree_true():
    pbs = load_playbooks(builtin_dir=ROOT / "playbooks", user_dir=None)
    assert "bug-fix" in pbs
    assert pbs["bug-fix"].requires_worktree is True


def test_shipped_research_playbook_publish_stage_exists():
    pbs = load_playbooks(builtin_dir=ROOT / "playbooks", user_dir=None)
    stages = {s.name for s in pbs["research"].stages}
    assert "publish" in stages


# ---------------------------------------------------------------------------
# Batch 1 new tests
# ---------------------------------------------------------------------------


async def test_spawn_supervised_logs_exception(caplog):
    """_spawn_supervised logs any exception raised by the wrapped coroutine."""
    import asyncio
    import logging

    from mopedzoomd.daemon import _spawn_supervised

    caplog.set_level(logging.ERROR, logger="mopedzoomd")

    async def boom():
        raise RuntimeError("kaboom")

    t = _spawn_supervised(boom(), name="bang")
    # Let the done callback fire.
    with pytest.raises(RuntimeError):
        await t

    assert any("kaboom" in rec.message or "kaboom" in str(rec.exc_info) for rec in caplog.records) \
        or any("supervised task" in rec.message for rec in caplog.records)


async def test_cli_op_cancel_sets_task_cancelled(tmp_path):
    """CLI `cancel` op flips task status to CANCELLED via TaskManager.cancel."""
    from mopedzoomd.daemon import TaskManager, build_cli_op_handler

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    pb = Playbook(
        id="p",
        summary="s",
        triggers=["t"],
        stages=[StageSpec(name="a", requires="r", produces="a.md", approval="none")],
    )
    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=AsyncMock(),
        playbook_registry={"p": pb},
        channels={"cli": AsyncMock()},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))

    handler = build_cli_op_handler(tm)
    resp = await handler("cancel", {"id": tid})
    assert resp["ok"] is True
    assert resp["status"] == "cancelled"

    t = await db.get_task(tid)
    assert t.status == TaskStatus.CANCELLED
    await db.close()


async def test_cli_op_tasks_lists_tasks(tmp_path):
    """CLI `tasks` op returns a list of tasks from the DB."""
    from mopedzoomd.daemon import TaskManager, build_cli_op_handler

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    pb = Playbook(
        id="p",
        summary="s",
        triggers=["t"],
        stages=[StageSpec(name="a", requires="r", produces="a.md", approval="none")],
    )
    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=AsyncMock(),
        playbook_registry={"p": pb},
        channels={"cli": AsyncMock()},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )
    await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))
    await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))

    handler = build_cli_op_handler(tm)
    resp = await handler("tasks", {})
    assert resp["ok"] is True
    assert len(resp["tasks"]) == 2
    assert all(t["playbook"] == "p" for t in resp["tasks"])
    await db.close()


async def test_stage_runner_raises_on_empty_agents(tmp_path):
    """StageRunner.run raises NoAgentsAvailable when agents=[] and stage.agent is None."""
    from mopedzoomd.stage_runner import NoAgentsAvailable, StageRunner

    scratch = ScratchDir(str(tmp_path), task_id=1)
    scratch.create()
    stage = StageSpec(name="impl", requires="do", produces="x.md", approval="none")
    runner = StageRunner()
    with pytest.raises(NoAgentsAvailable):
        await runner.run(
            stage=stage,
            stage_idx=0,
            agents=[],
            scratch=scratch,
            cwd=str(tmp_path),
            prompt="do it",
            permission_mode="allowlist",
        )
