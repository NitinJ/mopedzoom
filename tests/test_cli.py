import asyncio
import sys
from pathlib import Path

import pytest

from mopedzoomd.channels.cli_socket import CLISocketChannel

CLI = Path(__file__).parent.parent / "bin" / "mopedzoom"


@pytest.mark.asyncio
async def test_cli_submit_goes_to_socket(tmp_path):
    sock = tmp_path / "sock"
    ch = CLISocketChannel(str(sock))
    await ch.start()
    received = []

    async def h(m):
        received.append(m)

    ch.set_handler(h)
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(CLI),
        "submit",
        "hello from cli",
        env={"MOPEDZOOM_SOCKET": str(sock), "PATH": "/usr/bin"},
        stdout=asyncio.subprocess.PIPE,
    )
    await proc.wait()
    await asyncio.sleep(0.05)
    await ch.stop()
    assert received and received[0].text == "hello from cli"


@pytest.mark.asyncio
async def test_cli_tasks_dispatch(tmp_path):
    sock = tmp_path / "sock"
    ch = CLISocketChannel(str(sock))
    await ch.start()
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(CLI),
            "tasks",
            env={"MOPEDZOOM_SOCKET": str(sock), "PATH": "/usr/bin"},
            stdout=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        assert proc.returncode == 0
        assert b"ack" in out or b"tasks" in out or b"ok" in out
    finally:
        await ch.stop()


def test_cli_help_prints_usage():
    import subprocess

    r = subprocess.run(
        [sys.executable, str(CLI), "--help"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "mopedzoom" in r.stdout
    assert "submit" in r.stdout
