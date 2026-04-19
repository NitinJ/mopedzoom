from pathlib import Path

import pytest

COMMANDS = Path(__file__).parent.parent / "commands"


@pytest.fixture
def init_md() -> Path:
    return COMMANDS / "init.md"


def test_init_command_exists(init_md):
    assert init_md.exists(), f"missing: {init_md}"


def test_init_has_frontmatter_description(init_md):
    text = init_md.read_text()
    assert text.startswith("---\n")
    head_end = text.find("\n---", 4)
    assert head_end > 0
    frontmatter = text[4:head_end]
    assert "description:" in frontmatter


def test_init_mentions_key_steps(init_md):
    text = init_md.read_text().lower()
    for needle in (
        "telegram",
        "bot token",
        "systemd",
        "config",
        "anthropic_api_key",
        "gh auth",
    ):
        assert needle in text, f"init.md missing: {needle}"


def test_shared_readme_exists():
    # accept either _shared.md or README.md
    candidates = [COMMANDS / "_shared.md", COMMANDS / "README.md"]
    assert any(c.exists() for c in candidates), "expected commands/_shared.md or commands/README.md"
