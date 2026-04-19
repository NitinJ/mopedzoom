from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..state import StateDB

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(db: StateDB) -> FastAPI:
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    async def index(req: Request):
        tasks = await db.list_tasks(limit=50)
        return TEMPLATES.TemplateResponse(
            req, "index.html", {"tasks": tasks}
        )

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

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok"})

    return app
