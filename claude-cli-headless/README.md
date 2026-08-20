# claude-cli-headless

スクリプトやバックグラウンドプロセスから `claude -p`（Claude Code のヘッドレス/print
モード）をサブプロセスとして呼び出すときの罠をまとめた、参照用スキルです。
コードは同梱していません。ノウハウを `SKILL.md` に集約しています。

## これは何のためのスキルか

Anthropic API を別契約せず、**Claude Code のサブスク枠**を使って、スクリプトから
ちょっとした分類・判定・短文生成をさせたいことがあります。
`claude -p ...` をサブプロセスとして呼べば動きますが、素直に組むと以下でハマります。

- Claude Code のセッション内から呼ぶと「入れ子セッション」エラーで即死ぬ
- （特にWindows）プロンプトを引数で渡すと、改行やリダイレクト記号でコマンドが壊れる
- ウィンドウタイトルやWebページの中身のような信用できない文字列を渡すと、
  プロンプトインジェクションの入口になる
- `--output-format json` の中身の取り出し方が地味に面倒
- `pythonw` で動かすと標準出力が消え、ログが何も残らない

これらを1回ずつ踏んで直した記録が `SKILL.md` です。実装例として
[slack-off-police/scripts/checkers/judge.py](../slack-off-police/scripts/checkers/judge.py)
（進捗が無いときだけ `claude -p` でサボりを判定させる実装）が全部の対策を含んでいます。

## 使い方

コードは書かず、Claude Code に対する**指示書**として機能します。
`claude -p` をサブプロセスから呼ぶコードを書く/レビューする場面で、Claude Code が
自動的にこのスキルを読み込みます。明示的に呼びたい場合は次のように聞いてください。

- 「`claude -p` を使ってバックグラウンドで判定させたい」
- 「claude CLIをsubprocessで叩くコードを書いて」
- 「Windowsでclaude CLIの引数が壊れる」

## 対象外

- Anthropic API（Messages API / SDK）を直接叩く実装は別物です。API課金が絡む話は
  `claude-api` スキル（Anthropicのマーケットプレイス由来）に譲ります
- エージェント的にファイル操作やbashを回したい場合は、通常の `claude` インタラクティブ
  起動や `claude-agent-sdk` の領分です。このスキルは「1回のプロンプトに対する
  結果だけが欲しい」用途に限定しています

## ライセンス

このリポジトリ（[zabuton_skills](../)）に準拠し [MIT](../LICENSE)。
