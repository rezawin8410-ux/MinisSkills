---
name: roast
description: Roast a startup or business idea. It is funny and savage, but every burn is backed by a real, sourced fact, and it ends with the kill-my-idea verdict card. Use when the user says "roast my idea", "roast my startup", "roast <company>", or wants a shareable, funny verdict. Also works on famous companies (for example Juicero or Quibi).
---

# Roast My Idea

A roast is the kill-my-idea verdict told as stand-up. It is shareable on purpose.

## Steps

1. **Research and score** exactly as in
   `${CLAUDE_PLUGIN_ROOT}/skills/kill/SKILL.md`, sections 0–5: the safety rules, the brief,
   the kill-shot checklist, the source rules (minimum 10 sources from 6 domains for a roast),
   the metrics, the rubric, the formula and the fixed rules. Famous companies need no
   questions; use public, well-documented facts.
2. **Write the data files** so `compare` can rank the idea: `idea.json` and `verdict.json`
   (`mode: "roast"`) in `ideas/<YYYY-MM>-<slug>/`, following the kill skill's schemas.
   The HTML report is optional for a roast; write it (kill skill §6) if the user asks.
   For a famous company, the roast may use hindsight (that's the joke), but if the user asks
   "would it have worked?", score it as of its launch date (`as_of`, kill skill §3) and keep the
   hindsight in the roast lines only.
3. **Write the roast** to `roast.md` in the same folder, in this order:
   - the **verdict card** (kill skill §6), then the badge;
   - **5 burns**: one line each, one fact each, one source link each. Funny comes from the
     fact itself (numbers, quotes, irony), not from insults. Order them from the biggest
     kill shot down;
   - **"The one thing that's actually good"**: one honest line (the steelman, kill skill §5);
   - **The fix**: the cheapest test or best rescue path that could save it, in at most 2 lines,
     with its projected score;
   - one closing tagline, at most 12 words.
4. **Self-check** with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check-idea.py" ideas/<folder>`
   when Bash is available (it accepts a folder without report data).
5. **Answer in chat** with the full roast. It must fit in one screenshot: no extra
   sections, no preamble.

## Rules

- Roast the idea, never the person: no looks, identity or private life.
- Facts only. Every burn has a source, and opinions are labelled as opinions.
  This keeps it funny and fair, and stops it from becoming defamatory.
- Real companies and public figures: only public, documented facts.
- Quotes must be verbatim from the linked source. If you can't confirm the exact words,
  paraphrase without quotation marks.
- Same language as the user.
- Never post anything on the user's behalf.
