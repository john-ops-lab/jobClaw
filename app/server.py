import gzip
import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import policy
import preferences
import store


def ui_version(db):
    """Identify facts visible to the UI, including edits from other clients or Agents."""
    queries = {
        'jobs': 'SELECT id,revision FROM jobs ORDER BY id',
        'companies': 'SELECT id,revision FROM companies ORDER BY id',
        'tracking': 'SELECT id,revision FROM tracking_items ORDER BY id',
        'users': 'SELECT job_id,revision FROM user_job_state ORDER BY job_id',
        'favorites': 'SELECT job_id,created_at FROM user_job_favorites ORDER BY job_id',
        'requests': 'SELECT id,status,completed_at,result FROM review_requests ORDER BY id',
        'assessments': 'SELECT COALESCE(MAX(id),0),COUNT(*) FROM assessments',
        'preferences': 'SELECT COALESCE(MAX(rowid),0),COUNT(*) FROM preference_runs',
    }
    values = {name: [tuple(row) for row in db.execute(query)] for name, query in queries.items()}
    values['policy'] = policy.current(db)['fingerprint']
    return store.digest(store.packed(values))


def ui_bootstrap(db):
    """Read the visible snapshot and preference summary from one SQLite snapshot."""
    started = not db.in_transaction
    if started:
        db.execute('BEGIN')
    try:
        jobs = store.read_jobs(db)
        revision, config = policy.settings(db)
        contract = policy.current(db)
        data = {
            'jobs': jobs,
            'companies': [dict(r) for r in db.execute('SELECT * FROM companies ORDER BY name')],
            'tracking': store.tracking(db),
            'profile': {'goal': config['profile']['goal'] if revision else ''},
            'score_labels': contract['score_labels'],
            'timezone': contract['timezone'],
            'counts': {
                'jobs': len(jobs),
                'companies': db.execute('SELECT COUNT(*) FROM companies').fetchone()[0],
                'overrides': sum(j['score_override'] is not None for j in jobs),
                'pending_reviews': db.execute("SELECT COUNT(*) FROM review_requests WHERE status IN ('pending','working')").fetchone()[0],
            },
            'ui_version': ui_version(db),
        }
        return {'data': data, 'preferences': preferences.snapshot(db)}
    finally:
        if started:
            db.rollback()


def accepts_gzip(value):
    """Honor explicit gzip rejection, even when a wildcard is present."""
    qualities = {}
    for entry in value.split(','):
        parts = entry.strip().lower().split(';')
        if not parts[0]:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            name, sep, raw = parameter.strip().partition('=')
            if sep and name.strip() == 'q':
                try:
                    quality = float(raw.strip())
                except ValueError:
                    quality = 0.0
        qualities[parts[0].strip()] = quality
    return qualities.get('gzip', qualities.get('*', 0)) > 0


def user_record(db, jid):
    row = db.execute('''SELECT u.*,c.name AS company,j.title,j.identity_key,
        (SELECT MIN(h.created_at) FROM user_state_history h WHERE h.job_id=u.job_id) AS created_at
        FROM user_job_state u JOIN jobs j ON j.id=u.job_id JOIN companies c ON c.id=j.company_id
        WHERE u.job_id=?''', (jid,)).fetchone()
    return dict(row) if row else None


def tracking_record(db, tid):
    row = db.execute('''SELECT t.*,c.name AS company,r.id AS request_id,r.status AS request_status
        FROM tracking_items t JOIN companies c ON c.id=t.company_id
        LEFT JOIN review_requests r ON r.id=(SELECT MAX(r2.id) FROM review_requests r2 WHERE r2.tracking_id=t.id)
        WHERE t.id=?''', (tid,)).fetchone()
    return dict(row) if row else None

class Handler(BaseHTTPRequestHandler):
    server_version = 'JobClaw/1.0'

    def valid_host(self):
        port = self.server.server_port
        allowed = {'127.0.0.1', 'localhost'} if port == 80 else set()
        allowed.update({f'127.0.0.1:{port}', f'localhost:{port}'})
        if (self.headers.get('Host') or '').lower() not in allowed:
            self.send_json({'error': '请使用本机工作台地址'}, 403)
            return False
        return True
    def log_message(self, fmt, *args):
        if len(args)>1 and str(args[1]).startswith(('4','5')):
            super().log_message(fmt,*args)

    def send_json(self, data, code=200):
        self.send_bytes(store.packed(data).encode(),'application/json; charset=utf-8',code)

    def send_bytes(self, data, content_type, code=200):
        compressible = content_type.startswith('application/json') and len(data) >= 1024
        compressed = False
        if compressible and accepts_gzip(self.headers.get('Accept-Encoding', '')):
            candidate = gzip.compress(data, compresslevel=1, mtime=0)
            if len(candidate) < len(data):
                data, compressed = candidate, True
        self.send_response(code)
        self.send_header('Content-Type',content_type)
        if compressible:
            self.send_header('Vary','Accept-Encoding')
        if compressed:
            self.send_header('Content-Encoding','gzip')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers();self.wfile.write(data)

    def payload(self):
        length=int(self.headers.get('Content-Length','0'))
        if not 0<length<=3_000_000:
            raise ValueError('请求正文应在3MB以内')
        # Local use, no login. Reject unrelated web origins for mutation endpoints.
        origin=self.headers.get('Origin')
        if origin and urlparse(origin).netloc!=self.headers.get('Host'):
            raise ValueError('只接受本网站来源的浏览器写入请求')
        data=json.loads(self.rfile.read(length))
        if not isinstance(data,dict):
            raise ValueError('JSON正文必须是对象')
        return data

    def do_GET(self):
        if not self.valid_host():
            return
        try:
            p=urlparse(self.path).path
            files={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/views.css':'views.css'}
            if p in files:
                file=store.APP/'static'/files[p]
                ct={'html':'text/html','js':'text/javascript','css':'text/css'}[file.suffix[1:]]+'; charset=utf-8'
                return self.send_bytes(file.read_bytes(),ct)
            if p=='/favicon.ico':
                return self.send_bytes(b'','image/x-icon',204)
            with store.connection(self.server.db_path) as db:
                if p=='/api/health':
                    return self.send_json({'ok':True,'database':'sqlite','jobs':db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]})
                if p=='/api/ui/bootstrap':
                    return self.send_json(ui_bootstrap(db))
                m=re.fullmatch('/api/jobs/([^/]+)/evidence',p)
                if m:
                    return self.send_json([dict(r) for r in db.execute('''SELECT o.*,s.name AS source_name,s.sha256 FROM job_observations o
                        JOIN source_documents s ON s.id=o.source_id WHERE o.job_id=? ORDER BY o.checked_on DESC''',(m[1],))])
                m=re.fullmatch('/api/jobs/([^/]+)/sources',p)
                if m:return self.send_json(store.original_sources(db,m[1]))
                m=re.fullmatch('/api/companies/([^/]+)/sources',p)
                if m:
                    c=db.execute('SELECT * FROM companies WHERE id=?',(m[1],)).fetchone()
                    if not c:raise LookupError('公司不存在')
                    urls=[]
                    for url in (c['channel'], c['website']):
                        if policy.is_web_url(url) and url not in urls:
                            urls.append(url)
                    return self.send_json([{'url':u,'kind':'招聘入口','checked_on':c['checked_on'],'note':c['status_note']} for u in urls])
                m=re.fullmatch('/api/companies/([^/]+)/evidence',p)
                if m:
                    return self.send_json([dict(r) for r in db.execute('SELECT o.*,s.name AS source_name FROM company_observations o JOIN source_documents s ON s.id=o.source_id WHERE o.company_id=? ORDER BY o.checked_on DESC',(m[1],))])
                m=re.fullmatch('/api/sources/([^/]+)',p)
                if m:
                    row=db.execute('SELECT * FROM source_documents WHERE id=?',(m[1],)).fetchone()
                    if not row: raise LookupError('来源不存在')
                    return self.send_json(dict(row))
            self.send_json({'error':'未找到接口'},404)
        except Exception as exc:
            self.handle_error(exc)

    def do_POST(self):
        self.mutate()

    def do_PATCH(self):
        self.mutate()

    def mutate(self):
        if not self.valid_host():
            return
        try:
            p = urlparse(self.path).path
            data = self.payload()
            with store.connection(self.server.db_path) as db:
                db.execute('BEGIN IMMEDIATE')
                result={'ok':True}
                client_version = self.headers.get('X-UI-Version') if p.startswith('/api/me/') else None
                current_version = ui_version(db) if client_version else None
                m=re.fullmatch('/api/me/jobs/([^/]+)/state',p)
                if m and self.command=='PATCH':
                    store.update_user(db,m[1],data)
                    if client_version:
                        result['user_state'] = user_record(db,m[1])
                elif (m:=re.fullmatch('/api/me/jobs/([^/]+)/favorite',p)) and self.command=='PATCH':
                    store.update_favorite(db,m[1],data)
                    if client_version:
                        favorite = db.execute('SELECT created_at FROM user_job_favorites WHERE job_id=?',(m[1],)).fetchone()
                        result['favorite'] = {'job_id':m[1], 'is_favorite':bool(favorite),
                                              'favorite_at':favorite['created_at'] if favorite else None}
                elif p=='/api/me/review-requests' and self.command=='POST':
                    if set(data)-{'tracking_id','cancel'} or not isinstance(data.get('tracking_id'),str):
                        raise ValueError('需要tracking_id及可选cancel')
                    store.request_review(db,data['tracking_id'],data.get('cancel',False))
                    if client_version:
                        result['tracking'] = tracking_record(db,data['tracking_id'])
                else:
                    raise LookupError('未找到接口')
                if client_version:
                    result['ui_refresh_required'] = client_version != current_version
                    result['ui_version'] = ui_version(db)
            self.send_json(result)
        except Exception as exc:
            self.handle_error(exc)

    def handle_error(self, exc):
        if isinstance(exc,store.Conflict):code=409
        elif isinstance(exc,LookupError):code=404
        elif isinstance(exc,(ValueError,TypeError,KeyError,sqlite3.IntegrityError)):code=400
        else:code=500
        self.send_json({'error':str(exc) if code!=500 else '服务处理失败，请查看服务日志'},code)
        if code==500:
            import traceback;traceback.print_exc()

def serve(port):
    with ThreadingHTTPServer(('127.0.0.1', port), Handler) as server:
        server.db_path = store.DEFAULT_DB
        print(f'JobClaw: http://127.0.0.1:{server.server_port}/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
