import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mopedzoomd.daemon import TaskManager, _RetryStage, _StageFailed, resolve_interaction
from mopedzoomd.models import Interaction, InteractionKind, Task, TaskStatus
from mopedzoomd.scratch import ScratchDir
from mopedzoomd.state import StateDB


async def test_resolve_question_answer_writes_scratch_and_sets_awaiting_input(tmp_path):
    from mopedzoomd.scratch import ScratchDir

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="p", inputs={})
    )
    await db.set_task_status(tid, TaskStatus.AWAITING_INPUT)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.QUESTION,
            prompt="what angle?",
            posted_to_channel_ref="tg:x",
        )
    )
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=tid)
    scratch.create()
    await resolve_interaction(
        db, task_id=tid, answer="south india focus", scratch=scratch
    )
    assert scratch.read_answer(0) == "south india focus"
    assert (await db.get_task(tid)).status == TaskStatus.AWAITING_INPUT
    assert len(await db.list_pending_interactions(tid)) == 0
    await db.close()


async def test_resolve_revision_feedback_appends_scratch_and_sets_awaiting_input(
    tmp_path,
):
    from mopedzoomd.scratch import ScratchDir

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="p", inputs={})
    )
    await db.set_task_status(tid, TaskStatus.AWAITING_APPROVAL)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.REVISION,
            prompt="review pre-brief",
            posted_to_channel_ref="tg:y",
        )
    )
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=tid)
    scratch.create()
    await resolve_interaction(db, task_id=tid, answer="add welfare section", scratch=scratch)
    assert scratch.read_feedback(0) == ["add welfare section"]
    assert (await db.get_task(tid)).status == TaskStatus.AWAITING_INPUT
    assert len(await db.list_pending_interactions(tid)) == 0
    await db.close()


async def test_resolve_approve_with_scratch_still_sets_running(tmp_path):
    from mopedzoomd.scratch import ScratchDir

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="p", inputs={})
    )
    await db.set_task_status(tid, TaskStatus.AWAITING_APPROVAL)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.REVISION,
            prompt="review",
            posted_to_channel_ref="tg:z",
        )
    )
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=tid)
    scratch.create()
    await resolve_interaction(db, task_id=tid, answer="approve", scratch=scratch)
    assert (await db.get_task(tid)).status == TaskStatus.RUNNING
    await db.close()


async def test_resolve_approve_sets_running(tmp_path):
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.AWAITING_APPROVAL)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.APPROVAL,
            prompt="go?",
            posted_to_channel_ref="x",
        )
    )
    await resolve_interaction(db, task_id=tid, answer="approve")
    assert (await db.get_task(tid)).status == TaskStatus.RUNNING
    assert len(await db.list_pending_interactions(tid)) == 0
    await db.close()


async def test_resolve_cancel_sets_cancelled(tmp_path):
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.AWAITING_APPROVAL)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.APPROVAL,
            prompt="go?",
            posted_to_channel_ref="x",
        )
    )
    await resolve_interaction(db, task_id=tid, answer="cancel")
    assert (await db.get_task(tid)).status == TaskStatus.CANCELLED
    await db.close()


async def test_resolve_revise_sets_awaiting_input(tmp_path):
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.AWAITING_APPROVAL)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.APPROVAL,
            prompt="go?",
            posted_to_channel_ref="x",
        )
    )
    await resolve_interaction(db, task_id=tid, answer="revise")
    assert (await db.get_task(tid)).status == TaskStatus.AWAITING_INPUT
    await db.close()


async def test_resolve_pause_resume(tmp_path):
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.RUNNING)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.APPROVAL,
            prompt="go?",
            posted_to_channel_ref="x",
        )
    )
    await resolve_interaction(db, task_id=tid, answer="pause")
    assert (await db.get_task(tid)).status == TaskStatus.PAUSED

    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.APPROVAL,
            prompt="go?",
            posted_to_channel_ref="x",
        )
    )
    await resolve_interaction(db, task_id=tid, answer="resume")
    assert (await db.get_task(tid)).status == TaskStatus.RUNNING
    await db.close()


# ---------------------------------------------------------------------------
# _await_review tests
# ---------------------------------------------------------------------------


def _make_task_manager(tmp_path: Path, db: StateDB) -> TaskManager:
    """Build a minimal TaskManager with a mock channel for _await_review tests."""
    channel = MagicMock()
    channel.post = AsyncMock(return_value="tg:1:0:42")
    return TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=MagicMock(),
        playbook_registry={},
        channels={"telegram": channel},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )


def _make_stage_spec(idx: int = 0):
    from mopedzoomd.playbooks import StageSpec

    return StageSpec(
        name="pre-brief",
        requires="write brief",
        produces="brief.md",
        approval="review",
    ), idx


async def test_await_review_approve_resolves_stage(tmp_path):
    """_await_review should return normally when task status is RUNNING (approved)."""
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    tid = await db.insert_task(Task(channel="telegram", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.RUNNING)

    tm = _make_task_manager(tmp_path, db)
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=tid)
    scratch.create()

    # Write a deliverable manifest with one artifact
    artifact_file = scratch.dir / "brief.md"
    artifact_file.write_text("# Brief content")
    scratch.write_deliverable(0, "pre-brief", "done", [{"path": "brief.md", "kind": "markdown"}])

    sspec, idx = _make_stage_spec(0)

    # Simulate: _await_review posts, inserts interaction, then the interaction
    # is immediately resolved (no pending interactions → task is RUNNING).
    # We patch list_pending_interactions to return [] on the first call so the
    # poll loop exits immediately, then get_task returns RUNNING.
    original_list_pending = db.list_pending_interactions

    call_count = 0

    async def fake_list_pending(task_id):
        nonlocal call_count
        call_count += 1
        # First call returns the newly-inserted interaction; simulate resolution
        if call_count == 1:
            # Let the real DB run so the interaction actually gets inserted
            result = await original_list_pending(task_id)
            # Immediately resolve all pending interactions to simulate approval
            for i in result:
                await db.resolve_interaction(i.id)
            await db.set_task_status(task_id, TaskStatus.RUNNING)
            return []
        return []

    db.list_pending_interactions = fake_list_pending

    channel = tm.channels["telegram"]
    await tm._await_review(
        task_id=tid,
        stage=sspec,
        idx=idx,
        scratch=scratch,
        channel=channel,
    )

    # channel.post was called with document_path set and an ApprovalButton
    channel.post.assert_called_once()
    posted_msg = channel.post.call_args[0][0]
    assert posted_msg.document_path == artifact_file
    assert len(posted_msg.buttons) == 1
    btn = posted_msg.buttons[0]
    assert btn.callback == "approve"
    assert "Approve" in btn.label

    await db.close()


async def test_await_review_feedback_raises_retry_stage(tmp_path):
    """_await_review should raise _RetryStage when task status is AWAITING_INPUT (feedback sent)."""
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    tid = await db.insert_task(Task(channel="telegram", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.RUNNING)

    tm = _make_task_manager(tmp_path, db)
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=tid)
    scratch.create()

    artifact_file = scratch.dir / "brief.md"
    artifact_file.write_text("# Brief content")
    scratch.write_deliverable(0, "pre-brief", "done", [{"path": "brief.md", "kind": "markdown"}])

    sspec, idx = _make_stage_spec(0)

    original_list_pending = db.list_pending_interactions
    call_count = 0

    async def fake_list_pending(task_id):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            result = await original_list_pending(task_id)
            # Resolve interaction but set AWAITING_INPUT (feedback scenario)
            for i in result:
                await db.resolve_interaction(i.id)
            await db.set_task_status(task_id, TaskStatus.AWAITING_INPUT)
            return []
        return []

    db.list_pending_interactions = fake_list_pending

    channel = tm.channels["telegram"]
    with pytest.raises(_RetryStage):
        await tm._await_review(
            task_id=tid,
            stage=sspec,
            idx=idx,
            scratch=scratch,
            channel=channel,
        )

    await db.close()


async def test_await_review_missing_deliverable_raises_stage_failed(tmp_path):
    """_await_review should raise _StageFailed when there's no deliverable manifest."""
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    tid = await db.insert_task(Task(channel="telegram", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.RUNNING)

    tm = _make_task_manager(tmp_path, db)
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=tid)
    scratch.create()
    # No deliverable written

    sspec, idx = _make_stage_spec(0)
    channel = tm.channels["telegram"]

    with pytest.raises(_StageFailed):
        await tm._await_review(
            task_id=tid,
            stage=sspec,
            idx=idx,
            scratch=scratch,
            channel=channel,
        )


async def test_await_review_artifact_file_missing_raises_stage_failed(tmp_path):
    """_await_review should raise _StageFailed when manifest exists but artifact file is missing."""
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    tid = await db.insert_task(Task(channel="telegram", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.RUNNING)

    tm = _make_task_manager(tmp_path, db)
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=tid)
    scratch.create()

    # Write manifest referencing an artifact that does NOT exist on disk
    scratch.write_deliverable(0, "pre-brief", "done", [{"path": "brief.md", "kind": "markdown"}])
    # Intentionally do NOT write scratch.dir / "brief.md"

    sspec, idx = _make_stage_spec(0)
    channel = tm.channels["telegram"]

    with pytest.raises(_StageFailed):
        await tm._await_review(
            task_id=tid,
            stage=sspec,
            idx=idx,
            scratch=scratch,
            channel=channel,
        )

    await db.close()


# ---------------------------------------------------------------------------
# Fix 1: unexpected status raises RuntimeError
# ---------------------------------------------------------------------------


async def test_await_review_unexpected_status_raises_runtime_error(tmp_path):
    """_await_review should raise RuntimeError when status is PAUSED (unexpected)."""
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    tid = await db.insert_task(Task(channel="telegram", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.RUNNING)

    tm = _make_task_manager(tmp_path, db)
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=tid)
    scratch.create()

    artifact_file = scratch.dir / "brief.md"
    artifact_file.write_text("# Brief content")
    scratch.write_deliverable(0, "pre-brief", "done", [{"path": "brief.md", "kind": "markdown"}])

    sspec, idx = _make_stage_spec(0)

    original_list_pending = db.list_pending_interactions
    call_count = 0

    async def fake_list_pending(task_id):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            result = await original_list_pending(task_id)
            for i in result:
                await db.resolve_interaction(i.id)
            await db.set_task_status(task_id, TaskStatus.PAUSED)
            return []
        return []

    db.list_pending_interactions = fake_list_pending

    channel = tm.channels["telegram"]
    with pytest.raises(RuntimeError, match="unexpected task status after review"):
        await tm._await_review(
            task_id=tid,
            stage=sspec,
            idx=idx,
            scratch=scratch,
            channel=channel,
        )

    await db.close()


# ---------------------------------------------------------------------------
# Fix 5: path traversal test
# ---------------------------------------------------------------------------


async def test_await_review_artifact_path_traversal_raises_stage_failed(tmp_path):
    """_await_review should raise _StageFailed when artifact path escapes scratch dir."""
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    tid = await db.insert_task(Task(channel="telegram", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.RUNNING)

    tm = _make_task_manager(tmp_path, db)
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=tid)
    scratch.create()

    # Write manifest with a path traversal attempt
    scratch.write_deliverable(
        0, "pre-brief", "done", [{"path": "../../../etc/passwd", "kind": "text"}]
    )

    sspec, idx = _make_stage_spec(0)
    channel = tm.channels["telegram"]

    with pytest.raises(_StageFailed, match="escapes scratch dir"):
        await tm._await_review(
            task_id=tid,
            stage=sspec,
            idx=idx,
            scratch=scratch,
            channel=channel,
        )

    await db.close()


# ---------------------------------------------------------------------------
# Fix 6: retry on send_document failure
# ---------------------------------------------------------------------------

