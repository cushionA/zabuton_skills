# 引き継ぎ: AI Task Autotuner（開発ブランチ `claude/keen-edison-8il7ag`）

> 2026-10-20追記: 本書は初期開発時の記録です。今回のレビュー・修正・CLI切り替えと
> 未解決点は [ASTRA_HANDOFF.md](ASTRA_HANDOFF.md)、元データと証拠は
> [README.md](README.md) を参照してください。以下のテスト件数・
> 既定値・データの保管先は当時の情報です。
>
> 2026-10-24追記: main への取り込み時に、本書をスキル直下から `review-data/` に移しました。
> 試験データZIPは公開リポジトリから外しています（[README.md](README.md) 参照）。

2026-10-18〜19 の作業の引き継ぎ。main に取り込むときは、このファイルを残すか削除するか決めること（スキルの動作には関係しない）。

## 1. これは何か

テストデータとタスクの目的から、AI（API想定）タスクを設計し、実測で改善し続けるスキル。流れは次のとおり。

1. データ監査と合格判定
2. 実験設計
3. 評価の固定と検証
4. 改善ループ（精度 → 効率・構造）
5. 最終評価と成果物

API の代わりに `claude -p` を使う（既定は Haiku、思考は最小）。使い方は [README の節](../../README.md#ai-task-autotuner)、指示は [SKILL.md](../SKILL.md) にある。

## 2. 依頼内容と反映先

| 依頼 | 反映先 |
|---|---|
| テストデータからAIタスクを設計し、実測で自律改善（Autoresearch / Skill Evolver を参考） | SKILL.md、`references/loop.md`、`ledger.py`（採用・却下、事前登録、揺らぎの判定） |
| `claude -p` を API の代わりにする。疑似APIは haiku、明示があれば変更 | `llm_call.py`（cli / api の2系統）、`references/backends.md` |
| 停止条件はユーザー指定を優先し、なければ改善の限界で止める | `ledger.py status` の RECOMMEND（制限、目標、頭打ち） |
| ある程度精度が出たら効率とタスク構造を改善 | 品質フェーズ → 効率フェーズ（品質下限の固定、DRIFT 表示） |
| haiku の実行を詳細に分析し、改善を記録 | `analyze_run.py`、`journal.md`、`ledger.jsonl` |
| SKU 突合に特化させず汎用にする | テンプレートは汎用。SKU 固有の内容は評価用データにだけ置く |
| 評価方法（正解比較、LLM採点、採点器の検証、pass^k） | `references/evaluation.md`、`validate_eval.py`、`analyze_run.py --consistency` |
| データ監査と、問題があれば実験前に報告して止まる | `quality_gate.py`（PASS / REVIEW / STOP / INCOMPLETE）、`references/audit.md` |
| データ分析パイプライン | `profile_data.py`、`audit/checks.py`、`close_read.jsonl` |
| 出力以外の推論は英語 | SKILL.md の基本ルール、プロンプトのテンプレート |
| コンテキストの節約 | SKILL.md「Context discipline」、STATE.md、出力の要約、サブエージェントへの委任 |
| 行き詰まったとき、似た方向の袋小路を防ぐ | 手段の種類の管理、DIVERSIFY / FRESH_EYES / EXPLORE、類似仮説の拒否、分岐点 |
| 出力物（タスク設計、テストデータなど） | `export_deliverables.py`、`references/task-design.md` の task_design.md 雛形 |
| AI 以外（前処理など）を補助に使う。AI 不要と分かれば別途報告 | コードだけの候補の計測、状態表示の NO-LLM 行、`non_ai_findings.md` |
| 出力トークン上限などの設定を見積もる | `production_config.json`（実測の分布から算出し、根拠を添える） |
| 思考は既定で最小かゼロ | effort low、`thinking: "off"`（api 系のみ有効。cli では切れない） |

## 3. ファイル構成

```
ai-task-autotuner/
  SKILL.md                  手順の全体（英語）
  references/               audit / evaluation / loop / task-design / backends / report（英語）
  scripts/                  Python 標準ライブラリのみ（api 系だけ anthropic SDK が必要）
    init_workspace.py  profile_data.py  quality_gate.py  llm_call.py  run_experiment.py
    validate_eval.py  reference.py  ledger.py  analyze_run.py  export_deliverables.py
    relocate_workspace.py  common.py  workspace.py
  assets/workspace_template/  charter.json、audit/checks.py、eval/dataset.py・score.py、c000-baseline
  tests/                    単体テスト・結合テスト 15件（偽のモデルで全工程を通す）
  evals/evals.json          実データでの試験用の依頼文 2件
  HANDOFF.md                このファイル（現在は review-data/ に移動）
```

テスト: `python -B -m unittest discover -s ai-task-autotuner/tests`（2026-10-19 時点で 17件成功）。

## 4. 実データでの試験（iteration-1）

提供データ: 楽天→au SKU突合の通常セット（152ペア）。データ本体と実行記録は公開リポジトリに入れていない。このチャットで zip として受け渡す（「8. データ」参照）。

| 試験 | 状態 | 結果 |
|---|---|---|
| ①SKU突合・スキルあり | **完了**（利用上限で3回中断し、そのつど再開） | 下記参照 |
| ①SKU突合・スキルなし（比較用） | 途中（参照作成と実行器の作成まで） | 未評価 |
| ②au→楽天 同一商品特定・スキルあり | **完了** | 下記参照 |
| ②同・スキルなし（比較用） | 途中（監査報告の後、実行器の作成中に中断） | 未評価 |

### ② 同一商品特定・スキルあり（完了）

- **監査（モデル呼び出し前に REVIEW で停止）:** 次の問題を見つけた。
  - 誤ペア疑い3件
  - 楽天1出品にauの2出品が対応するケースが6件
  - 「該当なし」の問題が存在しない
  - 単純な類似度だけで152件中139〜143件の正解が1位に来る（候補を無作為に選ぶと簡単すぎる）
  - 楽天の商品コードが au 側のデータにそのまま出てくるペアが64件（比較用の実行は、説明文に限ると38件と報告）
  - テスト上の仮のユーザー回答「推奨案でOK」で再開した。スキルなしの比較用も、ほぼ同じ指摘をして停止した。
- **タスクの構築:** 1ペアから「正解あり」「正解を抜いて『該当なし』」の2問を作り、計304問にした。候補は似た出品の上位8件。dev 156問、holdout 148問。
- **結果（未検証のペア表との一致率。正答率ではない）:**
  - holdout: 0.909 → **0.951**
  - 誤採用率: 0.063 → 0.028
- **最大の改善:** 先に比較を書かせてから選ばせる出力順（dev: 0.881 → 0.968）。モデルが自分の理由と矛盾した選択をしていたのが原因だった。
- **効率化:** コードによる確定（18%）、候補を4件に絞る、説明文の削除で、費用を約55%削減した（キャッシュなし換算）。品質下限 0.94 は維持した。
- **コードだけの場合:** 0.827 にとどまったため、モデルは必要と判断した。
- **費用と時間:** 費用 $1.60（予算 $3）、作業時間 約0.8時間（上限2時間。中断時間は除く）。
- **本番設定（実測から算出）:** max_tokens 2048、timeout 30秒、同時実行 6、effort low、thinking off。

### ① SKU突合・スキルあり（完了）

- **dev / holdout:** dev は normal_50 の50ペア（楽天 SKU 642項目）、holdout は41ペア（544項目）。
- **参照データ:** Opus（effort high）を2回独立に実行して作り、不一致は裁定した。人の確認はしていない。
- **採点の版上げ:** 途中で参照データの誤りが見つかり、評価を v2、v3 と更新して全実行を採点し直した（評価を変えるときの手順どおり）。
- **実験:** 13件。品質フェーズの後、効率フェーズに移った。
- **最終構成:** 次の2段の組み合わせ。
  - コードで「完全一致ペア」を確定する（152ペア中78ペア・561枠。参照のある範囲では全件一致）。
  - 残りのペアだけ Haiku を1回呼ぶ。出力は簡潔な形式、入力は圧縮したテキストにする。
- **結果（Opus 参照との一致率。正答率ではない）:**
  - holdout: 初期構成 0.818 → 最終 **0.925**。差 +0.107、ペア単位の95%区間 +0.02〜+0.24。
  - 参照が否定する au 行への誤った紐付けは0件。
- **全152ペア:** matched 1,325 / unmatched 263 / conditional 163 / review 183 / 失敗 0。自動確定 82.1%。
- **処理時間:** 同時実行6で壁時計153秒、AI 呼び出し74回、AI を使ったペアの中央値8.9秒。
- **費用:** 約 $0.001 / ペア（非キャッシュ換算の定価）。
- **上限の確認:** Sonnet で同じプロンプトを試すと 0.854（Haiku は 0.896）。残りの誤りはモデルの能力ではなく、方針や参照の曖昧さによると分かった。
- **コードだけの場合:** 0.597 で、AI は必要と判断した。
- **本番設定:** max_tokens 23,040、timeout 110秒、同時実行6、effort low。
- **限界:** 少数の大きな商品ペアが揺れを左右している。同じ構成でも dev の一致率が実行ごとに0.08〜0.19変わった。
- **費用の合計（`claude -p` の定価換算）:** 約 $15.7。うち Opus 参照が $10.8。

## 5. 試験から分かったこと（スキルに反映済み）

- **利用枠:** `claude -p` は、スキルを動かしている Claude Code と同じ利用枠を使う。4並列の試験と Opus の参照作成で、20〜30分で上限に達した。
  - 上限に達した実行を打ち切る仕組み、事前見積もりの手順、中断時間を除く計時、`clock resume --gap` を追加した。
- **思考:** `claude -p` では Haiku の思考を止められない。effort low でも出力の約40%が思考だった。
  - `MAX_THINKING_TOKENS=0`、`CLAUDE_CODE_DISABLE_THINKING=1`、`CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1`、`alwaysThinkingEnabled:false` はどれも効かなかった。
  - api 系なら `thinking: {"type":"disabled"}` で止められる（未検証）。
- **キャッシュ:** `claude -p` はプロンプト全体をキャッシュする。同じ内容を再実行すると約1/6の費用になる。
  - 費用の比較は、キャッシュなし換算（`cost_uncached_list_usd_per_unit`）を既定にした。
- **データ分割の不具合:** 件数の少ない区分が開発用に偏っていた。修正済み。
- **揺らぎの見積もり（①の指摘から修正済み）:** SKU 1件ずつを独立とみなす計算では揺らぎを過小に見積もっていた。大きな商品ペアは、丸ごと結果が入れ替わる。
  - 揺らぎの幅と区間を、商品ペアなどのまとまり単位（ブートストラップ）で計算するようにした。
  - 評価を変えた後は、台帳の「最良」と下限を最新の評価で計算し直す。
  - 精読した商品は開発用データに回す。
  - 最終評価用データでは、集計だけを表示する。
  - 古い作業フォルダを新しい版に合わせる `init_workspace.py --migrate` を追加した。
- **その他（②の指摘から修正済み）:**
  - 上限で壊れた実行は判定から除外する。
  - 監査をやり直しても、承認を引き継ぐ。
  - 安定した構成で揺らぎを測り直せるようにした。
  - 品質低下の累計（DRIFT）を表示する。
  - 成果物の書き出しで、思考の量（effort）の誤記と、評価キットを作業フォルダの外で使えない問題を直した。
  - 空の応答を1回だけ再試行する。

## 6. 既知の問題・未検証

- api 系（Anthropic SDK）は実通信で検証していない（この環境に API キーが無かった）。最初は1件で試すこと。
- `limits.deadline` は壁時計の時刻で、中断しても延びない。時間で制限するなら `max_hours` を使う。
- ②の試験中、環境によってはサブエージェントのファイル書き込みツールが report.md の作成を拒否した（シェルで回避。SKILL.md に注記あり）。
- 比較用（スキルなし）の2件は途中までで、スキルあり・なしの定量比較はまだない。
- skill-creator の評価ビューア（generate_review.py）と、説明文（description）の起動精度の最適化は未実施。

## 7. ローカルでの再開手順

1. スキルを配置する（Windows の例）。

   ```powershell
   cmd /c mklink /J "$env:USERPROFILE\.claude\skills\ai-task-autotuner" "<repo>\ai-task-autotuner"
   ```

2. データ zip（`ai-task-autotuner-testdata-20261019.zip`）を展開する。`inputs/` の中に、元のデータ zip と案内文がある。元のデータ zip も展開する（例: `D:\work\sku-normal-v1`）。
3. **新しく試す場合:** `evals/evals.json` の依頼文の `DATA_DIR` / `BRIEF_PATH` を置き換え、Claude Code にそのまま依頼する。
4. **試験の作業フォルダを手元で動かす場合（追加実験など）:** 例として `iteration-1/eval-1-sku-matching-no-labels/with_skill/outputs/ws` を使う。手順は次のとおり（その後 `init_workspace.py <ws> --migrate` も実行する）。

   ```powershell
   python ai-task-autotuner/scripts/relocate_workspace.py <ws> --data-map /tmp/claude-0/-home-user-zabuton-skills/63e4c5c7-d309-596e-8dd4-3c5a2c09efb2/scratchpad/inputs/sku-normal-v1=D:/work/sku-normal-v1
   python ai-task-autotuner/scripts/quality_gate.py <ws> --verify      # "data unchanged" を確認
   python ai-task-autotuner/scripts/ledger.py clock <ws> resume --gap  # 中断時間を除外
   python ai-task-autotuner/scripts/ledger.py status <ws> --write      # RECOMMEND に従う
   ```

   Claude Code には「`<ws>` の STATE.md と journal.md の末尾を読み、ai-task-autotuner スキルで続きから進めて」と依頼する。runs/ の中の記録にもこの環境のパスが残るが、判定には影響しない。
5. 各試験の成果物は `.../with_skill/outputs/ws/deliverables/` にある。`final_message.md` が報告、`skill_feedback.md` がスキルへの指摘。

## 8. データ

- 公開リポジトリのため、テストデータ・案内文・実行記録はコミットしていない。
- zip の中身:
  - `inputs/`: 元のデータ zip、案内文 `brief_sku.md`
  - `iteration-1/`: 4試験の作業フォルダ・出力・所要時間
  - `pending_improvements.md`
  - `evals.json`

## 9. 次にやること（優先順）

1. 比較用（スキルなし）を同じ条件で回し、スキルあり・なしを skill-creator の評価ビューア（`generate_review.py`）で比べる。
   - 採点項目は `iteration-1/*/eval_metadata.json` に下書きがある。
   - 今回は利用枠の都合で、比較用は2件とも途中まで。
2. 修正後の版で、①または②をもう一度回す。今回の修正（まとまり単位の揺らぎ、最新評価での最良値の再計算など）が意図どおり働くかを確認する。
3. api 系を実際の API キーで1件検証し、thinking off の効果（思考トークンの削減と品質）を測る。
4. Opus で作った参照を人が部分確認する（`reference.py export-csv`）。①で揺れの大きい4ペア（168枠）が優先。
5. 突合タスク向けの採点テンプレートを用意する（判定と行IDの組、許容集合、誤紐付けや過剰主張のタグ）。①の `ws/eval/score.py` が雛形になる。
6. description の起動精度を最適化する（skill-creator の run_loop）。
