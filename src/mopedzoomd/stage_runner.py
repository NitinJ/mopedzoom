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

from .playbooks import StageSpec
from .scratch import ScratchDir

SESSION_RE = re.compile(r"session-id:\s*(\S+)")


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
    ) -> StageResult:
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
        rc = await proc.wait()

        deliverable = scratch.read_deliverable(stage_idx, stage.name)
        return StageResult(
            exit_code=rc,
            session_id=session_id,
            deliverable=deliverable,
            transcript_path=str(transcript),
        )
