"""Daemon: task manager + entry point."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from .channels.base import ApprovalButton, Channel, OutboundMessage
from .config import DeliverablesConfig, LimitsConfig
from .models import (
    Interaction,
    InteractionKind,
    Stage,
    StageStatus,
    Task,
    TaskEvent,
    TaskStatus,
    Worktree,
    WorktreeState,
)
from .playbooks import Playbook
from .router import Router
from .scratch import ScratchDir
from .stage_runner import NoAgentsAvailable, StageResult, StageRunner
from .state import StateDB

LOG = logging.getLogger("mopedzoomd")


def _parse_duration(s: str | int | None) -> float | None:
    """Parse a human-readable duration string into seconds.

    Accepts: "30m", "1h", "90s", bare integer string, or None.
    Returns float seconds, or None when input is None/empty.
    """
    if s is None:
        return None
    s_str = str(s).strip()
    if not s_str:
        return None
    if s_str.endswith("h"):
        return float(s_str[:-1]) * 3600
    if s_str.endswith("m"):
        return float(s_str[:-1]) * 60
    if s_str.endswith("s"):
        return float(s_str[:-1])
    return float(s_str)


def _spawn_supervised(coro, *, name: str | None = None) -> asyncio.Task:
    """Wrap asyncio.create_task with a done-callback that LOGs any exception.

    Callers still get the Task object synchronously, preserving fire-and-forget
    semantics. Exceptions are no longer silently swallowed: they are surfaced
    via ``LOG.exception`` so the operator can see them.
    """
    task = asyncio.create_task(coro, name=name)

    def _on_done(t: asyncio.Task) -> None:
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            LOG.exception(
                "supervised task %r raised",
                t.get_name(),
                exc_info=exc,
            )

    task.add_done_callback(_on_done)
    return task


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
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    deliverables: DeliverablesConfig | None = None
    permissions_mcp_enabled: bool = False

    async def submit_task(
        self, *, channel: str, user_ref: str, text: str, playbook: Playbook
    ) -> int:
        task = Task(
            channel=channel,
            user_ref=user_ref,
            playbook_id=playbook.id,
            inputs={"request": text},
            status=TaskStatus.QUEUED,
        )
        task_id = await self.db.insert_task(task)
        await self.db.log_event(
            TaskEvent(task_id=task_id, kind="task_submitted", detail={"text": text[:200]})
        )
        _spawn_supervised(self.run_task(task_id), name=f"run_task:{task_id}")
        return task_id

    # ------------------------------------------------------------------
    # CLI op surface (wired by channels.cli_socket via build_cli_op_handler).
    # All methods return plain JSON-serializable dicts.
    # ------------------------------------------------------------------

    async def get_status(self, task_id: int) -> dict:
        t = await self.db.get_task(task_id)
        if t is None:
            return {"ok": False, "error": f"no such task: {task_id}"}
        stages = await self.db.get_stages(task_id)
        events = await self.db.list_events(task_id)
        last = events[-1] if events else None
        return {
            "ok": True,
            "id": t.id,
            "status": t.status.value,
            "playbook": t.playbook_id,
            "channel": t.channel,
            "stages": [
                {"idx": s.idx, "name": s.name, "status": s.status.value}
                for s in stages
            ],
            "last_event": (
                {"kind": last.kind, "detail": last.detail} if last else None
            ),
        }

    async def list_tasks(self, *, limit: int = 100, status: str | None = None) -> dict:
        filt: list[TaskStatus] | None = None
        if status:
            try:
                filt = [TaskStatus(status)]
            except ValueError:
                return {"ok": False, "error": f"unknown status: {status}"}
        rows = await self.db.list_tasks(statuses=filt, limit=limit)
        return {
            "ok": True,
            "tasks": [
                {
                    "id": t.id,
                    "status": t.status.value,
                    "playbook": t.playbook_id,
                    "channel": t.channel,
                }
                for t in rows
            ],
        }

    async def cancel(self, task_id: int) -> dict:
        t = await self.db.get_task(task_id)
        if t is None:
            return {"ok": False, "error": f"no such task: {task_id}"}
        await self.db.set_task_status(task_id, TaskStatus.CANCELLED)
        await self.db.log_event(
            TaskEvent(task_id=task_id, kind="task_cancelled", detail={})
        )
        return {"ok": True, "id": task_id, "status": TaskStatus.CANCELLED.value}

    async def resume(self, task_id: int) -> dict:
        t = await self.db.get_task(task_id)
        if t is None:
            return {"ok": False, "error": f"no such task: {task_id}"}
        if t.status != TaskStatus.PAUSED:
            return {
                "ok": False,
                "error": f"task not paused (status={t.status.value})",
            }
        await self.db.set_task_status(task_id, TaskStatus.RUNNING)
        await self.db.log_event(
            TaskEvent(task_id=task_id, kind="task_resumed", detail={})
        )
        _spawn_supervised(self.run_task(task_id), name=f"resume_task:{task_id}")
        return {"ok": True, "id": task_id, "status": TaskStatus.RUNNING.value}

    async def tail_logs(self, task_id: int, n: int = 50) -> dict:
        stages = await self.db.get_stages(task_id)
        # Pick the most-recent stage with a transcript path.
        for s in reversed(stages):
            if s.transcript_path:
                try:
                    lines = Path(s.transcript_path).read_text(errors="replace").splitlines()
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "error": f"read failed: {exc}"}
                return {
                    "ok": True,
                    "id": task_id,
                    "stage": s.name,
                    "lines": lines[-n:],
                }
        return {"ok": True, "id": task_id, "stage": None, "lines": []}

    async def edit_stage(self, task_id: int, idx: int, body: dict) -> dict:
        t = await self.db.get_task(task_id)
        if t is None:
            return {"ok": False, "error": f"no such task: {task_id}"}
        # Persist the revision marker into the scratch dir so the next run
        # can pick it up, and log an event for audit.
        scratch = ScratchDir(self.runs_root, task_id)
        scratch.create()
        (scratch.dir / f"revision-{idx}.json").write_text(json.dumps(body, indent=2))
        await self.db.log_event(
            TaskEvent(
                task_id=task_id,
                kind="stage_edited",
                detail={"idx": idx, "keys": sorted(body.keys())},
            )
        )
        return {"ok": True, "id": task_id, "idx": idx}

    def show_playbook(self, pb_id: str) -> dict:
        pb = self.playbook_registry.get(pb_id)
        if pb is None:
            return {"ok": False, "error": f"no such playbook: {pb_id}"}
        return {"ok": True, "playbook": pb.model_dump()}

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
                await self.db.insert_stage(Stage(task_id=task_id, idx=i, name=st.name))

        await self.db.set_task_status(task_id, TaskStatus.RUNNING)
        await self.db.log_event(TaskEvent(task_id=task_id, kind="task_started", detail={}))

        channel = self.channels[task.channel]
        cwd = str(scratch.dir)
        worktree_info: tuple[str, str, str] | None = None  # (repo, path, branch)
        if pb.requires_worktree and self.worktree_mgr is not None:
            repo = task.inputs.get("repo") if isinstance(task.inputs, dict) else None
            allowed = getattr(self.worktree_mgr, "allowed", {}) or {}
            if repo and repo in allowed:
                try:
                    wt_path, wt_branch = self.worktree_mgr.create(
                        task_id=task_id, repo_name=repo, slug=pb.id
                    )
                    cwd = wt_path
                    worktree_info = (repo, wt_path, wt_branch)
                    await self.db.insert_worktree(
                        Worktree(
                            task_id=task_id,
                            repo=repo,
                            path=wt_path,
                            branch=wt_branch,
                            state=WorktreeState.ACTIVE,
                        )
                    )
                    await self.db.log_event(
                        TaskEvent(
                            task_id=task_id,
                            kind="worktree_created",
                            detail={"repo": repo, "branch": wt_branch},
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    LOG.exception("worktree create failed for task %s: %s", task_id, exc)
                    # Fall back to scratch cwd (preserves behavior).
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
                    if worktree_info is not None:
                        await self.db.set_worktree_state(task_id, WorktreeState.GRACE)
                    return
                except RuntimeError as exc:
                    if str(exc) == "task cancelled by user":
                        if worktree_info is not None:
                            await self.db.set_worktree_state(task_id, WorktreeState.GRACE)
                        return
                    raise

        await self.db.set_task_status(task_id, TaskStatus.DELIVERED)
        await self.db.log_event(TaskEvent(task_id=task_id, kind="task_delivered", detail={}))
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
            TaskEvent(task_id=task_id, kind="stage_started", detail={"stage": sspec.name})
        )
        agents = self.agent_discoverer()
        if sspec.agent:
            agents = [sspec.agent]
        prompt = self._build_prompt(pb, sspec, task, scratch, idx)
        mode = sspec.permission_mode or pb.permission_mode
        stage_timeout = (
            _parse_duration(sspec.timeout)
            if hasattr(sspec, "timeout") and sspec.timeout
            else self.limits.default_stage_timeout_s
        )
        perm_handler = None
        if self.permissions_mcp_enabled and mode in ("ask", "allowlist"):
            perm_handler = handle_permission_request
        try:
            result: StageResult = await self.stage_runner.run(
                stage=sspec,
                stage_idx=idx,
                agents=agents,
                scratch=scratch,
                cwd=cwd,
                prompt=prompt,
                resume_session_id=session_id,
                permission_mode=mode,
                timeout_s=stage_timeout,
                permission_handler=perm_handler,
            )
        except NoAgentsAvailable as exc:
            await self.db.update_stage(task_id, idx, status=StageStatus.FAILED)
            await self.db.set_task_status(task_id, TaskStatus.FAILED)
            await self.db.log_event(
                TaskEvent(
                    task_id=task_id,
                    kind="stage_failed",
                    detail={"stage": sspec.name, "reason": "no_agents_available"},
                )
            )
            await channel.post(
                OutboundMessage(
                    task_id=task_id,
                    body=f"Stage {sspec.name} failed: no agents available",
                )
            )
            raise _StageFailed() from exc
        session_id = result.session_id or session_id

        # Mid-stage bridge file handling — drain all bridge files in one pass.
        bridges = _drain_scratch_bridges(scratch)
        q = bridges.get("question")

        # If approval.json was written to scratch, log it for audit (DB stays authoritative).
        approval_payload = bridges.get("approval")
        if approval_payload is not None:
            await self.db.log_event(
                TaskEvent(
                    task_id=task_id,
                    kind="scratch_approval_seen",
                    detail={"payload": approval_payload},
                )
            )
            scratch.clear_approval()

        if q is not None:
            # Support both {"prompt": "..."} and {"questions": [...], "context": "..."} formats.
            if "prompt" in q:
                question_text = q["prompt"]
            elif "questions" in q:
                lines = [q.get("context", "")]
                for item in q["questions"]:
                    lines.append(f"  • {item.get('text', str(item))}")
                question_text = "\n".join(filter(None, lines))
            else:
                question_text = str(q)
            ref = await channel.post(
                OutboundMessage(
                    task_id=task_id,
                    body=f"\u2753 {sspec.name}:\n{question_text}",
                )
            )
            await self.db.insert_interaction(
                Interaction(
                    task_id=task_id,
                    stage_idx=idx,
                    kind=InteractionKind.QUESTION,
                    prompt=question_text,
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
            await channel.post(OutboundMessage(task_id=task_id, body=f"Stage {sspec.name} failed"))
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
        manifest_path = scratch.deliverable_manifest_path(idx, sspec.name)
        produces = sspec.produces if isinstance(sspec.produces, list) else [sspec.produces]
        research_instruction = ""
        if (
            sspec.name == "publish"
            and self.deliverables is not None
            and self.deliverables.research_repo is not None
        ):
            repo = self.deliverables.research_repo
            path = self.deliverables.research_path or "docs/research"
            research_instruction = (
                f"\nCommit the report into repo `{repo}` at path `{path}`.\n"
            )
        return (
            f"Task {task.id} ({pb.summary}).\n"
            f"Stage: {sspec.name}\n"
            f"Goal: {sspec.requires}\n"
            f"Inputs: {json.dumps(task.inputs)}\n"
            f"Prior deliverables: {prior or 'none'}\n"
            f"Working dir: {scratch.dir}\n"
            f"\n"
            f"Produce the following artifact(s) in {scratch.dir}/: {', '.join(produces)}\n"
            f"\n"
            f"IMPORTANT: When done, write a deliverable manifest to:\n"
            f"  {manifest_path}\n"
            f"with this exact JSON structure:\n"
            f'{{"stage": "{sspec.name}", "status": "done", '
            f'"artifacts": [{{"path": "<relative-path>", "kind": "<kind>"}}], '
            f'"notes": "<one-line summary>"}}\n'
            f"\n"
            f"To pause for user input, write {scratch.dir}/question.json with the format "
            f'{{"prompt": "Your question here"}} and exit WITHOUT writing the deliverable manifest. '
            f"Writing question.json means the stage is NOT complete — do not write both.\n"
            f"{research_instruction}"
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


async def resolve_interaction(
    db: StateDB, *, task_id: int, answer: str, scratch: ScratchDir | None = None
) -> None:
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
    else:
        # Free-text: store as question answer or revision feedback
        if scratch is not None:
            if i.kind == InteractionKind.QUESTION:
                scratch.write_answer(i.stage_idx, answer)
            elif i.kind == InteractionKind.REVISION:
                scratch.append_feedback(i.stage_idx, answer)
        await db.set_task_status(task_id, TaskStatus.AWAITING_INPUT)
    await db.log_event(
        TaskEvent(
            task_id=task_id,
            kind="resolved_interaction",
            detail={"answer": answer[:100]},
        )
    )


# ---------------------------------------------------------------------------
# Daemon composition + entry point (F21)
# ---------------------------------------------------------------------------

import os  # noqa: E402
import signal  # noqa: E402

from .channels.cli_socket import CLISocketChannel  # noqa: E402
from .channels.telegram import TelegramChannel  # noqa: E402
from .config import Config, load_config  # noqa: E402
from .bridges import FILES as _BRIDGE_FILES  # noqa: E402
from .permission_mcp import handle_permission_request  # noqa: E402
from .playbooks import load_playbooks  # noqa: E402
from .sweeper import sweep_once  # noqa: E402
from .worktree import WorktreeManager  # noqa: E402


async def _sweeper_loop(db, worktree_mgr, grace_days: int, interval_s: int) -> None:
    """Background task that sweeps expired grace-period worktrees on a schedule."""
    while True:
        try:
            await sweep_once(db, worktree_mgr=worktree_mgr, grace_days=grace_days)
        except Exception:
            LOG.exception("sweeper error")
        await asyncio.sleep(interval_s)


def _drain_scratch_bridges(scratch: ScratchDir) -> dict:
    """Single-pass poll of the scratch dir for bridge files.

    Returns a dict with keys "question", "approval", "permission", each set to
    the parsed JSON payload if the file exists, or None otherwise.
    """
    result: dict = {}
    for kind, fname in _BRIDGE_FILES.items():
        p = scratch.dir / fname
        if p.exists():
            try:
                result[kind] = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                result[kind] = None
        else:
            result[kind] = None
    return result


@dataclass
class Daemon:
    cfg: Config
    db: StateDB
    task_mgr: TaskManager
    channels: dict[str, Channel]

    async def start(self) -> None:
        for c in self.channels.values():
            await c.start()

    async def stop(self) -> None:
        for c in self.channels.values():
            await c.stop()
        await self.db.close()


async def handle_inbound(
    msg,
    *,
    db: StateDB,
    router: Router,
    tm: TaskManager,
    channels: dict[str, Channel],
    registry: dict[str, Playbook],
) -> None:
    """Route an inbound channel message.

    Extracted from `build_daemon_from_config.on_inbound` closure so it's
    reachable from tests without spinning up a full daemon.
    """
    if msg.task_id:
        scratch = ScratchDir(tm.runs_root, msg.task_id)
        await resolve_interaction(db, task_id=msg.task_id, answer=msg.text, scratch=scratch)
        return
    if msg.reply_to_ref and msg.task_id is None:
        interaction = await db.get_interaction_by_ref(msg.reply_to_ref)
        if interaction is not None:
            scratch = ScratchDir(tm.runs_root, interaction.task_id)
            await resolve_interaction(
                db, task_id=interaction.task_id, answer=msg.text, scratch=scratch
            )
            return
    if not msg.text:
        return
    LOG.info("new submission: %s", msg.text[:120])
    pb = await router.pick(msg.text)
    if pb is None:
        LOG.warning("no playbook matched for: %s", msg.text[:80])
        await channels[msg.channel].post(
            OutboundMessage(
                task_id=0,
                body=f"\u26a0\ufe0f No matching playbook for your request. "
                f"Available: {', '.join(registry)}",
            )
        )
        return
    task_id = await tm.submit_task(
        channel=msg.channel,
        user_ref=msg.user_ref,
        text=msg.text,
        playbook=pb,
    )
    if msg.thread_id is not None:
        ch = channels[msg.channel]
        if hasattr(ch, "bind_task_topic"):
            ch.bind_task_topic(
                task_id=task_id,
                thread_id=msg.thread_id,
                playbook_id=pb.id,
                repo="",
            )
    await channels[msg.channel].post(
        OutboundMessage(
            task_id=task_id,
            body=f"\u2705 Task #{task_id} queued \u2014 *{pb.id}*: {pb.summary}",
        )
    )


def build_cli_op_handler(tm: TaskManager):
    """Build an op_handler dispatcher that forwards CLI ops to TaskManager methods.

    Returned coroutine expects ``(op: str, payload: dict) -> dict``.
    """

    async def _handler(op: str, payload: dict) -> dict:
        if op == "status":
            tid = payload.get("id")
            if tid is None:
                return {"ok": False, "error": "missing id"}
            return await tm.get_status(int(tid))
        if op == "tasks":
            limit = int(payload.get("limit", 100))
            status = payload.get("status")
            return await tm.list_tasks(limit=limit, status=status)
        if op == "cancel":
            tid = payload.get("id")
            if tid is None:
                return {"ok": False, "error": "missing id"}
            return await tm.cancel(int(tid))
        if op == "resume":
            tid = payload.get("id")
            if tid is None:
                return {"ok": False, "error": "missing id"}
            return await tm.resume(int(tid))
        if op == "logs":
            tid = payload.get("id")
            if tid is None:
                return {"ok": False, "error": "missing id"}
            n = int(payload.get("n", 50))
            return await tm.tail_logs(int(tid), n=n)
        if op == "edit":
            tid = payload.get("id")
            idx = payload.get("stage")
            body = payload.get("body", {})
            if tid is None or idx is None:
                return {"ok": False, "error": "missing id/stage"}
            return await tm.edit_stage(int(tid), int(idx), body if isinstance(body, dict) else {})
        if op == "show-playbook":
            pb_id = payload.get("playbook") or payload.get("id")
            if not pb_id:
                return {"ok": False, "error": "missing playbook"}
            return tm.show_playbook(str(pb_id))
        if op == "ui":
            # No-op from the CLI side; dashboard URL is the UI.
            return {"ok": True, "op": "ui"}
        return {"ok": False, "error": f"unknown op: {op}"}

    return _handler


async def build_daemon_from_config(cfg: Config, *, start: bool = True) -> Daemon:
    state_root = Path(os.environ.get("MOPEDZOOM_STATE", str(Path.home() / ".mopedzoom")))
    state_root.mkdir(parents=True, exist_ok=True)
    db = StateDB(str(state_root / "state.db"))
    await db.connect()
    await db.migrate()

    builtin = Path(__file__).parent.parent.parent / "playbooks"
    user = state_root / "playbooks"
    registry = load_playbooks(builtin_dir=builtin, user_dir=user)

    allowed = {k: v.model_dump() for k, v in cfg.repos.items()}
    wmgr = WorktreeManager(str(state_root / "worktrees"), allowed)

    channels: dict[str, Channel] = {
        "cli": CLISocketChannel(str(state_root / "socket")),
        "telegram": TelegramChannel(
            bot_token=cfg.channel.bot_token,
            chat_id=cfg.channel.chat_id,
            mode=cfg.channel.mode,
        ),
    }

    def discover_agents() -> list[str]:
        paths = [
            Path.home() / ".claude" / "plugins",
            Path.home() / ".claude" / "agents",
        ]
        found: list[str] = []
        for p in paths:
            if p.exists():
                for f in p.rglob("agents/*.md"):
                    found.append(f.stem)
                for f in p.rglob("*.md"):
                    if f.parent.name == "agents":
                        found.append(f.stem)
        return sorted(set(found))

    tm = TaskManager(
        db=db,
        runs_root=str(state_root / "runs"),
        stage_runner=StageRunner(),
        playbook_registry=registry,
        channels=channels,
        worktree_mgr=wmgr,
        agent_discoverer=discover_agents,
        limits=cfg.limits,
        deliverables=cfg.deliverables,
        permissions_mcp_enabled=cfg.permissions.mcp_enabled,
    )

    router = Router(registry=registry, claude_client=None)

    async def on_inbound(msg):
        await handle_inbound(
            msg, db=db, router=router, tm=tm, channels=channels, registry=registry
        )

    for c in channels.values():
        c.set_handler(on_inbound)

    # Wire CLI op dispatcher so non-submit ops actually hit TaskManager.
    cli_ch = channels.get("cli")
    if isinstance(cli_ch, CLISocketChannel):
        cli_ch.set_op_handler(build_cli_op_handler(tm))

    d = Daemon(cfg=cfg, db=db, task_mgr=tm, channels=channels)
    if start:
        await d.start()

    if cfg.limits.sweeper_enabled:
        _spawn_supervised(
            _sweeper_loop(
                db,
                wmgr,
                grace_days=cfg.limits.grace_period_days,
                interval_s=cfg.limits.sweeper_interval_s,
            ),
            name="sweeper",
        )

    return d


def main() -> None:
    import argparse

    import uvicorn

    from .dashboard.app import create_app

    parser = argparse.ArgumentParser(
        prog="mopedzoomd",
        description="Always-on Claude-powered task orchestrator daemon.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config.yaml (defaults to $MOPEDZOOM_STATE/config.yaml).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    state_root = Path(os.environ.get("MOPEDZOOM_STATE", str(Path.home() / ".mopedzoom")))
    cfg_path = Path(args.config) if args.config else state_root / "config.yaml"
    cfg = load_config(cfg_path)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    daemon = loop.run_until_complete(build_daemon_from_config(cfg, start=True))

    dash_cfg = cfg.dashboard
    fastapi_app = create_app(
        db=daemon.db,
        playbook_registry=daemon.task_mgr.playbook_registry,
        agent_discoverer=daemon.task_mgr.agent_discoverer,
        user_playbooks_dir=state_root / "playbooks",
    )
    uv_config = uvicorn.Config(
        fastapi_app,
        host="127.0.0.1",
        port=dash_cfg.port,
        loop="none",
        log_level="warning",
    )
    uv_server = uvicorn.Server(uv_config)

    stop = asyncio.Event()

    def handle_sig(*_):
        loop.call_soon_threadsafe(stop.set)

    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, handle_sig)

    async def run_all():
        await asyncio.gather(
            uv_server.serve(),
            stop.wait(),
        )
        uv_server.should_exit = True

    try:
        loop.run_until_complete(run_all())
    finally:
        loop.run_until_complete(daemon.stop())
        loop.close()
