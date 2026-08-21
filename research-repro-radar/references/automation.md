# 無料の定期実行

## 組み立て

オーケストレータはPythonに固定し、LLMは差し替え可能な判断ステップとして呼ぶ。

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

判断対象はすべてstdinで渡し、ツールを使わせず、JSONスキーマに合わない応答は「判断できず」として
候補を保留に倒す。この検証は両方のバックエンドで共通なので、切り替えても挙動が変わらない。
プラットフォーム固有の注意は [../platform/claude.md](../platform/claude.md) と
[../platform/openai.md](../platform/openai.md) を見る。

現金支出ゼロでは、未知の論文コードを毎回自動で直して完全再現することは狙わない。1回直しても動かない候補は保留する。

## state

`--state-dir` で指定したディレクトリに3つのJSONを置く。両スクリプトで同じディレクトリを渡す。

| ファイル | 中身 | 効果 |
|---|---|---|
| `seen.json` | 詳細報告した候補のキーと日時 | 次回以降 `already_reported` で除外。365日または2000件で失効 |
| `queue.json` | 時間切れで検証できなかった候補の元レコード | 次回の入力へ自動で再投入。`max_carry_over_days`を過ぎたら失効 |
| `mail.json` | 送信済み指紋と`last_sent_at` | 詳細報告の二重送信防止と、生存報告の間隔判定 |

- 繰り越した候補は`lookback_days`を過ぎても除外しない。除外するとキューが意味を失う。
- 繰り越すのは`run_time_budget`で溢れた分だけ。GPU必須やRAM超過は次回も同じ制約に当たるので繰り越さない。
- ダイジェストで触れただけの候補は既報にしない。まだ検証していないので、キューに残す。
- 今回の探索で再発見された候補は、キューの古いメタデータより新しい方を使う。

実行を跨いでstateを保持できることが実行基盤の必須条件。
実行ごとにファイルシステムが消える基盤では、繰り越しも既報の重複排除も成立しない。
永続領域を持てない基盤は探索の下書きだけに使い、stateを持つ側へ結果を渡す。

## GitHub Actions

`.github/workflows/research-repro-radar.yml`（リポジトリ直下）で実装済み。週1回のcronと手動実行に対応する。

公開リポジトリの標準runnerは無料。非公開リポジトリはプランの無料枠を消費するため、支出上限を0にし、探索は週1回から始める。

構成は2ジョブに分けている。理由はSecretsのスコープを絞るため。

- `research`ジョブ: 探索・採点・読解確認・レポート作成。`claude-code-action`でClaude Codeを起動し、
  `CLAUDE_CODE_OAUTH_TOKEN`（サブスク枠、`claude setup-token`で発行）で認証する。
  論文・READMEなど信用できない外部データをWebSearch/WebFetchで読むジョブなので、
  SMTP認証情報はこのジョブに渡さない。同じ理由で、候補の公式コードを実行する
  簡易確認/比較済みレベルの検証はここでは行わず、読解確認レベルに留める。
  `research-repro-radar/ci-state`へ`report.json`と`body.md`を書き出し、コミットしてpushする。
- `send`ジョブ: `research`の後に実行。`scripts/send_email.py`を直接呼ぶだけで、
  エージェントもWeb取得も行わない。SMTP認証情報はこのジョブにだけ渡す。

必要なSecrets: `CLAUDE_CODE_OAUTH_TOKEN`、`RESEARCH_RADAR_SMTP_HOST`、`RESEARCH_RADAR_SMTP_PORT`、
`RESEARCH_RADAR_SMTP_USERNAME`、`RESEARCH_RADAR_SMTP_PASSWORD`、`RESEARCH_RADAR_EMAIL_TO`
（`RESEARCH_RADAR_EMAIL_FROM`は任意）。初回は`workflow_dispatch`の`dry_run`を`true`にして手動実行し、
`report.json`と`body.md`の内容を確認してから週次cronに任せる。

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

`report.json`は次の2つの配列を持つ。

- `recommended`: 詳細報告。各項目に`id`、`title`と、`large_effect`または`deep_dive_value`の`category`
- `digest`: ダイジェスト。各項目に`id`、`title`。検証まで到達しなかった上位候補

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
