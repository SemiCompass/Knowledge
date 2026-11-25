# 正規レコードとデータファイルの仕様

すべてのソースは、収集フェーズでこの「正規レコード」に変換してから保存します。
描画（build_dashboard.py）と見立て（knowledge.py）はこの形だけを前提に動きます。

## 1. 正規レコード

### 必須

| 項目 | 型 | 内容 |
|---|---|---|
| `id` | 文字列 | 元システムでのID。保存時に `<ソースID>:<元のID>` へそろえる（例 `jira-acme:ABC-123`） |
| `source` | 文字列 | ソースID（sources.json の `sources[].id`）。ingest が自動で入れる |
| `kind` | 文字列 | `task` / `meeting` / `mail` / `chat` / `page` / `alert` のどれか。省略時はアダプタの `default_kind` |
| `title` | 文字列 | 件名・要約・タイトル（200字まで） |

### 任意

| 項目 | 型 | 内容 |
|---|---|---|
| `url` | 文字列 | 元の画面へのリンク |
| `status` | 文字列 | 元システムの状態名（例 `In Progress`、`unread`、`replied`）。アダプタの `closed_statuses` に一致すると「決着」 |
| `self` | 文字列 | 自分との関係。`owner`（担当・主催）/ `reporter` / `watcher` / `to` / `cc` / `direct`（1対1）/ `mentioned` / `member` / `attendee` |
| `due` | 文字列 | 期限（ISO 8601）。**会議は開始日時**を入れる |
| `updated` | 文字列 | 最終更新・受信日時（ISO 8601） |
| `priority` | 文字列 | 元の表記のままでよい。ingest が `high` / `medium` / `low` にそろえる |
| `tags` | 文字列の配列 | ラベル・コンポーネント・スペースキー。重点カテゴリーに一致すると ingest がカテゴリー名を追加 |
| `summary` | 文字列 | 要点（120字まで）。本文にある事実だけを書く |
| `action` | 文字列 | 自分に求められている行動。**本文に明記されている場合だけ**入れる |
| `waiting_on` | 文字列 | 他者の対応待ちなら、その人の名前 |
| `elapsed_days` | 整数 | 経過日数。省略すると ingest が `updated` から計算 |
| `people` | 文字列の配列 | 関係者の表示名（差出人・担当者など）。優先メンバーの判定に使う |

### ingest が付ける管理項目（収集フェーズでは書かない）

| 項目 | 内容 |
|---|---|
| `first_seen` | 初めて取得できた日 |
| `last_seen` | 最後に実際に取得できた日 |
| `closed` | 決着済みなら `true` |
| `carried` | 今日は取得できず、直近7日の記録から再掲したものなら `true` |
| `carried_from` | 繰越が始まった日（＝最後に取得できた日） |
| `stale_source` | 今日そのソースが取得失敗したため古い情報のまま、なら `true` |

## 2. 収集結果ファイル（inbox）

収集フェーズは、ソースごとに `inbox/<日付>/<ソースID>.json` を1つ書きます。
書き込み先フォルダは `ingest.py prepare` が用意します（同じ日の再収集は `<日付>-2/` になり、上書きしない）。

```json
{
  "source_id": "jira-acme",
  "fetched_at": "2026-11-25T07:41:00+09:00",
  "ok": true,
  "records": [ { "id": "ABC-123", "title": "…", "status": "In Progress" } ],
  "empty_reason": null,
  "error": null,
  "query": "実行したJQLやツール名（任意・記録用）"
}
```

**「0件」と「取得失敗」は必ず区別します。**

| 状況 | 書き方 | ingest の扱い |
|---|---|---|
| 取得できて1件以上 | `ok: true`, `records: [...]` | `ok` |
| 取得できて0件 | `ok: true`, `records: []`, **`empty_reason` 必須**（例「JQL結果0件」） | `empty` |
| 取得失敗 | `ok: false`, **`error` 必須**（ツールのエラー文を短く） | `error` |
| 0件なのに `empty_reason` なし | ― | 失敗の可能性ありとして `error` |
| ファイル自体がない | ― | `error`（収集されていない） |

## 3. 日次ファイル `data/daily/<日付>.json`

```json
{
  "date": "2026-11-25",
  "generated_at": "2026-11-25T07:45:12+09:00",
  "engine_version": "1.0.0",
  "owner": { "name": "…", "email": "…" },
  "inbox": "2026-11-25",
  "sources": {
    "outlook-mail": { "status": "ok", "count": 12, "fetched_at": "…" },
    "teams-chat":   { "status": "empty", "count": 0, "empty_reason": "直近24時間に該当なし" },
    "jira-acme":    { "status": "error", "error": "タイムアウト" }
  },
  "warnings": ["teams-chat: 1件をスキップ（title がない）"],
  "records": [ … 正規レコード（繰越分を含む） … ]
}
```

* 同じ日に再実行すると `<日付>-2.json` を新しく作ります（既存は上書きしない）。読むときは最新の版を使います。

## 4. 繰越のルール

* 直近7日の日次ファイルにあり、今日の取得結果に無い項目が対象。
* 次のどれかに当たるものは繰り越さない: 決着済み（`closed`）、`closed.json` に記録済み、アダプタの `carry` が `false`（予定・ページ）、最後に取得できた日から7日超。

## 5. `data/closed.json`（追記のみ）

```json
{ "version": 1, "items": [
  { "id": "jira-acme:ABC-2", "source": "jira-acme", "kind": "task", "title": "…",
    "status": "Done", "url": "…", "first_seen": "2026-11-20", "closed_on": "2026-11-24" }
]}
```

* 既存の行は変更・削除しない。同じ `id` は2回追記しない。

## 6. 見立て `data/insight/<日付>.json`

```json
{ "date": "2026-11-25",
  "sentences": [ { "text": "期限切れの未完了タスクが2件あり、…。", "basis": ["jira-acme:ABC-1", "jira-acme:ABC-9"], "rule": "overdue" } ],
  "candidates_not_shown": ["carried"] }
```

* 最大3文。事実だけ。`basis` はレコードID、`source:<ID>`、`count:records` のいずれか。
* 規則（優先順）: 取得失敗 → 期限切れ → 今日の会議に関連する未決着（BM25検索）→ 優先メンバー → 本日期限 → 他者待ち → 繰越。

## 7. `persistence-check.json`

```json
{ "token": "…", "created_at": "…", "work_dir": "…", "runs": 12, "last_run": "…" }
```

* `init` で作成、`check` で `runs` と `last_run` だけ更新。
* `check` が `missing`（終了コード2）を返したら「蓄積が残らない場所」の可能性。`check` はファイルを作り直して `reset_detected_at` を記録し、同じ日の `ingest` がそれを見て日次ファイルの `warnings` に警告を入れる（ダッシュボードの取得状況に赤で表示される）。
