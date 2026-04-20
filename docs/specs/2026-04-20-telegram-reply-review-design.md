# Telegram Reply Routing & Stage Revision Loop — Design Spec

**Date:** 2026-04-20
**Status:** Approved

## Overview

Two tightly coupled features that make Telegram the primary collaboration surface for task review:

1. **Reply routing** — any reply to a bot message is matched to its task via `reply_to_ref` → `posted_to_channel_ref`, regardless of Telegram mode (topics, header, flat).
2. **`approval: review`** — a new stage approval mode where the daemon posts the stage deliverable to Telegram, accepts free-text feedback, reruns the stage with feedback injected into the prompt, and repeats until the user explicitly approves.

---

## Scope

**In scope:**
- Reply routing: match inbound `reply_to_ref` against `pending_interactions.posted_to_channel_ref`
- New `approval: review` value on `StageSpec`
- Daemon posts stage deliverable as a file attachment when a review stage completes
- Free-text reply = feedback → appended to scratch, injected into prompt on retry
- [✓ Approve] inline button = stage done
- Fix existing question.json answer drop: write user's answer to scratch, inject into prompt on retry

**Out of scope:**
- Multi-file deliverable review (only the first artifact is posted)
- Editing or deleting the Telegram message after revision
- Review loop on non-Telegram channels (CLI channel ignores `review`, behaves as `required`)

---

## Architecture

### Reply Routing

`InboundMessage.reply_to_ref` is already populated by `telegram.py` when a message is a reply. It contains the ref of the message being replied to in format `tg:{chat_id}:{thread_id}:{message_id}`.

`pending_interactions.posted_to_channel_ref` stores the ref of the bot message that created the interaction, in the same format.

**Change in `handle_inbound`:** Before routing as a new task, check if `msg.reply_to_ref` matches any `posted_to_channel_ref` in `pending_interactions`. If so, the message is an interaction response. Derive `task_id` and `interaction_id` from the matched row.

**New `StateDB` method:**
```python
async def get_interaction_by_ref(self, ref: str) -> Interaction | None:
    r = await self.fetch_one(
        "SELECT * FROM pending_interactions WHERE posted_to_channel_ref=?", (ref,)
    )
    return _row_to_int(r) if r else None
```

This composes with existing topic-mode routing: if `msg.task_id` is already set (topics mode), skip the ref lookup. Only fall through to ref lookup when `task_id` is None.

### `approval: review` Stage Mode

`StageSpec.approval` gains a new literal value: `"review"`.

When a stage with `approval: review` completes (deliverable manifest written, exit code 0):

1. Read the deliverable manifest → locate the first artifact file.
2. Post the artifact to Telegram as a file attachment via `bot.send_document()`, with caption: `"📄 {stage_name} — reply with feedback, or click Approve"` and an **[✓ Approve]** inline button.
3. Insert a `pending_interaction` with `kind=REVISION` and `posted_to_channel_ref` = the sent message's ref.
4. Enter wait loop (same polling structure as existing approval wait).

**While waiting:**
- Incoming reply matched via reply routing → text = feedback → `append_feedback(idx, text)` → `resolve_interaction(answer="feedback")` → sets task status to `AWAITING_INPUT` → polling loop exits → `_await_approval` sees `AWAITING_INPUT` → `raise _RetryStage()`
- Inline button callback `"approve"` → `resolve_interaction(answer="approve")` → sets task status to `RUNNING` → polling loop exits → `_await_approval` sees `RUNNING` → return (stage done)

This reuses the existing status-based signaling pattern already present in `resolve_interaction` and `_await_approval`.

**On `_RetryStage`:** Stage reruns. `_build_prompt` reads the feedback file and injects history. Agent produces a revised deliverable. Daemon posts the new version and a new [✓ Approve] button. Loop continues.

### `OutboundMessage` Extension

```python
@dataclass
class OutboundMessage:
    body: str
    buttons: list[ApprovalButton] = field(default_factory=list)
    task_id: int | None = None
    channel_ref: str | None = None
    document_path: Path | None = None   # NEW: if set, post as file attachment
```

`TelegramChannel.post()` checks `document_path`: if set, calls `bot.send_document(document=document_path, caption=body, reply_markup=kb)` instead of `send_message`.

### Feedback & Answer Injection

**Scratch files written by the daemon (never by the agent):**

`{idx}-{name}-feedback.json` — appended on each revision cycle:
```json
{"feedbacks": ["Focus more on South India", "Cut the genetics section"]}
```

`{idx}-{name}-answer.json` — written once when user answers a `question.json`:
```json
{"answer": "e, all-india, personal curiosity, 1500 words, evergreen"}
```

**`_build_prompt` change:** After building the base prompt, check for both files for the current stage. If present, append:

```
User answered your questions: "e, all-india, personal curiosity, 1500 words, evergreen"

User feedback from prior iterations:
  - Iteration 1: "Focus more on South India"
  - Iteration 2: "Cut the genetics section"
```

The agent sees the full history on every retry. No agent-side changes required.

**Fix for existing question.json flow:** Currently, when a user answers a question, the answer is discarded. New behavior: `handle_inbound` writes the answer text to `{idx}-{name}-answer.json` before calling `resolve_interaction`. On the next `_RetryStage` run, `_build_prompt` injects it.

### `resolve_interaction` Extension

`resolve_interaction(db, task_id, answer)` gains a `scratch` parameter. For `kind=QUESTION` or `kind=REVISION` interactions resolved with free text, it writes the answer/feedback to the appropriate scratch file before deleting the interaction row.

---

## Data Flow

```
Stage runs (approval: review)
  → writes deliverable to scratch dir
  → daemon reads manifest → locates first artifact (e.g. pre-brief.md)
  → daemon posts artifact as file to Telegram + [✓ Approve] button
  → pending_interaction created (kind=REVISION, posted_to_channel_ref=ref)
  → daemon enters wait loop

User replies to that message:
  → reply_to_ref matches posted_to_channel_ref → task_id + interaction_id derived
  → feedback appended to {idx}-{name}-feedback.json in scratch
  → pending_interaction resolved → _RetryStage raised

Stage reruns:
  → _build_prompt reads feedback file → injects iteration history into prompt
  → agent produces revised deliverable
  → daemon posts revised version + new [✓ Approve] button
  → loop repeats

User clicks [✓ Approve]:
  → pending_interaction resolved
  → stage marked DONE → next stage begins
```

---

## Validation Rules

- `review` is only meaningful for stages with a deliverable. If a stage with `approval: review` exits with no deliverable, it falls back to failing the stage (same as `required`).
- If the deliverable artifact file does not exist on disk, the daemon logs an error and fails the stage rather than posting an empty message.
- If Telegram `send_document` fails (network error), the daemon retries once then fails the stage.
- The CLI channel treats `review` identically to `required` (no file posting, no loop — just an approval prompt on stdout).

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Reply matches no interaction | Fall through to new-task routing |
| Deliverable file missing at review time | Stage fails with log error |
| User sends non-text (e.g. sticker) as reply | Ignored (no text → no feedback → interaction stays pending) |
| Stage fails on a revision retry | Stage marked FAILED, task stops |
| User never approves | Task stays AWAITING_APPROVAL indefinitely (existing timeout/cancel mechanisms apply) |

---

## Files Changed

```
src/mopedzoomd/
  channels/base.py       — OutboundMessage: add document_path: Path | None
  channels/telegram.py   — post(): send_document() when document_path is set
  playbooks.py           — StageSpec.approval: add "review" literal
  scratch.py             — append_feedback(idx, name, text)
                         — read_feedback(idx, name) -> list[str]
                         — write_answer(idx, name, text)
                         — read_answer(idx, name) -> str | None
  state.py               — get_interaction_by_ref(ref) -> Interaction | None
  daemon.py              — handle_inbound: reply routing via reply_to_ref
                         — _build_prompt: inject feedback + answer from scratch
                         — _await_approval: review branch (post deliverable, loop)
                         — resolve_interaction: write answer/feedback to scratch
```

No changes to `models.py`, `stage_runner.py`, or `router.py`.
