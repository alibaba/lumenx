# Wan 2.7 T2V 共同执行契约核对

核对日期：2026-10-13。仅公开官方资料；未调用真实供应商。repo-only 文档模式，外部原始文档库与 Context Hub 未同步。

来源：[官方 API](https://help.aliyun.com/en/model-studio/text-to-video-api-reference)、[模型列表](https://help.aliyun.com/en/model-studio/video-generate-edit-model)。现有 alias `wan2.7-t2v` 仍有效，不改模型身份或可见性。下面是研究结果转述，不是付费验收。

- POST video-synthesis + X-DashScope-Async，返回 task_id；GET tasks/{task_id} 查原任务。
- prompt 必填，最长 5000 字符；negative_prompt 可选、最长 500；产品拒绝超限而非依赖供应商静默截断。
- resolution 支持 720P/1080P（默认1080P），ratio 为16:9/9:16/1:1/4:3/3:4（默认16:9），duration整数2–15（默认5）。
- prompt_extend 默认true，watermark默认false；seed范围0..2147483647，省略为随机。LumenX通用-1随机标记须在发送前省略，不能发-1；0必须保留。
- 可选audio_url为WAV/MP3、2–30s、≤15MB；不传时模型生成声音。此切片现有T2V入口无音频字段，不新增上传音频控制。
- shot_type无实际作用，不把它引入正式持久契约。任务ID与结果URL各有24h时效，完成后需取回本地。

当前 catalog 的 T2V mode 只有身份，无参数契约。补齐上述已知参数是让同一个 normalize_generation_intent 能冻结用户输入的前置；不改变任何其他 mode、默认模型或产品可见性。异步持久执行用现有 ProviderSubmission/Handle/ExecutionRecord，不新增任务库。
