---
name: skill-router
description: Pick the right skill for an intent and print a pasteable command. Reads MANIFEST.json (or every SKILL.md frontmatter when there is no manifest) and scores the intent against use_when phrases, curated keywords and not_for exclusions. Use when an agent has several skills installed and must choose one entry point before doing anything, or when you want to know which script handles "make me a logo" versus "read this page".
version: 1.2.0
license: MIT
---

# skill-router

One command that answers "which of these seven things do I run?".

```bash
route.py "make me a logo for a coffee shop"
# anon-image  (1.88)
#   python3 anon-image/scripts/imgen.py gen "make me a logo for a coffee shop" -o out.jpg

route.py "which skill handles PDFs" --top 3
route.py "I need a voiceover for my video" --explain
route.py --list
route.py "read this page" --json
```

## What it actually does

1. **Find the pack.** `--skills-dir`, else `$AGENT_SKILLS_DIR`, else walk up from the
   script until it sees `MANIFEST.json` (or a directory of `SKILL.md` files).
2. **Load the catalogue.** `MANIFEST.json` if present — it carries `use_when`,
   `not_for`, `keywords`, `entry` and `commands`. No manifest? It parses the YAML
   frontmatter of every `SKILL.md` and guesses the entry point from `scripts/`.
   That fallback is what makes this work for *other* people's skill packs too.
3. **Score the intent.**

   ```
   score = 3.0 × best match against any use_when phrase
         + 3.0 × curated keyword hit rate        (two hits = full credit)
         + 1.5 × keyword overlap with description
         + 1.5 × keyword overlap with the skill name
         − 4.0 × match against not_for           (a veto, not a nudge)
   ```

   `not_for` is the field that stops an agent trying to generate music with a TTS
   endpoint, so it is weighted heavier than any positive signal.
4. **Refuse to guess.** Below `1.00` it prints "no confident match" and the closest
   three, exit code `1`. A wrong confident answer costs an agent more than an honest
   shrug — it will spend four tool calls doing the wrong thing.
5. **Print a command, not advice.** The output is pasteable and already contains the
   intent where the skill takes free text.

## Special case: meta questions

"which skill / what tool / list skills / what should I use" is a question about the
pack, not a task. Bag-of-words scoring will never rank the router first for those, so
there is an explicit detector that does. It is deliberately narrow — "route this: I
need a podcast intro" still routes to `anon-audio`, because `route` alone is not a
meta signal.

## Composition hint

When the runner-up is within ~1.2 points **and** the winner declares it in
`composes_with`, the router says so:

```
anon-audio  (2.62)
  python3 anon-audio/scripts/tts.py say "I need a voiceover for my video" -o out.mp3
```

Becomes, when `ffmpeg-pipeline` wins by a hair:

```
  note: close call with anon-audio and it composes — ffmpeg-pipeline can call it for you
```

That one line is usually the correct answer: the task genuinely needs two skills.

## Output contract

| Mode | Shape |
|---|---|
| default | `skill  (score)` + indented command (+ alternatives when `--top > 1`) |
| `--json` | `{intent, root, match:{skill,entry,command,score}, alternatives[], all[]}` |
| `--explain` | the per-field score breakdown for the winner |
| `--list` | the whole routing table: name, entry, first `use_when` phrase |

Exit codes: `0` matched · `1` nothing above threshold · `2` no skills found.

## Failure modes

| Symptom | Meaning | Action |
|---|---|---|
| `no confident match` | intent too vague, or genuinely not covered | rephrase with the object you want ("logo", "voiceover", "PDF"); the three closest are printed |
| everything scores `0.00` | no skill owns those words | check `--list`; the pack has no such capability and it is telling you so |
| wrong skill wins | that skill's `keywords` are too broad | edit `keywords` in `MANIFEST.json` — the router is data-driven on purpose, fixing routing should not mean editing Python |
| `no skills found` | pack layout not recognised | `--skills-dir /path/to/pack` or export `AGENT_SKILLS_DIR` |
| `only one skill found under …` | you installed the router on its own — there is nothing to route between | point it at a pack: `--skills-dir ~/agent-skills` or `export AGENT_SKILLS_DIR=~/agent-skills` |

## Notes for the agent

- Run the router **once**, before a multi-step task, not before every call. It costs
  nothing but it is a decision, not a reflex.
- `--json` if you are parsing; the human output is for humans and its exact spacing
  is not a contract.
- If the router says no confident match and you still think a skill applies, run it
  anyway with `--provider`-style explicit arguments — the router is a hint, and the
  scripts are the source of truth.
