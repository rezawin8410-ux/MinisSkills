# LLM Gateway & Cost

How your backend should talk to LLM providers: through **one reliable entry point** with a clean,
provider-agnostic boundary.

---

## Why a gateway

A missing or opaque LLM-integration file is a code smell: you cannot reason clearly about
reliability, security, or cost when you cannot inspect the gateway to your LLM providers.

Treat the gateway as a **first-class component** and a **stable abstraction**, not a utility:

```
your features ──▶ fetchLLM(request) ──▶ [provider adapter] ──▶ OpenAI / Anthropic / local / ...
                     ▲                          
        one request shape, one response shape, one error contract
```

Every feature calls one function. Provider specifics never leak upward.

---

## The clean boundary

A healthy gateway exposes a small set of types that everything else depends on; provider specifics
stay behind an interface.

```ts
interface LLMCompletionRequest {
  model: string;
  prompt: string;
  maxTokens?: number;
  temperature?: number;
}

interface LLMCompletionResponse {
  id: string;
  model: string;
  createdAt: Date;
  text: string;
}

interface LLMCompletionProvider {
  complete(request: LLMCompletionRequest): Promise<LLMCompletionResponse>;
}

async function fetchLLMCompletion(
  provider: LLMCompletionProvider,
  request: LLMCompletionRequest
): Promise<LLMCompletionResponse> { /* ... */ }
```

Once the boundary is right, you can change providers, add features, and improve reliability **without
touching the rest of the application**.

### The operations a gateway owns

- **Retries** with backoff on retryable errors (rate limits, transient 5xx). Never retry
  non-idempotently on a non-retryable error class.
- **Timeouts** on every call, defaulted and overridable.
- **Streaming support** — so a long generation doesn't hold a request hostage.
- **Token/cost accounting** per call (see §Cost).
- **Structured errors**: a typed error contract (rate-limited / invalid-request / provider-down /
  content-filtered) so callers can *react*, not just catch `Error`.
- **Observability hooks**: every call emits a span (prompt size, tokens, latency, cost, model) — see
  `evals-and-observability.md`.
- **Secret handling**: keys live only inside the gateway, never in the caller.

> The model is interchangeable; the boundary is what survives. Get the boundary right and swapping
> providers becomes a config change instead of a refactor.

---

## Avoiding vendor lock-in

A model abstraction layer is what keeps provider choice open:

- **Interface in your codebase, not the provider's SDK shape.** Depend on your own types.
- **Isolate provider quirks** (message formats, tool-call schemas, JSON modes, stop sequences) in
  adapters, one file per provider.
- **Normalize tool calling** to one internal shape so your agent logic is provider-agnostic.
- **Keep a provider switch as a config change**, not a code change. If you cannot switch in one
  place, you are locked in.
- **Beware silent coupling**: a "small" convenience (a provider-specific streaming parse, a magic
  header) is a future migration cost. Review adapter boundaries when you add them.
- Portability is only worth what you are willing to *test* — include more than one provider in your
  evals, or you do not actually have portability, you have the hope of it.

---

## Cost: tokens are the runtime bill

Token cost sinks more agent projects than capability does. Treat cost as a first-class engineering
concern from day one, not a post-launch surprise.

### Build cost vs run cost

- **Build cost** is finite and predictable: engineering time, a few API experiments.
- **Run cost** is unbounded and recurring: every prompt, every retrieval, every loop iteration, every
  retry, every agent you add.
- Teams obsess over build cost and ignore run cost — then the token bill sinks the project.

### Where the money leaks

| Leak | Fix |
|---|---|
| **Loops** | Cap tokens and iterations per run; alarm on runaway cost |
| **Over-retrieval** | Fewer, reranked chunks instead of stuffing 20 |
| **Big prompts for small tasks** | Route simple tasks to small models |
| **No caching** | Cache idempotent calls (embeddings, fixed prompts); a cache hit is not free but it is cheaper |
| **Retries without backoff** | Retry policies must include cost ceilings |
| **Every agent holds full context** | Shared state over duplication (see `orchestration.md`) |

### Model choice: latency, cost, capability

Pick the model by the job, and make the choice explicit:

- **Capability** — does the task need frontier reasoning, or is it extraction/classification?
- **Latency** — interactive (user waiting) vs background (batch).
- **Cost** — per-run cost at your expected volume.
- **Context size** — only as large as the task actually needs; larger windows cost more and can hurt.

Default to the **smallest/cheapest model that passes the eval suite**, and re-run the evals whenever
a model is changed. Routing simple work to small models is usually the single biggest cost lever.

### Cost guardrails to wire in

- **Per-run and per-step token caps.** A loop that cannot stop is a bill that cannot stop.
- **Cost accounting per call**, recorded on the trace span.
- **Budget alarms** on spend rate, not just monthly totals.
- **Per-user / per-tenant quotas** if the agent serves multiple users.
- **Model routing config** — change the model per task in one place.

Try next: `evals-and-observability.md` (cost is a trace field), `agent-architecture.md` → §Agent
Loop (why loops must have caps), `guardrails.md` (cost caps as an action control).
