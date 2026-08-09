"""Batch 5 tests: scratch bridge observability (_drain_scratch_bridges)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from mopedzoomd.daemon import TaskManager, _drain_scratch_bridges
from mopedzoomd.models import Task
from mopedzoomd.playbooks import Playbook, StageSpec
from mopedzoomd.scratch import ScratchDir
from mopedzoomd.stage_runner import StageResult
from mopedzoomd.state import StateDB


# ---------------------------------------------------------------------------
# _drain_scratch_bridges unit tests
# ---------------------------------------------------------------------------


def test_drain_scratch_bridges_returns_none_for_absent_files(tmp_path):
    """Empty scratch dir returns all-None dict."""
    scratch = ScratchDir(str(tmp_path), task_id=1)
    scratch.create()
    bridges = _drain_scratch_bridges(scratch)
    assert bridges["question"] is None
    assert bridges["approval"] is None
    assert bridges["permission"] is None


def test_drain_scratch_bridges_returns_parsed_approval(tmp_path):
    """approval.json present in scratch returns parsed content."""
    scratch = ScratchDir(str(tmp_path), task_id=1)
    scratch.create()
    payload = {"decision": "approve", "note": "looks good"}
    (scratch.dir / "approval.json").write_text(json.dumps(payload))
    bridges = _drain_scratch_bridges(scratch)
    assert bridges["approval"] == payload
    assert bridges["question"] is None
    assert bridges["permission"] is None


def test_drain_scratch_bridges_returns_question_and_approval(tmp_path):
    """Both question.json and approval.json present are both returned."""
    scratch = ScratchDir(str(tmp_path), task_id=1)
    scratch.create()
    (scratch.dir / "question.json").write_text(json.dumps({"prompt": "continue?"}))
    (scratch.dir / "approval.json").write_text(json.dumps({"decision": "approve"}))
    bridges = _drain_scratch_bridges(scratch)
    assert bridges["question"] == {"prompt": "continue?"}
    assert bridges["approval"] == {"decision": "approve"}
    assert bridges["permission"] is None


# ---------------------------------------------------------------------------
# _run_stage drains approval.json and logs event
# ---------------------------------------------------------------------------


async def test_run_stage_drains_approval_and_logs_event(tmp_path):
    """After a stage run, if approval.json is in scratch it is logged and cleared."""
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    approval_payload = {"decision": "approve", "note": "auto-approved"}

    class Runner:
        async def run(self, *, stage, stage_idx, agents, scratch, cwd, prompt, **kw):
            # Write approval.json during stage (before run returns)
            (scratch.dir / "approval.json").write_text(
                json.dumps(approval_payload)
            )
            scratch.write_deliverable(stage_idx, stage.name, "done", [], "ok")
            return StageResult(
                exit_code=0,
                session_id="s",
                deliverable=scratch.read_deliverable(stage_idx, stage.name),
                transcript_path="/t",
            )

    stage = StageSpec(name="impl", requires="r", produces="x.md", approval="none")
    pb = Playbook(id="p", summary="s", triggers=["t"], stages=[stage])
    channel = AsyncMock()
    channel.post = AsyncMock(return_value="ref")

    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=Runner(),
        playbook_registry={"p": pb},
        channels={"cli": channel},
        worktree_mgr=None,
        agent_discoverer=lambda: ["coder"],
    )
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))
    await tm.run_task(tid)

    # Verify the scratch_approval_seen event was logged
    events = await db.list_events(tid)
    event_kinds = [e.kind for e in events]
    assert "scratch_approval_seen" in event_kinds

    # Verify the approval.json was cleared
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=tid)
    assert not (scratch.dir / "approval.json").exists()

    await db.close()
