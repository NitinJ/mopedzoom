---
description: Edit a mopedzoom task's remaining stages or inputs
---

Ask the user for the task id. Run `mopedzoom status <id>` to show current state, then offer:

1. Skip a pending stage
2. Insert a new stage
3. Change the agent for a stage
4. Edit inputs

Collect the change via an interactive prompt, then send:

```
mopedzoom edit <id> --stage=<N>
```

(with additional flags as needed). Confirm the edit by re-printing the updated stage list.
