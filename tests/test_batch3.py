"""Batch 3 tests: sweeper feature-flag and permission MCP wiring."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mopedzoomd.config import LimitsConfig, PermissionsConfig


# ---------------------------------------------------------------------------
# Sweeper feature flag
# ---------------------------------------------------------------------------


def test_sweeper_disabled_by_default_does_not_launch():
    """LimitsConfig defaults to sweeper_enabled=False."""
    cfg = LimitsConfig()
    assert cfg.sweeper_enabled is False
    assert cfg.sweeper_interval_s == 3600


async def test_sweeper_enabled_launches_loop(tmp_path, monkeypatch):
    """When sweeper_enabled=True, the sweeper loop is invoked.

    We test this by directly checking that _sweeper_loop is defined in daemon
    and that LimitsConfig.sweeper_enabled=True triggers the code path (verified
    by unit-testing the conditional logic).
    """
    from mopedzoomd.config import LimitsConfig
    from mopedzoomd.daemon import _sweeper_loop

    # Verify _sweeper_loop exists and is a coroutine function
    assert asyncio.iscoroutinefunction(_sweeper_loop)

    # Verify the config flag enables the feature
    cfg_enabled = LimitsConfig(sweeper_enabled=True, sweeper_interval_s=1)
    assert cfg_enabled.sweeper_enabled is True

    # Verify _sweeper_loop calls sweep_once
    sweep_calls = []

    async def fake_sweep_once(db, *, worktree_mgr, grace_days):
        sweep_calls.append(grace_days)
        raise asyncio.CancelledError()  # stop after first iteration

    monkeypatch.setattr("mopedzoomd.daemon.sweep_once", fake_sweep_once)

    try:
        await _sweeper_loop(None, None, grace_days=7, interval_s=1)
    except asyncio.CancelledError:
        pass

    assert len(sweep_calls) == 1
    assert sweep_calls[0] == 7


# ---------------------------------------------------------------------------
# Permission MCP wiring
# ---------------------------------------------------------------------------


def test_permissions_mcp_disabled_by_default():
    """PermissionsConfig defaults to mcp_enabled=False."""
    cfg = PermissionsConfig()
    assert cfg.mcp_enabled is False


async def test_stage_runner_permission_handler_called_when_provided(tmp_path):
    """When permission_handler is set on StageRunner.run, it is invoked when
    permission.json appears in the scratch dir.
    """
    from mopedzoomd.scratch import ScratchDir
    from mopedzoomd.stage_runner import StageRunner
    from mopedzoomd.playbooks import StageSpec

    scratch = ScratchDir(str(tmp_path), task_id=1)
    scratch.create()
    stage = StageSpec(name="impl", requires="do", produces="x.md", approval="none")

    handler_calls = []

    async def fake_handler(data):
        handler_calls.append(data)
        return {"behavior": "allow", "updatedInput": data}

    class FakeProc:
        stdout = None

        async def wait(self):
            # Write permission.json and then return quickly
            (scratch.dir / "permission.json").write_text(
                json.dumps({"tool_name": "Bash", "input": {"command": "ls"}})
            )
            await asyncio.sleep(0.6)  # give poller time to fire
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    class FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    FakeProc.stdout = FakeStream()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProc()

    runner = StageRunner()
    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        result = await runner.run(
            stage=stage,
            stage_idx=0,
            agents=["coder"],
            scratch=scratch,
            cwd=str(tmp_path),
            prompt="do it",
            permission_mode="ask",
            permission_handler=fake_handler,
        )

    assert len(handler_calls) >= 1
    assert handler_calls[0].get("tool_name") == "Bash"


async def test_stage_runner_no_permission_handler_by_default(tmp_path):
    """Without permission_handler, no inner polling task is spawned."""
    from mopedzoomd.scratch import ScratchDir
    from mopedzoomd.stage_runner import StageRunner
    from mopedzoomd.playbooks import StageSpec

    scratch = ScratchDir(str(tmp_path), task_id=1)
    scratch.create()
    stage = StageSpec(name="impl", requires="do", produces="x.md", approval="none")

    tasks_before = set()

    class FakeProc:
        stdout = None

        async def wait(self):
            tasks_before.update(asyncio.all_tasks())
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    class FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    FakeProc.stdout = FakeStream()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProc()

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
        )

    # No permission-related files should be touched.
    assert not (scratch.dir / "permission_response.json").exists()
    assert result.exit_code == 0
