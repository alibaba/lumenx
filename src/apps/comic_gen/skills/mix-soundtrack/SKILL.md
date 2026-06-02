---
name: mix-soundtrack
title: Mix a soundtrack
subtitle: Generate score and SFX layered to your cut.
description: Plan a soundtrack mix layered against the user's current sequence.
icon: Music
category: audio
expected_tools:
  - agent.updatePlan
default_iteration_cap: 3
requires_inputs: []
prompt_template: |
  The user wants a soundtrack (score + SFX) for the project
  {project_title}. Soundtrack tooling is not yet integrated, so emit a
  single `agent.updatePlan` describing the score brief (mood, tempo,
  instrumentation) plus a per-shot SFX list keyed to the current
  sequence. Selected node (optional context): {selected_node_id}.
---

# Mix a soundtrack — audio plan skill

Audio generation is not yet part of the canvas tool set. This skill
is therefore a **plan-only** workflow that captures the soundtrack
brief on a plan node so the user can act on it manually or hand it
to a downstream audio tool.

## Flow

1. Emit `agent.updatePlan` with steps that read like a music
   supervision brief:
   - Score mood + tempo (e.g. "tense, 96bpm, sparse cello")
   - Key SFX moments (impact, room tone, foley)
   - Mix notes (ducking under dialogue, tail fade)

## Anti-patterns

- Do not call `generation.createVideoCandidates`.
- Do not modify any video drafts.
