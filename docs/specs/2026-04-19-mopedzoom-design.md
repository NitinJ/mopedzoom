# mopedzoom — Design

**Date:** 2026-04-19
**Status:** Brainstormed; pending user review before plan generation.
**Author:** nitin (with Claude)

## 1. Problem & goals

Build an always-on system that lets the user fire off software tasks (research, bug filing, bug fix, feature implementation) from anywhere — phone, terminal, or wherever — and have Claude-powered agents execute them on a home desktop, with a human-in-the-loop approval gate between each significant stage and a delivered artifact at the end (a markdown report, a GitHub issue, or a PR).

Non-goals (v1): multi-user; cloud deployment; web dashboard; voice UI; running without Claude Code installed.

Success criteria:

- User submits a task from Telegram, receives a pre-design within minutes, approves via inline button, and later gets a PR link — all without opening a laptop.
- Adding a new task type is a slash-command wizard, not a code change.
- Adding a new agent (Claude marketplace, GitHub, hand-written) is a plugin install, not a code change.
- Daemon restarts mid-task lose no work; all tasks resume where they left off.

## 2. High-level shape

mopedzoom is a plugin at `~/workspace/mopedzoom/` that installs a **long-running user-mode daemon** (`mopedzoomd`, systemd user unit) on the home desktop. The daemon is a small dispatcher — it does almost no LLM work itself; it invokes `claude -p` subprocesses per stage.

The daemon exposes **two intake channels**:

- **Telegram bot** (primary remote channel) — locked to a single chat id.
- **Local Unix socket** (terminal use) — consumed by the `mopedzoom` CLI and by the Claude-Code slash commands this plugin ships.

Both funnel into the same queue. All state lives in SQLite (`~/.mopedzoom/state.db`) plus a scratch directory per task (`~/.mopedzoom/runs/<task-id>/`).

The **unit of work** is a **task**. A task follows a **playbook** — a declarative YAML recipe of **stages**. Each stage invokes Claude Code with an available agent, produces a **deliverable**, and optionally pauses for **user approval** before the next stage runs.

Agents are **not** defined by mopedzoom. They come from the Claude Code ecosystem (marketplace plugins, `~/.claude/agents/`, project-local `.claude/agents/`). mopedzoom discovers what's available and lets Claude Code itself route each stage to the best subagent.

## 3. Path layout

### Plugin code (distributable) — `~/workspace/mopedzoom/`

```
~/workspace/mopedzoom/
├── .claude-plugin/plugin.json
├── commands/                     # slash commands (init, config, submit, …)
├── skills/                       # helper skills (playbook-authoring, …)
├── playbooks/                    # built-in playbooks
├── src/mopedzoomd/               # daemon source (Python 3.12, async)
│   ├── daemon.py
│   ├── channels/
│   │   ├── telegram.py
│   │   └── cli_socket.py
│   ├── state.py                  # SQLite access layer
│   ├── router.py                 # playbook picker
│   ├── stage_runner.py           # claude -p subprocess manager
│   ├── worktree.py
│   ├── bridges.py                # question.json / approval.json monitors
│   └── config.py                 # read/write ~/.mopedzoom/config.yaml
├── bin/mopedzoom                 # local CLI → Unix socket
├── systemd/mopedzoomd.service    # user unit template
├── docs/specs/                   # design + plan docs
├── pyproject.toml
└── README.md
```

### User state (per-user, gitignored) — `~/.mopedzoom/`

```
~/.mopedzoom/
├── config.yaml                   # managed by /mopedzoom:init and /mopedzoom:config
├── state.db                      # SQLite
├── runs/<task-id>/               # per-task scratch dirs
│   ├── task.json
│   ├── inputs.json
│   ├── <idx>-<stage>.transcript
│   ├── <idx>-<stage>.md          # or other deliverable
│   ├── <idx>-<stage>.deliverable.json
│   ├── question.json             # present only while awaiting_input
│   └── approval.json             # present only while awaiting_approval
├── worktrees/<repo-slug>/<task-id>/
├── playbooks/                    # user-authored playbooks
├── logs/mopedzoomd.log
└── socket                        # Unix socket for local CLI
```

### Why the split

- Plugin code is clonable/shareable — zero per-user state in the repo.
- Uninstall/reinstall/upgrade doesn't touch user data.
- Another user on the same machine can use the same plugin with their own `~/.mopedzoom/`.

### Playbook resolution order (later wins)

1. Plugin built-ins: `~/workspace/mopedzoom/playbooks/*.yaml`
2. User playbooks: `~/.mopedzoom/playbooks/*.yaml`

`/mopedzoom:playbook:new` always writes to user state, never the plugin dir.

## 4. Daemon architecture

### Components

| Component | Role |
|---|---|
| **Channel adapters** | Telegram long-poller + Unix-socket server. Normalize incoming messages into a `Request` object, push onto the in-memory queue (backed by an SQLite row so nothing is lost on restart). |
| **Router** | One cheap Haiku call that matches `Request.text` against playbook `summary` lines → picks a playbook. On low confidence, asks the user to choose via inline buttons. |
| **Task manager** | Creates the task row, scratch dir, and drives the stage loop. One coroutine per active task. |
| **Stage runner** | For each stage: build the agent allowlist, assemble the stage prompt, spawn `claude -p`, capture deliverable + session-id, enforce the approval gate. |
| **Channel bridge** | While a stage subprocess runs, watches the scratch dir for `question.json` and `approval.json`. When one appears, posts the content to the task's originating channel and parks the task. |
| **Worktree manager** | Creates and cleans up git worktrees for playbooks that require one. |
| **Sweeper** | Daily: remove worktrees/branches in grace-period state older than 7 days. |

### SQLite schema

```
tasks(
  id, channel, user_ref, playbook_id, status, created_at,
  parent_task_id, inputs_json
)

stages(
  task_id, idx, name, status, session_id, agent_used,
  deliverable_path, transcript_path, started_at, ended_at
)

pending_interactions(
  task_id, stage_idx, kind, prompt, posted_to_channel_ref, created_at
)
  -- kind in ('approval', 'question', 'input', 'revision')
  -- posted_to_channel_ref: Telegram message id, or CLI socket id

worktrees(task_id, repo, path, branch, created_at, state)
  -- state in ('active', 'grace', 'swept')

agent_picks(task_id, stage_idx, agent_name, from_transcript_parse)
  -- recorded after the fact by parsing the stage transcript

task_events(task_id, ts, kind, detail_json)  -- append-only audit trail
```

### Data flow for one task

1. Telegram message arrives → `Request` object queued.
2. Router picks a playbook (or confirms with user if ambiguous).
3. Required inputs resolved; if missing, daemon asks the user one-by-one.
4. `tasks` row inserted (`status = queued`), `~/.mopedzoom/runs/<id>/` scratch dir created.
5. For each stage in the playbook:
   - Agent allowlist assembled (Section 6).
   - `claude -p --agents <list> [--resume <sid>] "<stage prompt>"` spawned.
   - Stage-runner watches the subprocess and the scratch dir.
   - On subprocess exit:
     - deliverable present → apply approval rule → advance or park.
     - `question.json` present → park, post question.
     - no deliverable, non-zero exit → stage failed; offer retry.
6. Final deliverable posted to channel; task `delivered`.
7. Worktree destroyed (if any); task row retained for audit.

### Restart story

On startup:

- Scan `tasks` where `status IN ('running', 'awaiting_input', 'awaiting_approval')`.
- For `awaiting_input` / `awaiting_approval`: nothing to do; task resumes on next user reply.
- For `running`: stage subprocess is gone; re-dispatch the current stage from scratch using the stored session-id (transcript preserved with `.killed` suffix).

## 5. Playbooks

### Schema

```yaml
id: bug-fix
summary: "Triage and fix a bug, produce a PR"
triggers: ["fix", "bug", "broken", "error in"]

inputs:
  - name: repo
    required: true
    prompt: "Which repo?"
  - name: issue_ref
    required: true
    prompt: "Which issue or what's the bug?"

requires_worktree: true
permission_mode: bypass                # playbook-wide default; stage can override

stages:
  - name: pre-design
    requires: "Analyze bug, find root cause, propose fix, identify touched files"
    produces: pre-design.md
    approval: required                 # hard stop

  - name: implement
    requires: "Write code, run tests, commit atomically"
    produces: [commits, transcript.log]
    approval: on-completion            # show diff, user confirms
    timeout: 30m
    # permission_mode: ask             # example: override to route prompts to channel

  - name: open-pr
    requires: "Push branch and open PR with summary body"
    produces: pr_url
    approval: none
    # optional pin:
    # agent: claude-pr-opener
```

### Per-submission stage customization

Playbooks define the default stage list, but **the user can customize stages for a single task at submit time**. `/mopedzoom:submit --edit-stages` (and an inline `[Customize stages]` button on the Telegram "queued" message) opens a wizard showing the resolved stage list; the user can toggle, reorder, or add a one-off stage before the task starts. Customizations apply only to that task; the playbook YAML is untouched.

### Approval modes

| Mode | Behavior |
|---|---|
| `required` | Hard stop; task parks at `awaiting_approval` indefinitely. Never auto-advances. Use for gates where review is mandatory (plans, designs). |
| `on-completion` | Post result + `[Approve] [Revise] [Cancel]`. Same blocking behavior as `required` by default; the difference is that a playbook may opt into auto-advance after a configured timeout (`stage.auto_advance_after: 24h`). Use for stages where silence can reasonably mean approval. |
| `on-failure` | Only pause if the stage failed; otherwise advance silently. |
| `none` | Auto-advance regardless of outcome. |

### Task state machine

```
queued → classifying → awaiting_input → running (stage N)
                ↓              ↓                 ↓
                +──  awaiting_approval ← → running (stage N+1)
                                ↓                 ↓
                             …                delivered
                                        ↘ failed ↘ cancelled
                                        ↘ paused (user-requested) ← → running
```

`paused` semantics: user pauses → task does not advance past the current stage boundary. If a subprocess is running when pause is requested, it is *not* killed; the stage finishes naturally and the task parks at `paused`. Resuming continues from the next stage. `cancel` is still the hard-kill option.

All transitions append to `task_events` for a full audit trail.

### Stage prompt template

The stage runner substitutes into a template:

```
You are working on task <id> (<playbook.summary>).

Current stage: <stage.name>
Stage goal: <stage.requires>
Expected deliverable(s): <stage.produces>

Inputs:
  repo: <inputs.repo>
  issue_ref: <inputs.issue_ref>

Prior deliverables (read as needed):
  0-pre-design.md: <path>
  0-pre-design.deliverable.json: <path>

Working directory: <scratch-dir or worktree path>

If you need user input to proceed, write ~/.mopedzoom/runs/<id>/question.json:
  {"stage": "<stage.name>", "prompt": "<your question>", "options": [...]}
and exit. I'll resume you with the user's answer.

Dispatch to whichever available subagent is best suited.
```

### User-initiated controls (any time)

- `/mopedzoom:status <id>` — current state + last transcript tail
- `/mopedzoom:tasks` — interactive drilldown: list → select → `[Pause] [Resume] [Cancel] [Open deliverable] [Re-run stage]`
- `/mopedzoom:cancel <id>` — cancel, kill subprocess, clean up
- `/mopedzoom:resume <id>` — resume a paused task or re-run a stage that hit an intermittent failure
- `/mopedzoom:edit <id>` — open last deliverable in `$EDITOR` (local only), then resume

## 6. Agent discovery & selection

### Discovery (at each stage dispatch)

Scan, later paths win on name collision:

1. `~/.claude/plugins/*/agents/**/*.md`
2. `~/.claude/plugins/*/.claude/agents/**/*.md`
3. `~/.claude/agents/**/*.md`
4. `<task's target repo>/.claude/agents/**/*.md` (highest priority)

Cache 30s with filesystem watcher invalidation.

### Selection — Claude Code does it

The stage runner passes the full allowlisted agent list to `claude -p`:

```
claude -p --agents "<a1>,<a2>,…" [--resume <sid>] "<stage prompt>"
```

The prompt tells Claude to dispatch to whichever listed agent best fits `stage.requires`. Claude Code's built-in agent routing handles the choice. No custom classifier, no confidence thresholds, no custom selection UI.

**Stage-level pin** — if a playbook stage has `agent: <name>`, only that agent is passed to `--agents`. Claude has one option.

**Post-hoc logging** — after the stage completes, the runner parses the transcript to find the first `Agent` tool invocation and records the chosen agent in `agent_picks`. Useful for audit, not for control.

### Guardrails

```yaml
# ~/.mopedzoom/config.yaml, agents section:
agents:
  allow: ["*"]                        # glob allowlist; default permissive
  deny:  ["untrusted-plugin/*"]
```

- **Allowlist** — filters the `--agents` list. Prevents a newly installed sketchy plugin from being usable.
- **Stage pin** — per-playbook determinism escape hatch.

### Failure mode

If the allowlisted set is empty, the stage fails at dispatch with a message pointing to `~/.claude/agents/` and `/mopedzoom:config`. No silent fallback.

## 7. Worktrees & deliverables

### Worktrees (only when `requires_worktree: true`)

- Location: `~/.mopedzoom/worktrees/<repo-slug>/<task-id>/`
- Branch: `mopedzoom/<task-id>-<short-slug>`
- Branched from `repos.<name>.default_branch` (read from config).
- Agent's `cwd` is set to the worktree path, so `git` / `gh` Just Work.

### Repo allowlist (in config.yaml)

```yaml
repos:
  trialroomai:
    path: ~/workspace/yitfit/trialroomai
    default_branch: main
    pr_reviewers: [NitinJ]
    aliases: ["trial", "trialroom"]    # optional — name variants the router matches
  mopedzoom:
    path: ~/workspace/mopedzoom
    default_branch: main
```

Tasks referencing any unlisted repo are rejected at intake. Prevents misclassification from clobbering a random repo.

### Repo selection UX

User refers to a repo by **name** (keys of the `repos` map). Resolution order:

1. **Extracted from task text** — router scans the request for any allowlisted name or alias.
2. **Default repo** (optional) — `repos.default: <name>` in config; used when none is mentioned.
3. **Prompted** — if still unresolved, the daemon sends an inline-button picker (Telegram) or an arrow-key selector (CLI) listing allowlisted repo names.

Example: *"Fix the 401 on /api/users in trial"* → matches the `trial` alias of `trialroomai`. *"Fix the login bug"* with a `default: trialroomai` config → uses trialroomai silently.

### Lifecycle

| Event | Action |
|---|---|
| First worktree-requiring stage | Create worktree; insert `worktrees` row (`state=active`). |
| Task `delivered` (PR merged or report committed) | Destroy worktree; delete remote branch only if PR merged (daily sweeper polls `gh pr view`). |
| Task `cancelled` / `failed` | Mark worktree `state=grace`; keep 7 days for forensics; sweeper removes later. |
| Daemon restart | Reconcile — any `active` worktree whose task is no longer live → `grace`. |

### Deliverable manifest

Each stage writes `<idx>-<name>.deliverable.json`:

```json
{
  "stage": "pre-design",
  "status": "ok",
  "artifacts": [
    {"type": "markdown", "path": "0-pre-design.md", "role": "primary"},
    {"type": "file-list", "paths": ["src/auth.py", "tests/test_auth.py"]}
  ],
  "notes": "Root cause: token expiry not validated. ~40 LoC change."
}
```

Next stage's prompt template references prior manifests so the agent sees the full trajectory.

### Artifact types mopedzoom understands

| Type | Final delivery |
|---|---|
| `markdown` | Render first 800 chars inline in Telegram; attach full file; optionally commit to a configured docs repo. |
| `commits` | List of SHAs + summaries. |
| `pr_url` | Clickable link + opening comment; task `delivered`. |
| `issue_url` | Clickable link; task `delivered`. |
| `file-list` | Context hint for downstream stages; not user-visible. |
| `custom` | Opaque; carried as context. |

## 8. Slash commands

All commands live under `~/workspace/mopedzoom/commands/*.md`. They talk to the daemon over the Unix socket.

| Command | Behavior |
|---|---|
| `/mopedzoom:init` | One-time wizard. Collects everything the daemon needs (see "Config scope" below). Walks through Telegram forum-group setup (create group → enable topics → add bot as admin with `can_manage_topics`), verifies via `getChatMember` + a test topic, checks `gh auth status`, writes `~/.mopedzoom/config.yaml`, installs systemd unit at `~/.config/systemd/user/mopedzoomd.service`, starts daemon. Idempotent. |
| `/mopedzoom:config` | Interactive editor covering **every** configurable setting (see "Config scope" below). Changes trigger `systemctl --user reload mopedzoomd`. |
| `/mopedzoom:submit <text>` | Submit a task. `--edit-stages` opens the per-submission stage-customization wizard. |
| `/mopedzoom:status [id]` | Daemon health (no id) or single-task state (with id). |
| `/mopedzoom:tasks` | **Interactive drilldown.** Lists tasks; arrow-key to select; enter opens detail view with stages, artifacts, events; actions: `[Pause] [Resume] [Cancel] [Re-run stage] [Open deliverable] [Tail logs]`. |
| `/mopedzoom:cancel <id>` | Cancel; kill subprocess; clean up worktree. |
| `/mopedzoom:resume <id>` | Resume a paused task, or re-run the current stage after an intermittent failure (e.g., transient `gh` error). |
| `/mopedzoom:edit <id>` | Open last deliverable in `$EDITOR` (local CLI only); on save, resume task. |
| `/mopedzoom:logs <id>` | Tail current stage transcript. |
| `/mopedzoom:ui` | Open the local web dashboard (`http://127.0.0.1:9876`) in the default browser. |
| `/mopedzoom:playbook:new` | Wizard: id → summary → triggers → inputs → worktree y/n → permission_mode → stages (repeatable). Validates, writes `~/.mopedzoom/playbooks/<id>.yaml`, reloads daemon. |
| `/mopedzoom:playbook:edit <id>` | Wizard pre-filled with existing playbook. |
| `/mopedzoom:playbook:list` | List with summaries. |
| `/mopedzoom:playbook:delete <id>` | Confirm + remove. Refuses if a task is currently using it. |

### Config scope (what `/mopedzoom:init` and `/mopedzoom:config` cover)

Every runtime-adjustable setting is editable through these commands — users should not need to open `config.yaml` manually.

| Section | Keys |
|---|---|
| **Channel** | Telegram bot token; group chat id; UX mode (`auto` \| `topics` \| `header`, default `auto`); forum/topic setup verification; test-send confirmation. |
| **Repos** | Allowlist: name, path, default_branch, aliases, pr_reviewers. Default repo (optional). |
| **Agents** | `allow` / `deny` globs. |
| **Permissions** | Default `permission_mode` (`bypass` \| `ask` \| `allowlist`); when `allowlist`: the tool/command allowlist itself. |
| **Deliverables** | Target for research markdown (repo name + subpath). PR body template. |
| **Concurrency & timeouts** | `max_concurrent_tasks`; default `stage.timeout`. |
| **Worktree housekeeping** | Grace period (default 7d); sweeper schedule. |
| **Dashboard** | Enable/disable; bind port (default 9876); open-on-`:ui`. |
| **Metrics** | Enable Prometheus endpoint; port. |

The local `bin/mopedzoom` CLI mirrors every command above for use outside Claude Code.

## 9. Built-in playbooks (ship v1)

1. **`research`** — inputs: `topic`. Stages: `pre-brief` (`approval: required`) → `research` (`approval: on-completion`) → `publish` (commits `.md` to the configured research repo). No worktree.

2. **`bug-file`** — inputs: `repo`, `description`. Stages: `draft` (`approval: required`) → `file` (`gh issue create`, `approval: none`). No worktree.

3. **`bug-fix`** — inputs: `repo`, `issue_ref`. Stages: `pre-design` (`approval: required`) → `implement` (`approval: on-completion`) → `open-pr` (`approval: none`). Worktree.

4. **`feature-impl`** — inputs: `repo`, `description`. Stages: `pre-design` (`approval: required`) → `design-doc` (commits `docs/…-design.md`, `approval: required`) → `impl-plan` (commits plan, `approval: required`) → `implement` (`approval: on-completion`) → `open-pr` (`approval: none`). Worktree.

## 10. Channel UX

### Disambiguating messages in Telegram (multiple concurrent tasks)

**Primary mode — Telegram forum topics.** The bot runs in a **forum-enabled group chat**. Each task opens its own **topic** (thread) with title `#47 · bug-fix · trialroomai`. All messages for the task — approvals, questions, final delivery — live inside that topic. User replies in the topic are naturally scoped to the task; no per-message header needed, no ambiguity on clicks or text replies.

**Topic lifecycle:**
- Task queued → bot calls `createForumTopic` with an icon emoji picked from the playbook (🐛 bug-fix, ✨ feature-impl, 🔎 research, 📮 bug-file).
- Task `delivered` / `failed` / `cancelled` → bot pins a closing message, then calls `closeForumTopic`. The thread stays readable as history but new messages are disabled.
- A "General" topic remains available for free-text submissions that haven't been assigned a task yet and for bot health notifications.

**Init-time setup.** `/mopedzoom:init` walks the user through:
1. Create a group, add the bot, promote to admin.
2. Convert the group to a **forum** (Telegram → group settings → Topics: on).
3. Grant the bot `can_manage_topics` rights (init verifies via `getChatMember`).
4. Bot sends a welcome message in General + creates a test topic to confirm permissions.

If any step fails, init explains exactly what's missing and offers to retry.

**Fallback — header mode.** If topics aren't available (1-on-1 chat with the bot, or the group doesn't have forum mode enabled), the daemon falls back to prefixing every message with `[#47 · bug-fix · trialroomai]` and using Telegram's reply-context to disambiguate user text. Free-form text without reply context: if exactly one task is awaiting input, the text goes there; otherwise the bot asks *"Which task? [#47] [#52] [new task]"*.

Config: `channel.mode: auto` (default) → topics when available, header otherwise. Can be forced to `topics` or `header` if needed.

### Telegram flows (in topics mode)

All messages below live inside the task's topic thread — the topic title `#47 · bug-fix · trialroomai` is the persistent header, so message bodies carry no prefix.

**Submit** (text goes in General, bot spawns a topic):
```
User → General:   "Fix the 401 on /api/users in trial"
Bot  → General:   → opened thread #47 · bug-fix · trialroomai
                  (link)
Bot  → Topic #47: 🎯 queued
                  [ Customize stages ]  [ Cancel ]
```

**Ambiguous playbook:**
```
Bot → Topic #47: 🤔 Two matches — which one?
                 [ Bug fix ]  [ Feature impl ]  [ Cancel ]
```

**Hard-gate approval:**
```
Bot → Topic #47: 📝 pre-design ready
                 ---
                 <first 800 chars of deliverable>
                 ---
                 [ ✅ Approve ]  [ ✏️ Revise ]  [ ❌ Cancel ]
```
"Revise" opens a reply prompt; the user's message inside the topic becomes the revision instruction and the stage re-runs.

**Mid-stage question:**
```
Bot → Topic #47: ❓ implement needs input:
                 "Apply the fix at the middleware or handler layer?"
                 [ middleware ]  [ handler ]  [ let agent decide ]
```

**Permission prompt (when `permission_mode: ask`):**
```
Bot → Topic #47: ⚠️ agent wants to run:
                 $ npm run migrate:prod
                 [ Allow once ]  [ Allow + remember ]  [ Deny ]
```

**On-completion gate:**
```
Bot → Topic #47: ✓ implement done
                 • 3 files changed, +42 −18
                 • 5/5 tests passing
                 Continue? [ Yes ] [ Show diff ] [ Revise ]
```

**Final delivery (then the topic closes):**
```
Bot → Topic #47: 🚀 delivered
                 PR: https://github.com/yitfit/trialroomai/pull/892
Bot → Topic #47: [topic closed]
```

Inline buttons use Telegram's `callback_query` API; each callback payload encodes the task-id explicitly (belt-and-suspenders). Free-form user text inside a topic is implicitly scoped to that task via `message_thread_id`.

### Local CLI

Same flows, terminal-rendered. `mopedzoom submit "…"` opens a live TUI: approval prompts inline, transcripts tailed. Non-interactive commands (`tasks`, `logs`, `cancel`) mirror the slash commands.

## 11. Permission handling

Claude Code agents may need to run commands or invoke tools that normally require per-call permission. mopedzoom configures this via `permission_mode`, set playbook-wide and overridable per stage.

### Modes

| Mode | Behavior |
|---|---|
| `bypass` | Daemon passes `--dangerously-skip-permissions` (or `--permission-mode bypassPermissions`) to `claude -p`. Agent runs autonomously. Rationale: every stage already has its own approval gate, so per-command prompts are redundant noise. **Default.** |
| `ask` | Daemon passes `--permission-prompt-tool mopedzoom_permission`. mopedzoom ships a tiny MCP tool by that name — when the agent needs permission, the tool writes `permission.json` into the scratch dir. The channel bridge picks it up and posts to the originating channel with `[Allow once] [Allow + remember] [Deny]`. On user reply, the MCP tool returns the decision to the agent. |
| `allowlist` | Same plumbing as `ask`, but the MCP tool first checks the configured allowlist (patterns like `npm run *`, `gh issue *`); matches auto-approve. Only unlisted invocations hit the channel. |

### Configuration

- **Global default** in `config.yaml` → `permissions.default_mode: bypass`.
- **Per-playbook** via `permission_mode:` at the top level.
- **Per-stage** via `permission_mode:` on a stage (overrides playbook).
- **Allowlist patterns** in `config.yaml` → `permissions.allowlist: [...]`.
- **"Remember" decisions** from `ask` mode append to an in-memory session allowlist (scoped to that task only); they do not persist across tasks.

### Where the plumbing lives

- `src/mopedzoomd/permissions.py` — small MCP server exposing `mopedzoom_permission`.
- Spawned by the stage runner when the resolved mode is `ask` or `allowlist`; shares the scratch dir via env var.
- On stage exit, the MCP process is torn down.

## 12. Web dashboard

A lightweight local-only web UI for visibility into what the daemon is doing. Read-only in v1 — all mutations stay on CLI/Telegram to keep the attack surface small and the feature scope tight.

### Stack

- **FastAPI** (Python, same process tree as daemon — lives alongside `mopedzoomd` as an optional subroutine).
- **Server-rendered HTML** with Jinja2 templates + **htmx** for live updates. No build step, no npm, no JS bundle. htmx polls relevant fragments every few seconds.
- **Binding**: `127.0.0.1:<port>`, default `9876`. Loopback only; no auth (same threat model as systemd user services).
- **Access**: `/mopedzoom:ui` opens `http://127.0.0.1:9876` in the default browser; on a remote machine the user SSH-forwards the port or pokes through Tailscale.

### Pages

| Route | Content |
|---|---|
| `/` | Dashboard: daemon health (uptime, queue depth, `max_concurrent_tasks` usage), latest 20 tasks with status pills, last 10 events from `task_events`. |
| `/tasks` | Filterable task list: by status, playbook, repo, date. |
| `/tasks/<id>` | Task detail: header (playbook, repo, inputs), stage timeline with durations, artifacts list (render markdown inline, download raw), transcripts (collapsible, tail-friendly), `task_events` log. |
| `/agents` | Discovered agent catalog: name, source path, description, times used (from `agent_picks`). |
| `/playbooks` | Playbooks (built-in + user) with summaries; read-only view of the YAML. |
| `/health` | JSON endpoint for Prometheus/alerting. |

### Enabling / disabling

In `/mopedzoom:config` → Dashboard section: `enabled: true/false`, `port: 9876`. Defaults on. Systemd unit starts the dashboard as part of the daemon; no separate service.

### Why keep it read-only in v1

- Clear separation of concerns: UI observes, channels (Telegram/CLI) control.
- Avoids CSRF and auth design work for a feature whose core value is "show me what's happening."
- Buttons like Pause/Cancel on the task detail page could link to the dashboard-initiated version of the Telegram callback — noted as a v2 open question.

## 13. Concurrency & failure handling

### Concurrency

- Tasks run in parallel; each gets its own worktree and `claude -p` subprocess.
- Soft cap: `max_concurrent_tasks: 4` in config. Over the cap → queued with a `Queued (N ahead)` notification.

### Failures

| Failure | Response |
|---|---|
| `claude -p` exits non-zero, no deliverable | Stage `failed`; post last 20 transcript lines; offer `[Retry] [Retry with hint] [Cancel]`. |
| Stage timeout (default 30 min/stage) | Same as above; per-stage override via `stage.timeout`. |
| Subprocess killed (daemon restart) | Re-dispatch same stage; prior transcript kept as `.killed`; session-id preserved via `--resume`. |
| Allowlist empty / no agents | Fail at dispatch; link user to `/mopedzoom:config`. |
| Telegram API down | Adapter backs off; local CLI still works; replays pending posts on reconnect. |
| Permission MCP unreachable (`ask` mode) | Stage parked; user notified; retry on reconnect. |
| `gh` / `git push` failure in PR stage | Stay at `implement` completed; notify; `/mopedzoom:resume <id>` or `[Retry]` re-runs just the PR step. |
| Disk full | Refuse new tasks; existing tasks continue if possible. |

## 14. Observability

- **Structured logs** → `~/.mopedzoom/logs/mopedzoomd.log`, rotated daily.
- **Prometheus metrics** (optional, off by default) at `127.0.0.1:9877/metrics`: tasks by status, stage durations, failure reasons, agent-pick distribution. Dashboard's port is `9876`; metrics get the next port to avoid collision.
- **Per-task audit** — `/mopedzoom:logs <id>` prints the `task_events` history + all stage transcripts.
- **Dashboard** — see Section 12; the primary observability surface.
- **Daemon health** — `/mopedzoom:status` shows uptime, queue depth, active tasks, last 5 errors.

## 15. Security

- Telegram bot locked to a single group chat id (set at init). Messages from any other chat are logged + ignored. Within the group, only topics the bot created (or the General topic) are processed; outside-created topics are ignored.
- `gh` operations use the user's existing `gh` credentials; no secrets in config.
- Repos are allowlisted; daemon refuses to touch anything outside.
- Worktrees are user-owned (no sudo); isolation via per-task dirs.
- Dashboard and metrics bound to loopback only; remote access requires the user to set up their own tunnel (SSH/Tailscale).
- `bypass` permission mode runs agents with full filesystem authority — rely on the stage-level approval gate and repo allowlist for safety. `ask` / `allowlist` modes available when stricter control is needed.

## 16. Build reuse

Known-good pieces we lean on instead of reinventing:

- **Claude Code** — subprocess runtime, `--resume`, agent routing via subagents, skills, `--permission-prompt-tool` hook.
- **python-telegram-bot** — Telegram adapter.
- **FastAPI + htmx** — dashboard; both mature; zero-build footprint.
- **`gh` CLI** — all GitHub ops (issue, PR, merge polling).
- **git worktrees** — native; isolation for parallel tasks.
- **systemd user units** — always-on lifecycle; `journalctl --user` for logs.
- **SQLite** — state, no external deps.
- **Superpowers skills already installed** — `writing-plans`, `executing-plans`, `finishing-a-development-branch`, `using-git-worktrees`, `brainstorming`, `systematic-debugging`. Relevant playbooks invoke these inside their stage prompts rather than reimplementing.

## 17. Out of scope (v1)

- Multi-user.
- Cloud-hosted daemon.
- Dashboard write actions (Pause/Cancel buttons in the web UI) — read-only in v1.
- Voice-driven intake.
- Non-GitHub providers (GitLab, Gitea).
- Auto-merge.
- Cross-repo refactors in a single task.
- Persisted "remember" decisions for `ask` permission mode across tasks.

## 18. Open questions (deferred past v1)

- Multi-channel identity (same user on Telegram *and* CLI should see one queue) — currently treated as separate `user_ref`s; revisit if it's annoying.
- Sub-task visualization for chained playbooks (parent/child) — v1 just shows a flat task list.
- Dashboard write actions (mutation from the web UI).
- Per-repo default permission mode (e.g., always `ask` for production-ish repos).
