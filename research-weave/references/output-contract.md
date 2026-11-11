# Evidence output contract (v2)

## Reader-facing result

Lead with the research decision and prioritized queue, then explain the historical/evidence relationships needed to assess it. State main conclusions of key papers, strongest counterevidence, meaningful uncertainties, and next observations. Cite inspected locations. Report actual source access, searches, exclusions and stop basis. Avoid a vacuous synthesis such as “everything is conditional” when specific evidence can decide a narrower question.

Scout normally uses a short report and compact ledger. Map/Update may use `report.md`, `evidence.json`, `search-log.md`. Optional collections are included because useful, not to achieve a fixed report length. Historical artifacts retain their original version and are not silently rewritten.

## Versions and profiles

New saved runs use `schema_version: 2` and `profile: scout|full`. Scout requires only `context`, `papers`, `claims`, `reading_queue`; `propositions`, `edges`, `tensions`, `searches` default to empty. Full requires all seven collections. Old v1 full ledgers are readable but receive the same provenance/consistency checks; historical mistakes may now be flagged.

All descriptive fields below are strings, unless a type is stated. Unknown fields produce cautions to catch spelling drift; this permits documented extensions without pretending to validate them. IDs are unique per collection. `null` means unknown, never an invented zero.

## Core records

**Context:** `question`, `decision`, `mode` (`scout|map|update`), `as_of` (ISO date), `bounds`, `stop_reason`. Full also requires `target_setting`. Optional `stop_basis` is `budget|corpus_exhausted|decision_saturation|other`; `deep_read_budget` is a nonnegative integer.

**Paper:** `id`, `title`, `url` (primary URL or `local:` source URI), `access` (`fulltext|excerpt|abstract|metadata|unavailable`). Full requires `version` and `evidence_family`; Scout adds them when known. Access is what was inspected, not what is downloadable. Optional provenance: `doi`, `arxiv_id`, `authors`, `datasets`, `code_urls`, `study_ids`, publication status/date, `retrieved_at`, `aliases`, `note`. Authors/data/code/study IDs/aliases are lists of strings. Source identities and versions must agree; use one paper record for separate results in one version.

For inspected key papers record `main_claim`, `main_claim_locator`, and `main_claim_ids` (claim IDs). When the main evidence is inaccessible or outside the question, use `main_claim_omission` to explain. Declaring a main-claim ID does not prove that it represents the paper faithfully: reopen the main result before delivery.

**Claim:** `id`, `paper_id`, `statement`, `kind` (`empirical|theoretical|interpretive`), `scope`; `evidence`, `assessment`, `uses` below.

- `evidence`: `locator`, `observation`. Full also requires `authors_interpretation`, `analyst_inference`; Scout includes those only when making these claims. Source observation is a faithful result/paraphrase, not a bare citation.
- `assessment`: `strength` (`supported|provisional|unsupported`), `reason`, `limitation`. Supported means inspected primary content supports this *scoped extraction*, not universal truth. Abstract/metadata-only access cannot establish it.
- `uses`: list of `{role, decision, transfer_assumption}`. Role is `fact|mechanism|method|evaluation|boundary|counterevidence|analogy`. An analogy-only claim belongs in proposition context, not direct factual support. A claim may have multiple genuinely different uses.
- Optional `evidence_family` overrides the paper default for a distinct experiment or reused result. Reused results keep their originating family. Labels do not demonstrate independence.
- Optional `status`: `active` (default), `superseded`, `withdrawn`; `superseded_by` references the newer claim. Retain historical observations but exclude inactive claims from current direct support/counterevidence. A source-supported `new supersedes old` edge also marks old inactive.
- Optional `inherited_threats`: claim IDs identifying documented or suspected validity problems that may carry through shared methods/code/data. State whether this is an observed flaw or an analyst hypothesis; sharing code alone proves neither safety nor defect inheritance.
- Optional `estimate`: `{effect, ci_low, ci_high, null_value, unit}`; numeric fields are finite numbers, with `ci_low <= ci_high`. This preserves wide intervals as inspected evidence. It does not turn marginal intervals into a test of the difference between studies, or require different outcomes to be pooled.

**Reading queue:** ordered `{paper_id, action, focus, reason}` items. Action is `deep_read|targeted_read|verify_access|defer|exclude`; reason connects the reading to a decision. Preserve useful historical papers even when one result is superseded.

## Proposition synthesis

Each proposition has `id`, `statement`, `scope`, `synthesis`, `independent_evidence_count` (integer or null), `independence_assessment`, `coverage`, `justification`.

Its disjoint claim-ID lists are:

| Field | Meaning |
|---|---|
| `supporting_claim_ids` | Inspected, active, supported claims directly supporting the proposition at its declared scope |
| `counter_claim_ids` | Inspected, active, supported claims directly opposing it at that scope |
| `uncertain_claim_ids` | Inspected, active, supported observations that cannot discriminate the proposition (e.g. a wide interval covering a meaningful threshold and no gain) |
| `context_claim_ids` | Relevant inspected context, analogy or indirect transfer; not a direct vote |
| `candidate_claim_ids` | Leads not yet adequately assessed or accessed |

A claim's `supported` extraction does not settle membership: separately check quantifiers, task/model conditions, comparator, threshold and inferential precision. For example, a reliable +2% estimate with interval [-3%, +7%] does not settle “at least +5% gain”; record it as uncertain evidence and explain the threshold. Do not demote the inspected study to an unverified lead, infer zero effect, or equate compatibility with confirmation.

| Synthesis | Contract |
|---|---|
| `convergent` | At least two declared independent direct supporting contributions after provenance/validity checks; no unresolved direct counterevidence. Unknown independence calls for a narrower label. |
| `conditional` | Direct support is bounded to specified conditions or independence/generality remains limited. |
| `contested` | Consequential direct counterevidence remains; explain both sides and unresolved alignment. |
| `refuted` | Active direct counterevidence defeats the precise proposition within its stated scope. Requires `refutation_basis`; distinguish a logical/table-level counterexample from uncertain population inference. |
| `uninformative` | Relevant inspected evidence exists in `uncertain_claim_ids` but cannot distinguish the decision alternatives. |
| `insufficient` | Adequate direct evidence for this target question is missing. Context and leads may still be informative for what to retrieve next. |

`independent_evidence_count` includes the originating contribution. `independent_replication_count` optionally counts additional confirmed replications; it cannot exceed `max(independent_evidence_count - 1, 0)`. Count support only, not all papers encountered. For counts above one add `independence_basis` with source locations and a result-level explanation. Unique family IDs, authors, or URLs are not independence proofs. Shared authors/data/code trigger cautions, not automatic falsification.

## Edges and tensions

An **edge** has `id`, `source`, `target` (claim IDs), `relation` (`supports|contradicts|bounds|depends_on|imports|supersedes`), `status` (`source_supported|hypothesis`), `evidence_claims`, `rationale`, `independence` (`independent|shared|unknown`). Source-supported endpoints and relation evidence must be supported extractions from inspected text. Add `source_locator` for the location establishing the relationship itself. An analyst alignment defaults to hypothesis. Using both endpoint results in a comparison is valid; citing their IDs alone does not establish that comparison. Explicitly independent edges also add `independence_basis`.

A **tension** has `id`, `claim_ids` (at least two), `classification` (`comparable_conflict|scope_mismatch|inconclusive|not_opposed`), `comparison`, `explanation`, `resolution_search` (including explicit unperformed searches).

`comparable_conflict` needs supported active claims and adequate uncertainty/alignment. Opposing point estimates with intervals containing the null do not establish conflict without further comparative analysis. A consequential unresolved frontier adds `frontier_candidate: true`, `alternatives` (at least two strings) and `test` with string fields `variable`, `controls`, `predictions`, `gap_killer`. `not_opposed` is not a contradiction frontier; independent untested-regime hypotheses can be described separately. If frontier is false, alternatives/tests are optional diagnostics and do not imply an unresolved literature gap.

## Searches and stopping

A **search** has `channel`, `query`, `source`, `date`, `outcome`, `kept_ids` (paper IDs). Include corpus review, unsuccessful queries, exclusions/access failures in the log; do not fabricate searches. Dates cannot exceed `context.as_of`.

For expansion tracking add `round` (positive integer) and `changed` (IDs of papers/claims/propositions/edges/tensions or `queue:P1`; empty means no decision change). Multiple queries in one round aggregate changes. `stop_basis: decision_saturation` requires two consecutive logged rounds without changes. This establishes only declared in-channel saturation; a budget/corpus stop needs no invented expansions and implies no recall guarantee.

## Machine vs human checks

The checker rejects malformed data, duplicate declared source identities, inconsistent statuses/memberships, unsupported edge endpoints and some dependency/count errors. It cautions on unknown fields, missing main-claim coverage, undeclared relationship provenance, shared source metadata and incomplete logs. It does not fetch papers, check main-claim completeness, prove entailment, establish actual independence, or measure recall. A convincing false extraction can still pass. Manual source comparison remains necessary even with zero errors/cautions.
