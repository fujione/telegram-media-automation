# 发布验收清单

当前候选 `0.1.0rc6`，数据库 schema 14。订阅编辑、扫描历史、立即检查、脱敏诊断已在目标 NAS 上线并通过重启验证；公开 Compose 的空卷首次配置也已通过 Linux Docker CI。独立新 Telegram Session 已由本人完成登录，真实搜索、下载、归档、限定集数追更、下载期间重启续传、数据库恢复及完整 CI 镜像升级均已验收。公开镜像与版本化附件由Release工作流发行；工作流在发布前验证匿名拉取及实际发行Compose的空卷配置和重启。

## 当前证据

| 项目 | 状态与证明范围 |
| --- | --- |
| Python / 静态检查 | 本地236项测试通过，托管CI覆盖率门禁通过；Ruff与严格mypy通过 |
| 浏览器 | 中英文、8种宽度、订阅编辑/扫描历史/立即检查、诊断预览与下载一致、首次配置忙碌锁与秘密输入清空、登录失败重试与重复提交保护通过 |
| Docker首次配置 | 公共Compose空命名卷、非root、只读根、一次性码、同源保护、私有文件权限、重启后会话/签名密钥/偏好保留通过 |
| 业务贯通测试 | 受控替身覆盖完整流程；另在独立新Session完成真实频道搜索、两个完整视频下载及两种归档计划，限定补集自动入队且按计划停止，文件大小和SHA-256已核对 |
| 数据升级 | 生产数据库一致性副本13→14迁移，8个业务表的完整行指纹保留；单独兼容恢复镜像读取14副本通过 |
| 生产连续性 | 原有任务、订阅、挂载和Session保留；连接状态改用实际RPC；验收实例在下载期间重启后续传，完整CI镜像升级后授权、任务、订阅、归档哈希和挂载保留 |
| 备份恢复 | 真实业务数据库备份已在隔离目录恢复，整库校验值与完整性检查通过；保留两个任务、订阅、游标、归档计划和事件，默认不包含Session |
| 镜像隐私 | 公开rc6镜像的配置与8层、8162个普通文件已扫描；未发现实际凭据或私有部署信息；2处通用密钥告警为核对过的官方Python公钥指纹 |
| 发布包 | 配置ID、平台、运行用户、层哈希和额外附件拒绝检查已实现并测试；由实际发布工作流执行并保留结果 |

公共镜像目前验收平台为 `linux/amd64`，其他架构不标为已支持。开发质量检查和复现命令见 [开发文档](DEVELOPMENT.md)，公开用户只需要 [Docker部署流程](../deploy/README.md)。

## 自动化可证明的产品能力

| 需求 | 实现证据 | 自动化证据 |
|---|---|---|
| 可复现运行环境 | Python 包、容器、配置、Web、锁文件和 CI | `test_architecture.py` |
| Web 管理员认证 | 初始化账号、带盐密码哈希、HttpOnly Cookie、CSRF、限流、退出和已有安装的账号创建流程 | `test_web_auth.py`、`test_api.py`、`test_initializer.py`、`test_cli.py` |
| Telegram 用户账号 | 验证码、两步验证、持久 Session、稳定错误映射 | `test_telegram_gateway.py`、`test_api.py` |
| 搜索频道 | 持久添加、暂停、恢复、删除；单次搜索可覆盖默认值 | `test_api.py` |
| 候选识别与排序 | 中英文季集/范围、冲突、身份、语言、分辨率、HDR、编码和大小；保守身份建议、全局策略与单次硬过滤 | `test_episode_parser.py`、`test_media_identity.py`、`test_resources.py`、`test_api.py`、`test_telegram_gateway.py` |
| 持久下载 | 原子 lease、续租、失租隔离、过期接管、`.part` 续传、跨卷校验提交、无数据超时、磁盘门禁和限速 | `test_download_worker.py`、`test_download_service.py` |
| 任务控制 | 暂停、恢复、取消、重试、幂等创建和不可变事件时间线 | `test_states.py`、`test_repository.py`、`test_api.py` |
| 产品化任务工作台 | 完整队列及状态分组汇总、服务端筛选导航、候选质量徽章、任务状态/错误/归档目标分层展示 | `test_repository.py`、`test_api.py` 与前端 JavaScript 语法门禁 |
| 媒体归档 | 稳定电影/剧集命名、冲突保护、哈希去重，以及移动/同卷硬链接/校验复制策略 | `test_archive_service.py`、`test_import_service.py` |
| 指定影视追更 | 每订阅/频道独立游标、未来集积压和过期候选清理；下载后按固化计划自动归档 | `test_subscription_worker.py`、`test_download_worker.py` |
| 运维 | 本机/Compose 初始化、CLI 自检、代理、健康状态、保守残留清理、脱敏日志、校验备份/恢复、非 root 容器 | `test_initializer.py`、`test_doctor.py`、`test_maintenance_service.py`、`test_logging.py`、`test_backup.py` 与 CI 容器 smoke |

标准本机门禁：

```powershell
uv sync --locked --all-extras
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest --cov=telegram_media_automation --cov-report=term-missing
node --check src/telegram_media_automation/web/app.js
node --check src/telegram_media_automation/web/workspace.js
npm ci
npm test
npx playwright install chromium
uv run python scripts/check_web_workspace.py
uv build
```

## 公开发行状态

- [0.1.0rc6预发布](https://github.com/fujione/telegram-media-automation/releases/tag/v0.1.0rc6)已提供固定版本GHCR镜像、Compose、离线镜像、依赖SBOM、发行元数据、说明和校验文件。公开源码使用独立干净历史，旧开发历史保留为私有档案。
- 发布工作流已通过完整CI、无注册表凭据拉取、镜像ID/digest比对，以及实际发行Compose配合匿名拉取镜像的空卷配置和重启验收。
- 全部7个公开附件已匿名下载并校验；离线镜像已在NAS用Docker CLI导入，与发行元数据的镜像ID一致。NAS上的空卷首次配置和重启使用测试凭据及隔离网络，未向Telegram发送虚构登录请求。
- 真人独立新Session及搜索、下载、归档、限定补集链路在前序候选完成；本次公开镜像已在这套授权数据及现用服务上完成升级验收，任务、订阅、Session和归档哈希保留。验证码和两步密码由账号所有者在页面输入。

后续发行继续执行同样的检查，并区分隔离配置测试、真人业务验证和已有数据升级的证明范围。

## 历史真机基线

2026-09-10及后续旧部署已验证真实Telegram验证码恢复、公开/私有频道发现与历史搜索、约381MiB断点下载、进程中断接管、NAS跨卷归档、冲突保护和非root只读容器。这些记录支持已有下载能力，但不等同于新Docker首次配置流程已经完成真实账号验收。

真实FloodWait只在自然触发时记录，不通过高频请求主动限制用户账号；合并、退避与重启保留窗口由自动化测试覆盖。更多历史记录见 [验证记录](VALIDATION_LOG.md)，本轮发布维护流程见 [RELEASING.md](RELEASING.md)。
