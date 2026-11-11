# The promotion contract

You can always author a skill mid-build. You cannot always *keep* it. This file is the boundary.

## The rule

> Nothing is written permanently without the user's explicit approval.

Permanently means: `~/.agents/skills/`, `settings.json` hooks, installed plugins, or anything else that changes how every future session behaves.

## Session-first, promote later

```
GAP FOUND
   ↓
author into scratch dir          ← always allowed
   ↓
use it for the current task       ← always allowed
   ↓
it worked?                        ← evidence, not vibes
   ↓
propose at the end of the build   ← one proposal, batched
   ↓
USER APPROVES  →  write to ~/.agents/skills/<name>/   ← requires a yes
   ↓
rebuild registry                  ← so the next session routes to it
```

## What gets proposed

`scripts/record-outcome.py --propose` prints three lists:

| List | What it means | Action if approved |
|---|---|---|
| **Promote** | A skill you authored this session that was used and accepted | Write to `~/.agents/skills/<name>/` |
| **Skillsets** | A pipeline that proved itself on a real build | Write to `skillsets/<name>.json` |
| **Retire** | A skill that failed twice on the same kind of task | Flag it in the ledger; never delete without asking |

## Third-party installs

Installing a marketplace plugin is a supply-chain decision, not a technical one. An installed plugin can run code and inject instructions into every future session.

Therefore:

- **Searching and staging is free.** `python scripts/stage-install.py --search "<need>"` finds candidates.
- **Installing needs approval.** Stage it, show the user what it is and where it comes from, then install on a yes.
- **Prefer a local skill over a marketplace install when both work.** A local skill you can read is worth more than an install you trust.
- **Never auto-install a plugin that also registers hooks or MCP servers.** Those run outside the skill system and you cannot see them at call time.

## Recording outcomes

Every skill that runs in a build gets an outcome:

```bash
python scripts/record-outcome.py --skill impeccable --outcome accept --note "found the accessibility lens"
python scripts/record-outcome.py --skill some-skill --outcome reject --note "too generic, rewrote the description"
```

The ledger feeds the quality score, which feeds the ranking. A skill you reject twice gets ranked below its alternatives automatically.

**Your accept/reject is the highest-weight signal in the system.** A skill you rejected will not be auto-selected again even if its description matches perfectly.

## When you disagree with the user

If they reject something and you think it is wrong, say so once with evidence, then respect the decision. Record the outcome as rejected. Their preferences are the ground truth this system optimizes against — arguing with it breaks the loop.
