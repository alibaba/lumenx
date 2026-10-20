# R2V 一等公民重构方案

> 目标：消除 R2V "hidden + 从 I2V 推导" 的间接架构，让 R2V 成为 catalog 中直接可见、自带完整配置的独立模型。

## 核心问题

当前 R2V 模型在 catalog YAML 中标记为 `status: hidden` + `visible_in: []`，前端通过 40+ 行推导代码（`R2V_ROUTE_MAP` → `VIDEO_R2V_MODELS`）从 I2V 兄弟模型反推出 R2V 选项。这在 R2V 成为主工作流后带来：

- R2V 参数（duration/resolution）依赖 I2V 继承，无法独立演化
- 推导链条脆弱（`visible_in.length === 0` 作为筛选条件）
- 新 family 接入时必须理解这套隐式约定

## 风险清单（按严重度排序）

| 严重度 | 风险 | 触发条件 | 缓解方案 |
|--------|------|----------|----------|
| **CRITICAL** | `R2V_ROUTE_MAP` 变空 | R2V 设 `visible_in` 后不再匹配 `length === 0` | 引入 `selection_group: 'r2v'`，用 group 筛选代替 visibility 筛选 |
| **CRITICAL** | R2V 污染 `VIDEO_I2V_MODELS` | R2V 和 I2V 共享 `selection_group: 'i2v'` | 给 R2V 独立的 `selection_group: 'r2v'` |
| **HIGH** | 后端 auto-switch 覆写直选的 R2V | `create_video_task` 强制按前缀重写 model | 加 early-return：`if model.endswith('-r2v'): pass` |
| **HIGH** | VideoSidebar R2V 模式 UX 崩溃 | 当前硬锁定一个 I2V 模型作为 "R2V 代表" | 改为显示 `VIDEO_R2V_MODELS` 列表 |
| **HIGH** | `resolveModelSettings()` 不验证 r2v_model | 旧 ID 不触发 fallback | 新增 r2v_model 解析逻辑 |
| **MEDIUM** | Pydantic 默认值硬编码 `wan2.7-r2v` | 不跟随 catalog 默认值 | 从 catalog.meta.yaml 读取 |
| **MEDIUM** | `create_asset_video_task` 硬编码 `wan2.6-r2v` | 忽略用户项目设置 | 改读 `script.model_settings.r2v_model` |
| **MEDIUM** | 旧前端仍发 I2V model + r2v mode | 滚动部署期间 | 后端 auto-switch 保留为兼容路径 |
| **LOW** | R2V YAML 无 duration/params | 继承消失后退化为 fixed 5s | 补全 YAML 配置 |
| **LOW** | localStorage 偏好静默丢失 | catalog 变更后旧 key 无效 | 已有 fallback，记录在 release notes |

## 分阶段实施计划

### Phase 1：Catalog Schema 扩展（安全，不改变运行时行为）

**目标**：让 catalog 构建系统接受 `selection_group: 'r2v'`，为后续改动铺路。

| 任务 | 文件 | 说明 |
|------|------|------|
| 1a | `src/utils/model_catalog.py:10` | `SUPPORTED_SELECTION_GROUPS` 加 `'r2v'` |
| 1b | `frontend/src/lib/modelCatalog.ts:63` | `SelectionGroup` 类型加 `'r2v'` |
| 1c | `config/model_catalog/catalog.meta.yaml` | defaults 加 `r2v_model: wan2.7-r2v` |
| 1d | `src/utils/model_catalog.py` | `DefaultModelSettings` 加 `r2v_model` 字段 |
| 1e | `src/utils/model_catalog.py:12-17` | `DEFAULT_MODEL_SURFACE_REQUIREMENTS` 加 r2v_model 条目 |
| 1f-1k | 所有 family YAML 的 R2V mode | `selection_group` 改为 `'r2v'`，补全 `duration` + `params` |

**验证**：`build_model_catalog.py` + `validate_model_catalog.py` 通过，`npm run typecheck` 通过。

---

### Phase 2：前端简化（替换推导为直查 catalog）

**目标**：R2V 模型在 YAML 中设为 `status: active` + `visible_in: [video_sidebar, ...]`，前端直接从 catalog 读取，不再推导。

| 任务 | 文件 | 说明 |
|------|------|------|
| 2a | 所有 family YAML | R2V status → `active`，visible_in → `[video_sidebar, project_settings, series_settings, global_settings]` |
| 2b | rebuild | 重新生成 catalog JSON |
| 2c | `modelCatalog.ts:432-478` | `VIDEO_R2V_MODELS` 改为直查：`getVisibleModels('r2v', 'video_sidebar')` |
| 2d | `modelCatalog.ts:479` | `DEFAULT_R2V_MODEL_ID` 从 `VIDEO_R2V_MODELS[0]` 取 |
| 2e | `modelCatalog.ts:414-421` | `R2V_ROUTE_MAP` 改用 `selection_group === 'r2v'` 筛选（兼容旧客户端 fallback） |
| 2f | `modelCatalog.ts:398-403` | `R2V_SELECTION_MODEL_ID` / `R2V_ROUTE_MODEL_ID` 简化为 `DEFAULT_R2V_MODEL_ID` 的 alias |
| 2g | `modelCatalog.ts` | `isR2vSelectionModel()` 标记废弃，return false |
| 2h | `modelCatalog.ts:337-357` | `resolveModelSettings()` 新增 `r2v_model` 解析 |
| 2i | `VideoSidebar.tsx` | R2V 模式改为显示 `VIDEO_R2V_MODELS` 可选列表 |

**验证**：typecheck + build + 前端测试全通过。启动 dev server 验证 R2V tab 正确显示所有 R2V 模型。

---

### Phase 3：后端加固（直选 R2V 端到端 + auto-switch 降级为兼容路径）

**目标**：新前端直接发 `model='wan2.7-r2v'`，后端正确处理；旧前端发 I2V model + r2v mode 仍能通过 auto-switch 工作。

| 任务 | 文件 | 说明 |
|------|------|------|
| 3a | `pipeline.py:~1942` | auto-switch 块首行加 `if model and model.endswith('-r2v'): pass`（跳过重写） |
| 3b | `pipeline.py:~3032` | `create_asset_video_task` 改读 `script.model_settings.r2v_model` |
| 3c | `pipeline.py:~3675` | `update_model_settings` 加 r2v_model 校验 |
| 3d | `api.py` | `UpdateModelSettingsRequest` 加 r2v_model Pydantic validator |
| 3e | `pipeline.py:1972` | suffix 检测改为 catalog capability 查询（可选，低优先） |
| 3f | 验证各 family | 确认 kling-v3-r2v、seedance-2.0-r2v 等有正确的后端 handler |
| 3g | 无 handler 的 R2V | pixverse/vidu R2V 若无独立 handler，暂保持 hidden 不暴露 |

**验证**：`pytest -q` 通过。手动 POST video_tasks 直接用 R2V model ID 验证。

---

### Phase 4：清理废弃代码

| 任务 | 文件 |
|------|------|
| 4a | 移除 `R2V_SELECTION_MODEL_ID` export |
| 4b | 移除 `R2V_ROUTE_MODEL_ID` export |
| 4c | 移除 `isR2vSelectionModel()` |
| 4d | 简化 `getR2vRouteModelId()` 为 legacy fallback |
| 4e | I2V model YAML 去掉 `capabilities: [i2v, r2v]` 中的 `r2v`（只保留 `[i2v]`） |
| 4f | VideoSidebar 清理旧 R2V lock-in 标签 |
| 4g | 更新测试断言 |
| 4h | 更新 CLAUDE.md 文档 |

**验证**：typecheck + build + test:all + pytest 全通过。

## 向后兼容保证

1. **已保存项目**：r2v_model 字段值（如 `wan2.7-r2v`）不变，ID 没有 rename
2. **旧前端 → 新后端**：auto-switch 保留为 fallback，旧 client 发 I2V + r2v mode 仍能正确路由
3. **新前端 → 旧后端**：新前端直接发 `model='wan2.7-r2v'`，旧后端的 `endswith('-r2v')` 检查已能识别
4. **localStorage**：已有 fallback 逻辑，无效 key 静默降级为默认值

## 回滚策略

- **Phase 1**：revert YAML selection_group 改动，rebuild catalog
- **Phase 2**：revert 前端改动，R2V 模型改回 hidden，rebuild catalog
- **Phase 3**：移除 early-return guard，恢复 auto-switch 为唯一路径
- **Phase 4**：恢复废弃 exports（纯加法操作，无风险）

每个 Phase 独立可部署、独立可回滚。
