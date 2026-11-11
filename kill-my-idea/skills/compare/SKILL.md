---
name: compare
description: Rank all the ideas evaluated with kill-my-idea in this project, from their verdict.json files, recommend which ONE to focus on this week, and write the idea dashboard (ideas/board.html) and the brain note (ideas/BRAIN.md). With --refresh, re-research stale verdicts before ranking. Use when the user says "compare my ideas", "which idea should I do", "rank my ideas", "what should I focus on", "refresh", "update the ranking", or has evaluated 2 or more ideas.
---

# Compare Ideas

## Options

- **`--refresh`** (also when the user says "refresh" or "update the ranking"): re-research
  stale verdicts before ranking (step 2). Without it, compare only reads the verdicts.
  This option was requested by a user, so a ranking built on old research can be brought
  up to date in one command.
- **`--refresh all`** (or "refresh all of them"): refresh every verdict, stale or not.

## Steps

1. **Find the verdicts.** Read every `ideas/*/verdict.json` (and `idea.json`) in the
   current project.
   - Fewer than 2 found → say so, and suggest `/kill-my-idea:kill`.
   - A verdict whose `evaluated` date is more than 90 days before today is **stale**. Without
     `--refresh`, mark it and suggest `/kill-my-idea:compare --refresh`.
   - A verdict whose `confidence.level` is `low` is marked **low confidence**: its rank is
     provisional.
2. **Refresh (only with `--refresh`).** For each stale verdict (or every verdict with
   `--refresh all`), one idea at a time or one subagent per idea:
   1. Copy the current `mode`, `evaluated`, `score`, `verdict` and source count into a new
      entry at the end of `verdict.history`.
   2. Rerun the kill skill's research steps 3–5 for that idea
      (`${CLAUDE_PLUGIN_ROOT}/skills/kill/SKILL.md`), with its safety rules (§0) and source
      minimums. Keep `idea.json` as it is; the user's answers still hold.
   3. Update `verdict.json`: `evaluated` = today, the metrics, sub-scores, score, rules,
      confidence, kill shots, rescue paths and sources. Keep `mode`.
   4. Rewrite `report-data.json` (kill skill §6) with the new verdict and narrative, including
      `premortem`, `why_now` and `icp`, then rebuild `report.html` with
      `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build-report.py" ideas/<folder>`.
   5. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check-idea.py" ideas/<folder>` when Bash is
      available, and fix every error.
   Tell the user which ideas were refreshed and how each score moved ("42 → 47").
3. **Build the ranking table.** Sort by score, then by `time_to_first_revenue_weeks` (the
   faster one wins), then by confidence (`high` before `medium` before `low`).

   | # | Idea | Verdict | Score | Confidence | Best rescue (score, difficulty) | LTV : CAC | Break-even | Time to first revenue | Primary kill shot |
   |---|------|---------|-------|------------|----------------------------------|-----------|------------|-----------------------|-------------------|

   Read the values from `verdict.json`: `rescue[0]`, `metrics.ltv_cac`,
   `metrics.break_even_customers_per_month`, `metrics.time_to_first_revenue_weeks`,
   `primary_kill_shot`. A missing metric is `unknown`, never a guess. Mark a score that
   `rules_applied` capped ("69, capped: no channel"). Show the last `history` entry when a
   verdict changed ("42 → 47").
   Add a column per country when ideas target different markets.
4. **Decide like a co-founder:**
   - **Focus** on ONE idea this week, with the reason, its first action and a deadline.
     Prefer a lower score with `high` confidence over a higher score with `low` confidence,
     and say so.
   - **Pause:** what to park, and why. A NO-GO with a `doable` rescue path above 60 is a
     pause, not a kill.
   - **Kill:** NO-GOs that are still in `ideas/` and should be archived.
   - **Opportunity cost:** what the focus idea beats the others on, in one line.
   - Read `ideas/BRAIN.md` when it exists: the user's Me profile (budget, time, goal) and the
     kill-shot patterns weigh in the choice.
   - Save the focus as `ideas/focus.json`, a strict JSON object:
     `{"slug": "<verdict slug>", "why": "<one line>", "first_action": "<one action>", "deadline": "YYYY-MM-DD", "decided": "<today>"}`.
     Each value is at most 300 characters.
5. **Write `ideas/RANKING.md`** with the table, the decision and today's date.
6. **Write the dashboard and the brain.** With Bash, run
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build-brain.py" ideas`, then
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check-idea.py" --board ideas`. The script computes
   everything from the verdicts and `focus.json`: `ideas/board-data.js`, a fresh copy of
   `ideas/board.html`, and the generated block of `ideas/BRAIN.md` (focus, all ideas, what keeps
   killing your ideas, score changes, lessons). It keeps the Me section and the user's notes.
   Without Bash, do it by hand:
   - Write `ideas/board-data.js` in the format below, ideas in ranking order.
   - Copy the board page as-is. Never rewrite it.
     With Bash: `cp "${CLAUDE_PLUGIN_ROOT}/skills/compare/board.html" ideas/board.html`.
     Otherwise Read it and Write it unchanged.
   - Check it with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check-idea.py" --board ideas` when
     Bash is available, and fix every error.
7. **Answer in chat** with the table and the decision. Keep it short. Name the kill shot that
   hit the most ideas, if it hit 2 or more. End with "Open `ideas/board.html` in your browser.
   Your notes: `ideas/BRAIN.md`."

Without `--refresh`, do not re-research the ideas. Only compare what the verdicts contain.

## Board data

`build-brain.py` writes this file; the format is here for the no-Bash case.
`ideas/board-data.js` is one statement, a strict JSON literal (double quotes, no functions,
no comments, no template literals), following
`${CLAUDE_PLUGIN_ROOT}/skills/compare/board-data.schema.json`:

```js
window.KMI_BOARD = { "generated": "YYYY-MM-DD", "ideas": [ { "slug", "title", "verdict", "score", "confidence", "evaluated", "time_to_first_revenue_weeks" (number|null), "ltv_cac" (number|null), "primary_kill_shot" (string|null), "best_rescue": {"change","score_after","difficulty"}|null, "countries": [..], "report": "<folder>/report.html", "stale": bool } ] };
```

| Field | From |
|-------|------|
| `generated` | today |
| `slug`, `verdict`, `score`, `evaluated`, `primary_kill_shot` | `verdict.json`, copied exactly |
| `title`, `countries` | `idea.json` (launch country first) |
| `confidence` | `verdict.confidence.level`, or `null` for a legacy verdict |
| `time_to_first_revenue_weeks`, `ltv_cac` | the metric's `value` when it is a number, else `null` |
| `best_rescue` | `rescue[0]` with only `change`, `score_after`, `difficulty`, or `null` |
| `report` | the idea's folder name + `/report.html`, relative to `ideas/` |
| `stale` | `true` when `evaluated` is more than 90 days before `generated` |
| `history` (optional) | `verdict.history` + the current evaluation, as `{evaluated, score}`, oldest first, at most 10 |
| `hits` (optional) | the `verdict.kill_shots` keys that are `true` |
| top-level `focus` (optional) | the content of `ideas/focus.json`, or `null` |
| top-level `patterns` (optional) | per kill shot: `{kill_shot, hits, checked}` counted over all ideas, most hits first |
| top-level `brain` (optional) | `true` when `ideas/BRAIN.md` exists (shows a link to it) |

Never put a value in the board that is not in the verdict or the idea brief.
