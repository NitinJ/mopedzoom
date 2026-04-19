---
description: Browse mopedzoom tasks with an interactive drilldown
---

Run `mopedzoom tasks`. Parse the JSON response and present a numbered list showing id, playbook, status, and age.

Offer actions per task: `[status] [pause] [resume] [cancel] [logs] [open-deliverable]`, each mapping to the corresponding CLI subcommand (`mopedzoom status <id>`, `mopedzoom cancel <id>`, etc.). Loop on user input until the user exits.

For `[open-deliverable]`, read the deliverable path from `mopedzoom status <id>` and print the path (or open it in the user's editor if they prefer).
