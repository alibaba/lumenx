---
name: generate-hero-shot
title: Generate hero shot
subtitle: Make a single keyframe from a prompt or reference.
description: Plan a single keyframe video draft from a prompt or reference image.
icon: ImagePlus
category: image
expected_tools:
  - canvas.createVideoNode
  - canvas.attachReferenceNode
default_iteration_cap: 3
requires_inputs: []
prompt_template: |
  Create exactly one polished hero-shot video draft. If the user
  selected a reference image node ({selected_node_id}), attach it to
  the new draft with `canvas.attachReferenceNode`. Otherwise, leave
  references empty and rely on the prompt alone. Project:
  {project_title}.
---

# Generate hero shot — single-keyframe skill

The output of this skill is *one* draft node, polished and ready for
candidate generation in a follow-up turn.

## Flow

1. Fire a single `canvas.createVideoNode` with:
   - `title` = a concise label (4-6 words)
   - `prompt` = the user's request rewritten as a one-sentence shot
     description with subject + lens + mood
2. If the user has a reference image selected, attach it via
   `canvas.attachReferenceNode` using the alias pattern (`_alias` on the
   create call, `*_alias` on the attach call) so the harness binds the
   freshly-created draft id to the alias automatically.

## Anti-patterns

- Do not create more than one draft.
- Do not call `generation.createVideoCandidates` here — that's a
  separate, approval-gated action.
