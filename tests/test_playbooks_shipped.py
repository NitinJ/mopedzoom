"""Structural validation of shipped v1 playbook YAMLs.

C8 (src/mopedzoomd/playbooks.py with the real Playbook / PlaybookSpec pydantic
model) has not landed yet on main. To keep this lane (I27) unblocked and still
give real structural validation today, we mirror the C8 schema locally in this
test module.

Once C8 lands, lane L8b will revalidate these YAMLs against the real
`mopedzoomd.playbooks.Playbook` model and collapse this local mirror into a
thin import + smoke assertion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


# --- Local mirror of the C8 PlaybookSpec schema ---------------------------


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


# --- Fixtures -------------------------------------------------------------

ROOT = Path(__file__).parent.parent / "playbooks"


# --- Tests (match assertions from plan I27 Step 1) ------------------------


def test_research_valid():
    pb = Playbook.from_file(ROOT / "research.yaml")
    assert not pb.requires_worktree
    assert pb.stages[0].approval == "required"


def test_bug_fix_valid():
    pb = Playbook.from_file(ROOT / "bug-fix.yaml")
    assert pb.requires_worktree
    names = [s.name for s in pb.stages]
    assert names == ["pre-design", "implement", "open-pr"]


def test_feature_impl_has_five_stages():
    pb = Playbook.from_file(ROOT / "feature-impl.yaml")
    assert len(pb.stages) == 5


def test_bug_file_valid():
    pb = Playbook.from_file(ROOT / "bug-file.yaml")
    assert not pb.requires_worktree
    assert pb.stages[-1].name == "file"
