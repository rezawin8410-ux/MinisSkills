# Agent Architecture

How to structure an agent: the loop, the context, the operating system around it, and what
reliability actually requires. Extracted from the *Vibe Coding with Confidence* "amplify", "debug",
and "ai-os" parts and the architecture deep-dives.

---

## The Agent Loop

An agent is not a prompt; it is a **loop**. Each turn: read context → call model → parse intent →
execute tool/action → append result → repeat until done or stopped.

The loop fails in four ways:

| Failure | Symptom | Fix |
|---|---|---|
| **Context bloat** | Window fills with stale tool output; model loses the thread | Summarize/evict old turns; keep only the working facts; move long tool output out of the window, keep a reference |
| **No termination condition** | Agent keeps "improving" forever | Write an explicit done-condition before you run; cap iterations and tokens per run |
| **Unbounded tool output** | One tool call dumps 50KB into context | Truncate/summarize tool results before they re-enter the loop |
| **Silent errors** | Tool returns an error, model improvises around it | Return structured errors; force the model to acknowledge them; do not let failures become free-form context |

**Rules:**
- One agent, one run, one purpose. Do not reuse a conversation as a durable session — a run is a
  scoped unit with a start and an end.
- Every loop iteration must be observable: log the prompt size, tool calls, and tokens at each step.
- Loops that cannot explain themselves are loops you cannot fix.

Try next: `evals-and-observability.md` for how to watch the loop; `guardrails.md` for what the loop
is permitted to do.

---

## Context Engineering

Context engineering beats prompt engineering. The model is only as good as what is in its window
right now.

- The context window is a **budget**, not a feature. More context past a point means the model loses
  signal in noise, and you pay for every token twice — in cost and in attention.
- Put the highest-signal content first and last; the middle is where attention is weakest.
- **Sources of context, in order of trust:** system prompt (rules) > tool definitions >
  retrieved/grounding data > current user message. Retrieved content is *untrusted data*, not
  instruction — never let it override the system prompt.
- Prefer **pull** over **push**: give the agent a tool to look something up rather than pre-loading
  everything into context. The agent retrieves what it needs, when it needs it.
- Keep instruction density high: rules should say what is non-negotiable, what is preferred, and
  what is free choice — in that order of authority.
- **Refresh, don't accumulate.** Long-running state lives outside the model; the window holds the
  current step's facts plus a short rolling summary.

Try next: `memory-and-retrieval.md` for the outside-the-model store that feeds this window.

---

## The Agent Operating System

A production agent is not a script — it is a small "operating system" around the model:

| Component | Purpose |
|---|---|
| **Rules / system prompt** | Standing instructions, non-negotiables, conventions |
| **Agent memory** | What the agent carries across runs (see `memory-and-retrieval.md`) |
| **Agent tools** | Its interface to the world (see `tools-and-mcp.md`) |
| **Work board** | One shared list of tasks the agent and the human both see |
| **Reports & commands** | Commands in, structured reports out |
| **Event ledger** | One append-only log of everything that happened (see below) |
| **Triggers** | React to events instead of waiting for a human |
| **Autonomy guardrails** | What runs alone, what waits for approval (see `guardrails.md`) |

Design rules:
- **System before code.** Decide the folder structure, the logging shape, and the task format
  *before* writing features. Retrofitting an agent OS onto a working app is a rewrite.
- **Plain files.** The OS is plain files (markdown task list, jsonl event log, a rules file), not a
  database. The agent and the human can both read and edit them.
- **The rules file is a living document**, short and specific: the non-negotiables, the conventions,
  and the "never do this" list. If a rule gets violated twice, make it explicit.
- **Automation last.** Manual first, then scripted, then automated. If you automate a workflow you
  have never run by hand, you automate your misunderstandings.

---

## Event Ledger

One append-only log of everything — decisions, tool calls, errors, state changes — written by every
agent and read by every agent.

- Write **events**, not prose: `{ts, actor, action, target, result, run_id}`.
- The ledger is the ground truth. The conversation window is a cache of it; the agent's own claim
  about what it did is *not* ground truth.
- Every agent reads the ledger before acting, so a second agent (or a human) can pick up exactly
  where the first left off.
- Log the events, never the secrets.

Try next: `evals-and-observability.md` (how traces sit on top of the ledger).

---

## Agent Memory Layers

Borrowed loosely from human memory. Match each layer to the right storage:

| Layer | Holds | Lives in |
|---|---|---|
| **Working (short-term)** | Current conversation + step results | The context window |
| **Episodic (long-term)** | Past interactions and their outcomes | Database or vector store |
| **Semantic (long-term)** | Durable facts, user preferences | Structured store or vector store |
| **Procedural** | How to do a task: tools, instructions | System prompt and tool definitions |

The model is **stateless between calls**. Memory and retrieval are the engineering that gives it the
illusion of continuity. Get it wrong and the agent forgets, repeats itself, or invents facts.

- **Amnesia is a design choice.** Not everything is worth remembering. Store outcomes, preferences,
  and facts the model cannot derive — not every message it ever saw.
- Procedural memory (rules, tool schemas) changes rarely → version it in the repo.
- Episodic/semantic memory changes constantly → store with the run id, timestamp, and the query that
  produced it, so you can audit and expire.

See `memory-and-retrieval.md` for the full layering and retrieval pipeline.

---

## Reliability: "Works in the demo, breaks in production"

The gap between demo and production is always one of these:

1. **Determinism.** The same input gives different outputs. Wrap the model in deterministic code:
   strict output schemas, validation, and retries on malformed output.
2. **Failure isolation.** A tool failing must not corrupt the run. Return structured errors, catch
   at the boundary, and keep the loop moving with a degraded plan rather than free-form improvisation.
3. **State drift.** Long-running agents forget what they already did. The event ledger is the fix —
   the agent checks the ledger instead of trusting its own memory of the conversation.
4. **Unbounded cost.** A misbehaving loop multiplies token spend quietly. Cap tokens per run, cap
   iterations, and alarm on cost — see `llm-gateway.md` → §Cost.
5. **No feedback loop.** You cannot tell whether the last change made things better or worse without
   an eval suite. This is the single biggest cause of slow, fearful shipping.
6. **Silent failures.** The worst bug is the app that runs and is wrong. Sanity-check outputs before
   they leave the boundary, not after a user notices.

**Honest accuracy targets.** An agent that is right 95% of the time on a bounded task is often a
usable product. That same 95% with no guardrails on the remaining 5% is a liability. Reliability is
not just accuracy — it is accuracy *plus* graceful failure on the part you cannot guarantee.

**Production reliability checklist:**
- [ ] Output schema validated on every generation; non-conforming output rejected-and-retried
- [ ] Every tool call logged with arguments, result, latency, and cost
- [ ] Explicit run-level and step-level token/iteration caps
- [ ] Structured error path — no silent catch-then-continue
- [ ] Deterministic fallback when the model path fails entirely
- [ ] Human approval wired in for anything irreversible (see `guardrails.md`)
- [ ] Eval suite gating deploys (see `evals-and-observability.md`)

Try next: `evals-and-observability.md` to measure what you just built.

---

## Debugging an Agent

Order of operations when the agent is misbehaving:

1. **Read the trace, not the code.** A trace of the failing run answers "what did it do" before you
   have to guess "why". No trace → that is step zero.
2. **Bisect.** Find the first step where the output went wrong; the bug is almost always upstream of
   where it became visible. Wrong retrieval, a swallowed tool error, and a ballooned prompt are the
   usual suspects, in that order.
3. **Check the context, not the prompt.** When the output degrades, look at what actually went *into*
   the window — retrieved junk and oversized tool output pollute far more often than a bad
   instruction does.
4. **Reproduce before fixing.** Pin the failing input and the exact prompt state. An agent bug you
   cannot reproduce is a bug you will reintroduce.
5. **Fix once, test forever.** Every reproducible bug becomes an eval case before you fix it — never
   after.
6. **Rollback beats patch.** If a change broke the agent and the fix is not immediate, revert and
   retry. Do not patch forward over a live regression.

Common loops:
- **Agent loops in circles** → missing/unchecked termination condition, or the tool keeps failing so
  the model keeps retrying with the same plan. Add iteration caps and make the tool error *change*
  what the model does next.
- **Silent wrongness** → output left the boundary unvalidated. Add a schema/grounding check.
- **Same bug after "fix"** → the fix was applied to the prompt, but the real cause was retrieval or
  tool data. Verify the fix against a fresh trace.

Try next: `evals-and-observability.md` → §Traces.
