# 自作スキル集

Claude 用の自作スキルを置くリポジトリです。フォルダ1つが1スキルです。

| スキル | 内容 |
|---|---|
| [daily-info-dashboard](daily-info-dashboard/) | Outlook・Teams・JIRA・Confluence から自分専用のデイリー情報ダッシュボードと週次まとめを作る定期タスク2本を登録・実行する |

## 使い方

* Claude のスキルとして登録する: 各フォルダ（`SKILL.md` があるフォルダ）をスキルとしてアップロードする。
* 会話から使う: 「daily-info-dashboard のスキルを GitHub から取得してセットアップして」のように頼むと、
  Claude がこのリポジトリを取得して `SKILL.md` の手順で進めます。

## 動作要件（daily-info-dashboard）

* Python 3.9 以上（外部ライブラリ不要）
* Microsoft 365 と Atlassian の接続（使うソースのみ）
* 定期タスクを実行する PC 上の作業フォルダ（蓄積を残すため）
