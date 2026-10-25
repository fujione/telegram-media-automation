const $ = (selector) => document.querySelector(selector);
let apiConnected = false;
let dockerBootstrapRequired = false;
let currentLocale = "zh-CN";
let currentTimeZone = "Asia/Shanghai";
const resourceCache = new Map();
const channelInspectionCache = new Map();
let taskPageIndex = 0;
let enqueueIdempotencyKey = null;
const taskPageSize = 20;
const taskFilterStates = {
  all: [],
  active: ["pending", "searching", "candidate_selected", "queued", "downloading", "paused", "retrying", "downloaded", "importing"],
  attention: ["needs_selection", "failed"],
  complete: ["imported", "provisional", "cancelled"],
};
const translations = {
  "en-US": {
    "替换": "Replace",
    "下载后替换媒体库中的已有文件": "Replace an existing library file after download",
    "替换媒体库中的已有文件": "Replace an existing library file",
    "查找已有文件": "Find existing files",
    "替换目标": "Replacement target",
    "先填写片名并查找已有文件": "Enter a title and find existing files first",
    "请核对目标的季和集数。旧文件会保留备份，新文件校验成功后才会替换。": "Check the target season and episode. The original is backed up before the verified file replaces it.",
    "下载并自动替换": "Download and replace",
    "确认替换": "Confirm replacement",
    "请先填写片名": "Enter a title first",
    "请选择要替换的文件": "Select the file to replace",
    "未找到匹配的媒体库文件": "No matching library files found",
    "已加入队列，下载完成后将自动替换": "Queued and will replace the existing file after download",
    "自动替换至": "auto-replace at",
    "替换完成，旧文件备份已保留": "Replacement complete; original backup retained",
    "你的 Telegram，": "Your Telegram,",
    "你的媒体库。": "your media library.",
    "搜索、下载、追更与归档都留在自己的设备里。没有 Bot，没有转发，也没有云端账号。": "Search, download, follow and archive on your own device. No bot, forwarding or cloud account.",
    "精确搜索指定频道": "Search selected channels",
    "断点续传与可靠任务": "Resumable, reliable tasks",
    "规范整理进媒体库": "Organized media libraries",
    "正在检查服务…": "Checking the service…",
    "确认本地管理员状态，请稍候。": "Checking the local administrator status.",
    "用户名": "Username",
    "密码": "Password",
    "登录控制台": "Sign in",
    "管理员用户名": "Administrator username",
    "管理员密码": "Administrator password",
    "确认密码": "Confirm password",
    "创建账号并进入": "Create account and continue",
    "账号只保存在本机，密码以加盐哈希保存。": "The account stays on this device and the password is stored as a salted hash.",
    "概览": "Overview",
    "资源发现": "Discovery",
    "下载任务": "Downloads",
    "追更订阅": "Subscriptions",
    "系统运维": "Operations",
    "控制台已登录": "Console signed in",
    "退出登录": "Sign out",
    "本地优先 · 单用户 · Telegram-first": "Local-first · Single user · Telegram-first",
    "Telegram Media Automation 首页": "Telegram Media Automation home",
    "主导航": "Main navigation",
    "媒体控制台": "Media console",
    "搜索": "Search",
    "下载": "Download",
    "归档": "Archive",
    "把想看的内容，": "Put what you want to watch",
    "安静地放进媒体库。": "quietly into your library.",
    "只搜索你指定的频道，只下载你选择的影视资源。没有 Bot、转发、云盘同步，也没有多余的监控。": "Search only channels you select and download only media you choose. No bot, forwarding, cloud sync or generic monitoring.",
    "找到资源": "Find media",
    "可靠下载": "Reliable download",
    "规范归档": "Organized archive",
    "当前任务概览": "Task overview",
    "异常任务优先处理，其余下载会由后台可靠执行。": "Handle exceptions first; the service runs all other downloads reliably in the background.",
    "搜索新资源": "Search for media",
    "查看完整队列": "View full queue",
    "正在处理": "In progress",
    "排队、下载与归档": "Queued, downloading and archiving",
    "等待选择或失败": "Waiting for selection or failed",
    "已归档或已取消": "Archived or cancelled",
    "Telegram 后台账号": "Telegram account",
    "服务级 Session，与当前浏览器登录相互独立。": "A service-level session independent of the current browser login.",
    "正在读取后台状态": "Reading service status",
    "手机号": "Phone number",
    "发送验证码": "Send code",
    "验证码": "Verification code",
    "验证": "Verify",
    "两步验证密码": "Two-step verification password",
    "提交密码": "Submit password",
    "只有后台 Session 确实未登录时才显示验证码入口。验证码与两步验证密码不会写入配置或日志。": "The verification flow appears only when the service session is signed out. Codes and two-step passwords are never written to configuration or logs.",
    "运行概况": "System status",
    "服务、Worker 与存储的当前状态。": "Current service, worker and storage status.",
    "刷新状态": "Refresh status",
    "正在读取运行状态": "Reading system status",
    "先定义可信来源，再进行精确搜索。": "Define trusted sources, then search precisely.",
    "默认搜索频道": "Default search channels",
    "管理常用的 Telegram 资源来源。": "Manage frequently used Telegram sources.",
    "频道标识": "Channel identifier",
    "https://t.me/channel_name": "https://t.me/channel_name",
    "“点击加入”不能判断公开或私有。公开频道通常有 t.me/名称；已加入的私有频道请粘贴 t.me/c/... 消息链接或填写 -100 数字 ID，系统会先连接 Telegram 验证并标记类型。": "A Join button does not indicate visibility. Public channels usually have a t.me/name URL. For a joined private channel, paste a t.me/c/... message link or enter its -100 ID; the service validates it with Telegram and labels its type.",
    "备注名": "Display name",
    "验证并添加": "Verify and add",
    "发现已加入频道": "Discover joined channels",
    "暂无默认频道": "No default channels",
    "搜索 Telegram": "Search Telegram",
    "按片名、季集和临时频道筛选视频。": "Filter video by title, episode and temporary channel.",
    "影视名称": "Title",
    "临时频道": "Temporary channels",
    "开始搜索": "Search",
    "尚未搜索": "No search yet",
    "查看进度、重试、暂停并归档。": "Track progress, retry, pause and archive.",
    "刷新任务": "Refresh tasks",
    "任务筛选": "Task filter",
    "全部任务": "All tasks",
    "进行中": "Active",
    "需要处理": "Needs attention",
    "已结束": "Finished",
    "上一页": "Previous",
    "下一页": "Next",
    "暂无任务": "No tasks",
    "电视剧追更": "Series subscriptions",
    "只轮询你明确创建的影视订阅。": "Poll only the media subscriptions you explicitly create.",
    "按集自动选择": "Automatic episode selection",
    "剧名": "Series title",
    "搜索词": "Search query",
    "季": "Season",
    "下一集": "Next episode",
    "缺集补齐（高级）": "Fill missing episodes (advanced)",
    "需补集数（可选）": "Missing episodes (optional)",
    "例如 2, 4-6": "For example 2, 4-6",
    "补齐后继续追更“下一集”": "Continue from Next episode after filling gaps",
    "只补缺集时取消勾选；“下一集”不会因补较早的缺集而后退。": "Uncheck for gap filling only. Next episode never moves backwards when older gaps are filled.",
    "频道": "Channels",
    "创建订阅": "Create subscription",
    "暂无订阅": "No subscriptions",
    "数据库备份": "Database backups",
    "创建不含凭据和 Telegram Session 的安全快照。": "Create a safe snapshot without credentials or the Telegram session.",
    "创建备份": "Create backup",
    "暂无备份": "No backups",
    "完整灾难恢复备份需停机后使用 CLI 显式包含 Session。": "A full disaster-recovery backup must be created offline with the CLI session option.",
    "创建下载任务": "Create download task",
    "确认下载方式与归档信息。": "Confirm download and archive details.",
    "任务类型": "Task type",
    "电影": "Movie",
    "电视剧": "Series",
    "媒体库片名": "Library title",
    "年份（可选）": "Year (optional)",
    "起始集": "First episode",
    "结束集（可选）": "Last episode (optional)",
    "我已确认片名、年份和集数，可在下载后自动入库": "I confirmed the title, year and episodes for automatic archiving",
    "不确定信息时可选择“仅下载”，文件完成后仍可从任务列表手动归档。": "Choose Download only when details are uncertain; you can archive from the task list later.",
    "取消": "Cancel",
    "仅下载": "Download only",
    "下载并自动入库": "Download and archive",
    "归档媒体": "Archive media",
    "确认内容身份和媒体库路径。": "Confirm identity and library path.",
    "类型": "Type",
    "片名": "Title",
    "年份": "Year",
    "片名与年份已人工确认": "Title and year manually confirmed",
    "确认入库": "Archive",
    "任务时间线": "Task timeline",
    "状态变更与处理记录。": "State transitions and processing history.",
    "关闭": "Close",
    "用于最终目录和文件名": "Used for the final folder and file name",
    "可选，例如：影视源 A": "Optional, for example Source A",
    "留空使用默认频道；多个用英文逗号分隔": "Leave empty for defaults; separate multiple values with commas",
    "留空使用默认频道；可粘贴完整链接，多个用英文逗号分隔": "Leave empty for defaults; paste full links and separate multiple values with commas",
    "例如：葬送的芙莉莲 S01E02": "For example: Frieren S01E02",
    "资源筛选": "Resource filters",
    "正在读取默认偏好": "Reading default preferences",
    "限定分辨率": "Resolution filter",
    "使用全局规则": "Use global policy",
    "限定语言": "Language filter",
    "中文": "Chinese",
    "最大文件大小（GB）": "Maximum file size (GB)",
    "留空使用全局规则": "Leave empty for global policy",
    "HDR 排序": "HDR ranking",
    "优先 HDR": "Prefer HDR",
    "不优先 HDR": "Do not prefer HDR",
    "默认偏好": "Defaults",
    "不限": "unlimited",
    "登录已失效，请重新登录。": "Your session expired. Please sign in again.",
    "创建本地管理员": "Create local administrator",
    "欢迎回来": "Welcome back",
    "请先创建管理员账号，创建后即可登录。": "Create an administrator account to continue.",
    "使用初始化时创建的本地账号进入控制台。": "Use the local account created during initialization.",
    "API 客户端": "API client",
    "登录控制台后读取后台状态": "Sign in to read service status",
    "正在检查 Telegram 状态…": "Checking Telegram status…",
    "尚未登录，请先发送验证码": "Not signed in; request a verification code",
    "验证码已发送，请填写收到的验证码": "Code sent; enter the verification code",
    "账号启用了两步验证，请填写密码": "Two-step verification is enabled; enter the password",
    "Telegram 已登录": "Telegram signed in",
    "Telegram 暂时不可用，请检查代理后重试": "Telegram is unavailable; check the proxy and retry",
    "已加入队列，下载完成后将自动入库": "Queued and will be archived automatically after download",
    "已加入下载队列": "Added to the download queue",
    "没有找到可下载文件": "No downloadable files found",
    "分辨率未知": "Unknown resolution",
    "集数冲突，需人工判断": "Episode metadata conflict; review required",
    "集数未知": "Unknown episode",
    "编码未知": "Unknown codec",
    "语言未知": "Unknown language",
    "来源": "Source",
    "转发自": "Forwarded from",
    "匹配": "match",
    "创建任务": "Create task",
    "处理中…": "Working…",
    "正在搜索…": "Searching…",
    "正在入队…": "Adding to queue…",
    "任务": "Task",
    "尝试": "attempts",
    "电影库": "Movie library",
    "电视剧库": "TV library",
    "自动入库至": "auto-archive to",
    "事件": "Events",
    "暂停": "Pause",
    "恢复": "Resume",
    "重试": "Retry",
    "版本": "Version",
    "运行中": "Running",
    "未运行": "Stopped",
    "已配置": "Configured",
    "未配置": "Not configured",
    "区域": "Locale",
    "归档策略": "Archive mode",
    "移动": "Move",
    "硬链接": "Hard link",
    "复制": "Copy",
    "Inbox 空间": "Inbox free space",
    "文件维护": "File maintenance",
    "追更周期": "Subscription interval",
    "分钟": "min",
    "未知": "Unknown",
    "暂停追更": "Pause subscription",
    "恢复追更": "Resume subscription",
    "待补": "Missing",
    "继续追更": "Continue from",
    "补齐后停止": "Stop after filling gaps",
    "集数格式无效，请使用 2, 4-6 这样的格式。": "Invalid episode list. Use a format such as 2, 4-6.",
    "一次最多指定 500 集。": "Specify at most 500 episodes at a time.",
    "取消持续追更时，必须填写需补集数。": "Enter missing episodes when continuous tracking is disabled.",
    "频道格式无效。请填写公开 t.me 链接、@频道名、纯频道名或 -100 数字 ID": "Invalid channel. Enter a public t.me link, @username, plain username, or a numeric ID beginning with -100.",
    "订阅配置无效: 请检查频道、集数与追更方式": "Invalid subscription. Check the channels, episodes and tracking mode.",
    "删除": "Delete",
    "已启用": "Enabled",
    "已暂停": "Paused",
    "两次输入的密码不一致。": "The passwords do not match.",
    "已安全退出本地控制台。": "Signed out safely.",
    "数据库备份已创建": "Database backup created",
    "入库完成": "Archive complete",
    "订阅已创建": "Subscription created",
    "默认搜索频道已添加": "Default search channel added",
    "正在验证频道…": "Verifying channel…",
    "公开频道": "Public channel",
    "私有频道": "Private channel",
    "类型暂未验证": "Type not verified",
    "正在发现频道…": "Discovering channels…",
    "已读取 {count} 个已加入频道": "Loaded {count} joined channels",
    "管理员尚未创建。请在服务主机运行 mediactl set-admin。": "No administrator exists. Run mediactl set-admin on the service host.",
    "暂时无法连接服务：": "Unable to reach the service: ",
    "待处理": "Pending",
    "搜索中": "Searching",
    "等待选择": "Needs selection",
    "已选资源": "Candidate selected",
    "排队中": "Queued",
    "下载中": "Downloading",
    "等待重试": "Waiting to retry",
    "已下载": "Downloaded",
    "归档中": "Archiving",
    "已归档": "Archived",
    "临时归档": "Provisionally archived",
    "失败": "Failed",
    "已取消": "Cancelled"
  }
};

function t(value) {
  const scanTranslations = {
    "第 {episode} 集已有失败或取消任务，请检查下载队列。": "Episode {episode} has a failed or cancelled task. Check the download queue.",
    "第 {episode} 集已入队": "Episode {episode} queued",
    "等待第 {episode} 集": "Waiting for episode {episode}",
    "订阅计划已变更，本轮未入队": "Subscription plan changed; nothing queued this scan",
    "本轮媒体候选 {count} 个": "Media candidates this scan: {count}",
    "集数冲突": "Conflicting episode numbers",
    "集数无法识别": "Episode number not recognized",
    "季数不符": "Different season",
    "不在待补范围": "Outside the episode plan",
    "后续集待处理": "Later episodes waiting",
    "质量筛选未通过": "Excluded by quality filters",
    "追更计划已 {days} 天未推进，可检查是否已完结；扫描仍会继续。":
      "The episode plan has not advanced for {days} days. Check whether the season has ended; scanning will continue.",
  };
  if (currentLocale === "en-US" && scanTranslations[value]) return scanTranslations[value];
  return translations[currentLocale]?.[value] || value;
}

function formatDate(value) {
  try {
    return new Intl.DateTimeFormat(currentLocale, {
      dateStyle: "medium",
      timeStyle: "medium",
      timeZone: currentTimeZone,
    }).format(new Date(value));
  } catch (_error) {
    return new Date(value).toLocaleString();
  }
}

function parseEpisodePlan(value) {
  const episodes = new Set();
  const tokens = value.trim().split(/[,，\s]+/).filter(Boolean);
  for (const token of tokens) {
    const match = token.match(/^(\d+)(?:-(\d+))?$/);
    if (!match) throw new Error(t("集数格式无效，请使用 2, 4-6 这样的格式。"));
    const start = Number(match[1]);
    const end = Number(match[2] || match[1]);
    if (start < 1 || end < start || end > 100000) {
      throw new Error(t("集数格式无效，请使用 2, 4-6 这样的格式。"));
    }
    for (let episode = start; episode <= end; episode += 1) {
      episodes.add(episode);
      if (episodes.size > 500) throw new Error(t("一次最多指定 500 集。"));
    }
  }
  return [...episodes].sort((left, right) => left - right);
}

function formatEpisodePlan(values) {
  if (!values.length) return "";
  const ranges = [];
  let start = values[0];
  let end = values[0];
  for (const episode of values.slice(1)) {
    if (episode === end + 1) {
      end = episode;
      continue;
    }
    ranges.push(start === end ? `${start}` : `${start}-${end}`);
    start = episode;
    end = episode;
  }
  ranges.push(start === end ? `${start}` : `${start}-${end}`);
  return ranges.join(", ");
}

function translateStaticPage() {
  document.documentElement.lang = currentLocale;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    if (["SCRIPT", "STYLE"].includes(node.parentElement?.tagName)) continue;
    const value = node.nodeValue;
    const trimmed = value.trim();
    if (trimmed && t(trimmed) !== trimmed) node.nodeValue = value.replace(trimmed, t(trimmed));
  }
  for (const element of document.querySelectorAll("[placeholder], [aria-label]")) {
    for (const attribute of ["placeholder", "aria-label"]) {
      if (element.hasAttribute(attribute)) element.setAttribute(attribute, t(element.getAttribute(attribute)));
    }
  }
}

async function loadUiConfiguration() {
  Object.assign(translations["en-US"], workspaceEnglish);
  Object.assign(translations["en-US"], {
    "下载并自动入库": "Download and import", "确认入库": "Confirm import",
    "我已确认片名、年份和集数，可在下载后自动入库": "I have confirmed the title, year, and episodes for automatic import",
  });
  try {
    const response = await fetch("/api/v1/ui/config", { credentials: "same-origin" });
    if (response.ok) {
      const value = await response.json();
      currentLocale = value.language || currentLocale;
      currentTimeZone = value.timezone || currentTimeZone;
    }
  } catch (_error) {
    // The default locale keeps the login page usable while the service is starting.
  }
  translateStaticPage();
}
const taskStateLabels = {
  pending: "待处理",
  searching: "搜索中",
  needs_selection: "等待选择",
  candidate_selected: "已选资源",
  queued: "排队中",
  downloading: "下载中",
  paused: "已暂停",
  retrying: "等待重试",
  downloaded: "已下载",
  importing: "归档中",
  imported: "已归档",
  provisional: "临时归档",
  failed: "失败",
  cancelled: "已取消",
};

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  window.setTimeout(() => element.classList.remove("show"), 3200);
}

async function runBusy(control, busyLabel, action, scope = null) {
  if (!control || control.disabled) return undefined;
  const originalContent = control.innerHTML;
  const lockedControls = scope ? [...scope.querySelectorAll("button")] : [control];
  const disabledStates = lockedControls.map((item) => item.disabled);
  for (const item of lockedControls) item.disabled = true;
  control.classList.add("is-busy");
  control.setAttribute("aria-busy", "true");
  control.textContent = t(busyLabel);
  const form=scope?.matches("form")?scope:control.closest("form");form?.querySelectorAll(".form-error").forEach(node=>node.remove());
  try {
    return await action();
  } catch(error) {
    if(form){const notice=document.createElement("p");notice.className="form-error";notice.role="alert";notice.tabIndex=-1;notice.textContent=error.message;form.prepend(notice);if(form.getBoundingClientRect().width)notice.focus();}
    throw error;
  } finally {
    lockedControls.forEach((item, index) => { item.disabled = disabledStates[index]; });
    control.classList.remove("is-busy");
    control.removeAttribute("aria-busy");
    control.innerHTML = originalContent;
  }
}

async function api(path, options = {}) {
  const { authFlow = false, ...fetchOptions } = options;
  const headers = new Headers(options.headers || {});
  if (options.body) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes((options.method || "GET").toUpperCase())) {
    const csrf = document.cookie.split("; ").find((item) => item.startsWith("tma_csrf="));
    if (csrf) headers.set("X-CSRF-Token", decodeURIComponent(csrf.split("=").slice(1).join("=")));
  }
  const response = await fetch(path, { ...fetchOptions, headers, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail || data;
    if (response.status === 401 && !authFlow) showAuth("login", t("登录已失效，请重新登录。"));
    throw new Error(t(detail.message || detail.code || `HTTP ${response.status}`));
  }
  return data;
}

function showAuth(mode, message = "") {
  apiConnected = false;
  resourceCache.clear();
  taskCache.clear();
  channelInspectionCache.clear();
  searchResults = [];
  selectedResources.clear();pendingResourceBatch=[];enqueuePreparationGeneration++;
  taskRequestGeneration += 1;
  for (const dialog of document.querySelectorAll("dialog[open]")) dialog.close();
  $("#app-shell").hidden = true;
  $("#auth-screen").hidden = false;
  $("#login-form").hidden = mode !== "login";
  $("#setup-form").hidden = mode !== "setup";
  $("#auth-title").textContent = mode === "setup" ? t(dockerBootstrapRequired ? "配置你的媒体服务" : "创建本地管理员") : t("欢迎回来");
  $("#auth-copy").textContent = message || (mode === "setup"
    ? t(dockerBootstrapRequired ? "填写初始化码、Telegram API 与管理员账号。" : "请先创建管理员账号，创建后即可登录。")
    : t("使用初始化时创建的本地账号进入控制台。"));
  $("#auth-help").textContent = t(mode === "setup" ? "账号与配置保存在你的设备上。" : "使用你的管理员账号登录。");
  $("#login-password").value = "";
  $("#setup-password").value = "";
  $("#setup-password-confirm").value = "";
}

async function enterWorkspace(username) {
  apiConnected = true;
  $("#auth-screen").hidden = true;
  $("#app-shell").hidden = false;
  $("#connection").textContent = username === "api-token" ? t("API 客户端") : username;
  $(".avatar").textContent = (username || "A").slice(0, 1).toUpperCase();
  initializeWorkspace();
  await Promise.all([
    telegramStatus(), loadTasks(), loadTaskSummary(), loadSubscriptions(),
    loadSearchChannels(), loadSystemStatus(), loadQualitySettings(), loadBackups(),
  ]);
}

const telegramStateLabels = {
  disconnected: "登录控制台后读取后台状态",
  checking: "正在检查 Telegram 状态…",
  login_required: "尚未登录，请先发送验证码",
  code_sent: "验证码已发送，请填写收到的验证码",
  password_required: "账号启用了两步验证，请填写密码",
  authorized: "Telegram 已登录",
  unavailable: "Telegram 暂时不可用，请检查代理后重试",
};

function setTelegramStage(stage, message = "") {
  telegramStage = stage;
  const copy = message || t(telegramStateLabels[stage]) || stage;
  $("#telegram-state").textContent = copy;
  $("#telegram-dialog-state").textContent = copy;
  $("#connect-telegram").hidden = !["login_required", "code_sent", "password_required"].includes(stage);
  for (const [selector, requiredStage] of [["#phone-form", "login_required"], ["#code-form", "code_sent"], ["#password-form", "password_required"]]) {
    const form = $(selector);
    form.hidden = stage !== requiredStage;
    for (const control of form.elements) control.disabled = !apiConnected || form.hidden;
  }
  if (stage === "authorized" && $("#telegram-dialog").open) $("#telegram-dialog").close();
  updateGlobalStatus();
}

async function telegramStatus() {
  if (["code_sent", "password_required"].includes(telegramStage)) return;
  setTelegramStage("checking");
  try {
    const result = await api("/api/v1/telegram/status");
    setTelegramStage(result.status);
  } catch (error) {
    setTelegramStage("unavailable", error.message);
  }
}

function button(label, action, danger = false) {
  const control = document.createElement("button");
  control.type = "button";
  control.textContent = label;
  control.className = danger ? "secondary danger" : "secondary";
  control.addEventListener("click", async () => {
    try {
      await runBusy(control, "处理中…", action);
    } catch (error) {
      toast(error.message);
    }
  });
  return control;
}

function humanBytes(value) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / (1024 ** index)).toFixed(index > 1 ? 2 : 0)} ${units[index]}`;
}

function sourceLabel(source, forwarded = false) {
  if (!source) return "";
  if (forwarded) {
    return source.forwarded_from_title
      || (source.forwarded_from_username ? `@${source.forwarded_from_username}` : "")
      || (source.forwarded_from_channel_id ? String(source.forwarded_from_channel_id) : "");
  }
  return source.channel_title
    || (source.channel_username ? `@${source.channel_username}` : "")
    || String(source.channel_id);
}

function sourceElement(resource, forwarded = false, className = "") {
  const source = resource?.source;
  const label = sourceLabel(source, forwarded);
  if (!label) return null;
  const link = forwarded ? source.forwarded_from_link : source.message_link;
  const element = document.createElement(link ? "a" : "span");
  element.className = className;
  element.textContent = `${t(forwarded ? "转发自" : "来源")} ${label}`;
  if (link) {
    element.href = link;
    element.target = "_blank";
    element.rel = "noopener noreferrer";
  }
  return element;
}

async function enqueue(resourceId, archivePlan = null) {
  const payload = { resource_id: resourceId };
  if (archivePlan) payload.archive_plan = archivePlan;
  const result = await api("/api/v1/tasks", {
    method: "POST",
    headers: { "Idempotency-Key": enqueueIdempotencyKey || newIdempotencyKey() },
    body: JSON.stringify(payload),
  });
  toast(t(archivePlan?.replace_existing ? "已加入队列，下载完成后将自动替换" : archivePlan ? "已加入队列，下载完成后将自动入库" : "已加入下载队列"));
  await Promise.all([loadTasks(), loadTaskSummary()]);
}

function updateEnqueueKind() {
  const series = $("#enqueue-kind").value === "series";
  $("#enqueue-episode-fields").hidden = !series;
  for (const selector of ["#enqueue-season", "#enqueue-episode"]) {
    $(selector).required = series;
  }
}

async function prepareEnqueue(resource, replace = false) {
  const generation=++enqueuePreparationGeneration;
  resourceCache.set(resource.id, resource);
  enqueueIdempotencyKey = newIdempotencyKey();
  $("#enqueue-file").textContent = resource.file_name;
  const query = $("#query").value.trim();
  let suggestion = { title: query, year: null, kind: "movie", season: null, episode: null, episode_end: null };
  try {
    suggestion = await api("/api/v1/media/suggestions", {
      method: "POST",
      body: JSON.stringify({ text: query }),
    });
  } catch (_error) {
    // A suggestion failure must not prevent a user from creating a task manually.
  }
  if(generation!==enqueuePreparationGeneration||!apiConnected)return;
  $("#enqueue-title").value = suggestion.title;
  $("#enqueue-year").value = suggestion.year || "";
  $("#enqueue-confirmed").checked = false;
  $("#enqueue-replace").checked = replace;
  resetReplacementTarget("enqueue");
  updateReplacementMode("enqueue");
  const resourceEpisode = resource.episode_conflict ? null : resource.episode_range;
  const episode = resourceEpisode || (suggestion.kind === "series" ? {
    season: suggestion.season,
    start: suggestion.episode,
    end: suggestion.episode_end || suggestion.episode,
  } : null);
  $("#enqueue-kind").value = episode ? "series" : suggestion.kind;
  $("#enqueue-season").value = episode ? episode.season : 1;
  $("#enqueue-episode").value = episode ? episode.start : 1;
  $("#enqueue-episode-end").value = episode && episode.end > episode.start ? episode.end : "";
  updateEnqueueKind();
  updateDestinationPreview();
  $("#enqueue-resource-id").value = resource.id;
  $("#enqueue-dialog").showModal();
}

function renderResults(resources) {
  setSearchResults(resources);
}

async function act(taskId, action) {
  await api(`/api/v1/tasks/${taskId}/${action}`, { method: "POST", body: "{}" });
  await Promise.all([loadTasks(), loadTaskSummary()]);
}

async function showEvents(taskId) {
  const events = await api(`/api/v1/tasks/${taskId}/events`);
  const root = $("#event-list");
  root.replaceChildren();
  for (const event of events) {
    const item = document.createElement("div");
    item.className = "timeline-item";
    const state = document.createElement("strong");
    const current = t(taskStateLabels[event.new_state]) || event.new_state;
    const previous = t(taskStateLabels[event.previous_state]) || event.previous_state;
    state.textContent = event.previous_state ? `${previous} → ${current}` : current;
    const detail = document.createElement("small");
    detail.textContent = `${formatDate(event.occurred_at)} · ${event.actor}${event.reason ? ` · ${event.reason}` : ""}`;
    item.append(state, detail);
    root.append(item);
  }
  $("#events-dialog").showModal();
}

function resetReplacementTarget(prefix) {
  const select = $(`#${prefix}-target`);
  select.replaceChildren(new Option(t("先填写片名并查找已有文件"), ""));
}

function updateReplacementMode(prefix) {
  const replacing = $(`#${prefix}-replace`).checked;
  $(`#${prefix}-replacement-fields`).hidden = !replacing;
  $(`#${prefix}-target`).disabled = !replacing;
  $(`#${prefix}-target`).required = replacing;
  if (prefix === "import") $("#import-confirmed").required = true;
  $(`#${prefix}-form button[type=submit]`).textContent = t(replacing
    ? prefix === "enqueue" ? "下载并自动替换" : "确认替换"
    : prefix === "enqueue" ? "下载并自动入库" : "确认入库");
  if (prefix === "enqueue") $("#enqueue-download-only").hidden = replacing;
  if(prefix==="import"){ $("#import-dialog h2").textContent=t(importIsReplacementPlan?"设置下载后替换":replacing?"替换媒体": "入库媒体");if(importIsReplacementPlan)$("#import-form button[type=submit]").textContent=t("保存替换计划"); }
}

async function findReplacementTarget(prefix) {
  const title = $(`#${prefix}-title`).value.trim();
  if (!title) { toast(t("请先填写片名")); return; }
  const kind = $(`#${prefix}-kind`).value;
  resetReplacementTarget(prefix);
  try {
    await runBusy($(`#${prefix}-find-target`), "正在查询…", async () => {
      const files = await api(`/api/v1/library/files?${new URLSearchParams({kind, query: title, limit: "500"})}`);
      if ($(`#${prefix}-title`).value.trim() !== title || $(`#${prefix}-kind`).value !== kind) return;
      const select = $(`#${prefix}-target`);
      select.replaceChildren(new Option(t(files.length ? "请选择要替换的文件" : "未找到匹配的媒体库文件"), ""));
      for (const file of files) select.add(new Option(`${file.path} · ${humanBytes(file.file_size)}`, file.path));
    });
  } catch (error) { toast(error.message); }
}

async function importTask(taskId, replace = false, schedule = false) {
  importIsReplacementPlan=schedule;
  $("#import-replace").disabled=schedule;
  const task = taskCache.get(taskId);
  const plan = task?.archive_plan;
  const resource = resourceCache.get(task?.resource_id);
  const episode = resource?.episode_conflict ? null : resource?.episode_range;
  $("#import-title").value = plan?.title || "";
  $("#import-year").value = plan?.year || "";
  $("#import-kind").value = plan?.kind || (episode ? "series" : "movie");
  $("#import-season").value = plan?.season ?? episode?.season ?? 1;
  $("#import-episode").value = plan?.episode ?? episode?.start ?? 1;
  $("#import-episode-end").value = plan?.episode_end || (episode?.end > episode?.start ? episode.end : "");
  const series = $("#import-kind").value === "series";
  for (const field of ["#import-season", "#import-episode", "#import-episode-end"]) $(field).disabled = !series;
  $("#import-task-id").value = taskId;
  $("#import-replace").checked = replace;
  $("#import-confirmed").checked = false;
  resetReplacementTarget("import");
  updateReplacementMode("import");
  $("#import-dialog").showModal();
}

function renderTasks(tasks) {
  updateTaskRows(tasks);
}

async function loadTaskSummary(quiet = false) {
  try {
    const value = await api("/api/v1/tasks/summary");
    $("#summary-total").textContent = value.total;
    $("#summary-active").textContent = value.active;
    $("#queue-count").textContent = value.active;
    $("#summary-attention").textContent = value.attention;
    $("#summary-completed").textContent = value.completed;
    $("#task-summary").setAttribute("aria-busy", "false");
  } catch (error) {
    if (!quiet) toast(error.message);
  }
}

async function loadTasks(quiet = false) {
  const generation = ++taskRequestGeneration;
  try {
    const filter = $("#task-filter").value;
    const states = taskFilterStates[filter] || [];
    const parameters = new URLSearchParams({
      offset: String(taskPageIndex * taskPageSize),
      limit: String(taskPageSize),
    });
    if (states.length) parameters.set("states", states.join(","));
    const page = await api(`/api/v1/tasks/page?${parameters}`);
    if (generation !== taskRequestGeneration || !apiConnected) return;
    if (!page.items.length && page.total && taskPageIndex > 0) {
      taskPageIndex -= 1;
      await loadTasks(quiet);
      return;
    }
    const missingResourceIds = [...new Set(
      page.items
        .map((task) => task.resource_id)
        .filter((resourceId) => !resourceCache.has(resourceId)),
    )];
    await Promise.all(missingResourceIds.map(async (resourceId) => {
      try {
        const resource = await api(`/api/v1/resources/${resourceId}`);
        resourceCache.set(resource.id, resource);
      } catch (_error) {
        // A task remains useful even when its historical resource metadata is unavailable.
      }
    }));
    if (generation !== taskRequestGeneration || !apiConnected) return;
    renderTasks(page.items);
    renderTaskPager(page);
  } catch (error) {
    if (generation === taskRequestGeneration) $("#task-updated").textContent = t("未读取到最新状态，请重试。");
    if (!quiet) toast(error.message);
  }
}

function renderTaskPager(page) {
  const pager = $("#task-pager");
  const pageCount = Math.max(1, Math.ceil(page.total / page.limit));
  const current = Math.min(pageCount, Math.floor(page.offset / page.limit) + 1);
  pager.hidden = page.total <= page.limit;
  $("#task-previous").disabled = current <= 1;
  $("#task-next").disabled = current >= pageCount;
  $("#task-page-status").textContent = currentLocale === "en-US"
    ? `Page ${current} of ${pageCount} · ${page.total} tasks`
    : `第 ${current} / ${pageCount} 页 · 共 ${page.total} 项`;
}

async function loadSystemStatus() {
  try {
    const value = await api("/api/v1/system/status");
    const readinessResponse = await fetch("/health/ready", { credentials: "same-origin" });
    const readiness = await readinessResponse.json();
    value.readiness = readinessResponse.ok && readiness.status === "ready" && Object.values(readiness.checks || {}).every(Boolean);
    if (!apiConnected) return;
    lastSystemStatus = value;
    $("#setup-restart-note").hidden = !value.setup_restart_required;
    statusAvailable = true;
    $("#sidebar-version").textContent = `v${value.version}`;
    $("#status-checked").textContent = formatDate(new Date());
    updateGlobalStatus();
    const root = $("#system-status");
    root.className = "status-grid";
    root.replaceChildren();
    const metrics = [
      [t("版本"), `${value.version} · schema ${value.schema_version}`],
      ["Worker", value.workers_running ? t("运行中") : t("未运行")],
      ["Telegram", t(telegramHealthLabel(readiness))],
      [t("区域"), `${value.language} · ${value.timezone}`],
      [t("入库策略"), { move: t("移动"), hardlink: t("硬链接"), copy: t("复制") }[value.archive_mode] || value.archive_mode],
      [t("文件维护"), value.maintenance_running ? t("运行中") : t("未运行")],
      [t("追更周期"), `${Math.round(value.subscription_poll_interval / 60)} ${t("分钟")}`],
      [t("下载收件箱可用空间"), value.directories.inbox.free_bytes === null ? t("未知") : `${(value.directories.inbox.free_bytes / 1073741824).toFixed(1)} GB`],
    ];
    for (const [label, detail] of metrics) {
      const metric = document.createElement("div");
      metric.className = "status-item";
      const strong = document.createElement("strong");
      strong.textContent = label;
      const small = document.createElement("small");
      small.textContent = detail;
      metric.append(strong, small);
      root.append(metric);
    }
  } catch (error) {
    statusAvailable = false;
    updateGlobalStatus();
    $("#status-checked").textContent = t("未读取到最新状态，请重试。");
  }
}

async function loadQualitySettings() {
  try {
    const value = await api("/api/v1/settings/quality");
    const resolutionValues = value.allowed_resolutions.length
      ? value.allowed_resolutions
      : value.preferred_resolutions;
    const resolutions = resolutionValues
      .map((resolution) => `${resolution}p`)
      .join(value.allowed_resolutions.length ? " / " : " > ");
    const languages = value.allowed_languages.length
      ? value.allowed_languages.join(" / ")
      : value.preferred_languages.join(" > ");
    const maximum = value.max_file_size ? humanBytes(value.max_file_size) : t("不限");
    $("#quality-defaults").textContent = `${t("默认偏好")}: ${resolutions} · ${languages} · ${value.prefer_hdr ? t("优先 HDR") : t("不优先 HDR")} · ${maximum}`;
  } catch (error) {
    $("#quality-defaults").textContent = error.message;
  }
}

async function downloadBackup(name) {
  const response = await fetch(`/api/v1/backups/${encodeURIComponent(name)}`, {
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const link = document.createElement("a");
  link.href = URL.createObjectURL(await response.blob());
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function loadBackups() {
  try {
    const values = await api("/api/v1/backups");
    const root = $("#backups");
    root.replaceChildren();
    root.className = values.length ? "results" : "results empty";
    if (!values.length) { root.textContent = t("暂无备份"); return; }
    for (const value of values) {
      const row = document.createElement("div");
      row.className = "row";
      const info = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = value.name;
      const meta = document.createElement("small");
      meta.textContent = `${formatDate(value.created_at)} · ${(value.size / 1048576).toFixed(2)} MB`;
      info.append(title, meta);
      row.append(info, button(t("下载"), () => downloadBackup(value.name).catch((error) => toast(error.message))));
      root.append(row);
    }
  } catch (error) { toast(error.message); }
}

let subscriptionValues = [];
let subscriptionFilter = "all";
let subscriptionRenderKey = "";
let subscriptionRequest = 0;
document.querySelectorAll("[data-subscription-filter]").forEach(tab => tab.addEventListener("click", () => {
  subscriptionFilter = tab.dataset.subscriptionFilter;
  renderSubscriptions();
}));

function renderSubscriptions() {
    const key = JSON.stringify([subscriptionFilter, currentLocale, subscriptionValues]);
    if (key === subscriptionRenderKey) return;
    subscriptionRenderKey = key;
    for (const tab of document.querySelectorAll("[data-subscription-filter]")) {
      const filter = tab.dataset.subscriptionFilter;
      const count = subscriptionValues.filter(value => filter === "all" || value.enabled === (filter === "active")).length;
      tab.textContent = `${t(filter === "all" ? "全部" : filter === "active" ? "已订阅" : "暂停订阅")} ${count}`;
      tab.classList.toggle("selected", filter === subscriptionFilter);
      tab.setAttribute("aria-pressed", String(filter === subscriptionFilter));
    }
    const values = subscriptionValues.filter(value => subscriptionFilter === "all" || value.enabled === (subscriptionFilter === "active"));
    const root = $("#subscriptions");
    root.replaceChildren();
    root.className = values.length ? "results" : "results empty";
    if (!values.length) {
      root.textContent = t(subscriptionValues.length ? "没有符合筛选条件的订阅" : "暂无订阅");
      return;
    }
    for (const value of values) {
      const row = document.createElement("div");
      row.className = `row subscription-row ${value.enabled ? "subscription-active" : "subscription-paused"}`;
      const info = document.createElement("div");
      const heading = document.createElement("div");
      heading.className = "subscription-heading";
      const title = document.createElement("strong");
      title.textContent = value.title;
      const badge = document.createElement("span");
      badge.className = `subscription-badge ${value.enabled ? "is-active" : "is-paused"}`;
      badge.textContent = t(value.enabled ? "已订阅" : "暂停订阅");
      heading.append(title, badge);
      const meta = document.createElement("small");
      const plan = [];
      if (value.pending_episodes.length) plan.push(`${t("待补")} E${formatEpisodePlan(value.pending_episodes)}`);
      plan.push(value.continue_tracking
        ? `${t("继续追更")} S${String(value.season).padStart(2, "0")}E${String(value.next_episode).padStart(2, "0")}`
        : t("补齐后停止"));
      plan.push(value.channels.length ? value.channels.join(", ") : t("全部默认频道"));
      meta.textContent = plan.join(" · ");
      info.append(heading, meta);
      if (value.enabled) {
        const scan = value.scan_status;
        const status = document.createElement("small");
        status.className = `subscription-scan ${["failed", "partial", "rate_limited", "no_channels", "interrupted"].includes(scan?.state) ? "scan-warning" : ""}`;
        status.textContent = value.scan_health?.requested_at && scan?.state !== "scanning" ? t("检查已排队") : subscriptionScanLabel(scan);
        if (scan?.successful_channels != null) status.textContent += ` · ${scan.successful_channels}/${scan.channel_count} ${t("频道正常")}`;
        if (scan?.failed_channels?.length) status.textContent += ` · ${scan.failed_channels.join(", ")}`;
        if (scan?.message) status.textContent += ` · ${t(scan.message)}`;
        if (scan?.checked_at && scan.state !== "scanning") status.textContent += ` · ${t("最近检查")} ${formatDate(scan.checked_at)}`;
        info.append(status);
        const health = value.scan_health || {};
        for (const fields of [
          [["last_attempt_at", "最近尝试"], ["last_success_at", "最近成功"]],
          [["last_enqueued_at", "最近新增任务"], ["last_archived_at", "最近入库时间"]],
        ]) {
          const parts = fields.filter(([key]) => health[key]).map(([key, label]) => `${t(label)} ${formatDate(health[key])}`);
          if (parts.length) {
            const line = document.createElement("small");
            line.className = "subscription-scan scan-health";
            line.textContent = parts.join(" · ");
            info.append(line);
          }
        }
        if (health.consecutive_failures > 0) {
          const failure = document.createElement("small");
          failure.className = "subscription-scan scan-warning";
          failure.textContent = `${t("连续失败")} ${health.consecutive_failures}${health.last_error_code ? ` · ${health.last_error_code}` : ""}`;
          info.append(failure);
        }
        const details = subscriptionScanDetails(scan);
        if (details.length) {
          const summary = document.createElement("small");
          summary.className = "subscription-scan scan-summary";
          summary.textContent = details.join(" · ");
          info.append(summary);
        }
        const idleDays = health.resource_idle_days ?? scan?.plan_idle_days;
        const stale = health.resource_idle_days != null ? idleDays >= 7 : scan?.stale;
        if (stale && scan?.state === "succeeded" && scan.outcome === "waiting") {
          const hint = document.createElement("small");
          hint.className = "subscription-scan scan-warning scan-stale";
          hint.textContent = t("追更计划已 {days} 天未推进，可检查是否已完结；扫描仍会继续。")
            .replace("{days}", String(idleDays));
          info.append(hint);
        }
      }
      const actions = document.createElement("div");
      actions.className = "actions";
      if (value.enabled) {
        const pending = value.scan_health?.requested_at || value.scan_status?.state === "scanning";
        const check = button(t(pending ? value.scan_status?.state === "scanning" ? "正在检查" : "检查已排队" : "立即检查"), async () => {
          const result = await api(`/api/v1/subscriptions/${value.id}/check`, {method: "POST"});
          toast(`${t(result.status === "queued" ? "检查已加入队列" : "检查请求已合并")}${result.retry_at ? ` · ${t("最早重试时间")} ${formatDate(result.retry_at)}` : ""}`);
          await loadSubscriptions();
        });
        check.disabled = Boolean(pending);
        actions.append(check);
      }
      actions.append(button(t("扫描历史"), () => showScanHistory(value)));
      actions.append(button(t("编辑"), async () => {
        openSubscriptionEditor(await api(`/api/v1/subscriptions/${value.id}`));
      }));
      actions.append(button(t(value.enabled ? "暂停追更" : "恢复追更"), async () => {
        await api(`/api/v1/subscriptions/${value.id}/${value.enabled ? "pause" : "resume"}`, { method: "POST" });
        await loadSubscriptions();
      }));
      actions.append(button(t("删除"), async () => {
        if (!window.confirm(currentLocale === "en-US" ? `Delete subscription ${value.title}?` : `删除追更订阅 ${value.title}？`)) return;
        await api(`/api/v1/subscriptions/${value.id}`, { method: "DELETE" });
        await loadSubscriptions();
      }, true));
      row.append(info, actions);
      root.append(row);
    }
}

async function showScanHistory(subscription) {
  const dialog = $("#scan-history-dialog");
  $("#scan-history-title").textContent = `${t("扫描历史")} · ${subscription.title}`;
  const list = $("#scan-history-list");
  list.replaceChildren();
  list.textContent = t("正在读取扫描历史…");
  dialog.showModal();
  try {
    const reports = await api(`/api/v1/subscriptions/${subscription.id}/scans?limit=20`);
    list.replaceChildren();
    if (!reports.length) list.textContent = t("尚无扫描历史");
    for (const report of reports) {
      const item = document.createElement("div");
      item.className = "timeline-item";
      const heading = document.createElement("strong");
      heading.textContent = `${subscriptionScanLabel(report)} · ${formatDate(report.checked_at)}`;
      const detail = document.createElement("p");
      detail.textContent = [...subscriptionScanDetails(report), report.message ? t(report.message) : "", report.error_code || ""].filter(Boolean).join(" · ");
      item.append(heading, detail);
      list.append(item);
    }
  } catch (error) {
    list.textContent = error.message;
  }
}

async function loadSubscriptions(silent = false) {
  const request = ++subscriptionRequest;
  try {
    const values = await api("/api/v1/subscriptions");
    if (!apiConnected || request !== subscriptionRequest) return;
    subscriptionValues = values;
    if (silent && $("#subscriptions").contains(document.activeElement)) return;
    renderSubscriptions();
  } catch (error) { if (!silent) toast(error.message); }
}

async function loadSearchChannels() {
  try {
    const values = await api("/api/v1/channels");
    if (!apiConnected) return;
    updateChannelChoice(values);
    const root = $("#search-channels");
    root.replaceChildren();
    root.className = values.length ? "results" : "results empty";
    if (!values.length) { root.textContent = t("暂无默认频道"); return; }
    for (const value of values) {
      let inspection = channelInspectionCache.get(value.identifier);
      if (!inspection && currentPage === "channels-panel") {
        try {
          inspection = await api("/api/v1/channels/inspect", {
            method: "POST",
            body: JSON.stringify({ identifier: value.identifier }),
          });
          channelInspectionCache.set(value.identifier, inspection);
        } catch (_error) {
          inspection = null;
        }
      }
      const row = document.createElement("div");
      row.className = "row";
      const info = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = value.name || inspection?.title || value.identifier;
      const meta = document.createElement("small");
      const visibility = inspection
        ? t(inspection.visibility === "public" ? "公开频道" : "私有频道")
        : t("类型暂未验证");
      meta.textContent = `${value.identifier} · ${visibility} · ${t(value.enabled ? "已启用" : "已暂停")}`;
      info.append(title, meta);
      const actions = document.createElement("div");
      actions.className = "actions";
      actions.append(button(t(value.enabled ? "暂停" : "恢复"), async () => {
        await api(`/api/v1/channels/${value.id}/${value.enabled ? "pause" : "resume"}`, { method: "POST" });
        await loadSearchChannels();
      }));
      actions.append(button(t("删除"), async () => {
        const prompt = currentLocale === "en-US"
          ? `Delete default search channel ${value.identifier}?`
          : `删除默认搜索频道 ${value.identifier}？`;
        if (!window.confirm(prompt)) return;
        await api(`/api/v1/channels/${value.id}`, { method: "DELETE" });
        await loadSearchChannels();
      }, true));
      row.append(info, actions);
      root.append(row);
    }
  } catch (error) { toast(error.message); }
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const session = await api("/api/v1/auth/login", {
      method: "POST",
      authFlow: true,
      body: JSON.stringify({
        username: $("#login-username").value,
        password: $("#login-password").value,
      }),
    });
    await enterWorkspace(session.username);
  } catch (error) {
    $("#login-password").value = "";
    $("#auth-copy").textContent = error.message;
  }
});

$("#setup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = $("#setup-password").value;
  if (password !== $("#setup-password-confirm").value) {
    $("#auth-copy").textContent = t("两次输入的密码不一致。");
    return;
  }
  try {
    await runBusy($("#setup-form button[type=submit]"), "正在保存…", async () => {
      const payload = { username: $("#setup-username").value, password };
      if (dockerBootstrapRequired) Object.assign(payload, {
        setup_code: $("#setup-code").value,
        telegram_api_id: Number($("#setup-api-id").value),
        telegram_api_hash: $("#setup-api-hash").value,
        proxy_url: $("#setup-proxy").value.trim() || null,
        language: $("#setup-language").value,
        timezone: $("#setup-timezone").value,
      });
      const session = await api("/api/v1/auth/setup", {
        method: "POST",
        authFlow: true,
        body: JSON.stringify(payload),
      });
      $("#setup-form").reset();
      await enterWorkspace(session.username);
      if (session.restart_required) {
        window.location.hash = "operations";
        toast(t("配置已保存。请重启容器后登录 Telegram。"));
      }
    }, $("#setup-form"));
  } catch (error) {
    $("#auth-copy").textContent = error.message;
  }
});

$("#logout").addEventListener("click", async () => {
  try { await api("/api/v1/auth/logout", { method: "POST", body: "{}" }); } catch (_error) { /* Session will still be hidden in this page. */ }
  showAuth("login", t("已安全退出本地控制台。"));
});

$("#phone-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const value = await api("/api/v1/telegram/login/code", { method: "POST", body: JSON.stringify({ phone: $("#phone").value }) });
    setTelegramStage(value.status);
    $("#code").focus();
  } catch (error) { toast(error.message); setTelegramStage("unavailable", error.message); }
});
$("#code-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const value = await api("/api/v1/telegram/login/verify", { method: "POST", body: JSON.stringify({ code: $("#code").value }) });
    $("#code").value = "";
    setTelegramStage(value.status);
    if (value.status === "password_required") $("#password").focus();
  } catch (error) { $("#code").value = ""; toast(error.message); }
});
$("#password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const value = await api("/api/v1/telegram/login/password", { method: "POST", body: JSON.stringify({ password: $("#password").value }) });
    $("#password").value = "";
    setTelegramStage(value.status);
  } catch (error) { $("#password").value = ""; toast(error.message); }
});
$("#search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const channels = $("#channels").value.split(",").map((item) => item.trim()).filter(Boolean);
  if (!channels.length && $("#search-channel").value) channels.push($("#search-channel").value);
  const resolution = $("#search-resolution").value;
  const language = $("#search-language").value;
  const maximumGb = $("#search-max-size").value;
  const hdr = $("#search-prefer-hdr").value;
  const payload = {
    query: $("#query").value,
    channels,
    resolutions: resolution ? [Number(resolution)] : [],
    languages: language ? [language] : [],
    max_file_size: maximumGb ? Math.round(Number(maximumGb) * 1073741824) : null,
    prefer_hdr: hdr ? hdr === "true" : null,
  };
  try {
    await runBusy(event.submitter || $("#search-form button"), "正在搜索…", async () => {
      $("#search-feedback").textContent = t("正在搜索…");
      $("#results").setAttribute("aria-busy", "true");
      const results = await api("/api/v1/searches", { method: "POST", body: JSON.stringify(payload) });
      if (apiConnected) renderResults(results);
    });
  } catch (error) { $("#search-feedback").textContent = error.message; toast(error.message); }
  finally { $("#results").setAttribute("aria-busy", "false"); }
});
$("#refresh").addEventListener("click", () => loadTasks());
$("#task-filter").addEventListener("change", () => {
  taskPageIndex = 0;
  loadTasks();
});
$("#task-previous").addEventListener("click", () => {
  taskPageIndex = Math.max(0, taskPageIndex - 1);
  loadTasks();
});
$("#task-next").addEventListener("click", () => {
  taskPageIndex += 1;
  loadTasks();
});
$("#refresh-status").addEventListener("click", () => { telegramStatus(); loadSystemStatus(); });
let diagnosticPreview = null;
$("#preview-diagnostics").addEventListener("click", async () => {
  const output = $("#diagnostic-preview");
  diagnosticPreview = null;
  $("#download-diagnostics").disabled = true;
  output.textContent = t("正在生成诊断预览…");
  $("#diagnostics-dialog").showModal();
  try {
    diagnosticPreview = JSON.stringify(await api("/api/v1/diagnostics"), null, 2) + "\n";
    output.textContent = diagnosticPreview;
    $("#download-diagnostics").disabled = false;
  } catch (error) {
    output.textContent = error.message;
  }
});
$("#download-diagnostics").addEventListener("click", () => {
  if (!diagnosticPreview) return;
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([diagnosticPreview], {type: "application/json"}));
  link.download = "telegram-media-diagnostics.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
});
$("#create-backup").addEventListener("click", async () => {
  try {
    await api("/api/v1/backups", { method: "POST" });
    toast(t("数据库备份已创建"));
    await loadBackups();
  } catch (error) { toast(error.message); }
});
$("#enqueue-kind").addEventListener("change", updateEnqueueKind);
for (const prefix of ["enqueue", "import"]) {
  $(`#${prefix}-replace`).addEventListener("change", () => updateReplacementMode(prefix));
  $(`#${prefix}-find-target`).addEventListener("click", () => findReplacementTarget(prefix));
  for (const name of ["title", "kind", "year", "season", "episode", "episode-end"]) {
    $(`#${prefix}-${name}`).addEventListener("input", () => resetReplacementTarget(prefix));
  }
}
$("#enqueue-cancel").addEventListener("click", () => {pendingResourceBatch=[];enqueuePreparationGeneration++;$("#enqueue-dialog").close();});
$("#enqueue-download-only").addEventListener("click", async () => {
  try {
    await runBusy($("#enqueue-download-only"), "正在入队…", async () => {
      const resourceId=$("#enqueue-resource-id").value;await enqueue(resourceId);
      $("#enqueue-dialog").close();
      await advanceResourceBatch(resourceId);
    }, $("#enqueue-form"));
  } catch (error) { toast(error.message); }
});
$("#enqueue-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const series = $("#enqueue-kind").value === "series";
  const year = $("#enqueue-year").value;
  const episodeEnd = $("#enqueue-episode-end").value;
  const archivePlan = {
    kind: series ? "series" : "movie",
    title: $("#enqueue-title").value.trim(),
    year: year ? Number(year) : null,
    season: series ? Number($("#enqueue-season").value) : null,
    episode: series ? Number($("#enqueue-episode").value) : null,
    episode_end: series && episodeEnd ? Number(episodeEnd) : null,
    confirmed_identity: $("#enqueue-confirmed").checked,
    replace_existing: $("#enqueue-replace").checked,
    target_path: $("#enqueue-replace").checked ? $("#enqueue-target").value : null,
  };
  try {
    await runBusy(event.submitter || $("#enqueue-form button[type=submit]"), "正在入队…", async () => {
      const resourceId=$("#enqueue-resource-id").value;await enqueue(resourceId, archivePlan);
      $("#enqueue-dialog").close();
      await advanceResourceBatch(resourceId);
    }, $("#enqueue-form"));
  } catch (error) { toast(error.message); }
});
$("#import-kind").addEventListener("change", () => {
  const series = $("#import-kind").value === "series";
  $("#import-season").disabled = !series;
  $("#import-episode").disabled = !series;
  $("#import-episode-end").disabled = !series;
});
$("#import-cancel").addEventListener("click", () => $("#import-dialog").close());
$("#events-close").addEventListener("click", () => $("#events-dialog").close());
$("#import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const series = $("#import-kind").value === "series";
  const year = $("#import-year").value;
  try {
    const replacing = $("#import-replace").checked;
    await runBusy(event.submitter || $("#import-form button[type=submit]"), "正在入库…", async () => {
      await api(`/api/v1/tasks/${$("#import-task-id").value}/${importIsReplacementPlan ? "replacement-plan" : replacing ? "replace" : "import"}`, {
        method: "POST",
        body: JSON.stringify({
          kind: series ? "series" : "movie",
          title: $("#import-title").value,
          year: year ? Number(year) : null,
          season: series ? Number($("#import-season").value) : null,
          episode: series ? Number($("#import-episode").value) : null,
          episode_end: series && $("#import-episode-end").value ? Number($("#import-episode-end").value) : null,
          confirmed_identity: $("#import-confirmed").checked,
          ...(replacing ? {target_path: $("#import-target").value} : {}),
          ...(importIsReplacementPlan ? {replace_existing:true} : {}),
        }),
      });
      $("#import-dialog").close();
      toast(t(importIsReplacementPlan ? "已设置下载后替换" : replacing ? "替换完成，旧文件备份已保留" : "入库完成"));
      await Promise.all([loadTasks(), loadTaskSummary()]);
    }, $("#import-form"));
  } catch (error) { toast(error.message); }
});
$("#subscription-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitter = event.submitter || $("#subscription-submit");
  if (submitter.disabled) return;
  const generation = subscriptionEditorGeneration;
  const channels = selectedSubscriptionChannels();
  try {
    const pendingEpisodes = parseEpisodePlan($("#subscription-pending-episodes").value);
    const continueTracking = $("#subscription-continue").checked;
    if (!continueTracking && !pendingEpisodes.length && (!editingSubscription || editingSubscription.enabled)) {
      throw new Error(t("取消持续追更时，必须填写需补集数。"));
    }
    const editing = editingSubscription;
    await runBusy(submitter, editing ? "正在保存…" : "正在创建…", () => api(editing ? `/api/v1/subscriptions/${editing.id}` : "/api/v1/subscriptions", {
      method: editing ? "PUT" : "POST",
      body: JSON.stringify({
        title: $("#subscription-title").value,
        query: $("#subscription-query").value,
        season: Number($("#subscription-season").value),
        next_episode: Number($("#subscription-episode").value),
        pending_episodes: pendingEpisodes,
        continue_tracking: continueTracking,
        channels,
        ...(editing ? {expected_version: editing.version} : {}),
      }),
    }), $("#subscription-form"));
    if (generation !== subscriptionEditorGeneration) { await loadSubscriptions(); return; }
    $("#subscription-pending-episodes").value = "";
    $("#subscription-channels").querySelectorAll("input:checked").forEach(input => { input.checked = false; });
    updateSubscriptionChannelSummary();
    toast(t(editing ? "订阅已更新" : "订阅已创建"));
    $("#subscription-dialog").close();
    await loadSubscriptions();
  } catch (error) { toast(error.message); }
});

$("#channel-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await runBusy(event.submitter, "正在验证频道…", async () => {
      const inspection = await api("/api/v1/channels/inspect", {
        method: "POST",
        body: JSON.stringify({ identifier: $("#channel-identifier").value }),
      });
      await api("/api/v1/channels", {
        method: "POST",
        body: JSON.stringify({
          identifier: inspection.identifier,
          name: $("#channel-name").value || inspection.title,
        }),
      });
      channelInspectionCache.set(inspection.identifier, inspection);
      form.reset();
      toast(t("默认搜索频道已添加"));
      $("#channel-dialog").close();
      await loadSearchChannels();
    }, form);
  } catch (error) { toast(error.message); }
});

$("#discover-channels").addEventListener("click", async (event) => {
  try {
    await runBusy(event.currentTarget, "正在发现频道…", async () => {
      const channels = await api("/api/v1/channels/discover");
      joinedChannels = channels;
      $("#joined-picker").hidden = false;
      renderJoinedChannels();
      const options = $("#joined-channel-options");
      options.replaceChildren();
      for (const channel of channels) {
        channelInspectionCache.set(channel.identifier, channel);
        const option = document.createElement("option");
        option.value = channel.identifier;
        option.label = `${channel.title} · ${t(channel.visibility === "public" ? "公开频道" : "私有频道")}`;
        options.append(option);
      }
      const message = t("已读取 {count} 个已加入频道").replace("{count}", String(channels.length));
      toast(message);
      $("#channel-identifier").focus();
    });
  } catch (error) { toast(error.message); }
});

for (const card of document.querySelectorAll("[data-task-view]")) {
  card.addEventListener("click", () => {
    const view = card.dataset.taskView;
    $("#task-filter").value = view;
    for (const tab of document.querySelectorAll("[data-filter]")) tab.classList.toggle("selected", tab.dataset.filter === view);
    for (const item of document.querySelectorAll("[data-task-view]")) item.classList.toggle("selected",item.dataset.taskView===view);
    taskPageIndex = 0;
    loadTasks();
    setQueueVisible(true);
  });
}

$("#task-filter").addEventListener("change", () => {
  for (const tab of document.querySelectorAll("[data-filter]")) tab.classList.toggle("selected", tab.dataset.filter === $("#task-filter").value);
  for (const item of document.querySelectorAll("[data-task-view]")) item.classList.toggle("selected",item.dataset.taskView===$("#task-filter").value);
});

async function bootstrap() {
  await loadUiConfiguration();
  try {
    const session = await api("/api/v1/auth/session", { authFlow: true });
    await enterWorkspace(session.username);
    return;
  } catch (_error) {
    // No active Web session; determine whether this is an existing-install migration.
  }
  try {
    const setup = await api("/api/v1/auth/setup", { authFlow: true });
    dockerBootstrapRequired = Boolean(setup.bootstrap_required);
    $("#docker-setup-fields").hidden = !dockerBootstrapRequired;
    $("#docker-setup-fields").disabled = !dockerBootstrapRequired;
    if (setup.setup_required && setup.local_setup_allowed) {
      showAuth("setup");
    } else if (setup.setup_required) {
      showAuth("login", t("管理员尚未创建。请在服务主机运行 mediactl set-admin。"));
      $("#login-form").hidden = true;
    } else {
      showAuth("login");
    }
  } catch (error) {
    showAuth("login", `${t("暂时无法连接服务：")}${error.message}`);
  }
}

bootstrap();

let taskRefreshTick = 0;
window.setInterval(() => {
  if (apiConnected && document.visibilityState === "visible") {
    taskRefreshTick += 1;
    if (currentPage !== "operations" && $("#section-tasks-panel").open) loadTasks(true);
    if (taskRefreshTick % 4 === 0) loadTaskSummary(true);
    if (taskRefreshTick % 4 === 0 && currentPage === "subscriptions-panel") loadSubscriptions(true);
    if (taskRefreshTick % 10 === 0) {
      loadSystemStatus();
      if (!["code_sent", "password_required"].includes(telegramStage)) telegramStatus();
    }
  }
}, 3000);
