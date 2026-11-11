---
name: model-routing
description: Choose the right model for each job on this device. Use when a task seems too slow, too weak, or too expensive for the current model, when the user asks for a cheaper/faster/smarter model, or when configuring providers and model groups. Matches task shape to model size on a 2GB-RAM Android device.
version: 2.0.0
---

# Model Routing

On this device (Samsung A115F, 2GB RAM, Android 12) model choice is a real
constraint, not a preference. Route by task shape.

## Routing table

| Task shape | Example | Route to |
|---|---|---|
| Trivial / mechanical | reformatting, regex, unit conversion | smallest available model |
| Extraction | pull emails/URLs/tables from text | small fast model |
| Summarization | a long article → 5 bullets | mid model, cheap endpoint |
| Search-then-answer | "find the newest X" | search tool + small model for the answer |
| Writing in Persian/RTL | emails, messages | mid model with good multilingual output |
| Code generation | scripts, fixes | strongest coding model available |
| Multi-step reasoning | debugging, architecture, planning | strongest reasoning model, one at a time |
| Vision | read a screenshot, OCR an image | vision-capable model only when needed |

## Practical rules

1. **One heavy model at a time.** 2GB RAM means a single large inference; do
   not fan out to several big models in parallel.
2. **Prefer tools over models.** `grep`, `sqlite`, `sort` do not cost tokens
   and do not hallucinate. Filter with tools first, then ask the model.
3. **Stream when possible**, so a slow model does not look like a hang.
4. **Cache repetitive work.** If the same extraction runs often, save the
   output to a file instead of re-running the model.
5. **Persian output**: Persian/RTL text quality varies a lot between models —
   check which of the configured models handles fa-IR best before trusting
   one for user-facing text.

## Configuration

Providers, model groups and the Agent Loop group live under
[Settings → Providers / Model Groups](minis://settings/model-groups). Use
`minis-model-use list` to see what is available, including modality support
(image_output, audio_output). Never write API keys into chat — use
[Set ENV_NAME](minis://settings/environments?create_key=ENV_NAME).
