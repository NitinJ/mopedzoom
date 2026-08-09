"""Regression: daemon.main() must actually construct and serve the dashboard.

Before the fix main() never imported dashboard.app, so the UI port accepted no
connections. This test patches uvicorn.Server so serve() is a no-op and asserts
that create_app was called with a non-empty playbook registry and that the
uvicorn Config received the configured port + the 127.0.0.1 host.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from mopedzoomd.daemon import Daemon, TaskManager
from mopedzoomd.state import StateDB


def _write_cfg(path, port=9876):
    path.write_text(
        yaml.safe_dump(
            {
                "channel": {"bot_token": "t", "chat_id": -1, "mode": "header"},
                "repos": {},
                "dashboard": {"enabled": True, "port": port},
            }
        )
    )


def _fake_daemon(tmp_path):
    """Build a throwaway Daemon with an in-memory-ish StateDB stand-in.

    We don't actually need a live DB for the dashboard-startup assertion; just
    something that quacks like one for ``Daemon.stop()``.
    """
    db = AsyncMock(spec=StateDB)
    tm = MagicMock(spec=TaskManager)
    tm.playbook_registry = {"research": MagicMock(id="research", summary="s")}
    tm.agent_discoverer = lambda: []
    return Daemon(cfg=MagicMock(), db=db, task_mgr=tm, channels={})


def _run_main_with_patches(tmp_path, monkeypatch, port):
    monkeypatch.setenv("MOPEDZOOM_STATE", str(tmp_path))
    cfg_path = tmp_path / "config.yaml"
    _write_cfg(cfg_path, port=port)
    monkeypatch.setattr("sys.argv", ["mopedzoomd", "--config", str(cfg_path)])

    captured = {}

    class FakeServer:
        def __init__(self, config):
            captured["config"] = config
            self.should_exit = False

        async def serve(self):
            return

    class _PreSetEvent:
        """asyncio.Event stand-in that is permanently set.

        daemon.main() uses ``stop = asyncio.Event()`` and awaits ``stop.wait()``
        alongside uvicorn.serve(); we pre-set it so gather() returns promptly.
        """

        def set(self):
            pass

        def is_set(self):
            return True

        async def wait(self):
            return True

        def clear(self):
            pass

    seen_create_app = {}

    import mopedzoomd.dashboard.app as dash_app

    def wrapped_create_app(*args, **kwargs):
        seen_create_app["called"] = True
        seen_create_app["kwargs"] = kwargs
        return MagicMock()  # returns the "FastAPI app" passed to uvicorn

    async def fake_build(cfg, *, start=True):
        return _fake_daemon(tmp_path)

    import asyncio as _asyncio

    with (
        patch("uvicorn.Server", FakeServer),
        patch.object(dash_app, "create_app", wrapped_create_app),
        patch("mopedzoomd.daemon.build_daemon_from_config", fake_build),
        patch.object(_asyncio, "Event", _PreSetEvent),
    ):
        from mopedzoomd import daemon as daemon_mod

        daemon_mod.main()

    return captured, seen_create_app


def test_main_starts_dashboard_on_configured_port(tmp_path, monkeypatch):
    captured, seen = _run_main_with_patches(tmp_path, monkeypatch, port=9876)

    assert seen.get("called"), "create_app was never invoked by main()"
    # create_app must receive the registry + discoverer from the daemon.
    kwargs = seen["kwargs"]
    assert "playbook_registry" in kwargs
    assert "research" in kwargs["playbook_registry"]
    assert "agent_discoverer" in kwargs

    cfg = captured["config"]
    assert cfg.port == 9876
    assert cfg.host == "127.0.0.1"


def test_main_honours_port_override(tmp_path, monkeypatch):
    captured, _ = _run_main_with_patches(tmp_path, monkeypatch, port=8123)
    assert captured["config"].port == 8123
