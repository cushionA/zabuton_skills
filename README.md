# zabuton_skills

私が使う Claude 向けスキルをまとめるリポジトリ。対応環境はスキルごとに異なる。

## 収録スキル

| スキル | 概要 | 対応環境 |
| --- | --- | --- |
| [slack-off-police](slack-off-police/) | 作業ディレクトリの進捗を定期チェックし、サボっていたらYouTube等のプロセスを強制終了する | Windows |
| [claude-cli-headless](claude-cli-headless/) | `claude -p` をスクリプトから呼ぶときの罠（引数の壊れ方・インジェクション対策・出力パース）をまとめた参照用スキル | 全OS |
| [scraping-procedure-deck](scraping-procedure-deck/) | Webの操作・取得手順を、編集可能な赤枠・番号とデータレイアウト表付きのPowerPoint資料にする | Python / Playwright、描画はPowerPointまたはLibreOffice |
| [simple-plan-auditor](simple-plan-auditor/) | 作業計画の抜け、今必要な判断、根拠・確認方法、着手できる範囲を整理する | Claude Web・デスクトップ / Claude Code |
| [simple-plan-auditor-en](simple-plan-auditor-en/) | Simple Plan Auditor の英語版。英語のPlanを同じ考え方で整理・監査する | 全OS |

## 使い方

### Claude Web・デスクトップで Simple Plan Auditor を使う

1. `simple-plan-auditor/` フォルダ全体をZIPにする。ZIPの最上位にこのフォルダを置き、配下の `SKILL.md`、`references/`、`scripts/`、`assets/`、`examples/` の階層を保つ。
2. Claudeの「Customize → Skills」からZIPをアップロードし、有効にする。Skillsと「Code execution and file creation」が利用可能な設定になっている必要がある。組織アカウントでは管理者の設定も適用される。[公式の追加手順](https://support.claude.com/en/articles/12512180-use-skills-in-claude)
3. 計画書と必要な資料を添付し、次のように依頼する。

> Simple Plan Auditorを使って、この計画の不足、今決めること、着手できる範囲をレビューして。

Windowsでは、リポジトリのルートで次を実行すると一時フォルダにZIPを作れる。Windows PowerShell 5.1 の `Compress-Archive` はZIP内のパスを `\` 区切りで保存するため、Windows標準の `tar` を使う。

```powershell
tar -a -c -f "$env:TEMP\simple-plan-auditor.zip" --exclude __pycache__ simple-plan-auditor
```

### Claude Codeで使う

`~/.claude/skills/<スキル名>` にこのリポジトリ内のスキルディレクトリを配置（またはジャンクション/シンボリックリンク）すると、
Claude Code が自動で読み込む。

```powershell
# 例: リポジトリを参照するジャンクションを作る（管理者権限不要）
cmd /c mklink /J "$env:USERPROFILE\.claude\skills\slack-off-police" "<このリポジトリ>\slack-off-police"
```

## Simple Plan Auditor

既存計画は元の書式のまま監査し、新規作成・再構成では全体作業マップから必要な工程だけ詳細へ展開する。未決定事項は決める時点と待つ工程を示し、調査など先に進められる作業と区別する。

- **通常の出力:** 今決めること、不足箇所と根拠、次の確認・進める範囲。
- **消費を抑える運用:** 参照資料や完成例は必要時だけ読み、通常は一度の監査で完結する。再レビューは変更と影響先を中心に確認し、全文の再生成や常時の多重レビューを避ける。
- **任意の確認画面:** 同じMarkdownからHTMLを生成し、目次・文書内リンクによる移動と折り畳みで確認する。表示内の操作はAI呼び出しを伴わず、元の計画の変更・承認も行わない。
- **修正点の明示:** 承認済みの計画に後から変更を入れたときは、先頭の「今回の修正」に意味が変わった箇所だけを R1, R2… で示し、本文の対応箇所にも同じIDを付ける。作成中や監査での修正は載せない。

[スキルの指示](simple-plan-auditor/SKILL.md) / [CSV一括登録のサンプルプラン](simple-plan-auditor/examples/sample-plan.md) / [修正版の例](simple-plan-auditor/examples/sample-plan-revision.md) / [English version](simple-plan-auditor-en/)

補助スクリプトはPython標準ライブラリだけを使用する。リポジトリのルートからサンプルを確認する例：

```powershell
python simple-plan-auditor/scripts/validate_overview.py simple-plan-auditor/examples/sample-plan.md
python simple-plan-auditor/scripts/render_review.py simple-plan-auditor/examples/sample-plan.md "$env:TEMP\simple-plan-auditor-example.html"
```

構造検証は標準L0用。明確な構造不正はエラー、作業の詰め込み疑い・行数は警告として返す。既存書式の監査には強制せず、計画の妥当性・着手可否は内容の監査で判断する。

## Scraping Procedure Deck

Webサイトの操作・取得手順を、編集可能な赤枠・番号とデータレイアウト表を備えたPowerPoint資料にする。起点URL、URLの流れ、手持ちのキャプチャ、既存Playwrightコード、Playwrightの実行結果を入力にできる。

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

### テスト実行結果から作る

PlaywrightのJSONレポートと添付PNGを渡すと、画像候補から利用する画面と順序を確認して資料化できる。ログイン後の保存画面も利用でき、テストの再実行・再ログインは不要。赤枠の位置は画像から確認する。文字ログ・HTMLレポート・`trace.zip` の直接変換には対応しない。

[実行結果のインポート手順](scraping-procedure-deck/references/import-results.md)に、レポートの用意方法とコマンドを記載。インポート計画と座標はスキル側で作成し、選択したPNGだけを取り込む。

### 検証と既知の制約

```powershell
python -X utf8 -B -m unittest discover -s scraping-procedure-deck/evals -p "test_*.py"
```

2026-10-03時点で、ローカルモックの実ブラウザ統合テストを含む74テスト（既存38＋実行結果インポート33＋差し込み時の書式・配色と出力文字コード3）が成功。別途、5外部サイト＋ローカルの耐久評価10ケースを実施し、安定5件（入力制約1件含む）、失敗3件、証明書エラーで未検証2件。撮影検証は延べ17/22周、資料生成は60/60回成功し、最新42スライドを目視確認した。長時間運転やメモリリークの評価ではない。

次の問題は未修正。テスト成功はこれらの解消を意味しない。

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
