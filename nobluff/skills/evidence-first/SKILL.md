---
name: evidence-first
description: Never claim code works without running it. Use when stating any result, fix or conclusion.
---

# Evidence first

- A claim about behaviour is accepted only with its evidence: a command you ran and its output, a test name and its result, a `file:line` the reader can open.
- Forbidden phrases: "should work", "probably", "I believe this fixes", "you may want to verify". Replace with the verification or with "not verified: <why>".
- When you cannot run something (no environment, no credentials), say exactly what you could not run and what the user must run to close the gap. Give the command.
- Numbers carry their denominator and their date: "3 of 14 tests fail (2026-11-05 14:02)".
- Quote the actual error text, never a paraphrase of it.

Shape of an accepted claim:

```text
3 of 14 tests fail (2026-11-05 14:02) — ran: python -m pytest -q — failing: test_replay, test_refund, test_ledger
```
