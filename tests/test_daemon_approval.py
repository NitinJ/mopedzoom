from mopedzoomd.daemon import resolve_interaction
from mopedzoomd.state import StateDB
from mopedzoomd.models import Interaction, InteractionKind, Task, TaskStatus


async def test_resolve_approve_sets_running(tmp_path):
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
    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="p", inputs={})
    )
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
    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="p", inputs={})
    )
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
    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="p", inputs={})
    )
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
