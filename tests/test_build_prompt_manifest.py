"""Regression: _build_prompt must instruct the subprocess to write a manifest.

Before the fix, agents produced the artifact file but not the deliverable
manifest JSON, so scratch.read_deliverable() returned None and every stage was
marked failed. Pin the prompt contract.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from mopedzoomd.daemon import TaskManager
from mopedzoomd.models import Task
from mopedzoomd.playbooks import Playbook, StageSpec
from mopedzoomd.scratch import ScratchDir


def _tm(tmp_path):
    return TaskManager(
        db=None,
        runs_root=str(tmp_path / "runs"),
        stage_runner=AsyncMock(),
        playbook_registry={},
        channels={},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )


def test_build_prompt_includes_manifest_path_and_schema(tmp_path):
    tm = _tm(tmp_path)
    pb = Playbook(
        id="research",
        summary="Research a topic",
        triggers=["research"],
        stages=[
            StageSpec(name="pre-brief", requires="scope", produces="pre-brief.md", approval="none"),
            StageSpec(name="research", requires="do it", produces="report.md", approval="none"),
        ],
    )
    task = Task(id=7, channel="cli", user_ref="u", playbook_id="research", inputs={"topic": "x"})
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=7)
    idx = 1  # the "research" stage
    sspec = pb.stages[idx]

    prompt = tm._build_prompt(pb, sspec, task, scratch, idx)

    # (a) The exact deliverable manifest path must appear verbatim.
    expected_path = str(scratch.deliverable_manifest_path(idx, sspec.name))
    assert expected_path in prompt

    # (b) Every schema key the reader expects must be named in the prompt.
    for key in ('"stage"', '"status"', '"artifacts"', '"notes"'):
        assert key in prompt, f"prompt missing JSON key {key}"

    # Stage name and "done" literal status appear in the skeleton.
    assert '"stage": "research"' in prompt
    assert '"status": "done"' in prompt


def test_build_prompt_lists_every_produces_entry(tmp_path):
    tm = _tm(tmp_path)
    # ``produces`` may be a list per playbooks.StageSpec.
    stage = StageSpec(
        name="impl",
        requires="build",
        produces=["commits", "pr_url", "summary.md"],
        approval="none",
    )
    pb = Playbook(id="p", summary="s", triggers=["t"], stages=[stage])
    task = Task(id=1, channel="cli", user_ref="u", playbook_id="p", inputs={})
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=1)

    prompt = tm._build_prompt(pb, stage, task, scratch, 0)

    for produced in ("commits", "pr_url", "summary.md"):
        assert produced in prompt, f"produces entry {produced!r} missing from prompt"


def test_build_prompt_single_string_produces_appears(tmp_path):
    tm = _tm(tmp_path)
    stage = StageSpec(name="s", requires="r", produces="report.md", approval="none")
    pb = Playbook(id="p", summary="s", triggers=["t"], stages=[stage])
    task = Task(id=1, channel="cli", user_ref="u", playbook_id="p", inputs={})
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=1)
    prompt = tm._build_prompt(pb, stage, task, scratch, 0)
    assert "report.md" in prompt


def test_build_prompt_injects_answer(tmp_path):
    tm = _tm(tmp_path)
    stage = StageSpec(name="research", requires="do it", produces="report.md", approval="none")
    pb = Playbook(id="p", summary="s", triggers=["t"], stages=[stage])
    task = Task(id=1, channel="cli", user_ref="u", playbook_id="p", inputs={})
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=1)
    scratch.create()
    scratch.write_answer(0, "e, all-india, personal curiosity, 1500 words, evergreen")

    prompt = tm._build_prompt(pb, stage, task, scratch, 0)

    assert 'User answered your questions: "e, all-india, personal curiosity, 1500 words, evergreen"' in prompt


def test_build_prompt_injects_feedback(tmp_path):
    tm = _tm(tmp_path)
    stage = StageSpec(name="research", requires="do it", produces="report.md", approval="none")
    pb = Playbook(id="p", summary="s", triggers=["t"], stages=[stage])
    task = Task(id=1, channel="cli", user_ref="u", playbook_id="p", inputs={})
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=1)
    scratch.create()
    scratch.append_feedback(0, "Focus more on South India")
    scratch.append_feedback(0, "Cut the genetics section")

    prompt = tm._build_prompt(pb, stage, task, scratch, 0)

    assert 'Iteration 1: "Focus more on South India"' in prompt
    assert 'Iteration 2: "Cut the genetics section"' in prompt


def test_build_prompt_no_feedback_no_injection(tmp_path):
    tm = _tm(tmp_path)
    stage = StageSpec(name="research", requires="do it", produces="report.md", approval="none")
    pb = Playbook(id="p", summary="s", triggers=["t"], stages=[stage])
    task = Task(id=1, channel="cli", user_ref="u", playbook_id="p", inputs={})
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=1)
    scratch.create()

    prompt = tm._build_prompt(pb, stage, task, scratch, 0)

    assert "User answered" not in prompt
    assert "User feedback" not in prompt
