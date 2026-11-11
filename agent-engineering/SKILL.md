---
name: agent-engineering
description: Production AI agent engineering knowledge base — design patterns, memory, retrieval (RAG), evals, guardrails, observability, orchestration, and LLM gateway architecture. Use when building or debugging an AI agent, agent loop, RAG pipeline, tool-calling/MCP integration, multi-agent system, or any agentic automation; when deciding guardrails, autonomy levels, cost controls, or eval strategy for an agent; or when an agent is unreliable, hallucinating, looping, or drifting in production.
version: 1.0.0
---

# Agent Engineering Knowledge Base

A curated, de-duplicated knowledge base of production agent-engineering practice, extracted from
Mahmoud Zalt's site (zalt.me, CC-licensed, explicitly bot-permissive). Source of truth for this
skill: `/var/minis/shared/knowledge-zalt/` (raw 6.8 MB, full text).

This skill is **reference material, not a tool**. Load a section when the task matches it, apply the
pattern, then write the code.

## When to use which reference

| Task | Load this |
|---|---|
| Agent loops, context management, agent "OS", autonomy boundaries, autopilot | `references/agent-architecture.md` |
| Building/strengthening an agent loop; agent going in circles; steering & prompting | `references/agent-architecture.md` → §Agent Loops |
| Long-term memory, "agent forgets", RAG retrieval quality, chunking, vector DB choice | `references/memory-and-retrieval.md` |
| Testing a non-deterministic agent; gating deploys; "is the agent actually good?" | `references/evals-and-observability.md` |
| Tracing, logging, debugging production agent behavior; finding why output is wrong | `references/evals-and-observability.md` |
| Guardrails: input validation, output validation, tool allowlists, gating actions, cost caps | `references/guardrails.md` |
| Deciding human-in-the-loop vs full autonomy; what runs alone and what waits | `references/guardrails.md` → §Autonomy |
| Multi-agent vs single-agent; orchestration patterns (supervisor / pipeline / fan-out) | `references/orchestration.md` |
| Building an LLM gateway / model abstraction layer; provider portability, vendor lock-in | `references/llm-gateway.md` |
| Cost control, token spend, build-vs-run cost, model choice by latency/cost/capability | `references/llm-gateway.md` → §Cost |
| MCP servers, tool calling, connecting agents to the outside world | `references/tools-and-mcp.md` |
| Shipping an agent; build-vs-buy; pilot-to-production; readiness checks | `references/shipping.md` |
| An agent or automation is unreliable, hallucinating, drifting, or "works in demo breaks in prod" | Start at `references/agent-architecture.md` → §Reliability, then `references/evals-and-observability.md` |
| Career/advice/consulting questions ("how do I become an AI engineer") | **Do not use this skill.** This skill is engineering-only. Answer from general knowledge. |

## Quick decision rules (apply before loading anything)

- **Single-agent default.** Start with one agent + good tools. Only split into multi-agent when
  there are genuinely distinct skills, context would overflow, work is parallelizable, or you need
  separation of duties. Every extra agent multiplies failure modes and token cost.
- **Retrieval before prompting.** ~80% of production RAG failures are the retrieval layer, not the
  model. If a RAG answer is wrong, fix retrieval first; do not touch the prompt.
- **Evals gate deploys.** No prompt/model/tool change ships without passing the eval suite. If there
  is no eval suite, that is the first thing to build.
- **Guardrails at three checkpoints** — input, output, action. Push each guardrail as early (as
  close to the input) as it can reasonably go. The model decides what it *wants* to do; your
  guardrails decide what it is *permitted* to do.
- **The context window is finite and expensive.** Memory is not "store everything"; it is deciding,
  at each step, the minimum set of facts the model needs and putting only those in the window. Past
  a point, more context actively hurts.
- **Deterministic code around a probabilistic model.** Treat the LLM as a non-deterministic core you
  wrap with deterministic validation, schemas, retries, and gates.
- **Gates, not intent.** Gate the action, not the intent — check the specific operation the agent is
  about to perform, never a fuzzy reading of what it "means" to do.

## Using the knowledge base

- All references are plain Markdown. Read with `file_read`; search with `file_search`.
- Every section ends with a **Try next** line pointing to a related reference — follow those when the
  problem spans topics.
- Numbers/statistics in the references are from the source site as of 2026 — re-verify before
  quoting them to a user.
- The full unfiltered source (including ~80 SEO/marketing articles excluded here) remains at
  `/var/minis/shared/knowledge-zalt/llms-full.txt` — use it only if a topic is missing from the
  references.
