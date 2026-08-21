# 候補JSONの形式

`scripts/rank_candidates.py` の入力形式。JSON配列、単一JSONオブジェクト、JSON Linesのいずれでも読める。

探索で集めた候補は、採点にかける前に必ずこの形へ正規化する。

## 必須

| フィールド | 型 | 説明 |
|---|---|---|
| `title` | string | 空文字不可 |
| `source` | string | `profile.json`の`sources`に`enabled`で載っている名前と一致させる。現在は`huggingface` `openreview` `arxiv` `mdpi` `github` `crossref`。一致しないものは`disabled_or_unknown_source`で落ちる |
| `url` | string | 原論文、モデルカード、公式リポジトリのいずれか |

加えて、重複排除キーとして `doi` `arxiv_id` `id` `url` のうち最低1つが必要（この順で使う）。

## 任意

| フィールド | 型 | 既定 | 効果 |
|---|---|---|---|
| `abstract` | string | `""` | `title`と結合して関心キーワードの照合に使う |
| `published_at` | ISO日付文字列 | なし | 鮮度加点のみに使う。年代を理由に除外しない。3日以内+3、7日以内+2、`lookback_days`以内+1、それ以降+0。`adaptive_freshness`が発動中はこの加点だけが`boost_multiplier`倍される |
| `hot_signals` | 非負整数 または 文字列配列 | 0 | 配列なら大文字小文字を無視して重複排除した件数。最大4件までを1件あたり+2 |
| `trend_rank` | 正整数 | なし | Hugging Face Papersのtrending順位。3位以内+5、10位以内+3、30位以内+1 |
| `has_code` / `has_model` / `has_data` | boolean | `false` | それぞれ +4 / +3 / +2 |
| `requires_gpu` | boolean | `false` | `false`なら+3。`true`で`gpu.enabled`が`false`なら`gpu_disabled`で保留 |
| `estimated_cpu_minutes` | number | 10 | `max_cpu_minutes_per_candidate`超過で保留。実行時間予算の消費量にもなる |
| `estimated_gpu_minutes` | number | 10 | `requires_gpu`が`true`のときの所要時間 |
| `estimated_ram_gb` | number | なし | `max_ram_gb`超過で保留 |
| `estimated_cost_jpy` | number | 0 | `money_budget_jpy`超過で除外 |

`estimated_*` を埋めないと全候補が一律10分として扱われ、実行時間予算の配分が意味を持たなくなる。モデル容量、必要な依存、公式が示すハードウェア要件から粗くてよいので入れる。

## 繰り越しで付く値

`--state-dir`を使うと、時間切れの候補が`queue.json`へ保存され次回の入力に自動で戻る。
そのとき`carried_over_since`（最初に繰り越した日）と`carry_count`（繰り越し回数）が付く。
手で書く必要はない。年代による除外は無いので、繰り越しは純粋に「まだ実行できていない」を表す。

## 例

```json
{
  "id": "arxiv:2508.01234",
  "arxiv_id": "2508.01234",
  "title": "A tiny int8 encoder for on-device document classification",
  "abstract": "We quantize ... cpu inference ...",
  "source": "huggingface",
  "url": "https://huggingface.co/papers/2508.01234",
  "published_at": "2026-08-18",
  "trend_rank": 4,
  "hot_signals": ["official code", "third-party reproduction"],
  "has_code": true,
  "has_model": true,
  "has_data": false,
  "requires_gpu": false,
  "estimated_cpu_minutes": 8,
  "estimated_ram_gb": 4
}
```

## 出力

- `counts`: 入力件数と各区分の件数。ダイジェストや実行記録に使う
- `ranked_candidates`: 採点を通った候補をスコア降順で全件
- `scheduled_checks`: そのうち実行時間予算に収まる分。ここまでが今回検証する対象
- `deferred`: 予算切れ、GPU不可、CPU時間超過、RAM超過。`reason`に理由が入る
- `rejected`: 対象外。`reason`は`invalid_record` `duplicate` `already_reported` `disabled_or_unknown_source` `not_relevant_or_hot` `cash_budget_exceeded`。
  年代だけを理由にした`rejected`は無い。話題性(`hot_signals`/`trend_rank`)も関心一致も無い候補が`not_relevant_or_hot`になる。

`ranked_candidates`と`deferred`の`id`は重複排除キー（`doi` `arxiv_id` `id` `url` の優先順）に揃えてある。
`report.json`の`recommended`と`digest`にはこの`id`をそのまま入れる。`seen.json`が同じキーで既報を覚えるため、
ここがずれると既報の除外が効かなくなる。

壊れた候補は`invalid_record`として`rejected`に落ち、`detail`に原因が入る。1件の欠損で実行全体は止まらない。
