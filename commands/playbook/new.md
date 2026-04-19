---
description: Create a new user playbook (YAML) under ~/.mopedzoom/playbooks/
---

Ask the user for:

- **id** (slug, e.g. `my-playbook`)
- **summary** (one-line description)
- **triggers** (comma-separated keywords for the router)
- whether a **worktree** is required
- default **permission_mode** (`bypass` / `ask` / `allowlist`)
- **stages**, for each: name, `requires` template, `produces`, `approval` (`required`/`on-completion`/`on-failure`/`none`), optional `agent`, optional `timeout`.

Validate the result by constructing `mopedzoomd.playbooks.Playbook(**data)` (pydantic raises on invalid shape). Then write the YAML to `~/.mopedzoom/playbooks/<id>.yaml`.

Ask whether to reload the daemon so the new playbook becomes live: `systemctl --user reload mopedzoomd` (or restart if reload is not wired).
