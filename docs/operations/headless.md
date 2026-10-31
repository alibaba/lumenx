# LumenX 本地与远程服务

公开整理：2026-10-30。本页是可复用操作配方，来自研发版本 `7765f9e816088a88f8489a737ad70eb7211de051` 的 headless 说明；不携带个人服务器配置或历史现场日志。操作说明存在不表示 Docker、HTTPS、正式桌面或真实供应商已验收。当前结果见 [候选台账](../agents/context/lumenx-first-principles/EXECUTION-STATUS.md)，访问细节见 [访问合同](../1-api-reference/lumenx-access-contract.md)。

## 运行边界

- 一个数据根只启动一个后端进程。不要增加 workers、共享卷多副本或使用热重载运行正式数据。
- `--data-dir` 包含 `config.json`、`logs/`、`output/`。此模式不会借用工作区 `.env` 或进程供应商密钥。模型配置在该根保存，不写进前端构建或 Git。
- `LUMENX_ACCESS_MODE`、`LUMENX_ACCESS_TOKEN`、`LUMENX_ACCESS_ALLOWED_ORIGINS` 沿访问合同支持进程环境配置；不要在数据根同时留下矛盾配置。
- 静态目录不能与私有数据根重叠。Python 单服务使用默认 `/static/` 构建；`DOCKER_BUILD=true` 的根路径构建仅用于下面的 Docker 配方。
- 浏览器与正式桌面远程模式连接同一个应用 URL。工程包导入创建独立副本，不把两端数据目录做双向同步。
- 云端单机 MVP 只支持一个数据根、一个后端写进程和受控 Product Skill；本机 Codex/Claude Skill 目录不会自动同步到服务器。外部 Skill 必须先编译为版本化 manifest，不能在服务器直接执行任意脚本或读取开发者本机路径。
- 多用户生产需要独立的 PostgreSQL、对象存储、队列、隔离 worker、Skill registry、租户权限和审计方案；不能把本页单机配方通过增加 workers 或共享卷直接升级为生产集群。

## 本地单服务

准备 Python 3.11、与仓库 CI 一致的 Node.js、FFmpeg/ffprobe，并确认 `libx264`、`aac`、`xfade`、`acrossfade` 等实际使用能力可用。`ffmpeg -version` 本身不能证明导出可用。

在选定的固定版本中，使用新环境安装已有 headless 依赖清单并构建静态页面：

```bash
python3.11 -m venv .venv-server
.venv-server/bin/python -m pip install -r requirements-docker.txt
.venv-server/bin/python -m pip check
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend run build
.venv-server/bin/python -m src.lumenx_server --data-dir "$HOME/.lumenx-server-trial" --static-dir ./static --host 127.0.0.1 --port 17177
```

构建前清除当前 shell 中与本次目标冲突的 `DOCKER_BUILD`、`TAURI_BUILD` 或 `NEXT_PUBLIC_API_URL` 配置。浏览器打开 `http://127.0.0.1:17177/`，跳转到 `/static/index.html`；未提供 `--static-dir` 时仅提供 API。端口示例不是接管已有服务的授权：若已占用，为本次实例选择独立端口。

首次通过设置页配置模型服务。没有模型密钥仍可创建、编辑、保存项目及使用本地媒体；真实模型调用另验。PDF、Demucs 等可选依赖没有因为 headless 安装就自动成为可用能力。Python 清单并非完整的跨平台锁文件，记录安装解析版本与 `pip check`，不要将一台机器安装成功外推到所有平台。

关闭服务后保留完整数据根，以相同参数重新启动应读取同一环境。临时媒体需要足够磁盘空间；检查系统临时目录是否为内存盘，必要时用服务用户的私有磁盘目录配置 `TMPDIR`。

## 单机远程服务与 HTTPS

先准备独立系统用户、代码目录与私有数据根。例如代码 `/opt/lumenx`、数据 `/srv/lumenx-data`；复制源码和静态产物时核对同一版本，不复制另一台机器的 Python 环境。Linux 上单独安装依赖并验证媒体编解码。

仅由服务管理员读取的环境文件可包含：

```text
LUMENX_ACCESS_MODE=remote
LUMENX_ACCESS_TOKEN=<至少32字节的独立随机令牌>
LUMENX_ACCESS_ALLOWED_ORIGINS=https://lumenx.example.com
```

这些是占位内容，必须使用实际域名和合格令牌。供应商配置仍归数据根 `config.json`。工作目录为 `/opt/lumenx` 的服务以专用用户、`UMask=0077` 运行，加载上述访问环境，并使用私有磁盘临时目录：

```bash
/opt/lumenx/.venv-server/bin/python -m src.lumenx_server --data-dir /srv/lumenx-data --static-dir /opt/lumenx/static --host 127.0.0.1 --port 17177 --trusted-proxy-ips 127.0.0.1
```

后端保持回环监听；同机 Nginx 终止 HTTPS。下面示例需要实际证书，且仅适用于一个可信本机代理：

```nginx
server {
    listen 443 ssl;
    server_name lumenx.example.com;
    ssl_certificate /etc/letsencrypt/live/lumenx.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/lumenx.example.com/privkey.pem;
    client_max_body_size 256m;

    location / {
        proxy_pass http://127.0.0.1:17177;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 600s;
        proxy_next_upstream off;
    }
}
```

页面、API、SSE、媒体与工程包全部通过同源入口。不要直接公开后端端口、设通配代理信任或关闭 Secure cookie。加入负载均衡器/CDN/另一台代理时需重新验证来源 IP 和 scheme，不原样套用。Nginx 应用配置前先做语法检查；配置语义参考 [官方 proxy 文档](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)，现场仍需实际验收。

没有域名和 HTTPS 时可以先运行服务器回环检查；这不等于公网会话验收。服务器管理接入方式与产品访问方式是两个独立问题，不能把管理通道成功当作浏览器或桌面已经连接。

## Docker 配方

仓库 Compose 默认只发布到 `127.0.0.1:3000` 与 `127.0.0.1:17177`。它是本地试用配方，不自带公网 TLS：

```bash
docker compose --project-name lumenx-trial config
docker compose --project-name lumenx-trial build
docker compose --project-name lumenx-trial up -d
```

准备 Compose 要读取的 `.env`，但 headless 数据根模式不会把它用作模型密钥来源。已有 `./output` 挂到 `/var/lib/lumenx/output`，配置和日志位于该 Compose 项目的 `lumenx_user_data` 命名卷；备份必须同时包含两者。普通停止不删除卷。Docker 前端使用 `DOCKER_BUILD=true` 的根路径构建，与 Python 配方的 `/static/` 构建分别验证。

## 实际验收顺序

1. 记录源码/静态哈希、依赖版本、启动参数与数据根；确认原有服务未被接管。
2. 浏览器创建/保存项目、上传/播放媒体、停止后重开；第二写进程应被拒绝。
3. 远程 HTTPS 验证匿名拒绝、错误令牌、登录/退出、重启后登录、媒体鉴权与 Range，再运行产品旅程。
4. 导出合成工程包，在另一环境独立导入、编辑、播放；源项目不变，复制不获得在途付费任务执行权。
5. 正式桌面连接完整应用 URL，验证远程模式不启动本地生成后端，关闭窗口不影响非自有服务。

以隔离外部传输的测试项目先验证；收费模型、输出质量和正式平台各自记录。只有实际证据满足范围时才能关闭相关验收；安装、配方与包结构检查都不代替完整四组合运行。
