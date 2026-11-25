import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DAY = '2025-03-12'
COMPANY = '示例物流'
TITLE = '供应链计划专员'
DUTIES = '制定库存补货计划并分析库存周转。'
REQUIREMENTS = '具备供应链计划和数据分析经验。'
URL = 'https://jobs.example.invalid/posting?jobId=42'


def settings(label='库存规划优先'):
    return {
        'timezone': 'UTC',
        'search': {'scope': '供应链计划岗位', 'exclusions': '纯销售'},
        'profile': {'goal': '供应链计划', 'confirmed': '有库存分析经验。', 'unknowns': '未确认带团队经历。'},
        'scores': {
            '0': {'label': '明确不合适', 'criteria': '岗位核心职责是销售。'},
            '1': {'label': '可探索', 'criteria': '可迁移的采购运营岗位。'},
            '2': {'label': '可以关注', 'criteria': '职责有供应链分析。'},
            '3': {'label': label, 'criteria': '库存规划是主要职责。'},
        },
        'hard_filters': '已证实以销售为核心的岗位排除。',
    }


def full_batch():
    return {
        'source': {'url': URL, 'content': '\n'.join((COMPANY, TITLE, DUTIES, REQUIREMENTS)), 'checked_on': DAY},
        'companies': [{'id': 'example-logistics', 'name': COMPANY, 'source_locator': COMPANY}],
        'jobs': [{
            'company_id': 'example-logistics', 'identity_key': 'example-plan-42', 'title': TITLE,
            'direction': '供应链', 'city': '示例市', 'department': '库存规划',
            'batch': '春季批次', 'posting_status': 'listed', 'source_url': URL, 'source_locator': TITLE,
            'jd_quality': 'full', 'description': DUTIES, 'requirements': REQUIREMENTS,
            'jd_source_url': URL, 'jd_checked_on': DAY,
        }],
    }


def report_receipt(packet, name, status='processed', scope='all'):
    return {'name': name, 'sha256': packet['input_hashes'][name], 'status': status, 'scope': scope,
            'note': '已核对指定范围，结果见本次证据。'}


class JobClawTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='JobClaw 工作区 ')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = {**os.environ, 'JOBCLAW_HOME': str(self.home), 'PYTHONDONTWRITEBYTECODE': '1'}

    def cli(self, *args, error=None):
        result = subprocess.run([sys.executable, str(ROOT / 'jobclaw.py'), *map(str, args)],
                                env=self.env, text=True, encoding='utf-8', capture_output=True, timeout=15)
        if error:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(error, result.stderr)
        else:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def payload(self, value):
        path = self.home / 'task-input.json'
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path

    def configure(self, revision=0, label='库存规划优先', expected_error=None):
        return self.cli('configure', '--input', self.payload(settings(label)), '--expected-revision', revision,
                        error=expected_error)

    def sync_payload(self, batch=None, key='first', receipt=None):
        packet = self.cli('sync', 'prepare')
        return {'idempotency_key': key, 'policy_fingerprint': packet['agent_contract']['fingerprint'],
                'input_hashes': packet['input_hashes'], 'batches': [batch] if batch else [],
                'report_receipts': [receipt] if receipt else []}

    def review_payload(self, key='review-one', score=3, quote=DUTIES):
        packet = self.cli('review', 'prepare')
        return {
            'idempotency_key': key, 'policy_fingerprint': packet['policy_fingerprint'],
            'input_fingerprint': packet['input_fingerprint'], 'review_head': packet['review_head'],
            'summary': '按用户选择的优先级逐岗比较。',
            'rules': [],
            'reviews': [
                {'job_id': j['id'], 'job_revision': j['revision'], 'ai_score': score, 'basis_quote': quote,
                 'reason': '职责证据与优先条件一致。', 'gaps': '团队职责待核。'} for j in packet['jobs']
            ],
        }

    def start_server(self):
        process = subprocess.Popen([sys.executable, str(ROOT / 'jobclaw.py'), 'serve', '--port', '0'],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   encoding='utf-8')
        self.addCleanup(process.stdout.close)

        def stop():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)

        self.addCleanup(stop)
        line = process.stdout.readline()
        self.assertTrue(line.startswith('JobClaw: http://127.0.0.1:'), line)
        return line.strip().split()[-1].rstrip('/')

    def http(self, base, path, method='GET', body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = Request(base + path, method=method, data=data, headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=5) as response:
            return json.load(response)

    def test_empty_install_and_multi_agent_receipts(self):
        export = self.cli('init')
        self.assertTrue(export['ok'])
        self.assertEqual(export['jobs'], 0)
        base = self.start_server()
        self.assertEqual(self.http(base, '/api/health')['jobs'], 0)
        scope_path = Path(export['scope_path'])
        self.assertIn('尚未配置', scope_path.read_text(encoding='utf-8'))
        self.assertTrue(self.configure()['scan_ledger']['ok'])
        self.assertNotIn(settings()['profile']['confirmed'], scope_path.read_text(encoding='utf-8'))
        self.assertIn(settings()['search']['scope'], scope_path.read_text(encoding='utf-8'))
        self.configure(expected_error='配置版本已变化')
        inbox = self.home / 'inbox'
        names = ['scan-public-site-2025-03-12.md', 'scan-cloud-2025-03-12-R02.md']
        for name in [*names, 'scan-bad-date-2025-02-31.md', 'scan-first-invalid-2025-03-12-R01.md',
                     'unrelated-2025-03-12.md']:
            (inbox / name).write_bytes('## 本轮结果\r\n'.encode())
        packet = self.cli('sync', 'prepare')
        self.assertEqual(sorted(packet['input_hashes']), sorted(names))
        receipt = report_receipt(packet, names[0], 'partial', 'blocked_only')
        path = self.payload(self.sync_payload(receipt=receipt))
        self.assertEqual(self.cli('sync', 'preview', '--input', path)['summary']['report_receipts'], [receipt])
        self.assertEqual(len(self.cli('sync', 'prepare')['pending_reports']), 2)
        self.cli('sync', 'apply', '--input', path)
        self.assertTrue(self.cli('sync', 'apply', '--input', path)['reused'])
        states = {r['name']: r for r in self.cli('sync', 'prepare')['discovery_reports']}
        self.assertEqual(states[names[0]]['status'], 'partial')
        self.assertEqual(states[names[1]]['status'], 'pending')
        path = self.payload(self.sync_payload(key='complete', receipt=report_receipt(packet, names[0])))
        self.cli('sync', 'apply', '--input', path)
        self.assertEqual(len(self.cli('sync', 'prepare')['pending_reports']), 1)
        self.assertEqual((inbox / names[0]).read_bytes(), '## 本轮结果\r\n'.encode())
        (inbox / names[0]).write_text('编辑后的内容', encoding='utf-8')
        self.assertEqual(len(self.cli('sync', 'prepare')['pending_reports']), 2)

    def test_verified_job_review_configuration_and_personal_states(self):
        self.configure()
        base = self.start_server()
        path = self.payload(self.sync_payload(full_batch()))
        self.assertEqual(self.cli('sync', 'preview', '--input', path)['summary']['counts']['jobs'], 1)
        self.assertEqual(self.http(base, '/api/health')['jobs'], 0)
        sync_result = self.cli('sync', 'apply', '--input', path)
        self.assertTrue(sync_result['scan_ledger']['ok'])
        self.assertTrue(sync_result['summary']['preference_review_required'])
        ui = self.http(base, '/api/ui/bootstrap')['data']
        job = ui['jobs'][0]
        self.assertEqual(job['description'], DUTIES)
        self.assertIsNone(job['ai_score'])
        self.assertEqual(job['recruitment_type'], 'unknown')
        self.assertEqual(ui['score_labels'][3], '库存规划优先')
        self.assertNotIn(settings()['profile']['confirmed'], json.dumps(ui, ensure_ascii=False))
        path = self.payload(self.review_payload())
        self.cli('review', 'preview', '--input', path)
        self.assertIsNone(self.http(base, '/api/ui/bootstrap')['data']['jobs'][0]['ai_score'])
        self.cli('review', 'apply', '--input', path)
        self.assertEqual(self.http(base, '/api/ui/bootstrap')['data']['jobs'][0]['ai_score'], 3)
        state_path = f"/api/me/jobs/{job['id']}/state"
        favorite_path = f"/api/me/jobs/{job['id']}/favorite"
        self.http(base, state_path, 'PATCH', {'expected_revision': 0, 'status': 'applied',
                                              'score_override': 1, 'score_note': '愿意尝试这类库存工作。'})
        self.http(base, favorite_path, 'PATCH', {'favorite': True, 'expected_favorite': False})
        stale = self.review_payload('stale')
        self.assertEqual(self.configure(revision=1, label='计划与优化')['config_revision'], 2)
        self.cli('review', 'apply', '--input', self.payload(stale), error='重新 prepare')
        fresh = self.review_payload('review-two', score=2)
        fresh['rules'] = [{'title': '可探索库存', 'interpretation': '接受部分库存工作',
                           'scope': '库存岗位', 'evidence': [{'job_id': job['id'], 'quote': '愿意尝试这类库存工作'}]}]
        fresh['reviews'][0]['reason'] = '按新规则将其作为第二优先级。'
        self.cli('review', 'apply', '--input', self.payload(fresh))
        current = self.http(base, '/api/ui/bootstrap')
        state = current['data']['jobs'][0]
        self.assertEqual((state['user_status'], state['score_override'], state['score_note'], state['is_favorite']),
                         ('applied', 1, '愿意尝试这类库存工作。', True))
        self.assertEqual((state['ai_score'], state['effective_score']), (2, 1))
        self.assertEqual(current['data']['score_labels'][3], '计划与优化')
        self.assertEqual(current['preferences']['version_count'], 2)
        self.assertEqual(self.http(base, f"/api/jobs/{job['id']}/sources")[0]['url'], URL)
        ledger = (self.home / 'scan-ledger.md').read_text(encoding='utf-8')
        self.assertIn(TITLE, ledger)
        self.assertNotIn(state['score_note'], ledger)

    def test_rejects_unverified_jd_and_stale_report_atomically(self):
        self.configure()
        batch = full_batch()
        batch['jobs'][0]['description'] = '看起来相关，但来源没有这段文字。'
        self.cli('sync', 'apply', '--input', self.payload(self.sync_payload(batch)), error='须来自本批原文')
        self.assertEqual(self.cli('sync', 'prepare')['existing_companies'], [])
        name = 'scan-independent-feed-2025-03-12.md'
        (self.home / 'inbox' / name).write_text('原日报', encoding='utf-8')
        path = self.payload(self.sync_payload(full_batch()))
        self.cli('sync', 'preview', '--input', path)
        (self.home / 'inbox' / name).write_text('新内容', encoding='utf-8')
        self.cli('sync', 'apply', '--input', path, error='日报清单或内容已变化')
        self.assertEqual(self.cli('sync', 'prepare')['existing_jobs'], [])

    def test_dedup_keeps_distinct_cities_and_existing_originals(self):
        self.configure()
        self.cli('sync', 'apply', '--input', self.payload(self.sync_payload(full_batch())))
        batch = full_batch()
        batch['companies'] = []
        job = batch['jobs'][0]
        job.update(identity_key='possible-repost', expected_revision=1, jd_quality='missing')
        for field in ('description', 'requirements', 'jd_source_url', 'jd_checked_on'):
            job.pop(field)
        result = self.cli('sync', 'apply', '--input', self.payload(self.sync_payload(batch, key='same')))
        self.assertEqual(result['summary']['counts']['jobs'], 1)
        current = self.cli('sync', 'prepare')['existing_jobs'][0]
        self.assertEqual(current['description'], DUTIES)
        new_url = URL.replace('42', '43')
        batch['source']['url'] = new_url
        job.update(identity_key='new-city-43', city='另一示例市', source_url=new_url)
        job.pop('expected_revision')
        result = self.cli('sync', 'apply', '--input', self.payload(self.sync_payload(batch, key='other-city')))
        self.assertEqual(result['summary']['counts']['jobs'], 2)
        packet = self.review_payload()
        self.cli('review', 'apply', '--input', self.payload(packet), error='JD 未取得')
        self.assertIsNone(self.cli('review', 'prepare')['review_head'])

    def test_blocked_tracking_review_completion_and_report_scope(self):
        self.configure()
        batch = full_batch()
        tracking_url = 'https://jobs.example.invalid/access'
        message = '提示：需要登录后继续。'
        batch = {'companies': batch['companies'],
                 'source': {'url': tracking_url, 'content': COMPANY + '\n' + message, 'checked_on': DAY},
                 'tracking': [{'id': 'example-access', 'company_id': 'example-logistics', 'kind': 'blocked',
                               'reason': '页面要求登录', 'scope': '招聘详情入口', 'next_action': '请主 Agent 补查',
                               'source_locator': message, 'blocker_type': 'authentication',
                               'blocker_evidence': message, 'blocker_url': tracking_url}]}
        self.cli('sync', 'apply', '--input', self.payload(self.sync_payload(batch)))
        base = self.start_server()
        self.http(base, '/api/me/review-requests', 'POST', {'tracking_id': 'example-access'})
        pending = self.cli('sync', 'prepare')['review_queue'][0]
        batch = {'source': {'url': tracking_url, 'content': COMPANY, 'checked_on': DAY},
                 'tracking': [{'id': 'example-access', 'company_id': 'example-logistics', 'kind': 'uncertain',
                               'reason': '入口已恢复，岗位内容待核', 'scope': '招聘详情入口',
                               'next_action': '检查具体招聘详情', 'source_locator': COMPANY,
                               'expected_revision': 1, 'status': 'resolved'}]}
        payload = self.sync_payload(batch, key='recheck')
        payload['review_completions'] = [{'id': pending['id'], 'result': '原始入口已可读取，具体岗位仍待核。'}]
        self.cli('sync', 'apply', '--input', self.payload(payload))
        after = self.cli('sync', 'prepare')
        self.assertEqual(after['review_queue'], [])
        self.assertEqual(after['existing_tracking'][0]['status'], 'resolved')

    def test_all_document_placeholders_are_explained(self):
        import re

        guide = (ROOT / '填写清单.md').read_text(encoding='utf-8')
        paths = [ROOT / 'README.md', *sorted((ROOT / 'templates').iterdir()),
                 *sorted((ROOT / '.agents' / 'skills' / 'jobclaw').rglob('*.md'))]
        for path in paths:
            for token in set(re.findall(r'【[^】\n]+】', path.read_text(encoding='utf-8'))):
                self.assertIn(token, guide, f'{path.name}: {token}')


if __name__ == '__main__':
    unittest.main()
