from pathlib import Path

from mopedzoomd.playbooks import Playbook, load_playbooks, resolve_playbook

FIX = Path(__file__).parent / "fixtures" / "playbooks"


def test_playbook_validates():
    pb = Playbook.from_file(FIX / "sample.yaml")
    assert pb.id == "sample"
    assert pb.stages[0].approval == "required"
    assert pb.stages[1].approval == "none"


def test_load_playbooks_dedup(tmp_path):
    user_dir = tmp_path / "u"
    user_dir.mkdir()
    (user_dir / "sample.yaml").write_text(
        (FIX / "sample.yaml").read_text().replace("Sample playbook for tests", "User override")
    )
    reg = load_playbooks(builtin_dir=FIX, user_dir=user_dir)
    assert reg["sample"].summary == "User override"
    assert len(reg) == 1


def test_resolve_by_trigger():
    reg = load_playbooks(builtin_dir=FIX, user_dir=None)
    match = resolve_playbook("please run a sample task", reg)
    assert match is not None and match.id == "sample"
