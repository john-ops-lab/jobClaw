"""Workspace configuration and the shared Agent contract."""

import json
import re
from datetime import date
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import store

VERSION = "jobclaw-1"
DEFAULT_LABELS = ["明确不匹配", "可以探索", "值得关注", "优先考虑"]
RECRUITMENT_TYPES = ("social", "campus", "internship", "unknown", "mixed")
POSTING_STATES = ("listed", "not_seen", "unknown", "expired", "closed")
HC_STATES = ("unconfirmed", "confirmed")
JD_QUALITIES = ("full", "partial", "missing")
BLOCKER_TYPES = ("authentication", "anti_bot", "network", "tls", "rendering", "extraction", "broken_link")
MISMATCH_TYPES = ("recruitment_type", "education", "experience", "skills", "role_preference",
                  "location", "salary", "industry", "other_requirement")
TRACKING_FIELDS = ("blocker_type", "blocker_evidence", "blocker_url")


def validate_config(config):
    if not isinstance(config, dict) or set(config) != {"timezone", "search", "profile", "scores", "hard_filters"}:
        raise ValueError("配置需要 timezone、search、profile、scores、hard_filters")
    search, profile, scores = (config[k] for k in ("search", "profile", "scores"))
    if not isinstance(search, dict) or set(search) != {"scope", "exclusions"}:
        raise ValueError("search 需要 scope 和 exclusions")
    if not isinstance(profile, dict) or set(profile) != {"goal", "confirmed", "unknowns"}:
        raise ValueError("profile 需要 goal、confirmed 和 unknowns")
    if not isinstance(scores, dict) or set(scores) != {"0", "1", "2", "3"}:
        raise ValueError("scores 需要 0、1、2、3 四个评分档")
    for value in scores.values():
        if not isinstance(value, dict) or set(value) != {"label", "criteria"}:
            raise ValueError("每个评分档需要 label、criteria")
    required = [config["timezone"], search["scope"], profile["goal"]]
    for band in scores.values():
        required.extend((band["label"], band["criteria"]))
    optional = [search["exclusions"], profile["confirmed"], profile["unknowns"], config["hard_filters"]]
    if any(not isinstance(v, str) or "【" in v or "】" in v for v in required + optional):
        raise ValueError("配置值须为文本，填写或移除所有【】占位符")
    if any(not v.strip() for v in required):
        raise ValueError("时区、搜索范围、求职目标和评分档均须填写")
    try:
        if config["timezone"] != "UTC":
            ZoneInfo(config["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("使用可用的 IANA 时区；缺少时区数据时可使用 UTC 或安装 tzdata") from exc


def settings(db):
    row = db.execute("SELECT revision, config_json FROM workspace_settings WHERE id=1").fetchone()
    return (row["revision"], json.loads(row["config_json"])) if row else (0, None)


def current(db, include_private=False):
    revision, config = settings(db)
    result = {
        "version": VERSION,
        "config_revision": revision,
        "configured": config is not None,
        "timezone": config["timezone"] if config else "UTC",
        "score_labels": [config["scores"][str(i)]["label"] for i in range(4)] if config else DEFAULT_LABELS,
        "jd_qualities": JD_QUALITIES,
        "recruitment_types": RECRUITMENT_TYPES,
        "posting_states": POSTING_STATES,
        "hc_states": HC_STATES,
        "blocker_types": BLOCKER_TYPES,
        "mismatch_types": MISMATCH_TYPES,
    }
    result["fingerprint"] = store.digest(store.packed([result, config]))
    if include_private:
        result["config"] = config
    return result


def check(db, payload):
    contract = current(db)
    if not contract["configured"]:
        raise ValueError("请先通过对话完成配置，再入库或评分")
    if payload.get("policy_fingerprint") != contract["fingerprint"]:
        raise store.Conflict("配置或规则已改变，请重新 prepare")
    return contract


def locate(content, needle):
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if needle in line:
            end = i + 1
            if line.startswith("#"):
                while end < len(lines) and not lines[end].startswith("#"):
                    end += 1
            return i + 1, end, "\n".join(lines[i:end])
    raise ValueError("无法在来源中定位证据片段")


def is_web_url(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    try:
        url = urlparse(value)
        return (url.scheme in ("http", "https") and bool(url.hostname)
                and url.username is None and url.password is None)
    except ValueError:
        return False


def validate_dates(item, fields):
    for field in fields:
        value = item.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(f"{field} 使用 YYYY-MM-DD，未知日期使用 null")
        date.fromisoformat(value)


def validate_tracking(item, source):
    if item["kind"] != "blocked":
        if any(item.get(k) for k in TRACKING_FIELDS):
            raise ValueError("普通待核或已结束记录不能携带技术受阻诊断")
        return
    if item.get("blocker_type") not in BLOCKER_TYPES:
        raise ValueError("技术受阻需要有效 blocker_type")
    quote = item.get("blocker_evidence")
    if not isinstance(quote, str) or not quote.strip() or quote not in source["content"]:
        raise ValueError("blocker_evidence 须逐字来自本批证据")
    if not is_web_url(item.get("blocker_url")):
        raise ValueError("技术受阻需要原始网页 URL")


def validate_job(item, source):
    if item.get("recruitment_type", "unknown") not in RECRUITMENT_TYPES:
        raise ValueError("无效 recruitment_type")
    if item.get("hc_status", "unconfirmed") not in HC_STATES:
        raise ValueError("无效 hc_status")
    if item.get("hc_status") == "confirmed" and not (item.get("status_note") or "").strip():
        raise ValueError("招聘名额已确认时，需要在 status_note 记录来源依据")
    quality = item.get("jd_quality")
    if quality not in JD_QUALITIES:
        raise ValueError("岗位需要明确 JD 完整程度")
    validate_dates(item, ("checked_on", "jd_checked_on", "deadline"))
    if not is_web_url(item.get("source_url")):
        raise ValueError("岗位需要可核查的原始网页 URL")
    if item.get("application_url") and not is_web_url(item["application_url"]):
        raise ValueError("投递入口须为 HTTP(S) URL")
    if quality == "missing":
        if any(item.get(k) for k in ("description", "requirements", "jd_source_url", "jd_checked_on")):
            raise ValueError("未取得 JD 时将原文与 JD 来源字段留空")
        return
    if not is_web_url(item.get("jd_source_url")) or not item.get("jd_checked_on"):
        raise ValueError("JD 原文需要原始网页 URL 和核查日期")
    values = [item.get(k) for k in ("description", "requirements")]
    present = [v for v in values if v and v.strip()]
    if not present or (quality == "full" and len(present) != 2):
        raise ValueError("完整 JD 需要职责和要求；部分 JD 至少需要一段原文")
    normalized_source = " ".join(source["content"].split())
    if any(" ".join(v.split()) not in normalized_source for v in present):
        raise ValueError("JD 字段须来自本批原文")
