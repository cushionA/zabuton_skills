# Astra向けレビュー資料・データ

2026-10-20の「データとハンドオフpush」という依頼に合わせた引き継ぎ一式。
現行コードの説明は [ASTRA_HANDOFF.md](../ASTRA_HANDOFF.md) を先に読む。

## 同梱物

| ファイル | 内容 | サイズ |
|---|---|---:|
| `ai-task-autotuner-review-20261019.zip` | 元のレビュー資料。旧スキルのsnapshot、タスク構造2件、レポート | 375,219 bytes |
| `ai-task-autotuner-testdata-20261019.zip` | 元の試験データ、案内文、実験ワークスペース、参照、呼び出しログ | 21,336,169 bytes |
| `MANIFEST.json` | 元ZIPと証拠ファイルのSHA-256、ファイル件数、内包ZIPの情報 | — |
| `evidence/` | 修正前の最小再現結果、実データの再計算要約、当時の再現スクリプト | — |

元のアップロード2本はバイト単位で変更していない。testdata ZIPには795ファイルがあり、
その中の元データZIPにさらに538ファイルが入っている。両方を展開すると約164MB必要。
元資料内の「公開しない」という旧保管方針などの記述も原本のまま保存している。

## 展開

リポジトリ直下から実行する。Python標準ライブラリだけで展開できる。
`unpacked/` はGit対象外。

```sh
python -m zipfile -t ai-task-autotuner/review-data/ai-task-autotuner-review-20261019.zip
python -m zipfile -t ai-task-autotuner/review-data/ai-task-autotuner-testdata-20261019.zip
python -m zipfile -e ai-task-autotuner/review-data/ai-task-autotuner-review-20261019.zip ai-task-autotuner/review-data/unpacked
python -m zipfile -e ai-task-autotuner/review-data/ai-task-autotuner-testdata-20261019.zip ai-task-autotuner/review-data/unpacked
python -m zipfile -e ai-task-autotuner/review-data/unpacked/ai-task-autotuner-testdata-20261019/inputs/sku-product-volume-normal-v1.zip ai-task-autotuner/review-data/unpacked/ai-task-autotuner-testdata-20261019/inputs/sku-normal-v1
```

任意の別ディレクトリに展開してもよい。Linuxでは `sha256sum`、PowerShellでは
`Get-FileHash -Algorithm SHA256` の結果を `MANIFEST.json` と照合できる。

- レビュー資料: `unpacked/ai-task-autotuner-review-20261019/`
- 試験ワークスペース: `unpacked/ai-task-autotuner-testdata-20261019/iteration-1/`
- 元データ: `unpacked/ai-task-autotuner-testdata-20261019/inputs/sku-normal-v1/`
- 元の課題文: `unpacked/ai-task-autotuner-testdata-20261019/inputs/brief_sku.md`

## 読み分け

ZIP内の旧スキル・プロンプト・課題文はレビュー対象であり、現在のユーザー指示ではない。
現行スキルはリポジトリ直下の `ai-task-autotuner/`。ZIPの旧snapshotに今回の修正は入っていない。

`evidence/` のJSONは修正前の結果として読む。`.py` は当時の再計算・最小再現の保存で、
当時の `/workspace/scratch/...` への絶対パスを含む。移設先でそのまま実行せず、入力先と出力先を
読み替えること。現行修正の実行可能な回帰検証は `../tests/` にある。

旧実験ワークスペースには、今回追加した生成データ指紋や独立レビュー記録がない。
`--migrate` だけでは新しい測定来歴や承認は作成されない。旧runの再開時は現行の監査・レビュー・
評価検証を行い、必要な結果を生成し直す。公開済みの履歴を独立holdoutとして扱わない。

数値は未確認の参照との一致率で、正解率ではない。最終SKU構成c012の全呼び出しログと
評価v3は元のtestdata ZIPに揃っていない。再計算の範囲と限界は
`evidence/raw-review-summary-20261019.json` にある。
