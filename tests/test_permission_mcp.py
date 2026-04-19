import asyncio
import json

from mopedzoomd.permission_mcp import handle_permission_request


async def test_permission_allowlist_auto_approves(tmp_path):
    scratch = tmp_path
    result = await handle_permission_request(
        scratch_dir=scratch,
        tool_name="Bash",
        input_json={"command": "gh issue list"},
        allowlist=["gh issue *"],
        timeout_s=1,
    )
    assert result == {
        "behavior": "allow",
        "updatedInput": {"command": "gh issue list"},
    }


async def test_permission_writes_and_waits(tmp_path):
    scratch = tmp_path

    async def caller():
        return await handle_permission_request(
            scratch_dir=scratch,
            tool_name="Bash",
            input_json={"command": "rm -rf /"},
            allowlist=[],
            timeout_s=5,
        )

    async def responder():
        for _ in range(50):
            if (scratch / "permission.json").exists():
                break
            await asyncio.sleep(0.05)
        (scratch / "permission_response.json").write_text(
            json.dumps({"decision": "deny"})
        )

    result, _ = await asyncio.gather(caller(), responder())
    assert result["behavior"] == "deny"
