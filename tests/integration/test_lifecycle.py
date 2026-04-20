"""Lifecycle integration tests: approval gate, question gate, failure, CLI ops."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mopedzoomd.channels.base import Channel, OutboundMessage
from mopedzoomd.channels.cli_socket import CLISocketChannel
from mopedzoomd.daemon import TaskManager, build_cli_op_handler, resolve_interaction
from mopedzoomd.models import Interaction, InteractionKind, Task, StageStatus, TaskStatus
from mopedzoomd.playbooks import Playbook, StageSpec
from mopedzoomd.stage_runner import StageRunner
from mopedzoomd.state import StateDB


class _RecordingChannel(Channel):
    """Minimal channel that records outbound messages."""

    def __init__(self):
        self.posts: list[OutboundMessage] = []
        self._handler = None

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def set_handler(self, handler) -> None:
        self._handler = handler

    async def post(self, msg: OutboundMessage) -> str:
        self.posts.append(msg)
        return "ref:1"


async def test_approval_gate_full_cycle(fake_claude_variant, tmp_path):
    """run_task pauses at AWAITING_APPROVAL; injected approval resumes it to DELIVERED."""
    # The fake claude writes the deliverable for whichever stage is currently running.
    # It uses $MOPEDZOOM_STAGE and $MOPEDZOOM_SCRATCH to write the right manifest file.
    fake_claude_variant(
        """
echo "session-id: sess-approval"
stage="$MOPEDZOOM_STAGE"
scratch="$MOPEDZOOM_SCRATCH"

if [ "$stage" = "impl" ]; then
    cat > "$scratch/0-impl.deliverable.json" <<'EOF'
{"stage":"impl","status":"ok","artifacts":[],"notes":"done"}
EOF
elif [ "$stage" = "verify" ]; then
    cat > "$scratch/1-verify.deliverable.json" <<'EOF'
{"stage":"verify","status":"ok","artifacts":[],"notes":"done"}
EOF
fi
"""
    )

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    pb = Playbook(
        id="two-stage",
        summary="two stage approval test",
        triggers=["two"],
        stages=[
            StageSpec(name="impl", requires="do X", produces="impl.md", approval="none"),
            StageSpec(name="verify", requires="verify X", produces="verify.md", approval="required"),
        ],
    )
    ch = _RecordingChannel()
    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=StageRunner(),
        playbook_registry={"two-stage": pb},
        channels={"cli": ch},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )

    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="two-stage", inputs={})
    )

    async def inject_approval():
        for _ in range(200):
            task = await db.get_task(tid)
            if task.status == TaskStatus.AWAITING_APPROVAL:
                await resolve_interaction(db, task_id=tid, answer="approve")
                return
            await asyncio.sleep(0.05)
        raise TimeoutError("never reached AWAITING_APPROVAL")

    await asyncio.gather(tm.run_task(tid), inject_approval())

    task = await db.get_task(tid)
    assert task.status == TaskStatus.DELIVERED

    events = await db.list_events(tid)
    kinds = [e.kind for e in events]
    assert "stage_done" in kinds
    assert "resolved_approve" in kinds
    await db.close()


async def test_question_gate_full_cycle(fake_claude_variant, tmp_path):
    """Agent writes question.json on first run; answer injected; second run delivers."""
    fake_claude_variant(
        """
echo "session-id: sess-question"
SENTINEL="$MOPEDZOOM_SCRATCH/first_run_done"
if [ ! -f "$SENTINEL" ]; then
    touch "$SENTINEL"
    cat > "$MOPEDZOOM_SCRATCH/question.json" <<'EOF'
{"prompt":"Which city?","kind":"free_text"}
EOF
    exit 0
fi
cat > "$MOPEDZOOM_SCRATCH/0-impl.deliverable.json" <<'EOF'
{"stage":"impl","status":"ok","artifacts":[],"notes":"done"}
EOF
"""
    )

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    pb = Playbook(
        id="question-test",
        summary="question gate test",
        triggers=["q"],
        stages=[
            StageSpec(name="impl", requires="do X", produces="impl.md", approval="none"),
        ],
    )
    ch = _RecordingChannel()
    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=StageRunner(),
        playbook_registry={"question-test": pb},
        channels={"cli": ch},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )

    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="question-test", inputs={})
    )

    async def inject_answer():
        for _ in range(200):
            task = await db.get_task(tid)
            if task.status == TaskStatus.AWAITING_INPUT:
                await resolve_interaction(db, task_id=tid, answer="Paris")
                return
            await asyncio.sleep(0.05)
        raise TimeoutError("never reached AWAITING_INPUT")

    await asyncio.gather(tm.run_task(tid), inject_answer())

    task = await db.get_task(tid)
    assert task.status == TaskStatus.DELIVERED

    bodies = [p.body for p in ch.posts]
    assert any("Which city?" in b for b in bodies)

    events = await db.list_events(tid)
    kinds = [e.kind for e in events]
    assert "stage_done" in kinds
    await db.close()


async def test_stage_failure_no_manifest(fake_claude_variant, tmp_path):
    """Agent exits 0 but writes no manifest; task ends FAILED with stage_failed event."""
    fake_claude_variant(
        """
echo "session-id: sess-fail"
# Deliberately write no deliverable manifest.
"""
    )

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    pb = Playbook(
        id="fail-test",
        summary="failure test",
        triggers=["fail"],
        stages=[
            StageSpec(name="impl", requires="do X", produces="x.md", approval="none"),
        ],
    )
    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=StageRunner(),
        playbook_registry={"fail-test": pb},
        channels={"cli": _RecordingChannel()},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )

    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="fail-test", inputs={})
    )
    await tm.run_task(tid)

    task = await db.get_task(tid)
    assert task.status == TaskStatus.FAILED

    events = await db.list_events(tid)
    assert any(e.kind == "stage_failed" for e in events)

    # The stage exit code was 0 so DB marks it DONE, but no deliverable was written —
    # the task is still FAILED and the stage_failed event was emitted.
    stages = await db.get_stages(tid)
    assert stages[0].status in (StageStatus.DONE, StageStatus.FAILED)
    await db.close()


async def test_cli_socket_ops_round_trip(tmp_path):
    """Real socket send/receive for status, cancel, resume ops via CLISocketChannel."""
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    pb = Playbook(
        id="dummy",
        summary="dummy",
        triggers=["dummy"],
        stages=[StageSpec(name="impl", requires="r", produces="x.md", approval="none")],
    )
    ch = CLISocketChannel(str(tmp_path / "sock"))
    await ch.start()
    ch.set_handler(AsyncMock())
    ch.set_op_handler(
        build_cli_op_handler(
            TaskManager(
                db=db,
                runs_root=str(tmp_path / "runs"),
                stage_runner=StageRunner(),
                playbook_registry={"dummy": pb},
                channels={"cli": ch},
                worktree_mgr=None,
                agent_discoverer=lambda: [],
            )
        )
    )

    t1 = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="dummy", inputs={}))
    t2 = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="dummy", inputs={}))

    async def send_op(payload: dict) -> dict:
        r, w = await asyncio.open_unix_connection(str(tmp_path / "sock"))
        try:
            w.write((json.dumps(payload) + "\n").encode())
            await w.drain()
            line = await r.readline()
            return json.loads(line.decode())
        finally:
            w.close()
            try:
                await w.wait_closed()
            except (RuntimeError, BrokenPipeError):
                pass

    # list tasks
    resp = await send_op({"op": "tasks"})
    assert resp["ack"] is True
    assert resp["ok"] is True
    assert len(resp["tasks"]) == 2

    # get status
    resp = await send_op({"op": "status", "id": t1})
    assert resp["ok"] is True
    assert resp["id"] == t1
    assert "status" in resp

    # cancel task 1
    resp = await send_op({"op": "cancel", "id": t1})
    assert resp["ok"] is True
    task = await db.get_task(t1)
    assert task.status == TaskStatus.CANCELLED

    # resume task 2 (set to PAUSED first so resume is valid)
    await db.set_task_status(t2, TaskStatus.PAUSED)
    resp = await send_op({"op": "resume", "id": t2})
    assert resp["ok"] is True
    task = await db.get_task(t2)
    assert task.status == TaskStatus.RUNNING

    await ch.stop()
    await db.close()
