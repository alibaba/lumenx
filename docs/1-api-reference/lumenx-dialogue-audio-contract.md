# 对白音频：选择、成功快照与只读核验

接口核对：2026-10-10。适用于本轮 Core foundation 分支；实施/验收进度只见[执行台账](../agents/context/lumenx-first-principles/EXECUTION-STATUS.md)。实际传输能力继续由 [TTS 参数依据](lumenx-tts-parameters-2026-10-09.md)和 `TTSProcessor.prepare` 定义，客户端不可另写一套模型/钳制规则。

## 写入

`POST /projects/{id}/frames/{frame_id}/audio` 接受 `speed`、`pitch`、`volume`、`instructions` 的 patch，返回 raw Script。

| 请求值 | 含义 |
|---|---|
| 字段省略 / `{}` | 保留已保存的覆盖；没有覆盖则继承当前角色数值或结构化对白指令 |
| `null` | 清除该字段覆盖，恢复继承 |
| 数字（包括 volume=0） | 明确覆盖；不支持该覆盖的实际音色/模型在合成前422 |
| `instructions: ""` | 明确关闭指令，不当成省略或null |

desired 记录在 `frame.dialogue_audio_options`；只有成功生成才替换 `dialogue_audio_snapshot`，包括该文件引用、选择来源、解析请求和实际 prepared 参数。普通供应商失败保存验证过的选择与失败状态，旧成功快照/文件保留。前置422、版本冲突、存储失败不发布新选择。旧 `dialogue_instructions` 是混合历史，不能自动推断为用户覆盖。

`POST /projects/{id}/dialogue_audio/batch` 返回 raw Script 加 `_batch_stats`（generated/skipped/failed/no_voice）和 `_batch_errors`。批量跳过仅依赖当前准备结果与成功快照实际参数等价；数值被钳制为相同结果或未支持的继承参数改变，不应触发重复生成。显式单句生成属于用户新请求，仍会执行。不支持的已保存覆盖记录该帧错误并继续其他有效帧；存储失效、目标冲突等停止后续操作。

前端必须区分 raw 命令回执与合并了共享资产的 GET；用既有确认辅助函数最多读一次，不从错误或未确认反馈自动重发付费请求。

## 只读核验

`GET /projects/{id}/frames/{frame_id}/audio/state` 为 `Cache-Control: no-store` 的独立读取，不调用合成/下载，也不迁移或保存数据。

返回 `project_id`、`frame_id`、同捕获 `read_versions`、`audio_url`、`audio_error`、实际解析的 `dialogue`/`voice_id`、`options`、`resolved_request`、`sources`、`prepared`、`freshness`、`reason`、`error`，以及只供用户显式沿用的 `historical_instructions`。只允许 `audio_url` 转为 OSS 显示签名，所有输入文本必须逐字保留。签名响应可带 `media_refs.audio_url` 原始引用，遵循[媒体响应契约](lumenx-media-response-contract.md)。

| freshness | 含义 |
|---|---|
| current | 本次读取中，成功音频快照和当前实际提交参数匹配 |
| stale | 参数已改变或最近生成失败；不删除旧成果 |
| missing | 没有生成音频引用 |
| unknown | 缺输入、服务不可用、不支持覆盖、旧/未知版本快照、媒体引用不匹配等，不能证明一致 |

不支持覆盖时 `error` 使用 `{code: unsupported_audio_parameters, fields, family, model}`。缺项目/帧404；存储错误保留相应错误响应，不伪装 unknown 成功。未知快照版本作为不透明记录保留，损坏的已知 v1 不得通过未知分支逃过校验。

界面以 `options` 初始化选择，以安全的 `historical_instructions` 提供显式沿用；不能从历史生成指令初始化新覆盖。读取失败显示尚未核验并提供只读刷新。打开工作台、已观察的输入变化和生成后重新核验，迟到/跨项目/跨帧响应不更新当前工作台。

`read_versions` 不覆盖存放在另一个系列中的 custom voice。接口每次读取会重新解析该依赖，但不提供跨设备持续订阅保证。current 只证明本地提交参数等价，不证明文件仍存在、内容身份、供应商实际表演质量或 P6 持久任务回执。

客户端记录已接受读取中的共享来源版本下限；同一弹窗也记录项目读取版本下限，较旧响应不能将核验状态回退。读取核验状态不会用局部字段替换正式项目。批量/单句/配音命令共用当前环境和项目的执行标记；关窗重开仍等待正在执行的操作，结束后只读刷新。未保存的对白先保存成功，再允许生成；读回失败给出未确认反馈，不自动付费重试。

## 旧入口兼容边界

`POST /projects/{id}/generate_audio` 只委托现有 `dialogue_audio/batch` 并返回提交后的 Script，不隐式生成 SFX/BGM。需要 generated/skipped/failed/no_voice 统计和错误明细的客户端使用正式 batch 入口。项目缺失为 404，输入上下文校验为 422，存储错误保持现有统一错误处理。

`POST .../mix/generate_sfx` 与 `POST .../mix/generate_bgm` 返回 501；此前只会写不可播放占位字节，不属于已交付的生成能力。它们不能触发对白或供应商调用。脚本级 BGM 预设、`audio_mix` 配置和已有混音路径继续保留，真实音轨效果另验。

同样未实现的脚本级 `POST .../generate_video` 对存在项目返回 501 并指向 `/projects/{id}/video_tasks`；不存在项目为 404。当前分镜界面的任务式视频生成不走这个旧入口。

## 配音预览、采纳与撤销

- `POST /projects/{id}/frames/{frame_id}/dub/preview` 接受 `video_task_id` 与非负整数 `offset_ms`。任务必须属于当前分镜、已完成并有视频。锁外处理媒体；短锁重新核对项目与取材后提交唯一预览文件引用。
- `POST .../dub/apply` 只采纳仍匹配当前有效选中任务、原视频和当前对白音频的预览，并要求预览文件存在；不暗中切换所选候选。
- `POST .../dub/revert` 清除预览/采纳记录和兼容字段，保留原视频、对白音频及背景缓存；本片不删除媒体字节。

以上返回 raw Script 命令回执，客户端走同一有限读回协议，不直接把局部 frames 覆盖进共享读视图。

`dub_preview_source` 与 `dub_applied_source` 的已知 v1 形状为 `{schema_version: 1, task_id, video_url, audio_url, offset_ms}`。ID/引用为非空字符串，offset 为非负整数；已知版本不接受额外字段。未知版本作为历史保留，不认为已核验。引用保持原样，显示签名不能改写来源。

预览偏移只记录在预览来源中；`dub_offset_ms` 是已采纳偏移的兼容映射。修改工作台偏移后，必须先生成对应预览才能采纳。当前分镜画面、候选与成片只使用来源匹配的已采纳配音；预览只在明确预听入口使用。显式选择失效或指向别的分镜时，不能默默替换成第一个候选。没有可核验来源的旧配音保留为历史，不能宣称当前有效。

复制分镜时，已完成的视频候选可复制元数据并生成新的 task/frame 归属，复用已有媒体文件；匹配的选择、最终候选及 v1 配音来源随新 ID 映射。运行中任务与供应商执行权不随副本复制。未知/失配来源不会因复制被提升为有效记录。

以上来源匹配证明的是引用关系，并不证明相同路径下的字节未变化；固定内容版本和远程媒体处理继续由 P3/P6 验收。

实际回归入口：`tests/test_dialogue_audio_parameters.py`、`test_dialogue_audio_state.py`、`test_dialogue_edit_commands.py`、`test_audio_generation_commands.py`、`test_dub_source_provenance.py`、`test_frame_copy_media.py`、`test_frame_video_selection.py`，均经隔离启动器。前端实际组件和结果由[测试矩阵](../agents/context/lumenx-first-principles/TEST-MATRIX.md)及执行台账定位，不从后端通过推断。
