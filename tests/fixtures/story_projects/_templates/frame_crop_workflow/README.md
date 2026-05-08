# Frame Crop Workflow 模板

这个目录是新增中文 fixture frame 时的复制模板，不会被 `list_fixture_story_projects()` 当成正式 fixture 项目，因为这里没有 `project_manifest.json`。

## 固定入口

- 正式编辑统一从 `scripts/run_fixture_frame_script.ps1` 进入。
- 只做合回或 bbox 检测统一从 `scripts/compose_fixture_frame_crops.ps1` 进入。
- 中文目录下的 `run_formal_crop_edits.ps1` 只负责根据自身位置推导目录、组织 crop edit 参数，并在最后调用 compose wrapper。
- `crop_composition_manifest.json` 必须同时声明 Stage2 变化门禁和 Stage3 compose 回写门禁，不能只靠“文件存在”验收。

## 新增 frame 步骤

1. 直接复制本目录作为新 frame 的起点，不再从 `frame_15` 反向分叉。
2. 把 `crop_composition_manifest.template.json` 改名为 `crop_composition_manifest.json`。
3. 把 `run_formal_crop_edits.template.ps1` 改名为 `run_formal_crop_edits.ps1`。
4. 只替换 manifest 里的 bbox、输入 crop、输出 crop、最终输出名、prompt 和参考图路径。
5. 在正式脚本里只替换 `$ProjectSlug`、`$FrameId`、crop 文件名、prompt 文件名、参考图文件名和目标尺寸。
6. 给每个 crop 补 `visual_gate`，至少约束 Stage2 相对 base crop 的像素变化，以及 Stage3 与 edited crop 的 bbox 回写一致。

## 推荐命令

正式编辑：

```powershell
powershell -File scripts\run_fixture_frame_script.ps1 `
  -ProjectSlug liuyi-that-day `
  -FrameId liuyi_frame_15 `
  -ScriptName run_formal_crop_edits.ps1
```

只检测 bbox：

```powershell
powershell -File scripts\compose_fixture_frame_crops.ps1 `
  -ProjectSlug liuyi-that-day `
  -FrameId liuyi_frame_15 `
  -DetectOnly
```

只合回：

```powershell
powershell -File scripts\compose_fixture_frame_crops.ps1 `
  -ProjectSlug liuyi-that-day `
  -FrameId liuyi_frame_15
```
