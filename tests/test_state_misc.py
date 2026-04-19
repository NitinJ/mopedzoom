import pytest

from mopedzoomd.models import (
    AgentPick,
    Interaction,
    InteractionKind,
    Task,
    TaskEvent,
    Worktree,
    WorktreeState,
)
from mopedzoomd.state import StateDB


@pytest.fixture
async def db(tmp_path):
    d = StateDB(str(tmp_path / "s.db"))
    await d.connect()
    await d.migrate()
    tid = await d.insert_task(Task(channel="cli", user_ref="u", playbook_id="r", inputs={}))
    yield d, tid
    await d.close()


async def test_interactions_roundtrip(db):
    d, tid = db
    iid = await d.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.APPROVAL,
            prompt="approve?",
            posted_to_channel_ref="tg:42",
        )
    )
    pend = await d.list_pending_interactions(tid)
    assert len(pend) == 1 and pend[0].prompt == "approve?"
    await d.resolve_interaction(iid)
    assert len(await d.list_pending_interactions(tid)) == 0


async def test_worktrees(db):
    d, tid = db
    await d.insert_worktree(Worktree(task_id=tid, repo="x", path="/t/x", branch="b"))
    w = await d.get_worktree(tid)
    assert w.state == WorktreeState.ACTIVE
    await d.set_worktree_state(tid, WorktreeState.GRACE)
    assert (await d.get_worktree(tid)).state == WorktreeState.GRACE


async def test_agent_picks(db):
    d, tid = db
    await d.record_agent_pick(AgentPick(task_id=tid, stage_idx=0, agent_name="coder"))
    picks = await d.list_agent_picks(tid)
    assert picks[0].agent_name == "coder"


async def test_task_events(db):
    d, tid = db
    await d.log_event(TaskEvent(task_id=tid, kind="queued", detail={"note": "hi"}))
    evs = await d.list_events(tid)
    assert evs[0].kind == "queued" and evs[0].detail == {"note": "hi"}
