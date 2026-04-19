"""Git worktree lifecycle manager with repo allowlist."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


class RepoNotAllowed(ValueError):
    pass


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", s.lower())[:32].strip("-") or "task"


class WorktreeManager:
    def __init__(self, worktrees_root: str, allowed_repos: dict[str, dict]):
        self.root = Path(worktrees_root)
        self.allowed = allowed_repos

    def create(self, task_id: int, repo_name: str, slug: str) -> tuple[str, str]:
        if repo_name not in self.allowed:
            raise RepoNotAllowed(repo_name)
        info = self.allowed[repo_name]
        repo_path = Path(info["path"]).expanduser()
        default_branch = info.get("default_branch", "main")
        target = self.root / repo_name / str(task_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        branch = f"mopedzoom/{task_id}-{_slug(slug)}"
        subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "worktree",
                "add",
                "-b",
                branch,
                str(target),
                default_branch,
            ],
            check=True,
        )
        return str(target), branch

    def destroy(
        self,
        task_id: int,
        repo_name: str,
        path: str,
        branch: str,
        delete_branch: bool = False,
    ) -> None:
        repo_path = Path(self.allowed[repo_name]["path"]).expanduser()
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "remove", "--force", path],
            check=False,
        )
        if delete_branch:
            subprocess.run(
                ["git", "-C", str(repo_path), "branch", "-D", branch],
                check=False,
            )
