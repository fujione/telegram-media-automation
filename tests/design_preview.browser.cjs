/* Tests only the isolated design prototype, never the application API. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],requests=[];
    page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>requests.push(request.url()));
    await page.goto('http://127.0.0.1:8084/');
    const settle=()=>page.evaluate(()=>Promise.all(document.getAnimations().map(animation=>animation.finished.catch(()=>{}))));
    await settle();
    assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).colorScheme),'dark');
    assert.equal(await page.evaluate(()=>parseFloat(getComputedStyle(document.body).fontSize)>=16),true,'body text must not rely on tiny fonts');
    const contrast=await page.evaluate(()=>{const rgb=css=>css.match(/[\d.]+/g).slice(0,3).map(Number),lum=values=>values.map(value=>{const c=value/255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4;}).reduce((total,c,index)=>total+c*[.2126,.7152,.0722][index],0),ratio=(a,b)=>(Math.max(a,b)+.05)/(Math.min(a,b)+.05);const surface=lum(rgb(getComputedStyle(document.querySelector('.resource-row:not(.selected)')).backgroundColor));return [ratio(lum(rgb(getComputedStyle(document.querySelector('.open-resource')).color)),surface),ratio(lum(rgb(getComputedStyle(document.querySelector('.source')).color)),surface)];});
    assert.equal(contrast.every(value=>value>=4.5),true,'primary and secondary resource text contrast');
    const output=path.resolve(__dirname,'../dist/webui-design-preview');fs.mkdirSync(output,{recursive:true});
    assert.equal(await page.locator('.resource-row').count(),10);
    assert.equal(await page.locator('#selection-info').textContent(),'已选 2 个资源');
    assert.equal(await page.locator('.selection-bar').evaluate(node=>node.getBoundingClientRect().bottom<=innerHeight),true,'batch action remains visible on desktop');
    await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
    await page.locator('#next').click();assert.equal(await page.locator('.resource-row').count(),8);
    await page.locator('#previous').click();
    await page.locator('[data-quality="1080"]').click();assert.equal(await page.locator('.resource-row').count(),6);
    await page.locator('[data-quality="all"]').click();
    await page.locator('#filter-button').click();await page.locator('#episode-filter').fill('22');await page.locator('#episode-filter').dispatchEvent('change');assert.equal(await page.locator('.resource-row').count(),3);
    await page.locator('#clear-filters').click();await page.locator('#filter-button').click();
    await page.locator('.open-resource').first().click();await page.locator('#detail').waitFor({state:'visible'});
    assert.match(await page.locator('#detail-body').textContent(),/示例频道 A/);await page.keyboard.press('Escape');
    await page.locator('nav a[href="#subscriptions"]').click();await page.locator('#subscriptions').waitFor({state:'visible'});
    await page.locator('[data-subscription="0"]').click();assert.match(await page.locator('#subscription-list').textContent(),/已暂停/);
    await page.locator('#new-subscription').click();await page.locator('#edit-name').fill('新示例');await page.locator('#edit-extra').fill('https://t.me/example');await page.locator('#edit-form button[type="submit"]').click();assert.match(await page.locator('#subscription-list').textContent(),/新示例/);
    await page.locator('nav a[href="#resources"]').click();assert.equal(await page.locator('#query').inputValue(),'示例剧集');assert.equal(await page.locator('#selection-info').textContent(),'已选 2 个资源');
    await page.locator('#batch-download').click();await page.locator('#detail-actions').getByRole('button',{name:'加入示例队列',exact:true}).click();assert.equal(await page.locator('#queue-count').textContent(),'4');assert.equal(await page.locator('#batch-download').isDisabled(),true);
    await page.locator('.pause-job').first().click();assert.match(await page.locator('#queue-list').textContent(),/已暂停/);
    await page.locator('#queue-toggle').click();assert.equal(await page.locator('#queue').isVisible(),false);assert.equal(await page.locator('#queue-toggle').getAttribute('aria-expanded'),'false');await page.locator('#queue-toggle').click();
    await page.locator('[data-state="complete"]').click();assert.match(await page.locator('#queue-list').textContent(),/已入库/);await page.locator('[data-state="active"]').click();
    await page.locator('#query').fill('不存在的名字');await page.locator('#search-form').getByRole('button',{name:'搜索',exact:true}).click();assert.equal(await page.locator('.resource-row').count(),0);assert.match(await page.locator('#resource-list').textContent(),/没有匹配/);await page.locator('#query').fill('示例剧集');await page.locator('#search-form').getByRole('button',{name:'搜索',exact:true}).click();
    for(const width of [320,375,768,1024,1440,1920,2560]){
      await page.setViewportSize({width,height:1000});
      for(const view of ['resources','subscriptions','channels']){
        await page.goto(`http://127.0.0.1:8084/#${view}`);await page.locator(`#${view}`).waitFor({state:'visible'});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow in ${view} at ${width}px`);
        const collisions=await page.locator('.resource-row').evaluateAll(rows=>rows.filter(row=>row.getBoundingClientRect().width>0).some(row=>{const boxes=[...row.children].filter(node=>getComputedStyle(node).display!=='none').map(node=>node.getBoundingClientRect());return boxes.some((a,i)=>boxes.slice(i+1).some(b=>a.left<b.right-1&&a.right>b.left+1&&a.top<b.bottom-1&&a.bottom>b.top+1));}));
        assert.equal(collisions,false,`overlap at ${width}px`);
      }
    }
    await page.setViewportSize({width:2560,height:1240});await page.goto('http://127.0.0.1:8084/#resources');await page.reload();await settle();await page.screenshot({path:path.join(output,'wide.png'),fullPage:true});
    await page.setViewportSize({width:375,height:900});await page.reload();await settle();assert.equal(await page.locator('#query').evaluate(node=>node.getBoundingClientRect().width>200),true,'mobile search input remains readable');await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
    await page.evaluate(()=>{for(let index=0;index<8;index++)document.querySelector('#queue-toggle').click();});await settle();assert.equal(await page.locator('#queue').isVisible(),true,'rapid toggles retain final state');
    await page.emulateMedia({reducedMotion:'reduce'});await page.locator('#queue-toggle').click();assert.equal(await page.locator('#queue').isVisible(),false);
    await page.locator('#settings').click();await page.locator('#detail').waitFor({state:'visible'});await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(()=>document.getAnimations().length),0,'reduced motion disables motion');
    assert.deepEqual(errors,[]);assert.equal(requests.every(url=>url.startsWith('http://127.0.0.1:8084/')),true);
    console.log('Design checks passed: paging, filtering, selection, confirmation, queue controls, details, management, route state, empty state, 7 widths, no overlapping rows, reduced motion, no external/API requests.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
