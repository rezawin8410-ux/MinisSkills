# Compact Scout format

Use this contract for a short saved run; the full output-contract is only needed when adding rich propositions, edges or tension records. The example is fictional and complete.

Exact enums:

- `paper.access`: fulltext, excerpt, abstract, metadata, unavailable.
- `claim.kind`: empirical, theoretical, interpretive.
- `assessment.strength`: supported, provisional, unsupported. Supported requires inspected primary content.
- `uses.role`: fact, mechanism, method, evaluation, boundary, counterevidence, analogy.
- Queue `action`: deep_read, targeted_read, verify_access, defer, exclude.

Use `evidence_family`, not `family`. Extra interpretation goes under `evidence.authors_interpretation` / `evidence.analyst_inference`. Keep `scope` and other descriptive values as strings. Unknowns remain explicit; do not copy fictional content into a real run.

```json
{
  "schema_version": 2,
  "context": {
    "question": "Test loop?",
    "decision": "Prototype choice",
    "target_setting": "Fixed task/model/budget",
    "mode": "scout",
    "as_of": "2026-10-31",
    "bounds": "Explicitly fictional demonstration of the compact format; one supplied excerpt only",
    "stop_reason": "Supplied example exhausted",
    "stop_basis": "corpus_exhausted"
  },
  "papers": [
    {
      "id": "P1",
      "title": "Fictional study 1",
      "url": "local:P1",
      "version": "1",
      "access": "excerpt",
      "evidence_family": "F1",
      "main_claim_ids": [
        "C1"
      ]
    }
  ],
  "claims": [
    {
      "id": "C1",
      "paper_id": "P1",
      "statement": "Gain in specified regime",
      "kind": "empirical",
      "scope": "Fixed task/model/budget",
      "evidence": {
        "locator": "Table 1",
        "observation": "Paired accuracy gain, 200 tasks, interval excludes zero"
      },
      "assessment": {
        "strength": "supported",
        "reason": "Primary result excerpt inspected",
        "limitation": "One regime"
      },
      "uses": [
        {
          "role": "fact",
          "decision": "Whether to test the loop",
          "transfer_assumption": "Same regime"
        }
      ]
    }
  ],
  "reading_queue": [
    {
      "paper_id": "P1",
      "action": "targeted_read",
      "focus": "Table 1",
      "reason": "Test loop in target regime"
    }
  ],
  "profile": "scout"
}
```

Known version/family and main-claim coverage are useful even in Scout. For genuinely inaccessible main evidence, replace `main_claim_ids` with a `main_claim_omission` explanation. Keep superseded observations with `status: superseded` and `superseded_by: <new claim ID>`; use `inherited_threats: [claim IDs]` for possible carried-over validity problems. An optional numeric `estimate` uses effect, ci_low, ci_high, null_value and a unit string. Wide intervals remain inspected, uncertain evidence, not proof of zero effect.

Add `deep_read_budget` to context if the user sets a count. Offline stops can use stop_basis=corpus_exhausted. Live round tracking is only needed when claiming decision_saturation. The report states the substantive reasoning and uncertainty; the compact ledger need not duplicate every paragraph.
