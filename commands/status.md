---
description: Show status of a mopedzoom task (or the daemon itself)
---

If the user provides a task id, run `mopedzoom status <id>` and print:

- task id, playbook, status, channel, created_at
- stages table (index, name, status, agent, deliverable)
- pending interactions (approvals/questions) if any
- worktree path (if allocated)

If no id is given, run `mopedzoom status` (no arg) to show the daemon health summary: uptime, active tasks, queue depth, concurrency utilization, last error (if any).
