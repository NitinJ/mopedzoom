---
description: Cancel a running or queued mopedzoom task
---

Ask the user for the task id if not provided. Run `mopedzoom cancel <id>` and print the response.

If the task is already in a terminal state (`delivered`, `failed`, `cancelled`), report that back to the user without re-sending. Otherwise confirm cancellation and mention that the worktree will enter grace (configured in `config.yaml`) before being swept.
