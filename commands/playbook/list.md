---
description: List built-in and user playbooks
---

Load both registries with `mopedzoomd.playbooks.load_playbooks(builtin_dir, user_dir)` where:

- `builtin_dir` = `<plugin_path>/playbooks/`
- `user_dir` = `~/.mopedzoom/playbooks/`

Print a table: `id | source (builtin/user/override) | summary | #stages | triggers`.

Note that user entries with the same id as a built-in override it.
