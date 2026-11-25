"""Prepare, preview and commit user-scoped hiring evidence."""

import json

import discovery
import importer
import policy
import preferences
import store


def prepare(db):
    documents = discovery.input_documents()
    reports = discovery.report_states(db, documents)
    return {
        "agent_contract": policy.current(db, include_private=True),
        "input_hashes": discovery.manifest(documents),
        "discovery_reports": reports,
        "pending_reports": [{**r, "content": documents[r["name"]]["content"]}
                            for r in reports if r["status"] != "processed"],
        "existing_companies": [dict(r) for r in db.execute("SELECT * FROM companies ORDER BY name")],
        "company_aliases": [dict(r) for r in db.execute("SELECT * FROM company_aliases ORDER BY alias")],
        "existing_jobs": store.read_jobs(db),
        "job_keys": [dict(r) for r in db.execute("SELECT * FROM job_keys ORDER BY key")],
        "existing_tracking": store.tracking(db),
        "review_queue": [dict(r) for r in db.execute(
            "SELECT * FROM review_requests WHERE status IN ('pending','working') ORDER BY id")],
    }


def finish_requests(db, completions, updated_tracking):
    if not isinstance(completions, list):
        raise ValueError("review_completions 应为数组")
    for item in completions:
        if (not isinstance(item, dict) or set(item) != {"id", "result"}
                or type(item["id"]) is not int
                or not isinstance(item["result"], str) or not item["result"].strip()):
            raise ValueError("复查回执需要请求 id 和有证据的 result")
        row = db.execute("SELECT * FROM review_requests WHERE id=?", (item["id"],)).fetchone()
        if not row or row["status"] not in ("pending", "working"):
            raise store.Conflict("复查请求已变化，请重新 prepare")
        if row["tracking_id"] not in updated_tracking:
            raise ValueError("完成复查须在本批同时提交对应的核查记录与证据")
        db.execute("UPDATE review_requests SET status='done',result=?,completed_at=? WHERE id=?",
                   (item["result"], store.now(), item["id"]))


def commit(db, payload):
    required = {"idempotency_key", "policy_fingerprint", "input_hashes", "batches", "report_receipts"}
    if set(payload) - {"review_completions"} != required:
        raise ValueError("同步需要 idempotency_key、policy_fingerprint、input_hashes、batches、report_receipts")
    key = payload["idempotency_key"]
    if not isinstance(key, str) or not 1 <= len(key.strip()) <= 200:
        raise ValueError("需要 1—200 字符的幂等键")
    run_id = "sync-" + store.digest(key)[:24]
    payload_hash = store.digest(store.packed(payload))
    old = db.execute("SELECT * FROM sync_runs WHERE id=?", (run_id,)).fetchone()
    if old:
        if old["payload_hash"] != payload_hash:
            raise store.Conflict("同一幂等键不能提交不同内容")
        return {"id": run_id, "reused": True, "summary": json.loads(old["summary_json"])}
    contract = policy.check(db, payload)
    documents = discovery.input_documents()
    actual = discovery.manifest(documents)
    if actual != payload["input_hashes"]:
        raise store.Conflict("日报清单或内容已变化，请重新 prepare")
    receipts = discovery.validate_receipts(db, payload["report_receipts"], documents)
    batches = payload["batches"]
    if not isinstance(batches, list) or not (batches or receipts):
        raise ValueError("需要至少一批证据或一条日报回执")
    before_users = store.user_fingerprint(db)
    summaries = [importer.import_batch(db, batch) for batch in batches]
    finish_requests(db, payload.get("review_completions", []),
                    {t["id"] for summary in summaries for t in summary["tracking"] if t["action"] != "older"})
    for receipt in receipts:
        name = receipt["name"]
        store.source(db, name, documents[name]["content"], None)
    if before_users != store.user_fingerprint(db):
        raise RuntimeError("意外变更个人状态，已回滚")
    if actual != discovery.manifest(discovery.input_documents()):
        raise store.Conflict("提交期间日报变化，已回滚")
    summary = {
        "batches": summaries,
        "report_receipts": receipts,
        "policy_fingerprint": contract["fingerprint"],
        "personal_state_preserved": True,
        "preference_review_required": preferences.snapshot(db)["pending"],
        "counts": {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                   for table in ("companies", "jobs")},
    }
    db.execute("INSERT INTO sync_runs VALUES(?,?,?,?,?)",
               (run_id, payload_hash, store.packed(actual), store.packed(summary), store.now()))
    discovery.record_receipts(db, receipts, run_id)
    return {"id": run_id, "reused": False, "summary": summary}
