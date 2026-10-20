# Elicitation Skill — Intent Compiler for AI Agents

English | [简体中文](README.zh-CN.md)

**Elicitation Skill** is an AgentSkill that helps an AI assistant turn vague human intent into actionable definitions.

It is not a meeting-notes bot, a questionnaire, or a generic brainstorming prompt. It teaches an agent to ask a few sharp questions first, uncover the hidden judgment behind a fuzzy idea, and then compile the answer into an **Intent Brief** that can change a product, workflow, skill, memory rule, or agent behavior.

## Why this exists

Most AI agents are good at executing once the task is clear. The hard part is often before execution:

- The human has a strong but blurry intuition.
- The product direction is not yet explicit.
- The team feels tension but cannot name it.
- The agent wants to act too early and produces a generic plan.

Elicitation Skill creates a lightweight protocol for this pre-execution layer.

> Ask first. Synthesize after. Route into system change.

## What it does

Given a fuzzy prompt such as:

- “Help me clarify this product idea.”
- “Let’s do an elicitation session.”
- “What agent/workflow are we missing?”
- “This direction feels right, but I can’t explain why yet.”
- “Turn this vague thought into something executable.”

The agent will:

1. Identify the elicitation type.
2. Ask 3–5 concise, high-leverage questions.
3. Wait for the human’s answers.
4. Produce an Intent Brief.
5. Suggest 1–3 concrete system feedback actions.

## When to use it

Use it before execution when the task is still under-defined but high-stakes:

- Product strategy
- Agent / workflow design
- Creative direction
- Brand or taste alignment
- Weekly planning
- Postmortem synthesis
- Strategic tension clarification

Do **not** use it for routine Q&A or simple tasks that are already clear.

## Repository structure

```text
.
├── README.md
├── README.zh-CN.md
├── skill/
│   └── elicitation-agent/
│       ├── SKILL.md
│       └── references/
│           └── question-patterns.md
├── templates/
│   └── intent-brief.md
├── examples/
│   └── website-product-intent-brief.md
├── docs/
│   └── product-note.md
└── AGENTS.md
```

## Installation

Copy the skill folder into your OpenClaw / Codex / agent skills directory:

```bash
cp -R skill/elicitation-agent ~/.openclaw/workspace/skills/
```

Then restart or reload your agent environment if required.

## Quick start

Ask your agent:

```text
Let’s do an elicitation session for this vague product idea:
I think our website should feel less like a corporate landing page and more like a world people can enter.
```

The agent should **not** immediately write a sitemap. It should ask a few clarifying questions first.

## Core output: Intent Brief

The final artifact should distinguish:

- Explicit goal
- Hidden tension
- Agent judgment
- Executable translation
- Feedback target
- Verification method
- What not to do

See [`templates/intent-brief.md`](templates/intent-brief.md).

## Design principles

- Fewer, sharper questions beat long forms.
- Do not summarize too early.
- Do not turn the human into the project manager.
- Do not attribute the human’s insights to the agent.
- Every session must route into at least one concrete change.
- If nothing changes, the elicitation is incomplete.

## License

MIT. See [`LICENSE`](LICENSE).
