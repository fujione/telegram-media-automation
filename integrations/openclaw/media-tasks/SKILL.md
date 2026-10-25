---
name: media-tasks
description: 在 Telegram Media Automation 中搜索资源、创建下载任务、管理追更订阅，以及查看和控制下载状态。
user-invocable: true
---

# 媒体下载与追更

通过私有 `tasks-helper` 访问服务。凭据只在 OpenClaw 部署环境中保存，绝不读取、
展示或写入回复。服务地址和 API 使用独立 Bearer Token，与 Web 登录账号分开。

```bash
TASKS=/path/to/media-tasks/bin/tasks-helper
```

## 搜索并下载

先搜索，再根据用户确认的资源 ID 创建任务。已确认片名、季和集数时，应在创建任务时
一并提供 `archive_plan`，下载完成后会自动入库：

```bash
$TASKS search '{"query":"片名","channels":[],"limit":20}'
$TASKS download '{"resource_id":"<搜索结果中的 id>","archive_plan":{"kind":"series","title":"片名","season":1,"episode":1,"confirmed_identity":true}}'
```

搜索结果包含文件名、大小、清晰度、语言、集数范围、身份置信度和 `source` 来源。
向用户展示候选或任务时，应报告下载频道；`message_link` 可用时给出消息链接，
存在 `forwarded_from_*` 时另行说明转发来源。不要凭文件名
猜测资源 ID 或集数；存在多个候选时展示候选并让用户选择。只提供 `resource_id` 时
任务会停在 `downloaded`，用于尚未确认媒体身份或准备替换已有文件的场景。下载任务
创建后返回 `queued`，再用 `tasks` 查询实际状态。

频道可使用 `https://t.me/name`、`@name`、纯用户名或 `-100...` ID；留空表示使用
服务中已启用的搜索频道。

## 替换已有资源

先用媒体库查询核实旧文件的真实路径，不要猜路径。创建下载时带上替换计划，后台会在
下载完成后自动替换，服务重启后也会继续，无需守着下载：

```bash
$TASKS library '片名' series
$TASKS download '{"resource_id":"<新资源 id>","archive_plan":{"kind":"series","title":"片名","season":1,"episode":22,"confirmed_identity":true,"replace_existing":true,"target_path":"<library 返回的 path>"}}'
```

已有下载任务可用 `replacement-plan` 设置相同的计划（queued/downloading/paused/retrying/downloaded）；
已暂停的任务仍保持暂停。已经下载完也可直接替换：

```bash
$TASKS replacement-plan <task-id> '{"kind":"series","title":"片名","season":1,"episode":22,"confirmed_identity":true,"replace_existing":true,"target_path":"<library 返回的 path>"}'
$TASKS replace <task-id> '{"kind":"series","title":"片名","season":1,"episode":22,"confirmed_identity":true,"target_path":"<library 返回的 path>"}'
```

服务会校验下载大小与暂存文件 SHA-256，保留旧文件到媒体库内的隐藏备份目录，再原子替换。
如果任务尚未下载完成、目标不存在或校验失败，替换会拒绝执行。不要用普通 `import`
冒充替换，也不要直接删除旧文件。
目标必须位于对应的已配置媒体库内，不能是符号链接或隐藏文件；新旧扩展名必须一致。
备份路径记录在任务事件中，可用 `events` 查询；备份不会自动删除。

## 已下载文件入库

对于停在 `downloaded` 的任务，可在媒体身份明确后入库，无需重新下载：

```bash
$TASKS import <task-id> '{"kind":"series","title":"片名","season":1,"episode":1,"confirmed_identity":true}'
```

普通下载已提供 `archive_plan` 时不需要再次调用。容器中的 `/media/tv`、`/media/movies`
是服务路径；只有在飞牛影视实际索引中查到内容，才能声称已在飞牛影视可见。

## 追更订阅

```bash
$TASKS subscribe '{"title":"剧名","season":1,"next_episode":1,"query":"剧名","channels":["频道"]}'
$TASKS subscriptions
$TASKS subscription-pause <subscription-id>
$TASKS subscription-resume <subscription-id>
$TASKS subscription-remove <subscription-id>
```

`continue_tracking` 默认为 `true`，表示后续新集继续追更；仅补历史集时设置为
`false` 并提供 `pending_episodes`。频道必须至少提供一个值。

## 查询与控制任务

```bash
$TASKS summary
$TASKS tasks [limit]
$TASKS task <task-id>
$TASKS events <task-id>
$TASKS pause|resume|cancel|retry <task-id> [原因]
```

`tasks` 和 `task` 输出会附带对应的 `resource`，必须据此报告文件名、集数和来源，
不能根据邻近对话把其他任务误认成当前影视；没有 `archive_plan` 且文件名不含片名时，
片名应标为未知，不得把所有任务都冠以当前对话的片名。常见状态：`queued` 排队中、
`downloading` 下载中、`paused` 已暂停、`imported` 已
入库、`provisional` 已下载待归档、`failed` 失败、`cancelled` 已取消。
只有查询确认后才能向用户报告已下载或已入库。暂停、取消、重试前先确认目标
任务 ID；已入库文件不会因取消任务而删除。

连接失败时说明配置的服务地址不可达，不要编造进度。
