from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    QUEUED = "queued"
    CLASSIFYING = "classifying"
    AWAITING_INPUT = "awaiting_input"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_INPUT = "awaiting_input"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorktreeState(str, Enum):
    ACTIVE = "active"
    GRACE = "grace"
    SWEPT = "swept"


class InteractionKind(str, Enum):
    APPROVAL = "approval"
    QUESTION = "question"
    INPUT = "input"
    PERMISSION = "permission"
    REVISION = "revision"


@dataclass
class Task:
    channel: str
    user_ref: str
    playbook_id: str
    inputs: dict[str, Any]
    id: int | None = None
    status: TaskStatus = TaskStatus.QUEUED
    parent_task_id: int | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Stage:
    task_id: int
    idx: int
    name: str
    status: StageStatus = StageStatus.PENDING
    session_id: str | None = None
    agent_used: str | None = None
    deliverable_path: str | None = None
    transcript_path: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


@dataclass
class Interaction:
    task_id: int
    stage_idx: int
    kind: InteractionKind
    prompt: str
    posted_to_channel_ref: str | None = None
    id: int | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Worktree:
    task_id: int
    repo: str
    path: str
    branch: str
    state: WorktreeState = WorktreeState.ACTIVE
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class AgentPick:
    task_id: int
    stage_idx: int
    agent_name: str
    from_transcript_parse: bool = True


@dataclass
class TaskEvent:
    task_id: int
    kind: str
    detail: dict[str, Any]
    id: int | None = None
    ts: datetime = field(default_factory=datetime.utcnow)
