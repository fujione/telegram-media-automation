# 安全策略

## 报告漏洞

请通过 GitHub 仓库的 **Security → Report a vulnerability** 私下提交安全问题，不要先在
公开 Issue 中披露 Token、Telegram Session、手机号、频道标识或本地路径。

报告中请提供受影响版本、复现步骤、影响范围和建议修复方式；所有凭据和真实媒体信息均
应脱敏。项目维护者会在确认后协调修复和披露时间。

## 自托管安全边界

- Web/API 必须配置高熵 Bearer Token，并只通过可信局域网或带 TLS 的反向代理暴露。
- `secrets/`、`.env`、Telegram Session 和含 Session 的备份不得提交到 Git。
- 容器默认以非 root 用户运行；媒体与数据卷只授予实际需要的权限。
- 本项目不会绕过 Telegram 的访问控制，也不提供任何内容源。
