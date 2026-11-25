#!/usr/bin/env python3
"""Local command line for JobClaw."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))

import discovery
import policy
import preferences
import store
import sync


def initialize():
    (store.WORKSPACE / "inbox").mkdir(parents=True, exist_ok=True)
    store.initialize()


def configure(db, config, expected):
    policy.validate_config(config)
    revision, previous = policy.settings(db)
    if revision != expected:
        raise store.Conflict("配置版本已变化，请读取 status 后再提交")
    if config != previous:
        revision += 1
        db.execute("""INSERT INTO workspace_settings VALUES(1,?,?) ON CONFLICT(id) DO UPDATE
                      SET revision=excluded.revision, config_json=excluded.config_json""",
                   (revision, store.packed(config)))
    return {"config_revision": revision, "policy_fingerprint": policy.current(db)["fingerprint"]}


def read_object(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("输入文件须包含 JSON 对象")
    return value


def run(args):
    initialize()
    if args.command == "serve":
        from server import serve
        serve(args.port)
        return None
    if args.command in ("init", "export-ledger"):
        return discovery.export_ledger()
    if args.command == "status":
        with store.connection() as db:
            db.execute("BEGIN")
            return {"workspace": str(store.WORKSPACE), "inbox": str(store.WORKSPACE / "inbox"),
                    "policy": policy.current(db), "review_pending": preferences.snapshot(db)["pending"]}
    if args.command == "configure":
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            result = configure(db, read_object(args.input), args.expected_revision)
        return {**result, "scan_ledger": discovery.export_after_commit()}
    workflow = sync if args.command == "sync" else preferences
    if args.action == "prepare":
        with store.connection() as db:
            db.execute("BEGIN")
            return workflow.prepare(db)
    if args.input is None:
        raise ValueError("preview / apply 需要 --input")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        result = workflow.commit(db, read_object(args.input))
        if args.action == "preview":
            db.rollback()
    if args.command == "sync" and args.action == "apply":
        result["scan_ledger"] = discovery.export_after_commit()
    return {**result, "preview": args.action == "preview"}


def main():
    # Preserve UTF-8 JSON when redirecting the CLI on any supported platform.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "status", "export-ledger"):
        commands.add_parser(name)
    serve_cmd = commands.add_parser("serve")
    serve_cmd.add_argument("--port", type=int, default=18765)
    config_cmd = commands.add_parser("configure")
    config_cmd.add_argument("--input", type=Path, required=True)
    config_cmd.add_argument("--expected-revision", required=True, type=int)
    for name in ("sync", "review"):
        sub = commands.add_parser(name)
        sub.add_argument("action", choices=("prepare", "preview", "apply"))
        sub.add_argument("--input", type=Path)
        sub.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = run(args)
        if result is not None:
            output = getattr(args, "output", None)
            text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            if output:
                output.write_text(text, encoding="utf-8")
            else:
                print(text, end="")
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"JobClaw：{exc}\n")


if __name__ == "__main__":
    main()
