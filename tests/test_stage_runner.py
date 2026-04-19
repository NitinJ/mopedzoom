import os
import stat

import pytest

from mopedzoomd.playbooks import StageSpec
from mopedzoomd.scratch import ScratchDir
from mopedzoomd.stage_runner import StageResult, StageRunner

FAKE_CLAUDE = """#!/usr/bin/env bash
# Echo session-id line + write a trivial deliverable
echo "session-id: sess-1234"
echo "hello from fake claude"
mkdir -p "$MOPEDZOOM_SCRATCH"
cat > "$MOPEDZOOM_SCRATCH/0-pre.deliverable.json" <<EOF
{"stage":"pre","status":"ok","artifacts":[{"type":"markdown","path":"0-pre.md","role":"primary"}],"notes":"n"}
EOF
echo "body" > "$MOPEDZOOM_SCRATCH/0-pre.md"
"""


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    binp = tmp_path / "claude"
    binp.write_text(FAKE_CLAUDE)
    binp.chmod(binp.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    return binp


async def test_runner_spawns_and_captures(fake_claude, tmp_path):
    scratch = ScratchDir(str(tmp_path), task_id=1)
    scratch.create()
    stage = StageSpec(name="pre", requires="do", produces="0-pre.md", approval="none")
    runner = StageRunner()
    result = await runner.run(
        stage=stage,
        stage_idx=0,
        agents=["coder"],
        scratch=scratch,
        cwd=str(tmp_path),
        prompt="do stuff",
    )
    assert isinstance(result, StageResult)
    assert result.exit_code == 0
    assert result.session_id == "sess-1234"
    assert result.deliverable is not None
    assert result.deliverable["stage"] == "pre"
