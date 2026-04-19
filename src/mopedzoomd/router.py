"""LLM-backed router for picking a playbook when triggers are ambiguous."""

from __future__ import annotations

import json

from .playbooks import Playbook, resolve_playbook

ROUTER_SYSTEM = (
    "You classify user task requests into one of the provided playbooks. "
    'Respond strictly as JSON: {"pick": "<id>" or null, "confidence": 0-1}.'
)


class Router:
    def __init__(
        self,
        registry: dict[str, Playbook],
        claude_client,
        model: str = "claude-haiku-4-5-20251001",
    ):
        self.registry = registry
        self.client = claude_client
        self.model = model

    async def pick(self, text: str) -> Playbook | None:
        deterministic = resolve_playbook(text, self.registry)
        if deterministic:
            return deterministic
        if self.client is None:
            return None
        descriptions = "\n".join(f"- {p.id}: {p.summary}" for p in self.registry.values())
        prompt = (
            f"Request: {text}\n\nPlaybooks:\n{descriptions}\n\n"
            "Pick exactly one, or null if none fit."
        )
        msg = await self.client.messages.create(
            model=self.model,
            max_tokens=200,
            system=ROUTER_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text_out = msg.content[0].text
        try:
            data = json.loads(text_out)
        except json.JSONDecodeError:
            return None
        pid = data.get("pick")
        conf = float(data.get("confidence", 0))
        if not pid or conf < 0.5:
            return None
        return self.registry.get(pid)
