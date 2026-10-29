# 项目与共享来源的读取确认契约

适用：`feat/lumenx-core-foundation`，2026-10-10。本文维护已实现的 HTTP 返回边界；阶段完成情况只查[执行台账](../agents/context/lumenx-first-principles/EXECUTION-STATUS.md)，测试入口只查[测试矩阵](../agents/context/lumenx-first-principles/TEST-MATRIX.md)。Atelier 未固定的增强版本不能直接引用本文件宣称已经接入。

## 响应不是同一种快照

| 响应 | 能确认的事实 | 前端处理 |
|---|---|---|
| `GET /projects/{id}` | 同一次已提交捕获中的项目及解析后的共享资产；包含 `revision` 和 `read_versions` | 按环境、项目、系列和库的版本关系接受；较低或不可比较来源不能覆盖当前已确认投影 |
| `GET /projects` | 已提交项目列表，未合并共享资产，没有 enriched 来源证明 | 作为工作区列表；不能冒充单项目读取或覆盖已确认共享投影。旧请求不能删除随后创建/修改的项目，真实空数组与非法响应须区分 |
| 创建/重解析及多数 frame 命令返回的原始 `Script` | 命令所属项目的已提交状态及 `revision`；不证明共享来源仍新鲜 | 校验完整 ACK；按需保留已知共享来源并标记待刷新，或携带项目版本下限做一次 enriched GET。不得把旧文本拼到新 revision 下 |
| 单帧精修、工作台 PATCH、首帧上传返回的 `Frame` | 对应已保存分镜；不是 `Script`，没有项目版本 | 按真实返回形状处理。需要确认读取时，比对分镜收据；`updated_at` 精确匹配用于识别该次已提交内容，不是项目 revision 下限或全局时钟顺序 |
| 视频提交返回的任务集合 | 返回的任务 ID、项目/分镜归属、generation_mode 与 workbench_tab | 保留所有已接纳任务，部分提交不能丢弃成功项。读取须看见对应 ID 及相同归属/mode/tab；确认读取失败不可自动再次提交付费任务 |
| `GET /library/assets` | 同一次已提交捕获中的 `{revision, characters, scenes, props}` | library revision 是持久版本；按已观察下限拒绝回退，不使用请求时间代替。字段自 `a4f6eb14` 加入 |
| 库资产 create/promote 返回的资产 | 新资产 ID 与内容；不携带库版本 | 保存响应与列表确认分开；通过一次库读取确认返回 ID，读取失败保留已创建事实，不能重新 create/promote |

以上列出已使用的响应类别，不能据此假定所有旧 endpoint 都返回 Script。新增/修改调用点必须核对实际路由，测试替身须保持真实 HTTP 形状。

## 项目读取的来源证明

`read_versions.project` 必须等于项目 `revision`；`library` 为非负整数；`series` 明确为 `null`（无系列）或 `{id, revision}`。绑定系列暂时缺失时，系列 `revision: null` 表示未知，不能按数字零处理。缺少整个 `read_versions` 是旧/命令响应，不是 enriched 读取。缺来源、身份冲突或不可比较版本不得伪装成成功刷新。

项目命令 ACK 也推进独立项目 revision 下限，不能因为未立即合并缓存就让随后旧 GET 越过保存版本。系列列表新增/更新必须纳入共享观察；列表请求期间推进的来源不能因旧响应 omission 被移除。

当前前端环境范围使用 `API_URL`；它还不能识别同一 URL 上的服务重装或数据谱系回退，该能力属于 P7/P8。当前与缓存投影、独立来源观察下限必须一起维护，不能仅保护当前打开的页面。

## 错误与重试

写请求失败、合法 ACK 后读取失败、非法 ACK 的结果不确定，属于不同情况。非法 ACK 不显示“已保存”；初始只读请求失败也不显示“已保存”。一次确认失败不得形成后台无限 GET 循环，更不能自动重发生成、复制、提升或创建命令。请求已发出但网络断开、5xx 或 ACK 形状错误都可能已产生副作用；界面保留“结果未确认”，不能冒称任务失败并解锁再次付费。显式重新读取只发读请求；已确认失败后是否允许重新执行，由具体功能的状态决定。

回调须检查所属项目、环境和已失效的 UI 会话。用户继续编辑的草稿不能被较早保存结果覆盖；后台成功可更新已确认缓存，但不得抢走用户后来选择的项目或导航。

媒体签名 URL 用于展示，可能在 ACK 和 GET 之间变化。原始媒体引用及 `media_refs` 遵循[媒体响应契约](lumenx-media-response-contract.md)；不能以签名字符串相等代替保存或内容身份确认。

本文件描述 HTTP 契约，不声明所有客户端入口已经迁移，也不替代 P6 的持久接纳/进程恢复或 P9/P10 的真实产品验收。尚未接入路径见[共享读交接清单](../agents/raw/lumenx-shared-read-client-handoff.md)。


## 合成结果当前性

`GET /projects/{id}` 与 `POST /projects/{id}/merge` 返回只读 `merged_video_freshness: {state, reason, project_revision}`。state 为 current/stale/unknown，绑定该次项目快照 revision。普通编辑回执可能不含此字段，客户端必须撤下旧证明；不能把旧 current 拼到新 revision。该派生证明不写入项目JSON或浏览器持久缓存。

合成成功时，将 `merged_video_source` 与新URL一起原子提交。schema_version 1记录有序的 `[frame_id, 实际媒体引用]`、BGM引用及有效mix_settings，来源使用后端实际取材规则（包括已采纳且匹配的配音）。渲染期间输入或之前成片URL变化时禁止覆盖。来源缺失、损坏、未来版本为unknown；输入变化/文件丢失为stale；只有来源匹配且输出属于本项目受管合成文件，才是current。无分镜、输入片段或内部JSON不能冒充当前输出。

客户端保留历史成片预览和明确标注的历史下载；不能仅凭URL将项目、流水线或导出面板显示为完成。缺证明时显示待确认，通过当前后端读取或重新合成取得证明。

本字段目前固定引用身份与混音，不验证任意外部覆写同路径后的内容字节；固定媒体版本仍由P3负责。进度、验证版本和限制统一见执行台账，不由此契约文字代替实际验收。

## 中断视频任务的待确认状态

`VideoTask.status` 增加 `needs_confirmation`，表示本地流程已中断、供应商结果无法确认。启动时遗留的旧 `pending/processing` 进入此状态，保留已有错误、provider诊断ID、媒体引用和用户标注。匹配的共享资产任务镜像与项目状态同事务保存；不能因为相同task ID就覆盖内容或归属不同的共享记录。

该状态不表示供应商失败，不触发提交、查询或取回，也不进入前端无限轮询或失败Retry。迟到的本地错误不得降级它；调用旧cancel接口得到409。用户仍可从正常生成入口明确创建新任务，但不能把它描述为对旧请求的只读恢复。`completed/failed` 旧记录不由本片重新分类。

平铺的 `provider_task_id/provider_request_id` 只供诊断，不补造scope、account或credential_ref。完整收据和原凭据绑定具备后，P6另行接通只查询恢复；阶段证据与前端接入状态以执行台账为准。
