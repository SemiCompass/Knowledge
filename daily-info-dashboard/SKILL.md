---
name: daily-info-dashboard
description: Outlook予定・メール・Teams・JIRA・Confluenceから自分専用の「デイリー情報ダッシュボード」と「週次まとめ」を作る定期タスク2本を登録し、その実行手順も担う。「デイリーダッシュボードを設定して」「週次まとめを作って」や定期タスクからの呼び出しで使う。
---

# デイリー情報ダッシュボード／週次まとめ

このスキルには3つの使い方があります。依頼内容で選んでください。

| モード | いつ | 節 |
|---|---|---|
| **セットアップ** | 「設定して」「登録して」など初回・設定変更 | A |
| **デイリー実行** | 定期タスク（毎朝）から | B |
| **週次実行** | 定期タスク（週1）から | C |

全体の考え方:
* **収集（MCP呼び出し）と描画（Python）を分ける**。描画フェーズでは MCP を呼ばない。
* 全ソースを共通の「正規レコード」にして日次保存する → `references/record-schema.md`
* 「0件」と「取得失敗」を区別する（0件は `empty_reason`、失敗は `error` を必ず付ける）。
* 取得範囲から消えた未決着は直近7日から「繰越」として再掲。決着済みは `closed.json` に追記のみ。
* 蓄積は次回も残る場所に置き、`persistence-check.json` で確かめる。
* ダッシュボード冒頭の「今日の見立て」は、蓄積の全文検索（文字bigram＋BM25）で引いた**事実のみ3文以内**。根拠は `basis` に残す。

## 守ること（全モード共通）

* **既存データの削除・上書きはユーザーに確認してから**。スクリプトは同名ファイルがあれば `-2` 付きの別名で保存し、上書きしない。
  セットアップで既存の `config.json`・`sources.json`・`engine/` を置き換えるときは必ず確認する。
* **Confluence への出力は下書きのみ**。公開（publish）は絶対にしない。下書きで作れない場合はローカル保存に切り替える。
* ダッシュボードは単一ファイル HTML。配色は CSS 変数、localStorage のキー（`dailyInfoDashboard.state.v1`）は変えない。外部リソースを参照しない。
  詳細 → `references/html-design.md`
* 出力・会話は日本語。

## ファイル構成

```
<このスキル>/
  SKILL.md
  assets/   ingest.py  build_dashboard.py  knowledge.py  dashboard_style.css  dashboard_app.js  sources.json  VERSION
  references/   record-schema.md  source-adapters.md  html-design.md

<作業フォルダ>/                      ← セットアップで作る。蓄積はすべてここ
  config.json  sources.json  persistence-check.json
  engine/      ← assets と SKILL.md・references の写し（定期タスクはここを読む）
  inbox/<日付>/<ソースID>.json        ← 収集結果
  data/daily/<日付>.json  data/closed.json  data/insight/<日付>.json
  output/daily/dashboard-<日付>.html  output/weekly/weekly-<YYYY-Www>.{md,html,confluence.html}
  logs/run-log.jsonl
```

`assets/` が見当たらない場合（SKILL.md だけが登録されている場合）は、GitHub から取得する:
`git clone https://github.com/SemiCompass/Knowledge.git` → `daily-info-dashboard/` を使う。

---

## A. セットアップ

### A-1. 前提チェック（質問の前に黙って確認する）

1. **Python**: `python3 --version`（Windows は `py -3 --version`）で 3.9 以上。使えたコマンド名を控える（以下 `PY`）。
2. **接続（MCP）**: Microsoft 365（予定表・メール・Teams）と Atlassian があるか ToolSearch で確認。
   Atlassian はアクセス可能なサイト一覧を取得しておく（ヒアリングの選択肢に使う）。無い接続は SearchMcpRegistry → SuggestConnectors で提案。
3. **本人情報**: M365 のプロフィール取得ツール（`get_me` など）で氏名・メールを取得。**ユーザーには聞かない**。
4. **定期タスク作成ツール**: 使えるスケジュール系ツール（create_trigger など）を確認。無ければその旨を伝えて、手動実行用の手順だけ用意する。
5. **保存場所**: 作業フォルダは「次回の実行でも残る場所」でなければならない。ユーザーのPC上のフォルダ（デスクトップアプリで接続したフォルダ）を使う。
   一時領域（`/tmp`、クラウド作業領域、セッションごとに消えるフォルダ）は不可。

### A-2. ヒアリング（これだけ聞く）

AskUserQuestion で2回に分けて聞く。選択式にできるものは選択肢を出し、自由入力は「その他」欄で受ける。

1回目（選択）
* **使うソース**（複数選択）: Outlook予定表／Outlookメール／Teamsチャット／JIRA（サイトごと）／Confluence（サイトごと）
* **週次の出力先**: ローカル保存（推奨）／Confluence下書き（＋ローカル保存）
* **スケジュール**: 例「平日 7:40 にデイリー・金曜 17:20 に週次（推奨）」／「毎日 8:10・月曜 8:20 に週次」／その他
* **作成先フォルダ**: 接続済みフォルダの候補＋「その他（パス入力）」

2回目（自由入力。該当ソースを選んだ場合だけ聞く）
* **JIRAの追加JQL**（任意。空なら基本JQLのみ）
* **Confluenceのスペースキー**（カンマ区切り）／週次を下書きにする場合は置き場所のスペースキーと親ページ（任意）
* **重点カテゴリー**（例「イメージセンサ: CIS, 画素」のように 名前: キーワード）
* **優先メンバー**（名前をカンマ区切り）

### A-3. 置換マップの確定

ヒアリング結果を下の置換マップにまとめ、**表にしてユーザーに見せ、OKをもらってから**ファイルを作る。

| キー | 値の例 | 入る場所 |
|---|---|---|
| `{{WORK_DIR}}` | `C:\Users\me\Documents\daily-dashboard` | 定期タスクの指示文 |
| `{{PY}}` | `python3` / `py -3` | config.json, 指示文 |
| `{{OWNER_NAME}}` `{{OWNER_EMAIL}}` | get_me の結果 | config.json |
| `{{JIRA_SLUG}}` `{{JIRA_SITE}}` `{{JIRA_EXTRA_JQL}}` | `acme` / `acme.atlassian.net` / `project = CIS` | sources.json（JIRAサイトごとに1行） |
| `{{CONFLUENCE_SLUG}}` `{{CONFLUENCE_SITE}}` `{{CONFLUENCE_SPACE_KEYS}}` | `acme` / … / `DEV, CIS` | sources.json（サイトごとに1行。spaces は配列） |
| `{{FOCUS}}` | `[{"name":"イメージセンサ","keywords":["CIS","画素"]}]` | config.json |
| `{{PRIORITY_MEMBERS}}` | `["田中","山田"]` | config.json |
| `{{WEEKLY_OUTPUT}}` | `local` / `confluence-draft` | config.json |
| `{{DAILY_CRON}}` `{{WEEKLY_CRON}}` | `CRON_TZ=Asia/Tokyo 41 7 * * 1-5` / `CRON_TZ=Asia/Tokyo 23 17 * * 5` | 定期タスク |

* `assets/sources.json` を写して `{{…}}` を置き換える。使わないソースは `enabled: false` にせず**行ごと削除**、JIRA/Confluence はサイト数だけ行を増やす。
* JQL・スペースキーにダブルクオートが含まれる場合は JSON のエスケープに注意する。

`config.json` の形:

```json
{
  "skill_version": "<assets/VERSION>",
  "timezone": "Asia/Tokyo",
  "python": "{{PY}}",
  "owner": { "name": "{{OWNER_NAME}}", "email": "{{OWNER_EMAIL}}" },
  "focus_categories": {{FOCUS}},
  "priority_members": {{PRIORITY_MEMBERS}},
  "weekly_output": { "mode": "{{WEEKLY_OUTPUT}}", "confluence_source": "confluence-<slug>", "space_key": "", "parent_page_id": "" },
  "schedule": { "daily": "{{DAILY_CRON}}", "weekly": "{{WEEKLY_CRON}}" }
}
```

### A-4. 作業フォルダの準備

1. `{{WORK_DIR}}` に既に `config.json`・`sources.json`・`engine/` があれば、**置き換えてよいか確認**（data/ と output/ は決して触らない）。
2. `engine/` に `assets/` の全ファイルと `SKILL.md`・`references/` を写す。
3. `config.json`・`sources.json` を書く。
4. `{{PY}} engine/ingest.py --work {{WORK_DIR}} init` を実行（フォルダと persistence-check.json を作成。既存なら触らない）。

### A-5. 定期タスク2本の登録

スケジュール作成ツールで2本登録する。ユーザーのPCのフォルダを使うので「PCが必要」（requires_local_device 等）を有効にし、`{{WORK_DIR}}` を対象フォルダに含める。

**デイリー**（名前「デイリー情報ダッシュボード」、`{{DAILY_CRON}}`）
```
{{WORK_DIR}}/engine/SKILL.md を読み、「B. デイリー実行」の手順に従って今日のデイリー情報ダッシュボードを作る。
作業フォルダ: {{WORK_DIR}} ／ Python: {{PY}}
質問はせず最後まで進める。取得できなかったソースは error として記録し、他のソースは続ける。
既存ファイルの削除・上書きはしない。最後にダッシュボードの場所と「今日の見立て」を報告する。
```

**週次**（名前「週次まとめ」、`{{WEEKLY_CRON}}`）
```
{{WORK_DIR}}/engine/SKILL.md を読み、「C. 週次実行」の手順に従って今週の週次まとめを作る。
作業フォルダ: {{WORK_DIR}} ／ Python: {{PY}} ／ 出力先: {{WEEKLY_OUTPUT}}
Confluence に出す場合は下書きとして作成し、公開はしない。質問はせず最後まで進める。
```

### A-6. 確認

1. 登録した2本の名前・スケジュール・承認設定（自動承認か都度確認か）を一覧で示す。
2. その場で「B. デイリー実行」を1回通しで行い、出来たダッシュボードを渡す（初回は繰越・見立てが少ないのは正常）。
3. 翌回以降、`check` が `ok` で `runs` が増えていれば蓄積は残っている。`missing` が出たら保存場所の見直しを提案する。

---

## B. デイリー実行

`PY` と `WORK` は config.json・指示文の値。各コマンドの出力（JSON）を読んで次に進む。

### B-1. 前処理
1. `PY WORK/engine/ingest.py --work WORK check` … `missing`（終了コード2）なら蓄積が消えた可能性。処理は続け、最後の報告で伝える（ダッシュボードにも警告が出る）。
2. `engine/VERSION` と config の `skill_version` が違えば報告に一言添える（自動更新はしない）。

### B-2. 収集フェーズ（MCP を使うのはここだけ）
1. `PY WORK/engine/ingest.py --work WORK prepare` → 出力の `inbox_dir` に書く。
2. `WORK/sources.json` の各ソースについて、`references/source-adapters.md` の手順で MCP を呼び、
   正規レコードにして `<inbox_dir>/<ソースID>.json` に保存する。
   * 失敗 → `ok:false` ＋ `error`、0件 → `ok:true` ＋ `records:[]` ＋ `empty_reason`。どちらも必ずファイルを書く。
   * ツールが返していない値を作らない。`action` は依頼が明記されているときだけ。

### B-3. 正規化・蓄積
`PY WORK/engine/ingest.py --work WORK ingest`
→ 検証・正規化・繰越・closed.json 追記・日次保存。出力の `sources` と `warnings` を控える。

### B-4. 描画フェーズ（MCP を呼ばない）
1. `PY WORK/engine/knowledge.py --work WORK insight` … 今日の見立て（事実のみ・最大3文・basis 付き）
2. `PY WORK/engine/build_dashboard.py --work WORK daily` … 単一ファイル HTML を output/daily に保存

見立ての文を Claude が書き換えたり、励まし・一般論を足したりしない。

### B-5. 報告
* ダッシュボードのファイル（SendUserFile で送れる環境なら送る）
* 今日の見立て（3文以内そのまま）
* 取得失敗・0件のソース、persistence の警告があればそれだけ一言

---

## C. 週次実行

1. `PY WORK/engine/ingest.py --work WORK check`
2. `PY WORK/engine/build_dashboard.py --work WORK weekly` … 直近7日（今日まで）を集計し、
   output/weekly に `.md`・`.html`・`.confluence.html` を保存（MCP は呼ばない）。
3. config の `weekly_output.mode` が `confluence-draft` のとき:
   * `confluence_source` のサイトで、Confluence のページ作成ツールを**下書き（status: draft 等）指定で**呼ぶ。
     本文は `.confluence.html`（またはツールが Markdown を受け付けるなら `.md`）、タイトルは出力の `title`。
     `space_key`・`parent_page_id` があれば使う。
   * ツールに下書き指定の手段が無い、または失敗した → **Confluence には何も作らず**、ローカル保存のみとして報告する。
   * 既に同名の下書きがあっても更新・上書きしない（新しい下書きを作るか、ローカルのみにする）。
4. 報告: 保存先（と下書きのリンク）、決着・未決着・新規の件数。

---

## 蓄積の検索（任意）

ユーザーに「先週の〇〇の件どうなった？」と聞かれたら:
`PY WORK/engine/knowledge.py --work WORK search "〇〇" --top 10`
の結果（日付・状態・リンク）を元に答える。
