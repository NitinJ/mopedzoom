"""Stage runner: ``claude -p`` subprocess wrapper with transcript capture.

Spawns the Claude Code CLI in non-interactive mode for a single stage, captures
stdout to a transcript file, extracts the session-id, and reads the deliverable
manifest written by the agent into the scratch dir.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Callable

from .playbooks import StageSpec
from .scratch import ScratchDir

SESSION_RE = re.compile(r"session-id:\s*(\S+)")


class NoAgentsAvailable(Exception):
    """Raised when a stage has no agents and no fallback is allowed."""


@dataclass
class StageResult:
    exit_code: int
    session_id: str | None
    deliverable: dict | None
    transcript_path: str


class StageRunner:
    async def run(
        self,
        *,
        stage: StageSpec,
        stage_idx: int,
        agents: list[str],
        scratch: ScratchDir,
        cwd: str,
        prompt: str,
        resume_session_id: str | None = None,
        permission_mode: str = "bypass",
        timeout_s: float | None = None,
        permission_handler: Callable | None = None,
    ) -> StageResult:
        if permission_mode == "allowlist" and not agents and stage.agent is None:
            raise NoAgentsAvailable(
                f"stage {stage.name!r}: allowlist mode requires at least one agent"
            )
        scratch.create()
        transcript = scratch.transcript_path(stage_idx, stage.name)

        cmd: list[str] = ["claude", "-p"]
        if agents:
            cmd += ["--agents", ",".join(agents)]
        if resume_session_id:
            cmd += ["--resume", resume_session_id]
        if permission_mode == "bypass":
            cmd += ["--dangerously-skip-permissions"]
        cmd += [prompt]

        env = os.environ.copy()
        env["MOPEDZOOM_SCRATCH"] = str(scratch.dir)
        env["MOPEDZOOM_TASK_ID"] = str(scratch.task_id)
        env["MOPEDZOOM_STAGE"] = stage.name

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        session_id: str | None = None
        assert proc.stdout is not None
        with open(transcript, "wb") as f:
            async for line in proc.stdout:
                f.write(line)
                if session_id is None:
                    m = SESSION_RE.search(line.decode("utf-8", "ignore"))
                    if m:
                        session_id = m.group(1)

        # Optionally service permission.json requests from a background task.
        _perm_task: asyncio.Task | None = None
        if permission_handler is not None:
            async def _poll_permission():
                resp_path = scratch.dir / "permission_response.json"
                while True:
                    perm_path = scratch.dir / "permission.json"
                    if perm_path.exists():
                        try:
                            import json as _json
                            data = _json.loads(perm_path.read_text())
                        except Exception:
                            data = {}
                        result = await permission_handler(data)
                        try:
                            import json as _json2
                            resp_path.write_text(_json2.dumps(result))
                        except Exception:
                            pass
                    await asyncio.sleep(0.5)

            _perm_task = asyncio.create_task(_poll_permission())

        try:
            if timeout_s is not None:
                try:
                    rc = await asyncio.wait_for(proc.wait(), timeout=timeout_s)
                except asyncio.TimeoutError:
                    proc.terminate()
                    await asyncio.sleep(2)
                    proc.kill()
                    return StageResult(
                        exit_code=-1,
                        session_id=None,
                        deliverable=None,
                        transcript_path=str(transcript),
                    )
            else:
                rc = await proc.wait()
        finally:
            if _perm_task is not None:
                _perm_task.cancel()
                try:
                    await _perm_task
                except (asyncio.CancelledError, Exception):
                    pass

        deliverable = scratch.read_deliverable(stage_idx, stage.name)
        return StageResult(
            exit_code=rc,
            session_id=session_id,
            deliverable=deliverable,
            transcript_path=str(transcript),
        )
