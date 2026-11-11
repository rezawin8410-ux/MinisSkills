# Memory & Retrieval (RAG)

A language model only knows what is in its context window right now. Memory is what the agent
carries forward; retrieval is the mechanism that puts the right slice of an external store into the
window at the right moment. This reference covers the layering and the failure modes.

---

## Memory Layers

| Layer | Holds | Where it lives |
|---|---|---|
| **Working (short-term)** | The running conversation + step results | The context window |
| **Episodic (long-term)** | Past interactions and their outcomes | Database or vector store |
| **Semantic (long-term)** | Durable facts, user preferences | Structured store or vector store |
| **Procedural** | How to do a task: tools, instructions | System prompt + tool definitions |

The model is stateless between calls. Memory and retrieval are the engineering that gives it the
illusion of continuity. Get it wrong → the agent forgets, repeats itself, or invents facts. Get it
right → it feels like it remembers.

**The binding constraint:** the context window is finite and expensive. You cannot append every past
message forever — you hit the limit, cost climbs, and past a point more context *hurts* because the
model loses signal in noise.

> Memory is not about storing everything. It is about deciding, at each step, the **minimum set of
> facts the model needs** and putting only those in the window.

---

## The Retrieval Pipeline

The mechanics are the same whether you retrieve documents or long-term memories:

```
chunk → embed → store → search → rerank → inject
```

1. **Chunk** — split the source into passages small enough to be specific but large enough to keep
   meaning. Paragraph or semantic boundaries beat fixed-token windows.
2. **Embed** — convert each chunk into a vector so similar meanings sit near each other.
3. **Store** — keep the vectors in a vector store (`pgvector` is enough for most teams; managed only
   when you need scale).
4. **Search** — embed the query, find nearest chunks by similarity; combine with keyword search for
   exact terms (hybrid search).
5. **Rerank** — a cross-encoder reranker turns a rough top-k into an actual relevance ranking.
6. **Inject** — put only the top few into the prompt, with their source reference.

---

## Why RAG Returns Wrong Answers

~80% of production RAG failures are the **retrieval layer**, not the model. The model is faithfully
answering from bad context. Fix retrieval first; do not touch the prompt.

Failure modes, in order of frequency:

| # | Failure | Symptom | Fix |
|---|---|---|---|
| 1 | **Chunks wrong size** | A 2000-token chunk buries the relevant sentence; a 50-token chunk strips context | Match chunk size to query type (below) |
| 2 | **Embedding ≠ domain** | Poor similarity on legal/medical/technical vocab | Fine-tune or swap to a domain embedding model; measure before/after |
| 3 | **Pure semantic search** | Queries for `CVE-2024-4171` or `Section 12(b)(2)(A)` have no useful semantic neighborhood | Hybrid search: semantic + keyword/BM25 |
| 4 | **No reranking** | Mediocre chunks rank alongside good ones | Add a cross-encoder reranker after top-k |
| 5 | **Metadata ignored** | Right text, wrong tenant/version/date range | Filter on metadata (tenant, version, date) *before* similarity ranking |
| 6 | **Context stuffing** | 20 chunks crammed in; the model does retrieval inside its own window | Retrieve fewer, better chunks; let the reranker cut the count |

### Chunking: the most underrated decision

"Split every 512 tokens with 50-token overlap" is a placeholder, not a strategy.

| Query type | Chunk size | Why |
|---|---|---|
| Short factual (lookups, definitions, values) | 150–300 tokens | Small focused chunks, specific answers |
| Reasoning (comparisons, summaries, multi-step) | Larger, section-level | Needs surrounding context to reason over |

Then:
- Chunk on **semantic boundaries** (headings, paragraphs, list ends), not blind token counts.
- Preserve structure: keep heading hierarchy and list integrity inside a chunk.
- **Measure chunking, don't guess it.** Score retrieval precision/recall on a labeled set before and
  after any change to the chunking parameters.
- Re-chunk and re-embed when the source changes — stale indexes are a silent retrieval failure.

### Reranking

Top-k by cosine distance is a **rough filter, not a relevance ranking**. A cross-encoder reranker
scores query↔chunk relevance properly and is usually the single highest-leverage addition to a
mediocre RAG pipeline.

### Access control is a retrieval problem

Retrieval must never return a document the user has no right to see. Filter by permission
*inside the retrieval step*, before results reach the model — not as a post-check on the answer.
"Retrieved the right text from the wrong tenant" is a retrieval failure even when the similarity
score looks perfect. Prompts are not permissions.

---

## Choosing a Vector Database

- **Default to `pgvector`** (Postgres extension). If you already run Postgres, it removes a whole
  system to operate, and it covers most real workloads.
- Reach for a dedicated vector DB only when: scale exceeds what pgvector handles, you need
  specialized index types, or you have a dedicated infra team to run it.
- **Do you need a vector DB at all?** If your corpus is small, structured, or keyword-shaped, exact
  SQL/keyword search plus reranking may beat vectors at a fraction of the cost and complexity.
  Many teams install a vector database before they need one, then operate it forever.

---

## Diagnosing a Broken RAG Pipeline

Run this order — it matches the failure frequency above:

1. **Score retrieval separately from generation.** Feed the retrieved chunks to a judge (or a human)
   and ask "does the answer live in here?" If no → retrieval problem. Stop, do not touch the prompt.
2. **Inspect the chunks** for the top failing queries. Too big? too small? cut mid-sentence?
3. **Check the embedding model** against domain vocabulary. Cosine similarity scores that all cluster
   near each other is a symptom of a model with no discriminative power in that domain.
4. **Try keyword/hybrid** on exact-identifier queries that semantic search misses.
5. **Add reranking** and measure the lift.
6. **Add metadata filters** (tenant, version, date) and confirm they are actually applied pre-ranking.
7. **Only then** consider prompt changes.

**Regression guard:** every retrieval change (chunking, embedding, reranker, filters) must pass an
offline retrieval-quality eval before shipping. See `evals-and-observability.md`.

---

## RAG vs Fine-Tuning vs Prompting

| Method | Use when | Cost/effort |
|---|---|---|
| **Prompting** | Behavior change, formatting, tone, simple instructions | Cheapest; always try first |
| **RAG** | The model needs knowledge it was not trained on, or knowledge that changes | Medium; adds a pipeline to operate |
| **Fine-tuning** | Consistent style/format, a narrow domain vocabulary, or a fixed behavior you cannot get reliably otherwise | Highest; freezes knowledge at training time |

Defaults:
- **Try prompting first.** Most "we need fine-tuning" problems are prompting + RAG problems.
- Fine-tuning does not fix stale knowledge — it fixes *behavior*, not facts. Combine with RAG if the
  knowledge also moves.
- Fine-tuning is **not** worth it when: the domain shifts often, you have under ~1k examples, the
  gain is unmeasured, you need auditability, or prompting+RAG already meets the quality bar.

Try next: `agent-architecture.md` → §Context Engineering (what happens to retrieved chunks once they
are inside the window), `evals-and-observability.md` (scoring retrieval quality).
