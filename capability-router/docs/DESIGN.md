# aswath-capability — design

**Status:** v0.1.0 built and working
**Owner:** aswath
**Date:** 2026-11-05

---

## Intent

Take a one-line brief ("build me an ecommerce site") and carry it to shipped work: brainstorm the brief, research whether it will actually work, plan UI and architecture, then assemble and invoke whatever machinery the job needs — skills, subagents, hooks, MCP servers, plugins — authoring the missing pieces when a genuine gap appears, and reporting what it cost.

## Why it exists

The daily problem is not that skills are missing. It is that there are now hundreds of skills, new ones arrive every week, and no one can hold the whole map in their head. The map exists — it is just not navigable. This skill makes it navigable, and makes the map improve as it is used.

## Goals

- Resolve a goal into a concrete, executable plan using skills that already exist.
- Detect real gaps instead of improvising badly over them.
- Author missing capability in-session, then propose it for permanent promotion.
- Keep cost visible: the cheapest skill that fully covers the need wins.
- Report honestly what was verified and what was not.

## Non-goals

- Not a universal god-skill. It composes; it does not contain everything.
- Not a self-training model. There is no fine-tuning here — see *Approaches*.
- Not autonomous without gates. Three gates, no more.

## Anti-goals (what would make this a failure)

- The orchestrator becomes a rigid waterfall that ignores what the actual job needs.
- Routing silently degrades to "invoke the big famous skill every time."
- Auto-authored skills accumulate in the global directory without evidence.
- The system installs third-party plugins without consent.

---

## Approaches considered

### Approach A — A registry of skills plus a lookup tool

**What:** a generated index of every skill with tags and token cost, plus a script that queries it.
**Trade-off:** fast and cheap, but it only routes. It cannot tell you *what order* to do things in, and it has no opinion on quality.
**Verdict:** adopted — but as the substrate, not the whole thing.

### Approach B — Hand-written mega-skill covering every domain

**What:** one large skill containing all the process for UI, backend, research, deployment.
**Trade-off:** simple to invoke, but it loads 10,000+ tokens on every use regardless of need, and it cannot be improved in one place without breaking the others.
**Verdict:** rejected. Directly contradicts the cost discipline the user asked for.

### Approach C — Full agent that self-improves via training

**What:** accumulate build trajectories and fine-tune a model on them.
**Trade-off:** requires a data pipeline, training infrastructure, and a serving path — against a session that currently runs 243 local skills at 14.3k tokens of a 524k window.
**Verdict:** rejected for now. The compounding lever is a measured score ledger and a promotion rule, not weights. Revisit if the trajectory corpus ever gets large enough to matter.

### Adopted shape

Three layers: **atomic skills** (commodities, never edited in place) → **skillsets** (curated pipelines with gates) → **aswath-capability** (the composer, which resolves, detects gaps, authors what's missing, and orchestrates).

---

## Key decisions

| Decision | Choice | Why |
|---|---|---|
| Resolution model | Registry + capability taxonomy; embeddings later | A skill mentioning "product page" is not a provider of ecommerce. Strong/weak keyword evidence fixes this without an embedding backend. |
| Learning substrate | Score ledger + accept/reject | Your judgment is the ground truth; embeddings can rank relevance but cannot rank quality. |
| Autonomy boundary | Session-first authoring, promote on approval | Keeps a bad learn or a hostile upstream skill out of the directory every future session loads. |
| Market ingestion | Search and stage freely; install only on approval | A plugin can ship hooks and MCP servers that run outside the skill system. |
| Execution | Fan out to subagents; write Workflows for genuine loops | A workflow that fans out 30 agents is a waste, not an orchestration. |
| Cost model | Token cost is a ranking signal | Cheapest skill that fully covers the need. |

### The strong/weak evidence rule

The registry originally tagged any skill whose description merely *mentioned* a capability. `design-taste-frontend` was tagged "ecommerce" because it mentions "product page" — a design skill is not a cart implementation. The fix: **strong** keywords may match anywhere in a description; **weak** keywords only count when they appear in the skill's **name**. A skill named `shopify-cart` provides ecommerce; a landing-page skill that mentions one does not.

Local untagged skills went from 30 to 75 as a result — 45 false tags removed.

---

## Components

```
aswath-capability/
├── SKILL.md                  the orchestrator: 8 phases, 3 gates, the rules
├── registry/
│   ├── capabilities.json     the taxonomy (files / strong / weak evidence channels)
│   ├── skills.json           generated index: 243 local + 2910 marketplace
│   ├── ledger.json           quality scores from outcomes
│   └── staged.json           marketplace candidates awaiting approval
├── skillsets/
│   └── ui-design.json        the flagship: 8 phases, quality bar, gates
├── registry/
│   ├── backends.json         external agent runtimes + role assignment
│   └── staged.json           marketplace candidates awaiting approval
├── scripts/
│   ├── build-registry.py     disk + marketplace catalogs -> skills.json
│   ├── query-registry.py     resolution engine: need/cap/gap/install/list/stats
│   ├── record-outcome.py     ledger writes + promotion proposals
│   ├── stage-install.py      stage a marketplace plugin for approval
│   ├── delegate.py           the delegation seam to external agent runtimes
│   └── install-agents.py     install subagent briefs as real agent types
├── agents/
│   ├── aswath-researcher.md  feasibility verdict with evidence
│   ├── aswath-planner.md     executable plan with costs and gaps
│   └── aswath-critic.md      one lens, ranked actionable findings
├── workflows/
│   └── roast-and-fix.py      3 critics -> adversarial verify -> fix -> scope guard -> re-verify
├── reference/
│   ├── authoring.md          how to write a skill worth keeping
│   ├── promotion.md          the approval contract
│   └── quality-bar.md        what "done" means per domain
└── docs/DESIGN.md            this file
```

## The eight phases

Intake → Brainstorm *(gate 1)* → Feasibility → Resolve → Plan *(gate 2)* → Build → Roast & Fix → Verify & Report → Promote *(gate 3)*

## Delegation, not native orchestration

Work that needs a loop is handed to an external agent runtime rather than orchestrated by the host. Two backends, split by what each is good at:

| Backend | Role | Why |
|---|---|---|
| **`agy`** | structured **and** constrained edits | `--json-schema` is enforced at the tool layer, so the output shape is guaranteed rather than requested. Measured: it also applies a constrained fix brief *exactly* — changing only the selectors the finding named. |
| **`cmdc`** | exploratory work | Ships a strong editing skill set and `cmd taste learn` gives it a taste memory — a second learning substrate next to the aswath ledger. But it does **not** reliably follow a constrained brief: measured, it ran its own audit and applied different fixes than the confirmed findings. |

`registry/backends.json` holds the CLI contract per backend, including the **prompt style**: `agy --print` must have its prompt *attached* (`--print="..."`), because a detached `--print` silently swallows the next argument as the prompt. `cmdc -p` takes it as the following argument.

Three behaviours that had to be handled explicitly:

- **Headless permission denial.** `agy` auto-denies tool calls in headless mode unless `--dangerously-skip-permissions` is passed. A critic that cannot read files returns an *empty response*, not an error. The delegate treats empty-plus-denied as a failure rather than a result.
- **Competing output formats.** `cmdc` ships its own skills and will follow *their* output format (hallmark's audit format, for instance) instead of the JSON instruction. Schema-enforced output is preferred over parsed prose for this reason.
- **Backends that write scratch files.** `cmdc` writes `.hallmark/` into the working directory. The roast loop cleans known scratch dirs on exit.
- **Both backends refuse to act in headless mode without an explicit bypass.** `agy` silently returns an empty response; `cmdc` says so in prose and does nothing. `agy` needs `--dangerously-skip-permissions` (read-only roles still use `--mode plan`); `cmdc` needs `--yolo`. `--permission-mode accept-edits` alone is **not** enough for `cmdc` — verified by a probe that was refused with an explicit message.

## What was verified

- Registry build: 244 local skills, 30 plugin skills, 2831 marketplace skills, 3605 installable plugins (111 duplicates suppressed across the three sources).
- Skillset integrity: all 12 skills referenced by `skillsets/ui-design.json` resolve in the registry (checked by name *and* kebab slug).
- Capability tagging, three evidence channels (`files` / `strong` / `weak`):
  - `design-critique` resolves to exactly impeccable, design-is, hallmark, design-taste-frontend, principle-redesign-from-first-principles.
  - `design-direction` resolves to exactly impeccable, frontend-design, canvas-design, hallmark, design-taste-frontend.
- Resolution: `"roast my landing page and fix it"` returns `design-taste-frontend` and `impeccable` first — the correct answer.
- Gap detection: `"build an ecommerce site with cart and checkout"` correctly reports `ecommerce` and `payments` as gaps, and surfaces `wake-storefront`, `Paymob Integration`, `storefront-theme-kit` from the marketplace.
- Staging: stages `wake-storefront` without installing, and warns that its contents are outside the local catalog.
- Ledger: accept/reject writes produce a score that changes ranking order.
- Delegation: both backends driven headless end to end through `delegate.py`; `agy` returns schema-enforced `structured_output`.
- Model tiers: `fast` (gemini-3.8-flash-low) completes a call in ~12s against ~36s on the default model — the split between strong and fast models is what makes a multi-call loop affordable.
- Scope guard, unit-tested: a targeted fix that adds a class name and inline style is not flagged; a rewrite that invents a product name is flagged; a term the findings asked for is not flagged as invented.
- **End-to-end roast, delegated, verified.** 3 parallel critics on `agy` → 20 raw findings → capped to the top 3 before verification → adversarial verification confirmed 2 and refuted 1 → the fixer applied exactly the confirmed contrast fix (`#999` → `#595959` on `.stat .label`, `.card p`, `.footer`, `.footer a`, and nothing else) → per-finding re-verification reported the one genuinely unfixable finding as unfixed, with line-level evidence.
- Scope guard: detected a fixer that rewrote an entire page instead of applying findings.

## Failures found during verification, and fixed

1. **Substring matching on descriptions tagged the wrong skills.** `design-taste-frontend` was tagged "ecommerce" because its description mentions "product page". A skill that mentions a word is not a provider of that capability. Fixed with a three-channel evidence model: `files` (the reference files a skill ships — proof of what it does), `strong` (a keyword anywhere in the description), and `weak` (a keyword in the skill's *name* only).
2. **The orchestrator tagged itself as a capability provider.** Its description contains the trigger phrase "build me an ecommerce site", so it competed with the skills it dispatches. Fixed with an explicit exclusion list.
3. **Filename evidence was too loose.** Matching file *stems* as substrings tagged 82 skills as design-critique (every skill has a "review" file) and left only 1 untagged. Fixed by exact stem matching plus a generic-stem blacklist.
4. **The fixer rewrote the page instead of fixing it.** Given a list of findings, the fixer replaced the entire page — inventing a product ("Shipwright — staged rollouts"), a nav, and a skip link. Fixed with hard anti-redesign constraints in the fix prompt plus an automated scope guard (changed-line budget + newly-invented-term detection) that reports a violation even when the reviewer says the result is clean.
5. **`agy --print` swallowed the next argument.** A detached `--print` made agy treat `--output-format` as the prompt, so every structured call silently produced nothing. Fixed with a per-backend `prompt_style`.
6. **Empty responses masqueraded as results.** An agent denied its tools returns `""`, which the extractor used to fall back to the raw CLI envelope — making a failure look like a parseable object. Fixed.
7. **Installed plugin skills were invisible.** The registry scanned only the local skills directory and the marketplace catalog, so a skillset referencing `agent-cost-report` (shipped by the claude-mem plugin) resolved to nothing. Fixed by indexing `~/.claude/plugins/**/skills/*/SKILL.md` as a third source — 62 plugin skills.
8. **Names and slugs disagreed.** Plugin skills declare `name: Agent Cost Report` but are referenced everywhere as `agent-cost-report`. Fixed by carrying a `slug` on every registry entry and resolving lookups by either form.
9. **The editing backend could not edit.** `cmdc` with `--permission-mode accept-edits` still refuses file writes in print mode, and reports it as prose rather than an error — so the fix phase would have silently applied nothing. Fixed with `--yolo`, verified by a probe that made exactly the one-line change requested.
10. **The same skill was listed three times.** The registry counted `frontend-design` once as a local skill, once as an installed plugin skill, and once in the marketplace catalog — so "cheapest wins" ranking had three entries for one skill. Fixed by deduplicating in precedence order: local > plugin > marketplace. 111 duplicates suppressed.

## What was not verified

- The eight-phase pipeline has not been driven through a complete real build. The registry, resolution, gap detection, delegation, and roast layers are verified individually.
- The marketplace catalog is a cached snapshot from 2026-11-04. It is not refreshed automatically; rerun `build-registry.py` after a marketplace update.
- The `files` evidence channel does not apply to marketplace skills, which have no local file tree. They are tagged by name and description only.
- Delegated agents write scratch directories (`.hallmark/`) into the working directory. The roast loop cleans them up; other delegation paths should too.
- Delegated runs are slower and costlier than in-process subagents: `agy` is roughly 35-60s per call and carries its own system prompt. Parallelism is bounded by that, not by CPU.
