# スクレイピング証跡検証 引き継ぎメモ

Sonnet サブエージェントで Playwright 証跡（video/trace/screenshot/JSON）を検証した結果。

- mock-shop（scraping-procedure-deck/evals/files/mock-shop/site）では検証済み: trace はコードの手順と1対1で一致、動画も各局面が映っていた
- 外部サイトはネットワークポリシーで 403 のため未検証
- 既知の仕様: 新規タブは別動画になる／動画と trace の時刻は約1秒ずれる／1fps抽出だと短い局面を見落とす
- 未達: 実サイト検証、絞り込み後の2ページ以上、最終スクショ前の価格確定待ち
- 実行: `npm ci && npx playwright test`（`playwright install` は禁止。executablePath は設定済みか要確認）
