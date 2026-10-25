# ADR 0002：SQLite 作为默认数据库

状态：接受

## 决策

1.0 默认使用 SQLite WAL、短事务、乐观版本和 worker lease。schema 使用版本化迁移管理。

## 理由

单节点 NAS 不应为了队列额外部署 Redis/PostgreSQL。repository port 保留未来其他数据库
实现，不把 SQLite 类型泄漏到 domain。
