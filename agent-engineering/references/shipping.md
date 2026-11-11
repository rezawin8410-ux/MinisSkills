# Shipping Agents

Getting an agent from a working demo to something that runs in production and stays there.

---

## Why 80% of AI pilots never reach production

The demo works because you ran it on the easy cases. Production fails because it runs on all of them.

The usual sequence:

1. **Prototype blindness** — the demo works on 5 hand-picked examples, so you ship it.
2. **No failure path** — every tool was available, every call succeeded, every output was sane. In
   production a tool times out and the agent improvises into an incident.
3. **No measurement** — you can see the agent ran, but not whether it ran well. So you cannot tell if
   your fix helped.
4. **No gating** — a change ships because it looked fine in manual testing, and silently degrades a
   case nobody checked.

The fix, in order: failure paths → measurement (evals) → deploy gating. See
`evals-and-observability.md`.

---

## The pre-production checklist

Before an agent touches real users:

- [ ] **Eval suite exists and passes** — golden dataset + scoring + gate on the deploy
- [ ] **Output schema validated** on every generation; non-conforming output rejected-and-retried
- [ ] **Every failure path tested**: tool timeout, malformed output, rate limit, context overflow,
      provider down
- [ ] **Guardrails at all three checkpoints** — input, output, action (see `guardrails.md`)
- [ ] **Tool allowlists + scoped permissions**; irreversible actions require human approval
- [ ] **Per-run and per-step token/iteration caps** with alarms (see `llm-gateway.md` → §Cost)
- [ ] **Traces on every run** — prompt, response, tool calls, tokens, latency, cost
- [ ] **Structured logging**, never prose; secrets never logged
- [ ] **Deterministic fallback** for when the model path fails entirely
- [ ] **Rollback plan** for the agent's actions (undo path for anything it did)
- [ ] **Autonomy at minimum**: start with human approval, loosen only as evals justify

---

## Build vs buy

| Choose build when | Choose buy when |
|---|---|
| The capability is your core product or differentiator | It's a commodity capability (generic chat, standard summarization) |
| You need control over data, latency, or cost | Time-to-market matters more than control |
| The workflow is idiosyncratic to your domain | The vendor's offering matches your needs closely |
| You will iterate on it heavily | You won't — it's a one-off |

Guard against the trap: **buying looks cheap at small scale and expensive at large scale**; building
is the reverse. Model both, and put a real number on the run cost (tokens + maintenance + monitoring),
not just the build cost.

**Build vs buy vs wait** — a third option exists. If the capability is not central to your product and
the field is moving fast, waiting is a legitimate choice. Revisit in a quarter.

---

## The 30-day low-risk pilot

For automating a real workflow with an agent:

1. **Pick one workflow** — not a platform. One bounded, repetitive, measurable process.
2. **Shadow-run first** — run the agent alongside the human process; compare outputs, don't trust them
   yet.
3. **Measure before you automate** — baseline the human process's accuracy, cost, and latency. Without
   a baseline you cannot prove the pilot worked.
4. **Start with the least autonomy that works** — human approval on every action; loosen only with
   evidence (see `guardrails.md` → §Autonomy).
5. **Decide the kill criteria in advance** — the accuracy bar, the cost ceiling, the date. A pilot
   without a kill criterion becomes permanent regardless of results.
6. **Review at 30 days against the baseline** — then expand, fix, or kill.

---

## Readiness signals

**Ready for an agent:**
- You can describe the workflow as explicit steps
- The task is repetitive and bounded
- Failures are recoverable or low-cost
- You have data to evaluate against (or can create it)
- The workflow's cost justifies the automation effort

**Not ready:**
- The workflow is tacit knowledge nobody has written down
- It's a one-off or rare task
- Failures are costly, irreversible, or external-facing
- You cannot define what "good output" means
- The human process itself is broken — automating a broken process makes it break faster

---

## Choosing the right LLM for your product

Match the model to the job — see `llm-gateway.md` → §Cost for the full framework: capability,
latency, cost, context size. Default to the smallest/cheapest model that passes the evals.

---

## Once it's in production

- **Watch the bill before it watches you.** Cost alarms on spend rate, per-run caps, model routing —
  a misbehaving loop multiplies token spend quietly (see `llm-gateway.md`).
- **Track eval score over time** — online evals on sampled traffic catch drift your dataset missed.
- **Log every action** so an incident has a replay (see `agent-architecture.md` → §Event Ledger).
- **Close the loop** — when you make a change, check the metric it was supposed to move. A change
  without a measured outcome is not a fix, it's a guess.
- **Don't automate the maintenance away.** An agent system needs a human watching the evals, the
  cost, and the failure modes. The job changes from *doing the work* to *keeping the system that does
  the work honest*.

Try next: `evals-and-observability.md` (measurement), `guardrails.md` (autonomy), `llm-gateway.md`
(cost controls).
