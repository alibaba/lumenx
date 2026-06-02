---
name: try-workflow
title: Try a workflow
subtitle: Browse saved templates and recipes to start from.
description: Suggest a workflow template that matches the user's intent.
icon: Workflow
category: workflow
expected_tools:
  - agent.updatePlan
default_iteration_cap: 2
requires_inputs: []
prompt_template: |
  Help the user pick a starting workflow template. Read the user's
  message and emit one `agent.updatePlan` recommending 1-3 templates
  (motion study, character ref → video, 3-shot story, etc) with a
  one-line rationale per template. Project: {project_title}.
---

# Try a workflow — recipe picker skill

This skill is the gentle-on-ramp variant of the canvas — instead of
committing to drafts, it surfaces recommended structures the user can
opt into.

## Flow

1. Emit a single `agent.updatePlan` with 1-3 step entries, each one a
   workflow template name + one-line rationale ("3-shot story — best
   for the brief above because it gives a clear arc").

## Anti-patterns

- Do not create draft / image / region nodes.
- Do not invoke generation.
