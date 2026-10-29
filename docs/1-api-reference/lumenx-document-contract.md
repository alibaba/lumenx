# Studio 文档持久化契约

2026-10-10；F02 / P2M。与原文 `original_text` 分开：这里保存 Script Editor 的 Tiptap 文档。当前实施范围和验证版本以[执行台账](../agents/context/lumenx-first-principles/EXECUTION-STATUS.md)为准。

文档必须归属一个已提交的 Studio 项目，存放在当前应用 `data_dir/documents/<project_id>/`。不自动读取、迁移或删除旧 `~/.tron/comic/projects`。没有 projectId 的独立编辑器只能明确作为本地草稿，不能报告服务端已保存。

## 文档版本

GET/保存/恢复返回 `{project_id, revision, updated_at, content}`。文档 revision 独立于 Studio 项目 revision；项目存在但从未保存文档时，GET 返回 revision 0、updated_at null 和空 Tiptap doc。不存在的项目返回404。

| 请求 | 规则 |
|---|---|
| `GET /projects/{id}/document` | 读取当前文档 envelope |
| `POST /projects/{id}/document` | `{content, expected_revision, create_snapshot?}`；expected_revision 必须是非负整数，布尔/字符串不接受 |
| `GET /projects/{id}/document/snapshots` | 返回快照列表，包括 snapshot_id、created_at、revision、size_bytes；timestamp 作为 snapshot_id 的兼容别名 |
| `POST /projects/{id}/document/snapshots` | 保存当前文档的不可变快照；未曾保存的空文档404 |
| `POST /projects/{id}/document/snapshots/{snapshot_id}/restore` | 必须发送 `{expected_revision}`；验证快照后写入新文档revision |

缺少/非法版本与非有限JSON内容返回422；旧revision返回409并保留当前内容。保存内容不变不增加revision；恢复总是产生新revision。create_snapshot 保存替换前的已有文档；快照名含安全时间片段与随机后缀，不依赖秒级时间唯一性。

## 原子性与失败

文档复用当前应用目录写锁和存储关闭检查。JSON使用临时文件+原子替换；实际写入失败进入现有storage_unavailable错误契约，替换后同步失败标记commit_uncertain。客户端不能把未知结果当成未保存并盲目覆盖重试。

损坏的已有文档/快照不被当成空文档覆盖。文件/目录软链接、越界ID、内部快照ID与文件名不符均被拒绝。非法客户端JSON在创建快照或写盘前拒绝，不能令整个应用失效。

前端必须验证回执归属和revision；保存期间的新编辑保持dirty；冲突/网络错误保留草稿并显示失败；切换项目或卸载后的迟到读取不能覆盖当前编辑器。快照恢复需同时接纳内容与新revision，不得只替换编辑器内容。

项目编辑器在读取期间禁止编辑；未保存草稿按 API 地址与项目隔离。恢复草稿如发现服务端版本已变化，停止自动保存并保留旧基线；用户可以明确舍弃草稿再读取最新版本，读取失败不能先丢掉草稿。快照列表和恢复请求绑定当前打开会话，关闭、切换项目或离开后重返同一项目均不能接纳旧请求。没有项目的独立编辑器不调用文档持久化或模型补全接口。

## 文件导入与导出

`POST /projects/{id}/document/import` 接受 UTF-8 文件的严格 base64 编码，格式限 FDX/Fountain/TXT；只返回 Tiptap 内容，不隐式保存文档。FDX 拒绝 DTD/entity 和损坏 XML。错误编码为400，非法格式为422，不存在的项目为404。

`POST /projects/{id}/document/export` 接受 Tiptap doc 及 PDF/DOCX 格式；先验证项目和文档结构。非法节点或 content 形状为422。缺少对应渲染依赖为503，不能用 HTML/TXT 内容冒充 PDF/DOCX 下载。导入导出弹窗目前没有挂载到生产 ScriptEditorShell，API 的测试不能当作这些弹窗的端到端验收。

## 派生数据与镜头块

`GET /projects/{id}/sync_derivation` 读取 `{project_id, revision, updated_at, source_document_revision, data}`，POST 以 `expected_revision` 校验该 sidecar，并以 `source_document_revision` 校验当前文档，保存场景、角色、地点等派生信息。两种版本均为必填非负严格整数。文档变化或 sidecar 变化均返回409。

`GET /projects/{id}/shot_blocks` 读取相同版本元数据及 `blocks`。`POST /projects/{id}/shot_blocks/{shot_id}/confirm` 同样要求两个版本；每个 block 保留自身 `source_document_revision`。确认新版本中的另一个镜头不会把旧镜头改标成新文档来源。集合的 source_document_revision 表示最近一次确认依据，不代表全部镜头来源一致。

两种 sidecar 分别写入当前应用的 `derivation.json` / `shot_blocks.json`；没有文件时返回 revision 0、空数据，已有损坏文件不能退化成空数据。沿用目录锁、原子保存和实际写失败关闭规则。它们不等于修改 Studio 的 frames 或共享角色库；生产编辑器接线与入口能力以测试矩阵为准。

## 模型补全

`POST /projects/{id}/derive_gaps` 要求项目存在；显式 `raw_text_blocks` 优先，否则读取本应用持久化文档。响应为 `{entities, cached: false}`，不跨项目或应用缓存。空输入/明确空结果可返回空实体；未配置模型、供应商失败、非法JSON/实体结构返回502，不能伪装为成功但没有发现。

实体 `type` 限 character/prop/beat/location；name 必须非空；confidence 必须是0到1的有限数值，不能是字符串或布尔；scene_index 如有必须是非负整数。使用单次明确模型请求，SDK request timeout 为2秒、retry为0；这是网络请求超时设置，不是整个处理函数的严格墙钟上限。[SDK选项依据](https://github.com/openai/openai-python/blob/main/README.md#timeouts)。
