# Ubuntu NAS 网页文件管理与夸克网盘备份系统

## 详细设计说明书（供 Codex CLI 实现）

-   文档版本：1.0
-   日期：2026-11-04
-   目标环境：Ubuntu 26.04 LTS、Docker Compose
-   部署位置：NAS 主机
-   访问范围：仅局域网
-   核心模式：自建网页文件管理器 + 手动发起夸克网盘备份

------------------------------------------------------------------------

## 1. 项目目标

在 Ubuntu NAS 上部署一个轻量、可维护的 Web
文件管理系统。用户通过局域网浏览器访问网页，查看经过授权的 NAS
目录，执行常见文件管理操作，并可选择文件或文件夹，点击"备份到夸克"按钮，将其上传到夸克网盘。

本项目不是简单安装一个网盘客户端，也不是只部署通用文件管理器。核心交付物是一个具有统一用户界面的自建
Web 应用，夸克备份是文件管理器中的一项原生操作。

### 1.1 已确定需求

  项目           决策
  -------------- ----------------------------------------------
  文件范围       selected：仅允许访问配置文件中明确列出的目录
  备份方式       manual：用户手动选择文件/文件夹并发起备份
  访问范围       lan：仅局域网访问，不提供公网入口
  部署方式       Docker Compose
  数据根目录     `/srv/nas`
  应用部署目录   `/opt/docker/compose/nas-web`
  应用配置目录   `/opt/docker/config/nas-web`
  下载目录       `/srv/nas/07_Downloads`
  时区           `Asia/Shanghai`
  管理方式       SSH + Docker Compose
  实现方式       使用 Codex CLI 编写、测试和部署

### 1.2 用户使用场景

1.  用户在局域网电脑打开 `http://<NAS-LAN-IP>:8088`。
2.  登录系统。
3.  在左侧目录树或目录入口中选择一个已授权目录。
4.  浏览文件、搜索、排序、查看文件信息。
5.  勾选一个或多个文件/文件夹。
6.  点击"备份到夸克"。
7.  选择夸克网盘目标目录，确认备份。
8.  系统在服务器后台创建上传任务。
9.  用户在任务页面查看进度、速度、已上传字节、成功或失败原因。
10. 关闭浏览器不会中断后台上传。

------------------------------------------------------------------------

## 2. 总体设计决策

### 2.1 推荐架构

采用自建前后端分离应用，不将核心功能寄托在第三方文件管理器的插件机制上。

``` text
局域网浏览器
    |
    | HTTP :18888
    v
NAS Web 应用（单一对外入口）
    |
    +-- 前端：React + TypeScript
    |
    +-- 后端：Python FastAPI
           |
           +-- 文件服务
           |     +-- 目录浏览
           |     +-- 搜索、排序、分页
           |     +-- 上传、下载
           |     +-- 新建目录、重命名、移动、删除
           |
           +-- 备份任务服务
           |     +-- 创建任务
           |     +-- 任务状态与进度
           |     +-- 失败重试
           |     +-- 任务日志
           |
           +-- 夸克适配器
           |     +-- 登录/授权状态检查
           |     +-- 远端目录列表
           |     +-- 创建远端目录
           |     +-- 上传文件
           |     +-- 上传目录
           |
           +-- SQLite
                 +-- 用户与会话
                 +-- 备份任务
                 +-- 任务项目
                 +-- 操作审计
    |
    +-- NAS 授权目录（只挂载指定目录）
    |
    +-- 夸克网盘 CLI / 适配器进程
```

### 2.2 技术栈

  ----------------------------------------------------------------------------------
  层                      推荐技术                  说明
  ----------------------- ------------------------- --------------------------------
  前端                    React、TypeScript、Vite   组件化文件管理界面

  UI                      Ant Design 或             表格、树、对话框、通知、进度条
                          Mantine（二选一）

  后端                    Python 3.12+、FastAPI     API、权限校验、文件操作

  数据校验                Pydantic                  API 请求/响应模型

  数据库                  SQLite                    单机部署足够，降低运维成本

  ORM                     SQLAlchemy 2.x            数据模型和事务

  数据库迁移              Alembic                   可重复升级

  后台任务                独立 worker 进程 + SQLite 不依赖浏览器生命周期
                          任务表

  实时进度                Server-Sent Events（SSE） 推送上传进度；断线后可重新查询

  部署                    Docker Compose            便于升级和恢复

  反向代理                FastAPI 同源服务或 Nginx  第一版优先单入口，减少复杂度

  测试                    pytest、Playwright        后端测试与浏览器端到端测试
  ----------------------------------------------------------------------------------

不要为了第一版引入 Kubernetes、Redis、RabbitMQ、PostgreSQL
等额外组件。当前是单台 Ubuntu NAS 的个人/家庭使用场景，SQLite + 独立
worker 足够。

### 2.3 为什么不直接使用 FileBrowser 作为最终产品

通用文件管理器可以作为参考或后续备选，但本项目的核心要求是：文件列表中的选择项能够直接创建夸克备份任务，并提供远端目录选择、任务进度和失败重试。为避免依赖第三方产品未必支持的自定义按钮、内部
API 或非稳定扩展机制，第一版直接实现统一前端和后端。

------------------------------------------------------------------------

## 3. 主机环境与部署约定

### 3.1 已知环境

-   Ubuntu 主机名：`alex-linux`。
-   Ubuntu 版本：此前安装为 Ubuntu 26.04 LTS。
-   Docker 与 Docker Compose：已在 NAS 部署流程中使用。
-   NAS 数据根目录：`/srv/nas`。
-   下载目录：`/srv/nas/07_Downloads`。
-   Compose 文件统一放在 `/opt/docker/compose/`。
-   容器配置统一放在 `/opt/docker/config/`。
-   时区：`Asia/Shanghai`。
-   通过 SSH 维护服务器。

实现前必须通过命令核验实际环境，不得将上述信息视为无需检查的事实。

### 3.2 推荐目录结构

``` text
/opt/docker/
├── compose/
│   └── nas-web/
│       ├── compose.yaml
│       ├── .env
│       └── README-deploy.md
└── config/
    └── nas-web/
        ├── app.yaml
        ├── secrets/
        │   └── quark-auth.json
        ├── data/
        │   └── nas-web.sqlite3
        └── logs/
            └── app.log

/opt/nas-web-src/                 # 可选：源码工作目录
├── backend/
├── frontend/
├── worker/
├── tests/
├── Dockerfile
└── ...
```

实际项目源码可放在 `/opt/nas-web-src`，Compose
和运行配置仍放在既定目录。不要把源码、数据库、日志或凭据放入 `/srv/nas`
数据区。

### 3.3 首次部署前核验

Codex 应先执行只读检查并向用户汇报：

``` bash
hostname
lsb_release -a || cat /etc/os-release
id
id alex
docker --version
docker compose version
ip -brief address
find /srv/nas -mindepth 1 -maxdepth 2 -type d -printf '%M %u:%g %p\n' | sort
ls -ld /srv/nas /srv/nas/07_Downloads
```

如果 `/srv/nas/07_Downloads` 不存在，不要擅自创建或更改 NAS
目录布局；先报告并请求确认。

### 3.4 初始授权目录清单

第一版只开放：

``` yaml
allowed_roots:
  - id: downloads
    name: Downloads
    host_path: /srv/nas/07_Downloads
    read_only: false
    allow_upload: true
    allow_delete: false
```

设计必须支持以后增加其他目录，例如
Movies、TV、Documents，但不要在第一版擅自开放这些目录。目录清单应集中在
`app.yaml`，不允许通过网页请求传入任意服务器绝对路径。

------------------------------------------------------------------------

## 4. 功能需求

### 4.1 登录与会话

-   必须登录后才能访问文件和 API。
-   第一版采用单管理员账户。
-   首次启动通过环境变量或一次性 CLI
    命令初始化管理员，不允许固定默认密码。
-   密码使用 Argon2id 或 bcrypt 哈希保存，不保存明文。
-   登录成功后使用安全随机 Session ID。
-   Cookie 设置 `HttpOnly`、`SameSite=Lax`；局域网 HTTP 环境下不强制
    `Secure`，若未来启用 HTTPS 再开启。
-   提供退出登录。
-   登录失败需限速，避免无限密码尝试。
-   不提供公开注册。
-   不把密码、Cookie、Token 写入日志。

### 4.2 文件浏览

必须支持：

-   左侧显示授权目录入口。
-   进入文件夹、返回上级。
-   面包屑路径导航。
-   文件/文件夹名称、类型、大小、修改时间。
-   文件夹优先或用户可切换排序。
-   按名称、大小、修改时间排序。
-   文件名关键词搜索；第一版搜索范围限定为当前授权根目录。
-   分页或虚拟列表，避免大目录一次性加载全部内容。
-   多选、全选当前页、取消选择。
-   文件夹大小可不在列表中递归计算，避免大目录造成长时间阻塞。

### 4.3 文件操作

第一版支持：

  操作         需求
  ------------ --------------------------------------
  上传         从浏览器上传文件到当前目录
  下载         下载单个文件
  批量下载     可列入第二阶段；不作为第一版验收条件
  新建文件夹   支持
  重命名       支持
  移动         支持在授权根目录内部移动
  删除         默认关闭，需管理员显式开启
  预览         图片、文本、PDF；视频预览可后续增加

安全要求：

-   所有操作都必须在后端重新验证路径和权限。
-   不允许 `../`、绝对路径注入、符号链接逃逸。
-   不允许访问授权根目录以外的文件。
-   默认不跟随符号链接；遇到符号链接时显示为链接或拒绝进入。
-   移动操作不能将文件移出授权根目录。
-   删除功能默认关闭，第一版即使前端隐藏按钮，后端也必须拒绝删除 API。
-   覆盖同名文件必须二次确认；默认不覆盖。
-   上传采用临时文件写入，完整接收后原子重命名，避免中断留下伪完整文件。
-   上传文件名需过滤路径分隔符、控制字符和特殊路径名称。

### 4.4 夸克网盘备份

这是项目的核心差异功能。

#### 用户流程

1.  用户在文件列表勾选一个或多个文件/文件夹。
2.  点击"备份到夸克"。
3.  弹出目标目录选择框。
4.  用户可浏览夸克网盘目录，或新建目标文件夹。
5.  显示本次备份的本地项目清单、总文件数和估算总大小。
6.  用户确认后创建任务。
7.  后台 worker 执行上传。
8.  页面显示任务进度。
9.  任务完成后记录结果；失败时显示具体错误和可重试入口。

#### 备份规则

-   第一版只支持手动触发，不自动监听 qBittorrent。
-   支持单文件、多文件、单目录、多目录。
-   目录上传必须保留相对目录结构。
-   用户必须明确选择远端目标目录。
-   默认不删除本地文件。
-   默认不删除或覆盖远端文件。
-   如果远端存在同名文件，按适配器返回的冲突信息处理；默认跳过并标记冲突，不能静默覆盖。
-   任务创建后即使浏览器关闭也继续执行。
-   页面刷新后可通过任务 ID 恢复任务状态。
-   第一版不做双向同步，不做远端删除，不做自动清理。

#### 任务状态

建议状态机：

``` text
pending
  |
  v
preparing
  |
  v
running ---------> failed
  |                   |
  |                   v
  |                retrying
  |                   |
  v                   v
completed <-----------

running -> cancelled（仅在适配器支持安全取消时）
```

状态字段至少包括：

-   `id`
-   `status`
-   `created_at`
-   `started_at`
-   `finished_at`
-   `source_root_id`
-   `source_relative_path`
-   `remote_target_id`
-   `remote_target_path`
-   `total_files`
-   `completed_files`
-   `failed_files`
-   `total_bytes`
-   `uploaded_bytes`
-   `current_file`
-   `speed_bytes_per_second`
-   `error_summary`

任务子项目表需记录每个文件的状态、大小、已上传字节、远端 ID（如果 CLI
能提供）、错误信息和重试次数。

#### 夸克 CLI 适配器要求

不要在业务代码中直接散落 CLI 命令。定义统一接口：

``` python
class CloudDriveAdapter:
    def check_auth(self) -> AuthStatus: ...
    def list_directory(self, remote_path: str) -> list[RemoteEntry]: ...
    def create_directory(self, parent_path: str, name: str) -> RemoteEntry: ...
    def upload_file(
        self,
        local_path: Path,
        remote_directory: str,
        progress_callback,
    ) -> UploadResult: ...
    def upload_directory(
        self,
        local_path: Path,
        remote_directory: str,
        progress_callback,
    ) -> UploadResult: ...
```

实现 `QuarkCliAdapter`，通过配置指定可执行文件路径和命令参数映射。CLI
具体命令、认证方式、输出格式必须先在目标 Ubuntu
主机上验证，再实现适配器。不要根据未经核验的 README 示例假定 CLI
命令和输出格式一定稳定。

优先评估夸克官方 Linux Skill/CLI；如其不适合无人值守后台调用，再评估社区
CLI。社区项目使用 Cookie 时，必须将 Cookie 当作密码级秘密管理。不要把
Cookie 放入前端、数据库普通字段、Compose
明文环境变量、命令行参数或日志。应通过权限为 `0600` 的秘密文件或 Docker
secret 提供给 worker。

如果 CLI
无法提供可靠进度，至少实现文件级进度：当前文件、已完成文件数、总文件数；不得伪造精确字节进度。

#### 夸克授权与凭据安全

-   授权过程由管理员在服务器侧完成。
-   凭据只存在于 `/opt/docker/config/nas-web/secrets/`。
-   目录权限建议 `0700`，凭据文件 `0600`。
-   任何 API 响应都不得返回凭据内容。
-   日志必须对 Cookie、Token、Authorization Header 做脱敏。
-   不能将凭据提交到 Git。
-   提供"检查夸克授权状态"功能，不提供网页查看原始凭据。
-   如果官方工具使用 OAuth，应优先使用 OAuth。
-   如果使用 Cookie 方案，需明确提示其可能过期，支持管理员重新授权。
-   不允许绕过夸克的访问控制或分享限制。

### 4.5 任务中心

提供单独的"备份任务"页面：

-   当前运行任务。
-   等待任务。
-   已完成任务。
-   失败任务。
-   任务详情。
-   每个文件的上传状态。
-   上传速度与进度。
-   失败原因。
-   重试失败项或整个任务。
-   取消任务（仅在安全支持时）。
-   任务记录保留策略可配置；默认保留 90 天。
-   清理任务记录不得删除本地源文件或远端文件。

------------------------------------------------------------------------

## 5. 前端页面设计

### 5.1 页面布局

``` text
+------------------------------------------------------------+
| Ubuntu NAS 文件管理                         用户 | 退出     |
+----------------------+-------------------------------------+
| 导航                 | 当前路径：Downloads / Complete        |
|                      |                                     |
| 文件管理             | [搜索] [新建文件夹] [上传] [备份到夸克] |
| 备份任务             |                                     |
| 系统设置             | [ ] 文件名       大小   修改时间   操作 |
|                      | [ ] movie.mkv    8GB   ...          |
| 授权目录             | [ ] folder/       --   ...          |
| - Downloads          | [ ] archive.zip  2GB   ...          |
|                      |                                     |
+----------------------+-------------------------------------+
```

### 5.2 备份对话框

至少包含：

-   选中的本地文件/目录列表。
-   选中项目总数。
-   估算总大小。
-   夸克远端目录浏览器。
-   新建远端目录。
-   目标路径显示。
-   冲突策略说明（默认跳过，不覆盖）。
-   "开始备份"按钮。
-   取消按钮。

### 5.3 视觉要求

-   桌面浏览器优先，兼容常见 1366px 及以上分辨率。
-   采用简洁 NAS 管理后台风格。
-   左侧固定导航，右侧文件列表。
-   操作按钮使用明确中文名称。
-   危险操作采用二次确认。
-   上传进度必须清楚区分"当前文件进度"和"整个任务进度"。
-   不使用仅有图标、无文字且含义不明确的主要操作按钮。
-   适配窄屏，但第一版不以手机端复杂文件操作为目标。

------------------------------------------------------------------------

## 6. 后端 API 设计

统一 API 前缀：`/api/v1`。

### 6.1 会话

  方法   路径             说明
  ------ ---------------- ----------
  POST   `/auth/login`    登录
  POST   `/auth/logout`   退出
  GET    `/auth/me`       当前用户

### 6.2 授权目录和文件

  -----------------------------------------------------------------------------------
  方法                    路径                    说明
  ----------------------- ----------------------- -----------------------------------
  GET                     `/roots`                获取授权根目录列表

  GET                     `/files`                列出目录内容，参数
                                                  root、path、page、page_size、sort

  GET                     `/files/info`           文件元信息

  GET                     `/files/download`       下载文件

  POST                    `/files/upload`         上传文件

  POST                    `/files/mkdir`          新建目录

  POST                    `/files/rename`         重命名

  POST                    `/files/move`           移动

  DELETE                  `/files`                删除；第一版默认后端拒绝
  -----------------------------------------------------------------------------------

文件 API 使用 `root_id + relative_path`，绝不接受任意主机绝对路径。

### 6.3 夸克 API

  方法   路径                         说明
  ------ ---------------------------- --------------------
  GET    `/cloud/quark/status`        检查授权状态
  GET    `/cloud/quark/directories`   列出远端目录
  POST   `/cloud/quark/directories`   创建远端目录
  POST   `/backups`                   创建备份任务
  GET    `/backups`                   任务列表
  GET    `/backups/{id}`              任务详情
  GET    `/backups/{id}/events`       SSE 进度流
  POST   `/backups/{id}/retry`        重试失败项
  POST   `/backups/{id}/cancel`       取消任务（支持时）

### 6.4 创建备份任务请求示例

``` json
{
  "items": [
    {
      "root_id": "downloads",
      "relative_path": "Complete/example.mkv",
      "type": "file"
    },
    {
      "root_id": "downloads",
      "relative_path": "Complete/example-folder",
      "type": "directory"
    }
  ],
  "remote_directory_id": "remote-folder-id",
  "conflict_policy": "skip"
}
```

后端必须重新检查每个源路径是否属于授权根目录，且文件类型与实际类型一致。客户端传来的
`type` 不能作为权限依据。

------------------------------------------------------------------------

## 7. 文件路径与安全设计

这是项目最重要的安全部分之一。

### 7.1 路径解析

后端只接受：

-   `root_id`：必须存在于 `allowed_roots` 配置。
-   `relative_path`：相对于该授权根目录的路径。

路径解析必须：

1.  拒绝空路径中的异常控制字符。
2.  拒绝绝对路径。
3.  拒绝 `..` 路径段。
4.  将路径解析为规范路径。
5.  验证规范路径仍处于授权根目录内部。
6.  检查路径各级是否存在符号链接；第一版拒绝通过符号链接访问。
7.  对读写操作使用适当的竞态防护；关键文件操作尽可能使用目录文件描述符和
    `openat`/`O_NOFOLLOW` 等安全机制，避免 TOCTOU 路径替换问题。

仅使用字符串 `startswith()` 判断路径是否在根目录内是不安全的。

### 7.2 Docker 权限

-   容器不得使用 root 运行。
-   只挂载明确授权的 NAS 子目录。
-   容器内只需要访问 `/nas/Downloads` 等挂载点。
-   数据库、配置、日志使用独立挂载目录。
-   不挂载 `/var/run/docker.sock`。
-   不使用 `privileged: true`。
-   不开放不需要的 Linux capabilities。
-   如果宿主机目录权限导致写入失败，应通过核对 UID/GID、ACL
    或最小范围调整解决，禁止用 `chmod -R 777` 作为通用修复。

### 7.3 网络

-   服务端口只绑定 NAS 的 LAN IP，例如 `192.168.1.x:8088`。
-   不绑定 `0.0.0.0`，除非用户明确要求。
-   不配置公网反向代理、Cloudflare Tunnel 或公网端口映射。
-   若主机防火墙启用，只允许可信局域网网段访问 8088。
-   不假定 LAN 网段必然是 `192.168.1.0/24`，部署时核验实际网段。
-   CORS 默认同源，不允许 `*`。
-   API 使用 CSRF 防护或严格 SameSite Cookie 策略。

------------------------------------------------------------------------

## 8. 数据库设计

建议使用 SQLite，开启 WAL 模式。

### 8.1 表结构

#### users

-   `id`
-   `username`
-   `password_hash`
-   `created_at`
-   `last_login_at`
-   `is_active`

#### backup_jobs

-   `id`
-   `status`
-   `remote_directory_id`
-   `remote_directory_path`
-   `conflict_policy`
-   `total_files`
-   `completed_files`
-   `failed_files`
-   `total_bytes`
-   `uploaded_bytes`
-   `current_file`
-   `speed_bytes_per_second`
-   `error_summary`
-   `created_at`
-   `started_at`
-   `finished_at`

#### backup_job_items

-   `id`
-   `job_id`
-   `root_id`
-   `source_relative_path`
-   `source_type`
-   `file_size`
-   `uploaded_bytes`
-   `status`
-   `remote_file_id`
-   `error_message`
-   `retry_count`
-   `created_at`
-   `updated_at`

#### audit_logs

-   `id`
-   `user_id`
-   `action`
-   `root_id`
-   `relative_path`
-   `result`
-   `created_at`

审计日志不记录文件内容、密码、Cookie 或 Token。

### 8.2 数据库可靠性

-   启用 SQLite WAL。
-   使用事务创建任务及其子项目。
-   worker 使用原子任务领取机制，避免两个 worker 重复执行同一任务。
-   服务启动时检查未完成任务。
-   对于进程意外退出的 `running`
    任务，启动后标记为可恢复/待重试，不能直接假定已上传或未上传。
-   备份数据库文件时应使用 SQLite backup API
    或先执行一致性检查，不能只在数据库运行中随意复制主文件而忽略 WAL。

------------------------------------------------------------------------

## 9. 后台任务与进度处理

### 9.1 进程结构

第一版建议同一 Docker Compose 项目中运行两个服务：

-   `nas-web`：FastAPI + 前端静态资源。
-   `nas-worker`：读取 SQLite 任务队列并执行上传。

两者共享应用配置、数据库和只读/读写 NAS 挂载。只有 worker
需要访问夸克凭据。

### 9.2 worker 要求

-   任务按创建顺序执行，第一版并发上传数设为
    1，避免占满带宽或触发网盘限制。
-   单个目录任务逐文件上传，保留相对路径。
-   每个文件开始、进度变化、成功、失败均更新数据库。
-   不将整个大文件读入内存。
-   进度更新需节流，例如每 0.5--1 秒或每增加一定字节更新一次。
-   worker 退出时尽可能安全停止当前上传。
-   进程重启后恢复可恢复任务。
-   对网络临时错误使用有限次数指数退避。
-   认证失效时暂停队列并提示重新授权，不要无限重试。
-   磁盘文件消失或发生变化时，将对应项目标记为失败，并记录原因。
-   上传前后可比较本地文件大小和修改时间，检测源文件在上传过程中变化的情况。
-   对 CLI 的 stdout/stderr 做大小限制并脱敏，避免日志无限增长。

### 9.3 进度定义

任务总进度优先根据已完成项目数和已上传字节计算：

-   有可靠字节进度时：`uploaded_bytes / total_bytes`。
-   没有可靠字节进度时：显示"已完成文件数 / 总文件数"。
-   不得把文件已交给 CLI 误报为上传成功。
-   最终成功必须由 CLI 返回成功结果或远端核验结果确认。

------------------------------------------------------------------------

## 10. Compose 部署设计

Codex 实现时创建完整 Compose
文件。建议服务形态如下（这是结构要求，不是可直接运行的最终文件；镜像和启动命令应由项目
Dockerfile 实际构建）：

``` yaml
services:
  nas-web:
    build:
      context: /opt/nas-web-src
      target: web
    container_name: nas-web
    restart: unless-stopped
    user: "${PUID}:${PGID}"
    ports:
      - "${LAN_BIND_IP}:8088:8080"
    volumes:
      - /opt/docker/config/nas-web:/app/data
      - /srv/nas/07_Downloads:/nas/Downloads:rw
    environment:
      TZ: Asia/Shanghai
      APP_CONFIG: /app/data/app.yaml
      DATABASE_URL: sqlite:////app/data/data/nas-web.sqlite3
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL

  nas-worker:
    build:
      context: /opt/nas-web-src
      target: worker
    container_name: nas-worker
    restart: unless-stopped
    user: "${PUID}:${PGID}"
    volumes:
      - /opt/docker/config/nas-web:/app/data
      - /srv/nas/07_Downloads:/nas/Downloads:rw
    environment:
      TZ: Asia/Shanghai
      APP_CONFIG: /app/data/app.yaml
      DATABASE_URL: sqlite:////app/data/data/nas-web.sqlite3
      QUARK_CREDENTIAL_FILE: /app/data/secrets/quark-auth.json
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
```

注意：

1.  这是设计示例，Codex 必须根据实际
    Dockerfile、用户权限和数据库路径完善。
2.  不要在 web 容器暴露夸克凭据；最好仅 worker 挂载 secrets 子目录。
3.  SQLite 数据库被 web 和 worker 共享时，必须实现 SQLite busy
    timeout、WAL 和事务处理。
4.  `PUID`、`PGID`、`LAN_BIND_IP` 应在 `.env` 中配置。
5.  不要把真实密码、Cookie、Token 写入 Compose 文件或 Git。
6.  端口应绑定实际 LAN IP。
7.  不要使用 `latest` 作为生产部署的唯一版本约束；构建后固定镜像 tag
    或记录 Git commit。

------------------------------------------------------------------------

## 11. 配置文件示例

`/opt/docker/config/nas-web/app.yaml`：

``` yaml
app:
  name: Ubuntu NAS
  timezone: Asia/Shanghai
  listen_host: 0.0.0.0
  listen_port: 8080
  log_level: INFO

security:
  session_ttl_hours: 12
  login_rate_limit:
    attempts: 5
    window_seconds: 300
  allow_delete: false
  allow_symlinks: false
  cors_origins: []

storage:
  allowed_roots:
    - id: downloads
      name: Downloads
      container_path: /nas/Downloads
      enabled: true
      read_only: false
      allow_upload: true
      allow_delete: false

quark:
  enabled: true
  adapter: cli
  executable: /usr/local/bin/quark
  credential_file: /app/data/secrets/quark-auth.json
  max_concurrent_uploads: 1
  conflict_policy: skip
  retry:
    max_attempts: 3
    initial_delay_seconds: 5
    max_delay_seconds: 120

tasks:
  retention_days: 90
  progress_update_interval_seconds: 1
```

配置校验失败时服务必须启动失败并输出清晰错误，不能静默忽略错误配置。

------------------------------------------------------------------------

## 12. 日志、监控和备份

### 12.1 日志

-   使用结构化日志。
-   按级别输出 INFO/WARNING/ERROR。
-   不记录认证凭据。
-   记录任务 ID、文件相对路径、操作类型和结果。
-   对异常堆栈进行合理记录，但先脱敏。
-   Docker 日志设置轮转限制，避免日志撑满系统盘。

### 12.2 健康检查

至少提供：

-   `GET /health/live`：进程是否存活。
-   `GET /health/ready`：数据库、配置、授权目录是否可用。
-   夸克授权状态单独展示，不应因为网盘暂时不可用就让整个文件管理器变成不可用。

### 12.3 需要备份的数据

必须备份：

-   SQLite 数据库。
-   `app.yaml`。
-   管理员密码哈希和会话相关配置（如适用）。
-   夸克授权凭据（安全、加密地备份，或保留重新授权能力）。
-   部署 `.env`（其中不得含明文秘密）。

不得把 NAS 原始文件复制到应用配置备份目录。

------------------------------------------------------------------------

## 13. 测试计划与验收标准

### 13.1 部署验收

-   [ ] `docker compose config` 无错误。
-   [ ] `nas-web` 和 `nas-worker` 均正常运行。
-   [ ] 局域网电脑可访问网页。
-   [ ] 非授权目录不能访问。
-   [ ] 服务重启后数据库和配置保留。
-   [ ] 端口没有绑定到非预期公网接口。

### 13.2 安全验收

-   [ ] 未登录访问 API 返回 401。
-   [ ] 不能通过 `../` 访问授权根目录以外内容。
-   [ ] 不能通过绝对路径读取系统文件。
-   [ ] 不能通过符号链接逃逸授权目录。
-   [ ] 默认删除 API 被拒绝。
-   [ ] 上传覆盖同名文件必须确认或拒绝。
-   [ ] 日志不出现 Cookie、Token、密码。
-   [ ] 容器不是 root。
-   [ ] 容器没有 Docker socket 和 privileged 权限。

### 13.3 文件操作验收

-   [ ] 正常列出 Downloads。
-   [ ] 能进入子目录和返回上级。
-   [ ] 排序和搜索正常。
-   [ ] 能上传小文件。
-   [ ] 能下载文件并校验 SHA-256。
-   [ ] 能新建文件夹。
-   [ ] 能重命名。
-   [ ] 能在授权目录内部移动。
-   [ ] 上传中断不会留下看似完整的目标文件。

### 13.4 夸克备份验收

-   [ ] 能显示夸克授权状态。
-   [ ] 能浏览远端目录。
-   [ ] 能选择远端目录。
-   [ ] 能备份单文件。
-   [ ] 能备份多文件。
-   [ ] 能备份目录并保留层级。
-   [ ] 远端同名文件按 skip 策略处理。
-   [ ] 浏览器关闭后任务继续。
-   [ ] 页面刷新后能恢复进度。
-   [ ] 失败任务可查看原因。
-   [ ] 失败项可以重试。
-   [ ] 夸克授权过期时能明确提示，不无限重试。
-   [ ] 备份不会删除本地文件。

### 13.5 性能验收

建议测试：

-   1000 个文件的目录浏览。
-   单个 1GB 以上文件的备份。
-   多层目录备份。
-   浏览器刷新、断网、服务重启后的任务恢复。
-   上传时同时浏览和下载其他文件。

不要预设或承诺特定夸克上传速度，速度受网络、账号、服务端策略和 CLI
能力影响。

------------------------------------------------------------------------

## 14. 实施阶段与 Codex 工作顺序

Codex
不应一次性生成全部代码后直接部署。按阶段实施，每阶段完成测试后再进入下一阶段。

### 阶段 0：环境审计

1.  检查 Ubuntu、Docker、Compose、UID/GID。
2.  检查 LAN IP 和子网。
3.  检查 `/srv/nas/07_Downloads` 权限。
4.  输出审计报告。
5.  不修改系统，不创建或删除 NAS 文件。

交付：`docs/environment-audit.md`

### 阶段 1：项目骨架与文件浏览

1.  创建前后端项目。
2.  创建配置加载和校验。
3.  实现登录。
4.  实现授权根目录列表。
5.  实现目录浏览、排序、搜索、分页。
6.  实现路径安全模块。
7.  编写单元测试。

交付：可登录、只读浏览的网页。

### 阶段 2：文件操作

1.  上传。
2.  下载。
3.  新建目录。
4.  重命名。
5.  移动。
6.  默认拒绝删除。
7.  编写路径穿越、符号链接、越权测试。

交付：基础文件管理器。

### 阶段 3：夸克 CLI 技术验证

在正式接入前单独验证：

1.  选择官方工具或社区 CLI。
2.  核实 Linux x86_64 兼容性。
3.  核实安装方法和版本。
4.  完成授权。
5.  列出远端目录。
6.  创建测试远端目录。
7.  上传一个小文件。
8.  上传一个较大文件。
9.  核实进度输出、失败码和远端结果。
10. 评估 Cookie/OAuth 凭据保护。

交付：`docs/quark-cli-validation.md` 和适配器命令映射。

如果 CLI 无法可靠工作，停止集成并报告，不要伪造成功结果。

### 阶段 4：夸克备份任务

1.  实现 CLI adapter。
2.  实现任务数据库模型。
3.  实现 worker。
4.  实现远端目录浏览。
5.  实现备份对话框。
6.  实现进度页面和 SSE。
7.  实现失败重试。
8.  测试浏览器关闭和容器重启后的任务行为。

交付：完整手动备份闭环。

### 阶段 5：部署加固

1.  Compose 最小权限配置。
2.  LAN IP 绑定。
3.  管理员初始化流程。
4.  日志脱敏和轮转。
5.  数据库备份脚本。
6.  安装、升级、回滚文档。
7.  完整验收测试。

交付：可长期运行的部署版本。

------------------------------------------------------------------------

## 15. 运维命令要求

项目应提供清晰的运维文档，至少包括：

``` bash
# 查看状态
docker compose ps

# 查看 Web 日志
docker compose logs -f nas-web

# 查看 Worker 日志
docker compose logs -f nas-worker

# 重启
docker compose restart

# 更新
docker compose build --pull
docker compose up -d

# 停止
docker compose down

# 查看健康状态
curl -fsS http://127.0.0.1:8088/health/ready
```

注意：如果服务仅绑定 LAN IP，健康检查应使用容器内部地址或实际 LAN
IP，不能假定 `127.0.0.1:8088` 一定可访问。

项目还必须提供：

-   首次安装说明。
-   管理员密码重置说明。
-   夸克重新授权说明。
-   数据库备份与恢复说明。
-   升级步骤。
-   回滚步骤。
-   完全卸载步骤（卸载应用不得删除 `/srv/nas` 数据）。

------------------------------------------------------------------------

## 16. 明确不在第一版范围内

以下功能不应擅自加入第一版：

-   qBittorrent 下载完成自动备份。
-   自动定时备份。
-   多用户和复杂角色系统。
-   公网访问。
-   Tailscale 集成。
-   多网盘支持。
-   夸克网盘双向同步。
-   远端删除。
-   自动覆盖远端文件。
-   自动删除本地已备份文件。
-   AI 文件分类。
-   视频转码和媒体库管理。

保持第一版范围可控，优先交付稳定的文件管理和手动夸克备份闭环。

------------------------------------------------------------------------

## 17. 给 Codex CLI 的执行指令

将本设计文档放入项目目录，例如：

``` text
/opt/nas-web-src/docs/design-spec.md
```

然后在项目目录启动 Codex CLI，并提交以下任务：

> 请严格依据 `docs/design-spec.md` 实现 Ubuntu NAS
> 网页文件管理与夸克网盘手动备份系统。
>
> 执行规则：
>
> 1.  首先阅读完整设计文档，检查当前工作目录、系统环境和已有文件。
> 2.  先输出实施计划、技术风险和需要核验的外部 CLI
>     能力，不要立即大规模写代码。
> 3.  按文档第 14 节阶段 0 至阶段 5 顺序实现。
> 4.  每个阶段完成后运行测试，修复失败项，并更新阶段报告。
> 5.  不得擅自扩大 `/srv/nas` 的访问范围。
> 6.  不得对 NAS 数据执行递归 chmod、chown、删除或格式化操作。
> 7.  不得把夸克 Cookie、Token、密码写入代码、Git、前端或日志。
> 8.  夸克 CLI
>     的安装方式、认证方式、命令、输出和进度能力必须先实际验证；未经验证不得编写虚构适配逻辑。
> 9.  不得以"前端隐藏按钮"代替后端权限检查。
> 10. 不得将后台上传绑定到浏览器请求生命周期。
> 11. 不得在测试未通过时宣称阶段完成。
> 12. 每次部署前先执行配置检查，并备份应用数据库和配置。
> 13. 所有修改必须限制在项目目录、`/opt/docker/compose/nas-web` 和
>     `/opt/docker/config/nas-web`；只有经用户确认后才允许修改其他系统配置。
> 14. 最终交付源码、Docker
>     Compose、配置示例、测试、部署文档、运维文档和已知限制清单。
>
> 首次只执行阶段 0 环境审计，并向我报告结果，等待确认后再进入阶段 1。

------------------------------------------------------------------------

## 18. 最终交付验收定义

项目只有同时满足以下条件，才算第一版完成：

1.  局域网浏览器能够登录 Ubuntu NAS 网页。
2.  只能浏览配置允许的 NAS 目录。
3.  文件浏览、上传、下载、新建、重命名和移动功能正常。
4.  默认禁止删除，并且后端强制执行。
5.  文件列表可以勾选文件和目录。
6.  点击"备份到夸克"可以选择远端目录并创建任务。
7.  后台任务在浏览器关闭后仍继续。
8.  进度、失败原因和重试结果可查看。
9.  服务重启后任务记录不丢失。
10. 夸克凭据不泄露到前端或日志。
11. Docker 容器以非 root 身份运行。
12. 未授权目录、路径穿越和符号链接逃逸测试通过。

**项目优先级：数据安全 \> 备份可靠性 \> 文件操作正确性 \> 易用性 \>
外观。**
