# Playbook Editor UI — Design Spec

**Date:** 2026-04-20
**Status:** Approved

## Overview

Add inline playbook editing to the mopedzoom local dashboard. Users can expand any playbook row on the `/playbooks` page to edit its summary, triggers, and stage configuration (name, prompt, produces, approval). Edits are saved to the user override directory and hot-reloaded into the running daemon immediately, with no restart required.

---

## Scope

**In scope:**
- Inline edit form on the `/playbooks` page (expand row in-place)
- Editable playbook fields: `summary`, `triggers`
- Editable stage fields per stage: `name`, `requires` (prompt), `produces`, `approval`
- Add new stages and remove existing stages
- Save writes YAML to user override dir; hot-reloads shared in-memory registry

**Out of scope:**
- Submitting new tasks from the UI
- Editing `requires_worktree`, `permission_mode`, `inputs` spec, or stage `agent`/`timeout` fields
- Creating entirely new playbooks from scratch via the UI

---

## Architecture

### Registry hot-reload

The dashboard and daemon run in the same process and currently share a `playbook_registry` dict — but `main()` passes a **copy** to `create_app`. This must be fixed: pass the original reference so in-place mutations in the dashboard endpoints are immediately visible to `TaskManager`.

**Change in `daemon.py` `main()`:**
```python
# Before (broken for hot-reload):
fastapi_app = create_app(
    db=daemon.db,
    playbook_registry={k: v for k, v in daemon.task_mgr.playbook_registry.items()},
    ...
)

# After:
fastapi_app = create_app(
    db=daemon.db,
    playbook_registry=daemon.task_mgr.playbook_registry,  # same reference
    ...
)
```

### Save location

Edits always write to `~/.mopedzoom/playbooks/{pb_id}.yaml` (the user override directory). This means:
- Built-in playbooks are never mutated in source.
- Editing a built-in creates a user override that supersedes it (consistent with how `load_playbooks` already works).
- Editing a user playbook overwrites it in place.

### `create_app` signature change

```python
def create_app(
    db: StateDB,
    playbook_registry: dict[str, Playbook],
    agent_discoverer: Callable[[], list[str]] | None = None,
    user_playbooks_dir: Path | None = None,   # NEW
) -> FastAPI:
```

`user_playbooks_dir` defaults to `Path.home() / ".mopedzoom" / "playbooks"` if not passed.

---

## New Endpoints

### `GET /playbooks/{pb_id}/edit-form`
Returns an HTML fragment: a `<tr>` with `colspan=5` containing the edit form. Replaces the existing row via htmx.

### `GET /playbooks/{pb_id}/row`
Returns an HTML fragment: the plain summary `<tr>` for this playbook. Used by Cancel to restore the row without a page reload.

### `POST /playbooks/{pb_id}`
Accepts `application/x-www-form-urlencoded` form data. Validates, writes YAML, hot-reloads, returns the updated plain `<tr>` row on success, or the edit form with inline errors on failure.

**Form field names:**
| Field | Name |
|---|---|
| Playbook summary | `summary` |
| Triggers | `triggers` (comma-separated string) |
| Stage name | `stage_{i}_name` |
| Stage requires | `stage_{i}_requires` |
| Stage produces | `stage_{i}_produces` |
| Stage approval | `stage_{i}_approval` |

Stages are submitted in index order. Indices may be non-contiguous (from JS add/remove); the server re-indexes them 0..N-1 on save.

---

## Validation Rules

- `summary`: non-empty after strip
- `triggers`: zero or more; each trigger stripped and lowercased; empty strings filtered
- At least one stage required
- Each stage: `name` non-empty, `requires` non-empty, `produces` non-empty
- `approval` must be one of: `required`, `on-completion`, `on-failure`, `none`
- `produces`: stored as a single string. If the existing stage had a list value, it is joined with `, ` for display and split by `,` on save (trimming whitespace). This covers all current built-in playbooks which use single-string produces values.

Validation failures re-render the edit form fragment with a red error banner at the top. No page navigation occurs.

---

## Template Changes

### Modified: `playbooks.html`
- Add an "Edit" `<th>` column header
- Each `<tr>` gets an `id="pb-row-{pb_id}"` attribute for htmx targeting
- Each row gets an Edit button wired: `hx-get="/playbooks/{pb_id}/edit-form"` `hx-target="closest tr"` `hx-swap="outerHTML"`

### New: `fragment_playbook_row.html`
Plain `<tr>` for one playbook — rendered after save or cancel.

### New: `fragment_playbook_edit.html`
Inline edit form `<tr colspan=5>`. Contains:

**Playbook-level section:**
- Summary: `<input name="summary">`
- Triggers: `<input name="triggers">` (comma-separated, hint text)

**Stages section (one card per stage):**
- Stage name: `<input name="stage_{i}_name">` (monospace, editable)
- Requires: `<textarea name="stage_{i}_requires" rows="2">`
- Produces: `<input name="stage_{i}_produces">`
- Approval: `<select name="stage_{i}_approval">` with options required / on-completion / on-failure / none
- Remove button (✕): client-side JS removes the card from the DOM and re-indexes remaining cards

**"+ Add Stage" button:** client-side JS appends a blank stage card using an inline JS template; new stages get the next available index.

**Action buttons:**
- Cancel: `hx-get="/playbooks/{pb_id}/row"` `hx-target="closest tr"` `hx-swap="outerHTML"`
- Save & Reload: `hx-post="/playbooks/{pb_id}"` `hx-target="closest tr"` `hx-swap="outerHTML"` `hx-include="closest tr"`

---

## Client-side JS (inlined in `fragment_playbook_edit.html`)

Two small functions, no external dependencies:

**`addStage()`** — appends a blank stage card with the next index. Reads current max index from existing cards to compute next index.

**`removeStage(btn)`** — removes the card containing the button. Does not re-index (server re-indexes on save).

Both functions are defined inline in the edit fragment so they are only present when the form is open.

---

## Data Flow: Save

```
User clicks "Save & Reload"
  → POST /playbooks/{pb_id}
  → Parse + validate form fields
  → Build Playbook object (re-index stages 0..N-1)
  → Serialize to YAML via yaml.dump(pb.model_dump())
  → Write to user_playbooks_dir / f"{pb_id}.yaml"
  → registry[pb_id] = pb   ← hot-reload (in-place mutation)
  → Return fragment_playbook_row.html for this playbook
  → htmx swaps the edit row back to a summary row
```

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Validation failure | Re-render edit form with red error banner, no save |
| Disk write failure | Re-render edit form with error banner showing reason |
| Unknown pb_id | 404 JSON response |
| Concurrent edits | Last write wins (single-user local tool, no locking needed) |

---

## Files Changed

```
src/mopedzoomd/
  dashboard/
    app.py                          — new endpoints, updated create_app signature
    templates/
      playbooks.html                — add Edit button column + htmx attrs
      fragment_playbook_row.html    — NEW: single summary row fragment
      fragment_playbook_edit.html   — NEW: inline edit form fragment
  daemon.py                         — fix registry reference, pass user_playbooks_dir
```

No changes to `models.py`, `playbooks.py`, `state.py`, channels, or stage runner.
