# Guardrails & Autonomy

Guardrails are deterministic code wrapped around a probabilistic model. The model decides what it
*wants* to do; your guardrails decide what it is *permitted* to do.

They are not one filter you switch on — they are **defense in depth**.

---

## The three checkpoints

Every effective guardrail sits at one of three points where things go wrong:

| Checkpoint | Guards against | Typical controls |
|---|---|---|
| **Input** | Prompt injection, abuse, off-topic or unsafe requests | Validation, sanitization, injection screening, topic limits |
| **Output** | Malformed data, policy violations, hallucinated claims | Schema checks, safety/policy filters, grounding checks |
| **Action** | Unauthorized, costly, or irreversible operations | Tool allowlists, scoped permissions, rate and cost caps, human approval |

**Why separate them:** the same failure can be caught cheaply at one checkpoint and expensively at
another.

> Blocking a malicious instruction at the input costs you a validation call. Catching it only after
> the agent has already sent the email costs you a customer incident. **Push each guardrail as early
> as it can reasonably go.**

---

## Layer by layer: build order

From cheapest and most universal to most situational:

1. **Input validation & injection screening.** Treat user *and retrieved* content as untrusted. Strip
   or neutralize instructions hiding in data; reject inputs outside the agent's job.
2. **Structured output contracts.** Ask for a defined structure and validate against a schema. If it
   does not conform → reject and retry, never pass malformed data downstream.
3. **Policy & safety filtering.** Run outputs through content/policy checks before they reach a
   user — sized to your domain's real risk, not a token gesture.
4. **Grounding checks.** For factual answers, verify claims trace back to retrieved sources, so the
   agent cites rather than invents.
5. **Action controls.** Allowlist the tools; scope each tool's permissions to the minimum; cap rate
   and cost; require human approval for anything risky or irreversible.
6. **Autonomy boundaries.** Decide per-action what runs alone and what waits (below).

---

## Action controls

Actions are where an agent touches the real world, so they carry the highest stakes.

- **Allowlist tools.** The agent can only call what you explicitly list. No open-ended "run a shell".
- **Scope each tool's permissions to the minimum** that still does the job — an email tool scoped to a
  verified sender domain, a file tool scoped to one directory, a payments tool scoped to refunds
  under a threshold.
- **Cap cost and rate** per run and per tool.
- **Gates, not intent.** Gate the action, not the intent — check the specific operation the agent is
  about to perform, never a fuzzy reading of what it "means" to do.
- **Require human approval for anything risky or irreversible.** Reversible and cheap → automatic.
  Irreversible or costly → human.
- **Audit log every action** with actor, action, arguments, result, and timestamp (see
  `agent-architecture.md` → §Event Ledger).
- **Delegated authority:** the agent acts on your behalf, so log who authorized the agent and what it
  was authorized to do. An agent with unlogged authority is an agent you cannot hold accountable.

### Gating an agent's actions

A practical gate design:

```
action requested
  ├─ is the tool in the allowlist?          → no: deny
  ├─ are the arguments within scope?        → no: deny
  ├─ is it reversible + cheap?              → no: require human approval
  ├─ within rate/cost caps?                 → no: deny or queue
  └─ execute → log → return structured result
```

Every deny path must return a *structured* result the loop can act on, not an exception the model
improvises around.

---

## Autonomy: human-in-the-loop vs on-the-loop

Decide per action, not per agent:

| Level | What the human does | Use when |
|---|---|---|
| **Full autonomy** | Nothing — runs alone | Reversible, cheap, well-evaluated, low blast radius |
| **On the loop** | Reviews asynchronously after the fact | Reversible, low stakes, but volume is too high to pre-approve |
| **In the loop** | Approves before the action happens | Irreversible, costly, external-facing, or touches user data |
| **Manual** | Human performs it, agent drafts | High stakes, rare, or the failure cost is unacceptable |

Rules:
- **Default to the least autonomy the task tolerates**, not the most it can survive.
- Autonomy is earned: start with human approval, and loosen it only when the eval suite proves the
  action is reliable and the failure mode is graceful.
- **Never give an agent an irreversible action it has not failed gracefully first.** Make it fail
  safely in a sandbox before it fails unsafely in production.
- An automation that breaks silently is worse than no automation — it degrades trust in the *whole*
  system, including the parts that work.

### When NOT to automate

- The workflow is poorly understood (you cannot describe it as steps yet).
- The failure is costly and unbounded and you have no rollback.
- The cost of the automation (build + operate + monitor) exceeds the labor it saves.
- The task is rare and irregular — automating it calcifies a workflow that should stay flexible.
- The decision requires judgment the agent demonstrably does not have (check the evals, not the demo).

Try next: `evals-and-observability.md` (autonomy is *earned* with evals), `agent-architecture.md`
→ §Reliability.
