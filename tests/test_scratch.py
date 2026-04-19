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
