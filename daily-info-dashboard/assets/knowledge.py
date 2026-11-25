#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
knowledge.py ― 蓄積データの全文検索と「今日の見立て」を作るスクリプト

  * 外部ライブラリなし。日本語は「文字bigram（2文字ずつ）」、英数字は単語で索引を作り、
    BM25 という定番の計算式で関連度を出します。
  * 「今日の見立て」は事実だけを最大3文。各文に根拠（basis＝レコードIDなど）を付けて保存します。
    励まし・一般論・推測は書きません。

使い方:
  python3 knowledge.py --work <作業フォルダ> insight [--date 日付]   … data/insight/<日付>.json を作る
  python3 knowledge.py --work <作業フォルダ> search "検索語" [--top 10] … 蓄積から関連レコードを探す
"""

import argparse
import datetime as dt
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

# 同じフォルダの ingest.py の共通関数を使う
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ingest  # noqa: E402

HISTORY_DAYS = 30        # 検索対象にする過去日数
MAX_SENTENCES = 3        # 見立ての最大文数
RELATED_MIN_SCORE = 3.0  # 「関連あり」とみなす BM25 スコアの下限


# ─────────────────────────────────────────────
# トークン化（文字bigram＋英数字の単語）
# ─────────────────────────────────────────────
def tokenize(text):
    """文章を検索用の語のリストにする。
    例: 『設計レビュー ABC-12』→ ['設計','計レ','レビ','ビュ','ュー','abc','12']"""
    s = unicodedata.normalize("NFKC", text or "").lower()
    tokens = []
    # 英数字のかたまりは単語として扱う
    tokens += re.findall(r"[a-z0-9_]{2,}", s)
    # それ以外（日本語など）は空白・記号で区切ったうえで2文字ずつに切る
    for run in re.split(r"[\sa-z0-9_\W]+", s):
        if len(run) == 1:
            tokens.append(run)
        for i in range(len(run) - 1):
            tokens.append(run[i:i + 2])
    return tokens


def record_text(rec):
    """レコードのうち検索に使う部分をつなげた文章。"""
    parts = [rec.get("title"), rec.get("summary"), rec.get("action"), rec.get("waiting_on"),
             " ".join(rec.get("tags", [])), " ".join(rec.get("people", []))]
    return " ".join(p for p in parts if p)


# ─────────────────────────────────────────────
# BM25 索引
# ─────────────────────────────────────────────
class BM25:
    """シンプルな BM25 検索。docs は (id, 文章) のリスト。"""

    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.ids = [d[0] for d in docs]
        self.tfs = [Counter(tokenize(d[1])) for d in docs]           # 文書ごとの語の出現回数
        self.lens = [sum(tf.values()) for tf in self.tfs]             # 文書の長さ
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0
        df = Counter()                                                # 語が何文書に出るか
        for tf in self.tfs:
            df.update(tf.keys())
        n = len(docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def search(self, query, top=10, allow=None, exclude=None):
        """query に近い文書を (id, スコア) で返す。allow で対象 id を絞り、exclude で除外できる。"""
        q = set(tokenize(query))
        results = []
        for i, tf in enumerate(self.tfs):
            did = self.ids[i]
            if (allow is not None and did not in allow) or (exclude and did in exclude):
                continue
            score = 0.0
            for w in q:
                f = tf.get(w)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * self.lens[i] / (self.avg or 1))
                score += self.idf.get(w, 0) * f * (self.k1 + 1) / denom
            if score > 0:
                results.append((did, round(score, 3)))
        results.sort(key=lambda x: -x[1])
        return results[:top]


def load_corpus(work, date):
    """今日と過去 HISTORY_DAYS 日の日次データ＋closed.json から検索対象を作る。
    同じ id は一番新しい内容を使う。戻り値: (id→レコード, BM25索引)"""
    by_id = {}
    today = ingest.latest_daily(work, date.isoformat())
    days = ([(date.isoformat(), today)] if today else []) + ingest.recent_dailies(work, date, HISTORY_DAYS)
    for ds, data in days:  # 新しい順なので、最初に出たものを残す
        for r in data.get("records", []):
            by_id.setdefault(r["id"], dict(r, _date=ds))
    for c in ingest.load_closed(work).get("items", []):
        by_id.setdefault(c["id"], dict(c, closed=True, _date=c.get("closed_on")))
    index = BM25([(rid, record_text(r)) for rid, r in by_id.items()])
    return by_id, index


# ─────────────────────────────────────────────
# 見立て（事実だけ・最大3文）
# ─────────────────────────────────────────────
def short(title, n=28):
    """文中に入れるタイトルを短くする。"""
    return title if len(title) <= n else title[: n - 1] + "…"


def md(d):
    """date → '11/25' 表記。"""
    return f"{d.month}/{d.day}"


def is_open(r):
    return not r.get("closed")


def name_hit(r, members):
    """優先メンバーの名前がレコードに含まれていればその名前を返す。"""
    hay = ingest.norm_key(" ".join([r.get("title", ""), r.get("summary", ""), r.get("waiting_on", "") or "",
                                    " ".join(r.get("people", []))]))
    for m in members:
        if m and ingest.norm_key(m) in hay:
            return m
    return None


def build_facts(work, cfg, date):
    """今日のデータから「事実」の候補を優先順に作る。各要素: {text, basis, rule}"""
    today = ingest.latest_daily(work, date.isoformat())
    if not today:
        return [], None
    recs = today.get("records", [])
    facts = []

    # 規則1: 取得失敗したソース（データの欠けは最初に伝える）
    failed = [sid for sid, st in today.get("sources", {}).items() if st.get("status") == "error"]
    if failed:
        stale = [r for r in recs if r.get("stale_source")]   # 失敗ソースの前回記録から繰り越した件数
        tail = (f"（前回までの記録から{len(stale)}件を繰越表示）" if stale
                else "（本日分の項目は表示されていない）")
        facts.append({"rule": "source_error",
                      "text": f"{'、'.join(failed)} の取得に失敗した{tail}。",
                      "basis": [f"source:{s}" for s in failed] + [r["id"] for r in stale]})

    # 規則2: 期限切れの未完了タスク
    overdue = [r for r in recs if r["kind"] == "task" and is_open(r)
               and ingest.parse_date(r.get("due")) and ingest.parse_date(r.get("due")) < date]
    if overdue:
        overdue.sort(key=lambda r: r["due"])
        o = overdue[0]
        facts.append({"rule": "overdue",
                      "text": f"期限切れの未完了タスクが{len(overdue)}件あり、最も古いのは「{short(o['title'])}」（期限{md(ingest.parse_date(o['due']))}）。",
                      "basis": [r["id"] for r in overdue]})

    # 規則3: 今日の会議に関連する未決着項目（BM25で過去の記録から探す）
    by_id, index = load_corpus(work, date)
    open_ids = {rid for rid, r in by_id.items() if is_open(r) and r.get("kind") != "meeting"}
    best = None
    for m in [r for r in recs if r["kind"] == "meeting" and is_open(r)
              and ingest.parse_date(r.get("due")) == date]:
        hits = [h for h in index.search(m["title"] + " " + (m.get("summary") or ""), top=5, allow=open_ids)
                if h[1] >= RELATED_MIN_SCORE]
        if hits and (best is None or len(hits) > len(best[1])):
            best = (m, hits)
    if best:
        m, hits = best
        first = by_id[hits[0][0]]
        hhmm = (m.get("due") or "")[11:16]
        when = f"{hhmm}の" if hhmm else ""
        more = f"ほか{len(hits) - 1}件" if len(hits) > 1 else ""
        facts.append({"rule": "meeting_related",
                      "text": f"本日{when}「{short(m['title'], 20)}」に関連する未決着項目として「{short(first['title'], 20)}」{more}がある。",
                      "basis": [m["id"]] + [h[0] for h in hits]})

    # 規則4: 優先メンバーが関わる未対応の項目
    members = cfg.get("priority_members", [])
    pm = [(r, name_hit(r, members)) for r in recs if is_open(r) and r["kind"] in ("mail", "chat", "task")
          and (r.get("action") or r.get("status") in ("unread", "open", "flagged"))]
    pm = [(r, n) for r, n in pm if n]
    if pm:
        r, n = pm[0]
        facts.append({"rule": "priority_member",
                      "text": f"優先メンバーが関わる未対応項目が{len(pm)}件あり、うち1件は{n}さんの「{short(r['title'])}」。",
                      "basis": [x[0]["id"] for x in pm]})

    # 規則5: 今日が期限のタスク
    due_today = [r for r in recs if r["kind"] == "task" and is_open(r) and ingest.parse_date(r.get("due")) == date]
    if due_today:
        names = "」「".join(short(r["title"], 18) for r in due_today[:2])
        tail = f"ほか{len(due_today) - 2}件" if len(due_today) > 2 else ""
        facts.append({"rule": "due_today",
                      "text": f"本日期限のタスクは{len(due_today)}件（「{names}」{tail}）。",
                      "basis": [r["id"] for r in due_today]})

    # 規則6: 他者待ちの長いもの
    waiting = [r for r in recs if is_open(r) and r.get("waiting_on")]
    if waiting:
        waiting.sort(key=lambda r: -(r.get("elapsed_days") or 0))
        w = waiting[0]
        days = f"（{w['elapsed_days']}日経過）" if w.get("elapsed_days") is not None else ""
        facts.append({"rule": "waiting",
                      "text": f"他者待ちが{len(waiting)}件あり、最も長いのは「{short(w['title'])}」の{w['waiting_on']}さん待ち{days}。",
                      "basis": [r["id"] for r in waiting]})

    # 規則7: 繰越中の未決着項目
    carried = [r for r in recs if r.get("carried") and is_open(r)]
    if carried:
        facts.append({"rule": "carried",
                      "text": f"取得範囲外になった未決着項目が{len(carried)}件あり、直近7日の記録から繰越している。",
                      "basis": [r["id"] for r in carried]})

    return facts, today


def cmd_insight(work, cfg, date):
    """見立てを作って data/insight/<日付>.json に保存する（同日の再実行は別名）。"""
    facts, today = build_facts(work, cfg, date)
    if today is None:
        print(json.dumps({"error": f"{date} の日次データがありません。先に ingest を実行してください。"},
                         ensure_ascii=False))
        return 1
    chosen = facts[:MAX_SENTENCES]
    if not chosen:
        n = len(today.get("records", []))
        chosen = [{"rule": "none",
                   "text": f"本日の取得{n}件に、期限切れ・他者待ち・繰越・取得失敗の該当はない。",
                   "basis": ["count:records"]}]
    out = {
        "date": date.isoformat(),
        "generated_at": ingest.now_local(cfg).isoformat(timespec="seconds"),
        "sentences": [{"text": f["text"], "basis": f["basis"], "rule": f["rule"]} for f in chosen],
        "candidates_not_shown": [f["rule"] for f in facts[MAX_SENTENCES:]],
    }
    path = ingest.free_path(Path(work) / "data" / "insight" / f"{date.isoformat()}.json")
    ingest.write_json_atomic(path, out)
    print(json.dumps(dict(out, saved=str(path)), ensure_ascii=False, indent=2))
    return 0


def latest_insight(work, date_str):
    """その日の最新の見立てファイルを読む（無ければ None）。"""
    d = Path(work) / "data" / "insight"
    files = [d / f"{date_str}.json"] + sorted(d.glob(f"{date_str}-*.json"),
                                             key=lambda p: int(p.stem.rsplit("-", 1)[1]) if p.stem.rsplit("-", 1)[1].isdigit() else 0)
    files = [f for f in files if f.exists()]
    return ingest.load_json(files[-1]) if files else None


def cmd_search(work, cfg, date, query, top):
    """蓄積から関連レコードを探して表示する。"""
    by_id, index = load_corpus(work, date)
    hits = index.search(query, top=top)
    print(json.dumps([{"id": rid, "score": s, "date": by_id[rid].get("_date"),
                       "kind": by_id[rid].get("kind"), "title": by_id[rid].get("title"),
                       "closed": bool(by_id[rid].get("closed")), "url": by_id[rid].get("url")}
                      for rid, s in hits], ensure_ascii=False, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description="蓄積データの検索と今日の見立て")
    ap.add_argument("--work", required=True, help="作業フォルダ")
    ap.add_argument("command", choices=["insight", "search"])
    ap.add_argument("query", nargs="?", default="", help="search のときの検索語")
    ap.add_argument("--date", help="対象日 YYYY-MM-DD（省略時は今日）")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    cfg = ingest.load_config(args.work)
    date = dt.date.fromisoformat(args.date) if args.date else ingest.now_local(cfg).date()
    if args.command == "insight":
        return cmd_insight(args.work, cfg, date)
    return cmd_search(args.work, cfg, date, args.query, args.top)


if __name__ == "__main__":
    sys.exit(main())
