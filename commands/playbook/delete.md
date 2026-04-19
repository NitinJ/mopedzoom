---
description: Delete a user playbook YAML
---

List user playbooks under `~/.mopedzoom/playbooks/*.yaml` and ask which one to delete. Built-in playbooks are never deleted by this command.

Confirm with the user (print the file path and summary before proceeding), then `rm` the YAML file. If the deleted id was overriding a built-in, note that the built-in will be active again.

Ask whether to reload the daemon.
