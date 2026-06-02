---
name: replace-voice
title: Replace a voice
subtitle: Swap dialogue with a new voice and keep timing.
description: Plan a voice replacement turn for a selected video node.
icon: Mic2
category: audio
expected_tools:
  - agent.updatePlan
  - canvas.updateNodePrompt
default_iteration_cap: 3
requires_inputs:
  - selected_node
prompt_template: |
  The user wants to replace the voice on {selected_node_id} while
  keeping timing. Audio tooling is not yet on this canvas, so emit a
  single `agent.updatePlan` describing the steps the user should take
  next (export current audio, generate replacement TTS, mux back) and
  optionally `canvas.updateNodePrompt` to capture the desired voice
  direction on the existing node. Project: {project_title}.
---

# Replace a voice — audio skill

The Atelier audio toolchain is not yet integrated into the agent
canvas. This skill is therefore a **planning-only** workflow: produce a
plan node the user can follow manually.

## Flow

1. Emit `agent.updatePlan` with 3-4 concrete steps:
   - Export the current dialogue audio
   - Generate a replacement track with the target voice
   - Re-align timing if needed
   - Re-mux back into the video draft
2. Optionally call `canvas.updateNodePrompt` to add a `voice_direction`
   note onto the existing video node's prompt (do NOT overwrite the
   visual prompt).

## Anti-patterns

- Do not invoke any `generation.*` tool — audio is out of scope.
- Do not create new draft video nodes.
