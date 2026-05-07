# 问题清单

说明：本文是修复前的审计快照；其中第 1、5、6、7 项已在当前分支修复，当前状态见 `docs/audit/2026-05-07/fix-report.md`。

下面按“阻断优先级 -> 维护风险”排序。

## 1. 启动入口和端口没有单一真相
**证据**
- `README.md:137-156` 写的是 `8000/3000`。
- `scripts/start-backend.js:18` 用 `17177`。
- `start_backend.sh:11,19`、`Dockerfile.backend:26,28`、`frontend/src/lib/api.ts:7,16-17,26`、`frontend/next.config.mjs:5` 用 `18177`。
- `main.py:88,114` 又回到 `17177`。
- `docker/nginx.conf:15-64` 反代到 `backend:17177`。

**影响**
- `npm run dev`、Docker、桌面壳、README 之间互相打架。
- 这是“能不能连上后端”的基础问题，不是小瑕疵。

**方案**
- 把端口收敛成一个环境变量，例如 `LUMENX_API_PORT`。
- 所有脚本、README、nginx、前端默认值都从它读取。
- 如果要保留多入口，至少给每个入口明确写出适用场景，不要混用。

## 2. 后端暴露面过大，且没有真正的鉴权边界
**证据**
- `src/apps/comic_gen/api.py:99-102` 开了 `allow_origins=["*"]`、`allow_credentials=True`。
- `src/apps/comic_gen/api.py:122-127` 直接把整个 `output/` 挂到 `/files`。
- `start_backend.sh:19`、`Dockerfile.backend:28` 都是 `0.0.0.0`。

**影响**
- 任意网页都可能驱动本机/局域网里的后端做写入、删除、导出、配置保存。
- `/files` 还会暴露项目输出、项目元数据和生成产物，数据面太宽。

**方案**
- 默认只监听 `127.0.0.1`，Docker/远程场景再显式放开。
- 收紧 CORS 到明确前端 origin。
- 给 mutating API 加最小鉴权或本地 CSRF 防护。
- `/files` 只暴露必要的媒体子目录，不要整棵 `output/` 都公开。

## 3. 开发态配置写回项目根目录，容易污染工作区
**证据**
- `src/apps/comic_gen/api.py:919-984,1078` 里，开发态会把配置写回 `_get_project_root()/.env`。

**影响**
- 个人环境配置、临时 token、路径覆盖会落在仓库根目录。
- 多人协作时很容易互相覆盖，且有误提交风险。

**方案**
- 开发态也改成用户数据目录下的运行时配置文件。
- 如果必须支持 `.env`，至少改成 `.env.local` 或明确隔离的 runtime 文件。

## 4. 上传和生成接口都缺少边界约束
**证据**
- `src/apps/comic_gen/api.py:233-241,266-283,2113-2122` 的上传接口只做了 `copyfileobj`，没有大小、类型、内容校验。
- `src/apps/comic_gen/api.py:1249-1250,1427-1470,2028` 的 `duration`、`batch_size`、`volume` 等输入都没有服务端上限。

**影响**
- 大文件、错误文件、恶意请求会直接打到磁盘、内存和外部模型账单。
- 前端限流不可信，后端不设边界就是自欺欺人。

**方案**
- 上传时做文件大小上限、 MIME/后缀白名单、必要时内容嗅探。
- 生成类 API 给 `duration`、`batch_size`、`speed`、`pitch`、`volume` 等加 `ge/le` 约束。
- 给高成本接口加速率限制或队列配额。

## 5. 导出功能的 UI 承诺和后端真实能力不一致
**证据**
- `src/apps/comic_gen/api.py:2158-2181` 里，`resolution/format/subtitles` 只是接收，没有真正生效。
- `src/apps/comic_gen/export.py:29-63` 还是 mock：写 dummy video content，FFmpeg 三步全是 TODO。

**影响**
- UI 让用户以为能选分辨率、格式、字幕，实际上后端直接忽略。
- 这类“看起来像功能”的接口最伤信任。

**方案**
- 要么真做完整导出管线，要么先把 UI 控制收掉，并在接口层明确标记 unsupported。

## 6. 音频链路里，SFX/BGM 仍是占位实现
**证据**
- `src/apps/comic_gen/audio.py:102-157` 里，`generate_sfx`、`generate_sfx_from_video`、`generate_bgm` 都在写 dummy bytes 或 mock 逻辑。

**影响**
- 项目文案里说“智能视听合成”，但声音设计这一半并没有真正落地。
- 结果是产物可用性和宣传能力不匹配。

**方案**
- 先明确这些能力是实验性还是正式能力。
- 如果正式要上，就接入真实音频生成和混音流程；否则在 UI/文案里降级表述。

## 7. `/mix/generate_sfx` 和 `/mix/generate_bgm` 名义上分流，实际上都在跑全量音频
**证据**
- `src/apps/comic_gen/api.py:1890-1905` 里两个 endpoint 都直接调用 `pipeline.generate_audio(script_id)`。

**影响**
- 按钮名字和真实行为不一致，用户以为只做局部处理，实际上把对话、SFX、BGM 全跑了一遍。

**方案**
- 拆成真正的局部接口，或者删除这两个入口，改成一个清晰的全量动作。

## 8. 持久化方案过于脆弱，还是单机 JSON 文件
**证据**
- `src/apps/comic_gen/pipeline.py:540-543,635-640,4566-4589`。

**影响**
- 当前是内存对象 + JSON 文件，适合单进程 demo，不适合并发、崩溃恢复或横向扩展。
- 一旦写入中断，项目数据也可能损坏。

**方案**
- 最少做到原子写 + 备份 + 跨进程锁。
- 更稳妥的是迁到 SQLite/Postgres 这类事务型存储。

## 9. 前端本地状态和后端快照会漂移
**证据**
- `frontend/src/app/page.tsx:476-481` 只在 `backendProjects.length > 0` 时才同步到 store。
- `frontend/src/store/projectStore.ts:580-594` 删除项目时，后端失败了也会把本地状态删掉。

**影响**
- 后端清空或失败时，前端会留着旧项目卡片。
- 删除失败被静默吞掉，用户以为删成功了，实际上没有。

**方案**
- 同步时直接用后端快照覆盖本地。
- 删除失败不要默认乐观删除，或者至少给出明显的失败回滚提示。

## 10. 生产构建把类型和 lint 都关掉了
**证据**
- `frontend/next.config.mjs:22,25`。

**影响**
- 现在 `next build` 能过，不代表类型和规范能过。
- 这会把大量回归推到运行时和人工测试阶段。

**方案**
- 把类型/lint 重新放回 CI 或构建门禁。
- 如果暂时必须放宽，也要单独保留一个严格检查任务，不要永久关闭。

## 11. 国际化还只是半成品
**证据**
- `frontend/src/lib/i18n/en-US.ts:1-29` 只是 `"[TODO en-US]"` 占位。
- `frontend/src/app/layout.tsx:1-14` 还硬编码了 `lang="zh-CN"`。

**影响**
- README_EN 和双语叙述会给人“已经支持英文”的错觉，但实际 UI 还没准备好。
- 维护成本会上升，中文 copy、英文 copy 和 prompt 文本容易互相漂移。

**方案**
- 要么补齐英文 locale，要么先把双语承诺降级为中文优先。
- 先把用户可见文案从 prompt 文本里拆出来，再做真正的 i18n 收口。

## 12. 外部媒体下载关闭了证书校验，而且部分下载方式会整文件入内存
**证据**
- `src/models/image.py:1308` 用了 `verify=False`。
- `src/models/seedance.py:324-328`、`src/models/kling.py:180`、`src/models/vidu.py:144` 都在直接读 `.content`。

**影响**
- TLS 校验被关掉，外部下载更容易被中间人攻击。
- 大视频直接进内存，长任务和大文件场景会放大内存压力。

**方案**
- 恢复证书校验，只有极少数特殊场景才显式豁免。
- 改成流式下载到临时文件，再原子落盘。
