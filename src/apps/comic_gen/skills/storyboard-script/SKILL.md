---
name: storyboard-script
title: Storyboard a script
subtitle: Turn a script into shot-by-shot panels.
description: Convert a script into a shot-by-shot storyboard on the canvas.
icon: ScrollText
category: storyboard
expected_tools:
  - agent.updatePlan
  - canvas.createVideoNode
  - canvas.createRegion
  - canvas.attachToRegion
default_iteration_cap: 5
requires_inputs: []
prompt_template: |
  Read the user's script (or the prompt of the selected node when no
  script is provided) and break it into a shot list. Emit one
  `agent.updatePlan` summarising the breakdown (one step per shot),
  then create a `canvas.createVideoNode` for each shot with a focused
  `prompt`. Group all shots under a single `canvas.createRegion` titled
  after the scene. Project: {project_title}; selection: {selected_node_id}.
---

# Storyboard a script — shot list skill

When the user wants a script turned into panels, you are translating
prose into camera-and-subject directives.

## Flow

1. Emit `agent.updatePlan` listing each shot as a short step ("Shot 1
   — INT NIGHT — Maya enters", etc).
2. Open a `canvas.createRegion` titled after the scene heading.
3. For each shot, fire `canvas.createVideoNode`:
   - `title` = shot slug ("S01 — wide of alley")
   - `prompt` = shot description that reads cleanly to a downstream
     prompt-extender (subject, action, lens, mood)
4. Attach every draft to the region with `canvas.attachToRegion`.

## Anti-patterns

- Do not generate more than 8 shots per turn — overflow into a follow
  up turn so the user can review the first batch.
- Avoid copy-pasting full script paragraphs into prompts; distill to
  one camera-friendly sentence.
