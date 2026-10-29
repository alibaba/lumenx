# Wan 2.7 首帧恢复入口核验

采集日期：2026-10-11。适用：DashScope / Wan / wan2.7-i2v；官方模型还列出快照 wan2.7-i2v-2026-04-25。本次保持当前目录别名，不改用户选择。

来源：[API](https://help.aliyun.com/zh/model-studio/image-to-video-general-api-reference)、[模型信息](https://help.aliyun.com/zh/model-studio/wan2-7-i2v)。

集成事实：首帧放入 input.media（type=first_frame）；接受 data URI，图像 ≤20 MB。prompt ≤5000 字符，negative_prompt ≤500。duration 为 2–15 整数，resolution 为 720P/1080P，seed 为 0–2147483647；prompt_extend 与 watermark 为布尔。该协议没有 ratio、shot_type 或 audio 布尔开关。无 driving_audio 时模型可自动配背景声。模型、endpoint、key 必须同地域；旧 DashScope 域名仍受支持。task_id 与结果 URL 各有效 24 小时。

LumenX 本片只接入首帧，保留固定参数/媒体/身份及一次提交、后续查询下载。首尾帧、驱动音频、续写不在此片。此文件为仓库 staging 摘要；外部原始档案和 Context Hub 尚未同步，不代表跨仓文档更新完成。
