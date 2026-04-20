"""Integration test fixtures: fake `claude` binary on PATH + real temp StateDB."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

FAKE_CLAUDE = """#!/usr/bin/env bash
echo "session-id: sess-e2e"
stage="$MOPEDZOOM_STAGE"
cat > "$MOPEDZOOM_SCRATCH/0-pre-brief.deliverable.json" <<EOF
{"stage":"pre-brief","status":"ok","artifacts":[{"type":"markdown","path":"x","role":"primary"}],"notes":"done"}
EOF
cat > "$MOPEDZOOM_SCRATCH/1-research.deliverable.json" <<EOF
{"stage":"research","status":"ok","artifacts":[{"type":"markdown","path":"x","role":"primary"}],"notes":"done"}
EOF
cat > "$MOPEDZOOM_SCRATCH/2-publish.deliverable.json" <<EOF
{"stage":"publish","status":"ok","artifacts":[],"notes":"done"}
EOF
"""


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """Install a fake `claude` executable on PATH that writes canned deliverables."""
    p = tmp_path / "claude"
    p.write_text(FAKE_CLAUDE)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    return p


@pytest.fixture
def fake_claude_variant(tmp_path, monkeypatch):
    """Factory: install a custom bash script as the `claude` binary on PATH."""
    def _make(script_body: str) -> Path:
        p = tmp_path / "claude"
        p.write_text("#!/usr/bin/env bash\n" + script_body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
        return p
    return _make
