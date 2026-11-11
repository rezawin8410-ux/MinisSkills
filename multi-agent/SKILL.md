---
name: multi-agent
description: Run multiple specialized sub-agents on one task. Use when a job has clearly different parts (research + code + review), when a task would blow past one context window, or when the user asks for a "second opinion" / parallel work. Each sub-agent gets one job, one context, one output.
version: 2.0.0
---

# Multi-Agent

Split a large task into roles, run each in its own context, then merge.

## When it pays off

- The task has 2+ distinct skills (e.g. gather data, then analyze, then write).
- One context would overflow (long research + long generation).
- The user wants independent verification (writer + critic).

## When it does NOT

- Small tasks — the overhead of splitting is the whole cost.
- Steps that need each other's intermediate state every few lines.

## Roles that work well here

| Role | Job | Output |
|---|---|---|
| researcher | gather sources, facts | a compact brief |
| analyst | find patterns in the brief | conclusions + caveats |
| writer | turn conclusions into the deliverable | the final document |
| reviewer | critique the deliverable | a punch list |
| runner | executes commands, captures output | logs + exit codes |

## How to run one sub-agent

```
minis-model-use run --model <modelId> --input <json_file> --output <path>
```

The input is OpenAI-shaped `messages`. Keep one role per invocation; pass the
previous role's output file as the next role's input context.

## Rules

1. Each sub-agent gets a single, written goal — never "help with the task".
2. Pass context as files, not as re-explained prose.
3. Merge in a final pass that reads all outputs; do not concatenate blindly.
4. If two sub-agents disagree, report the disagreement instead of averaging.
5. Small models for research/extraction, the strong model for the merge — the
   device is RAM-limited, so run at most one heavy model at a time.
