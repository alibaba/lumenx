# F22 供应商接入证据

核对日期：2026-10-13。本文是 Playground 工作卡的外部契约依据，不记录实施进度；当前状态只见 EXECUTION-STATUS。研究仅只读官方公开资料，没有读取凭据或付费调用。参数范围在落码前仍须逐字段核对原文。

## 迁移前仓库基线（历史）

以下为调研时的历史基线；当前迁移进度只见 [执行台账](EXECUTION-STATUS.md)。当时 Core 正式 admission 覆盖 Wan 2.6/2.7 I2V 和 Wan 3；Wan 2.7 T2V/R2V/videoedit 与 Seedance 视频仍走旧 generate 包装。Playground 包含 hidden 目录项，不能因 Studio 不展示就视为无用户入口。

- Wan 旧 T2V/videoedit 丢 seed=0、固定 1080P；R2V 的 reference_image_urls 与当前协议不一致。
- Seedance 旧 POST 对连接错误和部分 5xx 自动重试，不能直接用于新付费 submit 边界；R2V 旧调用还可能重复第一张图。
- provider_binding 已有 MuleRouter HTTP 的精确 scope/credential 绑定；provider_recovery 的校验和 runtime 恢复仍是 Wan 专用。复用中性 record/handle，不复制任务库。

## Wan 与 HappyHorse：官方资料事实

共同异步入口为 POST video-synthesis 并启用 X-DashScope-Async，随后按 task_id 查询。Wan 的任务和结果下载地址有时效，必须及时取回本地，不能视远端 URL 为永久素材。

- [Wan T2V](https://help.aliyun.com/en/model-studio/text-to-video-api-reference)：wan2.7-t2v 使用 prompt，可附 negative_prompt/audio_url；parameters 包括 resolution、ratio、duration、prompt_extend、watermark、seed。seed=0 有效，不应按 truthy 判定遗漏；shot_type 不作为本片参数。
- [Wan R2V](https://help.aliyun.com/en/model-studio/wan-video-to-video-api-reference)：Wan 2.7 使用 input.media，角色包括 reference_image/reference_video/first_frame；支持 reference_voice。不能继承旧 reference_image_urls 请求形状；必须先把当前产品可选输入映射到已证实角色。
- [Wan VideoEdit](https://help.aliyun.com/en/model-studio/wan-video-editing-api-reference)：wan2.7-videoedit 需要一段 video，可加 0–4 张 reference_image。duration 缺省/0 保持输入时长，显式截取范围 2–10；不与普通生成的时长默认值混用。audio_setting 须依其独立契约校验。
- [视频模型列表](https://help.aliyun.com/en/model-studio/video-generate-edit-model)：HappyHorse 1.1 T2V/I2V/R2V 已有效；video edit 仍是 happyhorse-1.0-video-edit。
- [HappyHorse T2V](https://help.aliyun.com/en/model-studio/happyhorse-text-to-video-api-reference)、[I2V](https://help.aliyun.com/en/model-studio/happyhorse-image-to-video-api-reference)、[R2V](https://help.aliyun.com/en/model-studio/happyhorse-reference-to-video-api-reference)、[编辑](https://help.aliyun.com/en/model-studio/happyhorse-video-edit-api-reference)：I2V 用 first_frame 且不发送 ratio；R2V 用 1–9 张 reference_image；按各模型独立允许字段校验。

有版本歧义：R2V API 同时提到 alias 和 2026-06-12 snapshot，模型列表只列 snapshot；HappyHorse T2V 有一处旧 1.0 文案。实施时必须把显式目录身份和供应商 ID 的映射写入测试，不能猜测或静默改名。

## MuleRouter Seedance：官方上游事实与未证实项

证据来自 [OpenMule CLI](https://github.com/openmule/mulerouter-cli)、[官方 Skill 文档](https://raw.githubusercontent.com/openmule/mulerouter-skills/main/skills/mulerouter-skills/SKILL.md)、[CLI Reference](https://raw.githubusercontent.com/openmule/mulerouter-skills/main/skills/mulerouter-skills/references/REFERENCE.md)、[模型表](https://raw.githubusercontent.com/openmule/mulerouter-skills/main/skills/mulerouter-skills/references/MODELS.md)，以及其发布的 mulerouter/@mulerouter/core 0.5.0 注册表源码。发布包源码证据不同于已运行验收。

六个路由：`/vendors/bytedance/v1/seedance-2.0[-fast]/{text,image,reference}-to-video/generation`；提交取得 task_info.id，查询 GET 原 api_path/task_id，结果 key 为 videos。标准与 fast 的参数集合须分开。

wrapper 字段为 aspect_ratio；R2V 当前文档为 images/videos/audios，而非旧 reference_images。registry 接受 seed=-1，但其含义未明确，不能擅自省略。时长支持 -1 或 4–15；标准分辨率 480p/720p/1080p，fast 为 480p/720p。具体媒体限制、各模式 watermark 差异要以该模式注册表逐项建立 wire 测试。

公开新 CLI 支持 `mulerouter run --no-wait --json` 返回 task_id/api_path，之后 `status` 查询。它不是本项目的旧 `mulerun studio run` token/profile 路径；后者没有找到可证明持久 submit/query/fetch 的公开契约，不能混称已支持。

没有查到服务端幂等键、task retention、结果 URL 有效期的官方保证。LumenX 自己持久保存 request identity 与 returned handle；丢提交响应不能自动再 POST。上游 BytePlus/Volcengine 的 ratio 字段不能代替 MuleRouter wrapper 的 aspect_ratio。

## 实施约束

先完善同一产品存储的接纳/回执/失败投影，再补精确 provider 合同。每条新执行通道须验证实际 adapter + fake HTTP 的 submit/query/fetch、原凭据范围、零/false/-1、错误输入付费前拒绝、lost ACK 不重提、受管发布和重启恢复。真实供应商与 ECS 另行验收。
