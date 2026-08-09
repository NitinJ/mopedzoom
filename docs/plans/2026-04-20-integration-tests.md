# Integration Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 7 integration tests across two new files covering the 5 untested flows: approval gate, question gate, stage failure, CLI socket ops, and Telegram channel adapter.

**Architecture:** `test_lifecycle.py` reuses the existing `fake_claude` + `StateDB` pattern, each test using a custom bash script via a new `fake_claude_variant` factory fixture. `test_telegram_channel.py` injects a `FakeBot` stub directly into `TelegramChannel` via its `_bot` constructor kwarg, then drives `_on_message`/`_on_callback`/`post` without any network calls.

**Tech Stack:** `pytest`, `pytest-asyncio` (already installed), `python-telegram-bot>=21` (already a dep), `asyncio.Event` for async rendezvous, `MagicMock`/`AsyncMock` from `unittest.mock`.

---

## File Map

| Action | Path | Purpose |
|---|---|---|
| Modify | `tests/integration/conftest.py` | Add `fake_claude_variant` factory fixture |
| Create | `tests/integration/test_lifecycle.py` | 4 lifecycle integration tests |
| Create | `tests/integration/test_telegram_channel.py` | 3 Telegram adapter tests + helpers |

---

### Task 1: `fake_claude_variant` fixture

**Files:**
- Modify: `tests/integration/conftest.py`

- [ ] **Step 1: Read the current conftest.py**

```
tests/integration/conftest.py
```
Confirm it contains `FAKE_CLAUDE` script and `fake_claude` fixture.

- [ ] **Step 2: Add the `fake_claude_variant` factory**

Append to `tests/integration/conftest.py` after the existing `fake_claude` fixture:

```python
@pytest.fixture
def fake_claude_variant(tmp_path, monkeypatch):
    """Factory: install a custom bash script as the `claude` binary on PATH.

    Usage::

        def test_something(fake_claude_variant, tmp_path):
            fake_claude_variant(\"\"\"
    echo "session-id: sess-x"
    cat > "$MOPEDZOOM_SCRATCH/0-impl.deliverable.json" <<'EOF'
    {"stage":"impl","status":"ok","artifacts":[],"notes":"done"}
    EOF
    \"\"\")
    """
    def _make(script_body: str) -> Path:
        p = tmp_path / "claude"
        p.write_text("#!/usr/bin/env bash\n" + script_body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
        return p
    return _make
```

Also add `from pathlib import Path` to imports if not already present.

- [ ] **Step 3: Verify no import errors**

```bash
cd /home/nitin/workspace/mopedzoom && python3 -m pytest tests/integration/conftest.py --collect-only -q 2>&1 | head -10
```

Expected: no errors.

---

### Task 2: `test_approval_gate_full_cycle`

**Files:**
- Create: `tests/integration/test_lifecycle.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_lifecycle.py` with:

```python
"""Lifecycle integration tests: approval gate, question gate, failure, CLI ops."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mopedzoomd.channels.base import Channel, OutboundMessage
from mopedzoomd.channels.cli_socket import CLISocketChannel
from mopedzoomd.daemon import TaskManager, build_cli_op_handler, resolve_interaction
from mopedzoomd.models import Interaction, InteractionKind, Task, StageStatus, TaskStatus
from mopedzoomd.playbooks import Playbook, StageSpec
from mopedzoomd.stage_runner import StageRunner
from mopedzoomd.state import StateDB


class _RecordingChannel(Channel):
    """Minimal channel that records outbound messages."""

    def __init__(self):
        self.posts: list[OutboundMessage] = []
        self._handler = None

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def set_handler(self, handler) -> None:
        self._handler = handler

    async def post(self, msg: OutboundMessage) -> str:
        self.posts.append(msg)
        return "ref:1"


async def test_approval_gate_full_cycle(fake_claude_variant, tmp_path):
    """run_task pauses at AWAITING_APPROVAL; injected approval resumes it to DELIVERED."""
    fake_claude_variant(
        """
echo "session-id: sess-approval"
cat > "$MOPEDZOOM_SCRATCH/0-impl.deliverable.json" <<'EOF'
{"stage":"impl","status":"ok","artifacts":[],"notes":"done"}
EOF
cat > "$MOPEDZOOM_SCRATCH/1-verify.deliverable.json" <<'EOF'
{"stage":"verify","status":"ok","artifacts":[],"notes":"done"}
EOF
"""
    )

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    pb = Playbook(
        id="two-stage",
        summary="two stage approval test",
        triggers=["two"],
        stages=[
            StageSpec(name="impl", requires="do X", produces="impl.md", approval="none"),
            StageSpec(name="verify", requires="verify X", produces="verify.md", approval="required"),
        ],
    )
    ch = _RecordingChannel()
    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=StageRunner(),
        playbook_registry={"two-stage": pb},
        channels={"cli": ch},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )

    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="two-stage", inputs={})
    )

    async def inject_approval():
        for _ in range(200):
            task = await db.get_task(tid)
            if task.status == TaskStatus.AWAITING_APPROVAL:
                await resolve_interaction(db, task_id=tid, answer="approve")
                return
            await asyncio.sleep(0.05)
        raise TimeoutError("never reached AWAITING_APPROVAL")

    await asyncio.gather(tm.run_task(tid), inject_approval())

    task = await db.get_task(tid)
    assert task.status == TaskStatus.DELIVERED

    events = await db.list_events(tid)
    kinds = [e.kind for e in events]
    assert "stage_done" in kinds
    assert "resolved_approve" in kinds
    await db.close()
```

- [ ] **Step 2: Run the test**

```bash
cd /home/nitin/workspace/mopedzoom && python3 -m pytest tests/integration/test_lifecycle.py::test_approval_gate_full_cycle -v 2>&1 | tail -30
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
cd /home/nitin/workspace/mopedzoom && git add tests/integration/conftest.py tests/integration/test_lifecycle.py && git commit -m "test: add fake_claude_variant fixture and approval gate integration test"
```

---

### Task 3: `test_question_gate_full_cycle`

**Files:**
- Modify: `tests/integration/test_lifecycle.py`

- [ ] **Step 1: Append the test**

Add to `tests/integration/test_lifecycle.py`:

```python
async def test_question_gate_full_cycle(fake_claude_variant, tmp_path):
    """Agent writes question.json on first run; answer injected; second run delivers."""
    fake_claude_variant(
        """
echo "session-id: sess-question"
SENTINEL="$MOPEDZOOM_SCRATCH/first_run_done"
if [ ! -f "$SENTINEL" ]; then
    touch "$SENTINEL"
    cat > "$MOPEDZOOM_SCRATCH/question.json" <<'EOF'
{"prompt":"Which city?","kind":"free_text"}
EOF
    exit 0
fi
cat > "$MOPEDZOOM_SCRATCH/0-impl.deliverable.json" <<'EOF'
{"stage":"impl","status":"ok","artifacts":[],"notes":"done"}
EOF
"""
    )

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    pb = Playbook(
        id="question-test",
        summary="question gate test",
        triggers=["q"],
        stages=[
            StageSpec(name="impl", requires="do X", produces="impl.md", approval="none"),
        ],
    )
    ch = _RecordingChannel()
    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=StageRunner(),
        playbook_registry={"question-test": pb},
        channels={"cli": ch},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )

    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="question-test", inputs={})
    )

    async def inject_answer():
        for _ in range(200):
            task = await db.get_task(tid)
            if task.status == TaskStatus.AWAITING_INPUT:
                await resolve_interaction(db, task_id=tid, answer="Paris")
                return
            await asyncio.sleep(0.05)
        raise TimeoutError("never reached AWAITING_INPUT")

    await asyncio.gather(tm.run_task(tid), inject_answer())

    task = await db.get_task(tid)
    assert task.status == TaskStatus.DELIVERED

    bodies = [p.body for p in ch.posts]
    assert any("Which city?" in b for b in bodies)

    events = await db.list_events(tid)
    kinds = [e.kind for e in events]
    assert "stage_done" in kinds
    await db.close()
```

- [ ] **Step 2: Run the test**

```bash
cd /home/nitin/workspace/mopedzoom && python3 -m pytest tests/integration/test_lifecycle.py::test_question_gate_full_cycle -v 2>&1 | tail -30
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
cd /home/nitin/workspace/mopedzoom && git add tests/integration/test_lifecycle.py && git commit -m "test: add question gate integration test"
```

---

### Task 4: `test_stage_failure_no_manifest`

**Files:**
- Modify: `tests/integration/test_lifecycle.py`

- [ ] **Step 1: Append the test**

Add to `tests/integration/test_lifecycle.py`:

```python
async def test_stage_failure_no_manifest(fake_claude_variant, tmp_path):
    """Agent exits 0 but writes no manifest; task ends FAILED with stage_failed event."""
    fake_claude_variant(
        """
echo "session-id: sess-fail"
# Deliberately write no deliverable manifest.
"""
    )

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    pb = Playbook(
        id="fail-test",
        summary="failure test",
        triggers=["fail"],
        stages=[
            StageSpec(name="impl", requires="do X", produces="x.md", approval="none"),
        ],
    )
    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=StageRunner(),
        playbook_registry={"fail-test": pb},
        channels={"cli": _RecordingChannel()},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )

    tid = await db.insert_task(
        Task(channel="cli", user_ref="u", playbook_id="fail-test", inputs={})
    )
    await tm.run_task(tid)

    task = await db.get_task(tid)
    assert task.status == TaskStatus.FAILED

    events = await db.list_events(tid)
    assert any(e.kind == "stage_failed" for e in events)

    stages = await db.get_stages(tid)
    assert stages[0].status == StageStatus.FAILED
    await db.close()
```

- [ ] **Step 2: Run the test**

```bash
cd /home/nitin/workspace/mopedzoom && python3 -m pytest tests/integration/test_lifecycle.py::test_stage_failure_no_manifest -v 2>&1 | tail -20
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
cd /home/nitin/workspace/mopedzoom && git add tests/integration/test_lifecycle.py && git commit -m "test: add stage failure integration test"
```

---

### Task 5: `test_cli_socket_ops_round_trip`

**Files:**
- Modify: `tests/integration/test_lifecycle.py`

- [ ] **Step 1: Append the test**

Add to `tests/integration/test_lifecycle.py`:

```python
async def test_cli_socket_ops_round_trip(tmp_path):
    """Real socket send/receive for status, cancel, resume ops via CLISocketChannel."""
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    pb = Playbook(
        id="dummy",
        summary="dummy",
        triggers=["dummy"],
        stages=[StageSpec(name="impl", requires="r", produces="x.md", approval="none")],
    )
    ch = CLISocketChannel(str(tmp_path / "sock"))
    await ch.start()
    ch.set_handler(AsyncMock())
    ch.set_op_handler(
        build_cli_op_handler(
            TaskManager(
                db=db,
                runs_root=str(tmp_path / "runs"),
                stage_runner=StageRunner(),
                playbook_registry={"dummy": pb},
                channels={"cli": ch},
                worktree_mgr=None,
                agent_discoverer=lambda: [],
            )
        )
    )

    t1 = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="dummy", inputs={}))
    t2 = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="dummy", inputs={}))

    async def send_op(payload: dict) -> dict:
        r, w = await asyncio.open_unix_connection(str(tmp_path / "sock"))
        w.write((json.dumps(payload) + "\n").encode())
        await w.drain()
        line = await r.readline()
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass
        return json.loads(line.decode())

    # list tasks
    resp = await send_op({"op": "tasks"})
    assert resp["ok"] is True
    assert len(resp["tasks"]) == 2

    # get status
    resp = await send_op({"op": "status", "id": t1})
    assert resp["ok"] is True
    assert resp["id"] == t1
    assert "status" in resp

    # cancel task 1
    resp = await send_op({"op": "cancel", "id": t1})
    assert resp["ok"] is True
    task = await db.get_task(t1)
    assert task.status == TaskStatus.CANCELLED

    # resume task 2 (set to PAUSED first so resume is valid)
    await db.set_task_status(t2, TaskStatus.PAUSED)
    resp = await send_op({"op": "resume", "id": t2})
    assert resp["ok"] is True
    task = await db.get_task(t2)
    assert task.status == TaskStatus.RUNNING

    await ch.stop()
    await db.close()
```

- [ ] **Step 2: Run the full lifecycle test file**

```bash
cd /home/nitin/workspace/mopedzoom && python3 -m pytest tests/integration/test_lifecycle.py -v 2>&1 | tail -30
```

Expected: 4 passed.

- [ ] **Step 3: Commit**

```bash
cd /home/nitin/workspace/mopedzoom && git add tests/integration/test_lifecycle.py && git commit -m "test: add CLI socket ops round-trip integration test"
```

---

### Task 6: Telegram test helpers and fixtures

**Files:**
- Create: `tests/integration/test_telegram_channel.py`

- [ ] **Step 1: Create the file with helpers only (no tests yet)**

Create `tests/integration/test_telegram_channel.py`:

```python
"""Telegram channel adapter integration tests.

Uses FakeBot injected via TelegramChannel(_bot=...) to intercept all
Telegram API calls. No network calls are made. Handlers are exercised by
calling _on_message/_on_callback directly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mopedzoomd.channels.base import OutboundMessage
from mopedzoomd.channels.telegram import TelegramChannel
from mopedzoomd.daemon import TaskManager, handle_inbound, resolve_interaction
from mopedzoomd.models import Interaction, InteractionKind, Task, TaskStatus
from mopedzoomd.playbooks import load_playbooks
from mopedzoomd.router import Router
from mopedzoomd.stage_runner import StageRunner
from mopedzoomd.state import StateDB


class FakeBot:
    """Records all Telegram API calls; no network I/O."""

    def __init__(self):
        self.sent_messages: list[dict] = []
        self.created_topics: list[dict] = []
        self._msg_event: asyncio.Event = asyncio.Event()

    async def send_message(
        self, *, chat_id, text, reply_markup=None, message_thread_id=None, **kwargs
    ):
        self.sent_messages.append(
            {"chat_id": chat_id, "text": text, "thread_id": message_thread_id}
        )
        self._msg_event.set()
        m = MagicMock()
        m.chat_id = chat_id
        m.message_thread_id = message_thread_id or 0
        m.message_id = len(self.sent_messages)
        return m

    async def create_forum_topic(self, *, chat_id, name, **kwargs):
        self.created_topics.append({"chat_id": chat_id, "name": name})
        ft = MagicMock()
        ft.message_thread_id = 42
        return ft

    async def close_forum_topic(self, *, chat_id, message_thread_id, **kwargs):
        pass


def _make_message_update(text: str, chat_id: int = -100123, thread_id: int | None = None):
    """Build a fake telegram.Update carrying a text message."""
    from telegram import Update

    update = MagicMock(spec=Update)
    update.message = MagicMock()
    update.message.text = text
    update.message.chat_id = chat_id
    update.message.message_thread_id = thread_id
    update.message.reply_to_message = None
    update.callback_query = None
    return update


def _make_callback_update(task_id: int, action: str, chat_id: int = -100123):
    """Build a fake telegram.Update carrying an inline-button callback."""
    from telegram import Update

    update = MagicMock(spec=Update)
    update.message = None
    cq = MagicMock()
    cq.id = "cq_id_1"
    cq.data = f"{task_id}:{action}"
    cq.answer = AsyncMock()
    cq.message = MagicMock()
    cq.message.chat_id = chat_id
    update.callback_query = cq
    return update


@pytest.fixture
def bot_and_channel():
    """TelegramChannel with a FakeBot injected; no real Application built."""
    bot = FakeBot()
    # Pass _app=MagicMock() so TelegramChannel.start() sees a non-None _app
    # and skips the Application.builder() branch entirely.
    channel = TelegramChannel(
        bot_token="fake-token",
        chat_id=-100123,
        mode="topics",
        _bot=bot,
        _app=MagicMock(),
    )
    return bot, channel
```

- [ ] **Step 2: Verify it imports cleanly**

```bash
cd /home/nitin/workspace/mopedzoom && python3 -c "import tests.integration.test_telegram_channel" 2>&1
```

Expected: no output (clean import). If there's an import error from `telegram`, it means `python-telegram-bot` is installed — check with `pip show python-telegram-bot`.

- [ ] **Step 3: Commit the helpers**

```bash
cd /home/nitin/workspace/mopedzoom && git add tests/integration/test_telegram_channel.py && git commit -m "test: add Telegram test helpers (FakeBot, update factories, fixture)"
```

---

### Task 7: `test_telegram_inbound_text_submits_task`

**Files:**
- Modify: `tests/integration/test_telegram_channel.py`

- [ ] **Step 1: Append the test**

Add to `tests/integration/test_telegram_channel.py`:

```python
async def test_telegram_inbound_text_submits_task(bot_and_channel, tmp_path):
    """Inbound text matching a playbook trigger → task inserted in DB + ack sent."""
    bot, channel = bot_and_channel

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    root = Path(__file__).resolve().parents[2]
    registry = load_playbooks(builtin_dir=root / "playbooks", user_dir=None)
    router = Router(registry=registry, claude_client=None)
    tm = TaskManager(
        db=db,
        runs_root=str(tmp_path / "runs"),
        stage_runner=StageRunner(),
        playbook_registry=registry,
        channels={"telegram": channel},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )

    async def handler(msg):
        await handle_inbound(
            msg,
            db=db,
            router=router,
            tm=tm,
            channels={"telegram": channel},
            registry=registry,
        )

    channel.set_handler(handler)

    update = _make_message_update("research AI trends")
    await channel._on_message(update, None)

    # Wait up to 2s for the ack message to be posted
    await asyncio.wait_for(bot._msg_event.wait(), timeout=2.0)

    tasks = await db.list_tasks(limit=10)
    assert len(tasks) == 1
    assert tasks[0].status == TaskStatus.QUEUED
    assert len(bot.sent_messages) == 1
    assert str(tasks[0].id) in bot.sent_messages[0]["text"]

    await db.close()
```

- [ ] **Step 2: Run the test**

```bash
cd /home/nitin/workspace/mopedzoom && python3 -m pytest tests/integration/test_telegram_channel.py::test_telegram_inbound_text_submits_task -v 2>&1 | tail -30
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
cd /home/nitin/workspace/mopedzoom && git add tests/integration/test_telegram_channel.py && git commit -m "test: Telegram inbound text submits task"
```

---

### Task 8: `test_telegram_approval_button_resolves_interaction`

**Files:**
- Modify: `tests/integration/test_telegram_channel.py`

- [ ] **Step 1: Append the test**

Add to `tests/integration/test_telegram_channel.py`:

```python
async def test_telegram_approval_button_resolves_interaction(bot_and_channel, tmp_path):
    """Approval button callback → resolve_interaction sets task RUNNING."""
    bot, channel = bot_and_channel

    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()

    tid = await db.insert_task(
        Task(channel="telegram", user_ref="chat:-100123", playbook_id="research", inputs={})
    )
    await db.set_task_status(tid, TaskStatus.AWAITING_APPROVAL)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.APPROVAL,
            prompt="Approve stage?",
            posted_to_channel_ref="tg:-100123:42:1",
        )
    )

    async def handler(msg):
        await resolve_interaction(db, task_id=msg.task_id, answer=msg.text)

    channel.set_handler(handler)

    update = _make_callback_update(tid, "approve")
    await channel._on_callback(update, None)

    task = await db.get_task(tid)
    assert task.status == TaskStatus.RUNNING

    pending = await db.list_pending_interactions(tid)
    assert len(pending) == 0

    events = await db.list_events(tid)
    assert any(e.kind == "resolved_approve" for e in events)

    await db.close()
```

- [ ] **Step 2: Run the test**

```bash
cd /home/nitin/workspace/mopedzoom && python3 -m pytest tests/integration/test_telegram_channel.py::test_telegram_approval_button_resolves_interaction -v 2>&1 | tail -20
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
cd /home/nitin/workspace/mopedzoom && git add tests/integration/test_telegram_channel.py && git commit -m "test: Telegram approval button resolves interaction"
```

---

### Task 9: `test_telegram_post_sends_to_bound_topic`

**Files:**
- Modify: `tests/integration/test_telegram_channel.py`

- [ ] **Step 1: Append the test**

Add to `tests/integration/test_telegram_channel.py`:

```python
async def test_telegram_post_sends_to_bound_topic(bot_and_channel):
    """create_topic + bind_task_topic + post sends message to the right thread."""
    bot, channel = bot_and_channel

    # Simulate what the daemon does when starting a task in topics mode.
    thread_id = await channel.create_topic(title="Task #1 — research")
    assert thread_id == 42  # FakeBot always returns 42
    assert len(bot.created_topics) == 1
    assert bot.created_topics[0]["name"] == "Task #1 — research"

    channel.bind_task_topic(
        task_id=1, thread_id=thread_id, playbook_id="research", repo="ml"
    )

    ref = await channel.post(OutboundMessage(task_id=1, body="Stage done"))

    assert len(bot.sent_messages) == 1
    # topics mode: header is empty, body is sent verbatim
    assert bot.sent_messages[0]["text"] == "Stage done"
    assert bot.sent_messages[0]["thread_id"] == 42
    # ref encodes chat_id, thread_id, message_id
    assert ref.startswith("tg:")
    assert ":42:" in ref
```

- [ ] **Step 2: Run the full Telegram test file**

```bash
cd /home/nitin/workspace/mopedzoom && python3 -m pytest tests/integration/test_telegram_channel.py -v 2>&1 | tail -20
```

Expected: 3 passed.

- [ ] **Step 3: Run the full test suite**

```bash
cd /home/nitin/workspace/mopedzoom && python3 -m pytest tests/ -q 2>&1 | tail -10
```

Expected: 175 passed (168 existing + 7 new), 0 failed.

- [ ] **Step 4: Commit**

```bash
cd /home/nitin/workspace/mopedzoom && git add tests/integration/test_telegram_channel.py && git commit -m "test: Telegram post creates topic and sends to bound thread"
```

---

## Self-Review

**Spec coverage:**
- Approval gate (gap 1) → Task 2 ✓
- Question gate (gap 2) → Task 3 ✓
- Stage failure (gap 3) → Task 4 ✓
- CLI socket ops (gap 4) → Task 5 ✓
- Telegram inbound → submit (gap 5a) → Task 7 ✓
- Telegram button → resolve (gap 5b) → Task 8 ✓
- Telegram post → topic + message (gap 5c) → Task 9 ✓

**Note on spec deviation:** The spec said "first post auto-creates topic." The actual `TelegramChannel.post()` does not auto-create topics — `create_topic()` and `bind_task_topic()` must be called explicitly. Task 9 tests the actual API, not the spec's incorrect assumption.

**Type consistency:**
- `_RecordingChannel` used in Tasks 2–4; `CLISocketChannel` used in Task 5 — no overlap.
- `FakeBot._msg_event` is `asyncio.Event` — awaited with `asyncio.wait_for` in Task 7.
- `db.get_stages(tid)` called in Task 4; `db.list_tasks(limit=N)` called in Task 7 — both exist on `StateDB` (verified from daemon.py usage).
- `build_cli_op_handler` imported in Task 5 from `mopedzoomd.daemon` — confirmed present.
- `StageStatus` imported in Task 4 for `stages[0].status` assertion.
