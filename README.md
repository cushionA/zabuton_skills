# zabuton_skills

私が使う Claude Code スキルをため込むリポジトリ。

## 収録スキル

| スキル | 概要 | 対応環境 |
| --- | --- | --- |
| [simple-plan-auditor](simple-plan-auditor/) | 長大なPlanを「全体作業マップ → 必要箇所の詳細」という形に整理し、手順・依存・確認事項の抜けを人間が確認しやすくする | 全OS |
| [simple-plan-auditor-en](simple-plan-auditor-en/) | Simple Plan Auditor の英語版。英語のPlanを同じ考え方で整理・監査する | 全OS |
| [slack-off-police](slack-off-police/) | 作業ディレクトリの進捗を定期チェックし、サボっていたらYouTube等のプロセスを強制終了する | Windows |
| [claude-cli-headless](claude-cli-headless/) | `claude -p` をスクリプトから呼ぶときの罠（引数の壊れ方・インジェクション対策・出力パース）をまとめた参照用スキル | 全OS |

## Simple Plan Auditor

AIが作る詳細なPlanは、実行には便利でも人間が全体を監督するには長くなりがちです。

Simple Plan Auditor は、技術詳細を残したまま、人間向けに **目的 / 全体作業マップ / 要注意事項・人間判断 / 重要な依存・分岐** を先に提示します。各作業には論理的な確認事項を付け、必要な箇所だけL1/L2の詳細へ降りられる構成にします。

- [日本語版](simple-plan-auditor/)
- [English version](simple-plan-auditor-en/)

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
