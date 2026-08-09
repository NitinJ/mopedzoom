"""Regression: Router must accept claude_client=None and still work deterministically.

Passing None is the documented way to force trigger-only matching (no LLM
fallback). If Router.__init__ ever grows a required non-None arg, every call
site in daemon.py breaks.
"""

from __future__ import annotations

import pytest

from mopedzoomd.playbooks import Playbook, StageSpec
from mopedzoomd.router import Router


def _pb(pid, triggers):
    return Playbook(
        id=pid,
        summary=f"do {pid}",
        triggers=triggers,
        stages=[StageSpec(name="x", requires="r", produces="x.md", approval="none")],
    )


@pytest.fixture
def reg():
    return {
        "research": _pb("research", ["research", "investigate"]),
        "bug-fix": _pb("bug-fix", ["fix", "bug"]),
    }


def test_router_constructs_with_none_client(reg):
    r = Router(registry=reg, claude_client=None)
    assert r.client is None
    assert r.registry is reg


async def test_router_matches_trigger_without_llm(reg):
    r = Router(registry=reg, claude_client=None)
    pb = await r.pick("research this OAuth corner case")
    assert pb is not None
    assert pb.id == "research"


async def test_router_returns_none_when_no_trigger_and_no_llm(reg):
    r = Router(registry=reg, claude_client=None)
    assert await r.pick("hello there") is None


async def test_router_case_insensitive_trigger(reg):
    r = Router(registry=reg, claude_client=None)
    pb = await r.pick("RESEARCH this thing")
    assert pb is not None and pb.id == "research"
