import hashlib
import json
import os
import sqlite3
import re
from urllib.parse import urlparse, parse_qs
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent
WORKSPACE = Path(os.environ.get('JOBCLAW_HOME') or (APP.parent / '.jobclaw')).expanduser().resolve()
DEFAULT_DB = WORKSPACE / 'jobs.sqlite3'


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def digest(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()

@contextmanager
def connection(path=DEFAULT_DB):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=15000')
    try:
        with db:
            yield db
    finally:
        db.close()

def initialize(path=DEFAULT_DB):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connection(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript((APP / 'schema.sql').read_text())


class Conflict(ValueError):
    pass


def user_fingerprint(db):
    tables = ('user_job_state', 'user_job_favorites', 'user_state_history')
    return digest(packed({name: [dict(r) for r in db.execute(f'SELECT * FROM {name} ORDER BY rowid')]
                          for name in tables}))

def source(db, name, content, checked_on):
    sha = digest(content)
    sid = digest(name + ':' + sha)
    db.execute('INSERT OR IGNORE INTO source_documents VALUES(?,?,?,?,?,?)',
               (sid, name, sha, content, checked_on, now()))
    return sid

def upsert_company(db, item):
    allowed = ['name','source_name','parent_id','website','region','channel','summary','status_note','checked_on','next_action','industry']
    old = db.execute('SELECT * FROM companies WHERE id=?', (item['id'],)).fetchone()
    if not old:
        vals = {k:item.get(k) for k in allowed}
        db.execute('INSERT INTO companies(id,'+','.join(allowed)+',created_at) VALUES('+','.join('?' for _ in range(len(allowed)+2))+')',
                   [item['id']] + list(vals.values()) + [now()])
        return 'inserted'
    if item.get('checked_on') and old['checked_on'] and item['checked_on'] < old['checked_on']:
        return 'older'
    vals = {k:item.get(k,old[k]) for k in allowed}
    if all(vals[k] == old[k] for k in allowed):
        return 'unchanged'
    db.execute('UPDATE companies SET '+','.join(k+'=?' for k in allowed)+',revision=revision+1 WHERE id=?',list(vals.values())+[item['id']])
    return 'updated'

JOB_FIELDS = ['company_id','identity_key','title','city','department','batch','recruitment_type','direction','posting_status','hc_status',
              'source_url','application_url','degree','experience','salary','description','requirements','status_note','checked_on','deadline',
              'jd_quality','jd_source_url','jd_checked_on']

def original_source(db,jid,url,kind,checked_on,note=''):
    if not isinstance(url,str) or not url.startswith(('https://','http://','mailto:')):raise ValueError('来源需要原始URL')
    db.execute('''INSERT INTO job_original_sources VALUES(?,?,?,?,?,?) ON CONFLICT(job_id,url) DO UPDATE SET
        kind=CASE WHEN excluded.kind='jd' THEN 'jd' ELSE job_original_sources.kind END,
        checked_on=MAX(COALESCE(excluded.checked_on,''),COALESCE(job_original_sources.checked_on,'')),
        note=CASE WHEN excluded.checked_on >= COALESCE(job_original_sources.checked_on,'') AND excluded.note!='' THEN excluded.note ELSE job_original_sources.note END''',
        (digest(jid+url),jid,url,kind,checked_on,note))

def original_sources(db,jid):
    return [dict(r) for r in db.execute('SELECT * FROM job_original_sources WHERE job_id=? ORDER BY checked_on DESC,url',(jid,))]

def strong_key(url):
    if not url: return None
    u=urlparse(url);q={k.lower():v for k,v in parse_qs(u.query).items()}
    host = (u.hostname or '').lower()
    if host in ('zhaopin.com', 'zhaopin.cn') or host.endswith(('.zhaopin.com', '.zhaopin.cn')):
        match=re.search(r'/(?:jobdetail|jobs)/((?:cc|ccl)\d+j\d+)\.html?$',u.path,re.I)
        if match:return 'zhaopin|job='+match[1].upper()
    if host == 'linkedin.com' or host.endswith('.linkedin.com'):
        match=re.search(r'/jobs/view/(?:[^/]*-)?(\d+)(?:/|$)',u.path,re.I)
        if match:return 'linkedin|job='+match[1]
    for key in ('jobadid','jobid','positionid','postid'):
        if q.get(key):return u.netloc+u.path+'|'+key+'='+q[key][0]
    m=re.search(r'(?:^|/)job/([^/?]+)',u.fragment)
    if m:return u.netloc+u.path+'|job='+m[1]
    m=re.search(r'/job/(\d+)\.shtml',u.path)
    if m:return u.netloc+'|job='+m[1]
    return None

def resolve_job_identity(db, item):
    """Resolve all deduplication keys without changing the database."""
    old = db.execute('SELECT * FROM jobs WHERE identity_key=?', (item['identity_key'],)).fetchone()
    key=strong_key(item.get('source_url'))
    if key:
        by_key=db.execute('SELECT j.* FROM jobs j JOIN job_keys k ON k.job_id=j.id WHERE k.key=?',(key,)).fetchone()
        if old and by_key and old['id']!=by_key['id']:
            raise Conflict('输入身份与职位ID指向不同记录，需补查')
        old=old or by_key
    if not old and not key and item.get('city') and item.get('batch') not in (None, '', '批次待核'):
        candidates = db.execute('''SELECT * FROM jobs WHERE company_id=? AND title=?
            AND city=? AND batch=? AND department IS ?''',
            (item['company_id'], item['title'], item['city'], item['batch'], item.get('department'))).fetchall()
        if len(candidates) == 1:
            old = candidates[0]
        elif candidates:
            raise Conflict('同公司、标题、部门、城市和批次指向多条记录，请核对岗位身份')
    if old and old['company_id']!=item['company_id']:
        raise Conflict('同一岗位ID不能切换公司主体')
    return old, key


def upsert_job(db, item, expected_revision=None):
    old, key = resolve_job_identity(db, item)
    jid = old['id'] if old else 'job-' + digest(item['identity_key'])[:18]
    if old and (type(expected_revision) is not int or expected_revision != old['revision']):
        raise Conflict('岗位版本已变更，请重新读取后提交 expected_revision')
    if not old and expected_revision is not None:
        raise Conflict('新增岗位不能声明旧版本')
    def remember_source():
        if key:
            db.execute('INSERT OR IGNORE INTO job_keys VALUES(?,?)', (key, jid))
        original_source(db, jid, item['source_url'], 'original', item['checked_on'])
        if item.get('jd_source_url'):
            original_source(db, jid, item['jd_source_url'], 'jd', item['jd_checked_on'], '岗位职责与要求的原始来源')

    if old and item.get('checked_on') and old['checked_on'] and item['checked_on'] < old['checked_on']:
        remember_source()
        return jid, old['revision'], 'older'
    vals = {k: item.get(k, old[k] if old else None) for k in JOB_FIELDS}
    if old:vals['identity_key']=old['identity_key']
    vals['recruitment_type'] = vals['recruitment_type'] or 'unknown'
    vals['hc_status'] = vals['hc_status'] or 'unconfirmed'
    vals['batch'] = vals['batch'] or '批次待核'
    vals['status_note'] = vals['status_note'] or ''
    vals['jd_quality']=vals['jd_quality'] or 'missing'
    if vals['jd_quality'] not in ('full','partial','missing'):raise ValueError('无效JD完整度')
    # A new ledger summary must not downgrade a verified original JD.
    quality_rank={'missing':0,'partial':1,'full':2}
    older_jd=old and old['jd_checked_on'] and vals['jd_checked_on'] and vals['jd_checked_on']<old['jd_checked_on']
    if old and (quality_rank[old['jd_quality']]>quality_rank[vals['jd_quality']] or older_jd):
        for k in ('description','requirements','jd_quality','jd_source_url','jd_checked_on'):vals[k]=old[k]
    if old and all(old[k] == vals[k] for k in JOB_FIELDS):
        remember_source()
        return jid, old['revision'], 'unchanged'
    revision = old['revision'] + 1 if old else 1
    if old:
        db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in JOB_FIELDS)+',revision=?,updated_at=? WHERE id=?',
                   list(vals.values())+[revision,now(),jid])
    else:
        inserted_at = now()
        db.execute('INSERT INTO jobs(id,'+','.join(JOB_FIELDS)+',revision,updated_at,created_at) VALUES('+','.join('?' for _ in range(len(JOB_FIELDS)+4))+')',
                   [jid]+list(vals.values())+[revision,inserted_at,inserted_at])
    db.execute('INSERT INTO job_versions(job_id,revision,snapshot_json,reason,created_at) VALUES(?,?,?,?,?)',
               (jid,revision,packed(vals),item.get('merge_reason','按来源定位和核查日期交叉比对'),now()))
    remember_source()
    return jid, revision, 'updated' if old else 'inserted'

def assess(db, jid, revision, score, reason, known, unknowns, profile_version, rule_version):
    if score is not None and (type(score) is not int or not 0 <= score <= 3):
        raise ValueError('AI评分必须是0—3整数或null')
    db.execute('INSERT INTO assessments(job_id,job_revision,profile_version,ai_score,reason,known,unknowns,evaluated_at,rule_version) VALUES(?,?,?,?,?,?,?,?,?)',
               (jid,revision,profile_version,score,reason,known,unknowns,now(),rule_version))

def observation(db, table, owner, sid, start, end, raw, checked_on):
    owner_field = 'job_id' if table == 'job_observations' else 'company_id'
    oid = digest(table+owner+sid+str(start))
    db.execute(f'''INSERT INTO {table}(id,{owner_field},source_id,line_start,line_end,raw_text,checked_on) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT({owner_field},source_id,line_start) DO UPDATE SET checked_on=excluded.checked_on''',
               (oid,owner,sid,start,end,raw,checked_on))

def upsert_tracking(db, item):
    keys = ['company_id','job_id','kind','reason','scope','next_action','checked_on','review_on','status','source_id','line_start','line_end','blocker_type','blocker_evidence','blocker_url']
    old = db.execute('SELECT * FROM tracking_items WHERE id=?',(item['id'],)).fetchone()
    vals = {k:item.get(k,old[k] if old else None) for k in keys}
    vals['status'] = vals['status'] or 'open'
    for field in ('blocker_type','blocker_evidence','blocker_url'):vals[field]=vals[field] or ''
    if vals['kind']!='blocked':
        for field in ('blocker_type','blocker_evidence','blocker_url'):vals[field]=''
    if old and old['checked_on'] and vals['checked_on'] and vals['checked_on'] < old['checked_on']:
        return 'older'
    if old and all(old[k] == vals[k] for k in keys):
        return 'unchanged'
    rev = old['revision']+1 if old else 1
    if old:
        db.execute('UPDATE tracking_items SET '+','.join(k+'=?' for k in keys)+',revision=? WHERE id=?',list(vals.values())+[rev,item['id']])
    else:
        db.execute('INSERT INTO tracking_items(id,'+','.join(keys)+',revision,created_at) VALUES('+','.join('?' for _ in range(len(keys)+3))+')',[item['id']]+list(vals.values())+[rev,now()])
    db.execute('INSERT INTO tracking_versions(tracking_id,revision,snapshot_json,created_at) VALUES(?,?,?,?)',
               (item['id'],rev,packed(vals),now()))
    return 'updated' if old else 'inserted'

def read_jobs(db):
    import policy
    fingerprint = policy.current(db)["fingerprint"]
    rows = db.execute('''SELECT j.*,c.name AS company,c.parent_id,c.industry AS company_industry,
        a.ai_score,a.reason AS score_reason,a.known,a.unknowns,a.evaluated_at,a.job_revision AS assessment_revision,
        COALESCE(u.status,'new') AS user_status,u.score_override,u.score_note,COALESCE(u.revision,0) AS user_revision,
        (f.job_id IS NOT NULL) AS is_favorite, f.created_at AS favorite_at,
        COALESCE(u.score_override,a.ai_score) AS effective_score
        FROM jobs j JOIN companies c ON c.id=j.company_id
        LEFT JOIN assessments a ON a.id=(SELECT MAX(a2.id) FROM assessments a2
            WHERE a2.job_id=j.id AND a2.job_revision=j.revision AND a2.profile_version=?)
        LEFT JOIN user_job_state u ON u.job_id=j.id
        LEFT JOIN user_job_favorites f ON f.job_id=j.id
        ORDER BY effective_score DESC,j.checked_on DESC,c.name,j.title''', (fingerprint,)).fetchall()
    jobs = [dict(row) for row in rows]
    for job in jobs:
        job['is_favorite'] = bool(job['is_favorite'])
        job['preference_review_pending'] = job['assessment_revision'] is None
    return jobs

def update_favorite(db, jid, payload):
    if not db.execute('SELECT 1 FROM jobs WHERE id=?',(jid,)).fetchone():
        raise LookupError('岗位不存在')
    if set(payload) != {'favorite','expected_favorite'} or any(type(payload[k]) is not bool for k in payload):
        raise ValueError('收藏状态及预期状态必须是布尔值')
    current = bool(db.execute('SELECT 1 FROM user_job_favorites WHERE job_id=?',(jid,)).fetchone())
    if current != payload['expected_favorite']:
        raise Conflict('收藏状态已变更，请刷新后重试')
    if payload['favorite'] and not current:
        db.execute('INSERT INTO user_job_favorites(job_id,created_at) VALUES(?,?)',(jid,now()))
    elif not payload['favorite'] and current:
        db.execute('DELETE FROM user_job_favorites WHERE job_id=?',(jid,))

def update_user(db, jid, payload):
    if not db.execute('SELECT id FROM jobs WHERE id=?',(jid,)).fetchone():
        raise LookupError('岗位不存在')
    if not payload or set(payload)-{'status','score_override','score_note','expected_revision'}:
        raise ValueError('只允许修改个人状态、个人评分及改分理由')
    oldrow = db.execute('SELECT * FROM user_job_state WHERE job_id=?',(jid,)).fetchone()
    old = dict(oldrow) if oldrow else {'job_id':jid,'status':'new','score_override':None,'score_note':'','revision':0}
    if payload.get('expected_revision') != old['revision']:
        raise Conflict('个人标记已变更，请刷新后重试')
    state = {**old,**{k:v for k,v in payload.items() if k!='expected_revision'}}
    if state['status'] not in ('new','applied','ignored'):
        raise ValueError('无效个人状态')
    s = state['score_override']
    if s is not None and (type(s) is not int or not 0 <= s <= 3):
        raise ValueError('我的评分必须是0—3整数或null')
    if not isinstance(state['score_note'],str) or len(state['score_note'])>2000:
        raise ValueError('改分理由最多2000字')
    db.execute('''INSERT INTO user_job_state VALUES(?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET
        status=excluded.status,score_override=excluded.score_override,score_note=excluded.score_note,
        revision=excluded.revision,updated_at=excluded.updated_at''',
        (jid,state['status'],s,state['score_note'],old['revision']+1,now()))
    db.execute('INSERT INTO user_state_history(job_id,before_json,after_json,created_at) VALUES(?,?,?,?)',
               (jid,packed(old),packed(state),now()))

def tracking(db):
    return [dict(r) for r in db.execute('''SELECT t.*,c.name AS company,r.id AS request_id,r.status AS request_status
        FROM tracking_items t JOIN companies c ON c.id=t.company_id
        LEFT JOIN review_requests r ON r.id=(SELECT MAX(r2.id) FROM review_requests r2 WHERE r2.tracking_id=t.id)
        ORDER BY t.checked_on DESC,c.name,t.reason''')]

def request_review(db, tid, cancel=False):
    if not db.execute('SELECT id FROM tracking_items WHERE id=?',(tid,)).fetchone():
        raise LookupError('追踪项不存在')
    active = db.execute("SELECT * FROM review_requests WHERE tracking_id=? AND status IN ('pending','working')",(tid,)).fetchone()
    if cancel:
        if active and active['status']=='working':
            raise Conflict('Agent正在复查，不能取消')
        if active:
            db.execute("UPDATE review_requests SET status='cancelled',completed_at=? WHERE id=?",(now(),active['id']))
    elif not active:
        db.execute("INSERT INTO review_requests(tracking_id,status,created_at) VALUES(?,'pending',?)",(tid,now()))
