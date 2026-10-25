/* Isolated visual prototype. No network requests, credentials, or persistent writes. */
const $ = selector => document.querySelector(selector);
const esc = value => String(value).replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const icon = name => `<svg aria-hidden="true"><use href="#${name}"/></svg>`;
const motionQuery=matchMedia('(prefers-reduced-motion: reduce)'),runningMotion=new WeakMap();
function enter(node,distance=8){runningMotion.get(node)?.cancel();if(motionQuery.matches)return;const animation=node.animate([{opacity:.45,transform:`translateY(${distance}px)`},{opacity:1,transform:'translateY(0)'}],{duration:260,easing:'cubic-bezier(.22,1,.36,1)'});runningMotion.set(node,animation);}
function syncNavigation(){const active=$('nav a.active');if(!active)return;$('.nav-marker').style.width=`${active.offsetWidth}px`;$('.nav-marker').style.transform=`translateX(${active.offsetLeft}px)`;}
motionQuery.addEventListener('change',()=>{for(const node of document.querySelectorAll('.view,dialog,#queue,.resource-workspace'))node.getAnimations().forEach(animation=>animation.cancel());});
const variants = [{channel:'A',resolution:2160,codec:'HEVC',size:2.84},{channel:'B',resolution:2160,codec:'H.264',size:3.72},{channel:'C',resolution:1080,codec:'HEVC',size:1.36}];
const resources = Array.from({length:24}, (_, index) => {
  const variant = variants[index % 3],episode = 22 + Math.floor((index % 18) / 3),title = index < 18 ? '示例剧集' : '示例电影';
  return {...variant,id:index,title,episode,file:`${title}.S01E${episode}.${variant.resolution}p.${variant.codec}.mp4`};
});
let selected = new Set([0,3]),quality = 'all',pageIndex = 0,queueState = 'active',queueVisible = true,editKind = 'subscription';
let filtered = [],toastTimer;
const jobs = [
  {id:'initial-1',title:'示例剧集',episode:20,channel:'A',state:'downloading',progress:68,size:'1.93 / 2.84 GB'},
  {id:'initial-2',title:'示例剧集',episode:21,channel:'B',state:'queued',progress:0,size:'3.72 GB'},
  {id:'initial-3',title:'示例电影',episode:1,channel:'C',state:'imported',progress:100,size:'1.36 GB'},
];
const subscriptions = [{name:'示例剧集',episode:22,channel:'示例频道 A',enabled:true},{name:'示例订阅',episode:6,channel:'示例频道 B',enabled:true}];
const channels = ['A','B','C'].map(key=>({name:`示例频道 ${key}`,identifier:`https://t.me/example_${key.toLowerCase()}`,enabled:true}));
function notify(message){clearTimeout(toastTimer);$('#toast').textContent=message;$('#toast').classList.add('visible');toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),3500);}
function facts(items){return `<dl class="facts">${items.map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`;}
function showDetail(label,title,items,actions=[]){$('#detail-label').textContent=label;$('#detail-title').textContent=title;$('#detail-body').innerHTML=facts(items);$('#detail-actions').replaceChildren(...actions);$('#detail').showModal();enter($('#detail'),12);}
function action(label,callback,primary=false){const node=document.createElement('button');node.type='button';node.className=`button ${primary?'primary':'secondary'}`;node.textContent=label;node.addEventListener('click',callback);return node;}
function resourceDetails(resource){showDetail('资源详情',`${resource.title} · S01E${resource.episode}`,[['原始文件名',resource.file],['来源频道',`示例频道 ${resource.channel}`],['分辨率',`${resource.resolution}p`],['编码',resource.codec],['文件大小',`${resource.size.toFixed(2)} GB`],['来源记录','频道与消息来源在正式任务中保存']], [action('选择此资源',()=>{selected.add(resource.id);$('#detail').close();renderResources();})]);}
function updateSelection(){const count=selected.size;$('#selection-info').textContent=count?`已选 ${count} 个资源`:'选择资源后可批量下载';$('#batch-download').disabled=count===0;$('#clear-selection').disabled=count===0;}
function renderResources(){
  const query=$('#query').value.trim().toLowerCase(),channel=$('#search-channel').value,episode=Number($('#episode-filter').value),maxSize=Number($('#size-filter').value);
  const match=resource=>!query||resource.title.toLowerCase().includes(query)||resource.file.toLowerCase().includes(query);
  filtered=resources.filter(resource=>match(resource)&&(quality==='all'||resource.resolution===Number(quality))&&(channel==='all'||resource.channel===channel)&&(!episode||resource.episode===episode)&&(!maxSize||resource.size<=maxSize));
  filtered.sort($('#sort').value==='size'?(a,b)=>a.size-b.size:(a,b)=>a.episode-b.episode||b.resolution-a.resolution);
  const pageCount=Math.max(1,Math.ceil(filtered.length/10));pageIndex=Math.min(pageIndex,pageCount-1);
  const page=filtered.slice(pageIndex*10,pageIndex*10+10);
  $('#search-title').textContent=$('#query').value.trim()||'全部资源';$('#result-count').textContent=`${filtered.length} 个资源`;
  $('#resource-list').innerHTML=page.length?page.map(resource=>`<article class="resource-row ${selected.has(resource.id)?'selected':''}" data-id="${resource.id}"><div class="resource-top"><span class="episode-mark" aria-hidden="true"><small>EP</small><b>${resource.episode}</b></span><div class="resource-name"><button type="button" class="open-resource" aria-label="${esc(resource.title)} S01E${resource.episode} 资源详情">${esc(resource.title)}</button><small>S01E${resource.episode} · 第 ${resource.episode} 集</small></div><label class="selection-target"><input type="checkbox" ${selected.has(resource.id)?'checked':''} aria-label="选择 ${esc(resource.title)} 第${resource.episode}集 ${resource.resolution}p 示例频道${resource.channel}"></label></div><div class="resource-details"><div class="quality">${resource.resolution}p<small>${esc(resource.codec)}</small></div><div class="size">${resource.size.toFixed(2)} GB</div></div><div class="resource-bottom"><div class="source"><span aria-hidden="true">#</span>示例频道 ${resource.channel}</div><button class="row-download" aria-label="下载 ${esc(resource.title)} 第${resource.episode}集 示例频道${resource.channel}">${icon('arrow')}下载</button></div></article>`).join(''):'<div class="empty">没有匹配资源，试试“示例剧集”或清除筛选。</div>';
  $('#page-info').textContent=filtered.length?`${pageIndex*10+1}–${pageIndex*10+page.length} / ${filtered.length}`:'0 个资源';$('#page-number').textContent=`${pageIndex+1} / ${pageCount}`;$('#previous').disabled=pageIndex===0;$('#next').disabled=pageIndex+1>=pageCount;
  for(const row of document.querySelectorAll('.resource-row')){
    const resource=resources.find(item=>item.id===Number(row.dataset.id));
    row.querySelector('input').addEventListener('change',event=>{if(event.target.checked)selected.add(resource.id);else selected.delete(resource.id);row.classList.toggle('selected',event.target.checked);updateSelection();});
    row.querySelector('.open-resource').addEventListener('click',()=>resourceDetails(resource));
    row.querySelector('.row-download').addEventListener('click',()=>confirmDownload([resource]));
  }
  updateSelection();
}
function confirmDownload(items){
  showDetail('下载确认',items.length===1?`${items[0].title} · S01E${items[0].episode}`:`下载 ${items.length} 个所选资源`,[['资源',items.map(item=>`${item.title} S01E${item.episode} / 频道 ${item.channel}`).join('\n')],['预计大小',`${items.reduce((total,item)=>total+item.size,0).toFixed(2)} GB`],['本次操作','只加入示例队列，不下载文件、不写入媒体库']], [action('取消',()=>$('#detail').close()),action('加入示例队列',()=>{
    let added=0;for(const item of items){if(jobs.some(job=>job.resourceId===item.id))continue;jobs.push({id:`resource-${item.id}`,resourceId:item.id,title:item.title,episode:item.episode,channel:item.channel,state:'queued',progress:0,size:`${item.size.toFixed(2)} GB`});selected.delete(item.id);added++;}
    $('#detail').close();renderResources();queueState='active';setQueueVisible(true);renderQueue();notify(added?`${added} 个资源已加入示例队列`:'资源已在示例队列中');
  },true)]);
}
function jobDetails(job){showDetail('任务详情',`${job.title} · S01E${job.episode}`,[['来源',`示例频道 ${job.channel}`],['状态',stateLabel(job.state)],['入库计划','正式任务在下载前确认媒体信息'],['示例路径',`/media/tv/${job.title}/Season 01/${job.title} - S01E${job.episode}.mp4`],['预览说明','状态与路径都是设计示例，并非实际服务数据']], [action('关闭',()=>$('#detail').close())]);}
function stateLabel(state){return {downloading:'下载中',queued:'排队等待',paused:'已暂停',imported:'已入库'}[state];}
function renderQueue(){
  const active=jobs.filter(job=>job.state!=='imported'),complete=jobs.filter(job=>job.state==='imported');$('#queue-total').textContent=jobs.length;$('#active-count').textContent=active.length;$('#complete-count').textContent=complete.length;$('#queue-count').textContent=active.length;
  for(const tab of document.querySelectorAll('[data-state]'))tab.setAttribute('aria-pressed',String(tab.dataset.state===queueState));
  const visible=queueState==='active'?active:complete;
  $('#queue-list').innerHTML=visible.length?visible.map(job=>`<article class="job" data-id="${esc(job.id)}"><div class="job-heading"><button class="job-name">${esc(job.title)} · E${job.episode}</button>${job.state==='downloading'||job.state==='paused'?`<button class="icon-button pause-job" aria-label="${job.state==='paused'?'继续':'暂停'} ${esc(job.title)} 第${job.episode}集">${icon(job.state==='paused'?'chevron':'pause')}</button>`:icon(job.state==='imported'?'check':'arrow')}</div><span class="job-sub">Season 01 <span aria-hidden="true">/</span> 示例频道 ${esc(job.channel)}</span><div class="job-status"><span>${stateLabel(job.state)}</span>${job.state==='downloading'||job.state==='paused'?`<strong>${job.progress}%</strong>`:''}</div>${job.state==='downloading'||job.state==='paused'?`<div class="progress" role="progressbar" aria-label="${esc(job.title)}下载进度" aria-valuenow="${job.progress}" aria-valuemin="0" aria-valuemax="100"><i style="width:${job.progress}%"></i></div><div class="job-caption"><span>${esc(job.size)}</span><span>示例进度</span></div>`:`<span class="job-sub">${esc(job.size)}</span>`}<button class="text-button job-link">查看任务详情 ${icon('chevron')}</button></article>`).join(''):'<div class="empty">当前没有任务</div>';
  for(const row of document.querySelectorAll('.job')){
    const job=jobs.find(item=>item.id===row.dataset.id);for(const control of row.querySelectorAll('.job-name,.job-link'))control.addEventListener('click',()=>jobDetails(job));
    row.querySelector('.pause-job')?.addEventListener('click',()=>{job.state=job.state==='paused'?'downloading':'paused';renderQueue();notify(`${job.title} E${job.episode} ${stateLabel(job.state)}（示例）`);});
  }
}
function setQueueVisible(visible){const node=$('.resource-workspace');node.getAnimations().forEach(animation=>animation.cancel());const before=node.getBoundingClientRect();queueVisible=visible;$('#queue').hidden=!visible;$('#workbench').classList.toggle('queue-hidden',!visible);$('#queue-toggle').setAttribute('aria-expanded',String(visible));$('#queue-toggle-label').textContent=visible?'收起队列':'下载队列';const after=node.getBoundingClientRect();if(!motionQuery.matches&&before.width!==after.width){node.style.transformOrigin='top left';node.animate([{transform:`scaleX(${before.width/after.width})`},{transform:'scaleX(1)'}],{duration:300,easing:'cubic-bezier(.22,1,.36,1)'});}if(visible)enter($('#queue'));}
function renderManagement(){
  $('#subscription-list').innerHTML=subscriptions.map((item,index)=>`<article class="management-row"><div><h3>${esc(item.name)}</h3><p>从 S01E${String(item.episode).padStart(2,'0')} 追更 · ${esc(item.channel)}</p></div><div class="buttons"><span class="status ${item.enabled?'':'paused'}">${item.enabled?'追更中':'已暂停'}</span><button class="button secondary" data-subscription="${index}">${item.enabled?'暂停':'恢复'}</button></div></article>`).join('');
  $('#channel-list').innerHTML=channels.map((item,index)=>`<article class="management-row"><div><h3>${esc(item.name)}</h3><p>${esc(item.identifier)}</p></div><div class="buttons"><span class="status ${item.enabled?'':'paused'}">${item.enabled?'已启用':'已停用'}</span><button class="button secondary" data-channel="${index}">${item.enabled?'停用':'启用'}</button></div></article>`).join('');
  for(const button of document.querySelectorAll('[data-subscription]'))button.addEventListener('click',()=>{const item=subscriptions[Number(button.dataset.subscription)];item.enabled=!item.enabled;renderManagement();notify('已更新示例订阅');});
  for(const button of document.querySelectorAll('[data-channel]'))button.addEventListener('click',()=>{const item=channels[Number(button.dataset.channel)];item.enabled=!item.enabled;renderManagement();notify('已更新示例频道');});
}
function navigate(){const requested=location.hash.slice(1),view=['resources','subscriptions','channels'].includes(requested)?requested:'resources';for(const section of document.querySelectorAll('.view')){section.getAnimations().forEach(animation=>animation.cancel());section.hidden=section.id!==view;}for(const link of document.querySelectorAll('nav a')){const active=link.hash===`#${view}`;link.classList.toggle('active',active);if(active)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');}$('#view-title').textContent={resources:'今晚，想看什么？',subscriptions:'好剧，接着看。',channels:'你的资源来源。'}[view];$('#view-description').textContent={resources:'找到好资源，接下来交给下载队列。',subscriptions:'追更进度、来源和开关，一处掌握。',channels:'让搜索和追更，来自你信任的频道。'}[view];syncNavigation();enter(document.getElementById(view));}
$('#search-form').addEventListener('submit',event=>{event.preventDefault();pageIndex=0;renderResources();$('#resource-list').scrollTop=0;notify(`示例搜索完成，找到 ${filtered.length} 个资源`);});
for(const button of document.querySelectorAll('[data-quality]'))button.addEventListener('click',()=>{quality=button.dataset.quality;pageIndex=0;for(const peer of document.querySelectorAll('[data-quality]'))peer.setAttribute('aria-pressed',String(peer===button));renderResources();});
for(const control of document.querySelectorAll('#search-channel,#sort,#episode-filter,#size-filter'))control.addEventListener('change',()=>{pageIndex=0;renderResources();});
$('#filter-button').addEventListener('click',()=>{const open=$('#advanced').hidden;$('#advanced').hidden=!open;$('#filter-button').setAttribute('aria-expanded',String(open));});
$('#clear-filters').addEventListener('click',()=>{$('#episode-filter').value='';$('#size-filter').value='';pageIndex=0;renderResources();});
$('#previous').addEventListener('click',()=>{pageIndex--;renderResources();$('#resource-list').scrollTop=0;});$('#next').addEventListener('click',()=>{pageIndex++;renderResources();$('#resource-list').scrollTop=0;});
$('#clear-selection').addEventListener('click',()=>{selected.clear();renderResources();});$('#batch-download').addEventListener('click',()=>confirmDownload(resources.filter(resource=>selected.has(resource.id))));
$('#queue-toggle').addEventListener('click',()=>setQueueVisible(!queueVisible));for(const button of document.querySelectorAll('[data-state]'))button.addEventListener('click',()=>{queueState=button.dataset.state;renderQueue();});
for(const button of document.querySelectorAll('[data-close]'))button.addEventListener('click',()=>document.getElementById(button.dataset.close).close());
$('#settings').addEventListener('click',()=>showDetail('连接与设置','服务连接',[['控制台账号','示例管理员'],['Telegram','已连接（示例）'],['后台服务','设计稿未连接后台'],['入口设计','正常时只显示顶栏状态，连接操作在设置中提供']]));
function openEdit(kind){editKind=kind;$('#edit-title').textContent=kind==='subscription'?'新建订阅':'添加频道';$('#name-label').firstChild.nodeValue=kind==='subscription'?'剧名':'频道名称';$('#extra-label').firstChild.nodeValue='频道链接';$('#edit-name').value='';$('#edit-extra').value='';$('#edit').showModal();enter($('#edit'),12);}
$('#new-subscription').addEventListener('click',()=>openEdit('subscription'));$('#new-channel').addEventListener('click',()=>openEdit('channel'));
$('#edit-form').addEventListener('submit',event=>{event.preventDefault();const name=$('#edit-name').value.trim(),identifier=$('#edit-extra').value.trim();if(!name||!identifier)return;if(editKind==='subscription')subscriptions.push({name,episode:1,channel:identifier,enabled:true});else channels.push({name,identifier,enabled:true});$('#edit').close();renderManagement();notify('已添加示例，刷新后恢复初始数据');});
window.addEventListener('hashchange',navigate);window.addEventListener('resize',syncNavigation);renderResources();renderQueue();renderManagement();navigate();
