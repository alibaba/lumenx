# LumenX 应用访问契约

2026-10-10；归属 P7。执行状态和证据只在 [重构台账](../agents/context/lumenx-first-principles/EXECUTION-STATUS.md) 维护。本文规定当前 access primitive；不表示 ECS、正式桌面包或所有远程部署组合已验收。

## 运行方式

- `LUMENX_ACCESS_MODE=local`（默认）：本机桌面/开发无需访问令牌。同 hostname 的开发端口可访问 API；例如 `http://127.0.0.1:3008` → `http://127.0.0.1:17177`。本地模式没有账户认证，应绑定可信的本地接口。
- `LUMENX_ACCESS_MODE=remote`：启动时必须有至少 32 UTF-8 bytes 的 `LUMENX_ACCESS_TOKEN`，否则启动失败。该值是部署者生成的随机访问凭据，与 DashScope 等模型供应商密钥无关。
- 远程浏览器/桌面 webview 通过 HTTPS 同源应用入口登录；服务设置 Secure + HttpOnly + SameSite=Strict、Path=/ 的 `__Host-lumenx_session` cookie。页面请求和普通媒体 URL 共用此会话。令牌不得放到 URL、NEXT_PUBLIC_*、项目数据或持久化前端 store。
- `LUMENX_ACCESS_ALLOWED_ORIGINS` 可列出逗号分隔的明确 http(s) origin，不能用通配符、路径或不透明 origin。允许 CORS 本身不保证跨站 cookie 可用；本阶段推荐并验证同源拓扑，跨站部署另行验收。
- CLI 可对受保护接口发送 `Authorization: Bearer ...`。浏览器通过会话 cookie；不把 Bearer 塞进媒体 URL。
- `main.py --backend-url ...` 只连接已有服务，服务端 access mode 须由服务端独立配置。客户端选择远程 URL 不会替服务端开启保护。health readiness 和登录是两个步骤。

## HTTP 契约

| 请求 | 响应与行为 |
|---|---|
| `GET /auth/session` | `{auth_required: boolean, authenticated: boolean}`；匿名可调用，本地为 false/true |
| `POST /auth/session` | JSON `{token: string}`；远程须有效 Origin 和凭据，成功建立内存会话；请求体上限 4 KiB |
| `DELETE /auth/session` | 服务端撤销当前 cookie 会话并清 cookie；cookie 模式须有效 Origin |
| 受保护请求缺失/失效凭据 | HTTP401，顶层 `code: access_required`，不回显令牌 |
| cookie 写请求 Origin 不符 | HTTP403；不会进入业务路由 |
| `GET /health` | 匿名远程只返回最小 service/protocol/ready 信息；诊断路径与项目数须认证 |

远程模式除 health、应用静态壳、会话发现/登录和合法预检外，API、docs、配置、日志、媒体都受统一边界保护。受保护响应 no-store；媒体 Range 与 SSE 保持原协议。会话保存在 app 内存，默认 12 小时、至多 128 个，退出清理；重启后用户重新登录。

## 前端约束

初始会话未确认时不挂载产品。会话查询失败有独立的读取重试；不能把它当作模型配置缺失。登录 POST 后再 GET，确认浏览器确实接受了 cookie 才进入产品。

失效时保留已挂载草稿，通过全局登录屏障阻止交互；重新登录仅恢复界面，不能自动重放创建、生成或其他付费写入。API fetch 保留原始响应/SSE 流，axios 拦截只针对所选 API base。

## 媒体边界

`/files` 与历史媒体别名只提供受管理媒体。请求路径和真实文件路径都须满足媒体目录/后缀规则，内部 JSON、锁、临时文件、隐藏路径和越界软链接不可下载。SVG 以 sandbox CSP 返回；媒体启用 nosniff。访问控制与媒体白名单是两层独立检查。

## 验证限制

当前自动测试使用临时数据、TestClient/ASGI、合成媒体与受控前端请求。反向代理须正确传递可信 scheme/host；HTTPS 证书、真实浏览器 cookie、正式安装包、ECS 防火墙和持久卷属于后续现场门禁。默认 local 不是对公网部署的安全承诺。没有新增多人账户、权限角色或 SaaS 登录体系。


## 启动默认值

`npm run dev:backend` 和 `./start_backend.sh` 的开发后端默认绑定 `127.0.0.1:17177`，与正式桌面本地入口一致。需要网卡暴露时显式运行 Uvicorn，先配置 `LUMENX_ACCESS_MODE=remote`、访问令牌和HTTPS入口；缺少令牌的remote启动会失败。Docker端口映射与ECS反向代理仍需独立部署门禁，不能把开发脚本的loopback修复当成容器已验收。

## 配置保存边界

`POST /config/env` 的更新与删除作为一个完整替换持久化，成功后才发布当前应用的 `RuntimeEnvironment` 快照，不写入进程 `os.environ`。保存失败保留旧运行配置；替换后同步失败返回503与 `commit_uncertain: true`，不能自动重试或假定磁盘还是旧版本。JSON损坏/非法内容/软链接不得被当空配置覆盖；dotenv保留无关条目、引号和注释。

启动读取JSON时先校验全部键值，再应用包括空字符串在内的显式值；用户清空的密钥不能因为跳过空值而从继承环境中复活。指定用户根的桌面本地与 headless 入口仅从该根配置供应商/OSS，不借用进程环境中的密钥；开发入口保留显式配置的环境/.env 读取方式。客户端由应用快照持有，配置更新不会改写其它应用或已取得快照的任务。访问控制的环境回退另按上文规则，不与供应商密钥混为一谈。

配置文件自身只有进程内命令互斥，没有额外的通用跨进程事务锁。桌面本地与 headless 入口通过同一数据根的单写者约束阻止第二个服务启动；不要据此声称任意独立进程直接写同一配置文件也受到保护。核对日期：2026-10-18。
