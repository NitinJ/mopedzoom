# Playbook Editor UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add inline playbook editing to the `/playbooks` dashboard page — users can expand any row to edit summary, triggers, and per-stage fields (name, prompt, produces, approval), add/remove stages, and save with immediate hot-reload into the running daemon.

**Architecture:** Three new FastAPI endpoints handle the htmx-driven edit lifecycle (row fragment, edit form fragment, POST save). The save handler validates form data, writes the updated YAML to the user override dir (`~/.mopedzoom/playbooks/`), then mutates the shared `playbook_registry` dict in-place — which is the same object held by `TaskManager`, so changes take effect immediately without restart. A one-line fix in `daemon.py` is required first to ensure the registry is passed by reference rather than copied.

**Tech Stack:** FastAPI, Jinja2, htmx 2.0 (already loaded in `base.html`), PyYAML, pytest + httpx for tests.

---

## File Map

| Action | Path |
|---|---|
| Modify | `src/mopedzoomd/dashboard/app.py` |
| Modify | `src/mopedzoomd/daemon.py` |
| Modify | `src/mopedzoomd/dashboard/templates/playbooks.html` |
| Create | `src/mopedzoomd/dashboard/templates/fragment_playbook_row.html` |
| Create | `src/mopedzoomd/dashboard/templates/fragment_playbook_edit.html` |
| Create | `tests/test_dashboard_editor.py` |

---

## Task 1: Fix registry reference in daemon.py + add user_playbooks_dir to create_app

The registry is currently copied when passed to `create_app`, breaking hot-reload. This task fixes that and adds the `user_playbooks_dir` parameter to `create_app` so the save endpoint knows where to write YAMLs.

**Files:**
- Modify: `src/mopedzoomd/dashboard/app.py`
- Modify: `src/mopedzoomd/daemon.py`
- Create: `tests/test_dashboard_editor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_editor.py`:

```python
import pytest
import pytest_asyncio
from pathlib import Path
from httpx import AsyncClient, ASGITransport

from mopedzoomd.dashboard.app import create_app
from mopedzoomd.playbooks import Playbook, StageSpec
from mopedzoomd.state import StateDB


def _sample_playbook(pb_id: str = "research") -> Playbook:
    return Playbook(
        id=pb_id,
        summary="Research a topic",
        triggers=["research", "investigate"],
        stages=[
            StageSpec(name="pre-brief", requires="Scope the research", produces="pre-brief.md", approval="required"),
            StageSpec(name="research", requires="Do the research", produces="report.md", approval="on-completion"),
        ],
    )


@pytest_asyncio.fixture
async def editor_client(tmp_path):
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    reg = {"research": _sample_playbook()}
    app = create_app(db, playbook_registry=reg, user_playbooks_dir=tmp_path / "playbooks")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, reg, tmp_path
    await db.close()


@pytest.mark.asyncio
async def test_create_app_accepts_user_playbooks_dir(tmp_path):
    """create_app must accept user_playbooks_dir without error."""
    db = StateDB(str(tmp_path / "s.db"))
    await db.connect()
    await db.migrate()
    app = create_app(db, user_playbooks_dir=tmp_path / "playbooks")
    assert app is not None
    await db.close()
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /home/nitin/workspace/mopedzoom && python -m pytest tests/test_dashboard_editor.py::test_create_app_accepts_user_playbooks_dir -v
```

Expected: FAIL — `create_app() got an unexpected keyword argument 'user_playbooks_dir'`

- [ ] **Step 3: Update create_app signature in app.py**

In `src/mopedzoomd/dashboard/app.py`, update the imports and function signature:

```python
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..playbooks import Playbook
from ..state import StateDB

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(
    db: StateDB,
    playbook_registry: dict[str, Playbook] | None = None,
    agent_discoverer: Callable[[], list[str]] | None = None,
    user_playbooks_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI()
    registry = playbook_registry or {}
    discover = agent_discoverer or (lambda: [])
    user_dir = user_playbooks_dir or (Path.home() / ".mopedzoom" / "playbooks")
```

Keep all existing endpoints (`/`, `/tasks/{tid}`, `/fragments/tasks`, `/agents`, `/playbooks`, `/health`) unchanged beneath this.

- [ ] **Step 4: Fix registry reference in daemon.py**

In `src/mopedzoomd/daemon.py`, inside the `main()` function, change the `create_app` call:

```python
    # Before (breaks hot-reload — creates a new dict):
    fastapi_app = create_app(
        db=daemon.db,
        playbook_registry={k: v for k, v in daemon.task_mgr.playbook_registry.items()},
        agent_discoverer=daemon.task_mgr.agent_discoverer,
    )

    # After (same reference — mutations in endpoints are visible to TaskManager):
    fastapi_app = create_app(
        db=daemon.db,
        playbook_registry=daemon.task_mgr.playbook_registry,
        agent_discoverer=daemon.task_mgr.agent_discoverer,
        user_playbooks_dir=state_root / "playbooks",
    )
```

- [ ] **Step 5: Run test to confirm it passes**

```bash
cd /home/nitin/workspace/mopedzoom && python -m pytest tests/test_dashboard_editor.py::test_create_app_accepts_user_playbooks_dir -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /home/nitin/workspace/mopedzoom && git add src/mopedzoomd/dashboard/app.py src/mopedzoomd/daemon.py tests/test_dashboard_editor.py && git commit -m "feat: add user_playbooks_dir param to create_app, fix registry reference for hot-reload"
```

---

## Task 2: Plain row fragment — GET /playbooks/{pb_id}/row

This endpoint returns a single `<tr>` for a playbook. The Cancel button in the edit form uses it to restore the row without a page reload.

**Files:**
- Modify: `src/mopedzoomd/dashboard/app.py`
- Create: `src/mopedzoomd/dashboard/templates/fragment_playbook_row.html`
- Modify: `tests/test_dashboard_editor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard_editor.py`:

```python
@pytest.mark.asyncio
async def test_get_row_returns_200(editor_client):
    c, reg, _ = editor_client
    r = await c.get("/playbooks/research/row")
    assert r.status_code == 200
    assert "research" in r.text
    assert "Research a topic" in r.text


@pytest.mark.asyncio
async def test_get_row_unknown_returns_404(editor_client):
    c, _, _ = editor_client
    r = await c.get("/playbooks/nonexistent/row")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/nitin/workspace/mopedzoom && python -m pytest tests/test_dashboard_editor.py::test_get_row_returns_200 tests/test_dashboard_editor.py::test_get_row_unknown_returns_404 -v
```

Expected: FAIL — 404/422 (route not found)

- [ ] **Step 3: Create fragment_playbook_row.html**

Create `src/mopedzoomd/dashboard/templates/fragment_playbook_row.html`:

```html
<tr id="pb-row-{{pb.id}}">
  <td><code>{{pb.id}}</code></td>
  <td>{{pb.summary}}</td>
  <td>{{pb.triggers|join(", ")}}</td>
  <td>{{pb.stages|length}}</td>
  <td>
    <button hx-get="/playbooks/{{pb.id}}/edit-form"
            hx-target="closest tr"
            hx-swap="outerHTML"
            style="font-size:0.75rem;padding:3px 10px;background:#1e3a5f;border:1px solid #3b82f6;color:#93c5fd;border-radius:4px;cursor:pointer">Edit</button>
  </td>
</tr>
```

- [ ] **Step 4: Add the endpoint to app.py**

Inside `create_app`, after the `/playbooks` GET endpoint, add:

```python
    @app.get("/playbooks/{pb_id}/row", response_class=HTMLResponse)
    async def playbook_row(pb_id: str, req: Request):
        pb = registry.get(pb_id)
        if pb is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return TEMPLATES.TemplateResponse(req, "fragment_playbook_row.html", {"pb": pb})
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd /home/nitin/workspace/mopedzoom && python -m pytest tests/test_dashboard_editor.py::test_get_row_returns_200 tests/test_dashboard_editor.py::test_get_row_unknown_returns_404 -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /home/nitin/workspace/mopedzoom && git add src/mopedzoomd/dashboard/app.py src/mopedzoomd/dashboard/templates/fragment_playbook_row.html tests/test_dashboard_editor.py && git commit -m "feat: add GET /playbooks/{id}/row endpoint and row fragment template"
```

---

## Task 3: Edit form fragment — GET /playbooks/{pb_id}/edit-form

Returns the inline edit form with all current playbook values pre-filled. Includes client-side JS for adding/removing stages.

**Files:**
- Modify: `src/mopedzoomd/dashboard/app.py`
- Create: `src/mopedzoomd/dashboard/templates/fragment_playbook_edit.html`
- Modify: `tests/test_dashboard_editor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard_editor.py`:

```python
@pytest.mark.asyncio
async def test_get_edit_form_returns_200(editor_client):
    c, _, _ = editor_client
    r = await c.get("/playbooks/research/edit-form")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_get_edit_form_prefills_summary(editor_client):
    c, _, _ = editor_client
    r = await c.get("/playbooks/research/edit-form")
    assert "Research a topic" in r.text


@pytest.mark.asyncio
async def test_get_edit_form_contains_stage_fields(editor_client):
    c, _, _ = editor_client
    r = await c.get("/playbooks/research/edit-form")
    assert "stage_0_requires" in r.text
    assert "stage_1_requires" in r.text
    assert "Scope the research" in r.text


@pytest.mark.asyncio
async def test_get_edit_form_unknown_returns_404(editor_client):
    c, _, _ = editor_client
    r = await c.get("/playbooks/nonexistent/edit-form")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/nitin/workspace/mopedzoom && python -m pytest tests/test_dashboard_editor.py::test_get_edit_form_returns_200 tests/test_dashboard_editor.py::test_get_edit_form_prefills_summary tests/test_dashboard_editor.py::test_get_edit_form_contains_stage_fields tests/test_dashboard_editor.py::test_get_edit_form_unknown_returns_404 -v
```

Expected: FAIL — route not found

- [ ] **Step 3: Create fragment_playbook_edit.html**

Create `src/mopedzoomd/dashboard/templates/fragment_playbook_edit.html`:

```html
<tr id="pb-row-{{pb.id}}" style="background:#0f172a">
  <td colspan="5" style="padding:1rem 1.25rem">

    {% if errors %}
    <div style="background:#450a0a;border:1px solid #f87171;border-radius:4px;padding:0.6rem 1rem;margin-bottom:1rem;color:#fca5a5;font-size:0.85rem">
      {% for e in errors %}<div>⚠ {{e}}</div>{% endfor %}
    </div>
    {% endif %}

    <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:1rem">
      <code style="color:#60a5fa;font-weight:600">{{pb.id}}</code>
      <span style="font-size:0.7rem;color:#4ade80;background:#052e16;padding:2px 8px;border-radius:4px">editing</span>
    </div>

    <form hx-post="/playbooks/{{pb.id}}"
          hx-target="closest tr"
          hx-swap="outerHTML">

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem">
        <div>
          <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px;text-transform:uppercase;letter-spacing:0.05em">Summary</label>
          <input name="summary" value="{{pb.summary}}"
                 style="width:100%;box-sizing:border-box;background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:6px 10px;border-radius:4px;font-size:0.85rem;font-family:inherit">
        </div>
        <div>
          <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:4px;text-transform:uppercase;letter-spacing:0.05em">Triggers (comma-separated)</label>
          <input name="triggers" value="{{pb.triggers|join(', ')}}"
                 style="width:100%;box-sizing:border-box;background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:6px 10px;border-radius:4px;font-size:0.85rem;font-family:inherit">
        </div>
      </div>

      <label style="display:block;font-size:0.75rem;color:#94a3b8;margin-bottom:0.5rem;text-transform:uppercase;letter-spacing:0.05em">Stages</label>

      <div id="stages-container" style="display:flex;flex-direction:column;gap:0.5rem;margin-bottom:0.75rem">
        {% for s in stages_display %}
        <div class="stage-card" style="background:#1e293b;border:1px solid #334155;border-radius:4px;padding:0.75rem">
          <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem">
            <span style="font-size:0.7rem;color:#64748b;font-family:monospace;background:#0f172a;padding:2px 7px;border-radius:3px">{{s.idx}}</span>
            <input name="stage_{{s.idx}}_name" value="{{s.name}}"
                   style="flex:1;background:#0f172a;border:1px solid #334155;color:#60a5fa;padding:4px 8px;border-radius:4px;font-size:0.82rem;font-family:monospace">
            <button type="button" onclick="removeStage(this)"
                    style="font-size:0.75rem;padding:3px 8px;background:transparent;border:1px solid #7f1d1d;color:#f87171;border-radius:4px;cursor:pointer;line-height:1">✕</button>
          </div>
          <textarea name="stage_{{s.idx}}_requires" rows="2"
                    style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:6px 10px;border-radius:4px;font-size:0.82rem;font-family:inherit;resize:vertical">{{s.requires}}</textarea>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem;margin-top:0.5rem">
            <div>
              <label style="display:block;font-size:0.7rem;color:#64748b;margin-bottom:3px">Produces</label>
              <input name="stage_{{s.idx}}_produces" value="{{s.produces}}"
                     style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#94a3b8;padding:4px 8px;border-radius:4px;font-size:0.8rem;font-family:monospace">
            </div>
            <div>
              <label style="display:block;font-size:0.7rem;color:#64748b;margin-bottom:3px">Approval</label>
              <select name="stage_{{s.idx}}_approval"
                      style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#94a3b8;padding:4px 8px;border-radius:4px;font-size:0.8rem">
                {% for opt in ['required','on-completion','on-failure','none'] %}
                <option value="{{opt}}" {% if s.approval == opt %}selected{% endif %}>{{opt}}</option>
                {% endfor %}
              </select>
            </div>
          </div>
        </div>
        {% endfor %}
      </div>

      <button type="button" onclick="addStage()"
              style="width:100%;padding:8px;background:transparent;border:1px dashed #334155;color:#475569;border-radius:4px;cursor:pointer;font-size:0.82rem;margin-bottom:1rem">+ Add Stage</button>

      <div style="display:flex;gap:0.5rem;justify-content:flex-end">
        <button type="button"
                hx-get="/playbooks/{{pb.id}}/row"
                hx-target="closest tr"
                hx-swap="outerHTML"
                style="font-size:0.8rem;padding:5px 14px;background:transparent;border:1px solid #334155;color:#94a3b8;border-radius:4px;cursor:pointer">Cancel</button>
        <button type="submit"
                style="font-size:0.8rem;padding:5px 14px;background:#166534;border:1px solid #4ade80;color:#4ade80;border-radius:4px;cursor:pointer">Save &amp; Reload</button>
      </div>
    </form>

    <template id="stage-template">
      <div class="stage-card" style="background:#0d2137;border:1px dashed #3b82f6;border-radius:4px;padding:0.75rem">
        <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem">
          <span class="stage-idx-label" style="font-size:0.7rem;color:#64748b;font-family:monospace;background:#0f172a;padding:2px 7px;border-radius:3px">?</span>
          <input data-field="name" name="stage_0_name" placeholder="stage-name"
                 style="flex:1;background:#0f172a;border:1px solid #334155;color:#60a5fa;padding:4px 8px;border-radius:4px;font-size:0.82rem;font-family:monospace">
          <button type="button" onclick="removeStage(this)"
                  style="font-size:0.75rem;padding:3px 8px;background:transparent;border:1px solid #7f1d1d;color:#f87171;border-radius:4px;cursor:pointer;line-height:1">✕</button>
        </div>
        <textarea data-field="requires" name="stage_0_requires" rows="2" placeholder="Stage prompt (what the agent must do)"
                  style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:6px 10px;border-radius:4px;font-size:0.82rem;font-family:inherit;resize:vertical"></textarea>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem;margin-top:0.5rem">
          <div>
            <label style="display:block;font-size:0.7rem;color:#64748b;margin-bottom:3px">Produces</label>
            <input data-field="produces" name="stage_0_produces" placeholder="output.md"
                   style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#94a3b8;padding:4px 8px;border-radius:4px;font-size:0.8rem;font-family:monospace">
          </div>
          <div>
            <label style="display:block;font-size:0.7rem;color:#64748b;margin-bottom:3px">Approval</label>
            <select data-field="approval" name="stage_0_approval"
                    style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#94a3b8;padding:4px 8px;border-radius:4px;font-size:0.8rem">
              <option value="required" selected>required</option>
              <option value="on-completion">on-completion</option>
              <option value="on-failure">on-failure</option>
              <option value="none">none</option>
            </select>
          </div>
        </div>
      </div>
    </template>

    <script>
    function addStage() {
      const container = document.getElementById('stages-container');
      const idx = container.querySelectorAll('.stage-card').length;
      const tpl = document.getElementById('stage-template').content.cloneNode(true);
      tpl.querySelector('.stage-idx-label').textContent = idx;
      tpl.querySelectorAll('[data-field]').forEach(function(el) {
        el.name = 'stage_' + idx + '_' + el.dataset.field;
      });
      container.appendChild(tpl);
    }
    function removeStage(btn) {
      btn.closest('.stage-card').remove();
    }
    </script>

  </td>
</tr>
```

- [ ] **Step 4: Add the edit-form endpoint to app.py**

Inside `create_app`, add after the `/playbooks/{pb_id}/row` endpoint:

```python
    @app.get("/playbooks/{pb_id}/edit-form", response_class=HTMLResponse)
    async def playbook_edit_form(pb_id: str, req: Request):
        pb = registry.get(pb_id)
        if pb is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        stages_display = _stages_for_template(pb)
        return TEMPLATES.TemplateResponse(
            req,
            "fragment_playbook_edit.html",
            {"pb": pb, "stages_display": stages_display, "errors": []},
        )
```

Add the helper function `_stages_for_template` **outside** `create_app` (module level, before `create_app`):

```python
def _stages_for_template(pb: Playbook) -> list[dict]:
    """Convert StageSpec list to template-friendly dicts with string produces."""
    result = []
    for i, s in enumerate(pb.stages):
        produces = s.produces if isinstance(s.produces, str) else ", ".join(s.produces)
        result.append({
            "idx": i,
            "name": s.name,
            "requires": s.requires,
            "produces": produces,
            "approval": s.approval,
        })
    return result
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd /home/nitin/workspace/mopedzoom && python -m pytest tests/test_dashboard_editor.py::test_get_edit_form_returns_200 tests/test_dashboard_editor.py::test_get_edit_form_prefills_summary tests/test_dashboard_editor.py::test_get_edit_form_contains_stage_fields tests/test_dashboard_editor.py::test_get_edit_form_unknown_returns_404 -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /home/nitin/workspace/mopedzoom && git add src/mopedzoomd/dashboard/app.py src/mopedzoomd/dashboard/templates/fragment_playbook_edit.html tests/test_dashboard_editor.py && git commit -m "feat: add GET /playbooks/{id}/edit-form endpoint and edit form template"
```

---

## Task 4: Save handler — POST /playbooks/{pb_id}

Validates form data, writes YAML to the user override dir, mutates the shared registry in-place for immediate hot-reload, returns updated row on success or re-renders form with errors on failure.

**Files:**
- Modify: `src/mopedzoomd/dashboard/app.py`
- Modify: `tests/test_dashboard_editor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard_editor.py`:

```python
@pytest.mark.asyncio
async def test_post_valid_saves_yaml_and_returns_row(editor_client):
    c, reg, tmp_path = editor_client
    user_dir = tmp_path / "playbooks"
    data = {
        "summary": "Updated summary",
        "triggers": "research, dig",
        "stage_0_name": "pre-brief",
        "stage_0_requires": "Updated scope prompt",
        "stage_0_produces": "pre-brief.md",
        "stage_0_approval": "required",
    }
    r = await c.post("/playbooks/research", data=data)
    assert r.status_code == 200
    assert "Updated summary" in r.text
    # YAML file written
    yaml_path = user_dir / "research.yaml"
    assert yaml_path.exists()
    # Registry hot-reloaded
    assert reg["research"].summary == "Updated summary"
    assert reg["research"].stages[0].requires == "Updated scope prompt"


@pytest.mark.asyncio
async def test_post_empty_summary_returns_error_form(editor_client):
    c, _, _ = editor_client
    data = {
        "summary": "",
        "triggers": "research",
        "stage_0_name": "pre-brief",
        "stage_0_requires": "Scope it",
        "stage_0_produces": "pre-brief.md",
        "stage_0_approval": "required",
    }
    r = await c.post("/playbooks/research", data=data)
    assert r.status_code == 200
    assert "Summary is required" in r.text


@pytest.mark.asyncio
async def test_post_no_stages_returns_error_form(editor_client):
    c, _, _ = editor_client
    data = {"summary": "Good summary", "triggers": "research"}
    r = await c.post("/playbooks/research", data=data)
    assert r.status_code == 200
    assert "at least one stage" in r.text.lower()


@pytest.mark.asyncio
async def test_post_stage_missing_requires_returns_error_form(editor_client):
    c, _, _ = editor_client
    data = {
        "summary": "Good summary",
        "triggers": "research",
        "stage_0_name": "pre-brief",
        "stage_0_requires": "",
        "stage_0_produces": "pre-brief.md",
        "stage_0_approval": "required",
    }
    r = await c.post("/playbooks/research", data=data)
    assert r.status_code == 200
    assert "requires" in r.text.lower()


@pytest.mark.asyncio
async def test_post_triggers_parsed_as_list(editor_client):
    c, reg, _ = editor_client
    data = {
        "summary": "Good summary",
        "triggers": "research, investigate, look into",
        "stage_0_name": "pre-brief",
        "stage_0_requires": "Scope it",
        "stage_0_produces": "pre-brief.md",
        "stage_0_approval": "required",
    }
    await c.post("/playbooks/research", data=data)
    assert reg["research"].triggers == ["research", "investigate", "look into"]


@pytest.mark.asyncio
async def test_post_adds_new_stage(editor_client):
    c, reg, _ = editor_client
    data = {
        "summary": "Good summary",
        "triggers": "research",
        "stage_0_name": "pre-brief",
        "stage_0_requires": "Scope it",
        "stage_0_produces": "pre-brief.md",
        "stage_0_approval": "required",
        "stage_1_name": "new-stage",
        "stage_1_requires": "Do something new",
        "stage_1_produces": "output.md",
        "stage_1_approval": "none",
    }
    await c.post("/playbooks/research", data=data)
    assert len(reg["research"].stages) == 2
    assert reg["research"].stages[1].name == "new-stage"


@pytest.mark.asyncio
async def test_post_unknown_playbook_returns_404(editor_client):
    c, _, _ = editor_client
    r = await c.post("/playbooks/nonexistent", data={"summary": "x"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/nitin/workspace/mopedzoom && python -m pytest tests/test_dashboard_editor.py::test_post_valid_saves_yaml_and_returns_row tests/test_dashboard_editor.py::test_post_empty_summary_returns_error_form tests/test_dashboard_editor.py::test_post_no_stages_returns_error_form -v
```

Expected: FAIL — route not found

- [ ] **Step 3: Add the POST endpoint to app.py**

Add the following imports at the top of `app.py` (after existing imports):

```python
import re
import yaml
from fastapi import Form
from ..playbooks import StageSpec
```

Add the POST endpoint inside `create_app`, after the edit-form GET endpoint:

```python
    @app.post("/playbooks/{pb_id}", response_class=HTMLResponse)
    async def update_playbook(pb_id: str, req: Request):
        pb = registry.get(pb_id)
        if pb is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        form = await req.form()
        summary = str(form.get("summary", "")).strip()
        triggers_raw = str(form.get("triggers", ""))
        triggers = [t.strip() for t in triggers_raw.split(",") if t.strip()]

        # Collect stage indices from form keys matching stage_{i}_name
        stage_indices: set[int] = set()
        for key in form.keys():
            m = re.match(r"^stage_(\d+)_name$", key)
            if m:
                stage_indices.add(int(m.group(1)))

        errors: list[str] = []
        if not summary:
            errors.append("Summary is required")

        stages: list[StageSpec] = []
        for i in sorted(stage_indices):
            name = str(form.get(f"stage_{i}_name", "")).strip()
            requires = str(form.get(f"stage_{i}_requires", "")).strip()
            produces_raw = str(form.get(f"stage_{i}_produces", "")).strip()
            approval = str(form.get(f"stage_{i}_approval", "required")).strip()

            if not name:
                continue  # blank-named rows skipped (user left template empty)

            if not requires:
                errors.append(f"Stage '{name}': requires prompt cannot be empty")
            if not produces_raw:
                errors.append(f"Stage '{name}': produces cannot be empty")
            if approval not in ("required", "on-completion", "on-failure", "none"):
                errors.append(f"Stage '{name}': invalid approval value '{approval}'")

            produces: str | list[str]
            if "," in produces_raw:
                produces = [p.strip() for p in produces_raw.split(",") if p.strip()]
            else:
                produces = produces_raw

            stages.append(
                StageSpec(name=name, requires=requires, produces=produces, approval=approval)
            )

        if not stages:
            errors.append("At least one stage is required")

        if errors:
            stages_display = _stages_for_template(pb)
            return TEMPLATES.TemplateResponse(
                req,
                "fragment_playbook_edit.html",
                {"pb": pb, "stages_display": stages_display, "errors": errors},
            )

        updated = pb.model_copy(update={"summary": summary, "triggers": triggers, "stages": stages})

        # Write YAML to user override dir
        user_dir.mkdir(parents=True, exist_ok=True)
        out_path = user_dir / f"{pb_id}.yaml"
        out_path.write_text(
            yaml.dump(updated.model_dump(exclude_none=True), default_flow_style=False, sort_keys=False, allow_unicode=True)
        )

        # Hot-reload: mutate shared registry in-place
        registry[pb_id] = updated

        return TEMPLATES.TemplateResponse(
            req, "fragment_playbook_row.html", {"pb": updated}
        )
```

- [ ] **Step 4: Run all POST tests**

```bash
cd /home/nitin/workspace/mopedzoom && python -m pytest tests/test_dashboard_editor.py -k "post" -v
```

Expected: All PASS

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
cd /home/nitin/workspace/mopedzoom && python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: All existing tests continue to pass

- [ ] **Step 6: Commit**

```bash
cd /home/nitin/workspace/mopedzoom && git add src/mopedzoomd/dashboard/app.py tests/test_dashboard_editor.py && git commit -m "feat: add POST /playbooks/{id} — validate, write YAML, hot-reload registry"
```

---

## Task 5: Wire Edit buttons into playbooks.html

Update the playbooks list template to add the Edit button column and htmx row IDs.

**Files:**
- Modify: `src/mopedzoomd/dashboard/templates/playbooks.html`
- Modify: `tests/test_dashboard_editor.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_editor.py`:

```python
@pytest.mark.asyncio
async def test_playbooks_page_has_edit_buttons(editor_client):
    c, _, _ = editor_client
    r = await c.get("/playbooks")
    assert r.status_code == 200
    assert "edit-form" in r.text
    assert "hx-get" in r.text
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /home/nitin/workspace/mopedzoom && python -m pytest tests/test_dashboard_editor.py::test_playbooks_page_has_edit_buttons -v
```

Expected: FAIL — no `edit-form` in response

- [ ] **Step 3: Update playbooks.html**

Replace the entire content of `src/mopedzoomd/dashboard/templates/playbooks.html` with:

```html
{% extends "base.html" %}
{% block content %}
<h2>Playbooks</h2>
<table>
<tr><th>ID</th><th>Summary</th><th>Triggers</th><th>Stages</th><th></th></tr>
{% for pb in playbooks %}
<tr id="pb-row-{{pb.id}}">
  <td><code>{{pb.id}}</code></td>
  <td>{{pb.summary}}</td>
  <td>{{pb.triggers|join(", ")}}</td>
  <td>{{pb.stages|length}}</td>
  <td>
    <button hx-get="/playbooks/{{pb.id}}/edit-form"
            hx-target="closest tr"
            hx-swap="outerHTML"
            style="font-size:0.75rem;padding:3px 10px;background:#1e3a5f;border:1px solid #3b82f6;color:#93c5fd;border-radius:4px;cursor:pointer">Edit</button>
  </td>
</tr>
{% endfor %}
</table>
{% endblock %}
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
cd /home/nitin/workspace/mopedzoom && python -m pytest tests/test_dashboard_editor.py::test_playbooks_page_has_edit_buttons -v
```

Expected: PASS

- [ ] **Step 5: Run the full test suite**

```bash
cd /home/nitin/workspace/mopedzoom && python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
cd /home/nitin/workspace/mopedzoom && git add src/mopedzoomd/dashboard/templates/playbooks.html tests/test_dashboard_editor.py && git commit -m "feat: add Edit buttons to playbooks list — completes playbook editor UI"
```

---

## Task 6: Manual smoke test

Verify the full end-to-end flow in the running daemon.

- [ ] **Step 1: Restart the daemon to pick up code changes**

```bash
systemctl --user restart mopedzoomd && sleep 2 && systemctl --user status mopedzoomd
```

Expected: `active (running)`

- [ ] **Step 2: Open the dashboard and test editing**

Open http://localhost:7777/playbooks in a browser.

Verify:
1. Each playbook row has an "Edit" button
2. Clicking Edit expands the row inline with pre-filled fields
3. Modify a stage's `requires` text
4. Click "Save & Reload" — row collapses back showing updated summary
5. Click Edit again — the updated `requires` is shown
6. Check that `~/.mopedzoom/playbooks/<id>.yaml` was written with the updated content

```bash
cat ~/.mopedzoom/playbooks/research.yaml
```

- [ ] **Step 3: Test add stage**

1. Click Edit on any playbook
2. Click "+ Add Stage" — a blank dashed stage card appears
3. Fill in name, requires, produces, approval
4. Click "Save & Reload"
5. Click Edit again — new stage appears in list

- [ ] **Step 4: Test remove stage**

1. Click Edit on a playbook with multiple stages
2. Click ✕ on one stage — card disappears
3. Click "Save & Reload"
4. Click Edit again — removed stage is gone

- [ ] **Step 5: Test cancel**

1. Click Edit, make changes
2. Click "Cancel" — row restores to original values, no save occurs

- [ ] **Step 6: Test validation error**

1. Click Edit
2. Clear the Summary field
3. Click "Save & Reload" — error banner appears inline, no page navigation

---

## Self-Review

**Spec coverage:**
- ✅ Inline edit on `/playbooks` page (Task 5 — playbooks.html)
- ✅ `summary` and `triggers` editable (Task 3 — edit form template)
- ✅ Per-stage: `name`, `requires`, `produces`, `approval` (Task 3)
- ✅ Add new stages (Task 3 — `addStage()` JS)
- ✅ Remove existing stages (Task 3 — `removeStage()` JS)
- ✅ Save writes YAML to user override dir (Task 4 — POST handler)
- ✅ Hot-reload: `registry[pb_id] = updated` (Task 4)
- ✅ Registry reference fix in daemon.py (Task 1)
- ✅ Validation: summary non-empty, at least one stage, stage fields (Task 4)
- ✅ `produces` list-to-string display and back (Task 3 `_stages_for_template`, Task 4 POST handler)
- ✅ Cancel restores row without save (Task 3 — cancel button wiring)
- ✅ Edit form for unknown playbook returns 404 (Task 3)
