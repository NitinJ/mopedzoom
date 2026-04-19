# tests/test_models.py
from datetime import datetime
from mopedzoomd.models import (
    Task,
    Stage,
    Interaction,
    Worktree,
    AgentPick,
    TaskEvent,
    TaskStatus,
    StageStatus,
)


def test_task_defaults():
    t = Task(channel="telegram", user_ref="chat:123", playbook_id="bug-fix", inputs={"repo": "x"})
    assert t.status == TaskStatus.QUEUED
    assert t.parent_task_id is None
    assert isinstance(t.created_at, datetime)


def test_stage_progression():
    s = Stage(task_id=1, idx=0, name="pre-design")
    assert s.status == StageStatus.PENDING
    s.status = StageStatus.RUNNING
    assert s.status == StageStatus.RUNNING


def test_worktree_state_values():
    from mopedzoomd.models import WorktreeState

    w = Worktree(task_id=1, repo="trialroomai", path="/tmp/x", branch="mopedzoom/1-abc")
    assert w.state == WorktreeState.ACTIVE


def test_interaction_defaults():
    from mopedzoomd.models import InteractionKind

    i = Interaction(task_id=1, stage_idx=0, kind=InteractionKind.APPROVAL, prompt="ok?")
    assert i.id is None
    assert i.posted_to_channel_ref is None
    assert isinstance(i.created_at, datetime)


def test_agent_pick_default_flag():
    p = AgentPick(task_id=1, stage_idx=0, agent_name="coder")
    assert p.from_transcript_parse is True


def test_task_event_defaults():
    e = TaskEvent(task_id=1, kind="queued", detail={})
    assert e.id is None
    assert isinstance(e.ts, datetime)
