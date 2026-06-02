---
name: stylize-footage
title: Stylize footage
subtitle: Restyle existing clips with a look reference.
description: Re-style an existing video draft against a look reference.
icon: Palette
category: video
expected_tools:
  - canvas.createVideoNode
  - canvas.attachReferenceNode
  - canvas.updateNodePrompt
default_iteration_cap: 3
requires_inputs:
  - video_node
prompt_template: |
  Stylize the user's selected video / draft ({selected_node_id}) using
  the look they describe. Create a sibling `canvas.createVideoNode`
  whose prompt encodes the new look, attach the original draft as a
  motion reference (`canvas.attachReferenceNode`). If a look-reference
  image node also exists on the canvas, attach it too. Project:
  {project_title}.
---

# Stylize footage — restyle skill

This skill produces *a new draft* alongside the existing one. The
original is the motion source; the new draft holds the styled prompt.

## Flow

1. Create a new draft with `canvas.createVideoNode`, `_alias = "styled"`.
2. Attach the user's original video draft as a reference using
   `canvas.attachReferenceNode` with `video_node_id_alias = "styled"`
   and `image_node_id` = the original's id.
3. If a look-reference image node is on the canvas, attach it with a
   second `canvas.attachReferenceNode` call.
4. Keep the styled prompt purely about *style* (palette, era, medium)
   — motion comes from the source.

## Anti-patterns

- Do not delete the original draft.
- Do not emit `generation.createVideoCandidates`.
