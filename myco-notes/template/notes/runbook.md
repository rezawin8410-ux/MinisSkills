# {{PROJECT_NAME}}: runbook (question and answer style)

Created {{DATE}}. Each entry is a question a person (or an AI reader) would ask, with a tested answer: where it lives, the command, how to verify, how to undo, known traps. No secrets; say where they are. If this file and `state.md` disagree, the more detailed note wins and the other is corrected. Times are {{TIMEZONE}}.

## How do I restore from the latest backup? (since {{DATE}})
_(what is backed up, where, the exact steps, how to verify, how long it takes, the last restore drill date)_

## How do I check that everything is healthy? (since {{DATE}})
_(point to `verification/verification.md` and the script)_

## How do I add a new component? (since {{DATE}})
_(steps, plus the notes to update; the matrix in `sync-tracker.md` lists them)_

## What mistakes were made and must not be repeated?
- _(format: Never X because Y happened on DATE. Instead Z. See the incident entry.)_
