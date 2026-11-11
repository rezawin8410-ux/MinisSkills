# Tools & MCP

How an agent reaches the outside world: tool calling, and the Model Context Protocol (MCP) as the
standard way to expose tools.

---

## Tool calling

Tool calling is how an agent takes real actions. The model doesn't execute anything — it *requests*
an action, and your code executes it and returns the result.

The flow:

```
user ──▶ model sees tool definitions ──▶ model emits a tool-call request (structured args)
                                              │
                                    your code validates + executes the tool
                                              │
                                     result returned into the context ──▶ model continues
```

Key points:

- **The model proposes; the code disposes.** Never let the model execute anything itself — the
  request is a *suggestion* your runtime approves, executes, and logs.
- **Tool definitions are part of the context.** Every tool you define costs tokens and attention, so
  keep the set small, well-named, and precisely documented.
- **Validate the arguments** at the boundary before executing. The model will eventually send
  malformed args; treat that as a normal input, not an exception.
- **Return structured results**, not prose — the model reasons better over a typed object than a blob
  of text.
- **Log every call** with arguments, result, latency, cost (see `evals-and-observability.md`).

### Tool design rules

- **One tool, one purpose, one clear name.** If the model must guess which tool to call, the
  definition is bad. The name + description should make the right choice obvious.
- **Give tools the smallest possible scope** (see `guardrails.md` → Action controls). An
  `exec_shell` tool is almost always a mistake; a `read_file_in(dir)` tool is not.
- **Tools should fail loudly and structurally.** `{ok: false, error: "..."}` beats a raised exception
  the model improvises around. A tool that swallows its error teaches the model to ignore failures.
- **Idempotent where possible**, especially for anything the model may retry.
- **Document the arguments with constraints and units**, not just types (`timeout_seconds`, max 30).
- Prefer **fewer, composable tools** over many specialized ones — the model is better at combining a
  handful of primitives than picking among dozens of near-duplicates.

---

## MCP (Model Context Protocol)

MCP is a standard protocol that lets an agent talk to external servers that expose tools, resources,
and prompts — so integrations become interchangeable instead of bespoke per-app.

An MCP server exposes:

| Primitive | What it is |
|---|---|
| **Tools** | Actions the model can call (functions with typed args) |
| **Resources** | Data the model can read (files, records, endpoints) — identified by URI |
| **Prompts** | Pre-defined prompt templates the agent can use |

### Why it matters

- **Write once, use everywhere.** Build a tool integration once; any MCP-aware agent can consume it.
- **Standard boundary** — the same "clean interface" principle as the LLM gateway (see
  `llm-gateway.md`), applied to tools instead of models.
- The **context object** is the control tower of an MCP server: it carries session state, request
  scope, logging, and the tool registry through every call.

### Should you build a custom MCP server?

Default: **no**, until you have a concrete reason.

Reasons it *is* worth it:
- You need the same capability available to **multiple different agents or agent frameworks**
  (portability is the actual payoff).
- You want third parties to integrate with your agent ecosystem through a **stable, documented
  contract**.
- The capability is complex enough that a **well-tested standalone server** beats a tangle of inline
  tool code.

Reasons it is *not*:
- You have one agent and one integration — a plain tool function is simpler.
- The tools are internal-only and never reused.
- You would rather build the feature than build the plumbing.

---

## Common tool-calling failure modes

| Failure | Cause | Fix |
|---|---|---|
| Model calls the wrong tool | Names/descriptions ambiguous | Rename; tighten the description; split or merge tools |
| Model hallucinates arguments | Schema too loose or undocumented | Add constraints and examples to the schema; validate at the boundary |
| Tool result pollutes context | Tool dumps raw oversized output | Truncate/summarize the result before it re-enters the loop |
| Model ignores a tool failure | Error returned as free-form text | Return a structured error the model must acknowledge |
| Model loops on the same failing call | Same args retried unchanged | Make the error *change* the next action; cap retries |
| Agent does something irreversible | No gate on the action | Allowlist + scope + human approval (see `guardrails.md`) |

---

## Security: the injection surface

Tools turn text into consequences, so tool input is an attack surface:

- **Prompt injection comes through tools and retrieved content, not just the user.** Any data the
  agent reads from outside (a webpage, a document, a tool result, retrieved context) can carry
  instructions. Treat all of it as untrusted *data*, never as instruction.
- **Data and instruction channels must be separated.** Retrieved/tool content goes in the data
  channel; only your system prompt is the instruction channel.
- **Scope tools to limit blast radius.** The narrower the tool, the less damage a successful
  injection can do.
- **Re-validate before irreversible actions** — an instruction that arrived via retrieved content
  must never authorize an irreversible action. Human approval gates it (see `guardrails.md`).
- **Never pass secrets through the model.** Keys stay in the gateway/runtime, never in the prompt or
  tool arguments.

Try next: `guardrails.md` (gating tools), `agent-architecture.md` → §Context Engineering (tool
results as context), `llm-gateway.md` (normalize tool calls across providers).
