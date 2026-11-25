#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_dashboard.py ― 保存済みデータから HTML を作る「描画」スクリプト

  * MCP は呼びません。data/ にあるファイルだけを読みます。
  * デイリー: 単一ファイルの HTML（CSS・JS を埋め込み、外部リソースなし）
  * 週次    : Markdown（ローカル保存用）＋ HTML（閲覧用）＋ Confluence 用の簡易HTML
  * 出力ファイルは上書きしません（同名があれば -2 付きの別ファイル）。

使い方:
  python3 build_dashboard.py --work <作業フォルダ> daily  [--date 日付]
  python3 build_dashboard.py --work <作業フォルダ> weekly [--end 日付]
"""

import argparse
import datetime as dt
import hashlib
import html
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ingest     # noqa: E402  共通関数
import knowledge  # noqa: E402  見立て

KIND_LABEL = {"task": "タスク", "meeting": "会議", "mail": "メール", "chat": "チャット",
              "page": "ページ", "alert": "アラート"}
PRIORITY_LABEL = {"high": "高", "medium": "中", "low": "低"}
WEEKDAY = "月火水木金土日"

# デイリーの区分（上から順に判定し、各レコードは1区分だけに入る）
SECTIONS = [
    ("meetings", "今日の予定"),
    ("action", "要対応"),
    ("waiting", "他者待ち"),
    ("carried", "繰越（取得範囲外の未決着）"),
    ("info", "情報・更新"),
    ("closed", "本日決着"),
]


# ─────────────────────────────────────────────
# 小さな部品
# ─────────────────────────────────────────────
def esc(s):
    """HTML に入れる文字列をエスケープする。"""
    return html.escape(str(s if s is not None else ""), quote=True)


def anchor(rid):
    """レコードIDから HTML 用の短いアンカー名を作る（記号を含む ID でも安全に）。"""
    return "r-" + hashlib.md5(rid.encode("utf-8")).hexdigest()[:10]


def md_date(d):
    return f"{d.month}/{d.day}（{WEEKDAY[d.weekday()]}）"


def due_info(rec, today):
    """期限の表示文字と、超過・当日などの状態を返す。"""
    d = ingest.parse_date(rec.get("due"))
    if not d:
        return None, None
    if rec["kind"] == "meeting":
        t = (rec.get("due") or "")[11:16]
        return (f"{md_date(d)} {t}".strip(), "today" if d == today else None)
    diff = (d - today).days
    if diff < 0:
        return f"期限 {md_date(d)}（{-diff}日超過）", "overdue"
    if diff == 0:
        return f"期限 {md_date(d)}（今日）", "today"
    return f"期限 {md_date(d)}（あと{diff}日）", "soon" if diff <= 2 else None


def score(rec, today, members):
    """並べ替え用の点数（大きいほど上）。"""
    s = 0
    d = ingest.parse_date(rec.get("due"))
    if d and rec["kind"] != "meeting":
        diff = (d - today).days
        s += 50 if diff < 0 else 30 if diff == 0 else 15 if diff <= 2 else 0
    s += {"high": 20, "medium": 5}.get(rec.get("priority"), 0)
    if knowledge.name_hit(rec, members):
        s += 15
    if rec.get("self") in ("owner", "to", "direct", "mentioned"):
        s += 10
    s += min(rec.get("elapsed_days") or 0, 14)
    return s


def section_of(rec, today):
    """レコードをどの区分に入れるか決める。"""
    if rec.get("closed"):
        return "closed"
    if rec["kind"] == "meeting":
        return "meetings"
    if rec.get("carried"):
        return "carried"
    if rec.get("waiting_on"):
        return "waiting"
    d = ingest.parse_date(rec.get("due"))
    if rec.get("action") or (d and (d - today).days <= 2) or \
            (rec["kind"] in ("task", "alert") and rec.get("self") == "owner") or \
            (rec["kind"] in ("mail", "chat") and rec.get("status") in ("unread", "flagged", "open")):
        return "action"
    return "info"


# ─────────────────────────────────────────────
# デイリー HTML
# ─────────────────────────────────────────────
def render_item(rec, today, members, source_labels):
    """1件分の <li> を作る。"""
    due_text, due_state = due_info(rec, today)
    member = knowledge.name_hit(rec, members)
    badges = [f'<span class="badge kind-{esc(rec["kind"])}">{esc(KIND_LABEL.get(rec["kind"], rec["kind"]))}</span>']
    if due_state == "overdue":
        badges.append('<span class="badge warn">期限超過</span>')
    if rec.get("priority") == "high":
        badges.append('<span class="badge warn">優先度 高</span>')
    if member:
        badges.append(f'<span class="badge accent">優先: {esc(member)}</span>')
    if rec.get("stale_source"):
        badges.append('<span class="badge muted">取得失敗のため前回の情報</span>')
    for t in rec.get("tags", [])[:4]:
        badges.append(f'<span class="tag">#{esc(t)}</span>')

    title = esc(rec["title"])
    if rec.get("url"):
        title = f'<a href="{esc(rec["url"])}" target="_blank" rel="noopener noreferrer">{title}</a>'

    meta = []
    if due_text:
        meta.append(f'<span class="due {esc(due_state or "")}">{esc(due_text)}</span>')
    if rec.get("status"):
        meta.append(f"状態 {esc(rec['status'])}")
    if rec.get("waiting_on"):
        meta.append(f"{esc(rec['waiting_on'])}さん待ち")
    if rec.get("elapsed_days") is not None and rec["kind"] != "meeting":
        meta.append(f"{rec['elapsed_days']}日経過")
    if rec.get("carried"):
        meta.append(f"最終取得 {esc(rec.get('last_seen', ''))}")
    if rec.get("people"):
        meta.append(esc("・".join(rec["people"][:3])))
    meta.append(esc(source_labels.get(rec["source"], rec["source"])))

    body = ""
    if rec.get("action"):
        body += f'<p class="item-action">{esc(rec["action"])}</p>'
    if rec.get("summary"):
        body += f'<p class="item-summary">{esc(rec["summary"])}</p>'

    search_text = " ".join(filter(None, [rec["title"], rec.get("summary"), rec.get("action"),
                                         " ".join(rec.get("tags", [])), " ".join(rec.get("people", []))]))
    return (f'<li class="item" id="{anchor(rec["id"])}" data-id="{esc(rec["id"])}" data-kind="{esc(rec["kind"])}" '
            f'data-tags="{esc("|".join(rec.get("tags", [])))}" data-text="{esc(ingest.norm_key(search_text))}">'
            f'<label class="done-toggle" title="済みにする（このブラウザに記憶）"><input type="checkbox" class="done-box" '
            f'aria-label="済みにする"></label>'
            f'<div class="item-body"><div class="item-head">{" ".join(badges)}<span class="item-title">{title}</span></div>'
            f'<div class="item-meta">{" ・ ".join(meta)}</div>{body}</div></li>')


def render_daily(work, cfg, date):
    """デイリーダッシュボードの HTML 文字列を作る。"""
    data = ingest.latest_daily(work, date.isoformat())
    if not data:
        raise SystemExit(f"{date} の日次データがありません。先に ingest.py を実行してください。")
    insight = knowledge.latest_insight(work, date.isoformat())
    if insight is None:  # 見立てが未作成ならここで作る（MCP は使わない）
        knowledge.cmd_insight(work, cfg, date)
        insight = knowledge.latest_insight(work, date.isoformat())

    src_conf = ingest.load_sources(work)
    adapters = src_conf.get("adapters", {})
    source_labels = {s["id"]: f'{adapters.get(s.get("adapter"), {}).get("label", s["id"])}（{s["id"]}）'
                     for s in src_conf.get("sources", [])}
    members = cfg.get("priority_members", [])
    recs = data.get("records", [])

    # 区分ごとに振り分けて並べ替え
    buckets = defaultdict(list)
    for r in recs:
        buckets[section_of(r, date)].append(r)
    buckets["meetings"].sort(key=lambda r: r.get("due") or "")
    for key in ("action", "waiting", "carried", "info", "closed"):
        buckets[key].sort(key=lambda r: -score(r, date, members))

    # 見立て（根拠はページ内リンクにする）
    known = {r["id"] for r in recs}
    ins_html = ""
    for s in (insight or {}).get("sentences", []):
        links = [f'<a href="#{anchor(b)}">{esc(b.split(":", 1)[-1])}</a>' for b in s["basis"] if b in known][:6]
        other = [esc(b) for b in s["basis"] if b not in known][:3]
        basis = "、".join(links + other)
        ins_html += f'<li><span class="insight-text">{esc(s["text"])}</span><span class="basis">根拠: {basis}</span></li>'

    # 取得状況
    status_html = ""
    for sid, st in data.get("sources", {}).items():
        label = esc(source_labels.get(sid, sid))
        if st["status"] == "ok":
            status_html += f'<li class="src ok"><b>{label}</b> {st.get("count", 0)}件</li>'
        elif st["status"] == "empty":
            status_html += f'<li class="src empty"><b>{label}</b> 0件（{esc(st.get("empty_reason"))}）</li>'
        else:
            status_html += f'<li class="src error"><b>{label}</b> 取得失敗: {esc(st.get("error"))}</li>'
    for w in data.get("warnings", []):
        status_html += f'<li class="src warn">注意: {esc(w)}</li>'

    # 区分ごとの一覧
    sections_html = ""
    for key, label in SECTIONS:
        items = buckets.get(key, [])
        inner = "".join(render_item(r, date, members, source_labels) for r in items) or \
            '<li class="empty-note">該当なし</li>'
        sections_html += (f'<section class="panel" data-section="{key}"><h2><button class="collapse" '
                          f'aria-expanded="true">{esc(label)} <span class="count">{len(items)}</span></button></h2>'
                          f'<ul class="items">{inner}</ul></section>')

    # フィルタ用のチップ（種類・重点カテゴリー）
    kinds = Counter(r["kind"] for r in recs)
    kind_chips = "".join(f'<button class="chip" data-filter-kind="{k}">{KIND_LABEL[k]} {kinds[k]}</button>'
                         for k in ingest.KINDS if kinds.get(k))
    cats = [c.get("name") if isinstance(c, dict) else str(c) for c in cfg.get("focus_categories", [])]
    cat_count = Counter(t for r in recs for t in r.get("tags", []) if t in cats)
    cat_chips = "".join(f'<button class="chip cat" data-filter-tag="{esc(c)}">#{esc(c)} {cat_count.get(c, 0)}</button>'
                        for c in cats)

    owner = cfg.get("owner", {}).get("name", "")
    css = (HERE / "dashboard_style.css").read_text(encoding="utf-8")
    js = (HERE / "dashboard_app.js").read_text(encoding="utf-8")
    page_data = json.dumps({"date": date.isoformat(), "version": data.get("engine_version")},
                           ensure_ascii=False).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>デイリー {esc(date.isoformat())}</title>
<style>
{css}
</style>
</head>
<body>
<header class="top">
  <div>
    <h1>デイリー情報ダッシュボード</h1>
    <p class="sub">{esc(md_date(date))} ・ {esc(owner)} ・ 生成 {esc(data.get("generated_at", "")[11:16])} ・ v{esc(data.get("engine_version", ""))}</p>
  </div>
  <button id="theme-toggle" class="ghost" title="表示テーマの切り替え">テーマ</button>
</header>

<section class="panel insight">
  <h2>今日の見立て</h2>
  <ol class="insight-list">{ins_html or '<li>見立てを作成できませんでした。</li>'}</ol>
</section>

<section class="panel status">
  <h2>取得状況</h2>
  <ul class="src-list">{status_html}</ul>
</section>

<nav class="toolbar" aria-label="絞り込み">
  <input id="q" type="search" placeholder="キーワードで絞り込み" aria-label="キーワードで絞り込み">
  <div class="chips">{kind_chips}{cat_chips}</div>
  <label class="switch"><input type="checkbox" id="hide-done"> 済みを隠す</label>
</nav>

<main>
{sections_html}
</main>

<footer class="foot">このページはオフラインで動作します。チェック状態はこのブラウザにのみ保存されます。</footer>
<script type="application/json" id="page-data">{page_data}</script>
<script>
{js}
</script>
</body>
</html>
"""


def cmd_daily(work, cfg, date):
    html_text = render_daily(work, cfg, date)
    out = ingest.write_text_new(Path(work) / "output" / "daily" / f"dashboard-{date.isoformat()}.html", html_text)
    print(json.dumps({"saved": str(out)}, ensure_ascii=False))
    return 0


# ─────────────────────────────────────────────
# 週次まとめ
# ─────────────────────────────────────────────
def weekly_data(work, cfg, end):
    """end を最終日とする7日間の集計を作る。"""
    start = end - dt.timedelta(days=6)
    days = []
    for i in range(7):
        d = start + dt.timedelta(days=i)
        data = ingest.latest_daily(work, d.isoformat())
        if data:
            days.append((d, data))

    latest = {}           # id → 週内で一番新しいレコード
    source_errors = Counter()
    for d, data in days:
        for r in data.get("records", []):
            latest[r["id"]] = r
        for sid, st in data.get("sources", {}).items():
            if st.get("status") == "error":
                source_errors[sid] += 1

    closed = [c for c in ingest.load_closed(work).get("items", [])
              if start.isoformat() <= c.get("closed_on", "") <= end.isoformat()]
    new_items = [r for r in latest.values() if start.isoformat() <= r.get("first_seen", "") <= end.isoformat()
                 and r["kind"] != "meeting"]
    last_day = days[-1][1] if days else {"records": []}
    open_now = [r for r in last_day.get("records", []) if not r.get("closed") and r["kind"] in ("task", "mail", "chat", "alert")]
    open_now.sort(key=lambda r: -(r.get("elapsed_days") or 0))
    meetings = [r for r in latest.values() if r["kind"] == "meeting"
                and start <= (ingest.parse_date(r.get("due")) or dt.date.min) <= end]

    cats = [c.get("name") if isinstance(c, dict) else str(c) for c in cfg.get("focus_categories", [])]
    closed_ids = {c["id"] for c in closed}
    cat_rows = []
    for c in cats:
        ids = [rid for rid, r in latest.items() if c in r.get("tags", [])]
        cat_rows.append((c, len(ids), sum(1 for i in ids if i in closed_ids)))

    insights = []
    for d, _ in days:
        ins = knowledge.latest_insight(work, d.isoformat())
        if ins:
            insights.append((d, [s["text"] for s in ins.get("sentences", [])]))

    iso = end.isocalendar()
    return {
        "start": start, "end": end, "label": f"{iso[0]}-W{iso[1]:02d}",
        "days_with_data": len(days), "source_errors": source_errors, "closed": closed,
        "new_items": new_items, "open_now": open_now, "meetings": meetings,
        "cat_rows": cat_rows, "insights": insights,
    }


def link_md(r):
    return f"[{r['title']}]({r['url']})" if r.get("url") else r["title"]


def weekly_markdown(w, owner):
    """週次まとめの Markdown（事実のみ）。"""
    L = [f"# 週次まとめ {w['label']}（{md_date(w['start'])}〜{md_date(w['end'])}）", ""]
    if owner:
        L += [f"対象: {owner}", ""]
    L += ["## 概要",
          f"- 記録がある日: {w['days_with_data']}/7日",
          f"- 決着: {len(w['closed'])}件 ／ 新規: {len(w['new_items'])}件 ／ 週末時点の未決着: {len(w['open_now'])}件",
          f"- 会議: {len(w['meetings'])}件"]
    if w["source_errors"]:
        L.append("- 取得失敗があった日数: " + "、".join(f"{k} {v}日" for k, v in w["source_errors"].items()))
    L.append("")
    if w["cat_rows"]:
        L += ["## 重点カテゴリー", "", "| カテゴリー | 関連項目 | うち決着 |", "|---|---:|---:|"]
        L += [f"| {c} | {n} | {k} |" for c, n, k in w["cat_rows"]]
        L.append("")
    L += ["## 決着した項目"]
    L += [f"- {c['closed_on'][5:]} {link_md(c)}（{c['source']}）" for c in w["closed"]] or ["- なし"]
    L += ["", "## 未決着のまま残っている項目（経過日数の長い順・上位15件）"]
    L += [f"- {link_md(r)}（{r['source']}、{r.get('elapsed_days', '-')}日経過"
          + (f"、{r['waiting_on']}さん待ち" if r.get("waiting_on") else "") + "）"
          for r in w["open_now"][:15]] or ["- なし"]
    L += ["", "## 今週新たに現れた項目（上位15件）"]
    L += [f"- {r.get('first_seen', '')[5:]} {link_md(r)}（{r['source']}）" for r in w["new_items"][:15]] or ["- なし"]
    L += ["", "## 日々の見立て"]
    for d, sents in w["insights"]:
        L.append(f"- {md_date(d)}: " + " ".join(sents))
    if not w["insights"]:
        L.append("- 記録なし")
    L.append("")
    return "\n".join(L)


def markdown_to_simple_html(md_text):
    """週次 Markdown を、Confluence にも貼れる簡単な HTML に変換する（見出し・箇条書き・表・リンクのみ）。"""
    import re
    out, in_ul, table = [], False, []

    def inline(s):
        s = esc(s)
        return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)

    def flush_table():
        if not table:
            return
        rows = [r for r in table if not re.match(r"^\|[-:| ]+\|$", r)]
        cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
        h = "".join(f"<th>{inline(c)}</th>" for c in cells[0])
        b = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>" for row in cells[1:])
        out.append(f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>")
        table.clear()

    for line in md_text.splitlines():
        if line.startswith("|"):
            table.append(line)
            continue
        flush_table()
        if line.startswith("- "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{inline(line[2:])}</li>")
            continue
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    flush_table()
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)


def cmd_weekly(work, cfg, end):
    w = weekly_data(work, cfg, end)
    owner = cfg.get("owner", {}).get("name", "")
    md_text = weekly_markdown(w, owner)
    body = markdown_to_simple_html(md_text)
    css = (HERE / "dashboard_style.css").read_text(encoding="utf-8")
    page = (f'<!DOCTYPE html>\n<html lang="ja"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>週次まとめ {esc(w["label"])}</title><style>{css}</style></head>'
            f'<body><main class="weekly panel">{body}</main></body></html>')
    base = Path(work) / "output" / "weekly"
    p_md = ingest.write_text_new(base / f"weekly-{w['label']}.md", md_text)
    p_html = ingest.write_text_new(base / f"weekly-{w['label']}.html", page)
    p_conf = ingest.write_text_new(base / f"weekly-{w['label']}.confluence.html", body)
    print(json.dumps({"saved": [str(p_md), str(p_html), str(p_conf)],
                      "title": f"週次まとめ {w['label']}（{w['start'].isoformat()}〜{w['end'].isoformat()}）"},
                     ensure_ascii=False, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description="保存済みデータから HTML を作る（MCP は呼ばない）")
    ap.add_argument("--work", required=True, help="作業フォルダ")
    ap.add_argument("command", choices=["daily", "weekly"])
    ap.add_argument("--date", help="デイリーの対象日 YYYY-MM-DD（省略時は今日）")
    ap.add_argument("--end", help="週次の最終日 YYYY-MM-DD（省略時は今日）")
    args = ap.parse_args()

    cfg = ingest.load_config(args.work)
    today = ingest.now_local(cfg).date()
    if args.command == "daily":
        return cmd_daily(args.work, cfg, dt.date.fromisoformat(args.date) if args.date else today)
    return cmd_weekly(args.work, cfg, dt.date.fromisoformat(args.end) if args.end else today)


if __name__ == "__main__":
    sys.exit(main())
