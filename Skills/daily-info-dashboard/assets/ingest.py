#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ingest.py ― 収集フェーズの結果（inbox）を「正規レコード」に整えて日次保存するスクリプト

  * MCP（Outlook・JIRA などへの接続）は一切呼びません。標準ライブラリだけで動きます。
  * 既存データは削除も上書きもしません（同じ日に再実行したら「-2」付きの別ファイルにします）。

使い方（作業フォルダを --work で渡します）:
  python3 ingest.py --work <作業フォルダ> init                 … 初回だけ。フォルダと persistence-check.json を作る
  python3 ingest.py --work <作業フォルダ> check                … 蓄積が前回から残っているか確認する
  python3 ingest.py --work <作業フォルダ> prepare [--date 日付] … 収集結果の書き込み先 inbox フォルダを用意する
  python3 ingest.py --work <作業フォルダ> ingest [--date 日付]  … inbox を検証・正規化して data/daily に保存する

ほかの2本（knowledge.py / build_dashboard.py）もこのファイルの共通関数を import して使います。
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
import unicodedata
import uuid
from pathlib import Path

# ─────────────────────────────────────────────
# 定数（レコードの決まりごと。references/record-schema.md と対応）
# ─────────────────────────────────────────────
KINDS = ["task", "meeting", "mail", "chat", "page", "alert"]          # kind に使える値
REQUIRED_FIELDS = ["id", "source", "kind", "title"]                  # 必須項目
OPTIONAL_FIELDS = ["url", "status", "self", "due", "updated", "priority", "tags",
                   "summary", "action", "waiting_on", "elapsed_days", "people"]  # 任意項目
MANAGED_FIELDS = ["first_seen", "last_seen", "carried", "carried_from",
                  "stale_source", "closed"]                          # ingest が付ける管理項目
CARRY_DAYS = 7                                                       # 繰越を探す日数
SUMMARY_MAX = 120                                                    # summary の最大文字数

# 優先度の表記ゆれを high / medium / low にそろえる対応表
PRIORITY_MAP = {
    "highest": "high", "high": "high", "urgent": "high", "critical": "high", "blocker": "high",
    "最高": "high", "高": "high", "緊急": "high", "重要": "high",
    "medium": "medium", "normal": "medium", "中": "medium", "通常": "medium",
    "low": "low", "lowest": "low", "minor": "low", "trivial": "low", "低": "low", "最低": "low",
}


# ─────────────────────────────────────────────
# ファイル読み書きの共通関数
# ─────────────────────────────────────────────
def load_json(path, default=None):
    """JSONファイルを読む。ファイルが無ければ default を返す。"""
    p = Path(path)
    if not p.exists():
        return default
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json_atomic(path, data):
    """JSONを一時ファイルに書いてから置き換える（書き込み途中で壊れないように）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(p.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def free_path(path):
    """path が既にあれば name-2.ext, name-3.ext … の空いている名前を返す（上書き防止）。"""
    p = Path(path)
    if not p.exists():
        return p
    n = 2
    while True:
        cand = p.with_name(f"{p.stem}-{n}{p.suffix}")
        if not cand.exists():
            return cand
        n += 1


def write_text_new(path, text):
    """テキストを新規ファイルとして書く（既存なら別名にする）。書いたパスを返す。"""
    p = free_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ─────────────────────────────────────────────
# 設定と日付
# ─────────────────────────────────────────────
def load_config(work):
    """作業フォルダの config.json を読む（無ければ最低限の既定値）。"""
    cfg = load_json(Path(work) / "config.json", {}) or {}
    cfg.setdefault("timezone", "Asia/Tokyo")
    cfg.setdefault("owner", {})
    cfg.setdefault("focus_categories", [])
    cfg.setdefault("priority_members", [])
    return cfg


def load_sources(work):
    """作業フォルダの sources.json を読む。"""
    data = load_json(Path(work) / "sources.json")
    if data is None:
        sys.exit("sources.json がありません。セットアップを先に行ってください。")
    return data


def now_local(cfg):
    """設定のタイムゾーンで現在時刻を返す（tzdata が無い環境ではPCの時刻）。"""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(cfg.get("timezone", "Asia/Tokyo")))
    except Exception:
        return dt.datetime.now().astimezone()


def parse_date(value):
    """'2026-11-25' や '2026-11-25T10:00:00+09:00' から日付(date)を取り出す。だめなら None。"""
    if not value or not isinstance(value, str):
        return None
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", value.strip())
    if not m:
        m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", value.strip())
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def normalize_datetime(value):
    """日時文字列を ISO 8601 にそろえる（日付だけならそのまま YYYY-MM-DD）。"""
    if not value or not isinstance(value, str):
        return None
    s = value.strip().replace("/", "-")
    try:
        if len(s) <= 10:
            return parse_date(s).isoformat()
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
    except Exception:
        d = parse_date(s)
        return d.isoformat() if d else None


# ─────────────────────────────────────────────
# 日次ファイルの場所
# ─────────────────────────────────────────────
def daily_dir(work):
    return Path(work) / "data" / "daily"


def daily_files_for(work, date_str):
    """その日の日次ファイル（再実行分 -2, -3 を含む）を古い順に返す。"""
    d = daily_dir(work)
    files = []
    if (d / f"{date_str}.json").exists():
        files.append(d / f"{date_str}.json")
    rev = sorted(d.glob(f"{date_str}-*.json"),
                 key=lambda p: int(p.stem.rsplit("-", 1)[1]) if p.stem.rsplit("-", 1)[1].isdigit() else 0)
    files += [p for p in rev if p.stem.rsplit("-", 1)[1].isdigit()]
    return files


def latest_daily(work, date_str):
    """その日の最新の日次データを返す（無ければ None）。"""
    files = daily_files_for(work, date_str)
    return load_json(files[-1]) if files else None


def recent_dailies(work, end_date, days):
    """end_date を含まない直近 days 日分の日次データを [(日付文字列, データ)] で新しい順に返す。"""
    out = []
    for i in range(1, days + 1):
        ds = (end_date - dt.timedelta(days=i)).isoformat()
        data = latest_daily(work, ds)
        if data:
            out.append((ds, data))
    return out


def load_closed(work):
    """closed.json（決着済みの記録。追記のみ）を読む。"""
    return load_json(Path(work) / "data" / "closed.json", {"version": 1, "items": []})


# ─────────────────────────────────────────────
# レコードの正規化
# ─────────────────────────────────────────────
def clean_text(value, limit=None):
    """文字列の前後空白・改行を整え、必要なら limit 文字で切る。"""
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    if not s:
        return None
    if limit and len(s) > limit:
        s = s[: limit - 1] + "…"
    return s


def as_list(value):
    """文字列・リストのどちらで来ても文字列リストにそろえる。"""
    if value is None:
        return []
    if isinstance(value, str):
        value = [v for v in re.split(r"[,、]", value)]
    return [clean_text(v) for v in value if clean_text(v)]


def norm_key(text):
    """比較用に、全角半角・大文字小文字の違いをなくす。"""
    return unicodedata.normalize("NFKC", text or "").lower()


def is_closed_status(status, closed_statuses):
    """status が「決着済み」を表すかどうか。"""
    if not status:
        return False
    return norm_key(status) in {norm_key(s) for s in closed_statuses}


def normalize_record(raw, source, adapter, cfg, today):
    """inbox の1件を正規レコードにする。直せない問題があれば (None, 理由) を返す。"""
    if not isinstance(raw, dict):
        return None, "レコードがオブジェクトではない"

    rec = {}
    # 1) 必須項目: id は「ソースID:元のID」の形にそろえる
    native_id = clean_text(raw.get("id"))
    if not native_id:
        return None, "id がない"
    prefix = source["id"] + ":"
    rec["id"] = native_id if native_id.startswith(prefix) else prefix + native_id
    rec["source"] = source["id"]
    kind = clean_text(raw.get("kind")) or adapter.get("default_kind")
    if kind not in KINDS:
        return None, f"kind が不正（{kind}）"
    rec["kind"] = kind
    title = clean_text(raw.get("title"), 200)
    if not title:
        return None, "title がない"
    rec["title"] = title

    # 2) 任意項目: 型をそろえる
    rec["url"] = clean_text(raw.get("url"))
    rec["status"] = clean_text(raw.get("status"))
    rec["self"] = clean_text(raw.get("self"))
    rec["due"] = normalize_datetime(raw.get("due"))
    rec["updated"] = normalize_datetime(raw.get("updated"))
    pr = clean_text(raw.get("priority"))
    rec["priority"] = PRIORITY_MAP.get(norm_key(pr), None) if pr else None
    rec["tags"] = as_list(raw.get("tags"))
    rec["summary"] = clean_text(raw.get("summary"), SUMMARY_MAX)
    rec["action"] = clean_text(raw.get("action"), SUMMARY_MAX)
    rec["waiting_on"] = clean_text(raw.get("waiting_on"))
    rec["people"] = as_list(raw.get("people"))

    # 3) elapsed_days: 指定が無ければ updated からの経過日数を計算
    ed = raw.get("elapsed_days")
    if isinstance(ed, (int, float)) and ed >= 0:
        rec["elapsed_days"] = int(ed)
    else:
        ud = parse_date(rec["updated"])
        rec["elapsed_days"] = (today - ud).days if ud and ud <= today else None

    # 4) 重点カテゴリー: キーワードが含まれていればタグを付ける
    hay = norm_key(" ".join(filter(None, [rec["title"], rec["summary"], " ".join(rec["tags"])])))
    for cat in cfg.get("focus_categories", []):
        name = cat.get("name") if isinstance(cat, dict) else str(cat)
        words = (cat.get("keywords") if isinstance(cat, dict) else None) or [name]
        if name and any(norm_key(w) in hay for w in words if w) and name not in rec["tags"]:
            rec["tags"].append(name)

    # 5) 決着済みかどうか
    rec["closed"] = is_closed_status(rec["status"], adapter.get("closed_statuses", []))

    # 6) 空の任意項目は保存しない（ファイルを読みやすくするため）
    rec = {k: v for k, v in rec.items() if v not in (None, [], "")}
    rec.setdefault("closed", False)
    return rec, None


# ─────────────────────────────────────────────
# サブコマンド: init
# ─────────────────────────────────────────────
def cmd_init(work, cfg):
    """フォルダを作り、persistence-check.json を（無ければ）作る。既存は触らない。"""
    for sub in ["inbox", "data/daily", "data/insight", "output/daily", "output/weekly", "logs"]:
        (Path(work) / sub).mkdir(parents=True, exist_ok=True)
    pc_path = Path(work) / "persistence-check.json"
    if pc_path.exists():
        print(json.dumps({"status": "exists", "message": "persistence-check.json は既にあります（変更していません）",
                          "data": load_json(pc_path)}, ensure_ascii=False, indent=2))
        return 0
    data = {
        "token": uuid.uuid4().hex,
        "created_at": now_local(cfg).isoformat(timespec="seconds"),
        "work_dir": str(Path(work).resolve()),
        "runs": 0,
        "last_run": None,
    }
    write_json_atomic(pc_path, data)
    print(json.dumps({"status": "created", "data": data}, ensure_ascii=False, indent=2))
    return 0


# ─────────────────────────────────────────────
# サブコマンド: check（蓄積が残っているか）
# ─────────────────────────────────────────────
def cmd_check(work, cfg):
    """persistence-check.json が残っていれば ok。消えていれば missing（終了コード2）。"""
    pc_path = Path(work) / "persistence-check.json"
    pc = load_json(pc_path)
    history = sorted({p.stem[:10] for p in daily_dir(work).glob("*.json")}) if daily_dir(work).exists() else []
    if not pc:
        # 見つからない＝前回の蓄積が消えた可能性。作り直したうえで「初期化を検知」と記録する
        # （同じ日の ingest がこの記録を見て、ダッシュボードに警告を出す）
        now = now_local(cfg).isoformat(timespec="seconds")
        write_json_atomic(pc_path, {"token": uuid.uuid4().hex, "created_at": now,
                                    "work_dir": str(Path(work).resolve()), "runs": 1,
                                    "last_run": now, "reset_detected_at": now})
        print(json.dumps({"status": "missing",
                          "message": "persistence-check.json が見つかりませんでした（作り直しました）。作業フォルダが実行のたびに消える場所の可能性があります。",
                          "history_days": len(history)}, ensure_ascii=False, indent=2))
        return 2
    # 実行回数と最終実行時刻だけを更新する（管理用の項目のみ）
    pc["runs"] = int(pc.get("runs", 0)) + 1
    prev = pc.get("last_run")
    pc["last_run"] = now_local(cfg).isoformat(timespec="seconds")
    moved = pc.get("work_dir") != str(Path(work).resolve())
    write_json_atomic(pc_path, pc)
    print(json.dumps({"status": "ok", "created_at": pc.get("created_at"), "previous_run": prev,
                      "runs": pc["runs"], "history_days": len(history),
                      "oldest": history[0] if history else None,
                      "work_dir_moved": moved}, ensure_ascii=False, indent=2))
    return 0


# ─────────────────────────────────────────────
# サブコマンド: ingest
# ─────────────────────────────────────────────
def inbox_dirs_for(work, date_str):
    """その日の inbox フォルダ（再収集分 -2, -3 を含む）を古い順に返す。"""
    base = Path(work) / "inbox"
    dirs = [base / date_str] if (base / date_str).is_dir() else []
    rev = [p for p in base.glob(f"{date_str}-*") if p.is_dir() and p.name.rsplit("-", 1)[1].isdigit()]
    return dirs + sorted(rev, key=lambda p: int(p.name.rsplit("-", 1)[1]))


def cmd_prepare(work, date_str):
    """収集フェーズの書き込み先フォルダを用意して表示する。
    その日の inbox が既にあれば、上書きしないよう <日付>-2 のような新しいフォルダを作る。"""
    base = Path(work) / "inbox"
    target = base / date_str
    n = 2
    while target.exists():
        target = base / f"{date_str}-{n}"
        n += 1
    target.mkdir(parents=True)
    print(json.dumps({"inbox_dir": str(target)}, ensure_ascii=False))
    return 0


def read_envelope(path):
    """inbox の1ソース分を読む。壊れていればエラー情報を返す。"""
    try:
        return load_json(path), None
    except Exception as e:  # JSON の書式エラーなど
        return None, f"inbox ファイルを読めません: {e}"


def cmd_ingest(work, cfg, date_str):
    today = dt.date.fromisoformat(date_str)
    src_conf = load_sources(work)
    adapters = src_conf.get("adapters", {})
    dirs = inbox_dirs_for(work, date_str)          # 一番新しい収集結果を使う
    inbox = dirs[-1] if dirs else Path(work) / "inbox" / date_str

    source_status = {}   # ソースごとの取得結果（ok / empty / error）
    today_records = {}   # id → レコード
    warnings = []

    # ── 1) 有効な各ソースの inbox を検証して取り込む
    for source in src_conf.get("sources", []):
        if not source.get("enabled", True):
            continue
        sid = source["id"]
        adapter = adapters.get(source.get("adapter"), {})
        if not adapter:
            source_status[sid] = {"status": "error", "error": f"アダプタ『{source.get('adapter')}』が sources.json にありません"}
            continue
        env, err = read_envelope(inbox / f"{sid}.json")
        if env is None and err is None:
            err = "収集結果ファイルがありません（収集フェーズで書かれていない）"
        if err:
            source_status[sid] = {"status": "error", "error": err}
            continue

        # 取得失敗: ok=false のときは error を必須にする
        if env.get("ok") is False:
            source_status[sid] = {"status": "error",
                                  "error": clean_text(env.get("error")) or "取得失敗（理由の記載なし）"}
            continue

        raw_records = env.get("records") or []
        # 0件: empty_reason が無ければ「失敗かもしれない」ので error 扱いにする
        if not raw_records:
            reason = clean_text(env.get("empty_reason"))
            if reason:
                source_status[sid] = {"status": "empty", "count": 0, "empty_reason": reason}
            else:
                source_status[sid] = {"status": "error",
                                      "error": "0件なのに empty_reason がありません（取得失敗の可能性）"}
            continue

        ok_count, bad = 0, []
        for raw in raw_records:
            rec, why = normalize_record(raw, source, adapter, cfg, today)
            if rec is None:
                bad.append(why)
                continue
            rec["first_seen"] = date_str
            rec["last_seen"] = date_str
            today_records[rec["id"]] = rec   # 同じ id が2回来たら後のものを使う
            ok_count += 1
        source_status[sid] = {"status": "ok", "count": ok_count,
                              "fetched_at": env.get("fetched_at")}
        if bad:
            source_status[sid]["skipped"] = len(bad)
            warnings.append(f"{sid}: {len(bad)}件をスキップ（{'; '.join(sorted(set(bad)))}）")

    # ── 1.5) 蓄積領域の初期化を今日検知していたら警告に加える
    pc = load_json(Path(work) / "persistence-check.json") or {}
    if str(pc.get("reset_detected_at", "")).startswith(date_str):
        warnings.append("persistence-check.json が見つからず作り直しました。過去の蓄積が失われている可能性があります（繰越・見立ては今日のデータのみ）。")
    elif not pc:
        warnings.append("persistence-check.json がありません（init 未実行）。")

    # ── 2) 直近7日の記録と照合（first_seen の引き継ぎ・繰越）
    closed = load_closed(work)
    closed_ids = {c["id"] for c in closed.get("items", [])}
    history = recent_dailies(work, today, CARRY_DAYS)   # 新しい順

    last_known = {}   # id → (最後に見た日, レコード)  ※新しい日を優先
    for ds, data in history:
        for r in data.get("records", []):
            last_known.setdefault(r["id"], (r.get("last_seen", ds), r))

    # 今日も取れた項目は first_seen を過去から引き継ぐ
    for rid, rec in today_records.items():
        if rid in last_known:
            rec["first_seen"] = last_known[rid][1].get("first_seen", rec["first_seen"])

    # 今日取れなかった未決着項目を「繰越」として再掲する
    failed_sources = {sid for sid, st in source_status.items() if st["status"] == "error"}
    carried_count = 0
    for rid, (seen, old) in last_known.items():
        if rid in today_records or rid in closed_ids or old.get("closed"):
            continue
        adapter = adapters.get(next((s.get("adapter") for s in src_conf.get("sources", [])
                                     if s["id"] == old.get("source")), ""), {})
        if not adapter.get("carry", False):
            continue  # 予定やページは繰り越さない
        seen_date = parse_date(seen)
        if seen_date and (today - seen_date).days > CARRY_DAYS:
            continue  # 最後に実際に取得できた日から7日を過ぎたら繰越をやめる
        rec = dict(old)
        rec["carried"] = True
        rec["carried_from"] = rec.get("carried_from") or seen
        rec["last_seen"] = seen
        if old.get("source") in failed_sources:
            rec["stale_source"] = True   # 今日そのソースが失敗したので古い情報のまま
        else:
            rec.pop("stale_source", None)
        ud = parse_date(rec.get("updated"))
        if ud:
            rec["elapsed_days"] = (today - ud).days
        today_records[rid] = rec
        carried_count += 1

    # ── 3) 今日決着したものを closed.json に追記（既にある id は追記しない）
    newly_closed = []
    for rec in today_records.values():
        if rec.get("closed") and rec["id"] not in closed_ids:
            item = {k: rec.get(k) for k in ["id", "source", "kind", "title", "status", "url", "first_seen"] if rec.get(k)}
            item["closed_on"] = date_str
            newly_closed.append(item)
            closed_ids.add(rec["id"])
    if newly_closed:
        before = len(closed.get("items", []))
        closed.setdefault("items", []).extend(newly_closed)
        assert len(closed["items"]) == before + len(newly_closed)  # 追記のみであることを確認
        write_json_atomic(Path(work) / "data" / "closed.json", closed)

    # ── 4) 日次ファイルとして保存（同じ日の再実行は -2 付きの別ファイル）
    daily = {
        "date": date_str,
        "generated_at": now_local(cfg).isoformat(timespec="seconds"),
        "engine_version": read_version(),
        "owner": cfg.get("owner", {}),
        "inbox": inbox.name,
        "sources": source_status,
        "warnings": warnings,
        "records": sorted(today_records.values(), key=lambda r: (r["kind"], r["id"])),
    }
    out = free_path(daily_dir(work) / f"{date_str}.json")
    write_json_atomic(out, daily)

    # ── 5) 実行ログに1行追記
    log = Path(work) / "logs" / "run-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": daily["generated_at"], "step": "ingest", "file": out.name,
                            "sources": {k: v["status"] for k, v in source_status.items()}},
                           ensure_ascii=False) + "\n")

    summary = {
        "saved": str(out),
        "records": len(today_records),
        "carried": carried_count,
        "newly_closed": len(newly_closed),
        "sources": source_status,
        "warnings": warnings,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def read_version():
    """このスクリプトと同じフォルダの VERSION を読む。"""
    v = Path(__file__).with_name("VERSION")
    return v.read_text(encoding="utf-8").strip() if v.exists() else "unknown"


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="収集結果を正規レコードにして日次保存する")
    ap.add_argument("--work", required=True, help="作業フォルダ")
    ap.add_argument("command", choices=["init", "check", "prepare", "ingest"])
    ap.add_argument("--date", help="対象日 YYYY-MM-DD（省略時は今日）")
    args = ap.parse_args()

    cfg = load_config(args.work)
    date_str = args.date or now_local(cfg).date().isoformat()
    if args.command == "init":
        return cmd_init(args.work, cfg)
    if args.command == "check":
        return cmd_check(args.work, cfg)
    if args.command == "prepare":
        return cmd_prepare(args.work, date_str)
    return cmd_ingest(args.work, cfg, date_str)


if __name__ == "__main__":
    sys.exit(main())
