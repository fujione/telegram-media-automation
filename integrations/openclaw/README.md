# OpenClaw 集成

这里维护 Telegram Media Automation 的 OpenClaw 技能源文件。部署时将两个技能目录
同步到 OpenClaw 的 `plugin-skills` 目录，并在各自 `bin/api.env` 中配置：

```dotenv
TELEGRAM_MEDIA_API_URL=http://<服务地址>:18082
TELEGRAM_MEDIA_API_TOKEN=<从服务 secrets/api_token 读取>
```

`api.env` 只存在于部署机并应设为 `0600`，不提交到 Git。技能通过 Bearer Token
访问 `/api/v1/searches`、`/api/v1/tasks`、`/api/v1/subscriptions` 和
`/api/v1/channels`，不依赖 Web 登录 Cookie。
