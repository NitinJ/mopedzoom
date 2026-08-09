"""Batch 2 tests: timeout enforcement and research destination in prompt."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mopedzoomd.config import DeliverablesConfig, LimitsConfig
from mopedzoomd.daemon import TaskManager, _parse_duration
from mopedzoomd.models import Task
from mopedzoomd.playbooks import Playbook, StageSpec
from mopedzoomd.scratch import ScratchDir
from mopedzoomd.stage_runner import StageResult, StageRunner


# ---------------------------------------------------------------------------
# _parse_duration
# ---------------------------------------------------------------------------


def test_parse_duration_various():
    assert _parse_duration("30m") == 1800.0
    assert _parse_duration("1h") == 3600.0
    assert _parse_duration("90s") == 90.0
    assert _parse_duration("120") == 120.0
    assert _parse_duration(None) is None
    assert _parse_duration("") is None


# ---------------------------------------------------------------------------
# StageRunner enforces timeout
# ---------------------------------------------------------------------------


async def test_stage_runner_enforces_timeout(tmp_path):
    """Monkeypatched proc whose wait() hangs; runner returns exit_code=-1."""
    from mopedzoomd.scratch import ScratchDir
    from mopedzoomd.stage_runner import StageRunner, StageResult
    from mopedzoomd.playbooks import StageSpec

    scratch = ScratchDir(str(tmp_path), task_id=1)
    scratch.create()

    # Create a fake transcript file so open() in run() doesn't fail
    stage = StageSpec(name="impl", requires="do", produces="x.md", approval="none")
    transcript_path = scratch.transcript_path(0, "impl")
    transcript_path.write_bytes(b"")

    class FakeProc:
        stdout = None

        async def wait(self):
            # Hang forever
            await asyncio.sleep(9999)
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProc()

    async def fake_async_iter(self):
        return
        yield  # make it an async generator

    # Patch stdout to be an async iterable that yields nothing
    class FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    FakeProc.stdout = FakeStream()

    runner = StageRunner()
    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        result = await runner.run(
            stage=stage,
            stage_idx=0,
            agents=["coder"],
            scratch=scratch,
            cwd=str(tmp_path),
            prompt="do it",
            permission_mode="bypass",
            timeout_s=0.1,
        )

    assert result.exit_code == -1
    assert result.session_id is None
    assert result.deliverable is None


# ---------------------------------------------------------------------------
# _run_stage uses stage-level timeout when present
# ---------------------------------------------------------------------------


async def test_run_stage_respects_stagespec_timeout(tmp_path):
    """timeout_s computed from sspec.timeout is passed to runner.run."""
    from mopedzoomd.state import StateDB

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    seen_timeout = {}

    class Runner:
        async def run(self, *, stage, stage_idx, agents, scratch, cwd, prompt, timeout_s=None, **kw):
            seen_timeout["timeout_s"] = timeout_s
            scratch.write_deliverable(stage_idx, stage.name, "done", [], "ok")
            return StageResult(
                exit_code=0,
                session_id="s",
                deliverable=scratch.read_deliverable(stage_idx, stage.name),
                transcript_path="/t",
            )

    stage = StageSpec(name="impl", requires="r", produces="x.md", approval="none", timeout="30m")
    pb = Playbook(id="p", summary="s", triggers=["t"], stages=[stage])
    channel = AsyncMock()
    channel.post = AsyncMock(return_value="ref")

    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=Runner(),
        playbook_registry={"p": pb},
        channels={"cli": channel},
        worktree_mgr=None,
        agent_discoverer=lambda: ["coder"],
        limits=LimitsConfig(default_stage_timeout_s=300),
    )
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))
    await tm.run_task(tid)

    assert seen_timeout["timeout_s"] == 1800.0
    await db.close()


# ---------------------------------------------------------------------------
# _build_prompt publish stage with research repo configured
# ---------------------------------------------------------------------------


def test_build_prompt_publish_stage_includes_research_repo_when_configured(tmp_path):
    deliverables = DeliverablesConfig(research_repo="ml", research_path="docs/research")
    tm = TaskManager(
        db=None,
        runs_root=str(tmp_path),
        stage_runner=AsyncMock(),
        playbook_registry={},
        channels={},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
        deliverables=deliverables,
    )
    publish = StageSpec(name="publish", requires="commit report", produces="sha", approval="none")
    pb = Playbook(id="research", summary="s", triggers=["research"], stages=[publish])
    task = Task(id=1, channel="cli", user_ref="u", playbook_id="research", inputs={})
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=1)
    prompt = tm._build_prompt(pb, publish, task, scratch, 0)
    assert "ml" in prompt
    assert "docs/research" in prompt


def test_build_prompt_publish_stage_silent_when_unconfigured(tmp_path):
    tm = TaskManager(
        db=None,
        runs_root=str(tmp_path),
        stage_runner=AsyncMock(),
        playbook_registry={},
        channels={},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
        deliverables=None,
    )
    publish = StageSpec(name="publish", requires="commit report", produces="sha", approval="none")
    pb = Playbook(id="research", summary="s", triggers=["research"], stages=[publish])
    task = Task(id=1, channel="cli", user_ref="u", playbook_id="research", inputs={})
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=1)
    prompt = tm._build_prompt(pb, publish, task, scratch, 0)
    assert "ml" not in prompt
    assert "docs/research" not in prompt


def test_build_prompt_non_publish_stage_never_mentions_research_repo(tmp_path):
    deliverables = DeliverablesConfig(research_repo="ml", research_path="docs/research")
    tm = TaskManager(
        db=None,
        runs_root=str(tmp_path),
        stage_runner=AsyncMock(),
        playbook_registry={},
        channels={},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
        deliverables=deliverables,
    )
    pre_brief = StageSpec(name="pre-brief", requires="r", produces="x.md", approval="none")
    pb = Playbook(id="research", summary="s", triggers=["research"], stages=[pre_brief])
    task = Task(id=1, channel="cli", user_ref="u", playbook_id="research", inputs={})
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=1)
    prompt = tm._build_prompt(pb, pre_brief, task, scratch, 0)
    assert "ml" not in prompt
    assert "docs/research" not in prompt
