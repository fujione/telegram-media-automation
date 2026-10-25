# 第三方运行时依赖

本项目代码采用 MIT License。直接运行时依赖如下；确切版本、下载哈希和完整传递依赖见
`uv.lock` 与 `sbom.cdx.json`。

| 直接依赖 | 用途 | License |
|---|---|---|
| FastAPI | HTTP API 与 OpenAPI | MIT |
| Pydantic Settings | 配置校验 | MIT |
| SQLAlchemy | SQLite 持久层 | MIT |
| Uvicorn | ASGI 服务 | BSD-3-Clause |
| Telethon（telegram extra） | Telegram 用户账号公开 API 适配 | MIT |
| python-socks（telegram extra） | 可选 SOCKS/HTTP 代理传输 | Apache-2.0 |

发布镜像前由 CI 从锁文件重新生成 CycloneDX 1.5 SBOM。此表是工程清单，不替代各依赖
发行包内的许可证原文。
