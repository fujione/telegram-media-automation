/* Page layout and presentation. Download/import commands remain in app.js. */
const workspacePages = {
  overview: "工作台", discovery: "搜索资源", "tasks-panel": "下载任务",
  "subscriptions-panel": "追更订阅", "channels-panel": "频道管理", operations: "设置",
};
let currentPage = "discovery";
let queueVisible = true;
const selectedResources = new Map();

function telegramHealthLabel(readiness) {
  const labels = {
    authorized: "已连接", login_required: "需要登录", not_configured: "未配置",
    telegram_timeout: "连接超时，请检查代理或网络。",
    telegram_proxy_failed: "代理连接失败，请检查出口节点。",
    telegram_connection_failed: "连接失败，请检查代理或网络。",
    telegram_rate_limited: "请求限流，请等待后重试。",
    telegram_operation_failed: "连接检查失败，请查看服务日志。",
  };
  return labels[readiness?.telegram_status] || (readiness?.checks?.telegram ? "已连接" : "连接不可用");
}

function subscriptionScanLabel(scan) {
  const labels = {scanning:"正在检查",succeeded:"检查正常",partial:"部分频道异常",failed:"检查异常",rate_limited:"等待 Telegram 限流恢复",no_channels:"没有可用频道",completed:"已完成补集",interrupted:"上次检查中断，等待重试"};
  return t(labels[scan?.state] || "等待检查");
}

function subscriptionScanDetails(scan) {
  if (!scan || !["succeeded", "partial"].includes(scan.state)) return [];
  const lines = [];
  if (scan.outcome === "enqueued") {
    lines.push(t("第 {episode} 集已入队").replace("{episode}", String(scan.target_episode)));
  } else if (scan.outcome === "waiting") {
    lines.push(t("等待第 {episode} 集").replace("{episode}", String(scan.target_episode)));
  } else if (scan.outcome === "plan_changed") {
    lines.push(t("订阅计划已变更，本轮未入队"));
  } else if (scan.outcome === "existing_task") {
    lines.push(t("第 {episode} 集已有失败或取消任务，请检查下载队列。")
      .replace("{episode}", String(scan.target_episode)));
  }
  if (scan.scanned_media_count != null) {
    lines.push(t("本轮媒体候选 {count} 个").replace("{count}", String(scan.scanned_media_count)));
  }
  const labels = {
    episode_conflict: "集数冲突", unparsed_episode: "集数无法识别",
    wrong_season: "季数不符", past_episode: "不在待补范围",
    future_episode: "后续集待处理", quality_filtered: "质量筛选未通过",
  };
  for (const [reason, count] of Object.entries(scan.rejection_counts || {})) {
    if (count > 0 && labels[reason]) lines.push(`${t(labels[reason])} ${count}`);
  }
  return lines;
}
let pendingResourceBatch = [];
let enqueuePreparationGeneration = 0;
let importIsReplacementPlan = false;
let searchResults = [];
let resultPageIndex = 0;
let taskRequestGeneration = 0;
let joinedChannels = [];
let telegramStage = "checking";
let lastSystemStatus = null;
let statusAvailable = null;
const taskCache = new Map();

const workspaceEnglish = {
  "配置你的媒体服务": "Set up your media service", "填写初始化码、Telegram API 与管理员账号。": "Enter your setup code, Telegram API credentials, and administrator account.",
  "账号与配置保存在你的设备上。": "Your account and configuration stay on your device.",
  "界面语言": "Interface language", "时区": "Timezone",
  "首次配置": "First-time setup", "初始化码": "Setup code", "代理地址（可选）": "Proxy URL (optional)",
  "在部署目录的终端执行以下命令读取初始化码：": "Run this command from your deployment directory to read the setup code:",
  "使用你自己申请的 Telegram API。保存后重启容器，再登录 Telegram 账号。": "Use your own Telegram API credentials. Restart the container after saving, then sign in to Telegram.",
  "配置已保存": "Configuration saved", "请重启此容器，然后连接 Telegram。终端命令：": "Restart this container, then connect Telegram. Terminal command:",
  "配置已保存。请重启容器后登录 Telegram。": "Configuration saved. Restart the container, then sign in to Telegram.",
  "上次检查中断，等待重试": "Previous check interrupted; waiting to retry",
  "立即检查": "Check now", "检查已排队": "Check queued", "扫描历史": "Scan history",
  "检查请求已合并": "Check request merged", "检查已加入队列": "Check queued",
  "最早重试时间": "Retry after", "最近尝试": "Last attempt", "最近成功": "Last success",
  "最近新增任务": "Last new task", "最近入库时间": "Last import", "连续失败": "Consecutive failures",
  "尚无扫描历史": "No scan history yet", "正在读取扫描历史…": "Loading scan history…",
  "最近 20 次检查；历史最多保留 30 天。": "Latest 20 checks; history is kept for up to 30 days.",
  "诊断信息": "Diagnostics", "预览诊断信息": "Preview diagnostics", "下载此预览": "Download this preview",
  "仅包含运行状态、数量和扫描摘要；不包含凭据、剧名、频道或文件路径。": "Contains service status, counts, and scan summaries. Credentials, titles, channels, and file paths are excluded.",
  "正在生成诊断预览…": "Preparing diagnostic preview…",
  "已订阅": "Subscribed", "暂停订阅": "Paused", "订阅筛选": "Subscription filter",
  "没有符合筛选条件的订阅": "No subscriptions match this filter", "等待检查": "Waiting for check",
  "正在检查": "Checking", "检查正常": "Check complete", "部分频道异常": "Some channels unavailable",
  "检查异常": "Check failed", "等待 Telegram 限流恢复": "Waiting for Telegram rate limit",
  "没有可用频道": "No available channels", "已完成补集": "Gap filling complete",
  "频道正常": "channels checked", "最近检查": "Last checked",
  "部分频道暂不可用，将自动重试": "Some channels unavailable; retrying automatically",
  "频道检查失败，将自动重试": "Channel check failed; retrying automatically",
  "订阅检查失败，将自动重试": "Subscription check failed; retrying automatically",
  "Telegram 请求限流，等待后重试": "Telegram rate limit; retrying after the wait",
  "没有可用频道，请启用默认频道": "Enable a default channel to start checking",
  "全部默认频道":"All default channels", "选择频道（可选） ":"Choose channels (optional) ", "已选择 {count} 个频道":"{count} channels selected", "不选则检索全部已启用的默认频道；选择后仅检索所选频道。":"Leave unselected to search all enabled default channels. Select channels to restrict the search.", "默认范围会随频道列表更新，无需重复填写链接。":"The default scope follows your channel list. No links to re-enter.", "正在读取频道…":"Loading channels…", "暂无已启用频道，请先到频道页添加或启用。":"No enabled channels. Add or enable channels first.", "请先添加并启用至少一个默认频道":"Add and enable at least one default channel first",
  "今晚，想看什么？":"What are we watching tonight?", "好剧，接着看。":"Keep the good stories going.", "你的资源来源。":"Your trusted sources.", "下载与入库记录":"Downloads & imports",
  "找资源":"Discover", "追更":"Following", "频道":"Channels", "找到好资源，下载完成后自动入库。":"Find a resource. Import automatically when it finishes.", "追更进度、来源和开关，一处掌握。":"Progress, sources, and tracking in one place.", "搜索和追更，来自你信任的频道。":"Search and follow channels you trust.", "查看进度、来源、入库结果，处理异常任务。":"Track progress, sources, imports, and tasks needing attention.",
  "收起队列 ":"Hide queue ", "展开队列 ":"Show queue ", "展开记录 →":"Expand history →", "全部":"All", "进行中":"Active", "待处理":"Attention", "已结束":"Finished", "选择资源":"Select resource", "选择资源后可逐个确认下载":"Select resources to confirm each download", "下载所选":"Download selected", "清空":"Clear", "10 条":"10", "输入片名，开始寻找资源。":"Enter a title to find resources.", "跳到工作区":"Skip to workspace", "设为下载后替换":"Replace after download", "已设置下载后替换":"Replacement scheduled", "设置下载后替换":"Schedule replacement", "替换媒体":"Replace media", "保存替换计划":"Save replacement plan",
  "折叠全部": "Collapse all", "展开全部": "Expand all", "影视名称 · 来源 · 画质": "Title · Channel · Quality", "下载 · 入库 · 替换": "Download · Import · Replace", "自动寻找下一集": "Find the next episode", "搜索与追更来源": "Search and subscription channels",
  "搜索资源 ↗": "Search resources ↗", "搜索 · 下载 · 入库": "Search · Download · Import",
  "入库策略": "Import strategy", "下载收件箱可用空间": "Inbox free space",
  "正在读取任务…": "Loading tasks…", "正在读取订阅…": "Loading subscriptions…", "正在读取记录…": "Loading history…",
  "例如：早春晴朗 S01E22": "Example: Series name S01E22", "正在创建…": "Creating…",
  "媒体下载工作台": "Media workspace", "正在连接服务…": "Connecting to service…",
  "请稍候。": "Please wait.", "使用你的管理员账号登录。": "Sign in with your administrator account.",
  "工作空间": "Workspace", "工作台": "Overview", "搜索资源": "Search resources", "频道管理": "Channels", "设置": "Settings",
  "管理员": "Administrator", "正在连接": "Connecting", "关闭导航": "Close navigation", "打开导航": "Open navigation", "关闭": "Close",
  "今天想看什么？": "What would you like to watch?", "找到合适的资源，下载并整理进媒体库。": "Find a resource and organize it in your media library.",
  "搜索电影、电视剧，或指定季集…": "Search a film, series, or specific episode…", "搜索影视名称": "Search media titles",
  "查看下载记录": "Download history", "下载与入库": "Downloads and imports", "确认信息或重试": "Confirm or retry", "入库、暂存与取消": "Imported, provisional, and cancelled",
  "当前任务": "Current tasks", "正在下载与等待处理的内容": "Downloads and tasks awaiting action", "查看全部 →": "View all →", "追更订阅": "Subscriptions",
  "你正在关注的剧集": "Series you follow", "管理 →": "Manage →", "最近入库": "Recently imported", "已完成的媒体整理": "Completed library imports", "下载记录 →": "History →",
  "按影视名称搜索，比较季集、画质和来源。": "Compare episodes, quality, and channels.", "管理搜索频道 →": "Manage channels →",
  "搜索频道": "Search channel", "所有默认频道": "All default channels", "筛选与搜索范围": "Filters and scope", "频道链接，多个用英文逗号分隔": "Channel links, separated by commas",
  "搜索结果": "Results", "输入影视名称开始搜索": "Enter a title to start searching", "排序": "Sort", "匹配优先": "Best match", "季集顺序": "Episode order", "文件大小": "File size", "每页": "Per page", "20 条": "20", "50 条": "50",
  "资源 / 季集": "Resource / episode", "画质": "Quality", "大小": "Size", "来源频道": "Channel", "操作": "Action", "还没有搜索结果": "No search results yet",
  "下载、入库和替换的进度，都在这里。": "Track downloads, imports, and replacements.", "全部": "All", "媒体 / 来源": "Media / channel", "状态与进度": "Status / progress", "入库目标": "Library destination",
  "为喜欢的剧集设置来源与追更起点。": "Choose channels and the next episode to track.", "＋ 新建订阅": "+ New subscription", "管理搜索与追更使用的资源来源。": "Manage channels used for search and subscriptions.", "＋ 添加频道": "+ Add channel",
  "管理连接，查看运行状态与备份。": "Manage your connection, diagnostics, and backups.", "Telegram 连接": "Telegram connection", "正在读取连接状态": "Checking connection", "连接 Telegram": "Connect Telegram", "连接状态由后台服务保存。": "The connection is saved by the service.", "运行状态": "Service status", "正在读取状态": "Checking status",
  "备份任务、资源与订阅记录。": "Back up tasks, resources, and subscriptions.", "此备份不含登录凭据。完整恢复操作请查看运维文档。": "Credentials are not included. See the operations documentation for full recovery.",
  "添加频道": "Add channel", "选择已加入频道，或粘贴频道链接。": "Choose a joined channel or paste a link.", "从已加入频道选择": "Choose a joined channel", "查找频道": "Find a channel", "输入频道名称": "Channel name", "频道链接": "Channel link", "可选": "Optional", "也支持 @用户名、数字频道 ID 或已加入频道的 t.me/c/… 消息链接。": "Also accepts @usernames, numeric IDs, and message links from joined private channels.",
  "编辑": "Edit", "编辑追更订阅": "Edit subscription", "保存修改": "Save changes", "重新载入": "Reload", "正在保存…": "Saving…", "订阅已更新": "Subscription updated",
  "订阅已被扫描或其他操作更新,请重新载入后再保存。": "This subscription changed. Reload it before saving.",
  "修改只影响后续追更，已创建的下载任务保持原计划。": "Changes apply to future tracking. Existing download tasks keep their original plans.",
  "已连接": "Connected",
  "Telegram 代理连接失败。请检查代理服务和出口节点。": "Telegram proxy connection failed. Check the proxy service and exit node.",
  "需要登录": "Sign-in required",
  "连接不可用": "Connection unavailable",
  "连接超时，请检查代理或网络。": "Connection timed out. Check the proxy or network.",
  "代理连接失败，请检查出口节点。": "Proxy connection failed. Check the exit node.",
  "连接失败，请检查代理或网络。": "Connection failed. Check the proxy or network.",
  "请求限流，请等待后重试。": "Rate limited. Wait before retrying.",
  "连接检查失败，请查看服务日志。": "Connection check failed. Check the service logs.",
  "新建追更订阅": "New subscription", "设置从哪一集开始，以及使用哪些频道。": "Choose a starting episode and channels.", "从第几集追更": "Next episode", "补集设置": "Missing episodes", "补齐后继续追更": "Continue tracking after filling gaps",
  "下载资源": "Download resource", "确认媒体信息与入库位置。": "Confirm media details and library destination.", "入库位置预览": "Destination preview", "填写媒体信息后显示": "Enter media details", "仅下载的文件保留在下载收件箱，之后可从任务页入库。": "Download-only files stay in the inbox and can be imported from Tasks.",
  "入库媒体": "Import media", "确认媒体信息和目标位置。": "Confirm media details and destination.", "详情": "Details", "资源详情": "Resource details", "任务详情": "Task details", "下载": "Download", "入库": "Import", "继续": "Resume",
  "等待入库": "Waiting for import", "等待手动入库": "Awaiting manual import", "入库中": "Importing", "已入库": "Imported", "暂存": "Provisional", "集数待确认": "Confirm episode", "未识别季集": "Episode not identified",
  "原始文件名": "Original filename", "季集": "Episode", "语言标记": "Language tags", "匹配分数": "Match score", "原消息": "Original message", "打开原消息 →": "Open message →", "输出路径": "Output path", "下载方式": "Download mode", "仅下载": "Download only", "下载并入库": "Download and import", "查看时间线": "View timeline", "没有进行中的任务": "No active tasks", "尚无入库记录": "No imports yet", "还没有追更订阅": "No subscriptions yet",
  "服务正常 · Telegram 已连接": "Service ready · Telegram connected", "Telegram 需要登录": "Telegram sign-in required", "Telegram 连接异常": "Telegram connection unavailable", "服务连接中断": "Service connection interrupted", "服务运行受限": "Service not ready", "查看连接设置 →": "Connection settings →",
  "Telegram 暂时无法连接，已有任务记录仍可查看。": "Telegram is unavailable. Existing task records remain accessible.", "连接 Telegram 后即可搜索和下载资源。": "Connect Telegram to search and download resources.", "正在等待后台状态检查。": "Waiting for service checks.", "未读取到最新状态，请重试。": "Could not refresh status. Please retry.", "取消这个任务？不会删除媒体库中的文件。": "Cancel this task? Library files will not be deleted.", "自动替换": "Automatic replacement", "未设置入库计划": "No import plan", "状态": "Status", "下载进度": "Download progress", "尝试次数": "Attempts", "已暂停追更": "Tracking paused", "正在追更": "Tracking active",
};

function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = text;
  return node;
}

function newIdempotencyKey() {
  if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
  // getRandomValues also works on LAN HTTP; these IDs are not authentication tokens.
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map(byte => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}

let editingSubscription = null;
let subscriptionChannelDraft = new Set();
let subscriptionEditorGeneration = 0;

function openSubscriptionEditor(value = null) {
  subscriptionEditorGeneration++;
  editingSubscription = value ? {...value} : null;
  const form = $("#subscription-form");
  form.reset();
  form.querySelectorAll(".form-error").forEach(node=>node.remove());
  subscriptionChannelDraft = new Set(value?.channels || []);
  $("#subscription-title").value = value?.title || "";
  $("#subscription-query").value = value?.query || "";
  $("#subscription-season").value = value?.season ?? 1;
  $("#subscription-episode").value = value?.next_episode ?? 1;
  $("#subscription-pending-episodes").value = (value?.pending_episodes || []).join(", ");
  $("#subscription-continue").checked = value?.continue_tracking ?? true;
  $("#subscription-dialog h2").textContent = t(value ? "编辑追更订阅" : "新建追更订阅");
  $("#subscription-submit").textContent = t(value ? "保存修改" : "创建订阅");
  $("#subscription-reload").hidden = !value;
  for (const details of form.querySelectorAll("details")) {
    details.open = details.classList.contains("subscription-channel-scope")
      ? !!value?.channels?.length : !!value?.pending_episodes?.length || value?.continue_tracking === false;
  }
  updateSubscriptionChannelChoice([]);
  if (!$("#subscription-dialog").open) $("#subscription-dialog").showModal();
  loadSearchChannels();
}

function selectedSubscriptionChannels() {
  return [...$("#subscription-channels").querySelectorAll("input:checked")].map(input => input.value);
}

function updateSubscriptionChannelSummary() {
  const count = selectedSubscriptionChannels().length;
  $("#subscription-channel-summary").textContent = count
    ? t("已选择 {count} 个频道").replace("{count}", String(count)) : t("全部默认频道");
}

function updateSubscriptionChannelChoice(values) {
  const root = $("#subscription-channels");
  const selected = subscriptionChannelDraft;
  const choices = values.filter(value => value.enabled || selected.has(value.identifier));
  for (const identifier of selected) {
    if (!choices.some(channel=>channel.identifier === identifier)) choices.push({identifier});
  }
  root.replaceChildren();
  for (const channel of choices) {
    const label = el("label", "subscription-channel-option");
    const input = el("input"); input.type = "checkbox"; input.value = channel.identifier;
    input.checked = selected.has(channel.identifier);
    const info = el("span");
    info.append(el("strong", "", channel.name || channel.identifier), el("small", "", channel.identifier));
    label.append(input, info); root.append(label);
  }
  if (!root.children.length) root.textContent = t("暂无已启用频道，请先到频道页添加或启用。");
  updateSubscriptionChannelSummary();
}

function pageSlice(items, index, size) {
  const pageCount = Math.max(1, Math.ceil(items.length / size));
  const boundedIndex = Math.max(0, Math.min(index, pageCount - 1));
  return { items: items.slice(boundedIndex * size, (boundedIndex + 1) * size), index: boundedIndex, pageCount };
}

function episodeLabel(resource) {
  if (resource?.episode_conflict) return t("集数待确认");
  const episode = resource?.episode_range;
  if (!episode) return t("未识别季集");
  return `S${String(episode.season).padStart(2, "0")}E${String(episode.start).padStart(2, "0")}${episode.end > episode.start ? `–E${String(episode.end).padStart(2, "0")}` : ""}`;
}

function taskLabel(task) {
  if (task.state === "downloaded") return t(task.archive_plan ? "等待入库" : "等待手动入库");
  return t({importing: "入库中", imported: "已入库", provisional: "暂存"}[task.state] || taskStateLabels[task.state] || task.state);
}

function taskDestination(task) {
  const plan = task.archive_plan;
  if (!plan) return t("未设置入库计划");
  return plan.kind === "movie" ? `${t("电影库")} / ${plan.title}${plan.year ? ` (${plan.year})` : ""}`
    : `${t("电视剧库")} / ${plan.title} / S${String(plan.season).padStart(2, "0")}E${String(plan.episode).padStart(2, "0")}${plan.episode_end > plan.episode ? `–E${String(plan.episode_end).padStart(2, "0")}` : ""}`;
}

function taskTitle(task, resource) {
  const plan = task.archive_plan;
  return plan ? `${plan.title}${plan.kind === "series" ? ` · S${String(plan.season).padStart(2,"0")}E${String(plan.episode).padStart(2,"0")}${plan.episode_end > plan.episode ? `–E${String(plan.episode_end).padStart(2,"0")}` : ""}` : ""}`
    : resource?.file_name || `${t("任务")} ${task.id.slice(0, 8)}`;
}

function percentFor(task) {
  return task.expected_size ? Math.max(0, Math.min(100, task.downloaded_bytes / task.expected_size * 100)) : 0;
}

function mediaCell(name, sub, action, series = false) {
  const cell = el("div", "media-cell");
  cell.append(el("span", "media-icon", series ? "▤" : "▶"));
  const info = el("div", "media-text");
  const title = el("button", "media-name", name);
  title.type = "button";
  title.title = name;
  title.addEventListener("click", action);
  info.append(title, sub);
  cell.append(info);
  return cell;
}

function initializeWorkspace() {
  try { queueVisible = localStorage.getItem("tma-queue-visible") !== "false"; } catch (_error) { /* Optional preference. */ }
  selectedResources.clear();pendingResourceBatch=[];updateResourceSelection();
  for(const item of document.querySelectorAll("[data-task-view]"))item.classList.toggle("selected",item.dataset.taskView===$("#task-filter").value);
  navigateWorkspace(false);
}

function animateWorkspace(node, distance = 8) {
  node?.getAnimations().forEach(animation => animation.cancel());
  if (!node || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
  node.animate([{opacity:.5,transform:`translateY(${distance}px)`},{opacity:1,transform:"translateY(0)"}],{duration:260,easing:"cubic-bezier(.22,1,.36,1)"});
}

function updateNavigationMarker() {
  const marker=$(".nav-marker"),active=$(".workspace-nav a.active");
  marker.getAnimations().forEach(animation=>animation.cancel());
  if (!active) { marker.hidden=true;return; }
  marker.hidden=false;
  const before=marker.getBoundingClientRect(),after=active.getBoundingClientRect();
  marker.style.width=`${active.offsetWidth}px`;marker.style.transform=`translateX(${active.offsetLeft}px)`;
  if(before.width&& !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) marker.animate([{transform:`translateX(${active.offsetLeft+before.left-after.left}px) scaleX(${before.width/after.width})`},{transform:`translateX(${active.offsetLeft}px) scaleX(1)`}],{duration:300,easing:"cubic-bezier(.22,1,.36,1)"});
}

function setQueueVisible(visible) {
  queueVisible=visible;
  const expanded=currentPage==="tasks-panel",settings=currentPage==="operations";
  $("#section-tasks-panel").hidden=settings||(!visible&&!expanded);
  $("#section-tasks-panel").open=true;
  $(".workbench-grid").classList.toggle("queue-hidden",!visible);
  $("#toggle-sections").hidden=settings||expanded;
  $("#toggle-sections").setAttribute("aria-expanded",String(visible));
  $("#toggle-sections").firstChild.textContent=t(visible?"收起队列 ":"展开队列 ");
  try { localStorage.setItem("tma-queue-visible",String(visible)); } catch (_error) { /* Optional preference. */ }
  if(visible)animateWorkspace($("#section-tasks-panel"));
}

function navigateWorkspace(refresh = true) {
  const raw = window.location.hash.slice(1),requested=({resources:"discovery",subscriptions:"subscriptions-panel",channels:"channels-panel",overview:"discovery"})[raw]||raw;
  currentPage = Object.hasOwn(workspacePages, requested) ? requested : "discovery";
  if (requested !== currentPage) window.history.replaceState(null, "", `#${currentPage}`);
  for (const page of document.querySelectorAll("[data-page]")) page.hidden = page.dataset.page !== (currentPage === "operations" ? "operations" : "overview");
  for (const link of document.querySelectorAll(".workspace-nav a")) {
    const active = link.getAttribute("href") === `#${currentPage}`;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
  }
  const title = t({discovery:"今晚，想看什么？","subscriptions-panel":"好剧，接着看。","channels-panel":"你的资源来源。","tasks-panel":"下载与入库记录",operations:"设置"}[currentPage]);
  $("#page-title").textContent = title;
  $("#page-description").textContent=t({discovery:"找到好资源，下载完成后自动入库。","subscriptions-panel":"追更进度、来源和开关，一处掌握。","channels-panel":"搜索和追更，来自你信任的频道。","tasks-panel":"查看进度、来源、入库结果，处理异常任务。",operations:"管理连接，查看运行状态与备份。"}[currentPage]);
  document.title = `${title} · Telegram Media Automation`;
  for(const id of ["discovery","subscriptions-panel","channels-panel"]){const section=document.getElementById(`section-${id}`);section.hidden=id!==currentPage;section.open=true;}
  $(".workbench-grid").classList.toggle("tasks-expanded",currentPage==="tasks-panel");
  setQueueVisible(queueVisible);updateNavigationMarker();
  animateWorkspace(document.getElementById(`section-${currentPage}`)||$("#operations"));
  if(refresh)$("#main-content").focus({preventScroll:true});
  if (refresh && apiConnected) refreshWorkspacePage();
}

function refreshWorkspacePage() {
  if (currentPage === "discovery") { loadTasks(); loadTaskSummary(true); loadSearchChannels(); }
  if (currentPage === "tasks-panel") loadTasks();
  if (currentPage === "subscriptions-panel") loadSubscriptions();
  if (currentPage === "channels-panel") loadSearchChannels();
  if (currentPage === "operations") { telegramStatus(); loadSystemStatus(); loadBackups(); }
}

function updateResourceSelection(){const count=selectedResources.size;$("#selection-info").textContent=count?(currentLocale==="en-US"?`${count} selected`:`已选 ${count} 个资源`):t("选择资源后可逐个确认下载");$("#batch-download").disabled=!count;$("#clear-selection").disabled=!count;}
async function advanceResourceBatch(resourceId){selectedResources.delete(resourceId);pendingResourceBatch=pendingResourceBatch.filter(resource=>resource.id!==resourceId);renderSearchPage();if(pendingResourceBatch.length)await prepareEnqueue(pendingResourceBatch[0]);}

function setSearchResults(resources) {
  searchResults = resources;
  selectedResources.clear();pendingResourceBatch=[];updateResourceSelection();
  resultPageIndex = 0;
  for (const resource of resources) resourceCache.set(resource.id, resource);
  $("#search-feedback").textContent = currentLocale === "en-US" ? `Found ${resources.length} resources in this search` : `本次找到 ${resources.length} 条资源`;
  renderSearchPage();
}

function renderSearchPage() {
  const sorted = [...searchResults];
  const sort = $("#result-sort").value;
  if (sort === "size") sorted.sort((a, b) => a.file_size - b.file_size);
  if (sort === "episode") sorted.sort((a, b) => (a.episode_range?.season ?? 9999) - (b.episode_range?.season ?? 9999) || (a.episode_range?.start ?? Infinity) - (b.episode_range?.start ?? Infinity));
  const page = pageSlice(sorted, resultPageIndex, Number($("#result-page-size").value));
  resultPageIndex = page.index;
  const root = $("#results");
  root.replaceChildren();
  root.className = sorted.length ? "results" : "results empty";
  if (!sorted.length) root.textContent = t("没有找到可下载文件");
  for (const resource of page.items) {
    const row = el("article", `resource-row ${selectedResources.has(resource.id)?"selected":""}`);
    const top=el("div","resource-top"),mark=el("span","episode-mark");mark.setAttribute("aria-hidden","true");mark.append(el("small","",resource.episode_range&&!resource.episode_conflict?"EP":"FILE"),el("b","",resource.episode_conflict?"?":String(resource.episode_range?.start??"•")));
    const name=el("div","resource-name"),title=button(resourceDisplayName(resource),()=>showResourceDetails(resource));title.className="media-name";title.title=resource.file_name;
    name.append(title,el("div",resource.episode_conflict?"media-sub warning":"media-sub",episodeLabel(resource)));
    const selection=el("label","selection-target"),checkbox=el("input");checkbox.type="checkbox";checkbox.checked=selectedResources.has(resource.id);checkbox.setAttribute("aria-label",`${t("选择资源")} ${resource.file_name}`);checkbox.addEventListener("change",()=>{if(checkbox.checked)selectedResources.set(resource.id,resource);else selectedResources.delete(resource.id);row.classList.toggle("selected",checkbox.checked);updateResourceSelection();});selection.append(checkbox);top.append(mark,name,selection);
    const quality = resource.quality?.resolution ? `${resource.quality.resolution}p` : "—";
    const qualityCell = el("div", "quality-cell", quality);
    qualityCell.append(el("span", "cell-secondary", `${resource.quality?.codec || "—"}${resource.quality?.hdr ? " · HDR" : ""}`));
    const source = el("div", "source-cell");
    source.append(sourceElement(resource) || el("span", "muted", "—"));
    const actions = el("div", "actions");
    const download = button(t("下载"), () => prepareEnqueue(resource));
    download.classList.add("primary");
    actions.append(button(t("替换"), () => prepareEnqueue(resource,true)), download);
    const meta=el("div","resource-meta"),bottom=el("div","resource-bottom");meta.append(qualityCell,el("div","size-cell",humanBytes(resource.file_size)));bottom.append(source,actions);row.append(top,meta,bottom);
    root.append(row);
  }
  $("#result-count").textContent = sorted.length;
  $("#result-pager").hidden = !sorted.length;
  $("#result-previous").disabled = page.index === 0;
  $("#result-next").disabled = page.index + 1 >= page.pageCount;
  $("#result-page-status").textContent = currentLocale === "en-US" ? `Page ${page.index + 1} of ${page.pageCount}` : `第 ${page.index + 1} / ${page.pageCount} 页`;
  updateResourceSelection();
}

function resourceDisplayName(resource){const filename=resource.file_name.replace(/\.[^.]+$/,"");const prefix=filename.split(/S\d{1,3}E\d+|\b(?:2160|1080|720|480)p\b/i)[0].replace(/[._\s-]+$/g,"");return prefix||filename;}

function facts(items) {
  const list = el("dl", "detail-facts");
  for (const [label, value] of items) {
    const row = el("div");
    const detail = el("dd");
    if (value instanceof Node) detail.append(value); else detail.textContent = value ?? "—";
    row.append(el("dt", "", t(label)), detail);
    list.append(row);
  }
  return list;
}

function openDetails(kind, title, content, actions) {
  $("#detail-kind").textContent = t(kind);
  $("#detail-title").textContent = title;
  $("#detail-content").replaceChildren(content);
  $("#detail-actions").replaceChildren(...actions);
  if (!$("#detail-dialog").open) $("#detail-dialog").showModal();
}

function showResourceDetails(resource) {
  const items = [["原始文件名", resource.file_name], ["季集", episodeLabel(resource)], ["画质", `${resource.quality?.resolution ? `${resource.quality.resolution}p` : "—"} · ${resource.quality?.codec || "—"}${resource.quality?.hdr ? " · HDR" : ""}`], ["大小", humanBytes(resource.file_size)], ["语言标记", resource.quality?.languages?.join(" / ") || "—"], ["来源频道", sourceElement(resource) || "—"]];
  const forwarded = sourceElement(resource, true);
  if (forwarded) items.push(["转发自", forwarded]);
  items.push(["匹配分数", String(resource.identity_confidence ?? "—")]);
  openDetails("资源详情", resource.file_name, facts(items), [button(t("替换"), async () => { $("#detail-dialog").close(); await prepareEnqueue(resource,true); }),button(t("下载资源"), async () => { $("#detail-dialog").close(); await prepareEnqueue(resource); })]);
}

function taskActions(task, expanded = false) {
  const actions = [];
  if (!expanded) actions.push(button(t("详情"), () => showTaskDetails(taskCache.get(task.id) || task)));
  if (["queued", "downloading"].includes(task.state)) actions.push(button(t("暂停"), () => act(task.id, "pause")));
  if (task.state === "paused") actions.push(button(t("继续"), () => act(task.id, "resume")));
  if (["failed", "retrying"].includes(task.state)) actions.push(button(t("重试"), () => act(task.id, "retry")));
  if (task.state === "downloaded" && !task.archive_plan) actions.push(button(t("入库"), async () => { $("#detail-dialog").close(); await importTask(task.id); }));
  if (expanded) {
    actions.push(button(t("查看时间线"), async () => { $("#detail-dialog").close(); await showEvents(task.id); }));
    if (task.state === "downloaded") actions.push(button(t("替换"), async () => { $("#detail-dialog").close(); await importTask(task.id, true); }));
    if (["queued","downloading","paused","retrying"].includes(task.state)) actions.push(button(t("设为下载后替换"),async()=>{$("#detail-dialog").close();await importTask(task.id,true,true);}));
    if (!["imported", "provisional", "cancelled", "importing"].includes(task.state)) actions.push(button(t("取消"), async () => {
      if (!window.confirm(t("取消这个任务？不会删除媒体库中的文件。"))) return;
      await act(task.id, "cancel");
      $("#detail-dialog").close();
    }, true));
  }
  return actions;
}

function showTaskDetails(task) {
  const resource = resourceCache.get(task.resource_id);
  const content = el("div");
  content.append(facts([["状态", taskLabel(task)], ["原始文件名", resource?.file_name || "—"], ["来源频道", sourceElement(resource) || "—"], ["下载进度", `${percentFor(task).toFixed(1)}% · ${humanBytes(task.downloaded_bytes)} / ${humanBytes(task.expected_size)}`], ["下载方式", t(task.archive_plan?.replace_existing ? "自动替换" : task.archive_plan ? "下载并入库" : "仅下载")], ["入库目标", taskDestination(task)], ["输出路径", task.output_path || "—"], ["尝试次数", String(task.attempt_count)], ["任务", task.id]]));
  if (task.error_message) content.append(el("p", "detail-error", task.error_message));
  openDetails("任务详情", taskTitle(task, resource), content, taskActions(task, true));
}

function updateProgress(row, task) {
  const percent = percentFor(task);
  row.querySelector(".progress i").style.width = `${percent}%`;
  row.querySelector(".progress-percent").textContent = `${percent.toFixed(1)}%`;
  row.querySelector(".progress-bytes").textContent = `${humanBytes(task.downloaded_bytes)} / ${humanBytes(task.expected_size)}`;
}

function updateTaskRows(tasks) {
  const root = $("#tasks");
  const existing = new Map([...root.children].map(node => [node.dataset.taskId, node]));
  if (!tasks.length) { root.className = "tasks empty"; root.textContent = t("暂无任务"); return; }
  if (root.classList.contains("empty")) root.replaceChildren();
  root.className = "tasks";
  tasks.forEach((task, index) => {
    taskCache.set(task.id, task);
    const resource = resourceCache.get(task.resource_id);
    const signature = JSON.stringify([task.state, task.archive_plan, resource?.file_name, task.error_message, task.output_path]);
    let row = existing.get(task.id);
    if (!row || row.dataset.signature !== signature) {
      const old = row;
      row = el("div", "task-row task-columns");
      row.dataset.taskId = task.id;
      row.dataset.signature = signature;
      const sub = el("div", "media-sub");
      sub.append(sourceElement(resource) || el("span", "", "—"));
      row.append(mediaCell(taskTitle(task, resource), sub, () => showTaskDetails(taskCache.get(task.id)), resource?.episode_range || task.archive_plan?.kind === "series"));
      const status = el("div", "task-progress");
      status.append(el("span", `state-pill state-${task.state}`, taskLabel(task)));
      const track = el("div", "progress"); track.append(el("i"));
      const caption = el("div", "progress-caption"); caption.append(el("span", "progress-percent"), el("span", "progress-bytes"));
      status.append(track, caption);
      const actions = el("div", "actions"); actions.append(...taskActions(task));
      row.append(status, el("div", "target-cell", taskDestination(task)), actions);
      if (old) old.replaceWith(row);
    }
    updateProgress(row, task);
    if (root.children[index] !== row) root.insertBefore(row, root.children[index] || null);
    existing.delete(task.id);
  });
  for (const row of existing.values()) row.remove();
  $("#task-updated").textContent = currentLocale === "en-US" ? "Just updated" : "刚刚更新";
}

async function cacheTaskResources(tasks) {
  const ids = [...new Set(tasks.map(task => task.resource_id).filter(id => !resourceCache.has(id)))];
  await Promise.all(ids.map(async id => { try { const resource = await api(`/api/v1/resources/${id}`); resourceCache.set(id, resource); } catch (_error) { /* Retain task without historical metadata. */ } }));
  for (const task of tasks) taskCache.set(task.id, task);
}

function updateChannelChoice(values) {
  updateSubscriptionChannelChoice(values);
  const choice = $("#search-channel"); const selected = choice.value;
  choice.replaceChildren(new Option(t("所有默认频道"), ""));
  for (const value of values.filter(channel => channel.enabled)) choice.add(new Option(value.name || value.identifier, value.identifier));
  choice.value = selected;
  if (choice.selectedIndex < 0) choice.value = "";
}

function renderJoinedChannels() {
  const root = $("#joined-channel-list"); root.replaceChildren();
  const query = $("#joined-query").value.toLocaleLowerCase();
  const matching = joinedChannels.filter(channel => `${channel.title} ${channel.identifier}`.toLocaleLowerCase().includes(query));
  for (const channel of matching) {
    const choice = button(channel.title, () => { $("#channel-identifier").value = channel.identifier; $("#channel-name").value = channel.title; $("#joined-picker").hidden = true; });
    choice.append(el("span", "", t(channel.visibility === "public" ? "公开频道" : "私有频道"))); root.append(choice);
  }
  if (!matching.length) root.textContent = t("没有找到可用频道");
}

function updateGlobalStatus() {
  let state = "checking", label = "正在连接", alert = "";
  if (statusAvailable === false) { state = "error"; label = "服务连接中断"; alert = "未读取到最新状态，请重试。"; }
  else if (telegramStage === "authorized" && lastSystemStatus?.readiness) { state = "ready"; label = "服务正常 · Telegram 已连接"; }
  else if (["login_required", "code_sent", "password_required"].includes(telegramStage)) { state = "warning"; label = "Telegram 需要登录"; alert = "连接 Telegram 后即可搜索和下载资源。"; }
  else if (telegramStage === "unavailable") { state = "warning"; label = "Telegram 连接异常"; alert = "Telegram 暂时无法连接，已有任务记录仍可查看。"; }
  else if (telegramStage === "authorized") { state = "warning"; label = "服务运行受限"; }
  $("#service-indicator").dataset.state = state;
  $("#service-label").textContent = t(label);
  $("#service-indicator").title = $("#status-checked").textContent;
  $("#service-alert").hidden = !alert;
  $("#service-alert-copy").textContent = t(alert);
}

function updateDestinationPreview() {
  const title = $("#enqueue-title").value.trim();
  const kind = $("#enqueue-kind").value;
  const year = $("#enqueue-year").value;
  const label = `${title}${year ? ` (${year})` : ""}`;
  $("#enqueue-destination").textContent = !title ? t("填写媒体信息后显示") : kind === "movie"
    ? `${t("电影库")} / ${label} / ${label}`
    : `${t("电视剧库")} / ${label} / Season ${String($("#enqueue-season").value).padStart(2,"0")} / ${title} - S${String($("#enqueue-season").value).padStart(2,"0")}E${String($("#enqueue-episode").value).padStart(2,"0")}${$("#enqueue-episode-end").value ? `-E${String($("#enqueue-episode-end").value).padStart(2,"0")}` : ""}`;
}

window.addEventListener("hashchange", () => { if (typeof apiConnected !== "undefined" && apiConnected) navigateWorkspace(); });
document.addEventListener("DOMContentLoaded", () => {
  window.addEventListener("resize", updateNavigationMarker);
  window.matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change",()=>document.getAnimations().forEach(animation=>animation.cancel()));
  for (const control of document.querySelectorAll("[data-close]")) control.addEventListener("click", () => document.getElementById(control.dataset.close).close());
  $("#new-channel").addEventListener("click", () => $("#channel-dialog").showModal());
  $("#new-subscription").addEventListener("click", () => openSubscriptionEditor());
  $("#subscription-channels").addEventListener("change", () => {
    subscriptionChannelDraft = new Set(selectedSubscriptionChannels());
    updateSubscriptionChannelSummary();
  });
  $("#subscription-reload").addEventListener("click", async event => {
    const id = editingSubscription?.id;
    if (!id) return;
    try {
      const value = await runBusy(event.currentTarget, "正在读取订阅…", () => api(`/api/v1/subscriptions/${id}`), $("#subscription-form"));
      if (editingSubscription?.id === id) openSubscriptionEditor(value);
    } catch (error) { toast(error.message); }
  });
  $("#connect-telegram").addEventListener("click", () => $("#telegram-dialog").showModal());
  $("#service-indicator").addEventListener("click", () => { window.location.hash = "operations"; });
  $("#joined-query").addEventListener("input", renderJoinedChannels);
  $("#toggle-sections").addEventListener("click", () => setQueueVisible(!queueVisible));
  $("#section-tasks-panel").addEventListener("toggle",()=>{if(currentPage!=="tasks-panel"&&!$("#section-tasks-panel").open)setQueueVisible(false);});
  $("#clear-selection").addEventListener("click",()=>{selectedResources.clear();pendingResourceBatch=[];renderSearchPage();});
  $("#batch-download").addEventListener("click",async()=>{pendingResourceBatch=[...selectedResources.values()];if(pendingResourceBatch.length){try{await prepareEnqueue(pendingResourceBatch[0]);}catch(error){toast(error.message);}}});
  $("#enqueue-dialog").addEventListener("cancel",()=>{pendingResourceBatch=[];enqueuePreparationGeneration++;});
  for (const dialog of document.querySelectorAll("dialog"))new MutationObserver(()=>{if(dialog.open){dialog.querySelectorAll(".form-error").forEach(node=>node.remove());animateWorkspace(dialog,12);}}).observe(dialog,{attributes:true,attributeFilter:["open"]});
  for (const link of document.querySelectorAll(".workspace-nav a")) link.addEventListener("click", event => {
    if (link.hash === window.location.hash) { event.preventDefault(); navigateWorkspace(false); }
  });
  for (const selector of ["#result-sort", "#result-page-size"]) $(selector).addEventListener("change", () => { resultPageIndex = 0; renderSearchPage(); });
  $("#result-previous").addEventListener("click", () => { resultPageIndex -= 1; renderSearchPage(); $("#results").scrollTop=0; });
  $("#result-next").addEventListener("click", () => { resultPageIndex += 1; renderSearchPage(); $("#results").scrollTop=0; });
  for (const tab of document.querySelectorAll("[data-filter]")) tab.addEventListener("click", () => { $("#task-filter").value = tab.dataset.filter; $("#task-filter").dispatchEvent(new Event("change")); });
  for (const link of document.querySelectorAll("[data-completed-link]")) link.addEventListener("click", () => { $("#task-filter").value = "complete"; taskPageIndex = 0; });
  for (const control of document.querySelectorAll("#enqueue-form input, #enqueue-form select")) control.addEventListener("input", updateDestinationPreview);
});
