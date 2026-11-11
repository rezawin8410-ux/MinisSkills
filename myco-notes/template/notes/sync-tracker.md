# Sync tracker (read this when asked to "update the notes", and after any change)

Created {{DATE}}. One place that says which copies of the project's documentation exist, when each was last brought in line with reality, how to refresh it, and what is pending. Times are {{TIMEZONE}}. No secrets in this file.

## Rules (follow at the end of every session that changed something)
1. After a change, update the notes it touches (see the matrix below), add ONE line to the sync log, move the "last synced" date of each target you really updated, then commit everything together with `{{TOOLS_DIR}}/notes-commit "what and why"`.
2. If a target could not be updated (media not available, machine asleep, a password needed, the owner's yes missing), write it under **Pending** with what is needed. Never mark something synced that was not.
3. At the start of a session read this file with `working-agreement.md` and `state.md`; ask the owner about pending items that need them, once per session.
4. No copy-suffix files (`.bak`, `-old`) for files in this repository; history is git. Copy-suffix backups are for files OUTSIDE the repository, before changing them.
5. Read a file immediately before editing it; edit in place.

## Sync targets (derived-copy ledger)
| Target | What it is | Last synced | How to refresh | Status |
|---|---|---|---|---|
| Core notes | `state.md`, `runbook.md`, entity notes, `plans/` | {{DATE}} (baseline) | edit the files; verify facts live first | in sync |
| Mirror | second copy of this repository on another machine/disk | not set up | `{{TOOLS_DIR}}/mirror-push` | pending |
| Independent snapshots | encrypted, versioned backup of the mirror | not set up | backup job | pending |
| Notes map | generated navigable view | not built | `python3 {{TOOLS_DIR}}/build-notes-map.py` | pending |
| Offline encrypted copy | disaster-recovery media with checksum manifest | not set up | refresh script + `SHA256SUMS` | pending |
| Search index / knowledge base | AI-readable copy (state its allow-list of files) | not set up | loader | pending |

## Pending (what is still to do, and who)
1. _(item; owner; waiting on)_

## What to update when X changes (dependency matrix)
| Change | Update these |
|---|---|
| New or removed component | `state.md` (inventory, changelog), `runbook.md` Q&A, entity note, monitoring, verification guide, diagrams |
| New access method or account | `state.md` Access, secrets table (location only), `working-agreement.md` if rules change |
| Backup or recovery change | `state.md` Backups, `runbook.md`, verification guide, restore-drill record |
| Decision made or plan started | `plans/backlog.md`, the plan or decision file, `state.md` open items |
| Version update | `state.md` inventory versions, changelog |
| Incident | incident entry (`logs/`), `runbook.md` lessons list, monitoring |
| Anything else | at least a dated changelog line in `state.md` and one line in the sync log |

## Sync log (newest first)
- **{{DATE}} ({{TIMEZONE}}):** notes tree created from the Myco Notes template. Nothing else synced; mirror, snapshots, map and offline copy are pending.
