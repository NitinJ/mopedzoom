import asyncio
import json

from mopedzoomd.bridges import BridgeEvent, watch_scratch
from mopedzoomd.scratch import ScratchDir


async def test_detects_question(tmp_path):
    s = ScratchDir(str(tmp_path), task_id=1)
    s.create()
    events: list[BridgeEvent] = []

    async def consume():
        async for ev in watch_scratch(s, interval_s=0.02):
            events.append(ev)
            if len(events) == 1:
                break

    async def writer():
        await asyncio.sleep(0.05)
        (s.dir / "question.json").write_text(json.dumps({"prompt": "X?"}))

    await asyncio.gather(consume(), writer())
    assert events[0].kind == "question"
    assert events[0].payload["prompt"] == "X?"
