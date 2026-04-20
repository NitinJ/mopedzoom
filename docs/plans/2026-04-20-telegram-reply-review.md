# Telegram Reply Routing & Stage Revision Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reply-based interaction routing and a configurable `approval: review` stage mode that lets the agent iterate on its deliverable based on user feedback until the user explicitly approves.

**Architecture:** The Telegram channel already captures `reply_to_ref`; we match it against `pending_interactions.posted_to_channel_ref` in `handle_inbound` to derive task_id without relying on topics-mode threading. A new `_await_review` method on `TaskManager` posts the stage deliverable as a file attachment and loops: text replies append feedback to a scratch file and raise `_RetryStage`; the `[✓ Approve]` button sets status to RUNNING and returns. `_build_prompt` reads the scratch feedback/answer files and injects them into the agent prompt on retry.

**Tech Stack:** Python asyncio, aiosqlite, python-telegram-bot, pydantic, pytest-asyncio

---

### Task 1: ScratchDir feedback and answer helpers

**Files:**
- Modify: `src/mopedzoomd/scratch.py`
- Test: `tests/test_scratch.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scratch.py`:

```python
def test_feedback_append_and_read(tmp_path):
    s = ScratchDir(str(tmp_path), task_id=1)
    s.create()
    assert s.read_feedback(0) == []
    s.append_feedback(0, "more detail please")
    s.append_feedback(0, "cut genetics section")
    assert s.read_feedback(0) == ["more detail please", "cut genetics section"]


def test_feedback_persists_across_instances(tmp_path):
    s1 = ScratchDir(str(tmp_path), task_id=1)
    s1.create()
    s1.append_feedback(0, "iteration one")
    s2 = ScratchDir(str(tmp_path), task_id=1)
    assert s2.read_feedback(0) == ["iteration one"]


def test_answer_write_and_read(tmp_path):
    s = ScratchDir(str(tmp_path), task_id=1)
    s.create()
    assert s.read_answer(0) is None
    s.write_answer(0, "south india focus, 1500 words")
    assert s.read_answer(0) == "south india focus, 1500 words"


def test_answer_overwrite(tmp_path):
    s = ScratchDir(str(tmp_path), task_id=1)
    s.create()
    s.write_answer(0, "first")
    s.write_answer(0, "second")
    assert s.read_answer(0) == "second"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/nitin/workspace/mopedzoom
.venv/bin/pytest tests/test_scratch.py::test_feedback_append_and_read -v
```

Expected: `FAILED` with `AttributeError: 'ScratchDir' object has no attribute 'read_feedback'`

- [ ] **Step 3: Implement the helpers in scratch.py**

Add these methods to the `ScratchDir` class (after `clear_permission`):

```python
def feedback_path(self, idx: int) -> Path:
    return self.dir / f"{idx}-feedback.json"

def answer_path(self, idx: int) -> Path:
    return self.dir / f"{idx}-answer.json"

def append_feedback(self, idx: int, text: str) -> None:
    p = self.feedback_path(idx)
    self.dir.mkdir(parents=True, exist_ok=True)
    existing = json.loads(p.read_text()) if p.exists() else {"feedbacks": []}
    existing["feedbacks"].append(text)
    p.write_text(json.dumps(existing))

def read_feedback(self, idx: int) -> list[str]:
    p = self.feedback_path(idx)
    if not p.exists():
        return []
    return json.loads(p.read_text()).get("feedbacks", [])

def write_answer(self, idx: int, text: str) -> None:
    self.dir.mkdir(parents=True, exist_ok=True)
    self.answer_path(idx).write_text(json.dumps({"answer": text}))

def read_answer(self, idx: int) -> str | None:
    p = self.answer_path(idx)
    if not p.exists():
        return None
    return json.loads(p.read_text()).get("answer")
```

- [ ] **Step 4: Run all scratch tests**

```bash
.venv/bin/pytest tests/test_scratch.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/mopedzoomd/scratch.py tests/test_scratch.py
git commit -m "feat: add ScratchDir feedback and answer helpers"
```

---

### Task 2: StateDB.get_interaction_by_ref

**Files:**
- Modify: `src/mopedzoomd/state.py` (the `_MiscMixin` class)
- Test: `tests/test_state_misc.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_state_misc.py`:

```python
async def test_get_interaction_by_ref_found(db):
    d, tid = db
    await d.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.REVISION,
            prompt="review pre-brief",
            posted_to_channel_ref="tg:-1003970933483:0:99",
        )
    )
    result = await d.get_interaction_by_ref("tg:-1003970933483:0:99")
    assert result is not None
    assert result.task_id == tid
    assert result.kind == InteractionKind.REVISION
    assert result.stage_idx == 0


async def test_get_interaction_by_ref_not_found(db):
    d, tid = db
    result = await d.get_interaction_by_ref("tg:-1:0:doesnotexist")
    assert result is None


async def test_get_interaction_by_ref_returns_none_after_resolved(db):
    d, tid = db
    iid = await d.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.REVISION,
            prompt="review",
            posted_to_channel_ref="tg:-1:0:77",
        )
    )
    await d.resolve_interaction(iid)
    result = await d.get_interaction_by_ref("tg:-1:0:77")
    assert result is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_state_misc.py::test_get_interaction_by_ref_found -v
```

Expected: `FAILED` with `AttributeError: 'StateDB' object has no attribute 'get_interaction_by_ref'`

- [ ] **Step 3: Add the method to _MiscMixin in state.py**

Add this method inside the `_MiscMixin` class, after `resolve_interaction`:

```python
async def get_interaction_by_ref(self, ref: str) -> Interaction | None:
    r = await self.fetch_one(
        "SELECT * FROM pending_interactions WHERE posted_to_channel_ref=?", (ref,)
    )
    return _row_to_int(r) if r else None
```

The `setattr` loop at the end of the file (`for _name in dir(_MiscMixin)`) already copies this method to `StateDB` automatically.

- [ ] **Step 4: Run all state_misc tests**

```bash
.venv/bin/pytest tests/test_state_misc.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/mopedzoomd/state.py tests/test_state_misc.py
git commit -m "feat: add StateDB.get_interaction_by_ref for reply routing"
```

---

### Task 3: OutboundMessage.document_path + TelegramChannel file posting

**Files:**
- Modify: `src/mopedzoomd/channels/base.py`
- Modify: `src/mopedzoomd/channels/telegram.py`
- Test: `tests/test_channels_telegram.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_channels_telegram.py`:

```python
async def test_post_document_calls_send_document(monkeypatch, tmp_path):
    from pathlib import Path
    from mopedzoomd.channels.base import OutboundMessage

    doc = tmp_path / "brief.md"
    doc.write_text("# Pre-brief\n\nContent here.")

    bot = AsyncMock()
    bot.send_document = AsyncMock(
        return_value=type(
            "M", (), {"message_id": 200, "chat_id": -100, "message_thread_id": None}
        )()
    )
    ch = TelegramChannel(bot_token="x", chat_id=-100, mode="header", _bot=bot)
    ref = await ch.post(
        OutboundMessage(task_id=1, body="review this please", document_path=doc)
    )
    bot.send_document.assert_awaited_once()
    kwargs = bot.send_document.await_args.kwargs
    assert kwargs["document"] == doc
    assert "review this please" in kwargs["caption"]
    assert ref == "tg:-100:0:200"
    bot.send_message.assert_not_called()


async def test_post_without_document_still_uses_send_message(monkeypatch):
    from mopedzoomd.channels.base import OutboundMessage

    bot = AsyncMock()
    bot.send_message = AsyncMock(
        return_value=type(
            "M", (), {"message_id": 5, "chat_id": -1, "message_thread_id": None}
        )()
    )
    ch = TelegramChannel(bot_token="x", chat_id=-1, mode="header", _bot=bot)
    await ch.post(OutboundMessage(task_id=1, body="hello"))
    bot.send_message.assert_awaited_once()
    bot.send_document.assert_not_called()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_channels_telegram.py::test_post_document_calls_send_document -v
```

Expected: `FAILED` — `OutboundMessage` has no `document_path`

- [ ] **Step 3: Add document_path to OutboundMessage in base.py**

The current `OutboundMessage` dataclass in `src/mopedzoomd/channels/base.py`:

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
```

Add `from pathlib import Path` import (it may already be there; if not, add it). Then update the dataclass:

```python
@dataclass
class OutboundMessage:
    body: str
    buttons: list[ApprovalButton] = field(default_factory=list)
    task_id: int | None = None
    channel_ref: str | None = None
    document_path: Path | None = None
```

- [ ] **Step 4: Update TelegramChannel.post() in telegram.py**

Replace the current `post` method body with:

```python
async def post(self, msg: OutboundMessage) -> str:
    tb = self._topics.get(msg.task_id)
    header = _format_header(
        msg.task_id or 0,
        tb.playbook_id if tb else "?",
        tb.repo if tb else "?",
        self.mode,
    )
    kb = None
    if msg.buttons:
        rows = [
            [
                InlineKeyboardButton(b.label, callback_data=f"{msg.task_id}:{b.callback}")
                for b in msg.buttons
            ]
        ]
        kb = InlineKeyboardMarkup(rows)
    thread_id = tb.thread_id if (tb and self.mode == "topics") else None
    if msg.document_path is not None:
        sent = await self._bot.send_document(
            chat_id=self.chat_id,
            document=msg.document_path,
            caption=header + msg.body,
            reply_markup=kb,
            message_thread_id=thread_id,
        )
    else:
        sent = await self._bot.send_message(
            chat_id=self.chat_id,
            text=header + msg.body,
            reply_markup=kb,
            message_thread_id=thread_id,
        )
    return f"tg:{sent.chat_id}:{sent.message_thread_id or 0}:{sent.message_id}"
```

- [ ] **Step 5: Run all channel tests**

```bash
.venv/bin/pytest tests/test_channels_telegram.py tests/test_channels_base.py -v
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/mopedzoomd/channels/base.py src/mopedzoomd/channels/telegram.py tests/test_channels_telegram.py
git commit -m "feat: OutboundMessage.document_path + TelegramChannel send_document support"
```

---

### Task 4: StageSpec approval: review

**Files:**
- Modify: `src/mopedzoomd/playbooks.py`
- Test: `tests/test_playbooks.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_playbooks.py`:

```python
def test_stage_spec_approval_review_is_valid():
    from mopedzoomd.playbooks import StageSpec
    s = StageSpec(name="pre-brief", requires="scope the topic", produces="pre-brief.md", approval="review")
    assert s.approval == "review"


def test_stage_spec_approval_invalid_value_rejected():
    import pytest
    from pydantic import ValidationError
    from mopedzoomd.playbooks import StageSpec
    with pytest.raises(ValidationError):
        StageSpec(name="x", requires="r", produces="p.md", approval="not-a-real-value")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_playbooks.py::test_stage_spec_approval_review_is_valid -v
```

Expected: `FAILED` with `ValidationError` — `"review"` not in Literal

- [ ] **Step 3: Add "review" to the Literal in playbooks.py**

In `src/mopedzoomd/playbooks.py`, change line 22:

```python
approval: Literal["required", "on-completion", "on-failure", "none", "review"] = "required"
```

- [ ] **Step 4: Run playbook tests**

```bash
.venv/bin/pytest tests/test_playbooks.py tests/test_playbooks_shipped.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/mopedzoomd/playbooks.py tests/test_playbooks.py
git commit -m "feat: add approval: review to StageSpec"
```

---

### Task 5: resolve_interaction with scratch writing

**Files:**
- Modify: `src/mopedzoomd/daemon.py` — `resolve_interaction` function (line ~548)
- Test: `tests/test_daemon_approval.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_daemon_approval.py`:

```python
async def test_resolve_question_answer_writes_scratch_and_sets_awaiting_input(tmp_path):
    from mopedzoomd.scratch import ScratchDir
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.AWAITING_INPUT)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.QUESTION,
            prompt="what angle?",
            posted_to_channel_ref="tg:x",
        )
    )
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=tid)
    scratch.create()
    await resolve_interaction(db, task_id=tid, answer="south india focus", scratch=scratch)
    assert scratch.read_answer(0) == "south india focus"
    assert (await db.get_task(tid)).status == TaskStatus.AWAITING_INPUT
    assert len(await db.list_pending_interactions(tid)) == 0
    await db.close()


async def test_resolve_revision_feedback_appends_scratch_and_sets_awaiting_input(tmp_path):
    from mopedzoomd.scratch import ScratchDir
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.AWAITING_APPROVAL)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.REVISION,
            prompt="review pre-brief",
            posted_to_channel_ref="tg:y",
        )
    )
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=tid)
    scratch.create()
    await resolve_interaction(db, task_id=tid, answer="add welfare section", scratch=scratch)
    assert scratch.read_feedback(0) == ["add welfare section"]
    assert (await db.get_task(tid)).status == TaskStatus.AWAITING_INPUT
    assert len(await db.list_pending_interactions(tid)) == 0
    await db.close()


async def test_resolve_approve_with_scratch_still_sets_running(tmp_path):
    from mopedzoomd.scratch import ScratchDir
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.AWAITING_APPROVAL)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.REVISION,
            prompt="review",
            posted_to_channel_ref="tg:z",
        )
    )
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=tid)
    scratch.create()
    await resolve_interaction(db, task_id=tid, answer="approve", scratch=scratch)
    assert (await db.get_task(tid)).status == TaskStatus.RUNNING
    await db.close()
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_daemon_approval.py::test_resolve_question_answer_writes_scratch_and_sets_awaiting_input -v
```

Expected: `FAILED` — `resolve_interaction` doesn't accept `scratch` kwarg

- [ ] **Step 3: Update resolve_interaction in daemon.py**

Replace the current `resolve_interaction` function (starting at line ~548):

```python
async def resolve_interaction(
    db: StateDB, *, task_id: int, answer: str, scratch: ScratchDir | None = None
) -> None:
    """Called by channels when the user clicks an approval button or sends a reply."""
    pend = await db.list_pending_interactions(task_id)
    if not pend:
        return
    i = pend[0]
    await db.resolve_interaction(i.id)
    if answer == "approve":
        await db.set_task_status(task_id, TaskStatus.RUNNING)
    elif answer == "cancel":
        await db.set_task_status(task_id, TaskStatus.CANCELLED)
    elif answer == "revise":
        await db.set_task_status(task_id, TaskStatus.AWAITING_INPUT)
    elif answer == "pause":
        await db.set_task_status(task_id, TaskStatus.PAUSED)
    elif answer == "resume":
        await db.set_task_status(task_id, TaskStatus.RUNNING)
    else:
        # Free-text: store as question answer or revision feedback
        if scratch is not None:
            if i.kind == InteractionKind.QUESTION:
                scratch.write_answer(i.stage_idx, answer)
            elif i.kind == InteractionKind.REVISION:
                scratch.append_feedback(i.stage_idx, answer)
        await db.set_task_status(task_id, TaskStatus.AWAITING_INPUT)
    await db.log_event(
        TaskEvent(
            task_id=task_id,
            kind="resolved_interaction",
            detail={"answer": answer[:100]},
        )
    )
```

- [ ] **Step 4: Run all approval tests**

```bash
.venv/bin/pytest tests/test_daemon_approval.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/mopedzoomd/daemon.py tests/test_daemon_approval.py
git commit -m "feat: resolve_interaction writes answer/feedback to scratch"
```

---

### Task 6: Reply routing in handle_inbound

**Files:**
- Modify: `src/mopedzoomd/daemon.py` — `handle_inbound` function (line ~631)
- Test: `tests/test_daemon_on_inbound.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_daemon_on_inbound.py` (add `Stage` and `InteractionKind` to imports at the top):

```python
from mopedzoomd.models import Interaction, InteractionKind, Stage, Task, TaskStatus
```

Add these test functions:

```python
async def test_inbound_reply_to_ref_routes_to_revision_interaction(db, tmp_path):
    # Seed a REVISION interaction with a known posted_to_channel_ref.
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.AWAITING_APPROVAL)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.REVISION,
            prompt="review pre-brief",
            posted_to_channel_ref="tg:-1:0:42",
        )
    )
    # Create scratch dir so append_feedback can write.
    from mopedzoomd.scratch import ScratchDir
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=tid)
    scratch.create()

    router = AsyncMock()
    tm = AsyncMock()
    tm.runs_root = str(tmp_path / "runs")
    channel = AsyncMock()
    channels = {"cli": channel}

    msg = _Inbound(
        channel="cli",
        text="focus more on South India",
        reply_to_ref="tg:-1:0:42",
    )
    await handle_inbound(msg, db=db, router=router, tm=tm, channels=channels, registry={})

    # Interaction resolved, task set to AWAITING_INPUT (feedback given).
    t = await db.get_task(tid)
    assert t.status == TaskStatus.AWAITING_INPUT
    assert len(await db.list_pending_interactions(tid)) == 0
    # Feedback written to scratch.
    assert scratch.read_feedback(0) == ["focus more on South India"]
    # Router not consulted — this is not a new task.
    router.pick.assert_not_called()
    tm.submit_task.assert_not_called()


async def test_inbound_reply_to_ref_no_match_falls_through_to_new_task(db, tmp_path):
    pb = _pb("research", ("research",))
    router = AsyncMock()
    router.pick = AsyncMock(return_value=pb)
    tm = AsyncMock()
    tm.runs_root = str(tmp_path / "runs")
    tm.submit_task = AsyncMock(return_value=99)
    channel = AsyncMock()
    channel.post = AsyncMock(return_value="ref")
    channels = {"cli": channel}

    # reply_to_ref that doesn't match any interaction.
    msg = _Inbound(
        channel="cli",
        text="research cats in india",
        reply_to_ref="tg:-1:0:999",
    )
    await handle_inbound(
        msg, db=db, router=router, tm=tm, channels=channels, registry={"research": pb}
    )

    # Fell through to new task submission.
    tm.submit_task.assert_awaited_once()


async def test_inbound_task_id_takes_priority_over_reply_to_ref(db, tmp_path):
    """topics-mode task_id routing wins over reply_to_ref."""
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="p", inputs={}))
    await db.set_task_status(tid, TaskStatus.AWAITING_APPROVAL)
    await db.insert_interaction(
        Interaction(
            task_id=tid,
            stage_idx=0,
            kind=InteractionKind.APPROVAL,
            prompt="approve?",
            posted_to_channel_ref="tg:-1:0:10",
        )
    )
    router = AsyncMock()
    tm = AsyncMock()
    tm.runs_root = str(tmp_path / "runs")
    channel = AsyncMock()
    channels = {"cli": channel}

    msg = _Inbound(channel="cli", text="approve", task_id=tid, reply_to_ref="tg:-1:0:10")
    await handle_inbound(msg, db=db, router=router, tm=tm, channels=channels, registry={})

    t = await db.get_task(tid)
    assert t.status == TaskStatus.RUNNING
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_daemon_on_inbound.py::test_inbound_reply_to_ref_routes_to_revision_interaction -v
```

Expected: `FAILED` — reply_to_ref is not yet used in handle_inbound

- [ ] **Step 3: Update handle_inbound in daemon.py**

Replace the current `handle_inbound` function:

```python
async def handle_inbound(
    msg,
    *,
    db: StateDB,
    router: Router,
    tm: TaskManager,
    channels: dict[str, Channel],
    registry: dict[str, Playbook],
) -> None:
    """Route an inbound channel message.

    Extracted from `build_daemon_from_config.on_inbound` closure so it's
    reachable from tests without spinning up a full daemon.
    """
    if msg.task_id:
        scratch = ScratchDir(tm.runs_root, msg.task_id)
        await resolve_interaction(db, task_id=msg.task_id, answer=msg.text, scratch=scratch)
        return
    if msg.reply_to_ref:
        interaction = await db.get_interaction_by_ref(msg.reply_to_ref)
        if interaction is not None:
            scratch = ScratchDir(tm.runs_root, interaction.task_id)
            await resolve_interaction(
                db, task_id=interaction.task_id, answer=msg.text, scratch=scratch
            )
            return
    if not msg.text:
        return
    LOG.info("new submission: %s", msg.text[:120])
    pb = await router.pick(msg.text)
    if pb is None:
        LOG.warning("no playbook matched for: %s", msg.text[:80])
        await channels[msg.channel].post(
            OutboundMessage(
                task_id=0,
                body=f"\u26a0\ufe0f No matching playbook for your request. "
                f"Available: {', '.join(registry)}",
            )
        )
        return
    task_id = await tm.submit_task(
        channel=msg.channel,
        user_ref=msg.user_ref,
        text=msg.text,
        playbook=pb,
    )
    if msg.thread_id is not None:
        ch = channels[msg.channel]
        if hasattr(ch, "bind_task_topic"):
            ch.bind_task_topic(
                task_id=task_id,
                thread_id=msg.thread_id,
                playbook_id=pb.id,
                repo="",
            )
    await channels[msg.channel].post(
        OutboundMessage(
            task_id=task_id,
            body=f"\u2705 Task #{task_id} queued \u2014 *{pb.id}*: {pb.summary}",
        )
    )
```

- [ ] **Step 4: Run all inbound tests**

```bash
.venv/bin/pytest tests/test_daemon_on_inbound.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/mopedzoomd/daemon.py tests/test_daemon_on_inbound.py
git commit -m "feat: reply routing in handle_inbound via reply_to_ref"
```

---

### Task 7: _build_prompt feedback and answer injection

**Files:**
- Modify: `src/mopedzoomd/daemon.py` — `_build_prompt` method
- Test: `tests/test_build_prompt_manifest.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_build_prompt_manifest.py`:

```python
def test_build_prompt_injects_answer_when_present(tmp_path):
    tm = _tm(tmp_path)
    stage = StageSpec(name="pre-brief", requires="scope", produces="pre-brief.md", approval="review")
    pb = Playbook(id="research", summary="Research a topic", triggers=["research"], stages=[stage])
    task = Task(id=1, channel="cli", user_ref="u", playbook_id="research", inputs={})
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=1)
    scratch.create()
    scratch.write_answer(0, "south india, 1500 words, evergreen")

    prompt = tm._build_prompt(pb, stage, task, scratch, 0)

    assert "south india, 1500 words, evergreen" in prompt
    assert "User answered your questions" in prompt


def test_build_prompt_injects_feedback_history(tmp_path):
    tm = _tm(tmp_path)
    stage = StageSpec(name="pre-brief", requires="scope", produces="pre-brief.md", approval="review")
    pb = Playbook(id="research", summary="Research a topic", triggers=["research"], stages=[stage])
    task = Task(id=1, channel="cli", user_ref="u", playbook_id="research", inputs={})
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=1)
    scratch.create()
    scratch.append_feedback(0, "add welfare section")
    scratch.append_feedback(0, "cut genetics section")

    prompt = tm._build_prompt(pb, stage, task, scratch, 0)

    assert "add welfare section" in prompt
    assert "cut genetics section" in prompt
    assert "Iteration 1" in prompt
    assert "Iteration 2" in prompt
    assert "User feedback from prior iterations" in prompt


def test_build_prompt_no_injection_when_no_files(tmp_path):
    tm = _tm(tmp_path)
    stage = StageSpec(name="pre-brief", requires="scope", produces="pre-brief.md", approval="none")
    pb = Playbook(id="research", summary="Research a topic", triggers=["research"], stages=[stage])
    task = Task(id=1, channel="cli", user_ref="u", playbook_id="research", inputs={})
    scratch = ScratchDir(str(tmp_path / "runs"), task_id=1)
    scratch.create()

    prompt = tm._build_prompt(pb, stage, task, scratch, 0)

    assert "User answered" not in prompt
    assert "User feedback" not in prompt
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_build_prompt_manifest.py::test_build_prompt_injects_answer_when_present -v
```

Expected: `FAILED` — prompt does not yet contain injection text

- [ ] **Step 3: Update _build_prompt in daemon.py**

The current `_build_prompt` method returns a formatted string. Add feedback/answer injection at the end. Replace the return statement with:

```python
    base = (
        f"Task {task.id} ({pb.summary}).\n"
        f"Stage: {sspec.name}\n"
        f"Goal: {sspec.requires}\n"
        f"Inputs: {json.dumps(task.inputs)}\n"
        f"Prior deliverables: {prior or 'none'}\n"
        f"Working dir: {scratch.dir}\n"
        f"\n"
        f"Produce the following artifact(s) in {scratch.dir}/: {', '.join(produces)}\n"
        f"\n"
        f"IMPORTANT: When done, write a deliverable manifest to:\n"
        f"  {manifest_path}\n"
        f"with this exact JSON structure:\n"
        f'{{"stage": "{sspec.name}", "status": "done", '
        f'"artifacts": [{{"path": "<relative-path>", "kind": "<kind>"}}], '
        f'"notes": "<one-line summary>"}}\n'
        f"\n"
        f"To pause for user input, write {scratch.dir}/question.json with the format "
        f'{{"prompt": "Your question here"}} and exit WITHOUT writing the deliverable manifest. '
        f"Writing question.json means the stage is NOT complete — do not write both.\n"
        f"{research_instruction}"
    )
    suffix = ""
    answer = scratch.read_answer(idx)
    feedbacks = scratch.read_feedback(idx)
    if answer:
        suffix += f"\nUser answered your questions: {answer!r}\n"
    if feedbacks:
        suffix += "\nUser feedback from prior iterations:\n"
        for n, fb in enumerate(feedbacks, 1):
            suffix += f"  - Iteration {n}: {fb!r}\n"
    return base + suffix
```

The full method now looks like (replace the entire `_build_prompt` method):

```python
def _build_prompt(self, pb, sspec, task, scratch: ScratchDir, idx: int) -> str:
    prior = ""
    for i in range(idx):
        mpath = scratch.deliverable_manifest_path(i, pb.stages[i].name)
        if mpath.exists():
            prior += f"\n- {mpath.name}"
    manifest_path = scratch.deliverable_manifest_path(idx, sspec.name)
    produces = sspec.produces if isinstance(sspec.produces, list) else [sspec.produces]
    research_instruction = ""
    if (
        sspec.name == "publish"
        and self.deliverables is not None
        and self.deliverables.research_repo is not None
    ):
        repo = self.deliverables.research_repo
        path = self.deliverables.research_path or "docs/research"
        research_instruction = (
            f"\nCommit the report into repo `{repo}` at path `{path}`.\n"
        )
    base = (
        f"Task {task.id} ({pb.summary}).\n"
        f"Stage: {sspec.name}\n"
        f"Goal: {sspec.requires}\n"
        f"Inputs: {json.dumps(task.inputs)}\n"
        f"Prior deliverables: {prior or 'none'}\n"
        f"Working dir: {scratch.dir}\n"
        f"\n"
        f"Produce the following artifact(s) in {scratch.dir}/: {', '.join(produces)}\n"
        f"\n"
        f"IMPORTANT: When done, write a deliverable manifest to:\n"
        f"  {manifest_path}\n"
        f"with this exact JSON structure:\n"
        f'{{"stage": "{sspec.name}", "status": "done", '
        f'"artifacts": [{{"path": "<relative-path>", "kind": "<kind>"}}], '
        f'"notes": "<one-line summary>"}}\n'
        f"\n"
        f"To pause for user input, write {scratch.dir}/question.json with the format "
        f'{{"prompt": "Your question here"}} and exit WITHOUT writing the deliverable manifest. '
        f"Writing question.json means the stage is NOT complete — do not write both.\n"
        f"{research_instruction}"
    )
    suffix = ""
    answer = scratch.read_answer(idx)
    feedbacks = scratch.read_feedback(idx)
    if answer:
        suffix += f"\nUser answered your questions: {answer!r}\n"
    if feedbacks:
        suffix += "\nUser feedback from prior iterations:\n"
        for n, fb in enumerate(feedbacks, 1):
            suffix += f"  - Iteration {n}: {fb!r}\n"
    return base + suffix
```

- [ ] **Step 4: Run all prompt tests**

```bash
.venv/bin/pytest tests/test_build_prompt_manifest.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/mopedzoomd/daemon.py tests/test_build_prompt_manifest.py
git commit -m "feat: _build_prompt injects user feedback and question answers"
```

---

### Task 8: _await_review method and _run_stage wiring

**Files:**
- Modify: `src/mopedzoomd/daemon.py` — add `_await_review` method to `TaskManager`, update `_run_stage`
- Test: `tests/test_daemon_approval.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_daemon_approval.py`:

```python
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from mopedzoomd.daemon import TaskManager, _RetryStage, _StageFailed
from mopedzoomd.models import Interaction, InteractionKind, Stage, Task, TaskStatus
from mopedzoomd.playbooks import StageSpec
from mopedzoomd.scratch import ScratchDir
from mopedzoomd.state import StateDB


def _make_tm(db, runs_root):
    return TaskManager(
        db=db,
        runs_root=str(runs_root),
        stage_runner=AsyncMock(),
        playbook_registry={},
        channels={},
        worktree_mgr=None,
        agent_discoverer=lambda: [],
    )


def _make_result(artifact_path: str):
    r = AsyncMock()
    r.deliverable = {"artifacts": [{"path": artifact_path, "kind": "markdown"}]}
    return r


async def test_await_review_approve_resolves_and_returns(tmp_path):
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="research", inputs={}))
    await db.insert_stage(Stage(task_id=tid, idx=0, name="pre-brief"))
    await db.set_task_status(tid, TaskStatus.RUNNING)

    runs = tmp_path / "runs"
    runs.mkdir()
    scratch = ScratchDir(str(runs), task_id=tid)
    scratch.create()
    artifact = scratch.dir / "pre-brief.md"
    artifact.write_text("# Pre-brief")

    channel = AsyncMock()
    channel.post = AsyncMock(return_value="tg:-1:0:55")
    tm = _make_tm(db, runs)
    sspec = StageSpec(name="pre-brief", requires="r", produces="pre-brief.md", approval="review")
    result = _make_result("pre-brief.md")

    async def approve_async():
        await asyncio.sleep(0.05)
        pend = await db.list_pending_interactions(tid)
        await db.resolve_interaction(pend[0].id)
        await db.set_task_status(tid, TaskStatus.RUNNING)

    asyncio.create_task(approve_async())
    await tm._await_review(tid, 0, sspec, result, scratch, channel)

    # Document was posted
    posted_msgs = [call.args[0] for call in channel.post.call_args_list]
    assert any(m.document_path == artifact for m in posted_msgs)
    assert (await db.get_task(tid)).status == TaskStatus.RUNNING
    await db.close()


async def test_await_review_feedback_raises_retry_stage(tmp_path):
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="research", inputs={}))
    await db.insert_stage(Stage(task_id=tid, idx=0, name="pre-brief"))
    await db.set_task_status(tid, TaskStatus.RUNNING)

    runs = tmp_path / "runs"
    runs.mkdir()
    scratch = ScratchDir(str(runs), task_id=tid)
    scratch.create()
    (scratch.dir / "pre-brief.md").write_text("# Pre-brief")

    channel = AsyncMock()
    channel.post = AsyncMock(return_value="tg:-1:0:55")
    tm = _make_tm(db, runs)
    sspec = StageSpec(name="pre-brief", requires="r", produces="pre-brief.md", approval="review")
    result = _make_result("pre-brief.md")

    async def give_feedback():
        await asyncio.sleep(0.05)
        pend = await db.list_pending_interactions(tid)
        await db.resolve_interaction(pend[0].id)
        await db.set_task_status(tid, TaskStatus.AWAITING_INPUT)

    asyncio.create_task(give_feedback())
    with pytest.raises(_RetryStage):
        await tm._await_review(tid, 0, sspec, result, scratch, channel)
    await db.close()


async def test_await_review_missing_artifact_raises_stage_failed(tmp_path):
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    tid = await db.insert_task(Task(channel="cli", user_ref="u", playbook_id="research", inputs={}))
    await db.set_task_status(tid, TaskStatus.RUNNING)

    runs = tmp_path / "runs"
    runs.mkdir()
    scratch = ScratchDir(str(runs), task_id=tid)
    scratch.create()
    # No artifact file created on disk.

    channel = AsyncMock()
    channel.post = AsyncMock(return_value="ref")
    tm = _make_tm(db, runs)
    sspec = StageSpec(name="pre-brief", requires="r", produces="pre-brief.md", approval="review")
    result = _make_result("pre-brief.md")

    with pytest.raises(_StageFailed):
        await tm._await_review(tid, 0, sspec, result, scratch, channel)
    assert (await db.get_task(tid)).status == TaskStatus.FAILED
    await db.close()
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_daemon_approval.py::test_await_review_approve_resolves_and_returns -v
```

Expected: `FAILED` — `TaskManager` has no `_await_review`

- [ ] **Step 3: Add _await_review to TaskManager in daemon.py**

Add this method to the `TaskManager` class, after `_await_approval`:

```python
async def _await_review(
    self,
    task_id: int,
    idx: int,
    sspec,
    result: StageResult,
    scratch: ScratchDir,
    channel: Channel,
) -> None:
    manifest = result.deliverable or {}
    artifacts = manifest.get("artifacts", [])
    doc_path = None
    for art in artifacts:
        candidate = scratch.dir / art.get("path", "")
        if candidate.exists():
            doc_path = candidate
            break
    if doc_path is None:
        await self.db.set_task_status(task_id, TaskStatus.FAILED)
        await channel.post(
            OutboundMessage(
                task_id=task_id,
                body=f"Stage {sspec.name}: no deliverable artifact found for review",
            )
        )
        raise _StageFailed()
    ref = await channel.post(
        OutboundMessage(
            task_id=task_id,
            body=f"\U0001f4c4 {sspec.name} \u2014 reply with feedback, or click Approve",
            buttons=[ApprovalButton("approve", "\u2705 Approve")],
            document_path=doc_path,
        )
    )
    await self.db.insert_interaction(
        Interaction(
            task_id=task_id,
            stage_idx=idx,
            kind=InteractionKind.REVISION,
            prompt=f"Review {sspec.name}",
            posted_to_channel_ref=ref,
        )
    )
    await self.db.set_task_status(task_id, TaskStatus.AWAITING_APPROVAL)
    while True:
        pend = await self.db.list_pending_interactions(task_id)
        if not pend:
            break
        await asyncio.sleep(0.2)
    t = await self.db.get_task(task_id)
    if t.status == TaskStatus.RUNNING:
        return
    if t.status == TaskStatus.AWAITING_INPUT:
        raise _RetryStage()
    if t.status == TaskStatus.CANCELLED:
        raise RuntimeError("task cancelled by user")
```

- [ ] **Step 4: Wire _await_review into _run_stage**

In `_run_stage`, find the approval check at line ~451:

```python
if sspec.approval in ("required", "on-completion"):
    await self._await_approval(task_id, idx, sspec, result, channel)
```

Replace with:

```python
if sspec.approval in ("required", "on-completion"):
    await self._await_approval(task_id, idx, sspec, result, channel)
elif sspec.approval == "review":
    await self._await_review(task_id, idx, sspec, result, scratch, channel)
```

- [ ] **Step 5: Run all approval tests**

```bash
.venv/bin/pytest tests/test_daemon_approval.py -v
```

Expected: all tests PASS

- [ ] **Step 6: Run the full test suite**

```bash
.venv/bin/pytest tests/ -x -q
```

Expected: all tests pass (190+ passing)

- [ ] **Step 7: Commit**

```bash
git add src/mopedzoomd/daemon.py tests/test_daemon_approval.py
git commit -m "feat: _await_review revision loop for approval: review stages"
```
