# LumenX Codex Operating Rules

## Codex Built-In Imagegen Safety

- Treat Codex built-in image generation as a conversation-level request to `api-vip.codex-for.me/v1/responses`.
- Never attach or view original multi-reference storyboard/material images directly when using Codex built-in imagegen.
- Before any Codex built-in imagegen handoff, run `scripts/prepare_codex_imagegen_refs.py` and use only the generated safe reference images.
- Prefer handoff packages under `output/codex_imagegen_handoff/<project-or-frame>/`.
- The handoff package must include:
  - `codex_safe_reference_manifest.json`
  - safe reference images
  - `codex_imagegen_prompt.md`
  - `handoff_policy.json`
- High-consistency frames may use `two_stage_high_consistency`; generate it as a separate pack from the direct safe pack.
- In `two_stage_high_consistency`, stage 1 locks characters and key props; stage 2 attaches the stage 1 result, then refines scene, composition, and lighting with safe refs.
- The prompt shown to Codex should reference the safe reference pack and must not list raw source image paths.
- `codex_safe_reference_manifest.json` must expose prepared safe reference paths only; raw source paths are fixture/script input, not Codex handoff content.
- Keep the total prepared safe reference bytes conservative. Hard cap: no more than `1 MiB` of prepared JPEG refs per Codex handoff stage; within that cap, prefer the highest-fidelity safe refs that still fit. For heavy storyboard work, prefer `700 KiB` or lower only if the quality-first fit still leaves enough visual detail.
- If the safe pack cannot fit the budget, split the frame into staged handoffs instead of raising the budget.
- For `六一那天_v2` and similar multi-reference storyboard work, always use `safe_refs_only` or `two_stage_high_consistency`; use `off` only for a local-only experiment.

## Why This Exists

Codex built-in imagegen can fail with `413 Payload Too Large` when the aggregate conversation payload is too large. The limit is affected by the full request body, not just image count. A frame with six references can fail if the source images are large, even when each image is individually valid.
