import asyncio
import json

import pytest

from mopedzoomd.channels.base import InboundMessage
from mopedzoomd.channels.cli_socket import CLISocketChannel


@pytest.fixture
async def ch(tmp_path):
    s = CLISocketChannel(str(tmp_path / "sock"))
    await s.start()
    yield s
    await s.stop()


async def test_inbound_roundtrip(ch):
    received: list[InboundMessage] = []

    async def handler(m):
        received.append(m)

    ch.set_handler(handler)

    reader, writer = await asyncio.open_unix_connection(ch.path)
    writer.write((json.dumps({"op": "submit", "text": "hello"}) + "\n").encode())
    await writer.drain()
    line = (await reader.readline()).decode()
    writer.close()
    await writer.wait_closed()
    assert "ack" in json.loads(line)
    await asyncio.sleep(0.05)
    assert received and received[0].text == "hello"
