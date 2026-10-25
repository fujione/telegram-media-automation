# ADR 0001：模块化单体

状态：接受

## 决策

1.0 前使用一个产品镜像、一个 API 进程和受监督 worker，而不是拆分微服务。

## 理由

家庭 NAS 的部署和备份应保持简单；Telegram 用户 session 也更适合单 owner。领域 ports
和 repository 接口保留未来拆分能力。
