# 自作スキル集

Claude 用の自作スキルを置くリポジトリです。スキルは `Skills/` の中に、フォルダ1つが1スキルで入っています。

| スキル | 内容 |
|---|---|
| [daily-info-dashboard](Skills/daily-info-dashboard/) | Outlook・Teams・JIRA・Confluence から自分専用のデイリー情報ダッシュボードと週次まとめを作る定期タスク2本を登録・実行する |

## 使い方（GitHub からダウンロード → ローカルの Claude に入れる → セットアップ）

### 1. ダウンロード

* このページの **Code → Download ZIP** で全体をダウンロードして展開する
  （または `git clone https://github.com/SemiCompass/Knowledge.git`）。

### 2. ローカルの Claude に入れる

スキルは **`SKILL.md` があるフォルダ単位**で入れます（例 `Skills/daily-info-dashboard/`）。
`assets/` と `references/` も一緒に入れてください（スクリプト類が入っています）。

* **Claude デスクトップアプリ**: `Skills/` の中のスキルのフォルダ（例 `daily-info-dashboard`）だけを zip に圧縮し、
  設定のスキル画面からアップロードする。zip の直下が `daily-info-dashboard/SKILL.md` になるようにする。
* **Claude Code**: `Skills/` の中のスキルのフォルダごと `~/.claude/skills/` にコピーする
  （`~/.claude/skills/daily-info-dashboard/SKILL.md` になればOK）。

### 3. セットアップ

Claude に「デイリーダッシュボードを設定して」と頼む。
ヒアリング（使うソース・作成先フォルダ・スケジュールなど）のあと、定期タスク2本が登録されます。

### 更新するとき

新しい版をダウンロードして同じ手順で入れ直し、もう一度「デイリーダッシュボードを設定して」と頼む。
作業フォルダの `engine/` を新しい版に置き換えるかを確認されます（蓄積データ `data/` と出力 `output/` は変更されません）。

## 動作要件（daily-info-dashboard）

* Python 3.9 以上（外部ライブラリ不要）
* Microsoft 365 と Atlassian の接続（使うソースのみ）
* 定期タスクを実行する PC 上の作業フォルダ（蓄積を残すため）
