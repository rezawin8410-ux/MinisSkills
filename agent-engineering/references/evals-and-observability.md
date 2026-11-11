# Evals & Observability

Observability is **seeing** what the agent did. Evals are **measuring** how well it does it. You need
both, because an agent is non-deterministic: the same input can produce different outputs, so you
cannot verify it by eyeballing a few examples the way you would a normal function.

---

## Evals: test a system that has no right answer

A unit test asserts a function returns exactly what you expect. That model breaks with agents for two
reasons: the output is **non-deterministic** (run the same prompt twice, wording changes, so
string-equality assertions are useless), and **open-ended** ("summarize this ticket" has no single
correct answer — only better and worse ones).

Evals handle both by scoring quality as a **distribution across many runs**, not a single pass/fail.

### The four parts

1. **Golden dataset** — 20–100 real inputs (from actual usage or support tickets, not invented),
   each labeled with an expected output or a clear pass condition. Cover the common cases and the
   known edge cases.
2. **Run** — execute the agent over the dataset.
3. **Score** — exact/structural match for deterministic outputs (a JSON field, a classification
   label); **LLM-as-judge** with a written rubric for open-ended ones. For retrieval-heavy agents,
   also score whether the retrieved context actually contained the answer.
4. **Gate** — no prompt, model, or tool change ships without passing the suite.

### Scoring methods

| Method | For | Notes |
|---|---|---|
| Exact / structural match | Deterministic outputs (JSON schema, classification label) | Cheap, reliable |
| LLM-as-judge + rubric | Open-ended outputs | **Write the rubric carefully** — a vague rubric gives a vague score |
| Retrieval scoring | RAG agents | Did the retrieved context contain the answer? Score retrieval separately from generation |
| Human review | High-stakes / hard-to-judge | Sample, don't review everything |

Rubric rules: score one dimension per criterion (factual accuracy, completeness, tone, format) —
never one blob question. Anchor each score level with a concrete description ("3 = answers the
question but omits one required field"), not just "good/bad".

### Offline vs online evals

- **Offline** — fixed dataset, run before you ship a change. Your regression net. Include past
  failure cases; the suite is only as strong as its hard examples.
- **Online** — sample real production traffic and score it, to catch drift and failure modes your
  dataset never imagined.

### Build order for an agent with no evals

1. Collect the golden dataset (20–100 real inputs + expected outputs/pass conditions).
2. Pick a scoring method per case (deterministic → match; open → LLM-as-judge).
3. Write the judge rubric carefully.
4. Add the known-hard and past-failure cases.
5. Wire the suite into CI so a failing eval blocks the deploy.
6. Run it on the current agent to get a **baseline score** — before changing anything.

> **The regression payoff.** Model providers update models silently; you tweak a prompt; someone
> changes a chunking parameter. Any of these can quietly degrade behavior on inputs you were not
> looking at. An eval suite catches the regression before your users do. That is the entire point of
> testing.

The gap is not theoretical: teams widely wire up observability, but a much smaller share run offline
evals on a test set. They can *see* what their agent did; most still cannot tell you whether a change
made it better or worse. **That gap is what evals close.**

---

## Observability: see every step

An agent run is a chain of decisions. Without tracing it is a black box.

Record the run as a **trace made of spans** — one span per step, nested to show what called what:

| Capture per span | Why |
|---|---|
| The exact prompt sent to the model, including retrieved context and tool definitions | Most failures live here, not in the user message |
| The raw model response, including any tool-call requests | Shows what the model actually decided |
| Every tool call: arguments, result, success/fail | Tool errors the model then improvised around are a top failure cause |
| Tokens in/out, latency, dollar cost per step and per run | Where the money goes; a single misbehaving loop multiplies spend |

> When an agent gives a bad answer, the cause is almost never mysterious once you can see the trace.
> Usually retrieval fetched the wrong context, a tool returned an error the model then improvised
> around, or the prompt ballooned past the point where the model could focus. **Traces turn "the
> agent is flaky" into a specific, fixable step.**

### What to log before production

- Full prompt, full response, every tool call with arguments and result.
- Tokens (in/out), latency, and cost per step and per run.
- Structured fields (run_id, span_id, model, timestamps), not prose — so you can query, not just read.
- **Never log secrets.** Log the event, not the key (see `agent-architecture.md` → §Event Ledger).
- Sample full payloads at a rate you can afford; keep the structured summary on 100%.

### Tracing + logging

- **Traces** answer "what happened in *this* run" (per-request, high detail).
- **Metrics/aggregates** answer "what is happening *overall*" (error rate, p95 latency, cost per run,
  eval score over time).
- **Event ledger** answers "what is the durable history" (see `agent-architecture.md`).
- Keep all three: a metric spike tells you *something* is wrong, a trace tells you *what*, and the
  ledger lets you replay.

---

## How to test a non-deterministic agent

- Run each case **multiple times** and track the score distribution (mean, worst-case, pass rate),
  not a single result. A single run tells you nothing.
- Track the **worst case**, not the average — the tail is what hurts users.
- Test the pieces and their seams separately: retrieval alone, tool calling alone, the full loop
  end-to-end. An end-to-end pass that hides a broken retrieval layer is a false confidence machine.
- Test the **failure paths** explicitly: malformed output, tool timeout, rate limit, context overflow.
  An agent that works only when every tool succeeds is not production-ready.
- Include adversarial inputs in the suite (injection attempts, off-topic, abusive) — these are the
  inputs your guardrails exist for, so test the guardrail, not just the happy path.
- Sanity-check outputs before they leave the boundary — the worst bug is the app that runs and is wrong.

Try next: `guardrails.md` (what to do when the eval/trace shows the agent doing something it
shouldn't), `memory-and-retrieval.md` → §Diagnosing (scoring retrieval quality separately).
