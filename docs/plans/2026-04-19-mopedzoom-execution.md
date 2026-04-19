# mopedzoom Execution Plan — Parallel Multi-Agent Delivery

> **For the orchestrator (you):** This document is the companion to `2026-04-19-mopedzoom-implementation.md`. The implementation plan defines *what* to build task-by-task; this plan defines *how to dispatch* those tasks across parallel background agents to minimize wall-clock time. Read the implementation plan first — this plan references its Task IDs (A0, B2, C8, …) verbatim.

**Goal:** Deliver the v1 mopedzoom plugin as fast as possible by running independent implementation tasks in parallel git worktrees, with the main thread acting purely as orchestrator.

**Strategy:** Subagent-driven development using `superpowers:subagent-driven-development`. Each parallel "lane" is one background agent working in its own worktree, owning one or more sequential tasks. Between phases, the orchestrator merges lanes and runs an integration check before fanning out the next wave.

**Expected speedup:** ~3–4× over strict sequential execution. Critical path drops from ~30 tasks to ~11 sync points.

---

## 0. Ground Rules for the Orchestrator

1. **Delegate-first.** You do not write code in the main thread. Every implementation Task (A0…J30) is dispatched to a subagent with `run_in_background: true` unless it's a sync point that must finish before the next wave can start.
2. **One worktree per lane.** Each lane operates in its own git worktree at `~/workspace/mopedzoom-wt/<lane-name>`. The orchestrator creates worktrees with `git worktree add` and hands the path to the dispatched agent.
3. **Agent brief = self-contained.** Each Agent call must include: (a) the lane's worktree path, (b) the full Task block copy-pasted from the implementation plan, (c) "use TDD; commit after each step; report the final commit SHA". Never say "implement Task B2 from the plan" — the subagent has no context.
4. **Sync points are hard gates.** At each sync point, all in-flight agents must report done. The orchestrator merges their branches into `main`, runs the test suite on main, then fans out the next wave. If any agent fails, fix before proceeding.
5. **Two-stage review.** After a lane reports complete, dispatch a fresh `superpowers:code-reviewer` agent pointed at the diff. Only merge after review passes. Reviewer runs in its own background task; merge when review clears.
6. **Live progress snapshots.** Emit a `┌─ Progress ───┐` block every ~30 s or on any lane state change.

---

## 1. Dependency Graph (condensed)

```
A0 ──► A1 ──► B2 ──► B3 ──┬─► B4 ──┐
                          │        ├─► C9 ──┐
                          └─► B5 ──┤        │
                                   └─► C10 ─┼─► D11 ──┐
                          B6 ──► C8 ────────┘         │
                                                       │
                          B7 ─────────────────► D12 ──┤
                          B7 ─────────────────► D13 ──┤
                                                       │
                          E14 ──┬─► E15                │
                                └─► E16                │
                                                       │
                                           F17 ◄───────┘
                                           F17 ──► F18 ──► F19 ──► F20 ──► F21
                                                                            │
                                                                            ├─► G22 ──► G23
                                                                            ├─► H24
                                                                            │    H25 ──► H26
                                                                            │
                                           C8 ──► I27 (independent lane)
                                                                            │
                                                                            └─► J28 ──► J29 ──► J30
```

Readings:
- A0 and A1 are strictly serial — project has to exist before anything else can be written.
- After A1, **three lanes** can run in parallel: Data (B2→B3→{B4,B5}), Config (B6), Scratch (B7).
- Phase C fans out from B6 (for C8) and B5 (for C10); C9 waits for both C8 and B4.
- Phase D: D11 is the integrator, D12 and D13 are independent utilities that D11 wires in.
- Phase E: one serial task (E14) then two parallel (E15, E16).
- Phase F is mostly serial — the daemon wires everything together — but F18, F19, F20 are small additions on F17 and can be assigned to the same lane.
- **Phase I (built-in playbooks) is embarrassingly parallel** and only needs C8 — kick it off early and let it run in the background across multiple waves.

---

## 2. Lane Definitions

Six parallel lanes are used. At any wave, 2–4 are active.

| Lane ID | Name | Worktree branch | Task ownership |
|---------|------|-----------------|----------------|
| L0 | Bootstrap | `lane/bootstrap` | A0, A1 |
| L1 | Data | `lane/data` | B2, B3, B4, B5 |
| L2 | Config-Scratch | `lane/config-scratch` | B6, B7 |
| L3 | Playbooks-Routing | `lane/playbooks` | C8, C9, C10 |
| L4 | Stage-Exec | `lane/stage-exec` | D11, D12, D13 |
| L5 | Channels | `lane/channels` | E14, E15, E16 |
| L6 | Daemon | `lane/daemon` | F17, F18, F19, F20, F21 |
| L7 | Surface | `lane/surface` | G22, G23, H24, H25, H26 |
| L8 | Playbooks-Content | `lane/playbooks-content` | I27 (research + bug-file + bug-fix + feature-impl YAMLs) |
| L9 | Deploy | `lane/deploy` | J28, J29, J30 |

Note: L3 merges before L4 can start because D11 imports from C9/C10. Lanes with conflicting file paths never run concurrently on the same file.

---

## 3. Wave-by-Wave Execution

Each wave below lists which lanes run, the sync action at wave end, and the wall-clock critical-path task.

### Wave 1 — Bootstrap (serial, ~1 task wall time)

**Active:** L0 only.
**Tasks:** A0 → A1.

**Dispatch:**
```
Agent(L0): worktree=lane/bootstrap, execute A0 then A1 inline.
           Deliverables: repo skeleton + failing smoke test. Report final SHA.
```

**Sync:** Merge `lane/bootstrap` → `main`. Run `pytest` on main — must show the smoke test passing.

**Reason this wave is serial:** every other lane needs the project scaffold to exist.

---

### Wave 2 — Data + Config fan-out (3 parallel, ~B-phase wall time)

**Active:** L1, L2, L8.

**Dispatch (all concurrent, `run_in_background: true`):**
```
Agent(L1): worktree=lane/data, execute B2, B3, B4, B5 sequentially.
           Files owned: src/mopedzoom/{models.py, state.py}, tests/test_state.py.
Agent(L2): worktree=lane/config-scratch, execute B6 then B7.
           Files owned: src/mopedzoom/{config.py, scratch.py}, tests/{test_config.py, test_scratch.py}.
Agent(L8): worktree=lane/playbooks-content, execute I27 (write 4 YAML playbooks).
           Files owned: playbooks/{research,bug-file,bug-fix,feature-impl}.yaml + their tests.
           NOTE: I27 depends conceptually on C8's schema — hand the agent the PlaybookSpec
           pydantic model pasted inline from the design doc, so it can validate ahead of C8.
```

**Sync:** Merge L1, L2, L8 into main in that order. Run full `pytest`. No file overlap, so merges should be clean.

**Critical path:** L1 (B2→B3→B4→B5 is 4 tasks). L2 and L8 finish earlier and idle.

---

### Wave 3 — Playbooks + Routing (1 lane, 3 tasks)

**Active:** L3.
**Tasks:** C8, C9, C10 (C8 and C10 could parallelize but the sequential gain isn't worth another worktree — they touch `playbooks.py`/`worktree.py` and both import from Wave 2).

**Dispatch:**
```
Agent(L3): worktree=lane/playbooks, execute C8 → C10 → C9 (C9 last, it depends on both).
           Files owned: src/mopedzoom/{playbooks.py, router.py, worktree.py} + tests.
```

**Sync:** Merge L3 → main. Run pytest. This is a critical sync: L4, L6, L8 revalidation, L7 all read from here.

---

### Wave 4 — Stage-Exec + Channels + Content-revalidation (3 parallel)

**Active:** L4, L5, L8-revalidate.

**Dispatch:**
```
Agent(L4): worktree=lane/stage-exec, execute D12, D13 in parallel via internal step order,
           then D11 (which imports D12+D13). Single agent handles all three — they're in
           the same module neighborhood and fast to write.
           Files owned: src/mopedzoom/{stage_runner.py, bridges.py, permission_mcp.py}.
Agent(L5): worktree=lane/channels, execute E14 → {E15, E16}.
           Files owned: src/mopedzoom/channels/{base.py, cli_socket.py, telegram.py}.
Agent(L8b): worktree=lane/playbooks-content-v2, re-run I27 validation against merged C8.
            If schema changed, patch YAMLs and regenerate tests. Deliver fixed YAMLs.
```

**Sync:** Merge L4 → L5 → L8b → main. Pytest.

**Critical path:** L4 is 3 tasks; L5 is 3; run concurrently so critical path = max(L4, L5).

---

### Wave 5 — Daemon (1 lane, 5 sequential tasks)

**Active:** L6 only.
**Tasks:** F17 → F18 → F19 → F20 → F21.

This wave cannot be parallelized internally — each F-task adds a method to `TaskManager` or a handler that depends on the prior task. Single lane. All five tasks dispatched as a single agent brief.

**Dispatch:**
```
Agent(L6): worktree=lane/daemon, execute F17, F18, F19, F20, F21 sequentially.
           Files owned: src/mopedzoom/{task_manager.py, daemon.py, __main__.py} + tests.
           Final deliverable: `mopedzoomd` entry point starts, accepts a socket task, runs a
           no-op playbook end-to-end in-process.
```

**Sync:** Merge L6 → main. Pytest. Integration smoke: `python -m mopedzoom` starts without error and shuts down on SIGTERM.

**Critical path note:** This is the longest serial wave (~5 tasks). Consider letting L7 and L9-prep start in parallel (see Wave 6).

---

### Wave 6 — Surface + Dashboard + Deploy-prep (3 parallel)

**Active:** L7 (Surface+Dashboard combined), L9-prep.

**Dispatch:**
```
Agent(L7a): worktree=lane/surface-dashboard, execute G22, G23, H24, H25, H26 sequentially.
            G22+G23 produce the FastAPI dashboard; H24 is the CLI; H25/H26 are slash-command wizards.
            Files owned: src/mopedzoom/{dashboard/, cli.py}, commands/*.md.
Agent(L9a): worktree=lane/deploy-prep, execute J28 only (systemd unit + install script).
            Files owned: deploy/mopedzoomd.service, scripts/install.sh.
```

These two lanes have no file overlap — merge is clean.

**Sync:** Merge L7a → L9a → main. Pytest.

---

### Wave 7 — E2E + Polish (1 lane, final push)

**Active:** L9 continues.
**Tasks:** J29, J30.

**Dispatch:**
```
Agent(L9b): worktree=lane/deploy-final, execute J29 (full E2E integration test hitting
            Telegram mock + CLI socket + dashboard + real `claude -p` stub) then J30 (polish,
            README, changelog, final self-review checklist from the implementation plan).
            This agent must run the entire pytest + integration suite and report clean.
```

**Sync:** Merge L9b → main. **Final gate:** full test suite on main + manual smoke on the systemd unit.

---

## 4. Sync-Point Checklist (copy-paste for each wave end)

At every wave end the orchestrator runs this checklist before fanning out the next wave. Don't skip — most multi-lane bugs surface here.

```
[ ] All active-wave agents reported "complete" (not "in progress", not "blocked")
[ ] Each lane's final commit SHA recorded in the progress block
[ ] Dispatched superpowers:code-reviewer on the diff of each lane vs. main; review passed
[ ] Merged lanes into main in declared order (conflicts should be zero; if not, stop)
[ ] `pytest` runs clean on main
[ ] `ruff check` + `mypy` run clean on main (if configured)
[ ] Progress snapshot emitted showing which wave just closed and what's next
[ ] Worktrees for completed lanes pruned: `git worktree remove lane/<name>`
```

If any box fails, **do not proceed**. Dispatch a fix agent against the failing lane's branch (not main) and re-run the checklist.

---

## 5. Agent Brief Template

Use this template verbatim for every dispatched implementation agent. Replace `{{...}}` placeholders.

```
You are implementing {{N}} task(s) from the mopedzoom implementation plan, inside an
isolated git worktree. You have ZERO context from prior conversations — everything you
need is in this prompt.

== Worktree ==
Path: {{worktree_absolute_path}}
Branch: {{branch_name}}
You MUST cd into this path before doing any work. All edits, commits, and test runs
happen here. Do not touch any path outside this worktree.

== Plan context ==
The full implementation plan lives at:
  /home/nitin/workspace/mopedzoom/docs/plans/2026-04-19-mopedzoom-implementation.md
The design spec lives at:
  /home/nitin/workspace/mopedzoom/docs/specs/2026-04-19-mopedzoom-design.md
Read the RELEVANT task sections from the implementation plan before starting.

== Your tasks ==
Execute in order:
  {{task_id_1}} — {{task_title_1}}
  {{task_id_2}} — {{task_title_2}}
  ...

For each task:
  1. Read the task section in full from the implementation plan.
  2. Follow every step in order (write failing test → run → implement → run → commit).
  3. Use the exact commit message specified in the plan.
  4. Do not skip steps. Do not batch commits. TDD discipline is non-negotiable.

== Constraints ==
- Python 3.12, pytest + pytest-asyncio, pydantic v2.
- Never mark a task complete if its tests are failing.
- If a step references a file or function that doesn't exist, read the prior task in the
  plan — you may be out of order.
- Do NOT create files outside the paths listed in the "Files" section of each task.

== Reporting ==
When all your tasks are done, reply with:
  - Final commit SHA on your branch
  - `git log --oneline main..HEAD` output
  - `pytest` summary line (e.g., "42 passed in 3.1s")
  - Any deviations from the plan, with justification
If you get stuck, stop and report the blocker — do not guess.
```

---

## 6. Orchestrator Main-Thread Pseudocode

```python
# Wave 1
dispatch_and_wait(Agent(L0, tasks=[A0, A1]))
sync_merge_and_test(["lane/bootstrap"])

# Wave 2
dispatch_parallel([
    Agent(L1, tasks=[B2, B3, B4, B5]),
    Agent(L2, tasks=[B6, B7]),
    Agent(L8, tasks=[I27]),
])
wait_all()
sync_merge_and_test(["lane/data", "lane/config-scratch", "lane/playbooks-content"])

# Wave 3
dispatch_and_wait(Agent(L3, tasks=[C8, C10, C9]))
sync_merge_and_test(["lane/playbooks"])

# Wave 4
dispatch_parallel([
    Agent(L4, tasks=[D12, D13, D11]),
    Agent(L5, tasks=[E14, E15, E16]),
    Agent(L8b, tasks=["revalidate I27 against merged C8"]),
])
wait_all()
sync_merge_and_test(["lane/stage-exec", "lane/channels", "lane/playbooks-content-v2"])

# Wave 5
dispatch_and_wait(Agent(L6, tasks=[F17, F18, F19, F20, F21]))
sync_merge_and_test(["lane/daemon"])

# Wave 6
dispatch_parallel([
    Agent(L7a, tasks=[G22, G23, H24, H25, H26]),
    Agent(L9a, tasks=[J28]),
])
wait_all()
sync_merge_and_test(["lane/surface-dashboard", "lane/deploy-prep"])

# Wave 7
dispatch_and_wait(Agent(L9b, tasks=[J29, J30]))
sync_merge_and_test(["lane/deploy-final"])

print("mopedzoom v1 ready")
```

---

## 7. Risk & Mitigation

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Lane branches develop merge conflicts | Medium | Lane ownership is file-scoped (see Lane Definitions); two lanes never edit the same file in the same wave. If conflict appears, it means the decomposition is wrong — stop and revisit. |
| Subagent misreads a task and invents a different API | Medium | Agent brief includes the full task block verbatim; reviewer agent checks against the plan. |
| C8 playbook schema drifts from I27's YAMLs written earlier | Medium | Wave 4 includes a mandatory L8-revalidate lane that re-runs YAML validation after C8 lands. |
| F-phase is long and serial — blocks G/H/I | Low | L7 and L9-prep start the moment F21 merges; they don't wait for J tasks. |
| `claude -p` subprocess shape changes mid-build | Low | D11 is fully mocked in tests; real `claude` is only invoked in Wave 7's J29 E2E. If the CLI shape shifts, only D11's subprocess wrapper needs updating. |
| Reviewer agent finds issues late, forcing rework | Medium | Review happens per-lane before merge, not at end. Catches regressions within one wave. |

---

## 8. Estimated Timeline

Assuming each task takes ~3–8 minutes of agent wall time (TDD + commit) and a subagent can be dispatched in seconds:

| Wave | Serial tasks | Parallel tasks | Est. wall time |
|------|--------------|----------------|----------------|
| 1 Bootstrap | 2 | 0 | ~10 min |
| 2 Data fan-out | — | 4+2+1 in 3 lanes | ~20 min (L1 is longest) |
| 3 Playbooks | 3 | 0 | ~20 min |
| 4 Exec+Channels | — | 3+3+fixup in 3 lanes | ~20 min |
| 5 Daemon | 5 | 0 | ~30 min |
| 6 Surface+Deploy-prep | — | 5+1 in 2 lanes | ~30 min (L7a is longest) |
| 7 E2E+polish | 2 | 0 | ~20 min |
| **Total** | | | **~150 min** (~2.5 h) |

Strict serial execution would be ~30 tasks × 5 min ≈ 150 min too, but that assumes no review/merge friction. **Real speedup comes from absorbing review + test overhead into the parallel waves** — reviewer and tests run concurrently with the next lane's coding.

A realistic target: **mopedzoom v1 merged to main, systemd-installable, in a single ~3-hour orchestration session.**

---

## 9. Self-Review of This Execution Plan

- **Lane decomposition checked against implementation plan:** every task A0…J30 is assigned to exactly one lane. ✓
- **No two concurrent lanes write the same file:** confirmed by comparing the "Files" block of each task against the lane ownership column. ✓
- **Dependencies respected:** C9 waits on C8 and B4 (both land in earlier waves before Wave 3 starts); D11 waits on C9/C10/B7 (all in waves 2–3); F17 waits on C9/D11/E14 (all in waves 3–4). ✓
- **Sync-point checklist has teeth:** review agent + pytest + merge-order defined. ✓
- **Agent brief is self-contained:** template requires no prior conversation context, references absolute paths. ✓
- **Handoff:** when the user picks subagent-driven execution, the orchestrator starts Wave 1 immediately using the pseudocode in §6.

---

## 10. What This Plan Does NOT Do

Things the orchestrator still decides on the fly (not baked in here):
- **Reviewer findings triage:** if the reviewer flags nits vs. blockers, the orchestrator decides whether to loop or carry on.
- **Test flakes:** if pytest flakes on a lane, the orchestrator reruns once before calling it a real failure.
- **Scope creep from the 5 known gaps** listed in the implementation plan's self-review — those stay out of v1. The orchestrator must refuse scope expansion mid-execution.

---

**Handoff:** Execution plan complete. Both plans are now committed. The user can choose:

**1. Subagent-Driven (recommended, matches this plan)** — I start Wave 1 immediately using the pseudocode in §6.
**2. Inline Execution** — I execute the implementation plan sequentially in this session (ignoring parallelization).
**3. Review first** — you read both documents and adjust before any execution begins.

Which approach?
