# Authoring a skill mid-build

You are about to write a skill because a real gap showed up. The bar for that skill is different from the bar for a permanent one — it must be useful *right now*, and only worth keeping if it earns that.

## Before you write one

Check all three, in this order:

1. **Does a local skill already cover this under a different name?**
   ```bash
   python scripts/query-registry.py need "<the capability>"
   python scripts/query-registry.py list --tag <capability>
   ```
   A skill named `stripe` might live under `payments`. Search the registry, not your memory of skill names.

2. **Is this a skill at all, or just a task?**
   A skill is worth writing if the procedure will come up *again*. If it is one thing you do once, write it in the session and move on. Do not create a permanent artifact for a one-off.

3. **Can a subagent brief do the job?**
   Sometimes "missing capability" really means "needs a narrow, well-scoped agent with a clear brief." That is cheaper than a skill and does not pollute your skills directory. Prefer it.

If all three are answered, write the skill.

## Where to write it

Session-scoped, not global. During the build, write to a scratch directory:

```
<session scratch>/aswath-authored/<skill-name>/SKILL.md
```

Global (`~/.agents/skills/<name>/`) is **only** for skills the user has promoted. Writing there unprompted violates the promotion contract in `promotion.md`.

## The shape

Follow the convention of every other skill on this machine:

```markdown
---
name: <kebab-case-name>
description: "<What it does, and the phrases a user would say to trigger it. Third person. The description IS the routing signal - a vague description makes the skill unroutable.>"
version: 0.1.0
---

# <Title>

<One paragraph: what this is for and when to use it.>

## When to use this
<Concrete triggers.>

## Procedure
<Numbered steps. Each step says what to DO, not what to consider.>

## Output
<What the skill produces, in what shape.>

## Failure modes
<What goes wrong, and what to do instead.>
```

## The rules that make skills work

- **The description is the index.** Every routing decision in this system reads the `description:` field. Write it for a machine choosing between skills, not for a human browsing a list. Include the words a user would actually type.
- **Name the trigger words.** "Post to LinkedIn", "publish a post" — not "helps with social".
- **Short and specific beats long and general.** One job. If the skill has two jobs, it is two skills.
- **Make the output explicit.** A skill that says "produce a good analysis" produces nothing consistent. Say what artifact comes out.
- **State the failure modes.** The next agent reading this cold is your reader.
- **Do not duplicate an existing skill.** If yours is a variation of one, fold your improvement into the existing one's description instead — that is a smaller change with a bigger routing payoff.

## After writing it

1. Use it immediately. It was written for the task in front of you.
2. Register it so the registry can see it:
   ```bash
   python scripts/build-registry.py
   ```
3. Add it to the promotion proposal at the end of the build:
   ```bash
   python scripts/record-outcome.py --propose
   ```

A skill you wrote and never used is a guess. A skill you wrote, used, and it worked is evidence.
