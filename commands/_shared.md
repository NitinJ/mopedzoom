# mopedzoom slash commands — shared skeleton

All files in `commands/` are Claude Code slash commands. Each follows this pattern:

```
---
description: <one-line summary shown in the command palette>
---

<prose instructions to Claude on how to perform the command>
```

## Conventions

- **Frontmatter** MUST contain a `description:` field. Keep it short (≤ 70 chars).
- **Body** is prose addressed to Claude. Use the Bash tool to shell out to `mopedzoom <subcommand>` (the local CLI at `bin/mopedzoom`), which talks to the daemon over the Unix socket at `~/.mopedzoom/socket`.
- **Tool usage**: `Bash` for CLI + git + systemctl; `Read`/`Edit`/`Write` for editing config and playbooks.
- **Idempotency**: commands that write state (init, config, playbook:new, playbook:edit) should load the existing state first and offer edits rather than clobber.
- **Error reporting**: always surface the raw CLI output to the user on failure; do not swallow `stderr`.

## Command-to-CLI mapping

| Slash command | Primary CLI call |
| --- | --- |
| `/mopedzoom:init` | interactive, writes `~/.mopedzoom/config.yaml` + installs systemd unit |
| `/mopedzoom:config` | edits `~/.mopedzoom/config.yaml` |
| `/mopedzoom:submit` | `mopedzoom submit "<text>"` |
| `/mopedzoom:tasks` | `mopedzoom tasks` |
| `/mopedzoom:status` | `mopedzoom status [<id>]` |
| `/mopedzoom:cancel` | `mopedzoom cancel <id>` |
| `/mopedzoom:resume` | `mopedzoom resume <id>` |
| `/mopedzoom:edit` | `mopedzoom edit <id> [--stage=N]` |
| `/mopedzoom:logs` | `mopedzoom logs <id>` |
| `/mopedzoom:ui` | opens the dashboard |
| `/mopedzoom:playbook:new` | creates a YAML in `~/.mopedzoom/playbooks/` |
| `/mopedzoom:playbook:edit` | edits an existing playbook YAML |
| `/mopedzoom:playbook:list` | lists built-in + user playbooks |
| `/mopedzoom:playbook:delete` | deletes a user playbook YAML |

## Subdirectories

Nested command groups (e.g. `commands/playbook/*.md`) appear as colon-separated slash commands: `commands/playbook/new.md` → `/mopedzoom:playbook:new`.
