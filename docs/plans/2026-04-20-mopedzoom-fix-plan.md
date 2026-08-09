# mopedzoom Fix Plan — 2026-04-20

## 1. Corroboration table

| # | Finding (AUDIT.md) | AUDIT section | Spec reference | Severity | Breaking risk |
|---|---|---|---|---|---|
| 1.1 | `sweeper.sweep_once` never called from `daemon.main()` / `build_daemon_from_config` | 1 | design §4 "Sweeper: Daily: remove worktrees/branches in grace-period state older than 7 days"; impl plan ships sweeper | major | low |
| 1.2 | `WorktreeManager` instantiated but `run_task` hard-codes `cwd=scratch.dir` (TODO at daemon.py:79); `requires_worktree: true` silently ignored | 1 | design §4 "Worktree manager" + §7 "only when `requires_worktree: true`"; bug-fix.yaml ships with `requires_worktree: true` | critical | low |
| 1.3 | `permission_mcp.handle_permission_request` never wired; no MCP server started | 1 | design §11 "`src/mopedzoomd/permissions.py` — small MCP server...Spawned by the stage runner when the resolved mode is `ask` or `allowlist`" | major | low (feature-flag) |
| 1.4 | `bridges.watch_scratch` dead code; `_run_stage` polls `read_question()` directly | 1 | design §4 "Channel bridge...watches the scratch dir for question.json and approval.json" | minor | low |
| 1.5 | `scratch.read_approval` / `clear_approval` never consulted; approval flow uses DB only | 1 | undocumented — design §4/§5 speaks of `approval.json` as the file contract, but execution uses DB | minor | low |
| 1.6 | CLI socket ops `status\|tasks\|cancel\|resume\|edit\|logs\|ui\|show-playbook` return `{ok:true}` stubs — every CLI command except `submit` is a no-op | 1, 6 | design §8 — every command has defined behavior; impl plan H24 | critical | low |
| 1.7 | No dashboard `/tasks` (collection) or `/events` streaming route | 1 | design §12 lists `/tasks` as a required page | minor | none |
| 2.1 | `publish` stage told to commit to research repo/path but `_build_prompt` never surfaces `cfg.deliverables.research_repo` / `research_path` | 2 | design §8 config scope "Target for research markdown (repo name + subpath)"; research.yaml publish stage | major | none |
| 2.2 | `LimitsConfig.default_stage_timeout_s` / `max_concurrent_tasks` / `grace_period_days` never read; `timeout: 30m` in bug-fix.yaml never enforced | 2 | design §13 "Stage timeout (default 30 min/stage)...per-stage override via `stage.timeout`" | major | low |
| 2.3 | `StageSpec.agent` vs discoverer: if discoverer returns `[]` AND `sspec.agent` is None, `claude` runs with no `--agents` flag — silent default | 2 | design §6 "If the allowlisted set is empty, the stage fails at dispatch...No silent fallback." | major | none |
| 2.4 | `resolve_interaction(answer=...)` accepts any free-text; first pending interaction resolves silently | 2 | undocumented edge case | minor | medium (tightening) — defer |
| 3.1 | `Router.__init__` requires `claude_client` positionally; `None` OK but unenforced | 3 | impl plan C9 — already covered by `test_router_construction.py` | minor | none |
| 3.2 | `TaskManager` requires `agent_discoverer` (only caller always supplies it) | 3 | undocumented | minor | none |
| 4.1 | Dashboard port: code=9876, docs (`init.md`, `ui.md`)=7777 | 4 | design §12 "default `9876`" — code is correct, docs are wrong | minor | none (docs-only) |
| 4.2 | `init.md` says "stage timeout=1h"; code default=1800 (30 min) | 4 | design §13 "default 30 min/stage" — code is correct, docs are wrong | minor | none (docs-only) |
| 4.3 | `config.md` prints section named "Telegram"; pydantic key is `channel` | 4 | design §8 config-scope table uses "Channel"; code uses `channel` | minor | none (docs-only) |
| 4.4 | `init.md` never asks about `metrics`, `deliverables.research_repo`, or `default_repo` | 4 | design §8 "every runtime-adjustable setting is editable through these commands" | minor | none (docs-only) |
| 5.1 | `submit_task` fires `asyncio.create_task(self.run_task(task_id))` with no handler — crashes vanish silently | 5 | undocumented; test_daemon_submit_task pins "scheduling, not exception-handling" | critical | low |
| 5.2 | `CLISocketChannel._serve` has `except Exception: pass` on `writer.wait_closed()` | 5 | undocumented (intentional cleanup) | minor | none |
| 5.3 | `Router.pick` silently eats `json.JSONDecodeError`; indistinguishable from "no match" | 5 | undocumented | minor | none |
| 5.4 | `bridges.watch_scratch` silently `continue`s on malformed bridge file, stalling forever | 5 | undocumented | minor | none |
| 6.1 | Every slash command except `/mopedzoom:submit` hits the CLI no-op stubs | 6 | design §8; same as 1.6 | critical | low |
| 6.2 | `bin/mopedzoom show-playbook` has no invoking slash command | 6 | undocumented — prose reference only in `submit.md` | minor | none |
| 6.3 | `plugin.json` lists `playbook/new\|edit\|delete\|list` but they are prose-only instructions | 6 | design §8; impl plan ships them as Claude-driven wizards | minor | none |

## 2. Out of scope

- **2.4** free-text approval resolver: fixing "first pending interaction silently resolves" requires validating `answer` values and would tighten a contract currently loose; belongs to v0.2 with a proper state-machine guard.
- **5.2 / 5.3 / 5.4** intentional-suppression clauses: logging-only improvements; defer to a single observability polish pass in v0.2.
- **6.2 / 6.3** CLI ↔ slash-command parity for `show-playbook` and playbook wizards: purely documentation/plugin-manifest tidy; not functional gaps.
- **3.2** `TaskManager` agent_discoverer: only called by one code path that always supplies it; no bug; adding a default is churn, not a fix.
- **1.7** `/tasks` collection page and `/events` streaming: fully new dashboard surface; design-level addition, not a wiring fix. Track as v0.2 UI work.

## 3. Fix batches

Order: critical → major → minor. Within each tier, order by dependency (enabling helpers land first).

---

### Batch 1 — Stop silent crashes and dead CLI ops

- **Goal:** Turn the four currently-invisible failures (create_task swallowing, hard-coded scratch cwd, CLI stubs, silent `claude` invocation with no agents) into observable behavior without changing any external contract.
- **Findings addressed:** 1.2, 1.6, 2.3, 5.1, 6.1.
- **Files touched:**
  - `src/mopedzoomd/daemon.py` — add a `_spawn_supervised(coro)` helper that wraps `asyncio.create_task` with a done-callback logging any exception via `LOG.exception`; use it in `submit_task`. Add `TaskManager` methods `get_status(task_id)`, `list_tasks(filters)`, `cancel(task_id)`, `resume(task_id)`, `tail_logs(task_id, n)`, `edit_stage(task_id, idx, body)`, `show_playbook(pb_id)` — all additive, all returning plain dicts serializable to JSON. Thread `requires_worktree` through `_run_stage` by adding an Optional wmgr-use branch in `run_task` that creates the worktree only when `pb.requires_worktree and self.worktree_mgr` AND `task.inputs` resolves a repo (fallback to scratch otherwise — preserves current behavior).
  - `src/mopedzoomd/channels/cli_socket.py` — replace the stub dispatch with calls into the handler's new op surface. Extend the handler signature: instead of just `handler(inbound_msg)` for submit, add `handler(op=..., payload=...)` variant OR introspect op on the inbound and dispatch through new `TaskManager` methods via a small adapter `build_cli_op_handler(tm)`.
  - `src/mopedzoomd/stage_runner.py` — raise `NoAgentsAvailable` when `agents == []` (new exception class in same module); caller already surfaces stage_failed cleanly.
- **Non-breaking guarantee:**
  - `_spawn_supervised` keeps fire-and-forget semantics — callers still get the task id synchronously; existing `test_submit_task_swallows_run_task_exceptions` pins current-swallow behavior but asserts only that `submit_task` returns happily, which still holds. We add a second assertion in a new test that the exception is logged.
  - Worktree branch runs only when `pb.requires_worktree` is true; existing playbooks without the flag and the existing test `test_requires_worktree_playbook_runs_in_scratch_not_worktree` will need to be updated or re-interpreted. Current pinned test explicitly asserts the TODO; we update it to the new correct behavior and add a separate test pinning the fallback (no worktree_mgr → still scratch dir).
  - CLI stubs: the new dispatch returns the same `{"ack": True, "op": op, "ok": True}` plus additional keys on success. The pinned test `test_cli_socket_status_op_is_stub` must be updated to reflect wired behavior. Old fields remain present.
  - `NoAgentsAvailable`: only raised when both sides of the existing OR return nothing. Current playbooks always declare `sspec.agent` or run in setups that have agents; behavior was previously silently broken.
- **Implementation sketch:**
  1. In `daemon.py`: add `_spawn_supervised(coro) -> asyncio.Task` that attaches `.add_done_callback(lambda t: LOG.exception(...) if t.exception())`. Replace line 55.
  2. Add `TaskManager.cancel(tid)` → sets status CANCELLED; `TaskManager.resume(tid)` → if PAUSED, sets RUNNING, spawns `run_task`; `TaskManager.get_status(tid)` → returns `{id, status, stages:[...], last_event:...}`; `TaskManager.list_tasks(limit=...)`; `TaskManager.tail_logs(tid,n)` returns last-n lines of current stage transcript; `TaskManager.edit_stage(tid, idx, new_body)` → writes revision marker to scratch and re-queues; `TaskManager.show_playbook(pb_id)` → `pb.model_dump()`.
  3. In `cli_socket.py` replace the `elif op in {...}` block with a small dispatcher that the daemon wires in `build_daemon_from_config` by passing an `op_handler: Callable[[str, dict], Awaitable[dict]]` into the channel constructor (new optional kwarg, default None → old stub path preserved for any external callers). Set the op_handler in `build_daemon_from_config` after `tm` exists.
  4. In `run_task`: if `pb.requires_worktree and self.worktree_mgr and task.inputs.get("repo") in self.worktree_mgr.allowed`, call `wmgr.create(task.id, repo, slug=pb.id)`, use returned path as `cwd`, insert `worktrees` row. Else keep current behavior. Add a try/except that, on task termination, marks worktree state=`grace` (for cancel/fail) or destroys (for delivered).
  5. In `stage_runner.py`: raise `NoAgentsAvailable` if the resolved agents list is empty AND `sspec.agent is None`. `_run_stage` catches and treats as `_StageFailed` with a specific log/channel message.
- **Tests to add:**
  - `test_spawn_supervised_logs_exception` — asserts `LOG.exception` called when supervised coroutine raises.
  - `test_cli_socket_status_dispatches_to_handler` — replaces/augments `test_cli_socket_status_op_is_stub`; verifies op goes through the handler and status dict is returned.
  - `test_cli_op_cancel_sets_task_cancelled`.
  - `test_cli_op_tasks_lists_tasks`.
  - `test_requires_worktree_creates_worktree_and_uses_it_as_cwd` — new test pins fixed behavior; update the old xfail-style test or convert to pin of fallback (no wmgr supplied).
  - `test_stage_runner_raises_on_empty_agents`.
- **Verification:** `cd /home/nitin/workspace/mopedzoom && python -m pytest tests/ -x -q`.
- **Rollback plan:** pure `git revert`. No DB migrations; no config file rewrites.

---

### Batch 2 — Enforce timeouts and surface research destination

- **Goal:** Make `StageSpec.timeout` and `LimitsConfig.default_stage_timeout_s` actually bound subprocess runtime; expose `deliverables.research_repo` to the publish stage.
- **Findings addressed:** 2.1, 2.2.
- **Files touched:**
  - `src/mopedzoomd/stage_runner.py` — accept `timeout_s: float | None = None` kwarg on `StageRunner.run`; when set, wrap `proc.wait()` in `asyncio.wait_for`, on timeout SIGTERM then SIGKILL; return `StageResult(exit_code=-1, ...)`.
  - `src/mopedzoomd/daemon.py` — in `_run_stage`, parse `sspec.timeout` ("30m"/"1h"/"1800s") into seconds; fall back to `self.limits.default_stage_timeout_s`. Store `LimitsConfig` on `TaskManager` (new optional field with default = factory producing defaults).
  - Extend `_build_prompt` to insert — only when stage name is `publish` and `cfg.deliverables.research_repo` is non-None — a line "Commit the report into repo `<research_repo>` at path `<research_path>`." The `DeliverablesConfig` must be threaded via new optional `TaskManager.deliverables: DeliverablesConfig | None = None`. Default None preserves current behavior.
  - `build_daemon_from_config` — pass `limits=cfg.limits` and `deliverables=cfg.deliverables` to `TaskManager`.
  - Tiny util `_parse_duration(s)` in `daemon.py` (`30m`, `1h`, `90s`, int seconds).
- **Non-breaking guarantee:**
  - `StageRunner.run` — `timeout_s` is a new kwarg defaulting to `None` (unbounded). Existing test `test_stage_runner_ignores_stage_timeout` asserts `"timeout" not in sig.parameters`; must be updated to assert `"timeout_s" in sig.parameters` after fix — this is an additive signature change, fully backward-compatible at call sites.
  - `TaskManager.limits` / `deliverables` — new optional fields with defaults. Existing constructor calls unchanged.
  - Publish-stage prompt addition is conditional on stage name and config presence; research playbook without `research_repo` set sees no change.
- **Implementation sketch:**
  1. `StageRunner.run(..., timeout_s: float | None = None)` → if set, wrap wait in `asyncio.wait_for`; on `TimeoutError`, `proc.terminate()`, short wait, `proc.kill()`, then return a `StageResult` with `exit_code=-1`.
  2. `TaskManager` dataclass: add `limits: LimitsConfig = field(default_factory=LimitsConfig)` and `deliverables: DeliverablesConfig | None = None` (import inside daemon.py).
  3. `_run_stage` — `timeout_s = _parse_duration(sspec.timeout) if sspec.timeout else self.limits.default_stage_timeout_s` then pass to runner.
  4. `_build_prompt` — if `sspec.name == "publish"` and self.deliverables and self.deliverables.research_repo: append one-line "Commit to repo `<name>` at path `<path>`."
  5. In `build_daemon_from_config`, pass `limits=cfg.limits, deliverables=cfg.deliverables`.
- **Tests to add:**
  - `test_stage_runner_enforces_timeout` — monkey-patch subprocess to hang; assert returns with `exit_code=-1` near `timeout_s`.
  - `test_run_stage_respects_stagespec_timeout` — verify parser + pass-through.
  - `test_run_stage_falls_back_to_limits_default` — no stage timeout → limits value.
  - `test_build_prompt_publish_stage_includes_research_repo_when_configured`.
  - `test_build_prompt_publish_stage_silent_when_unconfigured`.
  - `test_build_prompt_non_publish_stage_never_mentions_research_repo`.
- **Verification:** `cd /home/nitin/workspace/mopedzoom && python -m pytest tests/ -x -q`.
- **Rollback plan:** `git revert`. Config schema untouched (field was already declared).

---

### Batch 3 — Wire the sweeper and the permission MCP (feature-flagged, defaulting off)

- **Goal:** Start calling `sweeper.sweep_once` on a schedule and wire `handle_permission_request` into `StageRunner` when `permission_mode != "bypass"`, behind config flags that default to current (unwired) behavior so this release can ship without surprise.
- **Findings addressed:** 1.1, 1.3.
- **Files touched:**
  - `src/mopedzoomd/config.py` — add `LimitsConfig.sweeper_enabled: bool = False` and `LimitsConfig.sweeper_interval_s: int = 3600`. Add `PermissionsConfig.mcp_enabled: bool = False`. All default False — does not break existing configs that have no value (absent → default) and does not break configs that explicitly set any other subset.
  - `src/mopedzoomd/daemon.py`:
    - in `main()` (or `build_daemon_from_config`), when `cfg.limits.sweeper_enabled`, launch a supervised background task looping `await sweep_once(db, worktree_mgr=wmgr, grace_days=cfg.limits.grace_period_days); await asyncio.sleep(cfg.limits.sweeper_interval_s)`.
    - when `cfg.permissions.mcp_enabled and resolved_mode in {"ask","allowlist"}`, pass the scratch dir + allowlist to `StageRunner.run(permission_handler=handle_permission_request, ...)` (new optional kwarg). Otherwise use current code path.
  - `src/mopedzoomd/stage_runner.py` — accept `permission_handler` kwarg (default None). If set and mode != bypass, spawn a small asyncio task to service `permission.json` requests by calling the handler and writing the response file into scratch; cancel on stage exit.
- **Non-breaking guarantee:**
  - New config fields default False → unchanged behavior for every existing `~/.mopedzoom/config.yaml`.
  - `StageRunner.run` new kwarg defaults to None → existing tests and callers unaffected.
  - Sweeper is opt-in for this release; becomes default True in v0.2 with migration note.
  - `test_sweeper_not_imported_by_daemon` and `test_permission_mcp_not_wired_into_daemon` in `test_audit_wiring.py` will need to flip: they are intentional pins that the audit author wrote to surface these gaps, and are expected to fail once wired. Update to assert the imports are now present (the audit tests themselves call this out: "If/when the daemon finally starts sweeping, update this test to assert the import IS present.").
- **Implementation sketch:**
  1. `config.py`: extend `LimitsConfig` and `PermissionsConfig` with new Optional-shaped booleans + interval int.
  2. `daemon.py`: after building `daemon` but before `run_all`, `if cfg.limits.sweeper_enabled: _spawn_supervised(_sweeper_loop(...))`. `_sweeper_loop` catches and logs exceptions per-iteration; never dies.
  3. `stage_runner.py`: when `permission_handler` is provided, start an inner task that polls `scratch.dir/permission.json`, calls the handler, writes `permission_response.json`. Inner task cancelled in a `finally`.
  4. Update the two audit tests (`test_sweeper_not_imported_by_daemon`, `test_permission_mcp_not_wired_into_daemon`) to assert the imports ARE present — per their own docstring directives.
- **Tests to add:**
  - `test_sweeper_loop_invokes_sweep_once_and_sleeps` — use a fake `sweep_once` + `asyncio.sleep` patch.
  - `test_sweeper_disabled_by_default_does_not_launch` — `cfg.limits.sweeper_enabled=False` (default) → no sweep task.
  - `test_stage_runner_passes_permission_handler_when_set`.
  - `test_stage_runner_no_permission_handler_when_bypass`.
- **Verification:** `cd /home/nitin/workspace/mopedzoom && python -m pytest tests/ -x -q`.
- **Rollback plan:** `git revert`. Config fields added as defaults, so revert leaves existing configs valid.

---

### Batch 4 — Documentation alignment (port, stage timeout, section name, missing sections)

- **Goal:** Reconcile doc drift in favor of code defaults so installed users aren't surprised. No code default changes.
- **Findings addressed:** 4.1, 4.2, 4.3, 4.4.
- **Files touched:**
  - `commands/init.md` — change `7777` → `9876`; change `stage timeout=1h` → `stage timeout=30m (default 1800s)`; add a Step 5b prompting for `metrics.enabled/port`, `deliverables.research_repo`, `deliverables.research_path`, `default_repo`.
  - `commands/ui.md` — `7777` → `9876`.
  - `commands/config.md` — rename "Telegram" → "Channel" in the summary section list; note the pydantic key is `channel`.
- **Non-breaking guarantee:**
  - Pure docs changes. No code defaults change.
  - Pinned audit tests (`test_init_md_dashboard_port_does_not_match_config_default`, `test_ui_md_dashboard_port_does_not_match_config_default`, `test_init_md_stage_timeout_does_not_match_limits_default`) must be inverted (they currently assert the wrong value IS present; after fix they assert the correct value is present).
- **Implementation sketch:**
  1. Edit `init.md` Step 6 concurrency/timeouts line.
  2. Add Step 5b in `init.md` for metrics + deliverables + default_repo.
  3. Edit `ui.md` line 5.
  4. Edit `config.md` line 5.
  5. Flip the three doc-drift audit tests.
- **Tests to add:**
  - Update the three existing audit tests. No new tests.
- **Verification:** `cd /home/nitin/workspace/mopedzoom && python -m pytest tests/ -x -q`.
- **Rollback plan:** `git revert`. No runtime impact.

---

### Batch 5 — Observability of scratch contract (minor consistency)

- **Goal:** Make `scratch.read_approval` / `clear_approval` visibly usable by routing a code path through them alongside the DB (additive only), and let `bridges.watch_scratch` be used as the single source of mid-stage event detection inside `_run_stage`. This is internal rewiring: external behavior unchanged.
- **Findings addressed:** 1.4, 1.5.
- **Files touched:**
  - `src/mopedzoomd/daemon.py` — factor the `read_question` poll in `_run_stage` to a helper that consults `watch_scratch` for a single-pass check (still returns the same dict when `question.json` is present).
  - Keep DB-based approval authoritative; add a call to `scratch.read_approval` that, if present, is logged as a `scratch_approval_seen` event for audit, then `clear_approval` is called. Does not drive state.
- **Non-breaking guarantee:** pure additive instrumentation; DB remains source of truth. All existing tests pass unchanged.
- **Implementation sketch:**
  1. Helper `_drain_scratch_bridges(scratch) -> dict[str, dict]` that imports `watch_scratch` lazily and does a single-pass poll (or uses `FILES` constant directly).
  2. In `_run_stage`, after subprocess exit, call helper; act on `question` as today; for `approval`, log-and-clear.
- **Tests to add:**
  - `test_run_stage_drains_approval_scratch_file_if_present` — puts approval.json in scratch, verifies it is cleared and an event is logged.
  - `test_daemon_imports_watch_scratch` — the audit pin `test_bridges_not_imported_by_daemon` and `test_scratch_approval_and_permission_readers_not_consulted` must be flipped once this lands.
- **Verification:** `cd /home/nitin/workspace/mopedzoom && python -m pytest tests/ -x -q`.
- **Rollback plan:** `git revert`.

---

## 4. Sequencing rationale

Batch 1 lands first because it eliminates the three loudest user-visible failures — silent `run_task` crashes, no-op CLI commands, and silently-ignored `requires_worktree` — all without changing any config schema or external command. It also introduces the `_spawn_supervised` helper and the CLI op-handler wiring, which the later batches lean on.

Batch 2 follows because the timeout and research-repo fixes require new (optional) fields on `TaskManager` — simpler to add on top of Batch 1's already-touched `TaskManager` dataclass than to interleave. Neither changes existing external contracts; both are pure threading of already-declared config values.

Batch 3 (sweeper + permission MCP) is riskier because it activates previously-dead subsystems. It ships opt-in behind flags that default to the current unwired state, so the release is non-breaking. Users who want the features flip a flag in `/mopedzoom:config`.

Batches 4 and 5 are low-risk polish — docs and observability — saved for last to avoid perturbing the critical-path fixes.

## 5. Explicitly excluded (deferred to v0.2 with migration plans)

- **Flip sweeper / permission-MCP defaults to enabled.** Once Batch 3 has baked in the field, v0.2 can change the default to `True` and add a one-line changelog note.
- **Reject free-text approval replies (finding 2.4).** v0.2 can introduce a whitelist of accepted answer tokens (`approve`/`revise`/`cancel`/`pause`/`resume`) and a clarifier re-prompt for others. Today's loose behavior must remain available via an opt-in `permissions.strict_approval_answers: false` flag in v0.2 to preserve any automation that assumed the current behavior.
- **Rename `config.md`'s "Telegram" section.** Already handled in Batch 4 as docs-only.
- **Router JSON-parse logging (5.3) and bridges malformed-file stall (5.4).** v0.2 observability pass with structured-log hooks.
- **Dashboard `/tasks` + `/events` surfaces (1.7) and `show-playbook` slash command (6.2).** Additive UI features, not wiring fixes.
- **Tighten `default_repo` / `research_repo` references against the repo allowlist.** Today the daemon refuses unlisted repos at worktree-create, but `deliverables.research_repo` is free-form string. v0.2 can add a validator that rejects unknown names; v0.1.x stays permissive.
