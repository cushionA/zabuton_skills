# Slack-off Police Watcher (任意)

サボり警察に、VS Code上の「読んでいる／書いている」動きを渡すだけの拡張機能。

## 入れなくても動く

拡張機能なしでも、サボり警察は VS Code が自分で保存している次のデータを読む。

| 見たいもの | 拡張なしで読む場所 |
| --- | --- |
| チャットでの質問 | `%APPDATA%\Code\User\workspaceStorage\<id>\chatSessions\*.jsonl` |
| 資料・ファイルの閲覧 | `state.vscdb` の `history.entries`（最近開いた順のリストを毎回比較） |
| 保存した編集 | `%APPDATA%\Code\User\History`（ローカル履歴） |

拡張機能を入れると、これに加えて **開いた瞬間・スクロール・選択・保存** が
秒単位のタイムスタンプ付きで記録され、「15分間ずっと同じ資料を読んでいた」まで判定できる。

## 記録する内容

1イベント1行のJSONLで、ファイルパスと種別だけを書き出す。
**ファイルの中身、チャットの本文、入力したテキストは一切記録しない。**

```json
{"ts":1783934652701,"type":"read","file":"C:\\work\\notes.md","lang":"markdown","workspace":"C:\\work"}
```

`type` は `start` / `open` / `read` / `edit` / `save` / `focus`。
出力先は `~/.slack-off-police/vscode-activity.jsonl`（1MBを超えたら後半だけ残す）。

## インストール

マーケットプレイスには置いていないので、拡張機能フォルダにコピーするだけ。

```powershell
$dest = "$env:USERPROFILE\.vscode\extensions\slack-off-police-watcher-0.1.0"
Copy-Item -Recurse -Force "<skill-path>\vscode-extension" $dest
```

コピー後に VS Code を再起動する（`Ctrl+Shift+P` → `Developer: Reload Window` でも可）。
Insiders なら `.vscode-insiders\extensions`、Cursor なら `.cursor\extensions` に置く。

動いているかは `Ctrl+Shift+P` → `サボり警察: 活動ログを開く` で確認できる。

## 設定

| 設定キー | 既定 | 意味 |
| --- | --- | --- |
| `slackOffPolice.enabled` | `true` | 記録のオン/オフ |
| `slackOffPolice.logPath` | 空 | 出力先の変更 |
| `slackOffPolice.throttleSeconds` | `30` | 同じファイル・同じ種別をまとめる間隔 |

## アンインストール

コピーしたフォルダを消して VS Code を再起動する。ログは
`~/.slack-off-police/vscode-activity.jsonl` に残るので、不要なら手で消す。
