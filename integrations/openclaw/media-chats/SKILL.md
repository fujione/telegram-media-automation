---
name: media-chats
description: 管理 Telegram Media Automation 使用的搜索频道；支持列表、识别、发现、添加、暂停、恢复和删除频道。
user-invocable: true
---

# 搜索频道

通过私有 `chats-helper` 访问 NAS 上的 Telegram Media Automation。凭据只保存在
OpenClaw 的部署环境中，不要读取、展示或写入回复。

```bash
CHATS=/vol1/@apphome/trim.openclaw/data/home/.openclaw/plugin-skills/media-chats/bin/chats-helper
```

命令：

- `$CHATS list`：列出已配置频道；
- `$CHATS inspect '<频道>'`：验证频道并显示公开/私有、名称和 ID；
- `$CHATS discover [数量]`：从当前 Telegram 账号发现可访问频道；
- `$CHATS add '<频道>' [显示名]`：添加搜索频道；
- `$CHATS pause '<频道>'` / `$CHATS resume '<频道>'`：暂停或恢复；
- `$CHATS remove '<频道>'`：删除配置。

频道输入支持 `https://t.me/name`、`@name`、纯用户名和 `-100...` 数字 ID。
邀请链接（`joinchat`、`+...`）不能作为搜索频道。添加前可先 `inspect`；私有频道
只要当前 Telegram 账号已加入即可使用，通常使用数字 ID 或其 `t.me/c/...` 链接。

回答时保留服务返回的频道名称、可见性和状态；不要声称邀请链接可以直接搜索。
连接失败时说明 `18082` 端口的 Telegram Media Automation 不可达，不要编造结果。
