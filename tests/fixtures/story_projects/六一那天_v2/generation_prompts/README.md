# generation_prompts

这里放 v2 的 18 镜分镜 prompt 包和门禁文件草案。

计划文件见 [docs/storyboard-rebuild-plan.md](../../../../../docs/storyboard-rebuild-plan.md)。
资产草案见 [project_manifest.draft.json](../project_manifest.draft.json) 和 [references/ASSET_INVENTORY.md](../references/ASSET_INVENTORY.md)。
正式版最终设计见 [docs/storyboard-consistency-final-design.md](../../../../../docs/storyboard-consistency-final-design.md)。

当前还不放 `project_manifest.json`，避免它被当成正式 fixture discovery。

## Codex imagegen 引用预算

不要把 v2 原始 PNG 资产直接作为 Codex imagegen 多图引用发送。`frame_17` 的 6 张真实引用虽然没有主板、没有 three view / expression sheet / head shot，但原始总量仍达到 10,591,345 bytes，base64/JSON 估算约 14,121,794 bytes，足以触发 `/v1/responses` 网关 413。

先运行 frame 级 handoff：

```powershell
python scripts\prepare_codex_imagegen_refs.py --frame-spec tests\fixtures\story_projects\六一那天_v2\generation_prompts\frame_17\codex_imagegen_handoff_manifest.json
```

然后只使用 `output/codex_imagegen_handoff/liuyi-that-day-v2/liuyi_frame_17/` 里的 `codex_safe_reference_manifest.json`、`codex_imagegen_prompt.md` 和 safe refs。公开 handoff manifest 只暴露 safe refs，不暴露原始 PNG 路径。原则是总请求体预算优先于引用数量，但在预算内要优先保真而不是盲目压小；高一致性镜头可以切到 `two_stage_high_consistency` 独立 pack。任何多参考图 Codex imagegen handoff 都必须先生成 safe refs，再进入真实请求。

长期回归固定两组真实 fixture smoke：

```powershell
python -m pytest tests/test_image_payload_budget.py -k "frame17_handoff or two_stage_extreme_fixtures or recommendation_and_selection"
```

- `frame_17`：6 张真实引用，默认固化为安全直连 handoff，同时后端推荐会提示两段式可选。
- `frame_18`：8 张真实引用，默认固化为高一致性两段式 handoff。

上游 Codex 内置 imagegen 渠道恢复后，按对应 handoff 目录里的 `codex_imagegen_prompt.md` / `codex_two_stage_handoff_prompt.md` 执行，并把产物放入 `output/codex_image_audit/liuyi-that-day-v2/generated/` 进行直连与两段式对比。
