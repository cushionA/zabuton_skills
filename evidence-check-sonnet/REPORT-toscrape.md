# 実サイト（quotes/books.toscrape.com）での証跡検証レポート

Sonnet サブエージェントがテストを作成・実行し、trace / 動画 / スクリーンショット / JSON がコードどおりに記録されたかを検証した結果。Fable 側で trace の件数・スクショ・動画フレームを抜き取り再確認済み。

## 総合判定

**録画はコードどおりだった。**

- ブラウザ側 trace の action 158 件は、コードの手順と 1 対 1 で一致（158/158）。
- メインタブの動画は、フォーム入力の順序やページ遷移の瞬間まで、trace の相対時刻と約 0.1 秒以内で対応。
- 自前スクショ 10 枚はすべて意図した局面。自動スクショ 2 枚は最終スクショと md5 が同一。
- JSON は quotes 14 件（10＋4）、books 32 件（20＋12）で、trace 上の抽出回数とも一致。

例外は 3 点（詳細は「不一致の分類」）。新規タブ動画は静止画面のため時刻を確定できない、books への goto は動画の描画が trace の完了より約 1 秒早く見える、`09-mystery-p2` は遅延読み込み画像が未ロードのまま撮れている。

## 実行環境

| 項目 | 値 |
|---|---|
| Playwright | 1.63.0 |
| ブラウザ | `/opt/pw-browsers/chromium-1194/chrome-linux/chrome`（`launchOptions.executablePath` で指定、`playwright install` は未実行） |
| 設定 | `playwright.toscrape.config.ts`（video/trace/screenshot すべて `on`、reporter は list + json + html、workers 1、viewport 1280x800） |
| テスト | `tests/toscrape.spec.ts`（test 1 本、test.step 8 個） |
| 結果 | 1 passed（テスト 12.3 秒、全体 13.9 秒） |
| annotations | `tag-love: clicked from Top Ten tags` / `login: logged-in` / `author-tab: opened by Ctrl+click` |

実行コマンド:

```
cd evidence-check-sonnet
npm ci
npx playwright test -c playwright.toscrape.config.ts
```

環境上の注意: 初回は `net::ERR_CERT_AUTHORITY_INVALID` で失敗した。Chromium が proxy の CA を信頼していなかったため。TLS 検証は無効化せず、`libnss3-tools` を入れて `/root/.ccr/ca-bundle.crt` を `certutil` で `~/.pki/nssdb` に登録して解消した。別環境で再実行する場合も同様の CA 登録が要ることがある。

生成物（コミット対象外、`.gitignore` で除外）:

- `test-results-toscrape/toscrape-toscrape-複合スクレイピング-quotes-books-/trace.zip`
- 同ディレクトリの `video.webm`（メインタブ、12.56 秒）と `video-1.webm`（新規タブ、8.60 秒）
- 同ディレクトリの `01-quotes-top.png` 〜 `10-book-detail-final.png`、`test-finished-1.png`、`test-finished-2.png`
- 同ディレクトリの `extracted-quotes.json`、`extracted-books.json`
- `results-toscrape/results.json`、`playwright-report-toscrape/`

## テスト構成（要件との対応）

| 要件 | 実装箇所 |
|---|---|
| 複数ページ遷移 | quotes トップ → /tag/love/ → page/2 → /login → トップ → books トップ → Mystery → page-2 → 詳細 |
| カテゴリ絞り込み後に 2 ページ以上 | quotes の love タグ（10 件 → 4 件）、books の Mystery（20 件 → 12 件） |
| 名前付き test.step | 8 step（番号付き） |
| 10 件以上の抽出と expect | quotes 14 件、books 32 件。件数・text 先頭・author・tags 数・price 形式を expect |
| フォーム操作 | /login に `dummy_user` / `dummy-pass` を fill して Login を click |
| 新規タブ | 作者「(about)」を Ctrl+クリックし `context.waitForEvent('page')` で取得（フォールバックは newPage+goto） |
| 条件分岐 | Top Ten tags に love があるか / ログイン後に Logout があるか / 新規タブが開いたか。通った枝は annotations に記録 |
| 待機 | `expect(...).toBeVisible()`、`waitForLoadState()`、`waitForTimeout(1500)` |
| 節目のスクショ | 各 step 末尾に `shot()` で 10 枚 |
| 最終スクショ前の値確定 | `expect(.price_color).toHaveText(/£\d/)` の後に最終スクショ |

## trace とコードの対応表

trace.zip の中身は 3 系統。

| ファイル | before エントリ数 | 内容 |
|---|---|---|
| `test.trace` | 302 | step、値 expect、attach、hook、fixture、ブラウザ action の鏡像 |
| `1-trace.trace` | 158 | ブラウザ側 action（コンテキスト 1 つ分。新規タブも同じファイル） |
| `0-trace.trace` | 0 | context-options のみ |

ブラウザ側 158 件の内訳と、コードから予想した回数:

| API | trace 件数 | コード上の予想 | 一致 |
|---|---|---|---|
| goto | 4 | 4（quotes トップ、/login、quotes トップ、books トップ） | ○ |
| click | 7 | 7（love、Next、Login、(about)、Mystery、next、本の h3 a） | ○ |
| fill | 2 | 2 | ○ |
| screenshot | 10 | 10 | ○ |
| queryCount | 6 | 6（love の count、quote の count 2 回、Logout の count、book の count 2 回） | ○ |
| 要素 expect（toBeVisible 9 / toHaveURL 3 / toContainText 2 / toHaveText 2 / toHaveCount 1） | 17 | 17 | ○ |
| innerText | 60 | 14×2（quotes）+ 32（books 価格）= 60 | ○ |
| evalOnSelectorAll（`allInnerTexts`） | 14 | 14（quotes の tags） | ○ |
| getAttribute | 32 | 32（books の title） | ○ |
| waitForLoadState | 2 | 2（Login 後、新規タブ） | ○ |
| waitForEvent "page" | 1 | 1 | ○ |
| waitForTimeout | 1 | 1 | ○ |
| scrollIntoView | 1 | 1 | ○ |
| newPage | 1 | 0（fixture が作る page） | △ コードには無いが fixture 由来 |

ループ内の innerText / evalOnSelectorAll / getAttribute を除いた時系列（時刻は trace 先頭からの相対、ms）:

| No. | step | コード上の手順 | trace の action | 時刻 | 一致 |
|---|---|---|---|---|---|
| 0 | fixture | （page fixture） | BrowserContext.newPage | 10 | △ fixture 由来 |
| 1 | 1 | `goto quotes.toscrape.com/` | Frame.goto | 115 (1221ms) | ○ |
| 2 | 1 | `expect(div.quote first).toBeVisible` | Expect toBeVisible `div.quote >> nth=0` | 1398 | ○ |
| 3 | 1 | `expect(div.quote).toHaveCount(10)` | Expect toHaveCount `div.quote` | 1440 | ○ |
| 4 | 1 | `shot 01-quotes-top` | Page.screenshot | 1450 | ○ |
| 5 | 2 | `love.count()`（条件分岐） | Frame.queryCount `.tags-box a.tag >> has-text=/^love$/` | 1554 | ○ |
| 6 | 2 | `love.first().click()` | Frame.click `... >> nth=0` | 1565 | ○ |
| 7 | 2 | `expect(page).toHaveURL(/\/tag\/love\//)` | Expect toHaveURL | 1803 | ○ |
| 8 | 2 | `expect(h3).toContainText('love')` | Expect toContainText `h3` | 1848 | ○ |
| 9 | 2 | `shot 02-tag-love-p1` | Page.screenshot | 1864 | ○ |
| 10 | 3 | `extractQuotes` の `cards.count()` | Frame.queryCount `div.quote` | 2010 | ○ |
| 11 | 3 | 10 件ループ（innerText×2 + allInnerTexts） | innerText×20、evalOnSelectorAll×10 | 2019〜2320 | ○ |
| 12 | 3 | `li.next a` click | Frame.click `li.next a` | 2322 | ○ |
| 13 | 3 | `expect(page).toHaveURL(/page\/2\//)` | Expect toHaveURL | 2491 | ○ |
| 14 | 3 | `expect(div.quote first).toBeVisible` | Expect toBeVisible | 2518 | ○ |
| 15 | 3 | `shot 03-tag-love-p2` | Page.screenshot | 2525 | ○ |
| 16 | 3 | `cards.count()` | Frame.queryCount `div.quote` | 2742 | ○ |
| 17 | 3 | 4 件ループ | innerText×8、evalOnSelectorAll×4 | 2745〜2858 | ○ |
| 18 | 3 | 値 expect（toHaveLength 等）、`fs.writeFileSync`、attach | ブラウザ trace には無し。test.trace に expect と Attach のみ | — | △ 仕様 |
| 19 | 4 | `goto /login` | Frame.goto | 2860 | ○ |
| 20 | 4 | `#username fill` | Frame.fill `#username` | 3325 | ○ |
| 21 | 4 | `#password fill` | Frame.fill `#password` | 3400 | ○ |
| 22 | 4 | `shot 04-login-filled` | Page.screenshot | 3434 | ○ |
| 23 | 4 | Login ボタン click | Frame.click `role=button[name="Login"]` | 3520 | ○ |
| 24 | 4 | `waitForLoadState()` | Wait for load state "load" | 3869 | ○ |
| 25 | 4 | `logout.count()`（条件分岐） | Frame.queryCount `role=link[name="Logout"]` | 4039 | ○ |
| 26 | 4 | `expect(logout).toBeVisible` | Expect toBeVisible | 4093 | ○ |
| 27 | 4 | `shot 05-after-login` | Page.screenshot | 4106 | ○ |
| 28 | 5 | `goto quotes トップ` | Frame.goto | 4170 | ○ |
| 29 | 5 | `expect(div.quote first).toBeVisible` | Expect toBeVisible | 4502 | ○ |
| 30 | 5 | `context.waitForEvent('page')` | Wait for event "page" | 4554 (494ms) | ○ |
| 31 | 5 | `(about).click({modifiers:['Control']})` | Frame.click、params に `modifiers=["Control"]` | 4554 | ○ |
| 32 | 5 | `author.waitForLoadState()` | Wait for load state "load" | 5049 | ○ |
| 33 | 5 | `expect(h3.author-title).toBeVisible` | Expect toBeVisible | 5373 | ○ |
| 34 | 5 | `expect(.author-born-date).toBeVisible` | Expect toBeVisible | 5453 | ○ |
| 35 | 5 | `waitForTimeout(1500)` | Frame.waitForTimeout (1503ms) | 5460 | ○ |
| 36 | 5 | `shot 06-author-tab` | Page.screenshot | 6967 | ○ |
| 37 | 6 | `goto books.toscrape.com/` | Frame.goto (1885ms) | 7128 | ○ |
| 38 | 6 | `expect(product_pod first).toBeVisible` | Expect toBeVisible | 9026 | ○ |
| 39 | 6 | `shot 07-books-top` | Page.screenshot | 9059 | ○ |
| 40 | 6 | Mystery click | Frame.click `.side_categories >> role=link[name="Mystery"]` | 9151 | ○ |
| 41 | 6 | `expect(h1).toHaveText('Mystery')` | Expect toHaveText `h1` | 9371 | ○ |
| 42 | 6 | `expect(product_pod first).toBeVisible` | Expect toBeVisible | 9446 | ○ |
| 43 | 6 | `shot 08-mystery-p1` | Page.screenshot | 9473 | ○ |
| 44 | 7 | `cards.count()` | Frame.queryCount `article.product_pod` | 9593 | ○ |
| 45 | 7 | 20 件ループ（getAttribute + innerText） | getAttribute×20、innerText×20 | 9600〜10130 | ○ |
| 46 | 7 | next click | Frame.click `role=link[name="next"]` | 10136 | ○ |
| 47 | 7 | `expect(page).toHaveURL(/page-2/)` | Expect toHaveURL | 10470 | ○ |
| 48 | 7 | `expect(li.current).toContainText('Page 2 of 2')` | Expect toContainText | 10508 | ○ |
| 49 | 7 | `last().scrollIntoViewIfNeeded()` | Scroll into view（内部は `waitForSelector article.product_pod >> nth=-1`） | 10527 | ○ |
| 50 | 7 | `shot 09-mystery-p2` | Page.screenshot | 10635 | ○ |
| 51 | 7 | `cards.count()` | Frame.queryCount | 10729 | ○ |
| 52 | 7 | 12 件ループ | getAttribute×12、innerText×12 | 10740〜11040 | ○ |
| 53 | 7 | 値 expect、`fs.writeFileSync`、attach | ブラウザ trace には無し（test.trace のみ） | — | △ 仕様 |
| 54 | 8 | 最初の本の `h3 a` click | Frame.click `article.product_pod >> nth=0 >> h3 a` | 11046 | ○ |
| 55 | 8 | `expect(.product_main h1).toBeVisible` | Expect toBeVisible | 11276 | ○ |
| 56 | 8 | `expect(.price_color).toHaveText(/£\d/)` | Expect toHaveText | 11326 | ○ |
| 57 | 8 | `shot 10-book-detail-final` | Page.screenshot | 11338 | ○ |

trace に現れないもの（コードにはある）:

- `fs.writeFileSync` と純粋な JS 演算（map / push / startsWith など）。直後の `testInfo.attach` だけが test.trace に記録される。
- 値に対する expect（`toHaveLength`、`toBeTruthy`、`toBeGreaterThan`、`toMatch` など 113 件）は test.trace にだけ出て、ブラウザ trace には出ない。

コードに無いのに trace に現れるもの:

- Before Hooks / After Hooks、Fixture（browser / context / page / viewport）、Launch browser / Create context / Create page / Close context。
- 自動スクショ（`test-finished-*.png`）は action としては出ず、results.json の attachments にだけ載る。

## 動画の確認

メインタブ `video.webm`（12.56 秒、800x500 に縮小）は 2fps で全体を俯瞰し、短い局面（フォーム fill → click、Next、Ctrl+クリック直後、詳細遷移）は 6〜8fps で切り出して目視した。新規タブ `video-1.webm`（8.60 秒）は 2fps で確認した。動画時刻 ≒ trace 相対時刻 − 約 0.1 秒。

| 局面 | trace 相対時刻 | 動画で確認できたこと |
|---|---|---|
| 録画開始〜トップ描画 | goto 115〜1336 | 最初の約 1.2 秒は白画面。1.25 秒で引用一覧が出る |
| love タグ 1 ページ目 | click 1565 | 1.875 秒で「Viewing tag: love」、André Gide が先頭 |
| Next | click 2322 | 2.25 秒でスクロール、2.5 秒で 2 ページ目（C.S. Lewis が先頭） |
| ログインフォーム | fill 3325 / 3400 | 3.125 秒で空のフォーム、3.25 秒で `dummy_user`、3.375 秒でパスワードがマスク表示（fill の順序どおり） |
| Login クリック後 | click 3520 | 3.9 秒でトップ。ヘッダーが Logout、各引用に「(Goodreads page)」リンクが出る |
| Ctrl+クリック直後 | click 4554 | メインタブは静止。作者ページは `video-1.webm` 側に Albert Einstein が映る |
| books 遷移 | goto 7128〜9013 | 7.8 秒で books トップが描画（trace の goto 完了より約 1 秒早い） |
| Mystery 1 ページ目 | click 9151 | 約 9.0 秒で「32 results、1 to 20」 |
| next とスクロール | click 10136 / scroll 10527 | 10.05 秒でスクロール、10.3 秒で Page 2 of 2 |
| 詳細ページ | click 11046 | 11.175 秒で画像未ロード、11.3 秒で画像あり。価格 £24.80 は先に出ている |
| 最終 | screenshot 11338 | 11.3〜12.0 秒は詳細ページで静止 |

HANDOFF にあった「動画と trace の時刻は約 1 秒ずれる」は、メインタブでは再現しなかった（約 0.1 秒以内）。

## スクリーンショットと JSON の確認

- 自前 10 枚はすべて意図した局面。`04-login-filled` は username にダミー値、password がマスク表示。`10-book-detail-final` は価格確定後の詳細ページ。
- `09-mystery-p2` は下段の本の画像が未ロード（遅延読み込み）。価格・タイトルは揃っている。
- `06-author-tab` は作者ページ。ヘッダーは「Login」表示で、ログイン状態の証跡にはならない（作者ページ側の仕様）。
- `test-finished-1.png` は `10-book-detail-final.png` と、`test-finished-2.png` は `06-author-tab.png` と md5 が同一。
- `extracted-quotes.json` は 14 件（page 1 が 10、page 2 が 4）、`extracted-books.json` は 32 件（20 + 12）。

## 不一致の分類

### Playwright の仕様によるもの

1. 新規タブは別の webm になる。Ctrl+クリック直後、メインの動画は静止して見える。
2. 動画は viewport 1280x800 を 800x500 に縮小して記録する。録画は page 作成時に始まるため、最初の約 1.2 秒は白画面。
3. 新規タブの動画はタブの存在期間（約 7.3 秒）より約 1.3 秒長く、画面が静止しているので trace との時刻対応を確定できない。
4. 要素 expect はリトライ込みで 1 action として出る。値 expect は test.trace にしか出ない。
5. `goto` は load 完了まで含む。books の goto は動画上の描画が trace の完了より約 1 秒早い。
6. `scrollIntoViewIfNeeded` は trace 上「Scroll into view」だが、内部は `waitForSelector ... >> nth=-1` として記録される。
7. 自動スクショ（`test-finished-*.png`）は trace の action に出ず、タブごとに 1 枚作られる。
8. before エントリに pageId が無いため、action がどのタブに対するものかは trace だけでは区別しにくい。

### テストの書き方によるもの（改善案つき）

1. `09-mystery-p2` で画像が未ロード。→ スクショ前に `img` の `complete` を `expect.poll` 等で確定させる。
2. `06-author-tab` はログイン状態の証跡にならない。→ 新規タブでログイン状態を見せたいなら、期待値を明示した別 step にする。
3. 新規タブの `waitForTimeout(1500)` は画面が静止しているため動画から待機を識別できない。→ スクロールなど動画に変化が出る操作と組み合わせる。
4. 抽出ループで innerText を 74 回呼ぶため、trace が細かい action で埋まる。→ `allInnerTexts` や `evaluateAll` に集約する。
5. 抽出が 1 つの step に長く続く。→ ページごとに step を分け、step 名に件数を入れる。
6. `li.next a` を使った理由がコード上で分かりにくい（`getByRole('link', { name: 'Next →' })` では一致しなかった）。→ コメントで理由を残す。

## 再実行手順

```
cd evidence-check-sonnet
npm ci
npx playwright test -c playwright.toscrape.config.ts
npx playwright show-trace test-results-toscrape/*/trace.zip   # GUI がある環境のみ
```

trace の action 一覧を取り出すには、trace.zip を展開して `test.trace` と `1-trace.trace`（JSONL）の `type === 'before'` エントリを `startTime` 順に並べる。動画のフレーム抽出は `ffmpeg -i video.webm -vf fps=6 frame_%03d.png` のように行う。
