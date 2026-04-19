"""Structural validation of shipped v1 playbook YAMLs.

Validates each shipped playbook YAML against the real
`mopedzoomd.playbooks.Playbook` pydantic schema (C8).
"""

from __future__ import annotations

from pathlib import Path

from mopedzoomd.playbooks import Playbook

ROOT = Path(__file__).parent.parent / "playbooks"


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
