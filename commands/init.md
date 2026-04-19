---
description: Initialize mopedzoom — Telegram, repos, permissions, systemd
---

You are running the mopedzoom init wizard. Guide the user through the following steps interactively. All subprocess calls go through the Bash tool; show the user what is happening at each step.

This command is **idempotent**: on re-run, load the existing `~/.mopedzoom/config.yaml` (if any) with `mopedzoomd.config:load_config` and allow the user to edit each section instead of starting fresh.

1. **Claude API key** — verify `ANTHROPIC_API_KEY` is set in the environment. If not, instruct the user to `export ANTHROPIC_API_KEY=sk-ant-...` (persistently, via their shell profile) before continuing.

2. **Telegram setup:**
   - Ask for the bot token (obtained from `@BotFather` in Telegram).
   - Ask for the group chat id. If the user does not know it, walk them through:
     1. Add the bot to the target group.
     2. Send a message in the group.
     3. Run `curl -s https://api.telegram.org/bot<TOKEN>/getUpdates` and extract the `chat.id`.
   - Confirm that the group has **forum topics** enabled (user verifies in Telegram → Group settings).
   - Instruct the user to promote the bot to admin with the `can_manage_topics` permission.
   - **Verify** by calling `getChatMember` for the bot in the group, then `createForumTopic` with name `mopedzoom-init-test`; `closeForumTopic` + `deleteForumTopic` to clean up on success.

3. **Repos** — auto-detect git repos under `~/workspace/` with `find ~/workspace -maxdepth 3 -name .git -type d`. For each, ask whether it should be allowlisted for mopedzoom. For accepted repos collect:
   - Default branch: `git -C <path> symbolic-ref --short HEAD`.
   - Optional PR reviewers (comma-separated GitHub handles).

4. **Permissions default** — ask which default permission mode to use:
   - `bypass` (recommended; `claude -p` runs with `--dangerously-skip-permissions`).
   - `ask` (prompt per tool call, routed through the mopedzoom permission MCP).
   - `allowlist` (Claude Code allowlist with no prompts).

5. **Deliverables** — ask where research reports should be committed. Default: one of the allowlisted repos at `docs/research/`.

6. **Concurrency / timeouts / grace period / dashboard** — offer defaults (concurrency=3, stage timeout=1h, worktree grace=7d, dashboard port=7777 bound to 127.0.0.1) and accept overrides.

7. **Verify `gh auth status`.** If not authenticated, instruct the user to run `gh auth login` and retry.

8. **Write config** to `~/.mopedzoom/config.yaml` using `mopedzoomd.config:save_config`.

9. **Install systemd user unit:**
   - Copy `systemd/mopedzoomd.service` from the plugin to `~/.config/systemd/user/mopedzoomd.service`, substituting `{{PLUGIN_PATH}}` with `$HOME/workspace/mopedzoom`.
   - Run `systemctl --user daemon-reload`.
   - Run `systemctl --user enable --now mopedzoomd`.

10. **Verify daemon running** via `systemctl --user status mopedzoomd` and `mopedzoom status`. Report success and print the dashboard URL (`http://127.0.0.1:<port>/`).
