---
name: no-placeholders
description: Deliver complete work with the decisions made. Use when producing code, documents or plans the user will use as-is.
---

# No placeholders

- No unfinished-work markers (to-do or fix-me comments), no "insert here", no "<your value>", no example data where real data was available.
- No optionality: when several approaches exist, pick one, state why in one sentence, and deliver it. Mention the discarded option only if the choice is reversible and the cost of being wrong is high.
- A deliverable is finished when the user can use it without editing it. If a value is truly unknown (a secret, a hostname), make it an explicit input with a one-line instruction, not a blank.
- Match the requested format exactly; do not add a summary the user did not ask for.

The one legitimate blank, written as an explicit input:

```bash
# set once, stored by your OS keychain — never committed:
export GITHUB_TOKEN=ghp_yourtoken
```
