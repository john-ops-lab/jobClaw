"""Versioned, evidence-backed scoring. The calling Agent supplies judgments."""

import json

import policy
import store


def records(db):
    return [dict(r) for r in db.execute('''SELECT u.*,c.name AS company,j.title,
        (SELECT MIN(h.created_at) FROM user_state_history h WHERE h.job_id=u.job_id) AS created_at
        FROM user_job_state u JOIN jobs j ON j.id=u.job_id JOIN companies c ON c.id=j.company_id
        ORDER BY u.updated_at DESC,u.job_id''')]


def fingerprint(db, contract):
    jobs = [tuple(r) for r in db.execute("SELECT id,revision FROM jobs ORDER BY id")]
    notes = [tuple(r) for r in db.execute(
        "SELECT job_id,score_note FROM user_job_state WHERE trim(score_note)!='' ORDER BY job_id")]
    return store.digest(store.packed([contract["fingerprint"], jobs, notes]))


def latest(db):
    return db.execute("SELECT * FROM preference_runs ORDER BY rowid DESC LIMIT 1").fetchone()


def prepare(db):
    contract = policy.current(db, include_private=True)
    previous = snapshot(db)["latest"]
    return {
        "policy_fingerprint": contract["fingerprint"],
        "input_fingerprint": fingerprint(db, contract),
        "agent_contract": contract,
        "jobs": store.read_jobs(db),
        "records": records(db),
        "previous_summary": previous,
        "review_head": previous["id"] if previous else None,
    }


def validate_rules(rules, current_records):
    if not isinstance(rules, list):
        raise ValueError("rules 应为数组")
    by_id = {r["job_id"]: r for r in current_records}
    keys = {"title", "interpretation", "scope", "evidence"}
    for rule in rules:
        if (not isinstance(rule, dict) or set(rule) != keys
                or not all(isinstance(rule[k], str) and rule[k].strip() for k in keys - {"evidence"})):
            raise ValueError("归纳规则需要 title、interpretation、scope、evidence")
        if not isinstance(rule["evidence"], list) or not rule["evidence"]:
            raise ValueError("从个人记录归纳的每条规则需要原话依据")
        for evidence in rule["evidence"]:
            if (not isinstance(evidence, dict) or set(evidence) != {"job_id", "quote"}
                    or not isinstance(evidence["job_id"], str)
                    or not isinstance(evidence["quote"], str) or not evidence["quote"].strip()):
                raise ValueError("个人偏好证据需要 job_id 和原话 quote")
            record = by_id.get(evidence["job_id"])
            if not record or evidence["quote"] not in record["score_note"]:
                raise ValueError("归纳只能引用当前保留的个人改分理由")


def validate_review(review, job):
    required = {"job_id", "job_revision", "ai_score", "reason", "gaps", "basis_quote"}
    if set(review) - {"mismatch_type"} != required:
        raise ValueError("review 字段不完整或包含个人状态字段")
    if type(review["job_revision"]) is not int or review["job_revision"] != job["revision"]:
        raise store.Conflict("岗位版本已变化，请重新 prepare")
    score = review["ai_score"]
    if score is not None and (type(score) is not int or not 0 <= score <= 3):
        raise ValueError("AI 分数为 0—3 的整数或 null")
    if not all(isinstance(review[k], str) and review[k].strip() for k in ("reason", "gaps")):
        raise ValueError("每岗需要理由和待核结论")
    mismatch = review.get("mismatch_type")
    if score == 0 and (mismatch not in policy.MISMATCH_TYPES or review["reason"].strip() in {"0", "0分", "0 分"}):
        raise ValueError("0 分必须提交明确、与截止日期独立的不匹配类型及原因")
    if score != 0 and mismatch is not None:
        raise ValueError("mismatch_type 仅用于 0 分")
    quote = review["basis_quote"]
    jd = (job["description"] or "") + "\n" + (job["requirements"] or "")
    if job["jd_quality"] == "missing":
        if quote != "" or score is not None:
            raise ValueError("JD 未取得，评分为 null，原文引句留空")
    elif not isinstance(quote, str) or not quote.strip() or quote not in jd:
        raise ValueError("评分依据须为对应岗位 JD 中的原文")


def commit(db, payload):
    fields = {"idempotency_key", "policy_fingerprint", "input_fingerprint", "review_head", "summary", "rules", "reviews"}
    if set(payload) != fields:
        raise ValueError("重评需要 idempotency_key、policy_fingerprint、input_fingerprint、review_head、summary、rules、reviews")
    key = payload["idempotency_key"]
    if not isinstance(key, str) or not 1 <= len(key.strip()) <= 200:
        raise ValueError("需要 1—200 字符的幂等键")
    run_id = "review-" + store.digest(key)[:24]
    payload_hash = store.digest(store.packed(payload))
    old = db.execute("SELECT * FROM preference_runs WHERE id=?", (run_id,)).fetchone()
    if old:
        if old["payload_hash"] != payload_hash:
            raise store.Conflict("同一幂等键不能提交不同内容")
        return {"id": run_id, "reused": True, **json.loads(old["result_json"])}
    contract = policy.check(db, payload)
    prev = latest(db)
    if payload["review_head"] != (prev["id"] if prev else None):
        raise store.Conflict("重评基线已变化，请重新 prepare")
    input_version = fingerprint(db, contract)
    if payload["input_fingerprint"] != input_version:
        raise store.Conflict("配置、岗位或理由已变化，请重新 prepare")
    if not isinstance(payload["summary"], str) or not payload["summary"].strip():
        raise ValueError("需要本轮评分和偏好总结")
    current_records = records(db)
    validate_rules(payload["rules"], current_records)
    jobs = {j["id"]: j for j in store.read_jobs(db)}
    reviews = payload["reviews"]
    if (not isinstance(reviews, list) or len(reviews) != len(jobs)
            or any(not isinstance(r, dict) or not isinstance(r.get("job_id"), str) for r in reviews)
            or {r["job_id"] for r in reviews} != set(jobs)):
        raise ValueError("每个岗位恰好提交一条评估")
    changes = []
    before = {c["job_id"]: c["after"] for c in json.loads(prev["result_json"])["changes"]} if prev else {}
    before_users = store.user_fingerprint(db)
    for review in reviews:
        job = jobs[review["job_id"]]
        validate_review(review, job)
        score = review["ai_score"]
        personal = job["score_override"]
        store.assess(db, job["id"], job["revision"], score, review["reason"], review["basis_quote"],
                     review["gaps"], contract["fingerprint"], run_id)
        changes.append({"job_id": job["id"], "company": job["company"], "title": job["title"],
                        "before": before.get(job["id"]), "after": score, "reason": review["reason"],
                        "personal_score": personal, "effective_score": personal if personal is not None else score})
    if before_users != store.user_fingerprint(db):
        raise RuntimeError("意外变更个人状态，重评已回滚")
    result = {"reviewed": len(jobs), "changed": sum(c["before"] != c["after"] for c in changes),
              "changes": changes, "personal_state_preserved": True}
    db.execute("INSERT INTO preference_runs VALUES(?,?,?,?,?,?)",
               (run_id, payload_hash, input_version, store.now(), store.packed(payload), store.packed(result)))
    return {"id": run_id, "reused": False, **result}


def snapshot(db):
    rows = db.execute("SELECT * FROM preference_runs ORDER BY rowid DESC LIMIT 20").fetchall()
    current = records(db)
    contract = policy.current(db)
    result = {
        "records": current,
        "pending": bool(rows and rows[0]["input_fingerprint"] != fingerprint(db, contract))
                   or (not rows and bool(db.execute("SELECT 1 FROM jobs LIMIT 1").fetchone())),
        "note_count": sum(bool(r["score_note"].strip()) for r in current),
        "applied": sum(r["status"] == "applied" for r in current),
        "ignored": sum(r["status"] == "ignored" or r["score_override"] == 0 for r in current),
        "version_count": db.execute("SELECT COUNT(*) FROM preference_runs").fetchone()[0],
        "history": [],
        "latest": None,
    }
    for run in rows:
        payload = json.loads(run["payload_json"])
        summary = json.loads(run["result_json"])
        entry = {"id": run["id"], "created_at": run["created_at"], "summary": payload["summary"],
                 "reviewed": summary["reviewed"], "changed": summary["changed"]}
        result["history"].append({**entry, "changes": [c for c in summary["changes"] if c["before"] != c["after"]]})
        if result["latest"] is None:
            result["latest"] = {**entry, "rules": payload["rules"], "changes": summary["changes"], "records": current}
    return result
