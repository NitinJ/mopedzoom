"""Daemon: task manager + entry point."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Callable

from .channels.base import ApprovalButton, Channel, OutboundMessage
from .models import (
    Interaction,
    InteractionKind,
    Stage,
    StageStatus,
    Task,
    TaskEvent,
    TaskStatus,
)
from .playbooks import Playbook
from .scratch import ScratchDir
from .stage_runner import StageResult, StageRunner
from .state import StateDB


class _RetryStage(Exception):
    """Signal that the current stage should be re-run (e.g. after user answer)."""


@dataclass
class TaskManager:
    db: StateDB
    runs_root: str
    stage_runner: StageRunner
    playbook_registry: dict[str, Playbook]
    channels: dict[str, Channel]
    worktree_mgr: object | None
    agent_discoverer: Callable[[], list[str]]

    async def run_task(self, task_id: int) -> None:
        task = await self.db.get_task(task_id)
        pb = self.playbook_registry[task.playbook_id]
        scratch = ScratchDir(self.runs_root, task_id)
        scratch.create()
        scratch.task_json_path.write_text(
            json.dumps(
                {"id": task_id, "playbook": pb.id, "inputs": task.inputs},
                indent=2,
            )
        )

        existing = await self.db.get_stages(task_id)
        if not existing:
            for i, st in enumerate(pb.stages):
                await self.db.insert_stage(
                    Stage(task_id=task_id, idx=i, name=st.name)
                )

        await self.db.set_task_status(task_id, TaskStatus.RUNNING)
        await self.db.log_event(
            TaskEvent(task_id=task_id, kind="task_started", detail={})
        )

        channel = self.channels[task.channel]
        cwd = str(scratch.dir)  # TODO: worktree if pb.requires_worktree
        session_id: str | None = None

        for idx, sspec in enumerate(pb.stages):
            while True:
                try:
                    session_id = await self._run_stage(
                        task_id=task_id,
                        pb=pb,
                        sspec=sspec,
                        idx=idx,
                        task=task,
                        scratch=scratch,
                        channel=channel,
                        cwd=cwd,
                        session_id=session_id,
                    )
                    break
                except _RetryStage:
                    continue
                except _StageFailed:
                    return
                except RuntimeError as exc:
                    if str(exc) == "task cancelled by user":
                        return
                    raise

        await self.db.set_task_status(task_id, TaskStatus.DELIVERED)
        await self.db.log_event(
            TaskEvent(task_id=task_id, kind="task_delivered", detail={})
        )
        await channel.post(OutboundMessage(task_id=task_id, body="\U0001f680 delivered"))

    async def _run_stage(
        self,
        *,
        task_id: int,
        pb: Playbook,
        sspec,
        idx: int,
        task: Task,
        scratch: ScratchDir,
        channel: Channel,
        cwd: str,
        session_id: str | None,
    ) -> str | None:
        await self.db.update_stage(task_id, idx, status=StageStatus.RUNNING)
        await self.db.log_event(
            TaskEvent(
                task_id=task_id, kind="stage_started", detail={"stage": sspec.name}
            )
        )
        agents = self.agent_discoverer()
        if sspec.agent:
            agents = [sspec.agent]
        prompt = self._build_prompt(pb, sspec, task, scratch, idx)
        mode = sspec.permission_mode or pb.permission_mode
        result: StageResult = await self.stage_runner.run(
            stage=sspec,
            stage_idx=idx,
            agents=agents,
            scratch=scratch,
            cwd=cwd,
            prompt=prompt,
            resume_session_id=session_id,
            permission_mode=mode,
        )
        session_id = result.session_id or session_id

        # Mid-stage question/permission handling (F19).
        q = scratch.read_question()
        if q is not None:
            ref = await channel.post(
                OutboundMessage(
                    task_id=task_id,
                    body=f"\u2753 {sspec.name}: {q.get('prompt', '?')}",
                )
            )
            await self.db.insert_interaction(
                Interaction(
                    task_id=task_id,
                    stage_idx=idx,
                    kind=InteractionKind.QUESTION,
                    prompt=q.get("prompt", ""),
                    posted_to_channel_ref=ref,
                )
            )
            await self.db.set_task_status(task_id, TaskStatus.AWAITING_INPUT)
            await self.db.update_stage(task_id, idx, status=StageStatus.AWAITING_INPUT)
            while True:
                pend = await self.db.list_pending_interactions(task_id)
                if not pend:
                    break
                await asyncio.sleep(0.2)
            scratch.clear_question()
            raise _RetryStage()

        await self.db.update_stage(
            task_id,
            idx,
            status=StageStatus.DONE if result.exit_code == 0 else StageStatus.FAILED,
            session_id=session_id,
            transcript_path=result.transcript_path,
            deliverable_path=(
                str(scratch.deliverable_manifest_path(idx, sspec.name))
                if result.deliverable
                else None
            ),
        )
        if result.exit_code != 0 or not result.deliverable:
            await self.db.set_task_status(task_id, TaskStatus.FAILED)
            await channel.post(
                OutboundMessage(task_id=task_id, body=f"Stage {sspec.name} failed")
            )
            await self.db.log_event(
                TaskEvent(
                    task_id=task_id,
                    kind="stage_failed",
                    detail={"stage": sspec.name},
                )
            )
            raise _StageFailed()
        if sspec.approval in ("required", "on-completion"):
            await self._await_approval(task_id, idx, sspec, result, channel)
        await self.db.log_event(
            TaskEvent(task_id=task_id, kind="stage_done", detail={"stage": sspec.name})
        )
        return session_id

    def _build_prompt(self, pb, sspec, task, scratch: ScratchDir, idx: int) -> str:
        prior = ""
        for i in range(idx):
            mpath = scratch.deliverable_manifest_path(i, pb.stages[i].name)
            if mpath.exists():
                prior += f"\n- {mpath.name}"
        return (
            f"Task {task.id} ({pb.summary}).\n"
            f"Stage: {sspec.name}\n"
            f"Goal: {sspec.requires}\n"
            f"Produce: {sspec.produces}\n"
            f"Inputs: {json.dumps(task.inputs)}\n"
            f"Prior deliverables: {prior or 'none'}\n"
            f"Working dir: {scratch.dir}\n"
            f"To pause for user input, write {scratch.dir}/question.json and exit.\n"
        )

    async def _await_approval(
        self,
        task_id: int,
        idx: int,
        sspec,
        result: StageResult,
        channel: Channel,
    ) -> None:
        preview = ""
        if result.deliverable:
            preview = result.deliverable.get("notes", "")[:800]
        ref = await channel.post(
            OutboundMessage(
                task_id=task_id,
                body=f"\U0001f4dd {sspec.name} ready\n---\n{preview}",
                buttons=[
                    ApprovalButton("approve", "\u2705 Approve"),
                    ApprovalButton("revise", "\u270f\ufe0f Revise"),
                    ApprovalButton("cancel", "\u274c Cancel"),
                ],
            )
        )
        await self.db.insert_interaction(
            Interaction(
                task_id=task_id,
                stage_idx=idx,
                kind=InteractionKind.APPROVAL,
                prompt="approve this stage?",
                posted_to_channel_ref=ref,
            )
        )
        await self.db.set_task_status(task_id, TaskStatus.AWAITING_APPROVAL)
        while True:
            pend = await self.db.list_pending_interactions(task_id)
            if not pend:
                break
            await asyncio.sleep(0.2)
        t = await self.db.get_task(task_id)
        if t.status == TaskStatus.RUNNING:
            return
        if t.status == TaskStatus.CANCELLED:
            raise RuntimeError("task cancelled by user")


class _StageFailed(Exception):
    """Internal signal: stage failed, abort task."""


async def resolve_interaction(db: StateDB, *, task_id: int, answer: str) -> None:
    """Called by channels when the user clicks an approval button or sends a reply."""
    pend = await db.list_pending_interactions(task_id)
    if not pend:
        return
    i = pend[0]
    await db.resolve_interaction(i.id)
    if answer == "approve":
        await db.set_task_status(task_id, TaskStatus.RUNNING)
    elif answer == "cancel":
        await db.set_task_status(task_id, TaskStatus.CANCELLED)
    elif answer == "revise":
        await db.set_task_status(task_id, TaskStatus.AWAITING_INPUT)
    elif answer == "pause":
        await db.set_task_status(task_id, TaskStatus.PAUSED)
    elif answer == "resume":
        await db.set_task_status(task_id, TaskStatus.RUNNING)
    await db.log_event(
        TaskEvent(task_id=task_id, kind=f"resolved_{answer}", detail={})
    )
