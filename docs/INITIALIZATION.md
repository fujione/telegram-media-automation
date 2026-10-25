# 内部初始化与旧开发环境

对外安装仅使用 [Docker 部署与首次 Web 配置](../deploy/README.md)。下文保留开发和旧安装的初始化工具说明，不要求公开用户安装 Python/uv。

## 推荐流程

新安装优先使用初始化器，而不是手工编辑敏感配置：

```powershell
uv sync --locked --all-extras
uv run mediactl init
uv run mediactl doctor
uv run mediactl serve
```

初始化器依次要求：

1. 创建本地 Web 管理员用户名和密码；
2. 选择界面语言与 IANA 时区；
3. 选择状态、备份、未完成下载、下载收件箱、电影库和电视剧库目录；
4. 选择媒体归档策略（移动、硬链接或复制）；
5. 输入 Telegram API ID、隐藏输入 API Hash；
6. 可选隐藏输入 Telegram 代理 URL。

初始化器会先创建并实际写入探测所有目录，再生成 `.env`、独立 secret 文件和 SQLite
数据库。任一必需目录不可写、时区无效、目录冲突或凭据不合法时，初始化会失败，不会
留下可误启动的 `.env`。

管理员密码只保存带随机盐的 `scrypt` 哈希。Telegram API Hash、内部自动化 Token 和
代理认证信息保存在 Git 忽略的 `secrets/` 中；初始化结束不会打印内部 Token。

界面会在认证前读取只含 `language` 和 `timezone` 的公开展示配置，因此登录页也会使用
所选语言。`zh-CN` 和 `en-US` 覆盖登录、控制台、任务状态及操作文案；事件与备份时间
使用所选 IANA 时区显示。该端点不会返回路径、凭据或运行密钥。

初始化器会写入默认质量排序：`2160p > 1080p > 720p`、`zh > en`，默认不硬性限制
分辨率或语言，也不限制文件大小。高级部署可在 `.env` 配置
`APP_QUALITY_PREFERRED_*`、`APP_QUALITY_ALLOWED_*`、`APP_QUALITY_PREFER_HDR` 和
`APP_QUALITY_MAX_FILE_SIZE`。允许列表是硬过滤，偏好列表只控制排序；修改后需重启。

电视剧追更默认每 300 秒扫描一次，由 `APP_SUBSCRIPTION_POLL_INTERVAL` 配置。新建或恢复
订阅会立即唤醒扫描；日常下载队列仍保持独立的短轮询响应。

Web 中的频道标识可直接粘贴 Telegram 频道详情里的完整公开链接，例如
`https://t.me/channel_name`。系统也接受 `@channel_name`、`channel_name`、`-100` 开头的
数字 ID，以及能转换为数字 ID 的 `https://t.me/c/.../...` 消息链接。`t.me/+...` 和
`joinchat` 邀请链接不是稳定频道身份，不能作为搜索范围；应先加入频道，再填写公开名或
数字 ID。

创建电视剧订阅时，“需补集数”接受 `2, 4-6`。勾选“补齐后继续追更”时，系统先处理这些
缺集，再从“下一集”继续；取消勾选时只创建缺集任务，全部入队后自动停止该订阅。

## 自定义目录

交互提示可以直接填写绝对路径。也可以用参数预填，仍由终端安全提示收集密码和 API
Hash：

```powershell
uv run mediactl init `
  --timezone Asia/Shanghai `
  --data-dir D:\MediaAutomation\data `
  --backup-dir D:\MediaAutomation\backups `
  --incomplete-dir E:\Downloads\incomplete `
  --inbox-dir E:\Downloads\inbox `
  --movie-dir Z:\Media\Movies `
  --tv-dir Z:\Media\TV `
  --archive-mode hardlink
```

未完成目录、收件箱、电影库和电视剧库必须互不相同。初始化只验证当前机器能写入路径；
网络盘断线、NAS 配额和容器用户权限仍应由 `mediactl doctor` 在每次部署后复核。

归档策略的语义是固定的：

- `move`（默认）：同卷原子移动；跨卷复制、校验并落盘后才删除 inbox 源文件。
- `hardlink`：保留 inbox 路径且不复制文件数据，仅适用于同一文件系统。
- `copy`：校验复制并保留 inbox 源文件，需要为两份文件预留空间。

系统不会把不可用的 `hardlink` 静默降级为复制。改变策略时修改
`APP_ARCHIVE_MODE` 并重启服务；该值影响之后执行的归档，不改写已经归档的文件。

## Docker Compose

Compose 初始化使用相同向导：

```powershell
uv run mediactl init --docker
docker compose up -d --build
```

`.env` 中的 `TMA_HOST_*` 变量描述宿主机目录，`APP_*` 变量描述容器内固定路径。这样电影
库、电视剧库、下载和状态数据可以分别位于不同磁盘或 NAS 挂载点。

Compose 默认只发布到 `127.0.0.1:8080`。需要从可信局域网直接访问时，在 `.env` 设置
`TMA_BIND_ADDRESS=0.0.0.0`；需要避开宿主机端口冲突时设置 `TMA_HOST_PORT`。例如
`TMA_HOST_PORT=18082` 会将宿主机的 18082 映射到容器的 8080。公开绑定前必须创建管理员
账号，并优先通过带 TLS 的反向代理访问。

Compose 以非 root 用户运行并将容器根文件系统设为只读。在 Linux/NAS 上，初始化器会把
当前宿主机用户的 UID/GID 写入 `TMA_UID`、`TMA_GID`，使容器只对明确挂载的下载、媒体库
与状态目录拥有该用户原本具备的权限。手工迁移项目时可用 `id -u`、`id -g` 核对这两个值。

若 NAS 无法访问 Docker Hub，可只为本项目设置
`TMA_PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim`。这不会修改 Docker 守护进程
或其他 Compose 项目的镜像源。

## Web 首次使用

打开管理页并使用初始化时创建的账号密码登录。登录控制台和 Telegram 用户登录是两层
独立状态：

- Web 管理员只控制谁可以操作本产品；
- Telegram Session 控制后台是否已登录 Telegram；
- 换浏览器只需重新登录本产品，不会重新发送 Telegram 验证码；
- 只有 Telegram Session 被删除、失效或撤销时才重新进行验证码及两步验证。

早期版本升级且尚无管理员凭据时，从服务主机的 `127.0.0.1` 打开 WebUI 可进行一次性
迁移。也可以在安全终端运行：

```powershell
uv run mediactl set-admin
```

Compose 宿主机使用：

```powershell
uv run mediactl set-admin --credentials-file secrets/admin_credentials.json
```

不要把管理员密码、Telegram 验证码、API Hash 或内部 Token 发到聊天、Issue 或截图中。
