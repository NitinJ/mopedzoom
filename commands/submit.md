---
description: Submit a task to mopedzoom (optionally customize stages first)
---

Ask the user for the task description if not provided as an argument.

If the user passes `--edit-stages`, first resolve the playbook with `mopedzoom show-playbook <auto-routed>` to show the stage list, let the user toggle stages interactively (numbered menu), then submit with:

```
mopedzoom submit --stages=<csv> "<text>"
```

Otherwise submit directly:

```
mopedzoom submit "<text>"
```

Report the returned task id to the user, plus the resolved playbook and the Telegram topic URL (if available in the response).
