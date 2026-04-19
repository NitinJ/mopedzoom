# src/mopedzoomd/scratch.py
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

class ScratchDir:
    def __init__(self, runs_root: str, task_id: int):
        self.runs_root = Path(runs_root)
        self.task_id = task_id
        self.dir = self.runs_root / str(task_id)

    @property
    def task_json_path(self) -> Path:
        return self.dir / "task.json"

    def create(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)

    def transcript_path(self, idx: int, name: str) -> Path:
        return self.dir / f"{idx}-{name}.transcript"

    def deliverable_manifest_path(self, idx: int, name: str) -> Path:
        return self.dir / f"{idx}-{name}.deliverable.json"

    def write_deliverable(self, stage_idx: int, stage_name: str,
                          status: str, artifacts: list[dict[str, Any]],
                          notes: str = "") -> None:
        p = self.deliverable_manifest_path(stage_idx, stage_name)
        p.write_text(json.dumps({
            "stage": stage_name,
            "status": status,
            "artifacts": artifacts,
            "notes": notes,
        }, indent=2))

    def read_deliverable(self, stage_idx: int, stage_name: str) -> dict[str, Any] | None:
        p = self.deliverable_manifest_path(stage_idx, stage_name)
        if not p.exists():
            return None
        return json.loads(p.read_text())

    def read_question(self) -> dict[str, Any] | None:
        p = self.dir / "question.json"
        return json.loads(p.read_text()) if p.exists() else None

    def clear_question(self) -> None:
        p = self.dir / "question.json"
        if p.exists():
            p.unlink()

    def read_approval(self) -> dict[str, Any] | None:
        p = self.dir / "approval.json"
        return json.loads(p.read_text()) if p.exists() else None

    def clear_approval(self) -> None:
        p = self.dir / "approval.json"
        if p.exists():
            p.unlink()

    def read_permission(self) -> dict[str, Any] | None:
        p = self.dir / "permission.json"
        return json.loads(p.read_text()) if p.exists() else None

    def clear_permission(self) -> None:
        p = self.dir / "permission.json"
        if p.exists():
            p.unlink()
