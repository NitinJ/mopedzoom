---
description: View or edit mopedzoom config (~/.mopedzoom/config.yaml)
---

Load the existing config at `~/.mopedzoom/config.yaml` with `mopedzoomd.config:load_config`. Print a summary grouped by section (Telegram, repos, permissions, concurrency, dashboard).

Ask the user which section they want to edit. After each edit, write the file back with `mopedzoomd.config:save_config` (which validates via pydantic before writing) and ask whether to restart the daemon with `systemctl --user restart mopedzoomd`.

If the file does not exist, suggest running `/mopedzoom:init` first.
