# Verification guide (health check / test procedure)

Created {{DATE}}. Everything here is READ-ONLY unless a step says otherwise. Fixes need the owner's explicit approval afterwards.

## 1. Before running
- Say in one line that the check is starting and about how long it takes.
- If a prerequisite fails (cannot reach a system), say so plainly and stop; do not ask for secrets in chat.

## 2. Run it
- _(command to run, expected duration, exit codes: 0 healthy, 1 attention, 2 problems)_
- Run it once. Re-run only a single targeted check of a failing item to rule out a blip.

## 3. Investigate every warning and failure (read-only) before reporting
| Finding | Look at |
|---|---|
| _(symptom)_ | _(where to look)_ |

## 4. Report (short, phone-friendly)
1. One-line status with counts. 2. What needs attention, with evidence, whether new or known, who fixes it. 3. Meaningful trends. 4. One line of good news. 5. Dated open items. 6. Offer the next step; do not start fixes uninvited.

## 5. Known and accepted conditions (do not raise as new problems)
| Condition | Accepted by / date | Why | Where it is encoded (script exception) |
|---|---|---|---|

## 6. Changing or extending the check
- Keep checks read-only and fast. After a change, run a syntax check and one full run, then update this guide.
