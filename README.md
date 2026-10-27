# Telegram Media Automation

面向个人与家庭 NAS 的 Telegram 媒体下载工作台：搜索频道资源、确认媒体身份、下载入库，并按剧集持续追更。

## 功能

- **找资源**：使用自己的 Telegram 账号，选择已加入的公开/私有频道，按片名、季集、画质和语言筛选。
- **下载与入库**：持久化队列、断点续传、暂停/继续/重试；确认片名与集数后自动整理到电影或电视剧目录。
- **安全替换**：选择媒体库中的实际目标文件，校验新文件后替换并保留旧文件备份。
- **剧集追更**：补集、持续追更、编辑和暂停；最近检查、扫描历史、失败原因和手动检查入口。
- **日常管理**：中英文响应式 WebUI、Telegram 连接诊断、数据库备份和可预览的脱敏诊断导出。

长期没有新增资源时进行提示，由用户判断是否停止追更。目前不自动判断剧集完结。

## Docker 部署

对外仅提供 Docker 部署。使用者自行申请 Telegram API ID/Hash，登录自己的 Telegram 账号，并选择自己有权访问的频道或群组。

当前预发布候选为 **0.1.0rc6 / schema 14**，支持 `linux/amd64`。从 [Releases](https://github.com/fujione/telegram-media-automation/releases) 获取固定版本 Compose、离线镜像和校验文件；完整步骤见 [Docker 部署说明](deploy/README.md)。

发布版流程为：将 [compose.yaml](compose.yaml) 放到空目录，启动服务并读取一次性初始化码：

```sh
docker compose up -d
docker compose exec api mediactl setup-code
```

打开 `http://你的NAS地址:8080`（本机可用 `http://localhost:8080`），在 Web 填写初始化码、管理员账号、本人 Telegram API 和可选代理。保存后重启：

```sh
docker compose restart api
```

进入设置完成 Telegram 验证码/两步验证登录，再添加频道、搜索、下载或创建追更。NAS 局域网访问、已有媒体目录挂载和升级方法见部署说明。宿主机无需 Python、uv、Node 或源码构建工具。

默认数据、Session、下载和媒体保存在持久化卷中。更新容器时复用原卷；数据库备份默认不包含凭据和 Session。升级前备份，核对新旧数据库版本，保留当前数据再决定恢复方案，详见 [运维文档](docs/OPERATIONS.md)。

## 使用约定

浏览器管理员登录与 Telegram 登录彼此独立。换浏览器只需登录本产品；后台 Telegram Session 有效时无需再次发送验证码。搜索和追更使用相同的默认质量策略，单次搜索可临时覆盖筛选条件。

下载前确认片名、年份及季集，系统据此生成媒体库路径。“仅下载”的文件保留在收件箱，可稍后入库。追更支持指定缺集列表，以及补齐后停止或继续追更；暂停、编辑和删除订阅不会删除已入库媒体。

一个部署运行一个 Telegram gateway 与嵌入式 worker。代理必须能从容器内访问，支持 SOCKS5、SOCKS4 和 HTTP；容器内的 `127.0.0.1` 指向容器自身。

## 文档与开发

- [Docker 部署与升级](deploy/README.md)
- [备份、恢复与故障排查](docs/OPERATIONS.md)
- [变更记录](CHANGELOG.md)
- [Web 功能说明](docs/WEBUI_FEATURE_COVERAGE.md)
- [产品规格](docs/PRODUCT_SPEC.md) · [架构](docs/ARCHITECTURE.md) · [行为约定](docs/BEHAVIOR_SPEC.md)
- [开发流程](docs/DEVELOPMENT.md) · [迭代计划](docs/ROADMAP.md)
- [安全策略](SECURITY.md) · [第三方依赖](THIRD_PARTY.md) · [许可证](LICENSE)
