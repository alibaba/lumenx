# frame_17 Codex Imagegen Handoff

这个目录固定 `六一那天_v2` 第 17 镜的 Codex 内置生图 handoff 回归样例。

不要把 6 张原始 PNG 参考图直接加载进 Codex 对话。先运行：

```powershell
python scripts\prepare_codex_imagegen_refs.py --frame-spec tests\fixtures\story_projects\六一那天_v2\generation_prompts\frame_17\codex_imagegen_handoff_manifest.json
```

然后只加载输出目录里的 safe refs 和 `codex_imagegen_prompt.md`。`codex_safe_reference_manifest.json` 不暴露原始 PNG 路径，避免后续 Codex 对话误把原图当引用重新加载。当前 safe refs 生成策略是预算内优先保真，不是盲目压缩到最小体积。
