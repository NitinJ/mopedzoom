# Telegram Topic Routing — 2026-04-20

## Problem

When a user sends a task request inside a Telegram forum topic (e.g., "Research"), all subsequent bot communication for that task (ack, stage updates, questions, approval buttons, deliverable) is posted with no `message_thread_id`, landing in the general chat instead of the originating topic.

## Goal

If the original task request was sent inside a Telegram topic, all bot responses for that task are posted into that same topic. If sent in general chat, responses stay in general chat. No other behavior changes.

## Approach

Option A: thread_id on InboundMessage + bind in handle_inbound.

## Changes

### `src/mopedzoomd/channels/base.py`

Add one field to `InboundMessage`:

```python
thread_id: int | None = None  # Telegram forum topic thread_id, if applicable
```

### `src/mopedzoomd/channels/telegram.py`

In `_on_message`, populate the new field:

```python
inbound = InboundMessage(
    channel="telegram",
    user_ref=f"chat:{msg.chat_id}",
    text=msg.text or "",
    reply_to_ref=...,
    raw={},
    task_id=task_id,
    thread_id=msg.message_thread_id,   # NEW
)
```

### `src/mopedzoomd/daemon.py`

In `handle_inbound`, after `submit_task` and before posting the ack, bind the topic if thread_id is present:

```python
task_id = await tm.submit_task(...)

# Bind originating topic so all subsequent posts go to the right thread.
if msg.thread_id is not None:
    ch = channels[msg.channel]
    if hasattr(ch, "bind_task_topic"):
        ch.bind_task_topic(
            task_id=task_id,
            thread_id=msg.thread_id,
            playbook_id=pb.id,
            repo="",
        )

await channels[msg.channel].post(OutboundMessage(task_id=task_id, body="✅ ..."))
```

`TelegramChannel.post()` already reads `_topics[task_id].thread_id` when building the `send_message` call — no changes required there.

## Edge Cases

| Scenario | Behaviour |
|---|---|
| Message in general chat (no thread_id) | `InboundMessage.thread_id = None`; `bind_task_topic` not called; `post()` sends without `message_thread_id` → general chat |
| Callback query (approval button) | Carries `task_id`; routed to `resolve_interaction`; topic already bound from original message; no change needed |
| Non-Telegram channels (CLI) | `InboundMessage.thread_id` defaults to `None`; `hasattr(ch, "bind_task_topic")` guard skips binding |
| `repo` field in `_TopicBinding` | Only used by `_format_header` in non-topics mode; topics mode always returns `""`; passing `repo=""` is safe |

## Non-Goals

- Persisting topic bindings across daemon restarts
- Auto-creating new topics for tasks that originate from general chat
- Any changes to the CLI channel

## Files Changed

| File | Change |
|---|---|
| `src/mopedzoomd/channels/base.py` | +1 field on `InboundMessage` |
| `src/mopedzoomd/channels/telegram.py` | +1 kwarg in `_on_message` |
| `src/mopedzoomd/daemon.py` | +5 lines in `handle_inbound` |

## Testing

Update `tests/integration/test_telegram_channel.py::test_telegram_inbound_text_submits_task` to assert that when the inbound update carries a `message_thread_id`, the channel's `_topics` dict has an entry for the new task with the correct `thread_id`.
