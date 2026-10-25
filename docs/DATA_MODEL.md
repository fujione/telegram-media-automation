# 数据模型

## 聚合

- `MediaItem`：电影或剧集的稳定内部身份。
- `Season/Episode`：结构化季集，不用目录字符串代替。
- `MediaResource`：Telegram 消息中的一个可下载媒体资源，并保留下载频道、消息和可用的转发来源。
- `MediaRequest`：用户的电影、单集、范围或追更意图。
- `DownloadTask`：一个具体下载 attempt。
- `ImportTask`：独立归档或 Arr 导入。
- `Subscription`：指定影视、频道集合、待补集集合、后续追更起点和每频道游标。
- `SearchChannel`：可启停的默认 Telegram 搜索作用域；不代表通用频道监控。
- `TaskEvent`：不可变状态和审计事件。

## 统一状态

```text
pending, searching, needs_selection, candidate_selected, queued,
downloading, paused, retrying, downloaded, importing, imported,
provisional, failed, cancelled
```

`provisional` 表示文件已成功归档但尚无稳定外部元数据身份，不表示失败。

## 数据约束

- Telegram locator 唯一键：account、chat ID、message ID、media index。
- 来源快照：频道 ID、消息 ID、发现时的频道标题与用户名、消息链接；Telegram 提供转发头时，另存原频道 ID、消息 ID、标题、用户名和链接。
- 频道名称后续改变不会覆盖已经下载资源的来源快照；实际读取媒体仍使用不可变的 locator。
- 每个 subscription/channel 使用独立 cursor。
- 同一 task 只有一个活动 lease。
- 状态版本用于乐观并发。
- 状态更新和 TaskEvent 必须在同一事务。
- secret 不进入业务表，只保存 secret reference。

## Schema 版本

`schema_versions` 记录已应用版本。应用和 worker 启动时先执行迁移；数据库版本高于
当前程序时拒绝启动，避免早期程序误写新结构。版本 1 是初始基线，版本 2
增加任务最终输出路径，版本 3 增加持久化重试时间，版本 4 增加默认搜索频道，版本 5
增加下载任务 lease 所有者与到期时间，版本 6 增加追更任务的不可变自动归档计划，
版本 7 增加未来集候选积压和多集文件归档范围。
版本 8 持久化文件名与消息说明之间的集数冲突；交互搜索显示冲突，自动追更拒绝冲突。
版本 9 增加待补集集合和“补齐后是否继续追更”计划，已有订阅升级后保持持续追更语义。
版本 10 保存 Telegram 消息和转发来源快照；版本 11 从既有 locator 补齐可确定的来源 ID。
版本 12 保存下载后的替换目标与替换模式。
版本 13 增加 `use_default_channels`：新订阅可动态跟随启用的默认频道；历史订阅保留原有明确频道范围，不自动转换。

当前版本14增加 `subscription_scan_summaries`、`subscription_scan_history` 和 `worker_gates`，保存每订阅的扫描摘要、受限历史、合并的检查请求与全局禁止重试时间。摘要按订阅计划版本校验；旧计划报告不能覆盖新计划。删除订阅时关联摘要和历史级联删除。

最近尝试与成功检查时间不同于最近新增任务和归档时间；编辑、暂停及恢复不产生资源更新。升级前的任务与事件可提供已有活动时间。每订阅最多50条历史，全局最多5000条，接口只展示最近30天，写入清理超限/过期记录。进程重启前未完成的扫描显示中断，等待后续重试；FloodWait 窗口在重启和手动请求后仍有效。

首次 Web 配置仅持久化到独立凭据目录，不进入业务表或默认数据库备份；公开诊断仅允许稳定状态码、数量、时间与匿名引用。
