# Integration Tests Design — 2026-04-20

## Context

The existing test suite has one E2E integration test (`tests/integration/test_end_to_end.py`) covering the happy-path research task via CLI socket channel. Five meaningful flows have no end-to-end coverage:

1. Approval gate: task pauses at AWAITING_APPROVAL, button click resumes it, task reaches DELIVERED
2. Question gate: agent writes `question.json`, task pauses at AWAITING_INPUT, answer injected, stage retries and completes
3. Stage failure: agent writes no manifest (or exits non-zero), task ends FAILED with correct event logged
4. CLI socket op round-trip: real socket send/receive for `status`, `cancel`, `resume` ops through `CLISocketChannel` + `TaskManager`
5. Telegram channel adapter: inbound update → `handle_inbound` → ack posted back, no real HTTP calls

## Approach

Option B: two focused integration modules with clean separation between daemon lifecycle concerns and Telegram adapter concerns.

## File Structure

```
tests/integration/
  conftest.py                  # existing + new fake_claude_variant factory
  test_end_to_end.py           # existing, unchanged
  test_lifecycle.py            # NEW: gaps 1–4
  test_telegram_channel.py     # NEW: gap 5
```

## Fixtures

### `fake_claude_variant(script)` — added to `tests/integration/conftest.py`

A factory fixture that accepts a custom bash script body string and installs it as the `claude` binary on PATH. Allows each test to control exactly what the fake agent does (write deliverable, write question.json, exit non-zero, sleep, etc.) without separate files.

```python
@pytest.fixture
def fake_claude_variant(tmp_path, monkeypatch):
    def _make(script_body: str) -> Path:
        p = tmp_path / "claude"
        p.write_text("#!/usr/bin/env bash\n" + script_body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
        return p
    return _make
```

### `fake_telegram` — local to `test_telegram_channel.py`

Stubs `python-telegram-bot` internals so no HTTP calls are made:

- `FakeBot`: async methods (`send_message`, `create_forum_topic`, `pin_message`, `answer_callback_query`, `close_forum_topic`) that record calls to a list and return canned `MagicMock` responses with realistic fields (`message_id=1`, `message_thread_id=42`).
- `FakeApplication`: wraps `FakeBot`; exposes an `asyncio.Queue` for injecting `Update` objects. Handlers registered on the real `TelegramChannel` are wired to process updates from this queue.
- Fixture starts the channel, yields `(channel, fake_app)`, stops the channel on teardown.

## `test_lifecycle.py` — Four Tests

### Test 1: `test_approval_gate_full_cycle`

**Setup:**
- Playbook with 2 stages: stage 0 `approval="none"`, stage 1 `approval="required"`
- `fake_claude_variant` writes valid deliverables for both stages

**Flow:**
- Insert task, run `tm.run_task(tid)` as a background asyncio task
- After stage 1 completes, daemon posts approval buttons and waits (task status: AWAITING_APPROVAL)
- Background coroutine polls DB until AWAITING_APPROVAL, then calls `resolve_interaction(db, tid, answer="approve")`
- `run_task` resumes, task reaches DELIVERED

**Assertions:**
- `task.status == DELIVERED`
- DB contains an `approval_requested` event and an `interaction_resolved` event
- Stage 1 row status is DONE

---

### Test 2: `test_question_gate_full_cycle`

**Setup:**
- Playbook with 1 stage, `approval="none"`
- `fake_claude_variant` script: checks for a sentinel file `$MOPEDZOOM_SCRATCH/first_run_done`; if absent, writes `question.json`, creates the sentinel, exits 0 with no deliverable; on second invocation (sentinel present) writes the deliverable

**Flow:**
- Insert task, run `tm.run_task(tid)` as background task
- Daemon detects `question.json`, posts question to channel, sets AWAITING_INPUT
- Background coroutine polls until AWAITING_INPUT, calls `resolve_interaction(db, tid, answer="Paris")`
- Stage retries, second fake_claude invocation writes deliverable, task reaches DELIVERED

**Assertions:**
- `task.status == DELIVERED`
- DB contains `question_posted` event and `interaction_resolved` event with `answer="Paris"`

---

### Test 3: `test_stage_failure_no_manifest`

**Setup:**
- Playbook with 1 stage
- `fake_claude_variant` exits 0 but writes no deliverable manifest

**Flow:**
- Insert task, `await tm.run_task(tid)` (blocking)

**Assertions:**
- `task.status == FAILED`
- DB contains a `stage_failed` event
- Stage row status is FAILED

---

### Test 4: `test_cli_socket_ops_round_trip`

**Setup:**
- Real `CLISocketChannel` + `TaskManager` with `build_cli_op_handler` wired
- Two tasks inserted (QUEUED)

**Flow:**
- Send `{"op": "tasks"}` via raw socket → assert response lists both tasks
- Send `{"op": "status", "id": 1}` → assert response has `status` field
- Send `{"op": "cancel", "id": 1}` → assert `ok: true`
- Query DB directly → assert task 1 is CANCELLED

**Assertions:**
- All socket responses are valid JSON with `ack: true`
- DB state matches socket responses

---

## `test_telegram_channel.py` — Three Tests

### Test 1: `test_telegram_inbound_text_submits_task`

**Setup:** `fake_telegram` fixture; real `StateDB`; `handle_inbound` wired with a real `Router` (deterministic trigger matching, no LLM)

**Flow:**
- Push a `Update` with `message.text = "research AI trends"` into `fake_app.update_queue`
- `FakeBot.send_message` sets an `asyncio.Event`; test awaits it with `asyncio.wait_for(..., timeout=2.0)`

**Assertions:**
- One task row exists in DB
- `FakeBot.send_message` called with body containing task id
- Task status is QUEUED (not yet running — submit is fire-and-forget)

---

### Test 2: `test_telegram_approval_button_resolves_interaction`

**Setup:** `fake_telegram`; task + AWAITING_APPROVAL interaction pre-inserted in DB

**Flow:**
- Push a `CallbackQuery` update with `callback_data = "1:approve"`
- `FakeBot.answer_callback_query` sets an `asyncio.Event`; test awaits it with `asyncio.wait_for(..., timeout=2.0)`

**Assertions:**
- `FakeBot.answer_callback_query` called
- Interaction row in DB updated (answer = "approve")
- `interaction_resolved` event logged

---

### Test 3: `test_telegram_post_creates_topic_and_sends_message`

**Setup:** `fake_telegram` in topics mode; channel not yet bound to a task

**Flow:**
- Call `await channel.post(OutboundMessage(task_id=1, body="Stage done"))` directly

**Assertions:**
- `FakeBot.create_forum_topic` called with a title containing the task id (first post for this task creates the topic)
- `FakeBot.send_message` called with `text="Stage done"` and correct `message_thread_id`
- Channel's internal topic mapping has task_id → thread_id entry

---

## Non-Goals

- No real `claude -p` subprocess calls (all tests use fake_claude_variant)
- No live Telegram API calls (FakeBot intercepts all)
- No testing of LLM output quality — only plumbing correctness
- No multi-process or cross-machine scenarios

## Success Criteria

- All 7 new tests pass in CI with `python3 -m pytest tests/integration/ -q`
- No new pytest plugins required
- Existing 168 tests remain green
- Each test is self-contained and deterministic (no sleep-based polling — use asyncio event / short-timeout retry loops)
