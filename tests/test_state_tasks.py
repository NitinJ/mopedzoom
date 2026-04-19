import pytest

from mopedzoomd.models import Stage, StageStatus, Task, TaskStatus
from mopedzoomd.state import StateDB


@pytest.fixture
async def db(tmp_path):
    d = StateDB(str(tmp_path / "s.db"))
    await d.connect()
    await d.migrate()
    yield d
    await d.close()


async def test_insert_and_get_task(db):
    t = Task(channel="cli", user_ref="u1", playbook_id="bug-fix", inputs={"repo": "x"})
    tid = await db.insert_task(t)
    assert tid > 0
    got = await db.get_task(tid)
    assert got.playbook_id == "bug-fix"
    assert got.inputs == {"repo": "x"}
    assert got.status == TaskStatus.QUEUED


async def test_update_task_status(db):
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="r", inputs={}))
    await db.set_task_status(tid, TaskStatus.RUNNING)
    t = await db.get_task(tid)
    assert t.status == TaskStatus.RUNNING


async def test_insert_and_list_stages(db):
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="r", inputs={}))
    await db.insert_stage(Stage(task_id=tid, idx=0, name="pre"))
    await db.insert_stage(Stage(task_id=tid, idx=1, name="impl"))
    stages = await db.get_stages(tid)
    assert len(stages) == 2
    assert stages[0].idx == 0


async def test_update_stage(db):
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="r", inputs={}))
    await db.insert_stage(Stage(task_id=tid, idx=0, name="pre"))
    await db.update_stage(tid, 0, status=StageStatus.DONE, session_id="abc")
    s = (await db.get_stages(tid))[0]
    assert s.status == StageStatus.DONE
    assert s.session_id == "abc"


async def test_list_tasks_by_status(db):
    tid1 = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="r", inputs={}))
    await db.set_task_status(tid1, TaskStatus.RUNNING)
    await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="r", inputs={}))
    running = await db.list_tasks(statuses=[TaskStatus.RUNNING])
    assert len(running) == 1
    assert running[0].id == tid1
