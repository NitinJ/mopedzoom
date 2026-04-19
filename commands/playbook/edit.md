---
description: Edit an existing user playbook YAML
---

List user playbooks under `~/.mopedzoom/playbooks/*.yaml` and ask which one to edit. (Built-in playbooks under the plugin's `playbooks/` directory are read-only; to override, copy to `~/.mopedzoom/playbooks/` with the same id.)

Open the YAML with the Read tool, let the user describe the change, apply with Edit/Write, then validate by loading with `mopedzoomd.playbooks.Playbook.from_file(path)`.

Ask whether to reload the daemon after a successful edit.
