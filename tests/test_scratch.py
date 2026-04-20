# tests/test_scratch.py
import json

from mopedzoomd.scratch import ScratchDir


def test_create_and_paths(tmp_path):
    s = ScratchDir(str(tmp_path), task_id=5)
    s.create()
    assert (tmp_path / "5").is_dir()
    assert s.task_json_path.parent == tmp_path / "5"


def test_deliverable_manifest_roundtrip(tmp_path):
    s = ScratchDir(str(tmp_path), task_id=1)
    s.create()
    s.write_deliverable(
        stage_idx=0,
        stage_name="pre",
        status="ok",
        artifacts=[{"type": "markdown", "path": "0-pre.md", "role": "primary"}],
        notes="found root cause",
    )
    m = s.read_deliverable(stage_idx=0, stage_name="pre")
    assert m["stage"] == "pre"
    assert m["artifacts"][0]["path"] == "0-pre.md"


def test_question_file_helpers(tmp_path):
    s = ScratchDir(str(tmp_path), task_id=2)
    s.create()
    assert s.read_question() is None
    (s.dir / "question.json").write_text(json.dumps({"stage": "impl", "prompt": "X?"}))
    q = s.read_question()
    assert q["prompt"] == "X?"
    s.clear_question()
    assert s.read_question() is None


def test_feedback_append_and_read(tmp_path):
    s = ScratchDir(str(tmp_path), task_id=1)
    s.create()
    assert s.read_feedback(0) == []
    s.append_feedback(0, "more detail please")
    s.append_feedback(0, "cut genetics section")
    assert s.read_feedback(0) == ["more detail please", "cut genetics section"]


def test_feedback_persists_across_instances(tmp_path):
    s1 = ScratchDir(str(tmp_path), task_id=1)
    s1.create()
    s1.append_feedback(0, "iteration one")
    s2 = ScratchDir(str(tmp_path), task_id=1)
    assert s2.read_feedback(0) == ["iteration one"]


def test_answer_write_and_read(tmp_path):
    s = ScratchDir(str(tmp_path), task_id=1)
    s.create()
    assert s.read_answer(0) is None
    s.write_answer(0, "south india focus, 1500 words")
    assert s.read_answer(0) == "south india focus, 1500 words"


def test_answer_overwrite(tmp_path):
    s = ScratchDir(str(tmp_path), task_id=1)
    s.create()
    s.write_answer(0, "first")
    s.write_answer(0, "second")
    assert s.read_answer(0) == "second"
