# mopedzoom audit — integration / wiring gaps

Audit date: 2026-04-19. Scope: same failure modes as the Telegram-bring-up fixes.
One line per finding. `✓` = regression test added. `—` = not testable without architecture change.

## 1. Defined-but-unwired components

- ✓ `sweeper.sweep_once` never called from `daemon.main()` / `build_daemon_from_config` — orphaned worktrees will never be reaped. (`src/mopedzoomd/daemon.py:333`, `src/mopedzoomd/sweeper.py:11`)
- ✓ `WorktreeManager` is instantiated (`daemon.py:345`) and stored on `TaskManager.worktree_mgr` but `run_task` hard-codes `cwd = str(scratch.dir)  # TODO: worktree if pb.requires_worktree` (`daemon.py:79`). Playbooks like `bug-fix.yaml` declaring `requires_worktree: true` are silently ignored.
- ✓ `permission_mcp.handle_permission_request` is never wired — no MCP server is started, `scratch.read_permission` / `clear_permission` are never called from the run loop (`daemon.py` has no reference).
- ✓ `bridges.watch_scratch` is dead code relative to the daemon — `_run_stage` polls `scratch.read_question()` directly. If anyone ever adds approval/permission bridging they will duplicate logic.
- ✓ `scratch.read_approval` / `read_approval`'s sibling `clear_approval` never consulted by the run loop; approval flow uses only DB interactions — the scratch-file contract is orphaned.
- ✓ CLI socket ops `status|tasks|cancel|resume|edit|logs|ui|show-playbook` return `{"ok": true}` stubs (`channels/cli_socket.py:57-71`). `bin/mopedzoom` exposes these subcommands but the daemon never dispatches; every CLI command except `submit` is a no-op.
- — No dashboard routes for `/tasks` (collection) or `/events` streaming; only a task-detail route. Minor.

## 2. Implicit contracts between components

- ✓ `publish` stage in `playbooks/research.yaml` is told to "Commit report to the configured research repo/path" but `_build_prompt` never surfaces `cfg.deliverables.research_repo` / `research_path` to the subprocess. The subprocess has no way to know the destination. (`config.py:42-45`, `daemon.py:202`)
- ✓ `LimitsConfig.default_stage_timeout_s` / `max_concurrent_tasks` / `grace_period_days` are declared but never read (grep returns zero call sites). Playbook-level `timeout: 30m` in `bug-fix.yaml` is also never enforced by `StageRunner.run` — subprocess runs unbounded.
- ✓ `StageSpec.agent` vs. agent-discoverer contract: `_run_stage` passes `agents=[sspec.agent]` into `StageRunner`, but if the discoverer returns `[]` AND `sspec.agent` is None, `claude` CLI still runs with no `--agents` flag — silent "no agent" default that may differ from user expectation.
- — `resolve_interaction(answer=...)` accepts any free-text reply and resolves the FIRST pending interaction; a user typing "yeah sure" during an approval flow is silently ignored (no branch hit) but the interaction is still deleted. Contract between channel and DB is implicit.

## 3. Required constructor args / startup crash paths

- ✓ `Router.__init__(registry, claude_client, ...)` requires `claude_client` positionally — passing `None` is documented in fixed code (`daemon.py:381`) but nothing in the codebase enforces/tests that contract. Covered by new `test_router_construction.py`.
- — `TaskManager` requires `agent_discoverer`; no default. The only instantiator (`build_daemon_from_config`) always supplies it, so OK.

## 4. Schema mismatches (init / docs / model)

- ✓ Dashboard port default mismatch: `DashboardConfig.port = 9876` (`config.py:34`) but `commands/init.md:32` says `dashboard port=7777` and `commands/ui.md:5` says "default `7777`".
- ✓ `commands/init.md:32` says "stage timeout=1h"; `LimitsConfig.default_stage_timeout_s = 1800` (30 min) in `config.py:50`.
- ✓ `commands/config.md:5` prints section named "Telegram" — the Pydantic key is `channel`.
- — `commands/init.md` never asks about `metrics`, `deliverables.research_repo`, or `default_repo`, though all are in `Config`. Idempotent re-init would lose these if rewritten from prompts.

## 5. Error-swallowing

- ✓ `TaskManager.submit_task` fires `asyncio.create_task(self.run_task(task_id))` at `daemon.py:55` — the returned task is not awaited, not stored, and has no exception handler. Any crash in `run_task` disappears silently.
- — `CLISocketChannel._serve` has `except Exception: pass` on `writer.wait_closed()` (`channels/cli_socket.py:80`). Intentional cleanup, low risk.
- — `Router.pick` catches `json.JSONDecodeError` silently and returns None — LLM parse failures indistinguishable from "no match". Logged nowhere.
- — `bridges.watch_scratch` catches `json.JSONDecodeError` and silently `continue`s — a malformed bridge file stalls the watcher forever.

## 6. CLI / entrypoint parity

- ✓ Every slash command except `/mopedzoom:submit` is wired to a CLI subcommand that resolves to a no-op at the daemon (see finding 1.6). Concretely broken: `/mopedzoom:status`, `/mopedzoom:tasks`, `/mopedzoom:cancel`, `/mopedzoom:resume`, `/mopedzoom:edit`, `/mopedzoom:logs`, `/mopedzoom:ui`.
- — `bin/mopedzoom` has a `show-playbook` subcommand but no slash command invokes it; referenced only by `submit.md` prose.
- — `plugin.json` lists `playbook/new|edit|delete|list` but the commands are purely prose instructions to Claude; no verification that the referenced `mopedzoomd.playbooks:load_playbooks` path stays stable. Non-runtime.

## Pre-existing test status

At audit start: `112 passed, 0 failed` (excluding the `tests/integration/*` directory which has its own `conftest.py` and separate collection). No pre-existing regressions.
