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
    yaml_path = user_dir / "research.yaml"
    assert yaml_path.exists()
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


@pytest.mark.asyncio
async def test_playbooks_page_has_edit_buttons(editor_client):
    c, _, _ = editor_client
    r = await c.get("/playbooks")
    assert r.status_code == 200
    assert "edit-form" in r.text
    assert "hx-get" in r.text
