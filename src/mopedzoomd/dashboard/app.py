from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..playbooks import Playbook
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

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok"})

    return app
