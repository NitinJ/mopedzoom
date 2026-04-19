"""Playbook loader + pydantic schema + deterministic trigger matcher."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class InputSpec(BaseModel):
    name: str
    required: bool = False
    prompt: str = ""


class StageSpec(BaseModel):
    name: str
    requires: str
    produces: str | list[str]
    approval: Literal["required", "on-completion", "on-failure", "none"] = "required"
    agent: str | None = None
    permission_mode: Literal["bypass", "ask", "allowlist"] | None = None
    timeout: str | None = None
    auto_advance_after: str | None = None


class Playbook(BaseModel):
    id: str
    summary: str
    triggers: list[str] = Field(default_factory=list)
    inputs: list[InputSpec] = Field(default_factory=list)
    requires_worktree: bool = False
    permission_mode: Literal["bypass", "ask", "allowlist"] = "bypass"
    stages: list[StageSpec]

    @classmethod
    def from_file(cls, path: Path) -> "Playbook":
        return cls.model_validate(yaml.safe_load(path.read_text()))


def load_playbooks(
    builtin_dir: Path | None, user_dir: Path | None
) -> dict[str, Playbook]:
    """Load playbooks from builtin_dir then user_dir; user entries override builtins."""
    reg: dict[str, Playbook] = {}
    for d in (builtin_dir, user_dir):
        if d is None or not d.exists():
            continue
        for f in sorted(d.glob("*.yaml")):
            pb = Playbook.from_file(f)
            reg[pb.id] = pb  # user_dir comes last, overrides built-ins
    return reg


def resolve_playbook(text: str, reg: dict[str, Playbook]) -> Playbook | None:
    """Deterministic first-pass matching: any playbook trigger appears in text."""
    text_l = text.lower()
    for pb in reg.values():
        if any(trig.lower() in text_l for trig in pb.triggers):
            return pb
    return None
