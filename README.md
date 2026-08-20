# zabuton_skills

私が使う Claude Code スキルをため込むリポジトリ。

## 収録スキル

| スキル | 概要 | 対応環境 |
| --- | --- | --- |
| [slack-off-police](slack-off-police/) | 作業ディレクトリの進捗を定期チェックし、サボっていたらYouTube等のプロセスを強制終了する | Windows |
| [claude-cli-headless](claude-cli-headless/) | `claude -p` をスクリプトから呼ぶときの罠（引数の壊れ方・インジェクション対策・出力パース）をまとめた参照用スキル | 全OS |

## 使い方

`~/.claude/skills/<スキル名>` にこのリポジトリ内のスキルディレクトリを配置（またはジャンクション/シンボリックリンク）すると、
Claude Code が自動で読み込む。

```powershell
# 例: リポジトリを参照するジャンクションを作る（管理者権限不要）
cmd /c mklink /J "$env:USERPROFILE\.claude\skills\slack-off-police" "<このリポジトリ>\slack-off-police"
```

## リポジトリに入れないもの

`.gitignore` で除外している。設定ファイルには作業ディレクトリの実パスなどが入るため、コミットしない。

- `.claude/settings.local.json` などのローカル設定
- スキルが生成する `.slack-off-police.yaml` / ログ
- `slack-off-police/assets/` に置く音声ファイル（著作物になりうる）

## ライセンス

[MIT](LICENSE)
