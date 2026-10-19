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
| [ai-task-autotuner](ai-task-autotuner/) | テストデータからAIタスク（分類・抽出・突合・検索など）を設計し、データ監査→独立した設計レビュー→固定評価→実測の採用／破棄ループで精度、次に効率を自律改善する | Codex CLI / Claude Code、Python 3.10+ |

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

Webサイトの操作・取得手順を、編集可能な赤枠・番号とデータレイアウト表を備えたPowerPoint資料にする。ベストプラクティスは、Claude Cowork のスキルで実際のサイト操作を録画して見せ、その録画を参考に撮影し直すこと。録画で操作の順番・絞り込み条件・開く商品・値の位置が伝わり、撮影し直した画面なので赤枠を正確に付けられる。録画のほか、起点URL、URLの流れ、手持ちのキャプチャ、既存Playwrightコード、Playwrightの実行結果も入力にできる。

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

## AI Task Autotuner

テストデータとタスクの目的を渡すと、AI（API想定）を使う処理を設計し、実測しながら自律的に改善し続ける。既定の `backend: auto` は実行元に合わせ、Codex上では Codex CLI＋Luna（`gpt-6-luna`）、Claude上では `claude -p`＋Haiku をAPIの代わりに使う。effort は `low` で開始する。

- **データ監査が先:** 件数・重複・キー・欠損・正解の出所・近道（答えが入力に漏れていないか）を計算し、精読結果と合わせて PASS / REVIEW / STOP を判定する。REVIEW / STOP なら実験を始めずに報告して止まる。
- **測る前にタスクをレビュー:** 設計と評価キットの草案を、別のレビュワーが確認する。目的・入力の十分さ・コードで確定する条件への反例・出力と評価の網羅性・漏洩・問題の難しさを確認し、要修正なら自律的に直して再レビューする。ユーザー承認を毎回求める工程ではない。現行設計の全項目PASS記録がなければ、ベースライン・参照作成を含む本測定は始められない。
- **評価は固定:** 正解ラベルがなければ設定した参照モデルで参照を作り、「正答率」ではなく「参照との一致率」と呼ぶ。採点器・参照の不備を直した場合は再採点・再検証する。入力・補助データの内容や役割・順序・対象範囲、読み込み処理、データ分割が変われば出力から測り直す。古い出力の再採点では新データの測定にできない。
- **最終評価用データを保護:** 実際に読み込むIDで判定し、全件実行・別名の分割・AIなしの候補も回数制限の対象にする。実行目的の変更で迂回できない。同じ候補ID・内容・実効設定の繰り返しは1回分として扱う。最終評価用の参照作成はレビュー後の採点しない準備処理として分ける。
- **改善ループ:** 1実験＝1仮説を事前登録し、実験IDと実行候補の一致を確認する。候補の内容とbackend・モデル・effort・同時実行数などの実効設定は初回の本測定で固定し、変更は新しい候補で測る。異なる設定の結果を反復として混ぜない。揺らぎを超えた改善だけ採用し、精度が頭打ちになったら品質下限を守って費用・速度・タスク構造を改善する。同じ方向の試行や行き詰まりには別の手段・新しい視点を求める。
- **AI以外も使う:** 正規化・ルール・候補絞り込みなどのコード処理をパイプラインに組み込み、AIなしの案も必ず測る。コードだけで足りると分かれば別枠で報告する。
- **停止条件:** 明示STOP・時計の一時停止・費用・時間・期限を実行開始時、各データの処理前、各モデル呼び出しと再試行の直前に確認する。待機中の呼び出しも開始前に確認する。費用は観測済みの額で判定し、進行中の呼び出しが完了すると上限を超える場合はある。実験回数は候補の枠として予約し、枠内の再測定・既測定候補の最終確認を許すが、STOPや費用・時間の制限は越えない。時間はユーザーの返答待ちや利用上限による停止を除いて数える。
- **成果物:** タスク設計書、最終パイプラインとプロンプト、本番向け設定、評価キット、報告書に、監査・タスクレビュー・レビュー済みベースライン・台帳・測定履歴を添える。移動先のデータが同じ内容か確認してからパスを書き換える。外部の元データには移動先の対応指定が必要で、承認や停止・予算・最終評価の履歴をリセットしない。
- **コンテキスト節約:** 設計レビューの詳細はファイルに保存し、レビュワーの返答は200語以内にする。初回は設計全体、再レビューは変更と影響先を中心に確認する。通常の候補改善ごとに全設計をレビューし直すことはない。会話のコンテキストを自動で破棄する機能は含まない。

依頼の例（Claude Code）:

> このデータ（inputs/products.jsonl）で、楽天とauのSKU突合をするAIタスクを設計して実測で改善して。モデルはhaiku、予算は$3まで。データに問題があれば動く前に報告して。

Codexではモデルの指定を省けば Luna を使う。参照作成も既定は Luna で、自動的に上位モデルへ切り替えない。同じモデルで作った参照との一致は、独立した正しさの検証にはならない。

作業フォルダはリポジトリの外に作る。主なコマンド（スキルが手順どおり実行する）:

```powershell
python ai-task-autotuner/scripts/init_workspace.py work/ws --task "sku-matching" --data <入力ファイル>
python ai-task-autotuner/scripts/llm_call.py --calibrate
python ai-task-autotuner/scripts/ledger.py status work/ws
```

注意点:

- backend/model の明示指定を優先し、指定がなければ `AITT_BACKEND`、実行元の環境変数、単独で利用できるCLIの順で決める。両CLIがあり実行元を判別できない場合などは `--backend codex` または `--backend claude` を明示する。初期化時の選択を作業フォルダに保存し、後から実行元が変わっても維持する。旧設定の `backend: cli` は従来どおりClaudeを指す。
- CLI呼び出しは対応するアカウントの利用枠を消費する。参照作成や並列実行は短時間で上限に達しやすい。上限に達した実行は自動で打ち切られ、時間の計測も止まる。
- Codex CLIはツールを使わない隔離呼び出しで動かす。API単体の時間・費用・思考トークン・応答トークン上限の強制は、取得不能または未対応として扱う。Lunaの料金を推測で埋めない。要求モデル名と、実際に返されたモデル名も区別する。
- Codexの効率指標は既定で1件当たりの呼び出し回数。費用上限（`limits.max_cost_usd`）があると、各モデル呼び出しで指定先を確認し、料金による制限を守れないCodex呼び出しを止める。コードだけの候補は実行できる。金額の上限には料金を計算できるbackendを使うか、ユーザーの意図を確認して時間・実験回数の上限を使う。予算を自動的に消すことはない。本番設定のトークン上限は未対応の `null`、思考はbackend管理とし、タイムアウトはCLI全体の実測p99から算出する。未対応の指定は `unsupported_settings` に記録する。
- `claude -p` では Haiku の思考を完全には無効化できない（effort low で最小化し、思考トークンを記録する）。実API（`backend: api`、`anthropic` SDK と認証情報が必要）では対応モデルで思考を無効化できる。Codex 0.159.2のLuna呼び出しは試したが、認証更新のHTTP 401で成功応答を得られず未検証。Claudeのライブ呼び出しも今回は再実行していない。認証を確認して最初に1件だけ試すこと。
- スクリプトは Python 標準ライブラリのみ。Windows でも動くよう、プロンプトは標準入力、システムプロンプトはファイルで渡す。

[スキルの指示](ai-task-autotuner/SKILL.md) / [データ監査](ai-task-autotuner/references/audit.md) / [タスクレビュー](ai-task-autotuner/references/task-review.md) / [評価設計](ai-task-autotuner/references/evaluation.md) / [改善ループ](ai-task-autotuner/references/loop.md) / [タスク設計](ai-task-autotuner/references/task-design.md)

```powershell
python -B -m unittest discover -s ai-task-autotuner/tests
```

## リポジトリに入れないもの

`.gitignore` で除外している。設定ファイルには作業ディレクトリの実パスなどが入るため、コミットしない。

- `.claude/settings.local.json` などのローカル設定
- スキルが生成する `.slack-off-police.yaml` / ログ
- `slack-off-police/assets/` に置く音声ファイル（著作物になりうる）

## ライセンス

[MIT](LICENSE)
