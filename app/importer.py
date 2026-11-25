"""Validate and reconcile one verified source batch inside a sync transaction."""

import policy
import store


def import_batch(db, batch):
    if not isinstance(batch, dict) or set(batch) - {"source", "companies", "jobs", "tracking"}:
        raise ValueError("每批需要来源及对应的企业、岗位或核查数组")
    for field in ("companies", "jobs", "tracking"):
        if not isinstance(batch.get(field, []), list):
            raise ValueError(f"{field} 需要数组")
    s = batch.get("source")
    if (not isinstance(s, dict) or set(s) != {"url", "content", "checked_on"}
            or not policy.is_web_url(s["url"])
            or not all(isinstance(s[k], str) and s[k].strip() for k in ("content", "checked_on"))):
        raise ValueError("source 需要原始 url、实际证据 content 和核查日期 checked_on")
    policy.validate_dates(s, ("checked_on",))
    sid = store.source(db, s["url"], s["content"], s["checked_on"])
    summary = {"source_url": s["url"], "companies": [], "jobs": [], "tracking": []}
    company_map={}
    for c in batch.get('companies',[]):
        fields={'id','name','source_name','parent_id','website','region','channel','summary','status_note','checked_on','next_action','industry','expected_revision','source_locator'}
        if not isinstance(c,dict) or set(c)-fields or not all(isinstance(c.get(k),str) and c[k] for k in ('id','name','source_locator')):
            raise ValueError('公司需要id/name/source_locator，只接受公司事实字段')
        exact=db.execute('SELECT c.* FROM companies c LEFT JOIN company_aliases a ON a.company_id=c.id WHERE c.id=? OR a.alias=? OR c.name=?',(c['id'],c['name'],c['name'])).fetchall()
        ids={x['id'] for x in exact}
        if len(ids)>1:raise store.Conflict('公司名称与ID存在歧义，请明确用工主体')
        old=exact[0] if exact else None
        if old and (type(c.get('expected_revision')) is not int or c['expected_revision'] != old['revision']):
            raise store.Conflict('公司版本已变更，请重新读取')
        if not old and c.get('expected_revision') is not None:
            raise store.Conflict('新增公司不能声明旧版本')
        if any(value is not None and not isinstance(value, str) for key, value in c.items()
               if key != 'expected_revision'):
            raise ValueError('公司事实字段须为文本或 null')
        if any(c.get(key) and not policy.is_web_url(c[key]) for key in ('website', 'channel')):
            raise ValueError('企业官网和招聘入口须为 HTTP(S) URL')
        cid=old['id'] if old else c['id'];company_map[c['id']]=cid
        item={**c,'id':cid,'checked_on':c.get('checked_on',s['checked_on'])}
        policy.validate_dates(item,('checked_on',))
        action=store.upsert_company(db,item)
        start,end,raw=policy.locate(s['content'],c['source_locator'])
        store.observation(db,'company_observations',cid,sid,start,end,raw,s['checked_on'])
        db.execute('INSERT OR IGNORE INTO company_aliases VALUES(?,?)',(c['name'],cid))
        summary['companies'].append({'id':cid,'action':action})
    for incoming in batch.get('jobs',[]):
        allowed_job=set(store.JOB_FIELDS)|{'expected_revision','source_locator','merge_reason'}
        if not isinstance(incoming,dict) or set(incoming)-allowed_job:
            raise ValueError('不支持的岗位字段；个人评分和标记不属于Agent导入字段')
        for key in ('identity_key','company_id','title','direction','posting_status','source_locator'):
            if not isinstance(incoming.get(key),str) or not incoming[key].strip():
                raise ValueError('岗位需要非空字段：'+key)
        if incoming['posting_status'] not in policy.POSTING_STATES:
            raise ValueError('无效posting_status')
        incoming={**incoming,'company_id':company_map.get(incoming['company_id'],incoming['company_id'])}
        if any(value is not None and not isinstance(value,str) for key,value in incoming.items() if key in store.JOB_FIELDS):
            raise ValueError('岗位事实字段应为字符串或null')
        company=db.execute('SELECT id FROM companies WHERE id=?',(incoming['company_id'],)).fetchone()
        if not company:
            raise ValueError('company_id未入库，请先使用现有公司主体')
        start, end, raw = policy.locate(s['content'], incoming['source_locator'])
        item = {**incoming, 'checked_on': s['checked_on']}
        if incoming.get('checked_on', s['checked_on']) != s['checked_on']:
            raise ValueError('岗位核查日期应与本批来源一致')
        policy.validate_job(item, s)
        if item['jd_quality'] != 'missing' and (
                item['jd_source_url'] != s['url'] or item['jd_checked_on'] != s['checked_on']):
            raise ValueError('JD 来源 URL 和日期应与本批证据一致')
        jid, rev, action = store.upsert_job(db, item, expected_revision=incoming.get('expected_revision'))
        store.observation(db, 'job_observations', jid, sid, start, end, raw, s['checked_on'])
        summary['jobs'].append({'id':jid,'revision':rev,'action':action})
    for t in batch.get('tracking',[]):
        allowed_track={'id','company_id','job_id','job_identity_key','kind','reason','scope','next_action','checked_on','review_on','status','source_locator','expected_revision',*policy.TRACKING_FIELDS}
        if not isinstance(t,dict) or set(t)-allowed_track:
            raise ValueError('不支持的追踪字段')
        if not all(isinstance(t.get(k),str) and t[k] for k in ('id','company_id','kind','reason','scope','next_action','source_locator')):
            raise ValueError('追踪需要id/company_id/kind/reason/scope/next_action/source_locator')
        t = {**t, 'company_id': company_map.get(t['company_id'], t['company_id'])}
        if t.get('job_identity_key'):
            if t.get('job_id'):
                raise ValueError('job_id 与 job_identity_key 只需填写一项')
            job = db.execute('SELECT id FROM jobs WHERE identity_key=?', (t.pop('job_identity_key'),)).fetchone()
            if not job:
                raise ValueError('核查项引用了未登记的岗位身份')
            t['job_id'] = job['id']
        if t['kind'] not in ('blocked','expired','uncertain','no_match') or t.get('status','open') not in ('open','resolved'):
            raise ValueError('无效追踪状态')
        previous=db.execute('SELECT revision,company_id,job_id FROM tracking_items WHERE id=?',(t['id'],)).fetchone()
        if previous and (type(t.get('expected_revision')) is not int or t['expected_revision']!=previous['revision']):
            raise store.Conflict('核查记录版本已变化，请重新prepare后沿用现有ID和revision')
        if not previous and t.get('expected_revision') is not None:
            raise store.Conflict('新增核查记录不能声明旧revision')
        if previous and (previous['company_id'] != t['company_id'] or
                         ('job_id' in t and previous['job_id'] != t['job_id'])):
            raise store.Conflict('不可把现有核查记录转移到其他企业或岗位')
        if previous and 'job_id' not in t:
            t['job_id'] = previous['job_id']
        if t.get('job_id'):
            job=db.execute('SELECT company_id FROM jobs WHERE id=?',(t['job_id'],)).fetchone()
            if not job or job['company_id']!=t['company_id']:
                raise store.Conflict('核查记录须关联同一用工主体的真实岗位；集团入口问题可单独记录，不混并子公司岗位')
        policy.validate_tracking(t, s)
        if t.get('blocker_url') and t['blocker_url'] != s['url']:
            raise ValueError('受阻入口应与本批证据指向同一 URL')
        if t.get('checked_on', s['checked_on']) != s['checked_on']:
            raise ValueError('核查日期应与本批来源一致')
        policy.validate_dates(t,('checked_on','review_on'))
        start,end,_=policy.locate(s['content'],t['source_locator'])
        action = store.upsert_tracking(db, {**t, 'source_id': sid, 'line_start': start,
                                           'line_end': end, 'checked_on': s['checked_on']})
        if t.get('job_id') and t.get('blocker_url') and not db.execute('SELECT 1 FROM job_original_sources WHERE job_id=? AND url=?',(t['job_id'],t['blocker_url'])).fetchone():
            store.original_source(db,t['job_id'],t['blocker_url'],'technical',t.get('checked_on',s['checked_on']),'技术采集障碍对应的原始招聘或附件URL')
        summary['tracking'].append({'id': t['id'], 'action': action})
    return summary
