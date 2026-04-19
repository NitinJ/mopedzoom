import pytest

from mopedzoomd.state import StateDB


@pytest.fixture
async def db(tmp_path):
    d = StateDB(str(tmp_path / "state.db"))
    await d.connect()
    await d.migrate()
    yield d
    await d.close()


async def test_schema_creates_all_tables(db):
    tables = await db.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r["name"] for r in tables}
    assert {
        "tasks",
        "stages",
        "pending_interactions",
        "worktrees",
        "agent_picks",
        "task_events",
    } <= names


async def test_migration_is_idempotent(db):
    await db.migrate()
    await db.migrate()
