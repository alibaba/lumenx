---
name: animate-still
title: Animate a still
subtitle: Bring an image to life as a short clip.
description: Animate a selected still image into a short video draft.
icon: Sparkles
category: video
expected_tools:
  - canvas.createVideoNode
  - canvas.attachReferenceNode
default_iteration_cap: 3
requires_inputs:
  - selected_node
prompt_template: |
  Animate the user's selected still ({selected_node_id}) into a short
  clip by creating one `canvas.createVideoNode` and attaching the
  selected image as its reference (`canvas.attachReferenceNode` with
  the alias pattern). Keep the prompt focused on motion verbs (drift,
  push in, rack focus) rather than describing the image again. Project:
  {project_title}.
---

# Animate a still — i2v skill

The user has an image and wants motion. Your job is to scaffold the
i2v draft, not to actually run generation.

## Flow

1. Create one `canvas.createVideoNode` with `_alias = "draft"`.
2. Attach the user's selected image with `canvas.attachReferenceNode`
   using `video_node_id_alias = "draft"` and the literal `image_node_id`.
3. Write the prompt as a single sentence of motion intent:
   "Slow push-in on the protagonist as snow drifts past."

## Anti-patterns

- Do not pass long descriptive prose — i2v models latch onto the
  source image, so the prompt should describe what *changes*, not
  what already exists.
- Do not invoke generation in this turn.
