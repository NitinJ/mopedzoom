---
description: Open the mopedzoom local dashboard in the default browser
---

Read the dashboard port from `~/.mopedzoom/config.yaml` (default `7777`). The dashboard binds `127.0.0.1` only.

Print the URL `http://127.0.0.1:<port>/` and attempt to open it with `xdg-open` (Linux) or `open` (macOS). If no GUI is available, just print the URL and suggest `curl http://127.0.0.1:<port>/health` to sanity-check.
