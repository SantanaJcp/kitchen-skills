---
name: unslop
description: "Cut AI writing tells and filler from prose, docs, READMEs, skills, and PR descriptions without losing meaning. Use when polishing prose or when I type /unslop or $unslop."
disable-model-invocation: true
---

# unslop

Edit text to remove AI patterns while preserving meaning and intended tone. Scan for the patterns below, then rewrite cleanly.

## Patterns to cut

- **Filler and openers:** delete chatbot phrases ("I hope this helps!", "Let me know if..."), praise or agreement openers, generic conclusions, and wordy connectors ("in order to" -> "to", "it is important to note that").
- **AI vocabulary:** cut words like *additionally, crucial, delve, enhance, foster, landscape, pivotal, showcase, testament, underscore, vibrant*. Use plain verbs: "is" or "has" instead of "serves as", "stands as", "boasts", "features"; "use" instead of "utilize" or "leverage".
- **Abstract metaphors:** replace *substrate, wedge, vector, nexus, primitive, harness, surface, scaffolding, paradigm, ratchet, north star, flywheel*, aphorisms, and personified code with the concrete word.
- **Empty syntax:** cut trailing "-ing" phrases that add no fact ("highlighting...", "ensuring..."), "not just X, but Y", forced lists of three, vague attributions ("experts believe"), and fake "from X to Y" scales.
- **Punctuation and formatting:** no em dashes, en dashes, or hyphens used as dashes (and no parentheses replacing them); use a period or comma. Use a colon before a list or example, never mid-sentence as a connector. Use straight quotes, sentence-case headings, no decorative emojis, and no bold label that restates its own line.
- **Clarity and precision:** one idea per sentence, active voice, one consistent name per concept, whole sentences with articles and verbs (spell out arrows in prose). Name the mechanism or number; cut any sentence that could appear unchanged in another project's docs. Keep at most one hedge ("may").

Principles (the kitchen's PRINCIPLES.md): `small-owned`, `owner-attention`.
