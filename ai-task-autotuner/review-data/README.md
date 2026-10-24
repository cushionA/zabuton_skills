# Astra向けレビュー資料・データ

2026-10-20の「データとハンドオフpush」という依頼に合わせた引き継ぎ一式。
現行コードの説明は [ASTRA_HANDOFF.md](ASTRA_HANDOFF.md) を先に読む。初期開発の記録は
[HANDOFF.md](HANDOFF.md)。どちらも当時の記録で、現行の手順は [../SKILL.md](../SKILL.md) と
`../tests/` を正とする。

## 同梱物

| ファイル | 内容 | サイズ |
|---|---|---:|
| `ai-task-autotuner-review-20261019.zip` | 元のレビュー資料。旧スキルのsnapshot、タスク構造2件、レポート | 375,219 bytes |
| `MANIFEST.json` | 元ZIPと証拠ファイルのSHA-256、ファイル件数、内包ZIPの情報 | — |
| `evidence/` | 修正前の最小再現結果、実データの再計算要約、当時の再現スクリプト | — |
| `HANDOFF.md`, `ASTRA_HANDOFF.md` | 開発時の引き継ぎ記録（2026-10-24にスキル直下から移動） | — |

レビュー資料ZIPはバイト単位で変更していない。商品データ・行ごとの判定・参照・呼び出しログは
元から含まない。

## 公開リポジトリに入れないもの

試験データ `ai-task-autotuner-testdata-20261019.zip`（21,336,169 bytes、795ファイル）は
2026-10-24に公開リポジトリから外した。実商品データから作った全プロンプト・応答（`calls.jsonl`）、
店頭ページ、実験ワークスペースを含み、原本に「do not publish」と書かれているため。
必要なときはリポジトリの外で受け渡し、`MANIFEST.json` のSHA-256で原本か確かめる。
`.gitignore` で再追加を防いでいる。

同じ理由で、`evidence/raw-review-summary-20261019.json` の商品タイトル10件を伏せ字にした。
他の値は変えていない。元のSHA-256とサイズは `MANIFEST.json` に残している。

## 展開

リポジトリ直下から実行する。Python標準ライブラリだけで展開できる。
`unpacked/` はGit対象外。

```sh
python -m zipfile -t ai-task-autotuner/review-data/ai-task-autotuner-review-20261019.zip
python -m zipfile -e ai-task-autotuner/review-data/ai-task-autotuner-review-20261019.zip ai-task-autotuner/review-data/unpacked
```

別途受け取った試験データZIPは、リポジトリの外に置いて展開する。中の元データZIP
（`inputs/sku-product-volume-normal-v1.zip`）も展開すると、合わせて約164MB必要。
Linuxでは `sha256sum`、PowerShellでは `Get-FileHash -Algorithm SHA256` の結果を
`MANIFEST.json` と照合できる。

- レビュー資料: `unpacked/ai-task-autotuner-review-20261019/`
- 試験データZIPの中: 試験ワークスペースは `iteration-1/`、元データは `inputs/` の内包ZIP、
  元の課題文は `inputs/brief_sku.md`

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
