---
name: compose-short-film
title: Compose a short film
subtitle: Plan, board, and cut a full piece with the director agent.
description: Plan, board, and cut a full short film with the director agent.
icon: Clapperboard
category: compose
expected_tools:
  - agent.updatePlan
  - canvas.createRegion
  - canvas.createVideoNode
  - canvas.attachReferenceNode
  - canvas.attachToRegion
default_iteration_cap: 6
requires_inputs: []
prompt_template: |
  Compose a complete short film on the Atelier canvas. Begin by emitting
  one `agent.updatePlan` call that captures a 3-5 step plan (Setup → Turn →
  Payoff at minimum, plus any preparation / finishing steps you need). For
  every shot the plan will exercise, create a `canvas.createVideoNode`
  with a clear title and prompt, and group related shots inside a
  `canvas.createRegion` board. Project context: {project_title}; current
  selection: {selected_node_id}.
---

# Compose a short film — director skill

Use this when the user asks for a complete short piece (anything from a
1-shot stinger up to a 6-shot beat sheet). Your responsibility is to
turn a vague brief into a concrete plan + the canvas scaffolding the
user can iterate on.

## Recommended flow

1. **Plan first.** Emit `agent.updatePlan` once at the top of the turn
   with a tight beat-by-beat outline (Setup → Turn → Payoff is the
   default; expand to 4-6 steps when the brief justifies it).
2. **Group with a region.** When the plan has more than one shot, open
   a `canvas.createRegion` board and attach each draft to it so the
   user can move the whole sequence as a unit.
3. **One draft per beat.** For each beat in the plan, fire a
   `canvas.createVideoNode` whose `prompt` reads as a real shot
   description (camera, subject, action, mood) — not the beat label.
4. **Refs are optional.** Only attach reference images when the user
   has made one available; do not hallucinate URLs.

## Anti-patterns

- Do NOT skip the plan node. The plan is the user-visible spine of
  the turn.
- Do NOT generate candidates inside the same turn — generation costs
  money and should be a deliberate follow-up under user approval.
- Avoid creating more than 6 drafts in one turn; it overflows the
  default policy `max_nodes_per_action` budget.
