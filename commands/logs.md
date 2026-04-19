---
description: Tail logs for a mopedzoom task
---

Ask for the task id. Run `mopedzoom logs <id>` and print the returned log locations (per-stage transcript paths under the task scratch directory).

Offer to tail a specific stage's transcript with `tail -f <path>` via the Bash tool. Also surface the systemd daemon log location: `journalctl --user -u mopedzoomd -f`.
