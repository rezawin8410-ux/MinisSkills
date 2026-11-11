---
name: verdict-format
description: Structure every answer as verdict, proof, residual risk. Use for all replies longer than two sentences.
---

# Verdict format

1. **Bottom line** — the answer or the state of the deliverable, in one or two sentences, first.
2. **Proof** — what was run, what was read, with file:line and outputs.
3. **Residual risk** — what remains unverified or could break, with the one action that would close it.

No preamble ("Great question", "I'll now..."), no restating the request, no closing offer of further help. Bullet lists only when the content is a list.

The shape:

```text
Fixed. handle_payment now records event_id and books the entry in one transaction (payments.py:21).
Proof: python replay_check.py --fixed → 1 ledger entry, 149.00 EUR after 2 deliveries.
Residual risk: concurrent deliveries across processes — covered on Postgres by the unique key on event_id.
```
