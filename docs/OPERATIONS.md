# 运维：升级、备份与恢复

## 业务健康与代理故障

`/health/live` 只说明 HTTP 进程存活。`/health/ready` 同时检查数据库、服务配置、Telegram 授权与 worker；业务未就绪返回 503，不能用容器进程仍在运行推断搜索和追更正常。

开发版本的 Compose 健康检查使用 `/health/ready`，启动宽限 60 秒、探针超时 10 秒。首次安装未完成配置或 Telegram 登录时可能显示 unhealthy，但 Web 管理页仍可访问；重启策略不会仅因 unhealthy 自动重启容器。

就绪响应增加 `telegram_status`：`authorized`、`login_required`、`not_configured`、`telegram_timeout`、`telegram_proxy_failed`、`telegram_connection_failed`、`telegram_rate_limited` 或 `telegram_operation_failed`。这些是稳定状态/原因码，不返回异常原文、代理地址或凭据。设置页用实际连接状态替代仅显示“已配置”。

代理端口能建立 TCP 连接，并不说明代理出口可用。出现持续连接重置或扫描全部超时时，应继续检查代理选中节点、路由、订阅有效期，以及实际出口连通性；部分服务商会把过期订阅替换成不可用的占位节点。不要仅凭网络错误删除 Telegram Session 或反复发送登录验证码。

切换出口前保留原选择并验证候选节点；恢复后复核 Telegram 授权、实际搜索、逐频道扫描和任务计数。代理支持保存节点选择时，应明确启用持久化，并在重启后验证，避免重启又选回不可用节点。代理配置、订阅地址与诊断日志应保存在私有位置，不进入公开仓库。

## 追更记录与诊断

从 rc3/schema14 起，最近尝试、成功、新增任务与扫描结果保存在数据库中。每订阅最多50条历史，全局最多5000条，展示最近30天；写入时清理超限和过期历史。进程重启前未完成的扫描显示中断，等待后续重试。

追更页面的“立即检查”与周期扫描共用同一 worker，重复请求会合并，不绕过频道退避或 Telegram FloodWait；全局禁止重试时间保存在数据库，重启仍有效。暂停订阅需先恢复才能检查。长期未更新按实际任务/入库活动判断，不根据编辑或暂停时间判断完结。

设置页可预览诊断信息，再下载当前预览。报告只允许输出版本、稳定状态码、时间、数量、磁盘空间和匿名扫描摘要，排除原始日志、Session、API密钥、手机号、代理、剧名、频道和路径。诊断接口需要现有管理员认证，响应禁止缓存。

rc3 将数据库升级到14；rc2只支持13，不能直接用于回滚已升级的数据库。升级前备份，遵守下方不兼容schema的恢复步骤，绝不让旧镜像写新数据库。

## 备份

API 可以保持运行；命令使用 SQLite 在线 Backup API 获取一致快照。默认不包含任何凭据：

```sh
docker compose exec api mediactl backup /backups/product.zip
```

需要完整灾难恢复时，可显式加入 Telegram Session。该归档属于敏感文件，应加密保存：

```sh
docker compose exec api mediactl backup /backups/product-with-session.zip --include-session
```

API Token、API Hash 和 `.env` 始终不进入备份。
备份格式清单包含数据库及可选 Session 的 SHA-256；恢复会先写临时文件并校验，再原子
放入目标位置。

Web 管理页也可以创建、列出和下载同样格式的数据库备份。Web 入口始终排除 Telegram
Session；需要完整灾难恢复时只能显式使用下方 CLI 参数。

## 恢复

先保留当前数据卷和备份，使用独立的新数据卷恢复，拒绝覆盖已有数据库或 Session。下面的镜像应替换为已经发布且兼容备份 schema 的固定版本，`YOUR_BACKUP_VOLUME` 替换为保存备份的实际卷名：

```sh
docker run --rm --read-only --tmpfs /tmp \
  --mount source=tma-restore-data,target=/data \
  --mount source=YOUR_BACKUP_VOLUME,target=/backups,readonly \
  ghcr.io/fujione/telegram-media-automation:0.1.0rc4 \
  mediactl restore /backups/product.zip --root /
```

该命令将数据库放入新的 `/data/media_automation.sqlite3`；新数据卷必须为空且对容器用户可写。先在独立部署中挂载此卷验证任务、订阅、游标和记录，再决定是否切换。默认备份没有管理员、Telegram API 和 Session，须另行保护原配置卷，或完成首次配置和本人账号登录。原始媒体仍使用原媒体卷，不在数据库恢复中复制或删除。

## 升级与回滚

1. 备份数据库；需要时包含 Session。
2. 更新 Compose 中的固定镜像标签，执行 `docker compose pull`，再执行 `docker compose up -d`。
3. 启动时应用只会执行向前迁移；数据库版本高于程序时拒绝启动。
4. 健康检查通过后再恢复外部流量。
5. 回滚前核对旧镜像支持的 schema。若兼容当前 schema，保留当前数据库、Session 和任务进度，仅回滚程序；不要默认恢复旧数据库。若旧程序不支持当前 schema，停止服务并保存当前数据，再在隔离目录恢复升级前备份并验证，确认可接受备份之后记录及进度的损失后才切换。旧程序不能写入不支持的新 schema。

媒体目录不打包进应用备份，应由 NAS 快照或独立文件备份策略保护。

## 归档策略

`APP_ARCHIVE_MODE` 支持 `move`、`hardlink` 和 `copy`。默认 `move` 在归档成功后移除
inbox 文件；`hardlink` 与 `copy` 都保留 inbox 文件，其中硬链接要求源和媒体库在同一
文件系统，复制则消耗双份空间。硬链接跨设备会明确报错，不会静默复制。目标路径已有
不同内容时任何策略都拒绝覆盖；内容相同则按哈希去重。

调整该配置后需要重启服务。使用 `hardlink` 时，NAS 快照、清理工具和容量统计可能按
各自规则计算链接数，正式部署前应在目标文件系统验证。

## 替换已有媒体

先创建仅下载任务，等待 `downloaded`，再通过 `GET /api/v1/library/files` 核对旧文件。
`POST /api/v1/tasks/{task_id}/replace` 接收与入库相同的媒体身份和可选 `target_path`。
媒体身份必须确认；显式目标只能是对应媒体库内的普通文件，扩展名须与新文件一致。
不指定路径时按标准入库命名定位，目标不存在则拒绝执行。

替换前核对下载大小、暂存副本 SHA-256 和旧版备份，再原子提交。旧文件保存在对应媒体库
根下的隐藏 `.tma-replaced-backups` 目录中，备份位置记录在任务事件里，不自动清理。
任务资源记录保留新下载的 Telegram 频道与消息来源；既有任务是历史记录。

Compose 的 `TMA_HOST_TV_DIR` 和 `TMA_HOST_MOVIE_DIR` 必须指向影视应用实际扫描的目录。
容器内 `imported` 仅代表文件已归档，不能代替影视应用索引可见性检查。

## 临时文件维护

替换下载可以在 `archive_plan` 中设置 `replace_existing: true` 与可选 `target_path`，
后台下载完成后自动执行；既有任务可通过 `POST /api/v1/tasks/{id}/replacement-plan`
追加计划。`target_path` 应来自媒体库文件查询，不要推测既有目录命名。计划持久保存在
数据库，重启后会继续处理尚未入库的完整下载。暂停任务仍需恢复才会执行。

历史 schema 11→12 增加替换计划，schema 13→14 增加扫描记录。旧程序不能直接写入更高版本数据库；恢复旧备份会损失之后的记录，必须按升级与回滚部分处理。

内置维护任务默认每小时运行一次，只清理超过 24 小时且能由严格命名规则证明属于本产品
的残留文件：已取消/已完成任务或已无数据库任务对应的 UUID `.part`，以及下载提交和
归档流程留下的 `.committing`、`.importing-*` 文件。暂停、失败、排队、重试或下载中的
断点文件会保留；普通文件、未知 `.part`、符号链接和归档深度之外的文件不会删除。

相关配置为 `APP_CLEANUP_ENABLED`、`APP_CLEANUP_INTERVAL_SECONDS` 和
`APP_CLEANUP_RETENTION_SECONDS`。保留期不得短于一小时。媒体库扫描按产品目录协议限制
在电影一层、电视剧两层，避免递归遍历任意 NAS 目录；单次维护失败只记录日志，不会使
下载 worker 退出。

## 追更扫描节流

`APP_SUBSCRIPTION_POLL_INTERVAL` 控制指定影视订阅的扫描周期，默认 300 秒，最短 30 秒。
下载队列仍按独立的 `APP_WORKER_POLL_INTERVAL` 响应，因此降低频道扫描频率不会拖慢已入队
任务。新建或恢复订阅会主动唤醒一次扫描，无需等待完整周期；Telegram 返回 FloodWait
时，在服务端要求的等待期结束前，即使收到唤醒也不会重复扫描。

## 日志

Windows CLI 显式使用 UTF-8 输出，PowerShell 中的人类可读结果和 `mediactl doctor --json`
的中文字段不会因活动代码页不同而乱码；JSON 仍可直接交给自动化工具解析。

CLI 启动时关闭 Uvicorn 的重复 access log，由应用统一输出结构化请求日志。健康检查、
静态资源和 Web 的正常任务轮询降为 debug；登录、搜索、写操作、错误与 worker 故障仍在
info/warning/error 级别记录。请求体、管理 Token、API Hash、手机号、密码和代理认证信息
均不得进入日志。

## Web 管理员认证与 Telegram Session

Docker 首次 Web 配置使用独立一次性初始化码创建管理员账号。密码使用带随机盐的 `scrypt` 哈希保存到数据卷的 `configuration/install/admin_credentials.json`，不会明文保存。登录成功后浏览器使用 HttpOnly、
SameSite=Strict 会话 Cookie；写操作还需通过 CSRF 校验。会话默认有效 12 小时。

内部 API Token 仅保留给 CLI 和自动化客户端的 Bearer 认证，不在 WebUI 中展示或输入。
轮换内部 Token 会使现有 Web 会话签名失效，浏览器需重新使用管理员账号登录。

Telegram 用户登录是服务级状态，持久保存在 `data/sessions/telegram.session`，与浏览器
无关。浏览器登录控制台后只读取该状态；只要 Session 仍有效，就会直接
显示“Telegram 已登录”，不会重发验证码。Session 文件被删除、失效或被 Telegram
撤销时，才需要重新完成验证码及可选的两步验证。

公开 Docker 部署可在容器终端运行 `mediactl set-admin`，或在部署目录运行以下交互命令重置管理员：

```sh
docker compose exec api mediactl set-admin
```

使用旧的只读 `/run/secrets` 挂载的安装须由原凭据管理方式更新管理员文件，不能直接写入只读挂载。该内部开发部署的初始化方式保留在 [开发文档](DEVELOPMENT.md)。

轮换内部 API 密钥后重启服务：

```sh
docker compose exec api mediactl rotate-token
docker compose restart api
```

新密钥默认不打印；现有浏览器会话失效后重新登录。

## Compose 首次启动

公开 Compose 默认向可信局域网提供 8080 端口，首次配置必须使用独立初始化码。需要只绑定本机时在 `.env` 设置 `TMA_BIND_ADDRESS=127.0.0.1`，端口冲突时用 `TMA_HOST_PORT` 选择独立宿主机端口。仓库根目录的旧开发 Compose 仍保留其原有配置。

使用 [公开 Compose 与首次 Web 配置](../deploy/README.md)，无需宿主机 Python/uv 或源码构建。默认命名卷由镜像初始化目录权限，容器以 UID/GID 10001、只读根文件系统运行，仅数据卷和 `/tmp` 可写。首次配置创建数据卷内的私有凭据目录，重启后应用 Telegram API 与区域偏好；初始化码不写普通服务日志。

接入已有媒体目录前，确认该运行用户对目标目录具备实际写权限；应用不会递归改变已有目录所有权。镜像内置 `/data`、`/backups`、`/downloads` 和 `/media` 路径，Compose 显式持久挂载，容器更新时保留这些卷。

如果 NAS 的 Telegram 出口依赖一个既有 Docker 网络，可在宿主机单独维护 Compose override，
只把本服务接入该 external network；网络名和代理地址属于主机配置，不应提交到通用仓库。
更新前使用不含生产 Session 的独立数据副本隔离验证镜像和迁移；实际替换时先停止旧服务，再由新容器独占原 Session，避免同一账号 Session 同时运行多个 gateway。保留升级前备份、旧镜像和配置，按 schema 兼容性决定恢复方式。
