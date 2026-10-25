# 架构

## 形态

1.0 前采用模块化单体：一个产品镜像、一个 SQLite 数据库、一个 Telegram 用户
session、多个受监督异步 worker。

```text
Web / CLI / automation clients
        │
        ▼
API + Auth + OpenAPI
        │
        ▼
Application commands / queries
        │
        ▼
Domain entities / state machine / rules
        │ ports
        ├─ Telegram gateway
        ├─ Download client
        ├─ Media archive filesystem
        └─ Repositories
              │
              ▼
Adapters + Infrastructure + Workers
```

## 依赖规则

```text
domain <- application <- api/workers
   ^             ^
   |             |
 ports      adapters/infrastructure
```

- `domain` 不得依赖 FastAPI、SQLAlchemy、Telegram 或文件系统实现。
- 外部系统原始状态只保存在 `provider_state`，不扩张领域状态枚举。
- 所有状态改变必须写 TaskEvent。
- Telegram session 在单节点内只允许一个活动 gateway。
- 外部自动化通过稳定 HTTP API 接入，不直接访问数据库或领域对象。

## 运行组件

- API：输入校验、Web 管理员 Cookie/CSRF 认证、自动化 Bearer 认证、OpenAPI 和事件订阅。
- SearchWorker：Telegram 历史搜索和候选持久化。
- DownloadWorker：lease、分块下载、控制命令和恢复。
- ImportWorker：把已校验 inbox 文件安全归档到电影或电视剧目录。
- SubscriptionWorker：只扫描用户创建的影视订阅。
- Lease Reconciliation：领取任务时原子回收已过期下载 lease，并记录恢复事件。
- FileMaintenance：按保守保留策略清理可证明归属本产品的过期 part/提交临时文件；与
  下载 worker 隔离运行。
