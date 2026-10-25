# Docker 首次部署（候选流程，发布验证中）

此 Compose 面向全新安装，与仓库根目录的开发 Compose 分开。默认镜像标签尚待发布；完整验收及镜像发布完成前，不将此流程标为可用的正式发布。

准备 Docker Engine 和 Docker Compose 插件，将此目录的 `compose.yaml` 保存到一个空目录。宿主机无需 Python、uv、Node 或源代码。默认使用 Docker 持久化卷保存配置、Session、下载、媒体和备份。

当前镜像支持 `linux/amd64`。Telegram API ID/Hash 按 [Telegram 官方指引](https://core.telegram.org/api/obtaining_api_id) 申请：登录自己的 Telegram 开发者页面，在 API development tools 创建应用，然后将所得参数填写到首次配置页。

```sh
docker compose up -d
docker compose exec api mediactl setup-code
```

也可以在 NAS 的容器终端直接执行 `mediactl setup-code`，无需 Docker 命令前缀。

打开 `http://你的NAS地址:8080`；在本机部署时可使用 `http://localhost:8080`。填写刚读取的一次性初始化码、管理员账号密码及本人 Telegram API ID/Hash。代理按需填写完整 URL；地址必须能从容器内访问，容器内的 `127.0.0.1` 指向容器自身。语言和时区也在此配置。

保存后执行以下命令，或在 NAS 的 Docker 管理界面重启此容器：

```sh
docker compose restart api
```

进入设置，完成本人 Telegram 手机号、验证码和可选两步验证密码登录，再添加本人有权访问的频道。之后搜索资源、确认媒体身份并下载入库，或创建剧集追更。初始化码不能再次使用，也不会显示在普通日志或未登录页面中。

首次配置和 Telegram 登录前容器可能显示 unhealthy；Web 页仍可访问，完成后用以下命令查看状态：

```sh
docker compose ps
docker compose logs --tail=50 api
```

## NAS 和媒体目录

默认面向可信局域网提供 8080 端口，首次配置需要容器终端中的一次性码。需要仅本机访问时，在该目录创建 `.env` 并写入 `TMA_BIND_ADDRESS=127.0.0.1`；端口冲突时设置 `TMA_HOST_PORT=8081` 等空闲端口。重新创建服务后访问对应端口。公网访问应由自己的 HTTPS 反向代理提供。

默认媒体和下载保存在 Docker 命名卷中。接入已有媒体库时，在首次启动前将对应挂载改为明确的宿主机目录，例如 `/your/media:/media`。容器使用 UID/GID 10001，须确认目标目录对该用户可写；页面显示的电影和电视剧目录分别为 `/media/movies` 与 `/media/tv`。服务不会递归修改原有目录权限。配置卷和媒体卷应分别保留，重新创建容器后仍复用原卷。

## 升级和备份

先从设置创建数据库备份，并单独保护配置数据卷中的凭据和 Session；默认数据库备份不包含它们。保存当前镜像标识，核对发布说明中的数据库版本，再更新 Compose 的固定镜像版本：

```sh
docker compose pull
docker compose up -d
docker compose ps
```

核对 Telegram 状态、任务、订阅和扫描结果。回滚须核对数据库兼容性，不默认恢复旧数据库覆盖新记录。停止或更新容器时保留卷；`docker compose down --volumes` 会删除这些持久化数据，不用于常规升级。
