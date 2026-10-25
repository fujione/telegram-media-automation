# 验证记录

本文件只记录不含账号、频道名称、Token、手机号、验证码、密码或代理凭据的验收事实。

## 2026-10-25 代理故障恢复与健康诊断

- 经飞牛 OAuth 授权管理接口排查：下载进程、数据库和 worker 存活，但 Telegram 业务未就绪；代理端口可连接，出口选中了过期订阅的占位节点，连接持续重置。
- 使用已有有效订阅中的实测可用节点恢复，Telegram 既有 Session 恢复授权；全部启用订阅的全部频道扫描成功，任务与订阅记录保留。
- 备份代理配置后启用保存节点选择，并实际重启代理验证选择保留；恢复后业务就绪检查返回 200。临时诊断容器已清理，未使用 SSH 密码。
- 本地新增健康原因码、设置页实际连接状态和 Compose 业务探针。213 项 Python 测试、12 项前端单元测试及完整浏览器回归通过，覆盖率 87.50%；Ruff 与严格 mypy 通过。
- 生产恢复通过代理修复完成；本次健康诊断代码及此前订阅编辑仍待单独部署，不将本机测试视作生产已更新。

## 2026-09-09 Windows 本机 canary

- `mediactl init` 完成目录、secret 和 schema 8 初始化。
- 默认端口冲突被启动前预检识别，服务改用空闲端口启动。
- 通过用户已启用的本机 HTTP 代理连接 Telegram。
- 真实用户账号完成验证码与两步验证登录。
- `/api/v1/telegram/status` 返回 `authorized=true`。
- `/health/ready` 返回 HTTP 200 和 `ready`。
- 服务停止并重新启动后，Telegram Session 无需验证码即可恢复授权。
- 重启后两个已配置搜索频道仍从 SQLite 正确读取。
- WebUI 已改为本地管理员账号密码登录，不再向普通用户展示内部 API Token。
- 已有安装的一次性本机管理员创建入口、密码哈希、Cookie 会话、CSRF 和退出登录已通过
  自动化验证。
- 新认证版本重启后，既有 Telegram Session 仍返回 `authorized=true`，健康检查仍为
  `ready`。
- 桌面和窄屏登录页已通过本机浏览器离屏渲染检查。
- 移动、硬链接和校验复制三种归档策略已通过自动化测试；硬链接跨设备会明确失败。
- 完整本机门禁通过：152 项测试、88% 语句覆盖率、ruff 格式与 lint、mypy、前端
  JavaScript 语法检查和 Python sdist/wheel 构建。
- 加载归档策略版本后再次重启，Telegram Session 保持授权，worker 正常且 readiness
  仍返回 HTTP 200。
- 使用现有已启用频道完成一次真实历史搜索：HTTP 200，返回 6 个结构化候选，所有响应
  资源 ID 均可从 SQLite 资源列表重新读取，且质量元数据完整；未记录频道或文件名称。
- 认证前 UI 配置端点仅返回 `language` 与 `timezone`；中文运行实例返回预期区域配置，
  本地化脚本已加载，重启后 Telegram 仍授权且 readiness 为 HTTP 200。
- 文件维护服务的取消任务、孤立断点、可恢复断点、提交临时文件、未知文件和最短保留期
  边界均有自动化覆盖；加载后运行实例显示下载 worker 与维护循环同时运行，四个受管目录
  文件计数保持为零，Telegram 仍授权且 readiness 为 HTTP 200。
- 媒体身份建议覆盖中英文集数、范围、括号年份、质量标签和裸数字片名；运行实例将
  `A Show (2026) S01E02 1080p` 建议为电视剧 `A Show`、年份 2026、第 2 集。自动归档 API
  未显式确认身份时返回 422，手动 provisional 归档语义保持不变。
- 运行实例公开的认证后质量设置为 `2160p > 1080p > 720p`、`zh > en`；真实频道搜索在
  设置最大文件 1 字节后返回 0 个候选，证明硬性大小过滤在 Telegram 搜索路径生效，且
  未创建任务或下载文件。重启后两个 worker、Telegram 授权和 readiness 均正常。
- 追更扫描与下载队列短轮询解耦，默认周期为 300 秒；生命周期测试验证新建订阅会唤醒
  扫描，扫描完成后不会随下载 worker 的短轮询重复请求 Telegram。
- schema 8 运行数据库原地升级到 schema 9 后，两个既有频道仍可读取，Telegram Session
  保持授权，worker 与 readiness 正常。频道解析的完整公开链接、`@` 用户名、纯用户名、
  数字 ID、`t.me/c/...` 和邀请链接拒绝均有自动化覆盖；Web 已加载完整链接示例。
- 缺集计划覆盖仅补齐后停止及补齐后继续追更；该能力收在 Web“高级选项”中，不增加默认
  线性追更表单的操作负担。
- 任务分页 API 在运行实例返回 `items/total/offset/limit`，Web 已加载状态分组筛选与翻页
  控件；仓储测试覆盖总数、offset 和状态过滤，资源详情按稳定 ID 独立读取。
- schema 9 运行实例执行 `mediactl doctor --json` 返回 `healthy=true`：内部 API 密钥、Web
  管理员、Telegram 凭据与代理、SQLite、五个受管目录及 worker 配置均通过检查；Session
  文件按设计提示需结合 readiness 确认，而运行 readiness 为 HTTP 200。
- Web 任务工作台加载后，运行实例的任务汇总接口返回 HTTP 200，汇总总数与分页总数一致；
  静态页面包含状态卡导航和分层候选/任务渲染代码。服务重启后 Telegram 仍为
  `authorized`，数据库、配置、Telegram 与 worker readiness 检查全部通过。

仍待验证：分别标记公开/私有频道的搜索覆盖、小文件下载暂停恢复、大文件下载、进程中断
接管、目标 NAS 跨卷归档，以及 Linux Docker 只读根文件系统运行。

## 2026-09-10 fnOS NAS 隔离 canary

- 在用户 NAS 的两个预先确认不存在的隔离根目录完成部署；没有修改 Docker 守护进程、
  全局镜像源、存储池、现有 Compose 项目或现有媒体目录。
- NAS 的 Docker Hub 镜像代理对官方 Python 基础镜像返回 401；只为本项目切换到 AWS
  Public ECR 的 Docker Official Images 镜像后构建成功，NAS 全局配置保持不变。
- Compose 配置校验与镜像构建通过。运行容器为 UID/GID `1000:1001`、非 root、根文件系统
  只读，只有受限 `/tmp` 和显式绑定的状态、下载、备份及媒体目录可写。
- 复用 NAS 已有透明代理网络进行 canary；项目自己的代理 secret 指向该网络的 mixed port，
  没有修改代理容器或其配置。Telegram Session 在 NAS 上直接恢复为 authorized，readiness
  的数据库、配置、Telegram 和 worker 四项均为 true。
- 完成一个 399,809,864 字节真实 Telegram 媒体下载。下载到 30,932,992 字节后主动暂停，
  `.part` 文件大小与数据库偏移一致且静置不再增长；恢复后从原偏移继续，最终任务状态为
  `downloaded`、字节数与预期一致，断点文件消失且 inbox 文件大小正确。
- 任务事件链依次包含 pending、candidate_selected、queued、downloading、paused、queued、
  downloading、downloaded；主动暂停只增加一次后续 worker 尝试，没有故障错误。
- 完成下载中强制终止容器的 lease canary。原进程偏移停止增长，新进程在 lease 到期前没有并发
  接管；到期后只记录一次 `worker_lease_expired`，attempt 从 1 增至 2，并从原偏移继续增长。
- 容器重启后 Telegram Session 无需验证码恢复，readiness 为 HTTP 200，已完成任务、字节数
  与输出路径均从 SQLite 恢复。
- canary 结束后取消中断测试任务，并按两个已核对的绝对文件路径删除其 `.part` 和已完成
  inbox 文件；复查两个下载目录均为空，未清理任何未知文件。
- 在隔离 inbox 生成 1 MiB 确定性内容并直接调用产品归档服务，完成 `/vol1` 到 `/vol3`
  的真机验证：copy 模式保留源且源/目标 SHA-256 一致，move 模式校验后删除源，不同内容
  的同名目标触发冲突且未覆盖，hardlink 模式跨设备明确拒绝且未降级复制。
- 跨卷 canary 只使用固定的 `TMA Canary 20260910` 路径；验证后逐个删除 3 个源文件、2 个
  目标文件、3 个测试目录和测试脚本，复查 inbox 与电影 canary 根均为空。
- 当前 Telegram Session 发现 21 个已加入频道/超级群，其中 17 个有公开 username，4 个
  无公开 username；未记录任何标题、username 或内部频道 ID。公开频道和私有频道均完成
  真实历史搜索，HTTP 状态为 200；至少一个私有频道返回真实媒体候选。
- 真机发现一个已加入私有频道的 access hash 未进入 Telethon Session 实体缓存，纯数字 ID
  搜索会在 Telethon 内部触发 `IndexError`。网关现会从已加入会话重新解析并缓存实体；修复
  后 4 个私有频道逐一搜索均返回 HTTP 200，Telegram 授权和 readiness 保持正常。
- 最终部署从隔离目录迁移到独立的应用与下载目录。切换前确认目标不存在；切换后数据库任务
  总数为 0，下载、inbox、电影和电视剧四个受管目录均为空，容器为 `1000:1001`、只读根
  文件系统且健康。Telegram API ID 与本项目初始化值一致，持久 Session 保持 authorized；
  切换前的运行实例已停止但未删除，其数据保留为回滚点。
- 初始化期间曾显示在交互记录中的内部自动化 API Token 已使用 `mediactl rotate-token`
  原子轮换，新值未输出；服务重启后 readiness 与 Telegram 授权保持正常。Telegram API
  ID、API Hash 和 Session 未随本产品 Token 轮换而改变。
- 首次 CI 暴露镜像在只读根文件系统下会回落到相对 `data/` 的问题；镜像补充固定 `/data`
  等容器默认路径后，当前提交的托管 CI 完整通过，
  包括 159 项测试、类型/格式/lint、构建、Compose、SBOM、Linux Docker build 和只读非
  root 容器 smoke。

观察项：不主动制造账号限流的 FloodWait 自然触发行为；等待真实自然触发时补充记录。
