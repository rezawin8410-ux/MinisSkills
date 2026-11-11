# Multi-Agent Orchestration

When to use multiple agents, and the patterns to coordinate them once you do.

---

## Start with a bias against multi-agent

A single agent with good tools is **simpler to build, cheaper to run, and far easier to debug** — and
it solves more problems than people expect.

Multi-agent earns its complexity **only** in these situations:

| Justification | Example |
|---|---|
| **Genuinely distinct skills** | Subtasks need different tools, instructions, or even different models. Separating keeps each prompt small and focused. |
| **Context would overflow** | One agent holding every instruction and every tool bloats the window and loses the thread. Splitting keeps each context lean. |
| **Independent parallel work** | Subtasks don't depend on each other → run as parallel agents to cut latency. |
| **Separation of duties** | A reviewer agent checks a builder agent's work and catches mistakes *precisely because* it reasons independently. |

If none apply, one well-equipped agent is the better engineering decision.

> Every additional agent multiplies the moving parts, the failure modes, and the token bill. Reach
> for orchestration when the problem structure demands it — not because multi-agent sounds more
> capable.

The relevant question is rarely "can agents collaborate?" but "**would a single agent with better
tools handle this?**" Most of the time the answer is yes.

---

## The core patterns

Most real systems combine four patterns. Knowing them by name turns a fuzzy "agents talking to
agents" idea into a design you can reason about:

| Pattern | How it works | Fits when |
|---|---|---|
| **Supervisor** | A lead agent delegates subtasks to workers and integrates their results | The task needs planning plus specialized execution |
| **Sequential pipeline** | Each agent's output becomes the next agent's input, in a fixed order | The work has clear, ordered stages |
| **Parallel fan-out** | Several agents run at once, then a step merges results | Subtasks are independent; latency matters |
| **Hierarchical** | Supervisors nested over supervisors (patterns composed) | The work decomposes into sub-orgs |

### What makes it orchestration (not a pile of prompts)

Two things separate a designed system from a prompt soup:

1. **Shared state** the agents read and write — so each agent sees the current truth, not a stale copy
   it was handed at birth. See `agent-architecture.md` → §Event Ledger.
2. **Explicit handoffs** that move control between agents — the caller decides who runs next, with
   what context, and what the result means.

Without shared state and explicit handoffs you have agents passing unvalidated text to each other,
which is how errors compound silently across a chain.

### Handoff rules

- Pass **structured payloads** (typed objects), not free-form prose, between agents.
- Each handoff must carry: which agent ran, what it produced, and its confidence/quality signal.
- A receiving agent treats upstream output as **untrusted input** — validate it, don't assume it.
- Log every handoff to the event ledger, so a failure can be traced across agent boundaries.

---

## Failure modes (what goes wrong)

- **Chat instead of work.** Agents ping-pong messages instead of acting. Fix: give each agent a
  *task* with a done-condition, not a conversation partner.
- **Compounding errors.** Each agent trusts the previous agent's output. Fix: validate at every
  handoff; the reviewer is only useful if it can *reject*.
- **Context duplication across agents.** Each agent re-retrieves the same data. Fix: shared state —
  one store, many readers.
- **Token explosion.** N agents each with full context. Fix: keep each agent's context minimal and
  move bulk state to the shared store.
- **Untraceable failures.** "One of the agents broke it." Fix: per-agent spans in the trace + handoffs
  in the ledger (see `evals-and-observability.md`).
- **Deadlocks / infinite delegation.** A→B→A forever. Fix: a global iteration/depth cap owned by the
  orchestrator, not by the agents.

---

## Practical guidance

- **The orchestrator is the system's control tower** — it decides who runs, with what context, and
  when. Put the reliability controls there (caps, gates, logging), not in the individual agents.
- **Fewer agents, better divided.** If you cannot say in one sentence what an agent is responsible
  for, it is doing too much.
- Give each agent its **own scope of tools and permissions** — least privilege per agent, same as
  per-action gating in `guardrails.md`.
- Multi-agent systems are observability-heavy: the trace must show the full tree, not just the agent
  that produced the final answer.

Try next: `guardrails.md` (gate each agent's actions), `evals-and-observability.md` (trace the whole
tree), `agent-architecture.md` → §Agent Loop.
