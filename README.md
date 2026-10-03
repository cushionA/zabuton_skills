# zabuton_skills

私が使う Claude Code スキルをため込むリポジトリ。

## 収録スキル

| スキル | 概要 | 対応環境 |
| --- | --- | --- |
| [simple-plan-auditor](simple-plan-auditor/) | 長大なPlanを「全体作業マップ → 必要箇所の詳細」という形に整理し、手順・依存・確認事項の抜けを人間が確認しやすくする | 全OS |
| [simple-plan-auditor-en](simple-plan-auditor-en/) | Simple Plan Auditor の英語版。英語のPlanを同じ考え方で整理・監査する | 全OS |
| [slack-off-police](slack-off-police/) | 作業ディレクトリの進捗を定期チェックし、サボっていたらYouTube等のプロセスを強制終了する | Windows |
| [claude-cli-headless](claude-cli-headless/) | `claude -p` をスクリプトから呼ぶときの罠（引数の壊れ方・インジェクション対策・出力パース）をまとめた参照用スキル | 全OS |
| [scraping-procedure-deck](scraping-procedure-deck/) | Webの操作・取得手順を、編集可能な赤枠・番号とデータレイアウト表付きのPowerPoint資料にする | Python / Playwright、描画はPowerPointまたはLibreOffice |

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

## Scraping Procedure Deck

Webサイトの操作・取得手順を、編集可能な赤枠・番号とデータレイアウト表を備えたPowerPoint資料にする。起点URL、URLの流れ、手持ちのキャプチャ、既存Playwrightコードを入力にできる。

撮影前に、対象画面・状態・代表件数・表示差分・再利用範囲・撮影枚数を合意する。コードの全件ループや見つかった表示差分を自動で全て撮影せず、追加は追加分だけ確認する。`--only` による部分撮影では、対象外の撮影結果を保持する。

[スキルの指示](scraping-procedure-deck/SKILL.md) / [入力と確認例](scraping-procedure-deck/references/inputs.md) / [シナリオ形式](scraping-procedure-deck/references/scenario-format.md) / [完成シナリオ例](scraping-procedure-deck/examples/aupay-market.json)

### 必要な環境と実行

Python 3.10+、`python-pptx`、`Pillow`、`lxml` を使用する。ブラウザ撮影にはPlaywrightとChromiumも必要。プレビューはWindowsのPowerPoint、またはLibreOfficeと`pdftoppm`で描画する。既定フォントはMeiryo UI。

```powershell
python -m pip install python-pptx Pillow lxml playwright
python -m playwright install chromium
```

リポジトリのルートから、合意済みの内容を `work/scenario.json` に記述して実行する例。`work/` は公開対象外の作業ディレクトリに置き換える。出力先フォルダは事前に作成する。

```powershell
New-Item -ItemType Directory -Force work/out | Out-Null
python scraping-procedure-deck/scripts/capture.py work/scenario.json --out work/captures
python scraping-procedure-deck/scripts/build_deck.py work/scenario.json --captures work/captures -o work/out/deck.pptx
python scraping-procedure-deck/scripts/render_preview.py work/out/deck.pptx --out work/preview
```

文言だけの修正では撮影をやり直さず、資料を再生成する。撮り直す場合は `capture.py --only <shot-id>` で合意した対象に限定する。選択shotの `peeks` も撮影され、到達に必要な前段操作が再実行される場合がある。生成後はWARNと全スライドの見た目を確認する。

### 検証と既知の制約

```powershell
python -X utf8 -B -m unittest discover -s scraping-procedure-deck/evals -p "test_*.py"
```

2026-10-03時点で、ローカルモックの実ブラウザ統合テストを含む38テストが成功。別途、5外部サイト＋ローカルの耐久評価10ケースを実施し、安定5件（入力制約1件含む）、失敗3件、証明書エラーで未検証2件。撮影検証は延べ17/22周、資料生成は60/60回成功し、最新42スライドを目視確認した。長時間運転やメモリリークの評価ではない。

次の問題は未修正。テスト成功はこれらの解消を意味しない。

- ダークテーマの表の強調行が低コントラストになる。手直し済みPPTXへの `--base --only` 追加で、タイトル・テーマ・フッターが揃わない場合がある。
- `build_deck.py` は出力先フォルダを自動作成しない。表紙に開始URLが可視文字で残る。
- 画像マップの `area` や入れ子フレームの対象を測定できない。
- 無限スクロールでは想定より多い商品が画像に写った。内部スクロールの影響が疑われるが因果は未確定。遅延表示も3周中1回タイムアウトし、原因は未確定。
- 撮影倍率1.5でPNG実寸と記録寸法に高さ1pxの差が出る。

評価用モック・テストコードは同梱する。生成PPTX・キャプチャ・ログは公開しない。`evals/files/generated/` は除外設定済みだが、任意の作業ディレクトリは利用者側で公開対象から外す。

## リポジトリに入れないもの

`.gitignore` で除外している。設定ファイルには作業ディレクトリの実パスなどが入るため、コミットしない。

- `.claude/settings.local.json` などのローカル設定
- スキルが生成する `.slack-off-police.yaml` / ログ
- `slack-off-police/assets/` に置く音声ファイル（著作物になりうる）

## ライセンス

[MIT](LICENSE)
