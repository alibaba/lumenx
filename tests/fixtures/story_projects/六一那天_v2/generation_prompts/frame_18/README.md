# frame_18 Two-Stage Handoff

这个样例偏多参考大场景：多场景锚点 + 多角色 + 关键道具。

先运行：

```powershell
python scripts\prepare_codex_imagegen_refs.py --frame-spec tests\fixtures\story_projects\六一那天_v2\generation_prompts\frame_18\codex_imagegen_handoff_manifest.json
```

stage 1 只锁人物与关键道具，stage 2 再接入 stage 1 结果并处理场景、构图与光影。
