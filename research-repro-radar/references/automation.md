# 無料の定期実行

## 組み立て

探索と実行は呼び出し元のエージェントが行い、採点・state更新・送信はPythonスクリプトを使う。LLMの判断ステップは差し替え可能にする。

1. スケジューラ（ローカルのタスクスケジューラ、またはGitHub Actionsのcron）
2. 探索と正規化 → [candidate-schema.md](candidate-schema.md) の形へ
3. `scripts/rank_candidates.py` で採点と実行時間予算の割り当て（既報の除外と繰り越しの再投入もここ）
4. LLM判断ステップ（原文確認、簡易検証の解釈、報告文の作成）→ `scripts/llm.py`
5. `scripts/send_email.py` でメール送信とstate更新

LLMをどう呼ぶかだけがプラットフォームで変わる。`scripts/llm.py` の `judge()` が唯一の接点で、
`RADAR_LLM` 環境変数で `claude` と `codex` を切り替える。未設定ならPATHにある方を使う。

```text
RADAR_LLM=claude python scripts/llm.py   # 接続確認
RADAR_LLM=codex  python scripts/llm.py
```

初回はスキル直下で `python -m pip install -r requirements.txt` を実行する。
判断対象はすべてstdinで渡し、JSONスキーマに合わない応答は「判断できず」として候補を保留に倒す。
Claudeにもスキーマ全文をstdinで渡し、両バックエンドの返り値を`jsonschema`で検証する。
不正なスキーマや依存未導入は設定エラーとして停止し、弱い検証へ切り替えない。
プラットフォーム固有の注意は [../platform/claude.md](../platform/claude.md) と
[../platform/openai.md](../platform/openai.md) を見る。

現金支出ゼロでは、未知の論文コードを毎回自動で直して完全再現することは狙わない。1回直しても動かない候補は保留する。

## state

`--state-dir` で指定したディレクトリに4つのJSONを置く。すべてのスクリプトで同じディレクトリを渡す。

| ファイル | 中身 | 効果 |
|---|---|---|
| `seen.json` | 詳細報告した候補のキーと日時 | 次回以降 `already_reported` で除外。365日または2000件で失効 |
| `queue.json` | 実行予定・時間切れの未完了候補の元レコード | 完了記録まで次回へ再投入。`max_carry_over_days`を過ぎたら失効 |
| `mail.json` | 送信済み指紋と`last_sent_at` | 詳細報告の二重送信防止と、生存報告の間隔判定 |
| `freshness.json` | 実行ごとの詳細報告件数と、うち鮮度基準内の件数 | 直近数回が掘り起こしばかりなら鮮度加点を自動でブースト（`adaptive_freshness`） |

- 実行予定への登録は完了ではない。`scheduled_checks`と`run_time_budget`で溢れた候補を保持し、途中終了しても次回へ残す。
- GPU・RAM・費用などの制約による保留は読解・報告対象に残すが、同じ制約のまま自動再試行するキューには入れない。
- ダイジェストで触れただけの候補は既報・完了扱いにしない。既存の未完了キューも保持する。
- 今回の探索で再発見された候補は新しいメタデータを使い、最初の繰り越し日と繰り越し回数を引き継ぐ。
- `send_email.py`は送信成功時に自動で`freshness.json`を更新する。対話実行で報告を確定したときは
  `scripts/record_run.py report.json --state-dir state`を呼ぶ。詳細報告がゼロでも実行結果を記録する。
- 報告の`recommended`のIDと`completed_ids`だけをキューから取り除く。`completed_ids`は、検証完了または理由を確認して見送った候補に限り、未着手・中断・時間切れを含めない。
- 送信が不要な実行も完了IDと鮮度実績を記録する。`--dry-run`ではstateを書き換えない。対話の`record_run.py`とメールの記録を同じ実行で二重に呼ばない。
- 公開日不明の候補は鮮度の分母・分子に含めず、判定できる候補がゼロなら鮮度ブーストを保留する。
- `run_id`がある報告の鮮度記録は同じ実行を二重に数えない。メールではIDが無い場合も送信指紋を使い、送信後のstate更新が中断したら再実行で不足分を記録する。

実行を跨いでstateを保持できることが実行基盤の必須条件。
実行ごとにファイルシステムが消える基盤では、繰り越しも既報の重複排除も成立しない。
永続領域を持てない基盤は探索の下書きだけに使い、stateを持つ側へ結果を渡す。

## GitHub Actions

`.github/workflows/research-repro-radar.yml`（リポジトリ直下）で実装済み。現在は対話実行主体のためcronをコメントアウトしており、手動実行のみ有効。週次再開の指示があった場合にcronを戻す。

公開リポジトリの標準runnerは無料。非公開リポジトリはプランの無料枠を消費するため、支出上限を0にし、探索は週1回から始める。

構成は2ジョブに分けている。理由はSecretsのスコープを絞るため。

- `research`ジョブ: 探索・採点・読解確認・レポート作成。`claude-code-action`でClaude Codeを起動し、
  `CLAUDE_CODE_OAUTH_TOKEN`（サブスク枠、`claude setup-token`で発行）で認証する。
  論文・READMEなど信用できない外部データをWebSearch/WebFetchで読むジョブなので、
  SMTP認証情報はこのジョブに渡さない。同じ理由で、候補の公式コードを実行する
  簡易確認/比較済みレベルの検証はここでは行わず、読解確認レベルに留める。
  依存をインストールし、前回の報告ファイルを消してから`research-repro-radar/ci-state`へ`report.json`と`body.md`を書き出し、コミットしてpushする。
  `report.json`の`run_id`を環境変数`RADAR_RUN_ID`と一致させる。`completed_ids`も出力し、stateの完了記録は`send`ジョブに任せる。
- `send`ジョブ: `research`の後に実行。`scripts/send_email.py`を直接呼ぶだけで、
  エージェントもWeb取得も行わない。`research`の出力IDを`--expected-run-id`へ渡し、報告のIDが不一致なら送信しない。`send`だけの再実行でも元の報告IDを引き継ぐ。SMTP認証情報はこのジョブにだけ渡す。

必要なSecrets: `CLAUDE_CODE_OAUTH_TOKEN`、`RESEARCH_RADAR_SMTP_HOST`、`RESEARCH_RADAR_SMTP_PORT`、
`RESEARCH_RADAR_SMTP_USERNAME`、`RESEARCH_RADAR_SMTP_PASSWORD`、`RESEARCH_RADAR_EMAIL_TO`
（`RESEARCH_RADAR_EMAIL_FROM`は任意。未設定・空文字ならSMTPユーザー名を使う）。初回は`workflow_dispatch`の`dry_run`を`true`にして手動実行し、
`report.json`と`body.md`の内容を確認する。

- 権限は`contents: write`（`ci-state`のコミットに使う）。
- 同時実行は1にする（`concurrency`で設定済み）。
- `research`は45分、`send`は10分でtimeoutする。
- `--state-dir research-repro-radar/ci-state`の中身はリポジトリへコミットして持ち越す。
  `research-repro-radar/state`はローカル対話実行専用で、CIとは別系統（`.gitignore`済み）。

## Kaggle

`references/profile.json`の`gpu.enabled`が`false`なら何もアップロードしない。初回はKaggleアカウント、CLI認証、Notebookの公開範囲、データライセンスをユーザーが確認する。

```text
kaggle kernels push -p <job-directory> --accelerator NvidiaTeslaT4 --timeout 3600
kaggle kernels status <owner>/<kernel-slug>
kaggle kernels output <owner>/<kernel-slug> -p <output-directory> --file-pattern ".*\.(json|csv|log)$"
```

公式CLIの2026年8月時点の注意に従い、既定イメージではP100を選ばずT4を使う。待機は最大60分、再投入は自動で行わない。

## メール

メールコネクターが利用可能なら優先する。SMTPの場合は`RESEARCH_RADAR_SMTP_HOST`、`RESEARCH_RADAR_SMTP_PORT`、`RESEARCH_RADAR_SMTP_USERNAME`、`RESEARCH_RADAR_SMTP_PASSWORD`、`RESEARCH_RADAR_EMAIL_TO`をsecretとして設定する。

```text
python scripts/send_email.py report.json report.md --state-dir state --heartbeat-days 14
```

`report.json`は次の配列を持つ。

- `recommended`: 詳細報告。各項目に`id`、`title`と、`large_effect`または`deep_dive_value`の`category`。鮮度判定用に`published_at`も引き継ぎ、不明なら`null`
- `digest`: ダイジェスト。各項目に`id`、`title`。検証まで到達しなかった上位候補
- `completed_ids`: 任意の文字列配列。詳細報告に入らなかった候補の検証完了・明示的な見送りを記録する。`recommended`のIDは自動で完了扱いになる

CIでは`run_id`も必須にし、`RADAR_RUN_ID`（GitHub実行IDと実行回数）をそのまま保存する。

送信の判定は`send_email.py`が行う。

| 状態 | 送るもの |
|---|---|
| `recommended`が1件以上 | 詳細報告。件数上限なし |
| `recommended`が空で`digest`が1件以上 | ダイジェスト。既定で最大15件 |
| どちらも空 | `--heartbeat-days`の間隔を過ぎていれば「該当なし」だけ送る |

詳細報告は内容が同じなら再送しない。ダイジェストと生存報告は毎回届くことが目的なので、
件名に日付が入り実行ごとに送られる。初回は`--dry-run`で確認し、送信先と継続送信が承認されてから本送信する。

## 参考仕様

- GitHub Actions billing: https://docs.github.com/en/billing/concepts/product-billing/github-actions
- Hugging Face Papers CLI: https://huggingface.co/docs/huggingface_hub/en/guides/cli#hf-papers
- Kaggle kernels CLI: https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md
- Crossref REST API: https://www.crossref.org/documentation/retrieve-metadata/rest-api/
