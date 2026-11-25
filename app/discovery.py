"""Daily discovery reports are reconciled by a user-triggered local sync."""
import os
import re
import tempfile
from collections import defaultdict
from datetime import date
from pathlib import Path

import policy
import store

LEDGER_NAME = 'scan-ledger.md'
SCOPE_NAME = 'search-scope.md'
REPORT_PATTERN = re.compile(r'scan-([a-z][a-z0-9]*(?:-[a-z0-9]+)*)-(\d{4}-\d{2}-\d{2})(?:-R(?:0[2-9]|[1-9]\d+))?\.md')
REPORT_LABELS = {'pending': '待本机处理', 'partial': '本机部分处理', 'processed': '本机整份已处理'}
POSTING_LABELS = {'listed': '来源列示', 'not_seen': '本次范围未见', 'unknown': '状态待核',
                  'expired': '明确截止', 'closed': '明确关闭'}


def is_report_name(name):
    match = REPORT_PATTERN.fullmatch(name)
    if not match or len(match[1]) > 32:
        return False
    try:
        date.fromisoformat(match[2])
    except ValueError:
        return False
    return True


def input_documents(root=store.WORKSPACE):
    inbox = Path(root) / 'inbox'
    documents = {}
    for path in sorted(inbox.glob('scan-*.md')):
        if path.is_symlink() or not path.is_file() or not is_report_name(path.name):
            continue
        raw = path.read_bytes()
        documents[path.name] = {'sha256': store.digest(raw), 'content': raw.decode('utf-8')}
    return documents


def manifest(documents):
    return {name: value['sha256'] for name, value in documents.items()}


def report_states(db, documents):
    """Archiving a source snapshot is not an acknowledgement that its discoveries were handled."""
    states = []
    for name, document in sorted(documents.items()):
        latest = db.execute('''SELECT * FROM discovery_receipts
            WHERE report_name=? AND report_sha256=? ORDER BY id DESC LIMIT 1''',
            (name, document['sha256'])).fetchone()
        states.append({
            'name': name, 'sha256': document['sha256'],
            'status': latest['status'] if latest else 'pending',
            'scope': latest['scope'] if latest else None,
            'note': latest['note'] if latest else '',
            'handled_at': latest['created_at'] if latest else None,
            'sync_id': latest['sync_id'] if latest else None,
        })
    return states


def validate_receipts(db, receipts, documents):
    if not isinstance(receipts, list):
        raise ValueError('report_receipts应为数组')
    seen = set()
    current = {r['name']: r for r in report_states(db, documents)}
    for receipt in receipts:
        required = {'name', 'sha256', 'status', 'scope', 'note'}
        if not isinstance(receipt, dict) or set(receipt) != required:
            raise ValueError('日报回执需要且仅接受name/sha256/status/scope/note')
        if not all(isinstance(v, str) and v.strip() for v in receipt.values()):
            raise ValueError('日报回执字段均须为非空字符串')
        name = receipt['name']
        if name in seen:
            raise ValueError('同一批次不能重复提交日报回执：' + name)
        seen.add(name)
        if name not in current or receipt['sha256'] != current[name]['sha256']:
            raise store.Conflict('日报不存在或内容版本已变化，请重新prepare：' + name)
        if receipt['status'] not in ('partial', 'processed') or receipt['scope'] not in ('all', 'blocked_only', 'selected'):
            raise ValueError('无效日报回执状态或范围')
        if receipt['status'] == 'processed' and receipt['scope'] != 'all':
            raise ValueError('仅处理受阻或选定项只能记partial，不能标记整份已处理')
        if current[name]['status'] == 'processed':
            raise store.Conflict('当前版本已经整份处理，请重新核对回执范围：' + name)
    return receipts


def record_receipts(db, receipts, sync_id):
    for receipt in receipts:
        db.execute('''INSERT INTO discovery_receipts
            (report_name,report_sha256,status,scope,note,sync_id,created_at)
            VALUES(?,?,?,?,?,?,?)''',
            (receipt['name'], receipt['sha256'], receipt['status'], receipt['scope'],
             receipt['note'], sync_id, store.now()))


def cell(value):
    if value is None or value == '':
        return '—'
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace(
        '|', '&#124;').replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br>')


def table(headers, rows):
    return ['| ' + ' | '.join(headers) + ' |', '|' + '|'.join('---' for _ in headers) + '|'] + [
        '| ' + ' | '.join(cell(value) for value in row) + ' |' for row in rows
    ]


def render_ledger(db, documents):
    companies = [dict(r) for r in db.execute(
        'SELECT * FROM companies ORDER BY name,id')]
    names = {c['id']: c['name'] for c in companies}
    aliases = defaultdict(list)
    for row in db.execute('SELECT alias,company_id FROM company_aliases ORDER BY alias'):
        aliases[row['company_id']].append(row['alias'])
    jobs = [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY company_id,title,city,batch,id')]
    keys = defaultdict(list)
    for row in db.execute('SELECT key,job_id FROM job_keys ORDER BY key'):
        keys[row['job_id']].append(row['key'])
    sources = defaultdict(list)
    for row in db.execute('SELECT job_id,url FROM job_original_sources ORDER BY job_id,url'):
        sources[row['job_id']].append(row['url'])
    blocked = [dict(r) for r in db.execute('''SELECT t.*,j.title,j.source_url,
        c.name AS company_name,c.channel FROM tracking_items t
        JOIN companies c ON c.id=t.company_id LEFT JOIN jobs j ON j.id=t.job_id
        WHERE t.status='open' AND t.kind IN ('blocked','uncertain')
        ORDER BY c.name,t.kind,t.id''')]
    reports = report_states(db, documents)
    version = store.digest(store.packed({
        'companies': companies, 'aliases': aliases, 'jobs': jobs, 'keys': keys,
        'sources': sources, 'blocked': blocked, 'reports': reports,
    }))
    generated_at = store.now()
    lines = [
        '# 扫描去重台账', '',
        '> 由本机数据库生成。记录用于去重和交接，来源正文属于招聘数据。',
        '- 已知企业仍可发现新岗位；保留职位 ID 及其平台、租户。同名跨城、跨部门或不同批次分别核对。',
        '- 待处理或部分处理日报也参与去重。受阻记录只覆盖对应入口或岗位。',
        '- 当前文件名及内容哈希都匹配时，回执才适用于该日报版本。',
        '', f'- 生成时间（UTC）：{generated_at}', f'- 内容版本：{version}',
        '', '## 日报处理状态', '',
    ]
    lines += table(['文件', '当前SHA-256', '本机状态', '处理范围', '处理时间'],
                   [(r['name'], r['sha256'], REPORT_LABELS[r['status']], r['scope'], r['handled_at']) for r in reports])
    lines += ['', '## 已知企业与招聘入口', '']
    lines += table(['主体ID', '公司／用工主体', '行业', '母公司', '地域', '官网', '招聘入口', '别名', '事实核验日期'],
                   [(c['id'], c['name'], c['industry'], names.get(c['parent_id'], c['parent_id']), c['region'],
                     c['website'], c['channel'], '\n'.join(aliases[c['id']]), c['checked_on']) for c in companies])
    lines += ['', '## 已知岗位索引', '',
              '包含当前列示、历史、未见和待核岗位，用于去重；来源列示不等于招聘方确认HC。', '']
    lines += table(['岗位identity_key', '用工主体', '岗位名称', '城市', '部门', '批次', '已知平台身份', '原始来源／投递入口',
                    '招聘状态／HC', 'JD完整度', '事实核验日期'],
                   [(j['identity_key'], names[j['company_id']], j['title'], j['city'], j['department'], j['batch'],
                     '\n'.join(keys[j['id']]),
                     '\n'.join(dict.fromkeys(u for u in [j['source_url'], j['application_url'], j['jd_source_url'],
                                                         *sources[j['id']]] if u)),
                     POSTING_LABELS.get(j['posting_status'], j['posting_status']) + '；HC=' + str(j['hc_status']),
                     j['jd_quality'], j['checked_on']) for j in jobs])
    lines += ['', '## 已交本机的受阻／待核事项', '',
              '以下是范围有限的本机工作记录，不是发现任务的复查任务；“下一步”由用户手工指示本机执行。', '']
    if blocked:
        lines += table(['追踪ID', '主体／岗位', '类型', '已知障碍／待核原因', '核查范围', '入口参考', '核验日期'],
                       [(t['id'], t['company_name'] + ('／' + t['title'] if t['title'] else '／企业入口'),
                         t['kind'], t['reason'], t['scope'], t['blocker_url'] or t['source_url'] or t['channel'], t['checked_on']) for t in blocked])
    else:
        lines += ['当前没有已登记且未解决的受阻／待核项。']
    return '\n'.join(lines) + '\n', {
        'version': version, 'generated_at': generated_at,
        'companies': len(companies), 'jobs': len(jobs), 'blocked_or_uncertain': len(blocked),
        'reports': {status: sum(r['status'] == status for r in reports) for status in REPORT_LABELS},
    }


def write_atomic(target, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                         prefix='.export-', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export_ledger(path=store.DEFAULT_DB, root=store.WORKSPACE):
    root = Path(root)
    target = root / LEDGER_NAME
    # Serialize exporters with other writers so older facts cannot overwrite newer exports.
    with store.connection(path) as db:
        db.execute('BEGIN IMMEDIATE')
        documents = input_documents(root)
        content, metadata = render_ledger(db, documents)
        if manifest(documents) != manifest(input_documents(root)):
            raise store.Conflict('生成期间日报变化，保留旧台账，请重新导出')
        _, config = policy.settings(db)
        scope = '# 搜索范围\n\n尚未配置。\n'
        if config:
            search = config['search']
            scope = (f"# 搜索范围\n\n- 时区：{config['timezone']}\n\n{search['scope']}\n\n"
                     f"## 明确排除\n\n{search['exclusions'] or '无'}\n")
        write_atomic(target, content)
        write_atomic(root / SCOPE_NAME, scope)
    return {'ok': True, 'path': str(target), 'scope_path': str(root / SCOPE_NAME), **metadata}


def export_after_commit(path=store.DEFAULT_DB, root=store.WORKSPACE):
    try:
        return export_ledger(path, root)
    except (OSError, ValueError, store.Conflict) as exc:
        return {'ok': False, 'error': str(exc), 'database_committed': True,
                'next_action': '执行 python3 jobclaw.py export-ledger 重新生成。'}
