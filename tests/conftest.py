# tests/conftest.py
import os
import tempfile
from pathlib import Path
import pytest

@pytest.fixture
def tmp_state(monkeypatch):
    """Redirect ~/.mopedzoom/ to a temp dir for the duration of the test."""
    with tempfile.TemporaryDirectory() as d:
        state = Path(d)
        (state / "runs").mkdir()
        (state / "worktrees").mkdir()
        (state / "playbooks").mkdir()
        (state / "logs").mkdir()
        monkeypatch.setenv("MOPEDZOOM_STATE", str(state))
        yield state
