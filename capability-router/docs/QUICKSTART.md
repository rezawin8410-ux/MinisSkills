# aswath-capability — quickstart

## Try it in one line

```
/aswath-capability build me an ecommerce site
```

Or just say it in plain conversation — the skill is model-invocable and triggers on
"build me a ...", "I want to build ...", "start a project".

## The commands it runs

All scripts live in `~/.agents/skills/aswath-capability/scripts/`.

```bash
cd ~/.agents/skills/aswath-capability

# Rebuild the index of every local + marketplace skill
python scripts/build-registry.py

# What covers this need?
python scripts/query-registry.py need "roast my landing page and fix it"

# What covers this CAPABILITY?
python scripts/query-registry.py cap design-critique --limit 8

# What does NOTHING cover? (this is the important one)
python scripts/query-registry.py gap "build an ecommerce site with cart and checkout"

# Find installable marketplace plugins for a gap
python scripts/query-registry.py install "stripe checkout"

# Stage a plugin for approval (installs nothing)
python scripts/stage-install.py --stage "wake-storefront"

# Record how a skill performed
python scripts/record-outcome.py --skill impeccable --outcome accept --note "a11y lens worked"

# See what should become permanent
python scripts/record-outcome.py --propose

# Coverage stats
python scripts/query-registry.py stats
```

## The UI skillset on its own

```
/aswath-capability run the ui-design skillset on my landing page
```

Phases: **frame → references → compose → build → inspect → roast → fix → efficiency**

Phase 2 is the one that asks you: bring your own references, or pick from the
template library. It always offers 3-4 side-by-side options with previews.

## The roast loop on its own

```
/aswath-capability roast this page and fix what they find
```

Or directly:

```bash
cd ~/.agents/skills/aswath-capability
python workflows/roast-and-fix.py --target ~/my-page/index.html --max-rounds 2
```

Three critics in parallel, each with a different lens, then every finding is
adversarially verified (plausible-but-wrong findings get killed), then fixed,
then re-inspected. A **scope guard** watches the fixer: an agent asked to fix
five findings but rewriting the whole page is caught and reported.

## Delegation

Loops are handed to external agent runtimes, not orchestrated natively.

```bash
python scripts/delegate.py --backends              # what's configured
python scripts/delegate.py --backend agy --prompt "..."   # one delegated call
```

| Backend | Used for | Why |
|---|---|---|
| `agy` | structured roles | `--json-schema` is enforced at the tool layer |
| `cmdc` | editing and audit | strong editing skills, plus its own `taste learn` memory |

Every call returns the same envelope: `ok`, `text`, `parsed`, `usage`,
`duration_s`, `session_id`. `usage` is where delegated cost comes from.

Add a backend by editing `registry/backends.json` — it needs the CLI command,
the prompt style (`attached` vs `next`), and how to extract the final answer.

## First-time setup

```bash
cd ~/.agents/skills/aswath-capability
python scripts/install-agents.py    # installs aswath-researcher / -planner / -critic as agent types
```

## Keeping the registry sharp

The registry is only as good as the taxonomy. When routing sends you to the
wrong skill, fix the taxonomy rather than the query:

```bash
$EDITOR ~/.agents/skills/aswath-capability/registry/capabilities.json
python scripts/build-registry.py
```

Tags are keyword-driven on purpose. `strong` keywords may appear anywhere in a
description; `weak` keywords only count when they appear in the skill **name**.
That rule exists because a landing-page skill that mentions "product page" is
not a provider of ecommerce capability.

## What gets written where

| Path | When |
|---|---|
| `~/.agents/skills/<name>/` | **Only after you approve a promotion.** |
| `~/.claude/agents/` | The three subagent briefs, installed by `install-agents.py`. |
| `~/.agents/skills/aswath-capability/registry/ledger.json` | Every outcome you record. Safe, automatic. |
| `~/.agents/skills/aswath-capability/registry/staged.json` | Marketplace candidates awaiting your approval. Safe, automatic. |

## Refreshing the marketplace

The marketplace catalog is a cached snapshot. To pick up new plugins:

```bash
# refresh Claude Code's plugin cache first, then:
python scripts/build-registry.py
```

`build-registry.py` reads whatever is in `~/.claude/plugins/plugin-catalog-cache.json`
and `plugin-directory-cache-v2.json`.
