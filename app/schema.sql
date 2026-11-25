PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS preference_runs (
 id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, input_fingerprint TEXT NOT NULL,
 created_at TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_settings (
 id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
 config_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_documents (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL, content TEXT NOT NULL,
 checked_on TEXT, received_at TEXT NOT NULL, UNIQUE(name,sha256)
);
CREATE TABLE IF NOT EXISTS companies (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, source_name TEXT,
 parent_id TEXT REFERENCES companies(id), website TEXT, region TEXT, channel TEXT,
 summary TEXT, status_note TEXT, checked_on TEXT, next_action TEXT, industry TEXT, revision INTEGER NOT NULL DEFAULT 1,
 created_at TEXT
);
CREATE TABLE IF NOT EXISTS company_aliases (
 alias TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id)
);
CREATE TABLE IF NOT EXISTS company_observations (
 id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id),
 source_id TEXT NOT NULL REFERENCES source_documents(id), line_start INTEGER NOT NULL,
 line_end INTEGER NOT NULL, raw_text TEXT NOT NULL, checked_on TEXT, UNIQUE(company_id,source_id,line_start)
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id), identity_key TEXT NOT NULL UNIQUE,
 title TEXT NOT NULL, city TEXT, department TEXT, batch TEXT NOT NULL, recruitment_type TEXT NOT NULL DEFAULT 'unknown',
 direction TEXT NOT NULL, posting_status TEXT NOT NULL, hc_status TEXT NOT NULL DEFAULT 'unconfirmed',
 source_url TEXT, application_url TEXT, degree TEXT, experience TEXT, salary TEXT,
 description TEXT, requirements TEXT, status_note TEXT, checked_on TEXT, deadline TEXT,
 jd_quality TEXT NOT NULL DEFAULT 'missing', jd_source_url TEXT, jd_checked_on TEXT,
 revision INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL, created_at TEXT
);
CREATE TABLE IF NOT EXISTS job_observations (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), source_id TEXT NOT NULL REFERENCES source_documents(id),
 line_start INTEGER NOT NULL, line_end INTEGER NOT NULL, raw_text TEXT NOT NULL, checked_on TEXT,
 UNIQUE(job_id,source_id,line_start)
);
CREATE TABLE IF NOT EXISTS job_keys (
 key TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS job_versions (
 id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), revision INTEGER NOT NULL,
 snapshot_json TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(job_id,revision)
);
CREATE TABLE IF NOT EXISTS assessments (
 id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), job_revision INTEGER NOT NULL,
 profile_version TEXT NOT NULL, ai_score INTEGER CHECK(ai_score BETWEEN 0 AND 3),
 reason TEXT NOT NULL, known TEXT NOT NULL, unknowns TEXT NOT NULL, evaluated_at TEXT NOT NULL,
 rule_version TEXT NOT NULL, UNIQUE(job_id,job_revision,profile_version,rule_version)
);
CREATE TABLE IF NOT EXISTS user_job_state (
 job_id TEXT PRIMARY KEY REFERENCES jobs(id), status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','applied','ignored')),
 score_override INTEGER CHECK(score_override BETWEEN 0 AND 3), score_note TEXT NOT NULL DEFAULT '',
 revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_job_favorites (
 job_id TEXT PRIMARY KEY REFERENCES jobs(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_state_history (
 id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), before_json TEXT NOT NULL,
 after_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tracking_items (
 id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id), job_id TEXT REFERENCES jobs(id),
 kind TEXT NOT NULL CHECK(kind IN ('blocked','expired','uncertain','no_match')), reason TEXT NOT NULL,
 scope TEXT NOT NULL, next_action TEXT NOT NULL, checked_on TEXT, review_on TEXT,
 status TEXT NOT NULL DEFAULT 'open', revision INTEGER NOT NULL DEFAULT 1,
 source_id TEXT REFERENCES source_documents(id), line_start INTEGER, line_end INTEGER,
 blocker_type TEXT NOT NULL DEFAULT '', blocker_evidence TEXT NOT NULL DEFAULT '', blocker_url TEXT NOT NULL DEFAULT '',
 created_at TEXT
);
CREATE TABLE IF NOT EXISTS tracking_versions (
 id INTEGER PRIMARY KEY, tracking_id TEXT NOT NULL REFERENCES tracking_items(id),
 revision INTEGER NOT NULL, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(tracking_id,revision)
);
CREATE TABLE IF NOT EXISTS review_requests (
 id INTEGER PRIMARY KEY, tracking_id TEXT NOT NULL REFERENCES tracking_items(id),
 status TEXT NOT NULL CHECK(status IN ('pending','working','done','cancelled')),
 created_at TEXT NOT NULL, completed_at TEXT, result TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_review ON review_requests(tracking_id) WHERE status IN ('pending','working');
CREATE INDEX IF NOT EXISTS jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(posting_status,direction);
CREATE INDEX IF NOT EXISTS assessments_job_latest ON assessments(job_id,id DESC);
CREATE INDEX IF NOT EXISTS source_documents_listing ON source_documents(name,id,sha256,checked_on,received_at);
CREATE INDEX IF NOT EXISTS evidence_job ON job_observations(job_id);
CREATE TABLE IF NOT EXISTS job_original_sources (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), url TEXT NOT NULL,
 kind TEXT NOT NULL, checked_on TEXT, note TEXT NOT NULL DEFAULT '', UNIQUE(job_id,url)
);
CREATE TABLE IF NOT EXISTS sync_runs (
 id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, manifest_json TEXT NOT NULL,
 summary_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discovery_receipts (
 id INTEGER PRIMARY KEY,
 report_name TEXT NOT NULL, report_sha256 TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('partial','processed')),
 scope TEXT NOT NULL CHECK(scope IN ('all','blocked_only','selected')),
 note TEXT NOT NULL, sync_id TEXT NOT NULL REFERENCES sync_runs(id),
 created_at TEXT NOT NULL,
 CHECK(status!='processed' OR scope='all'),
 UNIQUE(sync_id,report_name)
);
CREATE INDEX IF NOT EXISTS discovery_receipt_version
 ON discovery_receipts(report_name,report_sha256,id);
