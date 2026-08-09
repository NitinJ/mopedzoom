---
description: View or edit mopedzoom config (~/.mopedzoom/config.yaml)
---

Load the existing config at `~/.mopedzoom/config.yaml` with `mopedzoomd.config:load_config`. Print a summary grouped by section (Channel, repos, permissions, concurrency, dashboard).

Note: the pydantic key for the messaging channel section is `channel` (not "Telegram"). The `ChannelConfig` model holds `bot_token`, `chat_id`, and `mode`.

Ask the user which section they want to edit. After each edit, write the file back with `mopedzoomd.config:save_config` (which validates via pydantic before writing) and ask whether to restart the daemon with `systemctl --user restart mopedzoomd`.

If the file does not exist, suggest running `/mopedzoom:init` first.
