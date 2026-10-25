# 外部実装からの知見

2026-10-05 UTCに公開ソースを確認した。既存の自動監視、version固定、単調時計による待機期限、認証・proxy・CA設定を維持し、役立つ知見を追加する。

## 参照した実装

- `shepsci/kaggle-skill`、commit `a47bff9830af088bfce3bf974343916370137d1f`。[SKILL.md](https://github.com/shepsci/kaggle-skill/blob/a47bff9830af088bfce3bf974343916370137d1f/skills/kaggle/SKILL.md)、[Notebook待機](https://github.com/shepsci/kaggle-skill/blob/a47bff9830af088bfce3bf974343916370137d1f/skills/kaggle/shared/notebook.py)、[CLI応答の扱い](https://github.com/shepsci/kaggle-skill/blob/a47bff9830af088bfce3bf974343916370137d1f/skills/kaggle/shared/kaggle_cli.py)、[監査台帳](https://github.com/shepsci/kaggle-skill/blob/a47bff9830af088bfce3bf974343916370137d1f/skills/kaggle/shared/ledger.py)。
- `NVIDIA/nvidia-kaggle`、commit `2b78cf29f5f30680764292a6592de8d53d4147a8`。[SKILL.md](https://github.com/NVIDIA/nvidia-kaggle/blob/2b78cf29f5f30680764292a6592de8d53d4147a8/skills/nvidia-kaggle-skill/SKILL.md)、[提出・待機](https://github.com/NVIDIA/nvidia-kaggle/blob/2b78cf29f5f30680764292a6592de8d53d4147a8/skills/nvidia-kaggle-skill/scripts/submit_kernel.py)、[追記型の履歴](https://github.com/NVIDIA/nvidia-kaggle/blob/2b78cf29f5f30680764292a6592de8d53d4147a8/skills/nvidia-kaggle-skill/scripts/submission_log.py)、[Dataset公開処理](https://github.com/NVIDIA/nvidia-kaggle/blob/2b78cf29f5f30680764292a6592de8d53d4147a8/skills/nvidia-kaggle-skill/scripts/upload_dataset.py)。

## 取り込む運用

### 最新状態と履歴を分ける

NVIDIAの提出履歴はJSONLへの追記で、ローカル待機のtimeout後も後日の評価を結び付ける。履歴書き込みの失敗で提出そのものを中断しない。shepsciの台帳もイベントを追記している。

同梱helperでは、この考え方を既存waitの `monitor-events.jsonl` として追加した。`job.json` に提出の識別情報、`continuation.json` に最新の再開先、JSONLに観測の履歴を残す。別のwaitで同じジョブを再確認しても履歴を上書きしない。

各行はUTCの `observed_at`、`ref`、`version`、`source_signature`、`monitor_elapsed_seconds`、`event` を持つ。状態を取得できた場合だけ `status`、取得失敗の場合は例外の型だけを `error_type` に記録する。経過時間はそのローカルwaitの開始からであり、Kaggleジョブ全体の実行時間ではない。エラー本文・認証値・ログ本文を履歴へ入れない。

|event|意味|
|---|---|
|`wait_started`|保存された同じref/versionの監視を開始した。|
|`status_observed`|versionを固定したリモートstatusを取得した。|
|`status_retrieval_error`|statusの取得に失敗した。リモート状態は不明で、既存の例外を再送出する。|
|`log_retrieval_error`|status取得後にログの取得へ失敗した。|
|`wait_deadline`|ローカル待機の期限に達した。status欄は最後に観測したリモート状態。|
|`terminal`|既存waitが完了または失敗確認の分岐へ進んだ。cancel_requestedもこの分岐に含まれ、停止完了を証明するものではない。|
|`output_recovery_error`|成果物または保存ログの回収に失敗した。|
|`verification_ready`|成果物の回収が終わり、検証へ進める。実験結果の合格ではない。|

### 待機期限と失敗を区別する

参照実装でもローカル待機のtimeoutはKaggleの実行失敗と別に扱う。期限、通信失敗、リモートerror、出力回収失敗、成果物の検証失敗を区別する。期限後は保存した同じref/versionを監視し、観測不能だけを理由に停止・新規提出を行わない。

shepsciのNotebook待機はstatusの定期取得を中心とし、完了後に出力をダウンロードする。失敗時のログ末尾には行数・文字数の上限がある。稼働中ログのReadTimeoutを直接解消する仕組みとは確認できないため、既存のログAPI監視を継続する。表示用のログ抜粋と保存した証跡を分ける。

### CLI終了コード以外も照合する

shepsciは、CLIが一部の書き込み失敗でも終了コード0を返す場合を扱う。`Kernel push error`、`Dataset creation error`等の診断は書き込みコマンドの応答に限定し、NotebookログやDiscussion本文に引用された語を操作失敗と判定しない。

同梱helperはAPIを直接使う。CLIを併用する場合も、応答の識別情報と実際のref/version、ソース署名、非公開設定、GPU/Internet設定を照合する。曖昧な提出は既存の `submission_unknown` とreconcileを使う。

### complete後も成果物を検証する

参照実装の待機・回収の分離を採用し、提出前に成果物名、schema、件数、入力version、SHA、実際のGPU・ライブラリ版などの合格条件を定める。remote completeとファイル回収後に、この条件を確認してから実験の成功を報告する。出力0件であってもhelperは検証へ進めるため、必要な成果物の存在は実験側で確認する。

## 適用範囲

参考リポジトリはインストールせず、既存スキルを使う。shepsciの確認済みソースは `kaggle>=2.2.4` / `kagglehub>=1.0.2` を要求するが、同梱helperの検証済み依存をそのまま維持する。変更が必要なら専用環境で互換性を確認する。

shepsciのdry-run後の確認方式を、既に許可された試行へ追加の承認手順として適用しない。既存許可の範囲と予算の判断はSKILL.mdに従う。入力容量の処理には [入力サイズと転送](input-size.md) を使い、外部実装のファイル数上限や表示上限をKaggleの入力容量上限と混同しない。
