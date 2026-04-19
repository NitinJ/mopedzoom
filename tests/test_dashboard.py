import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from mopedzoomd.dashboard.app import create_app
from mopedzoomd.models import Task
from mopedzoomd.state import StateDB


@pytest_asyncio.fixture
async def client(tmp_path):
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="bug-fix", inputs={})
    )
    app = create_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c, db
    await db.close()


@pytest.mark.asyncio
async def test_index_lists_tasks(client):
    c, _ = client
    r = await c.get("/")
    assert r.status_code == 200
    assert "bug-fix" in r.text


@pytest.mark.asyncio
async def test_task_detail_page(client):
    c, _ = client
    r = await c.get("/tasks/1")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_health_json(client):
    c, _ = client
    r = await c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
