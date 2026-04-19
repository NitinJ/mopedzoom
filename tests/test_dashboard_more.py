import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from mopedzoomd.dashboard.app import create_app
from mopedzoomd.playbooks import Playbook, StageSpec
from mopedzoomd.state import StateDB


@pytest_asyncio.fixture
async def client(tmp_path):
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    reg = {
        "pb": Playbook(
            id="pb",
            summary="s",
            triggers=["t"],
            stages=[StageSpec(name="x", requires="r", produces="x.md", approval="none")],
        )
    }
    app = create_app(
        db,
        playbook_registry=reg,
        agent_discoverer=lambda: ["coder", "reviewer"],
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await db.close()


@pytest.mark.asyncio
async def test_agents_view(client):
    r = await client.get("/agents")
    assert "coder" in r.text and "reviewer" in r.text


@pytest.mark.asyncio
async def test_playbooks_view(client):
    r = await client.get("/playbooks")
    assert "pb" in r.text


@pytest.mark.asyncio
async def test_tasks_fragment(client):
    r = await client.get("/fragments/tasks")
    assert r.status_code == 200
