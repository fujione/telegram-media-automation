# ADR 0003：Telegram 适配器

状态：接受

## 决策

初始实现使用 Telethon，并把具体客户端实现限制在 `adapters/telegram`，由应用层端口定义
搜索、下载和会话能力。

## 后果

Telegram Session 由适配器持久化。适配器契约允许未来在不改变领域层的前提下增加其他
客户端实现。
