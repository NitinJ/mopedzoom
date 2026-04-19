# mopedzoom

Always-on orchestrator that runs Claude Code agents on your home desktop, driven
by Telegram or CLI. Playbook-driven task runs with git worktrees, approvals, and
a local dashboard.

See `docs/specs/2026-04-19-mopedzoom-design.md` for the full design.

## Install

```bash
pip install -e .[dev]
```

Then, from Claude Code, install the plugin and initialize the daemon:

```
/mopedzoom:init
```

`/mopedzoom:init` scaffolds `~/.mopedzoom/` (config, SQLite state, playbooks,
runs), registers the systemd user unit, and starts the daemon.

## Run

The daemon runs under systemd-user by default:

```bash
systemctl --user start mopedzoomd
systemctl --user status mopedzoomd
journalctl --user -u mopedzoomd -f
```

To run the daemon directly (development / debugging):

```bash
python -m mopedzoomd --help
MOPEDZOOM_STATE=~/.mopedzoom python -m mopedzoomd
```

Environment:

- `MOPEDZOOM_STATE` — overrides the state root (default: `~/.mopedzoom`).
- The config lives at `$MOPEDZOOM_STATE/config.yaml`.

## Using it

From Claude Code, the plugin exposes a set of slash commands:

- `/mopedzoom:submit <text>` — submit a new task (router picks a playbook).
- `/mopedzoom:status [id]` — show task status.
- `/mopedzoom:tasks` — list recent tasks.
- `/mopedzoom:cancel <id>` / `/mopedzoom:resume <id>` — lifecycle control.
- `/mopedzoom:edit <id> <stage>` — edit a paused stage.
- `/mopedzoom:logs <id>` — tail transcripts for a task.
- `/mopedzoom:ui` — open the local dashboard.
- `/mopedzoom:config` — show or edit daemon config.
- `/mopedzoom:playbook new|edit|list|delete` — manage user playbooks.

Or reach the daemon via the Unix socket directly:

```bash
echo '{"op":"tasks"}' | nc -U ~/.mopedzoom/socket
```

Telegram intake is optional; configure `channel.bot_token` and
`channel.chat_id` in `config.yaml` to enable it.

## Built-in playbooks

- `research` — scope → research → publish.
- `bug-file` — triage → file.
- `bug-fix` — reproduce → fix → verify → publish.
- `feature-impl` — design → implement → test → publish.

User-provided YAML playbooks in `~/.mopedzoom/playbooks/` override built-ins by
`id`.

## Development

```bash
./scripts/check.sh        # ruff format --check, ruff check, pytest --cov
pytest -q                 # fast test loop
ruff format . && ruff check --fix .
```

Tests mock `claude -p` via a tiny fake binary on `$PATH` (see
`tests/integration/conftest.py`).
