# ソースアダプタ（収集フェーズの手引き）

収集フェーズは **Claude が MCP ツールを呼び、結果を正規レコードに直して inbox に書く** 段階です。
Python は MCP を呼びません。描画フェーズでも MCP は呼びません。

## 1. sources.json の構成

```json
{
  "adapters": { "<アダプタ名>": { …種類ごとの定義… } },
  "sources":  [ { "id": "<ソースID>", "adapter": "<アダプタ名>", "enabled": true, "params": { … } } ]
}
```

### アダプタ定義の項目

| 項目 | 必須 | 内容 |
|---|---|---|
| `label` | ○ | 画面に出す名前 |
| `connector` | ○ | 使う接続（MCP）の名前。ツール探しの手がかり |
| `default_kind` | ○ | レコードの `kind` を省略したときの値 |
| `fetch` | ○ | 何をどの範囲で取るか（Claude が読む自然文の指示） |
| `map` | ○ | 正規レコードの各項目に元データの何を入れるか |
| `carry` | ○ | 取得範囲から消えた未決着を7日間繰り越すか（予定・ページは `false`） |
| `closed_statuses` | ○ | この `status` なら「決着」とみなす値の一覧（大文字小文字・全角半角は無視） |
| `multi` | ― | 同じアダプタを複数ソースで使うなら `true`（JIRA・Confluence） |
| `id_prefix` | ― | `multi` のときのソースIDの頭（`jira-` → `jira-<slug>`） |

**新しいソースを足すには**、`adapters` に定義を1つ足し、`sources` に1行足すだけです。Python の変更は不要です
（ingest は `default_kind`・`carry`・`closed_statuses` をここから読みます）。

例: Slack を足す場合

```json
"slack": {
  "label": "Slack", "connector": "Slack", "default_kind": "chat", "carry": true,
  "closed_statuses": ["replied"],
  "fetch": "直近24時間で自分がメンションされたメッセージとDMをスレッド単位で取得する。",
  "map": { "id": "チャンネルID＋スレッドts", "title": "スレッドの1行目", "url": "パーマリンク",
           "updated": "最新メッセージ日時", "status": "最後の発言が自分なら replied、相手なら open",
           "people": "発言者", "summary": "要点120字以内", "action": "依頼が明記されていればその内容" }
}
```

## 2. 収集の手順（ソースごとに繰り返す）

1. `python3 engine/ingest.py --work <作業フォルダ> prepare` で書き込み先 `inbox_dir` を受け取る（1回だけ）。
2. `sources` の `enabled: true` のソースを順に処理する。
3. アダプタの `connector` に合う MCP ツールを探す（ToolSearch でキーワード検索。例「calendar events」「outlook mail」「teams chat」「jira search」「confluence cql」）。
4. `fetch` の指示どおりに呼ぶ。`params` の値（JQL、スペースキー、サイト）を使う。
5. 結果を `map` に従って正規レコードにする。**ツールが返した項目だけ**を使い、無い値は省略する（推測で埋めない）。
6. `<inbox_dir>/<ソースID>.json` に書く（形式は record-schema.md の「収集結果ファイル」）。

### 失敗と0件の書き分け（必ず守る）

* ツールがエラーを返した → 1回だけ再試行。まだだめなら `ok: false` と `error`（エラー文の要約）を書く。
* ツールが見つからない・接続が切れている → `ok: false`, `error: "接続（<connector>）が見つからない／認証切れ"`。
* 正常に0件 → `ok: true`, `records: []`, `empty_reason` に**何をどう探して0件だったか**を書く（例「JQL: assignee=currentUser() AND … の結果0件」）。
* 1ソースが失敗しても他のソースの収集は続ける。

### 要約・行動の書き方

* `summary` は本文にある事実だけを120字以内で。評価語（「重要」「至急」など）は本文に書かれている場合だけ。
* `action` は**自分への依頼が明記されているときだけ**。「確認しておくとよい」のような提案は書かない。

## 3. 各アダプタの補足

### Outlook予定表（outlook-calendar）
* 範囲は今日0:00〜翌営業日24:00（金曜なら月曜まで）。
* `due` は開始日時。オンライン会議URLは `url` に入れてよい。

### Outlookメール（outlook-mail）
* 範囲は直近24時間。月曜は金曜0:00から。
* 自分が返信済みと分かれば `status: "replied"`（→ 決着）。
* ニュースレター・自動通知（no-reply 差出人など）は除く。

### Teamsチャット（teams-chat）
* スレッド単位で1レコード。`id` はチャットID＋先頭メッセージID。
* 自分の質問に相手が未回答なら `waiting_on` に相手の名前、`status: "replied"` にはしない（`open` のまま）。

### JIRA（jira-<slug>）
* Atlassian MCP で接続済みのサイトを列挙し（例 getAccessibleAtlassianResources）、選んだサイトごとに1ソース。
* `<slug>` はサイトのサブドメイン（`acme.atlassian.net` → `jira-acme`）。英小文字・数字・ハイフンのみ。
* 基本JQLは `fetch` を参照。`params.extra_jql` があれば**別クエリ**として実行し、課題キーで重複を除く。
* 基本JQLに「直近1日に完了したもの」を含めているのは、決着を `closed.json` に記録するため。

### Confluence（confluence-<slug>）
* `params.spaces` のスペースの直近1日の更新と、自分へのメンション（7日）を取得。
* ページは繰り越さない（`carry: false`）。

## 4. 本人情報

* 氏名・メールは Microsoft 365 のプロフィール取得ツール（`get_me` など）で取得する。ユーザーには質問しない。
* 取れなければ Atlassian のユーザー情報ツール（`atlassianUserInfo` など）で代用し、それも無理なら空欄のまま進める。
