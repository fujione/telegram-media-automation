/* Run against the local service. All task writes are intercepted; no downloads are started. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

async function main() {
  const root = path.resolve(__dirname, '..');
  const url = process.env.WEBUI_TEST_URL || 'http://127.0.0.1:8083';
  const python = path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
  const session = process.env.WEBUI_TEST_SESSION || execFileSync(python, ['-c',
    'from telegram_media_automation.infrastructure.config import Settings; from telegram_media_automation.infrastructure.web_auth import derive_web_session_key,issue_web_session; s=Settings(); print(issue_web_session("admin",derive_web_session_key(s.admin_credentials_file,s.resolved_api_token()),3600))'],
    {cwd: root, encoding: 'utf8'}).trim();
  const browser = await chromium.launch({headless: true, ...(process.env.WEBUI_TEST_BROWSER === 'chromium' ? {} : {channel: 'chrome'})});
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 960}});
    await context.addInitScript(() => Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true}));
    await context.addCookies([{name: 'tma_session', value: session, url, httpOnly: true, sameSite: 'Lax'}, {name: 'tma_csrf', value: 'local-review-csrf', url}]);
    const page = await context.newPage();
    const channelChoices = [{id:'choice-1',identifier:'first_channel',name:'来源一',enabled:true},
      {id:'choice-2',identifier:'second_channel',name:'来源二',enabled:true},
      {id:'choice-3',identifier:'paused_channel',name:'暂停的频道',enabled:false}];
    await page.route('**/api/v1/channels', route=>route.fulfill({json:channelChoices}));
    const reviewSubscriptions = [
      {id:'subscription-active',title:'正常追更测试',enabled:true,season:1,next_episode:23,pending_episodes:[],continue_tracking:true,channels:[],scan_status:{state:'succeeded',outcome:'waiting',target_episode:23,scanned_media_count:0,rejection_counts:{},stale:true,plan_idle_days:17,successful_channels:2,channel_count:2,checked_at:'2026-09-14T01:00:00Z'}},
      {id:'subscription-partial',title:'部分异常测试',enabled:true,season:1,next_episode:3,pending_episodes:[],continue_tracking:true,channels:['first_channel'],scan_status:{state:'partial',successful_channels:1,channel_count:2,failed_channels:['second_channel'],message:'部分频道暂不可用，将自动重试',checked_at:'2026-09-14T01:00:00Z'}},
      {id:'subscription-paused',title:'暂停追更测试',enabled:false,season:1,next_episode:4,pending_episodes:[],continue_tracking:true,channels:[],scan_status:{state:'failed',message:'频道检查失败，将自动重试'}},
    ];
    reviewSubscriptions[0].scan_health = {last_attempt_at:'2026-09-14T01:00:00Z',last_success_at:'2026-09-14T01:00:00Z',last_enqueued_at:'2026-08-28T01:00:00Z',resource_idle_days:17,consecutive_failures:0};
    await page.route('**/api/v1/subscriptions', route=>route.fulfill({json:reviewSubscriptions}));
    // Only explicitly mocked business commands may execute during this review.
    await context.route('**/*', route=>{
      const req=route.request(), pathname=new URL(req.url()).pathname;
      if(!['GET','HEAD','OPTIONS'].includes(req.method()) && pathname!='/api/v1/channels/inspect') return route.abort();
      return route.continue();
    });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(url);
    await page.locator('#app-shell').waitFor({state: 'visible'});
    await page.locator('#summary-total').filter({hasText: /^\d+$/}).waitFor();
    await page.locator('#tasks').filter({hasNotText:'正在读取任务'}).waitFor();
    await page.locator('#status-checked').filter({hasNotText:'正在读取状态'}).waitFor({state:'attached'});
    const output = path.join(root, 'dist/webui-review'); fs.mkdirSync(output, {recursive: true});
    await page.screenshot({path: path.join(output, 'overview-desktop.png')});
    await page.setViewportSize({width:2560,height:1240});
    await page.screenshot({path:path.join(output,'workbench-wide.png')});
    assert.ok(Math.abs(await page.locator('#section-discovery').evaluate(node=>node.getBoundingClientRect().top)-await page.locator('#section-tasks-panel').evaluate(node=>node.getBoundingClientRect().top))<1);
    assert.equal(await page.locator('#section-channels-panel').isVisible(),false);
    await page.setViewportSize({width:1440,height:960});
    assert.equal(await page.locator('[data-page]:visible').count(), 1);
    assert.equal(await page.locator('.workbench-section').count(),4);
    assert.equal(await page.locator('#task-summary').evaluate(node=>node.getBoundingClientRect().height<50),true);
    assert.equal(await page.locator('.workspace-nav a').count(),3);
    assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).colorScheme),'dark');
    await page.locator('#toggle-sections').click();
    assert.equal(await page.locator('#section-tasks-panel').isVisible(),false);
    await page.reload();
    await page.locator('#app-shell').waitFor({state:'visible'});
    assert.equal(await page.locator('#section-tasks-panel').isVisible(),false);
    await page.locator('#toggle-sections').click();
    assert.equal(await page.locator('#section-tasks-panel').isVisible(),true);
    await page.locator('.workspace-nav a[href="#subscriptions-panel"]').click();await page.locator('#subscriptions-panel').waitFor({state:'visible'});assert.equal(await page.locator('#discovery').isVisible(),false);
    await page.locator('.subscription-row').first().waitFor();
    assert.equal(await page.locator('.subscription-row').count(),3);
    assert.equal(await page.locator('.subscription-badge.is-active').count(),2);
    assert.equal(await page.locator('.subscription-badge.is-paused').count(),1);
    assert.match(await page.locator('.subscription-row').filter({hasText:'部分异常测试'}).locator('.scan-warning').textContent(),/部分频道异常.*second_channel.*自动重试/);
    assert.match(await page.locator('.scan-summary').textContent(),/等待第 23 集.*本轮媒体候选 0 个/);
    assert.match(await page.locator('.scan-stale').textContent(),/17 天未推进.*扫描仍会继续/);
    assert.equal(await page.locator('.subscription-paused .subscription-scan').count(),0);
    const activeRow = page.locator('.subscription-row').filter({hasText:'正常追更测试'});
    assert.match(await activeRow.locator('.scan-health').first().textContent(),/最近尝试.*最近成功/);
    let checks = 0;
    await page.route('**/api/v1/subscriptions/subscription-active/check', route=>{
      checks++;
      reviewSubscriptions[0].scan_health.requested_at = '2026-10-26T01:00:00Z';
      return route.fulfill({status:202,json:{status:'queued',retry_at:'2026-10-26T01:05:00Z'}});
    });
    await activeRow.getByRole('button',{name:'立即检查',exact:true}).click();
    await activeRow.getByRole('button',{name:'检查已排队',exact:true}).waitFor();
    assert.equal(await activeRow.getByRole('button',{name:'检查已排队',exact:true}).isDisabled(),true);
    assert.equal(checks,1);
    assert.match(await page.locator('#toast').textContent(),/最早重试时间/);
    reviewSubscriptions[0].scan_health.requested_at = null;
    await page.route('**/api/v1/subscriptions/subscription-active/scans?limit=20',route=>route.fulfill({json:[reviewSubscriptions[0].scan_status]}));
    await activeRow.getByRole('button',{name:'扫描历史',exact:true}).click();
    await page.locator('#scan-history-list').filter({hasText:'等待第 23 集'}).waitFor();
    await page.locator('[data-close="scan-history-dialog"]').click();
    await page.locator('[data-subscription-filter="active"]').click();
    assert.equal(await page.locator('.subscription-row').count(),2);
    assert.equal(await page.locator('[data-subscription-filter="active"]').getAttribute('aria-pressed'),'true');
    await page.locator('[data-subscription-filter="paused"]').click();
    assert.equal(await page.locator('.subscription-row').count(),1);
    assert.match(await page.locator('.subscription-row').textContent(),/暂停追更测试/);
    await page.locator('[data-subscription-filter="all"]').click();
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.locator('#subscriptions-panel').evaluate(n=>n.scrollWidth<=n.clientWidth+1),true);
    await page.screenshot({path:path.join(output,'subscriptions-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:960});
    await page.screenshot({path:path.join(output,'subscriptions-desktop.png'),fullPage:true});
    const safeDiagnostic = {format_version:1,subscriptions:[{reference:'masked',state:'succeeded'}]};
    await page.route('**/api/v1/diagnostics',route=>route.fulfill({json:safeDiagnostic}));
    await page.goto(`${url}/#operations`);
    await page.locator('#preview-diagnostics').click();
    await page.locator('#diagnostic-preview').filter({hasText:'masked'}).waitFor();
    const previewText = await page.locator('#diagnostic-preview').textContent();
    const downloadPromise = page.waitForEvent('download');
    await page.locator('#download-diagnostics').click();
    const diagnosticDownload = await downloadPromise;
    assert.equal(fs.readFileSync(await diagnosticDownload.path(),'utf8'),previewText);
    assert.deepEqual(JSON.parse(previewText),safeDiagnostic);
    await page.setViewportSize({width:320,height:844});
    assert.equal(await page.locator('#diagnostics-dialog').evaluate(n=>n.getBoundingClientRect().right<=innerWidth),true);
    await page.screenshot({path:path.join(output,'diagnostics-mobile.png')});
    await page.locator('[data-close="diagnostics-dialog"]').click();
    await page.setViewportSize({width:1440,height:960});
    await page.locator('.workspace-nav a[href="#discovery"]').click();
    assert.equal(await page.locator('#telegram-dialog').isVisible(), false);
    assert.equal(await page.locator('body').evaluate(node => node.textContent.includes('没有 Bot')), false);

    assert.equal(await page.evaluate(()=>typeof crypto.randomUUID),'undefined');
    const editFixture = {...reviewSubscriptions[0], query:'Original query', version:7, channels:['legacy_private']};
    let conflict = true, editedPayload;
    await page.route('**/api/v1/subscriptions/subscription-active',route=>{
      if(route.request().method()==='GET') return route.fulfill({json:editFixture});
      editedPayload=route.request().postDataJSON();
      if(conflict) return route.fulfill({status:409,json:{detail:{code:'subscription_changed',message:'订阅已被扫描或其他操作更新,请重新载入后再保存。'}}});
      return route.fulfill({json:{...editFixture,...editedPayload,version:9}});
    });
    await page.locator('.workspace-nav a[href="#subscriptions-panel"]').click();
    await page.locator('.subscription-row').filter({hasText:'正常追更测试'}).getByRole('button',{name:'编辑',exact:true}).click();
    await page.locator('#subscription-dialog').waitFor({state:'visible'});
    assert.equal(await page.locator('#subscription-query').inputValue(),'Original query');
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.locator('#subscription-dialog').evaluate(node=>node.scrollWidth<=node.clientWidth+1),true);
    await page.screenshot({path:path.join(output,'subscription-edit-mobile.png')});
    await page.setViewportSize({width:1440,height:960});
    await page.locator('#subscription-channels input[value="legacy_private"]').waitFor();
    assert.equal(await page.locator('#subscription-channels input[value="legacy_private"]').isChecked(),true,'retain a channel missing from defaults');
    await page.locator('#subscription-query').fill('Unsaved edit');
    await page.locator('#subscription-submit').click();
    await page.locator('#subscription-form .form-error').waitFor();
    assert.equal(await page.locator('#subscription-query').inputValue(),'Unsaved edit');
    assert.equal(editedPayload.expected_version,7);
    assert.deepEqual(editedPayload.channels,['legacy_private']);
    editFixture.version=8;editFixture.next_episode=24;
    await page.locator('#subscription-reload').click();
    await page.waitForFunction(()=>document.querySelector('#subscription-query').value==='Original query');
    assert.equal(await page.locator('#subscription-episode').inputValue(),'24');
    conflict=false;
    await page.locator('#subscription-query').fill('Saved edit');
    await page.locator('#subscription-submit').click();
    await page.locator('#subscription-dialog').waitFor({state:'hidden'});
    assert.equal(editedPayload.expected_version,8);
    assert.equal(editedPayload.query,'Saved edit');
    await page.locator('#new-subscription').click();
    assert.equal(await page.locator('#subscription-query').inputValue(),'');
    assert.equal(await page.locator('#subscription-reload').isVisible(),false);
    await page.locator('[data-close="subscription-dialog"]').click();
    const subscriptionsCreated=[];
    await page.route('**/api/v1/subscriptions',route=>{
      if(route.request().method()!=='POST')return route.fulfill({json:reviewSubscriptions});
      const payload=route.request().postDataJSON();subscriptionsCreated.push(payload);
      return route.fulfill({status:201,json:{id:'mock-subscription',...payload}});
    });
    await page.locator('.workspace-nav a[href="#subscriptions-panel"]').click();
    await page.locator('#new-subscription').click();
    await page.locator('#subscription-title').fill('测试订阅');await page.locator('#subscription-query').fill('测试订阅');
    await page.locator('#subscription-form button[type=submit]').click();await page.locator('#subscription-dialog').waitFor({state:'hidden'});
    assert.deepEqual(subscriptionsCreated[0].channels,[]);
    await page.locator('#new-subscription').click();
    await page.locator('.subscription-channel-scope summary').click();
    await page.locator('#subscription-channels input').first().waitFor();
    assert.equal(await page.locator('#subscription-channels input').count(),2);
    await page.locator('#subscription-title').fill('另一订阅');await page.locator('#subscription-query').fill('另一订阅');
    await page.locator('#subscription-channels input[value="second_channel"]').check();
    assert.match(await page.locator('#subscription-channel-summary').textContent(),/1/);
    await page.locator('#subscription-form button[type=submit]').click();await page.locator('#subscription-dialog').waitFor({state:'hidden'});
    assert.deepEqual(subscriptionsCreated[1].channels,['second_channel']);
    await page.locator('#new-subscription').click();assert.match(await page.locator('#subscription-channel-summary').textContent(),/全部默认频道/);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.locator('#subscription-dialog').evaluate(n=>n.scrollWidth<=n.clientWidth+1),true);
    await page.locator('[data-close="subscription-dialog"]').click();await page.setViewportSize({width:1440,height:960});

    const resources = Array.from({length: 500}, (_, i) => ({id: `review-resource-${i}`, provider: 'telegram',
      file_name: `界面测试剧集 S01E${String(i + 1).padStart(2,'0')} 2160p HEVC 中文.mp4`, file_size: 2800000000 + i,
      identity_confidence: 95, episode_range: {season: 1, start: i + 1, end: i + 1}, episode_conflict: false,
      quality: {resolution: 2160, codec: 'HEVC', hdr: false, languages: ['zh']},
      source: {channel_id: -100123, message_id: i + 1, channel_title: '界面测试频道', message_link: `https://t.me/review/${i + 1}`},
    }));
    await page.route('**/api/v1/searches', route => route.fulfill({json: resources}));
    await page.locator('.workspace-nav a[href="#discovery"]').click();
    await page.locator('#query').fill('界面测试剧集');
    await page.locator('#search-form button[type=submit]').click();
    await page.locator('#result-count').filter({hasText: '500'}).waitFor();
    assert.equal(await page.locator('.resource-row').count(), 10);
    assert.match(await page.locator('#result-page-status').textContent(), /50/);
    assert.equal(await page.locator('#result-pager').evaluate(node => node.getBoundingClientRect().bottom <= innerHeight), true);
    await page.locator('#result-next').click();
    assert.match(await page.locator('.resource-row').first().textContent(), /S01E11/);
    await page.locator('.resource-row').first().locator('.media-name').click();
    await page.locator('#detail-dialog').waitFor({state:'visible'});
    await page.locator('[data-close="detail-dialog"]').click();
    assert.match(await page.locator('#result-page-status').textContent(), /2 \/ 50/);
    await page.locator('#result-page-size').selectOption('50');
    assert.equal(await page.locator('.resource-row').count(), 50);
    await page.locator('#result-page-size').selectOption('20');
    await page.screenshot({path: path.join(output, 'search-desktop-test-data.png')});

    // Validate media confirmation, target selection, and retry idempotency without actual task writes.
    await page.locator('.resource-row').first().getByRole('button', {name:'下载', exact:true}).click();
    await page.locator('#enqueue-dialog').waitFor({state:'visible'});
    assert.equal(await page.locator('#enqueue-confirmed').isChecked(), false);
    await page.locator('#enqueue-title').fill('界面测试剧集');
    await page.locator('#enqueue-replace').check();
    assert.equal(await page.locator('#enqueue-download-only').isVisible(), false);
    await page.route('**/api/v1/library/files?*', route => route.fulfill({json:[{path:'/media/tv/界面测试剧集/Season 01/界面测试剧集 - S01E01.mp4',file_size:1200000000}]}));
    await page.locator('#enqueue-find-target').click();
    await page.locator('#enqueue-target option').filter({hasText:'/media/tv/'}).waitFor({state:'attached'});
    assert.equal(await page.locator('#enqueue-target').inputValue(), '');
    await page.locator('#enqueue-target').selectOption({index:1});
    await page.locator('#enqueue-confirmed').check();
    const requests = [];
    await page.route('**/api/v1/tasks', route => {
      if (route.request().method() !== 'POST') return route.continue();
      requests.push({key: route.request().headers()['idempotency-key'], body: route.request().postDataJSON()});
      return route.fulfill(requests.length === 1 ? {status:503,json:{detail:{message:'界面测试：重试'}}} : {json:{id:'review-task'}});
    });
    await page.locator('#enqueue-form button[type=submit]').click();
    await page.locator('#enqueue-form .form-error').filter({hasText:'界面测试：重试'}).waitFor();
    await page.locator('#enqueue-form button[type=submit]').click();
    await page.locator('#enqueue-dialog').waitFor({state:'hidden'});
    assert.equal(requests.length,2); assert.equal(requests[0].key,requests[1].key);
    assert.equal(requests[1].body.archive_plan.replace_existing,true);
    assert.equal(requests[1].body.archive_plan.confirmed_identity,true);

    // Multi-select retains individual media confirmation and creates distinct idempotency keys.
    await page.locator('.resource-row input[type=checkbox]').nth(0).check();
    await page.locator('.resource-row input[type=checkbox]').nth(1).check();
    await page.locator('#batch-download').click();await page.locator('#enqueue-dialog').waitFor({state:'visible'});
    assert.equal(await page.locator('#enqueue-confirmed').isChecked(),false);
    await page.locator('#enqueue-confirmed').check();await page.locator('#enqueue-form button[type=submit]').click();
    await page.waitForFunction(()=>document.querySelector('#enqueue-resource-id').value==='review-resource-1');
    assert.equal(await page.locator('#enqueue-confirmed').isChecked(),false);
    assert.equal(await page.locator('#enqueue-episode').inputValue(),'2');
    await page.locator('#enqueue-confirmed').check();await page.locator('#enqueue-form button[type=submit]').click();await page.locator('#enqueue-dialog').waitFor({state:'hidden'});
    assert.equal(requests.length,4);assert.notEqual(requests[2].key,requests[3].key);assert.equal(requests[2].body.archive_plan.replace_existing,false);assert.equal(requests[3].body.archive_plan.episode,2);
    assert.equal(await page.locator('#batch-download').isDisabled(),true);

    // Direct replacement entry is discoverable without opening resource details first.
    await page.locator('.resource-row').first().getByRole('button',{name:'替换',exact:true}).click();
    await page.locator('#enqueue-dialog').waitFor({state:'visible'});
    assert.equal(await page.locator('#enqueue-replace').isChecked(),true);await page.locator('#enqueue-cancel').click();

    // A thousand-task fixture validates server pagination and stable progress updates.
    const task = {id:'review-task-1', resource_id:resources[0].id, state:'downloading', downloaded_bytes:100,
      expected_size:1000, attempt_count:1, archive_plan:{kind:'series',title:'界面测试剧集',season:1,episode:1}, output_path:null,error_message:null};
    await page.route('**/api/v1/tasks/page?*', route => {
      const query = new URL(route.request().url()).searchParams;
      const offset = Number(query.get('offset') || 0),limit=Number(query.get('limit')||20);
      return route.fulfill({json:{items:Array.from({length:limit},(_,i)=>({...task,id:`review-task-${offset+i+1}`})),offset,limit,total:1000}});
    });
    await page.locator('a[href="#tasks-panel"]').click();
    await page.locator('#task-page-status').filter({hasText:'1 / 50'}).waitFor();
    assert.equal(await page.locator('.task-row').count(),20);
    assert.match(await page.locator('#task-page-status').textContent(),/50/);
    await page.locator('.task-row').first().getByRole('button',{name:'详情',exact:true}).focus();
    await page.waitForTimeout(3400);
    assert.equal(await page.locator('.task-row').first().getByRole('button',{name:'详情',exact:true}).evaluate(node=>node===document.activeElement),true);
    await page.locator('#task-next').click();
    await page.locator('#task-page-status').filter({hasText:'2 / 50'}).waitFor();
    await page.locator('.task-row').first().getByRole('button',{name:'详情',exact:true}).click();
    assert.match(await page.locator('#detail-content').textContent(),/界面测试频道/);
    await page.locator('#detail-actions').getByRole('button',{name:'设为下载后替换',exact:true}).click();
    await page.locator('#import-dialog').waitFor({state:'visible'});assert.equal(await page.locator('#import-replace').isChecked(),true);
    await page.locator('#import-find-target').click();await page.locator('#import-target').selectOption({index:1});await page.locator('#import-confirmed').check();
    let replacementPlan;
    await page.route('**/api/v1/tasks/*/replacement-plan',route=>{replacementPlan=route.request().postDataJSON();return route.fulfill({json:{id:'review-task-21'}});});
    await page.locator('#import-form button[type=submit]').click();await page.locator('#import-dialog').waitFor({state:'hidden'});
    assert.equal(replacementPlan.replace_existing,true);assert.equal(replacementPlan.confirmed_identity,true);assert.match(replacementPlan.target_path,/\/media\/tv/);

    // Completed-file replacement must use /replace, not a stale replacement-plan mode.
    task.state='downloaded';task.archive_plan=null;task.output_path='/downloads/inbox/test.mp4';
    await page.locator('#refresh').click();await page.locator('.task-row').first().filter({hasText:'等待手动入库'}).waitFor();
    await page.locator('.task-row').first().getByRole('button',{name:'详情',exact:true}).click();
    await page.locator('#detail-actions').getByRole('button',{name:'替换',exact:true}).click();await page.locator('#import-dialog').waitFor({state:'visible'});
    await page.locator('#import-title').fill('界面测试剧集');await page.locator('#import-find-target').click();await page.locator('#import-target').selectOption({index:1});await page.locator('#import-confirmed').check();
    let completedReplacement;
    await page.route('**/api/v1/tasks/*/replace',route=>{completedReplacement=route.request().postDataJSON();return route.fulfill({json:{id:'review-task-21'}});});
    await page.locator('#import-form button[type=submit]').click();await page.locator('#import-dialog').waitFor({state:'hidden'});assert.equal(completedReplacement.confirmed_identity,true);assert.equal(completedReplacement.replace_existing,undefined);

    // Task controls use explicit commands while never reaching a real mutation endpoint.
    task.state='downloading';
    await page.route('**/api/v1/tasks/*/pause',route=>{task.state='paused';return route.fulfill({json:{id:'review-task'}});});
    await page.route('**/api/v1/tasks/*/resume',route=>{task.state='downloading';return route.fulfill({json:{id:'review-task'}});});
    await page.locator('#refresh').click();await page.locator('.task-row').first().getByRole('button',{name:'暂停',exact:true}).click();await page.locator('.task-row').first().getByRole('button',{name:'继续',exact:true}).waitFor();
    await page.locator('.task-row').first().getByRole('button',{name:'继续',exact:true}).click();await page.locator('.task-row').first().getByRole('button',{name:'暂停',exact:true}).waitFor();

    for (const width of [320,390,768,1024,1366,1440,1920,2560]) {
      await page.setViewportSize({width,height:900});
      for (const hash of ['overview','discovery','tasks-panel','subscriptions-panel','channels-panel','operations']) {
        await page.goto(`${url}/#${hash}`);
        await page.locator(`#${hash}`).waitFor({state:'visible'});
        // Layout assertions must observe the final responsive position, after
        // the navigation marker and queue transitions finish.
        await page.evaluate(async()=>{
          await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
          await Promise.all(document.getAnimations().filter(animation=>Number.isFinite(animation.effect.getComputedTiming().endTime)).map(animation=>animation.finished.catch(()=>{})));
        });
        const overflow = await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
        if (overflow) {
          console.log(JSON.stringify(await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(n=>n.getBoundingClientRect().right>innerWidth+1).slice(0,12).map(n=>({tag:n.tagName,id:n.id,class:n.className,right:n.getBoundingClientRect().right})))));
          await page.screenshot({path:path.join(output,`overflow-${hash}-${width}.png`)});
        }
        assert.equal(overflow,false,`${hash} horizontal overflow at ${width}px`);
      }
    }
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.locator('a[href="#operations"][aria-label="设置"]').isVisible(),true);
    await page.locator('.workspace-nav a[href="#discovery"]').click();
    await page.locator('#query').fill('界面测试剧集');
    await page.locator('#search-form button[type=submit]').click();
    await page.locator('.resource-row').first().waitFor();
    await page.screenshot({path:path.join(output,'search-mobile-test-data.png')});
    assert.equal(await page.locator('[data-page]:visible').count(),1);
    const textContrast=await page.locator('.resource-row').first().evaluate(node=>{const rgb=css=>css.match(/[\d.]+/g).slice(0,3).map(Number),lum=values=>values.map(value=>{const c=value/255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4;}).reduce((total,c,i)=>total+c*[.2126,.7152,.0722][i],0),bg=lum(rgb(getComputedStyle(node).backgroundColor));return ['.media-name','.media-sub'].map(selector=>{const text=lum(rgb(getComputedStyle(node.querySelector(selector)).color));return (Math.max(text,bg)+.05)/(Math.min(text,bg)+.05);});});
    assert.equal(textContrast.every(value=>value>=4.5),true);
    await page.emulateMedia({reducedMotion:'reduce'});await page.locator('.workspace-nav a[href="#subscriptions-panel"]').click();await page.locator('#subscriptions-panel').waitFor({state:'visible'});assert.equal(await page.evaluate(()=>document.getAnimations().length),0);
    const localePage = await context.newPage();
    localePage.on('pageerror',error=>errors.push(error.message));
    await localePage.route('**/api/v1/ui/config', route => route.fulfill({json:{language:'en-US',timezone:'UTC'}}));
    await localePage.goto(`${url}/#overview`);
    await localePage.locator('#page-title').filter({hasText:'What are we watching tonight?'}).waitFor();
    assert.equal(await localePage.locator('.workspace-nav a[href="#channels-panel"]').textContent(), 'Channels');
    await localePage.route('**/api/v1/subscriptions',route=>route.fulfill({json:reviewSubscriptions}));
    await localePage.goto(`${url}/#subscriptions-panel`);
    await localePage.locator('.scan-summary').filter({hasText:'Waiting for episode 23'}).waitFor();
    assert.equal(await localePage.getByRole('button',{name:'Check now',exact:true}).count(),2);
    assert.equal(await localePage.getByRole('button',{name:'Scan history',exact:true}).count(),3);
    assert.match(await localePage.locator('.scan-stale').textContent(),/17 days.*scanning will continue/);
    await localePage.route('**/api/v1/subscriptions/subscription-active',route=>route.fulfill({json:editFixture}));
    await localePage.locator('.subscription-row').filter({hasText:'正常追更测试'}).getByRole('button',{name:'Edit',exact:true}).click();
    await localePage.locator('#subscription-dialog h2').filter({hasText:'Edit subscription'}).waitFor();
    assert.equal(await localePage.locator('#subscription-submit').textContent(),'Save changes');
    await localePage.locator('[data-close="subscription-dialog"]').click();
    await localePage.route('**/api/v1/telegram/status', route => route.fulfill({status:503,json:{detail:{message:'Temporary network issue'}}}));
    await localePage.route('**/health/ready',route=>route.fulfill({status:503,json:{status:'not_ready',checks:{database:true,configured:true,workers:true,telegram:false},telegram_status:'telegram_proxy_failed'}}));
    await localePage.goto(`${url}/#operations`);
    await localePage.locator('#service-label').filter({hasText:'Telegram connection unavailable'}).waitFor();
    await localePage.locator('#system-status').filter({hasText:'Proxy connection failed. Check the exit node.'}).waitFor();
    assert.equal(await localePage.locator('#phone-form').isVisible(),false);
    await localePage.route('**/api/v1/tasks/summary', route => route.fulfill({status:401,json:{detail:{message:'Session expired'}}}));
    await localePage.goto(url);
    await localePage.locator('#login-form').waitFor({state:'visible'});
    assert.equal(await localePage.locator('#telegram-dialog').isVisible(),false);
    const firstRunContext = await browser.newContext({viewport:{width:390,height:844}});
    await firstRunContext.addCookies(await context.cookies());
    await firstRunContext.route('**/*',route=>['GET','HEAD','OPTIONS'].includes(route.request().method())?route.continue():route.abort());
    const firstRun = await firstRunContext.newPage();
    firstRun.on('pageerror',error=>errors.push(error.message));
    await firstRun.route('**/api/v1/ui/config',route=>route.fulfill({json:{language:'en-US',timezone:'UTC'}}));
    await firstRun.route('**/api/v1/auth/session',route=>route.fulfill({status:401,json:{detail:{code:'unauthorized'}}}));
    let setupPayload, finishSetup, setupStarted;
    const setupGate = new Promise(resolve=>{finishSetup=resolve;});
    const started = new Promise(resolve=>{setupStarted=resolve;});
    await firstRun.route('**/api/v1/auth/setup',async route=>{
      if(route.request().method()==='GET') return route.fulfill({json:{setup_required:true,local_setup_allowed:true,bootstrap_required:true}});
      setupPayload=route.request().postDataJSON();setupStarted();await setupGate;
      return route.fulfill({json:{authenticated:true,username:'admin',restart_required:true}});
    });
    await firstRun.route('**/api/v1/system/status',async route=>{
      const response=await route.fetch();return route.fulfill({json:{...await response.json(),setup_restart_required:true}});
    });
    await firstRun.goto(url);
    await firstRun.locator('#docker-setup-fields').waitFor({state:'visible'});
    assert.match(await firstRun.locator('#docker-setup-fields').textContent(),/First-time setup.*mediactl setup-code/);
    await firstRun.locator('#setup-code').fill('browser-only-setup-code');
    await firstRun.locator('#setup-api-id').fill('12345');
    await firstRun.locator('#setup-api-hash').fill('browser-only-api-hash');
    await firstRun.locator('#setup-proxy').fill('socks5://proxy.invalid:1080');
    await firstRun.locator('#setup-language').selectOption('en-US');
    await firstRun.locator('#setup-timezone').fill('UTC');
    await firstRun.locator('#setup-password').fill('Browser-only-password-2026');
    await firstRun.locator('#setup-password-confirm').fill('Browser-only-password-2026');
    await firstRun.setViewportSize({width:320,height:844});
    assert.equal(await firstRun.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    await firstRun.evaluate(()=>scrollTo(0,0));
    await firstRun.screenshot({path:path.join(output,'docker-first-run-mobile.png'),fullPage:true});
    await firstRun.locator('#setup-form button[type=submit]').click();await started;
    assert.equal(await firstRun.locator('#setup-form button[type=submit]').isDisabled(),true);
    finishSetup();
    await firstRun.locator('#app-shell').waitFor({state:'visible'});
    await firstRun.locator('#setup-restart-note').waitFor({state:'visible'});
    assert.match(await firstRun.locator('#setup-restart-note').textContent(),/Configuration saved.*docker compose restart api/);
    assert.equal(setupPayload.telegram_api_id,12345);
    assert.equal(setupPayload.telegram_api_hash,'browser-only-api-hash');
    assert.equal(setupPayload.setup_code,'browser-only-setup-code');
    assert.equal(setupPayload.language,'en-US');
    assert.equal(await firstRun.locator('#setup-api-hash').inputValue(),'');
    assert.equal(await firstRun.locator('#setup-code').inputValue(),'');
    assert.equal(await firstRun.locator('#setup-password').inputValue(),'');
    assert.equal(await firstRun.evaluate(()=>Object.values(localStorage).join(' ').includes('browser-only-api-hash')),false);
    await firstRunContext.close();
    assert.deepEqual(errors,[]);
    console.log('Browser checks passed: top navigation, dark cards, persisted queue, 500-resource paging, 1000-task paging, individual batch confirmation, auto-import plans, direct replacement, replacement scheduling, idempotency, focus preservation, 8 viewport widths, English UI, network failure, session expiry.');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode=1; });
