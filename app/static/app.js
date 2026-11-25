const $=s=>document.querySelector(s);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names={jobs:'岗位清单',favorites:'收藏',companies:'公司概览',companyJobs:'公司岗位',applied:'已投递',ignored:'已忽略岗位',ended:'已结束岗位',blocked:'受阻清单',unfit:'已查无匹配',preferences:'偏好总结'};
const statuses={listed:'列表可见',unknown:'状态待核',not_seen:'当前未见',expired:'已截止',closed:'已关闭'};
const scoreLabels=()=>data.score_labels;
const state={page:'jobs',jobStatus:'new',freshness:'all',selected:'',tab:'fit',search:'',score:'all',city:'all',direction:'all',posting:'all',company:'',tracking:'open',review:'all',sort:'deadline',deadline:'all'};
let data=null,preferenceData=null,evidenceCache={},scoreJob='',toastTimer;
const DAY_MS=86400000;
let timeZone='UTC';
let workspaceDateFormatter=dateFormatter(timeZone);
function dateFormatter(zone){
 return new Intl.DateTimeFormat('en-US',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'});
}
const trackingIndexes=new WeakMap();

function workspaceToday(now=new Date()){
 const parts=workspaceDateFormatter.formatToParts(now);
 const value=type=>parts.find(p=>p.type===type).value;
 return `${value('year')}-${value('month')}-${value('day')}`;
}
function calendarDay(value){
 if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value))return null;
 const day=Date.parse(value+'T00:00:00Z');
 return Number.isFinite(day)&&new Date(day).toISOString().slice(0,10)===value?day:null;
}
function registrationDay(value){
 if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)||calendarDay(value.slice(0,10))===null)return null;
 const instant=new Date(value);return Number.isFinite(instant.getTime())?workspaceToday(instant):null;
}
function matchesFreshness(item,mode='all',today=workspaceToday()){return mode!=='today'||registrationDay(item.created_at)===today}
function viewFreshness(job,page,mode='all',today=workspaceToday(),tracking=[],trackingStatus='open'){
 return page==='blocked'?mode!=='today'||technicalTracking(job,tracking,trackingStatus).some(t=>matchesFreshness(t,mode,today)):matchesFreshness(job,mode,today);
}
function isEnded(job,today=workspaceToday()){
 const end=calendarDay(job.deadline),day=calendarDay(today);
 return ['expired','closed'].includes(job.posting_status)||(end!==null&&day!==null&&end<day);
}
function isIgnored(job){return job.score_override===0||job.user_status==='ignored'}
function isUnmatched(job,today=workspaceToday()){return job.ai_score===0&&!isEnded(job,today)&&!isIgnored(job)&&!(job.score_override>0)}
function trackingByJob(tracking){
 let cached=trackingIndexes.get(tracking);
 if(!cached||cached.length!==tracking.length){
  const items=new Map();
  for(const item of tracking){if(!items.has(item.job_id))items.set(item.job_id,[]);items.get(item.job_id).push(item)}
  cached={length:tracking.length,items};trackingIndexes.set(tracking,cached);
 }
 return cached.items;
}
function jobTracking(job,tracking=[],status='open'){
 return (trackingByJob(tracking).get(job.id)||[]).filter(t=>t.kind!=='no_match'&&(status==='all'||t.status===status));
}
function technicalTracking(job,tracking=[],status='open'){return jobTracking(job,tracking,status).filter(t=>t.kind==='blocked')}
function mismatchReason(job){return (job.score_reason||'').trim().replace(/^0\s*分\s*[：:，,]\s*/,'')||'不匹配依据尚未补充，需重新核对。'}
function blockingReasons(job,today=workspaceToday(),tracking=[]){
 const reasons=[];
 if(isEnded(job,today))reasons.push(job.posting_status==='closed'?'已关闭':'已截止');
 if(job.jd_quality!=='full')reasons.push('JD不完整');
 if(!['social','campus','internship','mixed'].includes(job.recruitment_type))reasons.push('招聘类型待核');
 if(['unknown','not_seen'].includes(job.posting_status))reasons.push('在招状态待核');
 if(job.effective_score==null)reasons.push('匹配度待评估');
 if(technicalTracking(job,tracking).length)reasons.push('技术采集受阻');
 else if(jobTracking(job,tracking).length)reasons.push('有待核查事项');
 return reasons;
}
function belongsToView(job,page='jobs',jobStatus='new',today=workspaceToday(),tracking=[],trackingStatus='open',freshness='all'){
 if(!viewFreshness(job,page,freshness,today,tracking,trackingStatus))return false;
 if(page==='companyJobs')return true;
 if(page==='favorites')return job.is_favorite===true;
 if(page==='ended')return isEnded(job,today);
 if(page==='ignored')return isIgnored(job,today);
 if(page==='unfit')return isUnmatched(job,today);
 if(page==='blocked')return !isEnded(job,today)&&technicalTracking(job,tracking,trackingStatus).length>0;
 return !isIgnored(job,today)&&!isEnded(job,today)&&(job.effective_score===null||job.effective_score>0)&&job.user_status===jobStatus;
}
function deadlineInfo(job,today=workspaceToday()){
 const end=calendarDay(job.deadline),start=calendarDay(today);
 const days=end===null||start===null?null:(end-start)/DAY_MS;
 const closed=['expired','closed'].includes(job.posting_status),past=days!==null&&days<0;
 const eligible=job.user_status==='new'&&job.effective_score!==0&&!isIgnored(job,today)&&!closed&&!past;
 const bucket=closed||past?3:job.effective_score===0?2:days!==null?0:1;
 const result={days,bucket,soon:eligible&&days!==null&&days<=7,tone:'muted',text:'截止未公开',hint:''};
 if(days===null)return result;
 result.hint='按工作区时区计算；具体截止时刻请查看招聘来源。';
 if(past)return {...result,text:`截止日已过 · ${job.deadline}`};
 if(closed)return {...result,text:`${job.deadline}截止 · 来源已${job.posting_status==='closed'?'关闭':'截止'}`};
 if(!eligible)return {...result,text:`${job.deadline}截止`};
 return {...result,tone:days<=3?'urgent':days<=7?'soon':days<=14?'approaching':'neutral',text:days===0?`今天截止 · ${job.deadline}`:`剩${days}天 · ${job.deadline}截止`};
}
function isSoonDeadline(job,today=workspaceToday()){return deadlineInfo(job,today).soon}
function matchingOrder(a,b){
 return (b.effective_score??-1)-(a.effective_score??-1)||Number(b.posting_status==='listed')-Number(a.posting_status==='listed')||(b.checked_on||'').localeCompare(a.checked_on||'');
}
function compareJobs(a,b,mode='deadline',today=workspaceToday()){
 if(mode==='match')return matchingOrder(a,b);
 const first=deadlineInfo(a,today),second=deadlineInfo(b,today);
 return first.bucket-second.bucket||(first.bucket===0?first.days-second.days:0)||matchingOrder(a,b);
}
function compareJobsForView(a,b,page='jobs',jobStatus='new',mode='deadline',today=workspaceToday()){
 if(page==='jobs'&&['new','applied'].includes(jobStatus)){
  const favoriteOrder=Number(Boolean(a.is_favorite))-Number(Boolean(b.is_favorite));
  if(favoriteOrder)return jobStatus==='new'?favoriteOrder:-favoriteOrder;
 }
 return compareJobs(a,b,page==='jobs'?mode:'match',today);
}
function deadlineBadge(job,today=workspaceToday()){
 const info=deadlineInfo(job,today);
 return `<span class="deadline-badge ${info.tone}"${info.hint?` title="${esc(info.hint)}"`:''}>${esc(info.text)}</span>`;
}
let displayedDay=workspaceToday(),deadlineTimer;
function refreshDeadlineDay(now=new Date()){
 const today=workspaceToday(now);
 if(today!==displayedDay){displayedDay=today;if(data)renderPreservingJobView()}
}
function scheduleDeadlineDay(){
 clearTimeout(deadlineTimer);
 deadlineTimer=setTimeout(()=>{refreshDeadlineDay();scheduleDeadlineDay()},60000-Date.now()%60000+100);
}

async function api(url,options={}){
 const response=await fetch(url,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});
 const result=await response.json();if(!response.ok){const error=new Error(result.error||'请求失败');error.status=response.status;throw error}return result;
}
function toast(message){$('#toast').textContent=message;$('#toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),3000)}
function fail(error){$('#error').hidden=false;$('#error').textContent=error.message||String(error)}
function nearbyJob(selected,previousIds,currentIds,visibleIds=[]){
 const available=new Set(currentIds);
 if(available.has(selected))return selected;
 if(visibleIds.length&&!visibleIds.includes(selected)){
  const visible=visibleIds.find(id=>available.has(id));if(visible)return visible;
 }
 const index=previousIds.indexOf(selected);
 if(index>=0){
  for(const id of previousIds.slice(index+1))if(available.has(id))return id;
  for(let i=index-1;i>=0;i--)if(available.has(previousIds[i]))return previousIds[i];
 }
 return currentIds[0]||'';
}
function captureJobView(){
 const list=$('.list-scroll');if(!list)return null;
 const bounds=list.getBoundingClientRect(),rows=[...list.querySelectorAll('.job-row')];
 const visible=rows.map(row=>{const rect=row.getBoundingClientRect();return {id:row.dataset.job,top:rect.top-bounds.top,bottom:rect.bottom-bounds.top}})
  .filter(row=>row.bottom>0&&row.top<bounds.height);
 return {page:state.page,selected:state.selected,tab:state.tab,ids:rows.map(row=>row.dataset.job),visible,
  listTop:list.scrollTop,detailTop:$('.detail')?.scrollTop||0,windowX:window.scrollX,windowY:window.scrollY};
}
function preserveJobSelection(view){
 if(view&&view.page===state.page){
  const visible=[...view.visible.filter(row=>row.top>=0),...view.visible.filter(row=>row.top<0)].map(row=>row.id);
  state.selected=nearbyJob(view.selected,view.ids,filteredJobs().map(job=>job.id),visible);
 }
}
function restoreJobView(view){
 if(!view||view.page!==state.page)return;
 const list=$('.list-scroll');if(!list)return;
 const rows=[...list.querySelectorAll('.job-row')],byId=new Map(rows.map(row=>[row.dataset.job,row]));
 const oldIndex=view.ids.indexOf(view.selected),newIndex=rows.findIndex(row=>row.dataset.job===view.selected);
 const moved=state.selected===view.selected&&oldIndex>=0&&newIndex>=0&&oldIndex!==newIndex;
 const anchor=view.visible.find(row=>byId.has(row.id)&&(!moved||row.id!==view.selected));
 if(anchor){const row=byId.get(anchor.id);list.scrollTop+=row.getBoundingClientRect().top-list.getBoundingClientRect().top-anchor.top}
 else list.scrollTop=view.listTop;
 if(state.selected===view.selected&&state.tab===view.tab&&$('.detail'))$('.detail').scrollTop=view.detailTop;
 window.scrollTo(view.windowX,view.windowY);
}
function renderPreservingJobView(view=captureJobView()){
 preserveJobSelection(view);render();restoreJobView(view);
}
function focusSelectedJob(){document.querySelector('.job-row[aria-pressed="true"]')?.focus({preventScroll:true})}
function focusAfterScore(id){
 const scoreButton=state.selected===id?[...document.querySelectorAll('[data-score]')].find(button=>button.dataset.score===id):null;
 if(scoreButton)scoreButton.focus({preventScroll:true});else focusSelectedJob();
}
async function refresh(){
 const next=await api('/api/ui/bootstrap'),view=captureJobView();
 data=next.data;preferenceData=next.preferences;evidenceCache={};
 if(timeZone!==data.timezone){timeZone=data.timezone;workspaceDateFormatter=dateFormatter(timeZone);displayedDay=workspaceToday()}
 renderPreservingJobView(view);
}
function tag(text,type=''){return `<span class="tag ${type}">${esc(text)}</span>`}
function statusTag(j){return tag(isEnded(j)?(j.posting_status==='closed'?'已关闭':'已截止'):statuses[j.posting_status]||j.posting_status,!isEnded(j)&&j.posting_status==='listed'?'good':'warm')}
function heading(title,sub){return `<div class="page-heading"><div><h1>${esc(title)}</h1><p>${esc(sub)}</p></div></div>`}
function count(status,freshness='all',today=workspaceToday()){return data.jobs.filter(j=>belongsToView(j,status==='ignored'?'ignored':'jobs',status,today,data.tracking,'open',freshness)).length}
function freshnessTabs(){return `<div class="freshness-bar"><div class="tabs freshness-tabs" role="group" aria-label="新增日期"><button data-freshness="all" aria-pressed="${state.freshness==='all'}">全部</button><button data-freshness="today" aria-pressed="${state.freshness==='today'}">今日新增</button></div><p class="freshness-note">按工作区时区的首次入库日期日期${state.page==='blocked'?' · 技术核查记录':state.page==='preferences'?' · 总结版本与个人记录':''}</p></div>`}
function blockedCompanyItems(){const kind=state.page==='ended'?'expired':'blocked';return data.tracking.filter(t=>!t.job_id&&t.kind===kind&&
 matchesFreshness(t,state.freshness)&&
 (state.page==='ended'||state.tracking==='all'||t.status===state.tracking)&&(state.review==='all'||['pending','working'].includes(t.request_status))&&
 (!state.company||t.company_id===state.company)&&(!state.search||(t.company+t.reason+t.scope+t.next_action).toLowerCase().includes(state.search.toLowerCase())))
 .sort((a,b)=>Number(b.kind==='expired')-Number(a.kind==='expired')||(b.checked_on||'').localeCompare(a.checked_on||''));}
function filteredJobs({applyDeadline=true,today=workspaceToday()}={}){return data.jobs.filter(j=>
 belongsToView(j,state.page,state.jobStatus,today,data.tracking,state.tracking,state.freshness)&&
 (state.page!=='blocked'||state.review==='all'||technicalTracking(j,data.tracking,state.tracking).some(t=>['pending','working'].includes(t.request_status)))&&
 (!state.search||(j.company+j.title+j.description+j.requirements+blockingReasons(j,today,data.tracking).join(' ')).toLowerCase().includes(state.search.toLowerCase()))&&
 (state.score==='all'||(state.score==='pending'?j.effective_score===null:j.effective_score===Number(state.score)))&&(state.city==='all'||j.city===state.city)&&
 (state.direction==='all'||j.direction===state.direction)&&(state.posting==='all'||j.posting_status===state.posting)&&
 (!state.company||j.company_id===state.company)&&(!applyDeadline||state.page!=='jobs'||state.deadline!=='soon'||isSoonDeadline(j,today))).sort((a,b)=>compareJobsForView(a,b,state.page,state.jobStatus,state.sort,today));}
function deadlineControls(today=workspaceToday()){
 const soon=filteredJobs({applyDeadline:false,today}).filter(j=>isSoonDeadline(j,today)).length;
 return `<button type="button" id="deadline-toggle" aria-pressed="${state.deadline==='soon'}" aria-describedby="deadline-help">只看7天内截止 <strong>${soon}</strong></button><span id="deadline-help">按工作区时区 · 0 分及已截止岗位不计入提醒</span>`;
}
function updateJobArea(today=workspaceToday()){
 const view=captureJobView();preserveJobSelection(view);
 const tools=$('#deadline-tools');if(tools)tools.innerHTML=deadlineControls(today);
 $('#job-area').innerHTML=jobArea(today);restoreJobView(view);loadEvidence().catch(fail);
}
function options(values,selected){return values.map(([v,t])=>`<option value="${esc(v)}" ${String(v)===selected?'selected':''}>${esc(t)}</option>`).join('')}
function jobsPage(){const title=names[state.page],sub={jobs:'查看还未结束的岗位，按匹配度、地域和截止日期筛选。',favorites:'你收藏的重点关注岗位，投递、忽略或招聘结束后仍保留在这里。',companyJobs:'查看所选公司的全部岗位，包含信息待核、已结束和个人标记。',ignored:'你主动忽略或手动评为 0 分的岗位。',unfit:'AI评分为0的具体岗位；红色展示不匹配依据，人工正分覆盖后回到对应清单。',ended:'已截止或已关闭的岗位，保留匹配度评分和投递记录，方便追踪后续批次。',blocked:'仅收录认证、反爬、网络、动态页面或附件解析等技术采集障碍；已结束岗位单独归档。'};
 return heading(title,sub[state.page])+
 `<div class="metrics"><div class="metric"><strong>${count('new')}</strong>未投递</div><div class="metric"><strong>${count('applied')}</strong>已投递</div><div class="metric"><strong>${count('ignored')}</strong>已忽略</div><div class="metric note">${data.counts.overrides} 个岗位采用我的评分</div></div>
 ${state.page==='jobs'?`<div class="freshness-bar"><div class="tabs job-status-tabs" role="group" aria-label="投递状态"><button data-job-status="new" data-freshness="all" aria-pressed="${state.jobStatus==='new'&&state.freshness==='all'}">未投递 <span>${count('new')}</span></button><button data-job-status="new" data-freshness="today" aria-pressed="${state.jobStatus==='new'&&state.freshness==='today'}">未投递-今日新增 <span>${count('new','today')}</span></button><button data-job-status="applied" data-freshness="all" aria-pressed="${state.jobStatus==='applied'}">已投递 <span>${count('applied')}</span></button></div><p class="freshness-note">今日新增按工作区时区的首次入库日期</p></div>`:freshnessTabs()}
 <div class="filters"><input type="search" id="search" aria-label="搜索公司或岗位" placeholder="搜索公司、岗位或职责…" value="${esc(state.search)}">
 <select id="score-filter" aria-label="匹配度评分">${options([['all','全部评分'],...[3,2,1,0].filter(i=>i||state.page!=='jobs').map(i=>[String(i),i+' 分 · '+scoreLabels()[i]]),['pending','待评估']],state.score)}</select>
 <select id="city-filter" aria-label="工作地域">${options([['all','全部地域'],...[...new Set(data.jobs.map(j=>j.city).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-CN')).map(x=>[x,x])],state.city)}</select>
 <select id="direction-filter" aria-label="岗位方向">${options([['all','全部方向'],...[...new Set(data.jobs.map(j=>j.direction))].map(x=>[x,x])],state.direction)}</select>
 <select id="posting-filter" aria-label="招聘状态">${options([['all','全部招聘状态'],...Object.entries(statuses)],state.posting)}</select>${state.page==='jobs'?`<select id="sort-filter" aria-label="岗位排序">${options([['deadline','截止日期优先'],['match','匹配度优先']],state.sort)}</select>`:''}</div>
 ${state.page==='jobs'&&state.jobStatus==='new'?`<div id="deadline-tools" class="deadline-tools">${deadlineControls()}</div>`:''}
 ${state.page==='blocked'?`<div class="filters"><select id="tracking-filter" aria-label="追踪状态">${options([['open','当前受阻'],['resolved','已解决记录'],['all','全部记录']],state.tracking)}</select><select id="review-filter" aria-label="复查请求">${options([['all','全部项目'],['pending','只看待复查']],state.review)}</select><span class="muted">${filteredJobs().length} 个岗位 · ${blockedCompanyItems().length} 条入口／批次记录</span></div>`:''}
 ${state.company?`<div class="chip-clear">${esc(data.companies.find(c=>c.id===state.company)?.name)} <button data-clear-company>清除公司筛选 ×</button></div>`:''}
 <div id="job-area">${jobArea()}</div>${['blocked','ended'].includes(state.page)?companyTrackingHTML():''}`;
}
function blockerReasonHTML(t){return t.kind==='blocked'&&t.status==='open'?`<p class="blocker-reason"><strong>未解决原因</strong>${esc(t.reason||'具体技术原因待补充；当前仍未取得完整采集结果。')}<small>核查 ${esc(t.checked_on||'日期待核')}</small></p>`:''}
function trackingReasonHTML(t){return blockerReasonHTML(t)||`<p class="tracking-reason"><strong>${t.status==='resolved'?'已解决 · ':''}${esc(t.reason)}</strong></p>`}
function jobReasonHTML(j){
 if(state.page==='blocked')return technicalTracking(j,data.tracking,state.tracking).map(trackingReasonHTML).join('');
 if(state.page==='unfit')return `<p class="mismatch-reason"><strong>不匹配原因</strong>${esc(mismatchReason(j))}</p>`;
 const reason=j.score_override!==null?(j.score_note||'采用我的评分。'):j.score_reason;
 return reason?`<p class="reason">${esc(reason)}</p>`:'';
}
function jobArea(today=workspaceToday()){const list=filteredJobs({today});if(!list.some(j=>j.id===state.selected))state.selected=list[0]?.id||'';
 const deadlineMode=state.page==='jobs'&&state.sort==='deadline',soonOnly=state.page==='jobs'&&state.deadline==='soon';
 const orderNote=state.page==='jobs'?`${state.jobStatus==='applied'?'收藏排在最前':'收藏排在最后'} · 组内按${deadlineMode?'截止日期':'匹配度'}`:state.page==='blocked'?'技术采集障碍 · 按匹配度':state.page==='ended'?'历史岗位 · 按匹配度':'按当前评分从高到低';
 return `<div class="work-grid${state.page==='jobs'?' deadline-view':''}"><section class="job-list" aria-label="岗位列表"><div class="list-head"><span role="status" aria-live="polite">${list.length} 个岗位</span><span>${orderNote}</span></div><div class="list-scroll">${list.length?list.map(j=>`<button class="job-row" data-job="${j.id}" aria-pressed="${state.selected===j.id}"><span class="job-company"><span>${esc(j.company)}</span>${statusTag(j)}</span><h2>${esc(j.title)}</h2><div class="job-deadline">${deadlineBadge(j,today)}</div>${blockingReasons(j,today,data.tracking).length?`<div class="verification-tags">${blockingReasons(j,today,data.tracking).map(r=>tag(r,'warm')).join('')}</div>`:''}${jobReasonHTML(j)}<span class="job-meta"><span>${[j.city,j.experience].filter(Boolean).map(esc).join(' · ')||'地区待核'}</span><span class="score-badge">${j.effective_score??'—'} / 3 分</span>${j.score_override!==null?tag('我的评分','mine'):tag('AI评分')}${tag(j.direction)}${tag(j.user_status==='applied'?'已投递':isIgnored(j)?'已忽略':'未投递')}${j.is_favorite?tag('已收藏','mine'):''}</span></button>`).join(''):
 `<div class="empty"><strong>${soonOnly?'暂无符合筛选的7天内截止岗位':state.freshness==='today'?'暂无符合筛选的今日新增岗位':state.page==='favorites'&&!data.jobs.some(j=>j.is_favorite)?'还没有收藏岗位':state.score==='3'?'暂无符合筛选的3分岗位':'暂无符合筛选的岗位'}</strong><p>${soonOnly?'取消“只看7天内截止”或调整其他筛选条件。':state.freshness==='today'?`按工作区时区的首次入库日期；已有岗位更新不计为新增。可切回“${state.page==='jobs'?'未投递':'全部'}”查看历史记录。`:state.page==='favorites'&&!data.jobs.some(j=>j.is_favorite)?'在岗位详情中点击“收藏”，之后可在这里查看。':!data.jobs.length?'让 Codex 初始化工作雷达并核查第一批岗位。':'试试调整筛选条件。'}</p></div>`}</div></section><section class="detail" aria-label="所选岗位详情">${jobDetail(data.jobs.find(j=>j.id===state.selected),today)}</section></div>`;
}
function jobDetail(j,today=workspaceToday()){if(!j)return '<div class="empty">选择岗位，查看是否适合你</div>';
 let body='';const mine=j.score_override!==null;
 if(state.tab==='fit')body=`<div class="match-box"><div class="match-top"><span class="match-score">${j.effective_score??'—'} <small>/ 3 分 · ${mine?'我的优先级':esc(scoreLabels()[j.effective_score])||'待评估'}</small></span><button class="text-button" data-score="${j.id}">调整评分</button></div><p>${esc(mine?(j.score_note||'你已自主调整本岗位评分。'):j.score_reason)}</p>${mine?`<p class="muted">我的评分 ${j.score_override} 分 · AI 原评分 ${j.ai_score??'—'} 分</p><p class="muted">AI依据：${esc(j.score_reason)}</p>`:j.ai_score==null?'<p class="muted">匹配度待评估。</p>':'<p class="muted">当前采用 AI 评分，可按自己的判断调整。</p>'}</div>
 <h3>评分引用</h3><p class="body-text">${esc(j.known||"待评估")}</p><h3>还需要核对</h3><p class="body-text">${esc(j.unknowns||"待评估")}</p>
 <div class="callout">${j.posting_status==='listed'?(j.hc_status==='confirmed'?'公开列表可见，招聘名额有来源证据。':'公开列表可见，招聘名额待核。'):esc(j.status_note||'当前在招证据不足或原批次已截止，先核对有效投递信息。')} 分数结合方向与个人限制；个人改分不改变在招状态。${j.preference_review_pending?' 等待按当前配置评估。':''}</div>`;
 if(state.tab==='jd'){const real=['full','partial'].includes(j.jd_quality);body=`${tag(j.jd_quality==='full'?'已取得职责与要求原文':j.jd_quality==='partial'?'原文部分已取得':'JD原文待补充',real?'good':'warm')}<h3>岗位职责</h3><p class="body-text">${real&&j.description?esc(j.description):'尚未取得对应岗位职责原文，等待根据原始URL补充。'}</p><h3>任职要求</h3><p class="body-text">${real&&j.requirements?esc(j.requirements):'尚未取得对应任职要求原文，不以研判摘要替代。'}</p>${j.jd_source_url?`<h3>JD原始URL</h3>${urlLink(j.jd_source_url)}<p class="muted">原文读取 ${esc(j.jd_checked_on||'日期待核')}</p>`:''}<h3>薪资与批次</h3><p class="body-text">${esc(j.salary)}\n${esc(j.batch)}${j.deadline?' · 截止 '+esc(j.deadline):''}</p>${j.status_note?`<div class="callout">${esc(j.status_note)}</div>`:''}`;}
 if(state.tab==='evidence')body=`<p class="muted">核查日期 ${esc(j.checked_on)} · 岗位版本 ${j.revision} · HC ${esc(j.hc_status==='confirmed'?'来源明确确认':'待核')}</p><div id="evidence-list">${evidenceHTML(j.id)}</div>`;
 return `<div class="detail-top"><span>${esc(j.company)}</span>${statusTag(j)}</div><h2>${esc(j.title)}</h2><p class="subtitle">${[j.city,j.department,j.degree,j.experience].filter(Boolean).map(esc).join(' · ')||'任职信息待核'}</p><div class="detail-deadline">${deadlineBadge(j,today)}${calendarDay(j.deadline)!==null?'<span class="deadline-note">按工作区时区计算，具体截止时刻见来源。</span>':''}</div>
 ${verificationHTML(j,today)}<div class="tabs" aria-label="岗位详情视图">${[['fit','适合我吗'],['jd','职位介绍'],['evidence','岗位来源']].map(([v,t])=>`<button data-tab="${v}" aria-pressed="${state.tab===v}">${t}</button>`).join('')}</div>${body}
 <div class="actions">${j.user_status!=='ignored'?`<button data-state="ignored" data-id="${j.id}">忽略</button>`:''}<button class="favorite-button" data-favorite="${esc(j.id)}" aria-pressed="${j.is_favorite}"><span aria-hidden="true">${j.is_favorite?'★':'☆'}</span> 收藏</button>${j.user_status==='new'?`<button class="primary" data-state="applied" data-id="${j.id}">标记已投递</button>`:j.user_status==='applied'?`<button data-state="new" data-id="${j.id}">撤回已投标记</button>`:`<button class="primary" data-state="new" data-id="${j.id}">恢复待看</button>`}
 ${/^https?:\/\//.test(j.source_url||'')?`<a href="${esc(j.source_url)}" target="_blank" rel="noopener noreferrer">查看招聘来源 ↗</a>`:''}</div>${j.score_override===0?'<p class="callout">人工0分已归入已忽略岗位；调整为正分或恢复AI评分后自动重新归类，原投递状态保留。</p>':''}<p class="personal-status">我的状态：${{new:'未投递',applied:'已投递',ignored:'已忽略'}[j.user_status]}${j.is_favorite?' · 已收藏':''}${j.score_override===0?' · 人工0分归入忽略':''} · 数据核查 ${esc(j.checked_on)}</p>`;
}
function reviewButton(t){const active=['pending','working'].includes(t.request_status);return `<button class="button ${active?'':'primary'}" data-review="${esc(t.id)}" ${t.request_status==='working'?'disabled':''} aria-pressed="${active}">${t.request_status==='working'?'复查中':active?'待复查 · 取消':'加入复查'}</button>`}
function verificationHTML(j,today=workspaceToday()){
 const reasons=blockingReasons(j,today,data.tracking),items=state.page==='blocked'?technicalTracking(j,data.tracking,state.tracking):jobTracking(j,data.tracking);
 if(!reasons.length&&!items.length)return '';
 return `<aside class="verification-box" aria-label="岗位核查事项"><div class="verification-tags">${reasons.map(r=>tag(r,'warm')).join('')}</div>${isEnded(j,today)?'<p>已结束招聘，评分保留岗位匹配度，供追踪后续批次。</p>':''}${items.length?`<details><summary>${items.length} 条关联核查记录</summary><p class="muted">保留当时的核查结论；当前匹配评分以本页为准。</p>${items.map(t=>`<article class="verification-record">${trackingReasonHTML(t)}${t.blocker_url?urlLink(t.blocker_url):''}<p>${esc(t.scope)}</p><p>下一步 · ${esc(t.next_action)}</p><small>核查 ${esc(t.checked_on||'日期待核')}</small>${reviewButton(t)}</article>`).join('')}</details>`:''}</aside>`;
}
function urlLink(url){return /^https?:\/\//.test(url||'')?`<a class="original-url" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)} ↗</a>`:`<span class="original-url">${esc(url)}</span>`}
function sourceHTML(records){return records.map(e=>`<article class="evidence"><h3>${e.kind==='jd'?'岗位描述原文来源':/^https?:\/\/[^/?#]+\/?(?:[?#].*)?$/.test(e.url)?'官网入口（具体岗位URL待补）':'原始招聘来源'}</h3>${urlLink(e.url)}<p>${e.checked_on?'读取日期 '+esc(e.checked_on):'当前有效性待核'}</p><p>${esc(e.note||'')}</p></article>`).join('')||'<div class="empty">原始URL待补充</div>'}
function evidenceHTML(id){const records=evidenceCache[id];return records?sourceHTML(records):'<div class="muted">正在读取原始URL…</div>'}
async function loadEvidence(){if(state.tab!=='evidence'||!state.selected)return;const id=state.selected;if(!evidenceCache[id])evidenceCache[id]=await api('/api/jobs/'+encodeURIComponent(id)+'/sources');if(state.selected===id&&state.tab==='evidence'&&$('#evidence-list'))$('#evidence-list').innerHTML=evidenceHTML(id)}
function companyPage(){let companies=data.companies.filter(c=>matchesFreshness(c,state.freshness)&&(!state.search||(c.name+c.source_name+c.summary+c.region).toLowerCase().includes(state.search.toLowerCase())));
 const parents=new Map(data.companies.map(c=>[c.id,c])),jobCounts=new Map(),checks=new Map();
 for(const j of data.jobs)jobCounts.set(j.company_id,(jobCounts.get(j.company_id)||0)+1);
 for(const t of data.tracking){if(!checks.has(t.company_id))checks.set(t.company_id,[]);checks.get(t.company_id).push(t)}
 return heading('了解公司，再看机会','按用工主体查看已有证据；母公司和子公司分别保留。')+freshnessTabs()+`<div class="filters"><input id="search" type="search" aria-label="搜索公司" placeholder="搜索公司、集团或地域…" value="${esc(state.search)}"></div><div class="track-summary">${companies.length} 个公司主体 · 包含已确认的集团与子公司关系</div><div class="company-grid">${companies.map(c=>{const jobs=jobCounts.get(c.id)||0,parent=parents.get(c.parent_id);return `<section class="company-card"><small>${esc(c.region||'地域待核')}${parent?' · '+esc(parent.name)+'旗下':''}</small><h2>${esc(c.name)}</h2><p class="company-note">${esc(c.status_note||'保留集团／主体关系；具体招聘情况参见对应子公司证据。')}</p><small>核查 ${esc(c.checked_on||'见关联主体')} · ${jobs} 个岗位记录</small><div class="company-footer">${jobs?`<button class="button" data-company="${esc(c.id)}">查看岗位 →</button>`:tag('尚无已核实目标岗位')}${c.website?`<a href="${esc(c.website)}" target="_blank" rel="noopener noreferrer" class="source-link">官网 ↗</a>`:''}${c.ledger_number?`<button class="text-button" data-company-evidence="${esc(c.id)}">核查依据</button>`:''}</div>${companyChecks(c.id,checks)}</section>`}).join('')||'<div class="empty">暂无符合筛选的公司主体</div>'}</div>`;
}
function companyChecks(id,index){const items=index?index.get(id)||[]:data.tracking.filter(t=>t.company_id===id);if(!items.length)return '';return `<details class="company-checks"><summary>核查记录 · ${items.length} 条</summary>${items.map(t=>`<article><div>${tag(t.status==='resolved'?'已解决':{blocked:'技术采集受阻',uncertain:'信息／状态待核',expired:'批次已结束',no_match:'历史范围结论'}[t.kind],t.status==='open'&&t.kind==='blocked'?'warm':'')}</div>${trackingReasonHTML(t)}${t.blocker_url?urlLink(t.blocker_url):''}<p>${esc(t.scope)}</p><p>下一步 · ${esc(t.next_action)}</p><small>核查 ${esc(t.checked_on||'日期待核')}</small>${reviewButton(t)}</article>`).join('')}</details>`}
function companyTrackingHTML(){const list=blockedCompanyItems(),ended=state.page==='ended';
 return `<section class="company-tracking"><div class="section-heading"><h2>${ended?'已结束批次':'入口技术受阻'}</h2><span>${list.length} 条记录 · 暂无具体岗位的不评分</span></div><p class="muted">${ended?'仅保留已确认结束的批次，不推断整家公司停止招聘。':'保留具体采集障碍；招聘类型、HC或信息缺项单独标记待核。'}加入复查不代表已设定时任务。</p><div class="tracking-list">${list.length?list.map(t=>`<section class="track-row"><div class="track-top"><h2>${esc(t.company)}</h2>${tag(t.status==='resolved'?'已解决':ended?'批次已结束':'技术采集受阻',t.status==='resolved'?'good':'warm')}</div>${trackingReasonHTML(t)}<p>${esc(t.scope)}</p><div class="track-next">下一步 · ${esc(t.next_action)}</div><div class="track-bottom"><span>核查 ${esc(t.checked_on||'待核')}<br>建议复查 ${esc(t.review_on||'新批次或新证据出现时')}</span><div>${t.blocker_url?urlLink(t.blocker_url):`<button class="text-button" data-company-evidence="${esc(t.company_id)}">原始URL</button>`} ${reviewButton(t)}</div></div></section>`).join(''):'<div class="empty">暂无符合条件的入口／批次记录</div>'}</div></section>`;
}
function localTime(value){return value?new Intl.DateTimeFormat('zh-CN',{timeZone,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)):'尚未生成'}
function preferenceView(p,mode='all',today=workspaceToday()){
 const records=p.records.filter(r=>matchesFreshness(r,mode,today));
 return {...p,records,latest:p.latest&&matchesFreshness(p.latest,mode,today)?p.latest:null,
  history:p.history.filter(h=>matchesFreshness(h,mode,today)),
  applied:records.filter(r=>r.status==='applied').length,ignored:records.filter(r=>r.status==='ignored'||r.score_override===0).length};
}
function preferencePage(){const p=preferenceView(preferenceData,state.freshness),s=p.latest,changes=s?s.changes.filter(c=>c.before!==c.after):[],notes=p.records.filter(r=>r.score_note.trim()),todayOnly=state.freshness==='today';
 const recordMap=new Map((s?.records||[]).map(r=>[r.job_id,r]));
 const score=value=>value===null?'—':value+'分';
 return heading('从我的选择，校准下一次推荐','记录保留原话，归纳说明适用范围，每次重评分都留下版本。')+freshnessTabs()+
 `<div class="preference-hero"><div><span class="eyebrow">我的偏好 · ${s?'第 '+p.version_count+' 版':todayOnly?'今天尚无新增总结':'等待首次归纳'}</span><h2>更接近我想做的工作</h2><p>${esc(s?.summary||(todayOnly?'今天尚未生成新的偏好总结，可切回“全部”查看当前偏好。':'你在岗位中写下的调整理由，会在这里形成有证据的偏好总结。'))}</p><div class="preference-meta">${tag(p.pending?'待更新 · 显示上次结果':'已覆盖当前记录',p.pending?'warm':'good')}<span>${todayOnly?'今日':'最近'}总结 ${esc(localTime(s?.created_at))}</span><button class="text-button" data-refresh-preferences>刷新记录 ↻</button></div></div><aside><span class="eyebrow">更新偏好</span><p>可以在 Codex 中调整评分规则，再让它重评现有岗位。</p><small>投递、忽略与手动改分继续保留。</small></aside></div>
 <div class="preference-stats"><div><strong>${notes.length}</strong><span>条当前调整理由</span></div><div><strong>${s?.rules.length||0}</strong><span>条有来源的归纳</span></div><div><strong>${s?.reviewed||0}<small> / ${data.jobs.length}</small></strong><span>个岗位已重新评估</span></div><div><strong>${changes.length}</strong><span>个 AI 分数发生变化</span></div><div><strong>${p.applied}<small> / ${p.ignored}</small></strong><span>已投递 / 我的忽略</span></div></div>
 <div class="preference-columns"><section aria-label="当前偏好规则"><div class="section-heading"><h2>当前偏好</h2><span>有边界，才有用</span></div><div class="preference-rules">${s?s.rules.map((r,i)=>`<article class="preference-rule"><span class="rule-index">${String(i+1).padStart(2,'0')}</span><div><h3>${esc(r.title)}</h3><p>${esc(r.interpretation)}</p><div class="rule-scope">适用边界 · ${esc(r.scope)}</div><details><summary>查看你的原话 · ${r.evidence.length} 条</summary>${r.evidence.map(e=>{const record=recordMap.get(e.job_id);return `<blockquote>“${esc(e.quote)}”<footer>${esc(record?.company)} · ${esc(record?.title)}</footer></blockquote>`}).join('')}</details></div></article>`).join(''):'<div class="empty">尚无已归纳的偏好；原始记录仍保存在下方。</div>'}</div></section>
 <section aria-label="本轮评分变化"><div class="section-heading"><h2>本轮评分变化</h2><span>AI 重评 · 我的评分不变</span></div><div class="preference-changes">${changes.length?changes.map(c=>`<article class="preference-change"><div><span>${esc(c.company)}</span><strong class="score-transition">${score(c.before)} <i>→</i> ${score(c.after)}</strong></div><button class="change-job" data-open-preference-job="${esc(c.job_id)}">${esc(c.title)} ↗</button><p>${esc(c.reason)}</p>${c.personal_score!==null?tag('我的 '+c.personal_score+' 分继续优先','mine'):tag(c.after===null?'仍待评估':'当前采用 AI '+c.after+' 分')}</article>`).join(''):'<div class="empty">本轮没有 AI 分数变化。</div>'}</div></section></div>
 <section class="preference-records" aria-label="我的原始记录"><div class="section-heading"><h2>我的原始记录</h2><span>采用当前保留的理由；撤回的旧理由不再作为当前偏好</span></div><table><thead><tr><th>公司 / 岗位</th><th>我的状态</th><th>AI / 我的评分</th><th>调整理由原文</th><th>记录时间</th></tr></thead><tbody>${p.records.filter(r=>r.score_note.trim()||r.status!=='new'||r.score_override!==null).map(r=>{const j=data.jobs.find(j=>j.id===r.job_id);return `<tr><td><small>${esc(r.company)}</small><button class="text-button" data-open-preference-job="${esc(r.job_id)}">${esc(r.title)}</button></td><td>${{new:'待看',applied:'已投递',ignored:'已忽略'}[r.status]}</td><td>${score(j?.ai_score??null)} / ${score(r.score_override)}</td><td>${esc(r.score_note||'未填写理由；状态本身不推断通用偏好')}</td><td>首次 ${esc(localTime(r.created_at))}${r.updated_at!==r.created_at?`<br><small>更新 ${esc(localTime(r.updated_at))}</small>`:''}</td></tr>`}).join('')||`<tr><td colspan="5">${todayOnly?'今天尚无新增个人记录':'暂无个人记录'}</td></tr>`}</tbody></table></section>
 <details class="preference-history"><summary>${todayOnly?'今日':''}总结历史 · ${todayOnly?p.history.length:p.version_count} 个版本（展示最近20版）</summary>${p.history.map(h=>`<details class="history-version"><summary>${esc(localTime(h.created_at))} · 评估 ${h.reviewed} 岗 · 改分 ${h.changed} 岗</summary><p>${esc(h.summary)}</p>${h.changes.map(c=>`<p>${esc(c.company)} · ${esc(c.title)}：${score(c.before)} → ${score(c.after)}<br>${esc(c.reason)}</p>`).join('')}<code>${esc(h.id)}</code></details>`).join('')}</details>`;
}
function render(){if(!data)return;document.querySelectorAll('nav [data-page]').forEach(b=>{if(b.dataset.page===(state.page==='companyJobs'?'companies':state.page))b.setAttribute('aria-current','page');else b.removeAttribute('aria-current')});$('#breadcrumb').textContent=names[state.page];
 $('#profile-summary').textContent=data.profile.goal||'尚未设置';
 const today=workspaceToday();
 $('#count-new').textContent=count('new','all',today)+count('applied','all',today);$('#count-favorites').textContent=data.jobs.filter(j=>j.is_favorite).length;$('#count-ignored').textContent=count('ignored','all',today);$('#count-companies').textContent=data.companies.length;$('#count-ended').textContent=data.jobs.filter(j=>isEnded(j,today)).length;$('#count-blocked').textContent=data.jobs.filter(j=>belongsToView(j,'blocked','new',today,data.tracking)).length+data.tracking.filter(t=>!t.job_id&&t.kind==='blocked'&&t.status==='open').length;$('#count-unfit').textContent=data.jobs.filter(j=>isUnmatched(j,today)).length;
 const checked=data.jobs.reduce((d,j)=>j.checked_on>d?j.checked_on:d,'');
 $('#data-date').textContent=checked?'证据核查至 '+checked+' · '+data.jobs.length+' 个岗位':'尚未加入岗位';
 $('#count-preferences').textContent=preferenceData.pending?'待总结':preferenceData.note_count;
 $('#content').innerHTML=state.page==='preferences'?preferencePage():state.page==='companies'?companyPage():jobsPage();
 loadEvidence().catch(fail);
}
function resetFilters(){Object.assign(state,{jobStatus:'new',freshness:'all',search:'',score:'all',city:'all',direction:'all',posting:'all',company:'',tracking:'open',review:'all',deadline:'all'})}
function openJob(id){const job=data.jobs.find(j=>j.id===id);if(!job)return;resetFilters();state.page=isIgnored(job)?'ignored':isEnded(job)?'ended':isUnmatched(job)?'unfit':'jobs';if(state.page==='companyJobs')state.company=job.company_id;state.jobStatus=job.user_status==='applied'?'applied':'new';state.selected=job.id;state.tab='fit';render()}
function applyUiMutation(result,kind){
 if(!result.ui_version||result.ui_refresh_required||!result[kind])return false;
 const item=result[kind],view=captureJobView();
 if(kind==='user_state'){
  const job=data.jobs.find(j=>j.id===item.job_id);if(!job)return false;
  Object.assign(job,{user_status:item.status,score_override:item.score_override,score_note:item.score_note,
   user_revision:item.revision,effective_score:item.score_override??job.ai_score});
  preferenceData.records=[item,...preferenceData.records.filter(r=>r.job_id!==item.job_id)]
   .sort((a,b)=>(b.updated_at||'').localeCompare(a.updated_at||'')||a.job_id.localeCompare(b.job_id));
  preferenceData.note_count=preferenceData.records.filter(r=>r.score_note.trim()).length;
  preferenceData.applied=preferenceData.records.filter(r=>r.status==='applied').length;
  preferenceData.ignored=preferenceData.records.filter(r=>r.status==='ignored'||r.score_override===0).length;
  preferenceData.explicit_ignored=preferenceData.records.filter(r=>r.status==='ignored').length;
  preferenceData.pending=true;
  data.counts.overrides=data.jobs.filter(j=>j.score_override!==null).length;
 }else if(kind==='favorite'){
  const job=data.jobs.find(j=>j.id===item.job_id);if(!job)return false;
  job.is_favorite=item.is_favorite;job.favorite_at=item.favorite_at;
 }else if(kind==='tracking'){
  const tracking=data.tracking.find(t=>t.id===item.id);if(!tracking)return false;
  Object.assign(tracking,item);
  data.counts.pending_reviews=data.tracking.filter(t=>['pending','working'].includes(t.request_status)).length;
 }else return false;
 data.ui_version=result.ui_version;renderPreservingJobView(view);return true;
}
async function saveUiMutation(url,options,kind){
 let result;
 try{result=await api(url,{...options,headers:{...options.headers,'X-UI-Version':data.ui_version||''}})}
 catch(error){
  if(error.status===409){try{await refresh()}catch(_){throw new Error('记录已在其他页面更新，请手动刷新后重试。')}
   throw new Error('记录已在其他页面更新，已刷新，请核对后重试。')}
  throw error;
 }
 if(!applyUiMutation(result,kind))await refresh();
 $('#error').hidden=true;$('#error').textContent='';
}
async function saveUser(id,changes){const j=data.jobs.find(j=>j.id===id);await saveUiMutation('/api/me/jobs/'+id+'/state',{method:'PATCH',body:JSON.stringify({...changes,expected_revision:j.user_revision})},'user_state')}
function showScore(id){scoreJob=id;const j=data.jobs.find(j=>j.id===id);$('#score-job').textContent=j.company+' · '+j.title;$('#ai-explanation').textContent='AI评分 '+(j.ai_score??'待评估')+' / 3：'+(j.score_reason||'待评估');
 document.querySelectorAll('input[name=score]').forEach(r=>{
  const value=Number(r.value);
  r.checked=value===j.effective_score;
  r.closest('label').querySelector('span').textContent=scoreLabels()[value];
 });$('#score-note').value=j.score_note||'';$('#score-error').hidden=true;$('#score-error').textContent='';$('#restore-ai').disabled=j.score_override===null;$('#score-dialog').showModal();}
async function showSources(kind,id){const records=await api('/api/'+kind+'/'+encodeURIComponent(id)+'/sources');$('#source-title').textContent='原始来源URL';$('#source-meta').textContent='查看原始招聘入口、岗位详情或招聘公告';$('#source-content').innerHTML=sourceHTML(records);$('#source-dialog').showModal()}
if(typeof module!=='undefined'&&module.exports)module.exports={workspaceToday,calendarDay,registrationDay,matchesFreshness,preferenceView,deadlineInfo,isSoonDeadline,compareJobs,compareJobsForView,nearbyJob,matchingOrder,deadlineBadge,isEnded,isIgnored,isUnmatched,blockingReasons,belongsToView,technicalTracking,mismatchReason,blockerReasonHTML,trackingReasonHTML};
if(typeof document!=='undefined'){
document.addEventListener('click',async event=>{const b=event.target.closest('button');if(!b)return;try{
 if(b.id==='deadline-toggle'){state.deadline=state.deadline==='soon'?'all':'soon';updateJobArea();$('#deadline-toggle').focus({preventScroll:true});return}
 if(b.dataset.page){state.page=b.dataset.page==='applied'?'jobs':b.dataset.page;resetFilters();if(b.dataset.page==='applied')state.jobStatus='applied';render();return}
 if(b.dataset.jobStatus){state.jobStatus=b.dataset.jobStatus;state.freshness=b.dataset.freshness||'all';state.deadline='all';state.selected='';render();return}
 if(b.dataset.freshness){state.freshness=b.dataset.freshness;state.selected='';render();return}
 if(b.hasAttribute('data-refresh-preferences')){b.disabled=true;await refresh();toast('已读取最新个人记录与总结');return}
 if(b.dataset.openPreferenceJob){openJob(b.dataset.openPreferenceJob);return}
 if(b.dataset.job){
  state.selected=b.dataset.job;
  const previous=$('.job-row[aria-pressed="true"]');if(previous)previous.setAttribute('aria-pressed','false');
  b.setAttribute('aria-pressed','true');
  const detail=$('.detail');detail.innerHTML=jobDetail(data.jobs.find(j=>j.id===state.selected));detail.scrollTop=0;
  await loadEvidence();return;
 }
 if(b.dataset.tab){state.tab=b.dataset.tab;$('.detail').innerHTML=jobDetail(data.jobs.find(j=>j.id===state.selected));await loadEvidence();return}
 if(b.dataset.score){showScore(b.dataset.score);return}
 if(b.dataset.close){$('#'+b.dataset.close).close();return}
 if(b.dataset.favorite){const id=b.dataset.favorite,j=data.jobs.find(j=>j.id===id),favorite=!j.is_favorite;b.disabled=true;await saveUiMutation('/api/me/jobs/'+encodeURIComponent(id)+'/favorite',{method:'PATCH',body:JSON.stringify({favorite,expected_favorite:j.is_favorite})},'favorite');const target=[...document.querySelectorAll('[data-favorite]')].find(button=>button.dataset.favorite===id);(target||document.querySelector('.job-row[aria-pressed="true"]')||document.querySelector('[data-page="favorites"]')).focus({preventScroll:true});toast(favorite?'已加入收藏':'已取消收藏');return}
 if(b.dataset.state){const id=b.dataset.id,next=b.dataset.state;b.disabled=true;await saveUser(id,{status:next});const target=state.selected===id?[...document.querySelectorAll('[data-state]')].find(button=>button.dataset.id===id&&button.dataset.state===(next==='new'?'applied':'new')):null;if(target)target.focus({preventScroll:true});else focusSelectedJob();toast('个人状态已保存');return}
 if(b.id==='restore-ai'){b.disabled=true;await saveUser(scoreJob,{score_override:null,score_note:''});$('#score-dialog').close();focusAfterScore(scoreJob);toast('已恢复采用 AI 评分');return}
 if(b.dataset.company){resetFilters();state.company=b.dataset.company;state.page='companyJobs';render();return}
 if(b.hasAttribute('data-clear-company')){state.company='';render();return}
 if(b.dataset.jobSources){await showSources('jobs',b.dataset.jobSources);return}
 if(b.dataset.companyEvidence){await showSources('companies',b.dataset.companyEvidence);return}
 if(b.dataset.review){b.disabled=true;const t=data.tracking.find(t=>t.id===b.dataset.review);const cancel=t.request_status==='pending';await saveUiMutation('/api/me/review-requests',{method:'POST',body:JSON.stringify({tracking_id:t.id,cancel})},'tracking');const target=[...document.querySelectorAll('[data-review]')].find(button=>button.dataset.review===t.id&&!button.disabled);if(target)target.focus({preventScroll:true});else focusSelectedJob();toast(cancel?'已取消待复查请求':'已加入复查队列');return}
 }catch(error){b.disabled=false;fail(error);if($('#score-dialog').open){$('#score-error').textContent=error.message;$('#score-error').hidden=false}else toast(error.message)}
});
$('#score-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{const checked=$('input[name=score]:checked');if(!checked)return;await saveUser(scoreJob,{score_override:Number(checked.value),score_note:$('#score-note').value});$('#score-error').hidden=true;$('#score-dialog').close();focusAfterScore(scoreJob);toast('我的评分已保存，后续导入将保留')}catch(error){fail(error);$('#score-error').textContent=error.message;$('#score-error').hidden=false}finally{button.disabled=false}});
document.addEventListener('input',event=>{if(event.target.id==='search'){const pos=event.target.selectionStart;state.search=event.target.value;if(['jobs','favorites','ignored','unfit'].includes(state.page))updateJobArea();else{render();$('#search').focus();$('#search').setSelectionRange(pos,pos)}}});
document.addEventListener('change',event=>{const map={'score-filter':'score','city-filter':'city','direction-filter':'direction','posting-filter':'posting','tracking-filter':'tracking','review-filter':'review','sort-filter':'sort'};if(map[event.target.id]){state[map[event.target.id]]=event.target.value;if(['jobs','favorites','ignored','unfit'].includes(state.page))updateJobArea();else render()}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden){refreshDeadlineDay();scheduleDeadlineDay()}});
window.addEventListener('focus',()=>{refreshDeadlineDay();scheduleDeadlineDay()});
const initialPage=new URLSearchParams(location.search).get('page');if(names[initialPage])state.page=initialPage==='applied'?'jobs':initialPage;if(initialPage==='applied')state.jobStatus='applied';
refresh().catch(fail);
scheduleDeadlineDay();
}
