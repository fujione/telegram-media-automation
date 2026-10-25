const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/telegram_media_automation/web/workspace.js'), 'utf8');

function fixture(crypto = require('node:crypto').webcrypto) {
  const context = vm.createContext({window: {addEventListener() {}}, document: {addEventListener() {}},
    t: value => value, taskStateLabels: {downloading: '下载中'}, crypto,
  });
  vm.runInContext(source, context);
  return context;
}

test('LAN HTTP without randomUUID generates unique valid UUIDs with getRandomValues', () => {
  const webcrypto = require('node:crypto').webcrypto;
  const context = fixture({getRandomValues: bytes => webcrypto.getRandomValues(bytes)});
  const keys = Array.from({length:1000}, () => context.newIdempotencyKey());
  assert.equal(new Set(keys).size,1000);
  for (const key of keys) assert.match(key,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

test('500 resources render a bounded page with every item reachable', () => {
  const context = fixture();
  const items = Array.from({length: 500}, (_, i) => i);
  const visited = [];
  for (let index = 0; index < 25; index++) {
    const page = context.pageSlice(items, index, 20);
    assert.equal(page.items.length, 20);
    assert.equal(page.pageCount, 25);
    visited.push(...page.items);
  }
  assert.deepEqual(visited, items);
  assert.equal(context.pageSlice(items, 999, 50).index, 9);
  assert.equal(context.pageSlice([], -1, 20).pageCount, 1);
});

test('downloaded task distinguishes automatic and manual import', () => {
  const context = fixture();
  assert.equal(context.taskLabel({state: 'downloaded'}), '等待手动入库');
  assert.equal(context.taskLabel({state: 'downloaded', archive_plan: {}}), '等待入库');
  assert.equal(context.taskLabel({state: 'imported'}), '已入库');
  assert.equal(context.taskLabel({state: 'cancelled'}), 'cancelled');
});

test('scan feedback explains waiting, rejection and enqueue without hiding channel errors', () => {
  const context = fixture();
  const scan = {state:'partial',outcome:'waiting',target_episode:23,scanned_media_count:4,
    rejection_counts:{episode_conflict:2,unparsed_episode:1,future_episode:1,past_episode:0}};
  assert.deepEqual(Array.from(context.subscriptionScanDetails(scan)),
    ['等待第 23 集','本轮媒体候选 4 个','集数冲突 2','集数无法识别 1','后续集待处理 1']);
  assert.deepEqual(Array.from(context.subscriptionScanDetails({...scan,state:'failed'})),[]);
  assert.deepEqual(Array.from(context.subscriptionScanDetails({state:'succeeded',outcome:'enqueued',target_episode:23})),['第 23 集已入队']);
  assert.deepEqual(Array.from(context.subscriptionScanDetails(null)),[]);
  assert.deepEqual(Array.from(context.subscriptionScanDetails({state:'succeeded',outcome:'existing_task',target_episode:23})),['第 23 集已有失败或取消任务，请检查下载队列。']);
});

test('Telegram health differentiates an existing session from proxy and network failure', () => {
  const context = fixture();
  assert.equal(context.telegramHealthLabel({telegram_status:'authorized'}),'已连接');
  assert.equal(context.telegramHealthLabel({telegram_status:'login_required'}),'需要登录');
  assert.match(context.telegramHealthLabel({telegram_status:'telegram_proxy_failed'}),/代理.*出口节点/);
  assert.match(context.telegramHealthLabel({telegram_status:'telegram_timeout'}),/超时.*代理或网络/);
  assert.equal(context.telegramHealthLabel({checks:{telegram:false}}),'连接不可用');
  assert.equal(context.telegramHealthLabel({checks:{telegram:true}}),'已连接');
});

test('episode conflict never displays a confidently guessed episode', () => {
  const context = fixture();
  assert.equal(context.episodeLabel({episode_conflict: true, episode_range: {season: 1, start: 22, end: 22}}), '集数待确认');
  assert.equal(context.episodeLabel({episode_range: {season: 1, start: 22, end: 24}}), 'S01E22–E24');
});

test('progress is bounded and unknown size does not imply completion', () => {
  const context = fixture();
  assert.equal(context.percentFor({downloaded_bytes: 200, expected_size: 100}), 100);
  assert.equal(context.percentFor({downloaded_bytes: 20, expected_size: 0}), 0);
});

test('resource card title is derived from the original filename, not a search guess',()=>{
  const context=fixture();
  assert.equal(context.resourceDisplayName({file_name:'早春晴朗.S01E22.2160p.HEVC.mp4'}),'早春晴朗');
  assert.equal(context.resourceDisplayName({file_name:'电影名字 1080p HEVC.mkv'}),'电影名字');
  assert.equal(context.resourceDisplayName({file_name:'S01E22.mp4'}),'S01E22');
});
