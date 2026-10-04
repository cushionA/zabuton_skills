# スクレイピング証跡検証 引き継ぎメモ

Sonnet サブエージェントで Playwright 証跡（video/trace/screenshot/JSON）を検証した結果。

## 検証済み

- mock-shop（scraping-procedure-deck/evals/files/mock-shop/site）: trace はコードの手順と1対1で一致、動画も各局面が映っていた。テストは `tests/scrape.spec.ts`、設定は `playwright.config.ts`。
- 実サイト（quotes.toscrape.com + books.toscrape.com）: trace のブラウザ action 158 件がコードと1対1で一致、メインタブの動画は約 0.1 秒以内で trace に対応、スクショ 10 枚と JSON（14 件 + 32 件）も意図どおり。テストは `tests/toscrape.spec.ts`、設定は `playwright.toscrape.config.ts`。詳細は `REPORT-toscrape.md`。

## 既知の仕様

- 新規タブは別動画になる（新規タブ側は静止画面なので trace との時刻対応は取れない）
- 録画は page 作成時に始まるため、最初の約 1.2 秒は白画面
- 「動画と trace の時刻が約 1 秒ずれる」は実サイト検証では再現せず（メインタブは約 0.1 秒以内）。ただし goto は load 完了まで含むため、描画が trace の完了より先に見えることがある
- 値 expect と `fs.writeFileSync` はブラウザ trace に出ない（test.trace のみ）
- 1fps 抽出だと短い局面を見落とす。短い局面は 6fps 以上で切り出す

## 未達・改善案

- 遅延読み込み画像があるページは、スクショ前に画像のロード完了を待たないと未ロードのまま撮れる（`09-mystery-p2`）
- 抽出ループで innerText を多用すると trace が細かい action で埋まる。`allInnerTexts` や `evaluateAll` に集約すると追いやすい

## 実行

- `npm ci && npx playwright test`（mock-shop）
- `npm ci && npx playwright test -c playwright.toscrape.config.ts`（実サイト）
- `playwright install` は禁止。executablePath は両 config で `/opt/pw-browsers/chromium-1194/chrome-linux/chrome` を指定済み
- 外部サイトに出る場合、Chromium が proxy の CA を信頼していないと `ERR_CERT_AUTHORITY_INVALID` になる。`libnss3-tools` を入れて `/root/.ccr/ca-bundle.crt` を `certutil` で `~/.pki/nssdb` に登録すると通る（TLS 検証は無効化しない）
- 生成物（test-results*/、playwright-report*/、results*/）は `.gitignore` で除外済み
