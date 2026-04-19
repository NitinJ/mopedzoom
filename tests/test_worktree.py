import subprocess
from pathlib import Path

import pytest

from mopedzoomd.worktree import RepoNotAllowed, WorktreeManager


@pytest.fixture
def origin(tmp_path):
    """Create a bare-ish repo to branch from."""
    repo = tmp_path / "origin"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    (repo / "f.txt").write_text("hi")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=x@x",
            "-c",
            "user.name=x",
            "commit",
            "-m",
            "init",
        ],
        cwd=repo,
        check=True,
    )
    return repo


def test_create_worktree_happy_path(tmp_path, origin):
    wt_root = tmp_path / "worktrees"
    allowed = {"demo": {"path": str(origin), "default_branch": "main"}}
    mgr = WorktreeManager(str(wt_root), allowed)
    path, branch = mgr.create(task_id=7, repo_name="demo", slug="fix")
    assert (Path(path) / "f.txt").exists()
    assert branch.startswith("mopedzoom/7-")


def test_rejects_unallowed_repo(tmp_path):
    mgr = WorktreeManager(str(tmp_path), {})
    with pytest.raises(RepoNotAllowed):
        mgr.create(task_id=1, repo_name="nope", slug="x")


def test_destroy_removes_worktree(tmp_path, origin):
    mgr = WorktreeManager(
        str(tmp_path / "wt"),
        {"demo": {"path": str(origin), "default_branch": "main"}},
    )
    path, branch = mgr.create(task_id=9, repo_name="demo", slug="y")
    mgr.destroy(
        task_id=9,
        repo_name="demo",
        path=path,
        branch=branch,
        delete_branch=True,
    )
    assert not Path(path).exists()
