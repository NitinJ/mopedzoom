from __future__ import annotations

import re
import yaml
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..playbooks import Playbook, StageSpec
from ..state import StateDB

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _stages_for_template(pb: Playbook) -> list[dict]:
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

    @app.get("/", response_class=HTMLResponse)
    async def index(req: Request):
        tasks = await db.list_tasks(limit=50)
        return TEMPLATES.TemplateResponse(req, "index.html", {"tasks": tasks})

    @app.get("/tasks/{tid}", response_class=HTMLResponse)
    async def task_detail(tid: int, req: Request):
        t = await db.get_task(tid)
        stages = await db.get_stages(tid)
        events = await db.list_events(tid)
        return TEMPLATES.TemplateResponse(
            req,
            "task.html",
            {"task": t, "stages": stages, "events": events},
        )

    @app.get("/fragments/tasks", response_class=HTMLResponse)
    async def tasks_fragment(req: Request):
        tasks = await db.list_tasks(limit=50)
        return TEMPLATES.TemplateResponse(req, "fragment_tasks.html", {"tasks": tasks})

    @app.get("/agents", response_class=HTMLResponse)
    async def agents_view(req: Request):
        agents = discover()
        return TEMPLATES.TemplateResponse(req, "agents.html", {"agents": agents})

    @app.get("/playbooks", response_class=HTMLResponse)
    async def playbooks_view(req: Request):
        pbs = list(registry.values())
        return TEMPLATES.TemplateResponse(req, "playbooks.html", {"playbooks": pbs})

    @app.get("/playbooks/{pb_id}/row", response_class=HTMLResponse)
    async def playbook_row(pb_id: str, req: Request):
        pb = registry.get(pb_id)
        if pb is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return TEMPLATES.TemplateResponse(req, "fragment_playbook_row.html", {"pb": pb})

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

    @app.post("/playbooks/{pb_id}", response_class=HTMLResponse)
    async def update_playbook(pb_id: str, req: Request):
        pb = registry.get(pb_id)
        if pb is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        form = await req.form()
        summary = str(form.get("summary", "")).strip()
        triggers_raw = str(form.get("triggers", ""))
        triggers = [t.strip() for t in triggers_raw.split(",") if t.strip()]

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
                continue

            if not requires:
                errors.append(f"Stage '{name}': requires prompt cannot be empty")
            if not produces_raw:
                errors.append(f"Stage '{name}': produces cannot be empty")
            if approval not in ("required", "on-completion", "on-failure", "none", "review"):
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

        user_dir.mkdir(parents=True, exist_ok=True)
        out_path = user_dir / f"{pb_id}.yaml"
        out_path.write_text(
            yaml.dump(
                updated.model_dump(exclude_none=True),
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
        )

        registry[pb_id] = updated

        return TEMPLATES.TemplateResponse(
            req, "fragment_playbook_row.html", {"pb": updated}
        )

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok"})

    return app
