# 开发与内部维护

此文面向贡献者与旧开发环境。对外部署仅使用 [Docker 部署流程](../deploy/README.md)，用户无需宿主机 Python/uv。下列旧初始化与独立 worker 命令不作为公开安装流程。

## 本地开发

```powershell
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run mypy
```

启动 API：

```powershell
$env:APP_API_TOKEN='change-me'
uv run mediactl serve
```

首次本地使用运行交互式初始化。初始化器会先创建 Web 管理员账号；管理员密码、
Telegram API Hash 和可选代理都不会回显：

```powershell
uv run mediactl init
uv run mediactl doctor
uv run mediactl serve
```

`mediactl doctor` 会检查管理密钥、Telegram API 配置、代理、数据库 schema、worker 和
所有下载/媒体目录的真实写入权限及可用空间；尚未在 Web 完成 Telegram 登录只会显示
警告。自动化部署可使用 `mediactl doctor --json`，存在必需项错误时退出码为 1。

如果管理 Token 曾经出现在聊天、截图或日志中，立即轮换并重启服务：

```powershell
uv run mediactl rotate-token
uv run mediactl serve
```

新 Token 默认不会打印。它是供 CLI 或自动化客户端使用的内部密钥，不再作为 WebUI
登录方式。需要在当前安全终端中显示一次时可显式运行
`uv run mediactl rotate-token --show`。

使用自带 Compose 时，让初始化器直接写入容器路径和 secret 引用：

```powershell
uv run mediactl init --docker
docker compose up -d --build
```

该命令创建 `.env`、`secrets/`、数据和下载目录；API Hash、管理 Token 及可选代理认证
不会写入 `.env`。

`.env.example` 用于高级自动化部署参考；它不能代替管理员密码哈希的安全创建流程。
可选代理支持 `socks5://`、`socks4://` 和 `http://` URL。

访问 `http://127.0.0.1:8080/`，使用初始化时创建的管理员账号登录后，可完成 Telegram
登录、搜索、入队、任务控制、归档与追更订阅。Telegram API ID/Hash 可使用 `.env` 中的
`APP_TELEGRAM_API_ID`、`APP_TELEGRAM_API_HASH`，生产环境建议使用 secret 文件。

搜索面板的“资源筛选”可临时限定分辨率、语言和最大文件大小，并调整 HDR 排序；留空时
继承全局质量策略。电视剧追更使用同一套全局策略，避免交互搜索和自动追更选择标准不一致。

频道输入框可直接粘贴 `https://t.me/channel_name`，也接受 `@channel_name`、纯用户名或
`-100` 数字 ID。登录 Telegram 后也可以直接发现当前账号已加入的频道，按真实标题选择；
系统会依据 username 标记公开/私有，不要求用户查找私有频道数字 ID。电视剧订阅可填写
`2, 4-6` 这样的缺集计划，并选择仅补齐后停止，或补齐后继续从指定“下一集”追更。

下载任务面板按 20 项分页，可筛选全部、进行中、需要处理和已结束任务。分页与过滤在
SQLite 查询层执行，因此长期运行后旧任务仍可查询，不会受首页固定列表长度影响。

Web 管理员登录和 Telegram 登录是两层独立状态：浏览器使用账号密码获得安全会话
Cookie；Telegram 登录属于后台服务，Session 持久保存在
`data/sessions/telegram.session`。换浏览器只需重新登录本产品，不会要求重新发送
Telegram 验证码。只有 Session 失效、被删除或被 Telegram 撤销时，页面才会再次显示
手机号登录入口。

初始化选择的 `zh-CN` 或 `en-US` 会应用到登录页和完整控制台；任务事件和备份时间统一
按配置的 IANA 时区显示，不随访问浏览器所在时区漂移。

尚未创建管理员的安装会在首次打开 WebUI 时显示一次性管理员创建页。也可在服务主机
使用隐藏密码提示创建或重置账号：

```powershell
uv run mediactl set-admin
```

`mediactl serve` 默认在同一进程和事件循环中启动受监督 worker，确保 Telegram Session
只有一个活动 gateway。`mediactl worker --once` 仅用于 API 已停止时的维护处理。

若默认端口已被占用，命令会直接给出替代端口，例如：

```powershell
uv run mediactl serve --port 8082
```

Telegram 连接默认最多重试一次，并对 Web 操作设置 20 秒硬超时。无法直连 Telegram 的
网络应在 `.env` 或 secret 文件中配置 SOCKS5/SOCKS4/HTTP 代理，而不是反复等待页面。

初始化时可以选择归档策略：`move` 是默认值，归档后从 inbox 移除文件；`hardlink` 不占
额外文件数据空间并保留 inbox 路径，但要求 inbox 与媒体库处于同一文件系统；`copy`
保留 inbox 文件并复制一份到媒体库，因此需要双份空间。策略不可用时任务会明确失败，
不会在后台静默换用另一种方式。
