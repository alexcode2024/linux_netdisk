# NAS 文件管理

面向 Ubuntu NAS 的局域网文件管理与云盘备份应用。Web 界面管理服务器上的授权目录，并可将文件或文件夹作为后台任务备份到夸克网盘或百度网盘。

> 本项目默认只在局域网提供服务。不要直接暴露到公网；如需远程访问，请先自行配置可信的 VPN 或反向代理、HTTPS 和访问控制。

## 功能

- 管理员登录、会话认证和登录限速。
- 浏览授权目录，支持搜索、排序、分页、上传、下载、预览、新建文件夹、重命名和移动。
- 从文件管理页面发起夸克或百度网盘备份，支持批量选择和目标目录选择。
- 后台备份任务持久化，显示进度和状态，支持查看详情、取消任务和重试失败项。
- 夸克网盘通过官方 [quarkclouddrive_offical](https://github.com/quark-clouddrive/quarkclouddrive_offical) 项目的 CLI 操作。
- 百度网盘通过 OAuth 2.0 和网盘 OpenAPI 授权、浏览应用目录并上传文件。
- “夸克网盘”页面支持浏览文件列表、进入文件夹和通过面包屑返回上级目录，并为文件或文件夹生成分享链接。分享支持公开或带提取码的链接及有效期选择，提取码由官方服务生成；可一键复制分享信息。
- 支持浅色和深色主题。
- Docker Compose 以非 root 用户运行，并限制容器权限和可访问的本地目录。

## 技术栈

- 前端：React、TypeScript、Vite、Lucide
- 后端：Python、FastAPI、SQLAlchemy、SQLite
- 后台任务：独立 Python worker
- 部署：Docker、Docker Compose

## 项目结构

```text
backend/app/       FastAPI、云盘适配器、数据库及 worker
frontend/          React + TypeScript 前端
config/            应用配置模板
deploy/            Docker Compose 和环境变量模板
scripts/           配置检查、数据库备份等运维工具
tests/             后端测试
```

## 部署

### 前置要求

- Ubuntu 主机及 Docker Engine、Docker Compose plugin
- 主机上已存在要管理的目录
- 夸克网盘需另外安装并授权官方 `quarkclouddrive_offical` 项目；仅启用相应功能时需要
- 百度网盘需在百度网盘开放平台创建应用，并准备 AppID、AppKey、SecretKey、SignKey

### 配置主机路径

克隆项目并创建本地部署配置：

```bash
git clone <你的 GitHub 仓库地址> nas-file-manager
cd nas-file-manager
cp deploy/.env.example deploy/.env
```

编辑 `deploy/.env`，设置 NAS 的局域网地址、运行 UID/GID、配置目录和夸克官方 CLI 安装目录。至少检查以下变量：

```dotenv
LAN_BIND_IP=192.168.1.230
PUID=1000
PGID=1000
NAS_CONFIG_DIR=/opt/docker/config/nas-web
NAS_SOURCE_DIR=/opt/nas-web-src
QUARK_SKILL_DIR=/opt/quarkclouddrive
```

后续命令需要读取这些路径变量，先加载本地环境文件：

```bash
set -a
. deploy/.env
set +a
```

`LAN_BIND_IP` 必须是 NAS 的实际局域网 IP，不要改成 `0.0.0.0`。请按自己的主机调整目录。Compose 示例将主机目录 `/srv/nas/07_Downloads` 挂载至容器 `/nas/Downloads`；若目录不同，同时修改 `deploy/compose.yaml` 的 bind mount 和 `config/app.yaml` 中对应的 `container_path`。

准备持久化目录并安装运行配置：

```bash
sudo mkdir -p "$NAS_CONFIG_DIR/data" \
  "$NAS_CONFIG_DIR/secrets/quark-home" \
  "$NAS_CONFIG_DIR/secrets/baidu"
sudo install -o "$PUID" -g "$PGID" -m 600 \
  config/app.yaml.example "$NAS_CONFIG_DIR/app.yaml"
sudo chown -R "$PUID:$PGID" "$NAS_CONFIG_DIR"
chmod 700 "$NAS_CONFIG_DIR/secrets" \
  "$NAS_CONFIG_DIR/secrets/quark-home" \
  "$NAS_CONFIG_DIR/secrets/baidu"
sudoedit "$NAS_CONFIG_DIR/app.yaml"
```

在主机配置文件中调整授权目录和云盘设置。`NAS_SOURCE_DIR` 必须指向项目源码目录；Compose 文件默认从 `deploy/` 的上级目录构建镜像。

### 配置云盘

云盘在 `config/app.yaml` 中分别启用，模板默认关闭。未配置或未授权某个云盘时，可保持该云盘关闭。

**夸克网盘**

按官方项目文档安装 [quarkclouddrive_offical](https://github.com/quark-clouddrive/quarkclouddrive_offical)，确保 `QUARK_SKILL_DIR` 下存在 `scripts/quark-drive.cjs`。将 `quark.enabled` 设为 `true`，确认 `script_path` 和 `home` 与 Compose 挂载位置一致。授权数据保存在 `secrets/quark-home`，不要提交到 Git。

**百度网盘**

在百度开放平台配置 OAuth 回调地址，地址必须与 `config/app.yaml` 的 `baidu.redirect_uri` 完全相同。例如：

```text
http://192.168.1.230:8088/api/v1/cloud/baidu/oauth/callback
```

将 IP 和端口替换为实际访问地址。设置 `baidu.app_name` 为开放平台中登记的应用名称；网盘 API 只能访问该应用的 `/apps/{应用名称}` 目录。

在主机创建凭据文件，内容格式如下。请填入自己的值，不要把真实凭据提交到仓库：

```text
AppID=替换为你的 AppID
AppKey=替换为你的 AppKey
SecretKey=替换为你的 SecretKey
SignKey=替换为你的 SignKey
```

使用 `sudoedit "$NAS_CONFIG_DIR/secrets/baidu/baidu_param"` 创建该文件，然后设置属主和权限：

```bash
sudo chown "$PUID:$PGID" "$NAS_CONFIG_DIR/secrets/baidu/baidu_param"
sudo chmod 600 "$NAS_CONFIG_DIR/secrets/baidu/baidu_param"
```

确认 `$NAS_CONFIG_DIR/app.yaml` 中的 `credentials_file` 和 `token_file` 指向 Compose 挂载的 `/app/data/secrets/baidu/` 目录，然后将 `baidu.enabled` 设为 `true`。服务启动后登录 WebUI，在“系统设置”中完成百度授权。

### 构建和启动

从项目根目录执行：

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml config
docker compose --env-file deploy/.env -f deploy/compose.yaml build
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps nas-web \
  python -m backend.app.init_admin --username admin
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d
```

管理员密码通过交互方式设置，不会写入镜像或应用配置。检查服务状态：

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml ps
curl -fsS "http://$(grep '^LAN_BIND_IP=' deploy/.env | cut -d= -f2):8088/health/ready"
```

浏览器访问 `http://<NAS 局域网 IP>:8088` 并登录。

### 夸克上传错误处理

夸克官方 CLI 上传遇到可识别的网络读取超时、连接重置或服务端错误 `53000`，且返回持久化任务 ID 时，备份 worker 会按 `quark.retry` 配置调用官方 `upload resume` 续传，不会重新创建整文件上传任务。错误码和任务 ID 从 CLI 的结构化响应或错误日志中提取，续传成功必须确认对应任务的文件 FID。默认最多续传 3 次，依次等待 5、10、20 秒；等待时间不超过 `max_delay_seconds`。未返回任务 ID、授权、空间及文件大小限制等错误不会自动续传；续传次数耗尽后仍会保留错误供手动处理。

### 百度上传错误处理

百度网盘的普通 JSON 响应、HTTP 错误正文和分片响应使用统一解析逻辑。错误信息包含接口名、错误分类、业务码、HTTP 状态及百度返回的 `request_id`，任务详情与 worker 日志可用于排查；日志中的 Token、应用密钥和带参数链接会被隐藏。

- 网络异常、临时 HTTP 错误、接口频控 `31034` 和分片 MD5 校验失败：在允许安全重试的步骤中，首次请求失败后最多重试 10 次，每次等待 60 秒。
- 调用额度限制 `20012`：检查应用审核状态、调用额度及调用频率，不按普通临时错误反复重试。
- 授权、权限、路径、容量、参数和文件大小错误：给出对应处理提示，不盲目重试。现有 Token 到期前自动刷新机制保留，服务端提前判定授权失效时会提示刷新或重新授权。
- 文件合并确认超时：重试前查询目标文件，核对路径、类型和大小；已生成则恢复成功，查询失败时不重复提交创建请求。
- 每个实际上传的分片都必须返回与本地一致的 MD5（不区分大小写），校验通过后才更新进度；缺少 MD5 或不一致会重试该分片，耗尽次数后终止，禁止继续合并文件。

百度没有返回 `request_id`（例如连接建立前就超时）时，日志会明确记录该标识不可用。网络恢复仍取决于主机与百度服务之间的连接状况。

## 开发与测试

后端：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt pytest httpx
PYTHONPATH=. pytest -q
```

前端：

```bash
cd frontend
npm ci
npm run build
```

## 安全说明

- 本地 `config/app.yaml`、`deploy/.env`、百度凭据、OAuth token、夸克授权目录、数据库和构建产物不应提交到 Git。
- 仓库提供的是 `config/app.yaml.example` 和 `deploy/.env.example` 模板；请在主机侧维护真实配置及凭据。
- `app.yaml` 只开放 `storage.allowed_roots` 中明确列出的容器路径。新增目录时，必须同时配置主机 bind mount 和授权根目录。
- 删除和符号链接访问默认关闭。请先确认授权目录和备份策略，再调整安全设置。
- 备份 SQLite 数据库请使用 `scripts/backup_database.py`，不要只复制运行中的数据库主文件。

## 限制

- 这是单管理员、局域网优先的轻量应用，不提供多用户权限管理或公网安全网关。
- 远端冲突默认跳过，不覆盖同名文件。
- 百度网盘只能在该应用专属目录内操作。
- 云盘授权和上传依赖对应平台的有效应用配置及授权状态；本地适配器测试不代表真实云端上传测试。
