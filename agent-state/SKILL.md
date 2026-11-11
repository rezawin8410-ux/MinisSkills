---
name: agent-state
description: Persistent goal/task ledger and durable environment bootstrap for the Minis agent. Use at the START of any session to recover long-running work, when the user mentions ongoing projects/tasks/goals, before starting multi-step work that may span sessions, and whenever a sandbox tool (python3, rg, git, curl, jq) reports MISSING after a rootfs reprovision.
version: 1.0.0
---

# Agent State — ledger + bootstrap

Minis has chat sessions and timers but **no built-in long-lived task state**, and
its Alpine rootfs is **reprovisioned without warning** (observed 2026-10-20:
python3/rg/git/curl/jq all vanished mid-day). This skill supplies both halves.

Everything here lives under `/var/minis/shared/`, which persists.

---

## Part 1 — The ledger (`minis-ledger`)

Durable goal/task/fact store. Data: `/var/minis/shared/agent/goals.json`.

### Run this first in any session where ongoing work might exist

```sh
minis-ledger brief
```

If `minis-ledger` is not on PATH (fresh reprovision), use the canonical form —
it always works and self-heals python3 on the way:

```sh
sh /var/minis/shared/agent/minis-ledger brief
```

`brief` prints: durable facts, open goals sorted by priority then due date, each
goal's `NEXT` action and first pending tasks, the last logged step, and an
**Attention** section flagging blocked and overdue goals.

### Core commands

| Purpose | Command |
|---|---|
| Session-start briefing | `minis-ledger brief` |
| Single next action | `minis-ledger next` |
| List goals | `minis-ledger show [--status active,blocked]` |
| One goal as JSON | `minis-ledger show g1` |
| Create a goal | `minis-ledger add --title "X" --why "..." --next "..." [--due YYYY-MM-DD] [--priority N] [--task "..."]` |
| Add a task | `minis-ledger task g1 --text "..."` |
| Finish a task | `minis-ledger done g1 t2` |
| Finish a goal | `minis-ledger done g1` |
| Progress note | `minis-ledger log g1 --note "..."` |
| Mark blocked | `minis-ledger block g1 --note "reason"` |
| Clear blocked | `minis-ledger unblock g1` |
| Change fields | `minis-ledger set g1 --status/--priority/--next/--progress/--due/--title` |
| Abandon | `minis-ledger drop g1 --note "why"` |
| Delete | `minis-ledger rm g1` |
| Durable fact | `minis-ledger fact <key> "<value>"` (omit value to delete) |
| List facts | `minis-ledger facts` |
| Archive finished | `minis-ledger prune` |
| Raw dump | `minis-ledger json` |

Goals accept `g1`, bare `1`, or a **unique** title substring as reference.

### Schema

```
goal: id, title, status(active|paused|blocked|done|dropped), priority(1=highest),
      why, created_at, due, progress(0-100), next_action, blockers[], tasks[], log[]
task: id, text, done, done_at
fact: free-form key → value, shown at the top of every brief
```

`progress` auto-derives from completed tasks when tasks exist; a goal flips to
`done` on its own when the last task completes. `log` is capped at 40 entries.

### Workflow discipline

1. **Session start** → `brief`. Never re-derive state the ledger already holds.
2. **User mentions new work** → `add` it immediately, with `--why` capturing
   intent and `--next` holding a concrete verb-first action.
3. **After real progress** → `log` it, and update `--next`. A goal with a stale
   `next_action` is worse than no goal.
4. **Cannot proceed** → `block` with the specific reason, then tell the user.
   `brief` will keep surfacing it until resolved.
5. **Durable discoveries** → `fact`. Use for environment truths that outlive the
   task: device limits, permission states, reprovision history, API quirks.
6. **Periodically** → `prune` so `brief` stays scannable. Archived goals go to
   `goals.archive.json`, not to oblivion.

### Guarantees

- **Concurrency-safe.** All writes hold an `flock` on `goals.lock` across the
  whole load→mutate→save cycle. Verified with 10 simultaneous writers: 10/10
  survived with unique IDs. (An earlier unlocked version silently lost a goal.)
- **Atomic.** Writes go to a temp file, `fsync`, then `os.replace`.
- **Self-healing.** Unparseable JSON is quarantined to
  `goals.json.corrupt.<timestamp>` and the ledger starts empty rather than dying.
- **Failure-safe.** If a command errors mid-mutation the DB keeps its last good
  state — nothing is written.
- **stdlib only.** No pip packages, so a reprovision cannot break it.

---

## Part 2 — The bootstrap (`minis-bootstrap`)

Idempotent toolchain repair. Run it whenever a needed binary is MISSING.

```sh
sh /var/minis/shared/bootstrap/setup.sh          # full setup
sh /var/minis/shared/bootstrap/setup.sh --check   # report only, install nothing
sh /var/minis/shared/bootstrap/setup.sh --quiet   # minimal output
```

Or `minis-bootstrap` if the symlinks are already in place. Exit 0 = ready.

What it does, in order:

1. **detect** — checks each `binary=package` pair from
   `/var/minis/shared/bootstrap/packages.txt`
2. **install** — `apk add --no-cache` for whatever is missing, with per-package
   retry if the batch fails
3. **dirs** — ensures `shared/agent`, `shared/logs`, `workspace` exist
4. **link** — re-creates `/usr/local/bin/minis-ledger` and `minis-bootstrap`
   symlinks into the volatile rootfs, pointing at the durable sources
5. **cron** — detects a stale `/var/minis/cron.lock` and clears it via
   `minis-cron stop`
6. **verify** — re-checks everything, writes `/var/minis/shared/bootstrap/last-run.txt`,
   exits non-zero and names the still-missing tools if anything failed

Add tools by editing `packages.txt` (one `binary=apk-package` per line).

---

## Operating rules for this environment

- **Trust `/var/minis/`, distrust `/`.** Put anything that must survive under
  `shared/`. The rootfs has already been wiped once mid-day.
- **Discover capabilities at runtime, not from docs.** The installed
  `minis-scheduled` binary lacks flags the system prompt documents
  (`--after`, `--interval`, `--command`, `--trigger`). Always check `--help`.
- **Never assume python3 exists.** It was absent until an MCP wrapper installed
  it. The `minis-ledger` shim handles this; your own scripts should too.
- **Memory is the hard ceiling.** 1.8 GB total, ~188 MB available, 1.1 GB of
  swap in use. No local models, no heavy ffmpeg/browser work, no long parallel
  subagent fan-out. Prefer small sequential steps.
- **ICMP is blocked.** Test connectivity with `curl`/`wget`, never `ping`.
