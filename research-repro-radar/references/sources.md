# 探索ソース

## 優先順

1. Hugging Face Papersの新着・trendingと、関連するModel Card・公式リポジトリ
2. OpenReviewとarXivの原文、著者の公式実装
3. MDPIのAI、InformationのArtificial Intelligence、関連誌のRSS
4. Crossrefの新着メタデータ
5. GitHubの公式releaseと再現報告

検索結果ページ、SNS投稿、まとめ記事だけで評価しない。原論文、公式モデルカード、公式コードのいずれかへ到達する。

## Hugging Face

`hf` CLIがある場合はJSONを使う。

```text
hf papers ls --sort=trending --format=json
hf papers ls --sort=publishedAt --format=json
hf papers search "text summarization" --format=json
hf papers search "text classification efficient" --format=json
hf papers search "information extraction" --format=json
hf papers search "structured extraction noisy text" --format=json
hf papers search "small language model" --format=json
hf papers search "quantization int4 inference" --format=json
hf papers search "distillation encoder" --format=json
hf papers search "lightweight vision" --format=json
hf papers search "coding agent" --format=json
hf papers search "cpu inference throughput" --format=json
```

trendingの上位数件だけを取って終わりにしない。trendingは全体を見て、新着とクエリ検索を必ず併用する。
公開日は絞り込み条件にしない。何年前の論文・モデルでも、今trendingなら候補にする
（PagedAttention、OpenDevin、TradingAgentsのような数年前の論文がtrending上位に居座ることは普通にある）。
CLIやAPIの1ページに収まらない場合は、ページネーションを続ける。
`exploration.min_candidates_gathered`に届かないなら、クエリを足すかソースを広げる。
trending上位は関心語との一致が弱くてもワイルドカード候補になれる。モデルがある場合はModel Cardからライセンス、容量、推奨ハードウェアを確認する。

## MDPIとCrossref

MDPI全誌を走査せず、AI、InformationのArtificial Intelligence、Applied Sciences、Sensorsなど関心に近いRSSだけを見る。Crossrefでは`from-created-date`または`from-update-date`で直近分を取り、DOIで重複排除する。

## 話題性の信号

- Hugging Face Papersのtrending順位
- 原論文と同時公開されたコードやモデル
- 複数の独立した実装・再現報告
- 最近のrelease、issue対応、活発な保守
- 精度、速度、メモリ、開発時間の比較が明示されている

引用数やスター数だけで判断しない。trending上位で味のアンカーに近い候補は、関心語と完全一致しなくてもワイルドカードとして残してよい。

スコアは並べ替えのための道具であって、判断そのものではない。上位N件を機械的に採用せず、
`review_pool`を上から読んで、刺さらない理由を言えるものから落とす。読解は実行より桁で安いので、
GPU必須などで実行できない候補も読む対象に含める。
