import pytest
import pytest_asyncio
from pathlib import Path
from httpx import AsyncClient, ASGITransport

from mopedzoomd.dashboard.app import create_app
from mopedzoomd.playbooks import Playbook, StageSpec
from mopedzoomd.state import StateDB


def _sample_playbook(pb_id: str = "research") -> Playbook:
    return Playbook(
        id=pb_id,
        summary="Research a topic",
        triggers=["research", "investigate"],
        stages=[
            StageSpec(name="pre-brief", requires="Scope the research", produces="pre-brief.md", approval="required"),
            StageSpec(name="research", requires="Do the research", produces="report.md", approval="on-completion"),
        ],
    )


@pytest_asyncio.fixture
async def editor_client(tmp_path):
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    reg = {"research": _sample_playbook()}
    app = create_app(db, playbook_registry=reg, user_playbooks_dir=tmp_path / "playbooks")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, reg, tmp_path
    await db.close()


@pytest.mark.asyncio
async def test_create_app_accepts_user_playbooks_dir(tmp_path):
    """create_app must accept user_playbooks_dir without error."""
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    app = create_app(db, user_playbooks_dir=tmp_path / "playbooks")
    assert app is not None
    await db.close()


@pytest.mark.asyncio
async def test_get_row_returns_200(editor_client):
    c, reg, _ = editor_client
    r = await c.get("/playbooks/research/row")
    assert r.status_code == 200
    assert "research" in r.text
    assert "Research a topic" in r.text


@pytest.mark.asyncio
async def test_get_row_unknown_returns_404(editor_client):
    c, _, _ = editor_client
    r = await c.get("/playbooks/nonexistent/row")
    assert r.status_code == 404
