---
name: self-healing
description: Automatic diagnosis and recovery when Minis' own tooling breaks. Use when a tool returns an error instead of output, a script that used to work now fails, a dependency is missing, or the sandbox seems broken (no network, no DNS, no package installs). Fixes first, explains after.
version: 2.0.0
---

# Self-Healing

Recovery procedures for the failure modes this environment actually hits.

## Triage order

1. **Reproduce** — run the failing command, capture stderr. No guessing.
2. **Classify** — match against the table below.
3. **Fix** — apply the listed remedy.
4. **Verify** — rerun the original command.
5. **Record** — if it failed twice, hand it to the `self-improving` skill.

## Known failure modes and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `curl`/`wget` exit 28, all hosts 000, `ping` hangs | DNS broken in PRoot | `printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\n' > /etc/resolv.conf` |
| `ping` hangs forever | ICMP blocked in sandbox | never use `ping`; use `curl -sS -o /dev/null -w '%{http_code}'` |
| `pip install numpy/pandas/scipy` fails (no matching wheel) | musl-aarch64 has no wheels | `apk add py3-numpy py3-pandas py3-scipy py3-pillow` |
| matplotlib crashes / no display | no display server | `import matplotlib; matplotlib.use('Agg')` before importing pyplot |
| `not found` on a valid ELF binary | binary needs glibc, sandbox is musl | try `gcompat` (`apk add gcompat`); else run from source with Node/Python |
| heredoc with braces/quotes executes weirdly | BusyBox ash is not bash | write the file with `file_write`, then execute it |
| `find -o -name` misbehaves | no grouping in busybox | `-print0 \| while IFS= read -r -d ''` |
| `**` glob does nothing | globstar unsupported | `find <dir> -name '*.ext'` |
| background server dies silently | SIGPIPE when shell exits | redirect: `cmd > /dev/null 2>&1 &` |
| python script dies with MemoryError | 2GB device RAM | process in chunks, avoid loading whole files into RAM |

## Network specifically

```
# The sandbox resets /etc/resolv.conf on restart; re-apply if all hosts fail:
printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\n' > /etc/resolv.conf
curl -sS -o /dev/null -w '%{http_code}\n' https://github.com/   # expect 200
```

## Rules

1. Fix silently when the failure is environmental and the fix is known-safe.
2. If a fix changes user data or deletes anything, ask first — policy as of
   2026-09-21: never delete files without explicit confirmation.
3. If the tool is fundamentally unavailable (needs root, needs glibc, needs
   hardware the device lacks), say so plainly instead of looping on fixes.
4. One fix attempt per known remedy; after that, escalate to the user with
   what was tried and what happened.
