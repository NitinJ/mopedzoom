from pathlib import Path

import pytest

COMMANDS = Path(__file__).parent.parent / "commands"

TOP_LEVEL = [
    "config",
    "submit",
    "tasks",
    "status",
    "cancel",
    "resume",
    "edit",
    "logs",
    "ui",
]
PLAYBOOK = ["new", "edit", "list", "delete"]


@pytest.mark.parametrize("name", TOP_LEVEL)
def test_top_level_command_exists(name):
    p = COMMANDS / f"{name}.md"
    assert p.exists(), f"missing: {p}"


@pytest.mark.parametrize("name", PLAYBOOK)
def test_playbook_subcommand_exists(name):
    p = COMMANDS / "playbook" / f"{name}.md"
    assert p.exists(), f"missing: {p}"


@pytest.mark.parametrize("name", TOP_LEVEL)
def test_top_level_has_frontmatter(name):
    text = (COMMANDS / f"{name}.md").read_text()
    assert text.startswith("---\n")
    head_end = text.find("\n---", 4)
    assert head_end > 0, f"{name}.md: missing closing ---"
    assert "description:" in text[4:head_end]


@pytest.mark.parametrize("name", PLAYBOOK)
def test_playbook_has_frontmatter(name):
    text = (COMMANDS / "playbook" / f"{name}.md").read_text()
    assert text.startswith("---\n")
    head_end = text.find("\n---", 4)
    assert head_end > 0
    assert "description:" in text[4:head_end]


def test_submit_mentions_cli():
    text = (COMMANDS / "submit.md").read_text()
    assert "mopedzoom submit" in text


def test_tasks_mentions_cli():
    text = (COMMANDS / "tasks.md").read_text()
    assert "mopedzoom tasks" in text


def test_ui_mentions_dashboard():
    text = (COMMANDS / "ui.md").read_text().lower()
    assert "127.0.0.1" in text or "dashboard" in text


def test_playbook_new_creates_yaml():
    text = (COMMANDS / "playbook" / "new.md").read_text().lower()
    assert "yaml" in text
