# The quality bar

"What is done" is a per-domain question. This file is where the answers live, so the build phase never has to invent its own standard.

The general rule, before the per-domain ones:

> **A thing is done when it has been checked by something other than the thing that made it.** The agent that wrote the code does not get to certify it.

---

## UI / visual design

| Level | Means |
|---|---|
| **Broken** | Layout breaks, content overlaps, unreadable contrast, unusable on mobile. |
| **Correct** | Renders correctly at all breakpoints. Functional. |
| **Designed** | A deliberate visual direction, chosen on purpose, applied consistently — with tokens that exist as a file. |
| **Award-level** | Survives the roast from three independent lenses with no critical or major findings. |

**The bar for a public-facing page is Award-level.** For an internal tool, *Designed* is usually enough.

**Never let a page reach "done" without a screenshot.** Reasoning about how a page probably looks is not inspection, and it is the single most common way a generic page ships.

**Never present one design option.** Three to four, side by side, with previews. A choice presented as an option is not a choice.

---

## Code

| Level | Means |
|---|---|
| **Broken** | Does not run. |
| **Works** | Does what the spec says, on the happy path. |
| **Right** | Spec satisfied, edge cases handled, errors handled, and it will not silently rot. |
| **Maintainable** | Another agent can read it cold and change it without guessing. |

**Right** requires: errors handled rather than swallowed, inputs validated at the boundary, and the two or three cases most likely to occur in production actually exercised by a test.

**Maintainable** adds: names that state intent, no comment explaining what the code obviously does, and one obvious way to do the common thing.

---

## Research / feasibility

The bar is not "I found some articles." It is:

- A verdict (GO / GO-WITH-CHANGES / NO-GO)
- A named hard part with a reason
- Evidence with sources
- **UNVERIFIED** called out explicitly

A verdict with `CONFIDENCE: low` and named gaps is better than a confident verdict built on thin research. The build phase will act on the confidence rating.

---

## Efficiency

Three numbers, reported every build:

| Measure | Failure condition |
|---|---|
| **Token cost per phase** | One phase costing more than the entire rest of the build. |
| **Skills invoked per phase** | More than 3. More means one job spread across many tools. |
| **Context always-on** | Anything non-trivial. Detail belongs in `reference/`, loaded on demand. |

A build that is 40% cheaper than last time and equally good is a better build. Report the comparison when you have one.

---

## Completion honesty

The report must state, without hedging:

- what was built
- what was verified, and **the command or screenshot that proves it**
- what was **not** verified, and why
- what was skipped, and why
- what the next session should know

If a phase produced nothing, say the phase produced nothing. If a fix was applied but not re-checked, say that. **A report that claims everything passed is worth less than a report that names its soft spots**, because it gives the reader nothing to act on.
