# Deployed artifacts (repository copies)

Every script, configuration, manifest, query, schema or notebook that runs somewhere else has a copy here. The live copy and this copy must be identical; `{{TOOLS_DIR}}/notes-commit` can sync designated files to where they run (see `.myco-notes.conf`).

Conventions:
- File names: `<system-or-feature>-<what>.<ext>`.
- A bundle of several files gets its own folder with a `README.md` (what it does, where each file is deployed, how to redeploy, how to roll back).
- Never put secrets here; reference where they are stored.
- When you deploy a changed file: copy it here, commit, and write the changelog entry in `../state.md`.
