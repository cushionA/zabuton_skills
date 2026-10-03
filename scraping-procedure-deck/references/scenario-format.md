# scenario.json の書き方

1つの JSON に「資料の情報」「データレイアウト」「画面ごとの手順」を書く。A/B/D では `capture.py` がこれを実行して撮影する。C では画像と座標を直接書く。完成例は [examples/aupay-market.json](../examples/aupay-market.json)。

スライドに出るのは次の文言だけ。`target` などの技術情報は資料に出ない。

- `title` `subtitle` `screen` `text` `detail` `notes` `variant` `diff`
- `peeks` の `text` と `example`
- データレイアウトの文言

## トップレベル

| キー | 必須 | 内容 |
|---|---|---|
| `title` | 必須 | 資料タイトル（表紙・フッター） |
| `subtitle` | | 表紙のサブタイトル |
| `site` / `start_url` | | 表紙の「対象サイト」。`start_url` はリンクにもなる |
| `date` | | 作成日。省略時は実行日 |
| `browser` | | `viewport`（既定 `[1280, 800]`）、`device_scale_factor`（既定 2）、`locale`、`timezone`、`user_agent`、`timeout_ms`、`settle_ms` |
| `data_layout` | 必須 | 下記 |
| `shots` | 必須 | 画面（撮影単位）の配列。遷移順に並べる |

## data_layout

```json
{"no": 4, "name": "価格", "type": "数値", "note": "税込価格"}
{"no": 1, "name": "クロール日時", "type": "日時", "on_screen": false, "origin": "クロール実行時に付与"}
```

- `on_screen: false` の項目はグレーアウトし、「※グレーの項目は画面から取得しない項目です」を添える
- `origin` は画面外項目の取得元（まとめスライドの「取得元」列）
- `note` はまとめスライドの「備考」列

## shots[]

| キー | 内容 |
|---|---|
| `id` | 必須。英数字。ファイル名と撮影結果の対応に使う |
| `screen` | 必須。画面名。画像の上と全体の流れに出て、元ページへのリンクになる |
| `title` | スライドタイトル（何をするか。例: ショップで絞り込み）。全体の流れの説明文にもなる |
| `data_title` | データ取得スライドのタイトル。省略時は、取得専用の画面なら `title`、それ以外は項目名から自動生成 |
| `goto` | この画面を開くURL。A は最初の画面だけ、B は全画面に書く |
| `url` / `link` | リンク先を明示するときは `url` を書く（既定は撮影時のURL）。ログインが必要など顧客が開けないページは `"link": false` |
| `setup` | 撮影前の下準備（赤枠なし）。`[{"action": "hide", "target": ".popup"}]` など |
| `wait_for` | この要素が表示されるまで待ってから撮る（`target` と同じ書き方） |
| `steps` | 手順・取得項目・注記（下記） |
| `notes` | 右パネル下の補足（「※」付きで表示）。例: 一覧の各商品について⑤以降を繰り返します |
| `next` | 「次の画面」の表示名。省略時は次の shot の `screen` |
| `wait_ms` | 画面表示後の追加待ち（非同期で出る値を待つ）。その画面の `peeks` にも効く |
| `blur` | `false` で入力後のフォーカス解除をしない（サジェストを写したいとき） |
| `variant` / `diff` | 表示パターンの専用スライドにする（下記「表示の違い」） |
| `peeks` | 他ページの小さな切り抜き（下記「表示の違い」） |
| `image` / `scale` / `page_top` | C で指定する。`scale` は表示スケール（100%→1、150%→1.5）。A/B/D は capture.py が書き込む |

## steps[]

操作（画面遷移スライドに載る）:

```json
{"kind": "op", "text": "「検索」ボタンをクリック", "target": {"role": "button", "name": "検索", "exact": true}, "action": "click"}
```

取得項目（データ取得スライドに載る）:

```json
{"kind": "data", "item": 4, "target": "strong[class*='Price_price__currentPrice']"}
```

注記（点線の赤枠と ※1。取得しない箇所を示す）:

```json
{"kind": "mark", "text": "メーカー希望小売価格は取得しない", "target": "span[class*='SuggestedRetailPrice']"}
```

| キー | 内容 |
|---|---|
| `kind` | `op` / `data` / `mark` |
| `text` | op の手順文、mark の注記文。画面上の文言は「」で囲む |
| `detail` | 補足（op は手順の下、data は表の項目名の下に小さく出る） |
| `item` | data のデータレイアウト No. |
| `target` | 赤枠を付ける要素（下記）。最初に一致した要素を使う |
| `box` | C 用。画像ピクセルでの `[x, y, 幅, 高さ]` |
| `action` | op で実際に行う操作（下記）。確認だけの手順なら省略 |
| `timing` | `before`（撮影前に実行）/`after`（撮影後に実行）。省略時は fill/type/select/check/hover/focus が before、click/press が after |
| `fit` | `box`（要素の矩形。op の既定）/`text`（文字の範囲。data の既定） |
| `label` | 番号の位置を固定: `left` `top` `right` `top-center` `bottom-center` `left-bottom` `bottom` `top-right` `inside` |
| `no` | op の番号を手動で指定（通常は全体の通し番号が自動で振られる） |

`target` の無い op は、右の一覧にだけ載る（赤枠なし、警告が出る）。

### target の書き方

文字列なら Playwright のセレクタ（CSS・`text=`・`:has()`・`:text-is()`・`>> nth=1`）。オブジェクトなら Playwright の `get_by_*` と同じ指定になる。

| オブジェクト | Playwright での意味 |
|---|---|
| `{"role": "button", "name": "検索", "exact": true}` | `get_by_role("button", name="検索", exact=True)` |
| `{"placeholder": "キーワードを入れて検索"}` | `get_by_placeholder(...)` |
| `{"text": "もっと見る"}` / `{"label": "…"}` / `{"alt": "…"}` / `{"title": "…"}` / `{"test_id": "…"}` | `get_by_text` / `get_by_label` / `get_by_alt_text` / `get_by_title` / `get_by_test_id` |
| `{"css": "h6.title"}` | `locator("h6.title")` |
| `"within": <target>` | 親要素の中で探す（`locator(親).get_by_role(...)`） |
| `"has_text": "…"` | `.filter(has_text="…")` |
| `"nth": 0` | `.nth(0)`（`.first` は `"nth": 0`） |

例: `page.locator("section").filter(has_text="ショップから探す").get_by_role("link", name="コジマ au PAY マーケット店")` は次のように書く。

```json
{"role": "link", "name": "コジマ au PAY マーケット店", "within": {"css": "section", "has_text": "ショップから探す"}}
```

書き方のコツ:

- ハッシュ付きのクラス（`Price_price__HskDt`）は前方一致で `[class*='Price_price__']` と書く
- ラベルと値で1つの意味を持つ場合は、両方を含む親要素を選ぶ
- `peeks` の `target` は配列にすると、それらを全部含む範囲を切り抜く

### action

| 書き方 | 動作 |
|---|---|
| `"click"` | クリック。新しいタブが開いたら自動でそちらに移る |
| `{"fill": "ワイヤレスイヤホン"}` | 入力欄に値を入れる |
| `{"type": "..."}` | 1文字ずつ入力（fill で反応しない入力欄用） |
| `{"press": "Enter"}` | キー入力（target 無しならページ全体に） |
| `{"select": "値"}` | セレクトボックスの選択 |
| `"check"` / `"uncheck"` / `"hover"` / `"focus"` / `"scroll"` | 各操作 |
| `{"wait": 1000}` | 待機（ミリ秒） |
| `{"goto": "https://..."}` | URL を開く |
| `"hide"` | `setup` 専用。要素を見えなくする（ポップアップ・同意バナー等。同意ボタンは押さない） |

## 表示の違い

### 小さな切り抜き（peeks）

「レビューが無い商品は評価欄が出ない → 0件とする」のような、ちょっとした違いに使う。取得スライドの右パネルに、別ページの該当ブロックだけを小さく貼る。入りきらなければ、直後に専用スライドを自動で足す。

```json
"peeks": [{
  "item": 6,
  "text": "レビューがない商品は評価欄が表示されないため、レビュー件数を0とします",
  "example": "レビューがない商品の例",
  "goto": "https://wowma.jp/item/780424332",
  "target": ["h1[class*='ItemTitle_itemTitle']", "div[class*='Point_point__']"],
  "mark": "[class*='ItemDetails_reviewShareGrid']"
}]
```

| キー | 内容 |
|---|---|
| `item` | どの項目の話か（その項目が載るスライドに出る）。省略時はその画面の最後のデータ取得スライド |
| `text` | 扱いのルール（顧客が読む文） |
| `example` | 参照リンクの表示名（既定「表示例のページ」） |
| `goto` / `target` | 切り抜くページと範囲（A/B/D）。`pad` で余白（CSS px、既定 8） |
| `mark` | 切り抜きの中で点線の赤枠を付ける場所 |
| `image` / `box` / `mark_box` / `url` | C 用。手持ち画像と切り抜き範囲・点線枠の座標・参照リンク |
| `link` | `false` でリンクを付けない |

### 表示パターンの専用スライド（variant）

取得位置が変わるなど、1枚使って説明すべき違いに使う。同じ `screen` 名の shot を通常の shot の後ろに足す。

```json
{
  "id": "item_sale",
  "screen": "商品詳細ページ",
  "variant": "メーカー希望小売価格が表示される商品",
  "data_title": "価格の取得位置",
  "goto": "https://wowma.jp/item/412429708",
  "diff": ["価格の上にメーカー希望小売価格が併記される", "価格は販売価格（税込）を取得し、メーカー希望小売価格は取得しない"],
  "steps": [
    {"kind": "data", "item": 4, "target": "strong[class*='Price_price__currentPrice']"},
    {"kind": "mark", "text": "メーカー希望小売価格は取得しない", "target": "span[class*='SuggestedRetailPrice']"}
  ]
}
```

- `variant` は画面名の横に「（表示パターン：…）」として出る
- `diff` は右パネル上部の「通常表示との違い」に出る

## 番号の振り方

- op: shots の順に全体で通し番号（①②③…）。画面をまたいでも続く
- data: データレイアウトの `item`（No.）がそのまま番号になる
- mark: スライドごとに ※1, ※2…
- 同じ画面に op と data がある場合は「データ取得」→「画面遷移」の順に別スライドになる
- 1スライドの赤枠は既定で5個まで。超える場合や、枠が縦に大きく離れている場合は自動で「（1/2）」…に分かれる

## Playwright コードからの変換（パターンD）

SKILL.md の撮影範囲の合意後に、対象に含めた操作・画面だけを変換する。条件分岐や全件処理があること自体は、その全パターンを撮影する指示ではない。

| コード | scenario |
|---|---|
| `page.goto(url)` | 新しい shot の `goto` |
| `fill(value)` | その画面の op に `"action": {"fill": "入力値"}`。`text` は顧客向けの手順文にする |
| `press_sequentially(value)` | その画面の op に `"action": {"type": "入力値"}` |
| `select_option(value)` | その画面の op に `"action": {"select": "選択値"}` |
| `check()` | その画面の op に `"action": "check"` |
| `click`（画面が変わる） | その画面の最後の op。次の操作から新しい shot |
| `expect(...).to_be_visible()` / `wait_for_selector` | 確認の op（`action` なし）または shot の `wait_for` |
| `inner_text()` / `text_content()` / `get_attribute()` で値を取る行 | data。出力先のキーや変数名をデータレイアウトの項目に対応させる |
| `get_by_*` / `locator().filter()` / `.first` / `.nth()` | `target` のオブジェクト形式（上表） |
| `for` で全件を回す処理 | 合意した代表1件だけ撮り、`notes` に「各商品について…を繰り返します」と書く |
| ページ送り | 合意した範囲で次ページへ進む操作を op として1回だけ撮り、`notes` に繰り返しを書く |
| ログイン | 実行しない。ログイン後の画面は C（手持ち画像）にする |

## C（手持ち画像）の例

```json
{
  "id": "item",
  "screen": "商品詳細ページ",
  "title": "商品名・価格・ポイントを取得",
  "image": "user_item.png",
  "scale": 1,
  "steps": [
    {"kind": "data", "item": 3, "box": [775, 285, 340, 75]},
    {"kind": "data", "item": 4, "box": [775, 432, 142, 36]}
  ]
}
```

座標は `python scripts/overlay.py grid user_item.png` で作った目盛り付き画像から読む。`overlay.py boxes scenario.json` で枠を描いた確認画像を作り、ずれていたら直す。

A/B と C を混ぜる場合、`image` のある shot は撮影を飛ばすのでブラウザの画面は進まない。C の shot の次に自動撮影する shot には `goto` を書く。

## コマンドのオプション

`capture.py`:

| オプション | 内容 |
|---|---|
| `--only id1,id2` | 指定した shot だけ撮り直す。`goto` の無い shot は、`goto` のある shot まで遡って操作を再現する。他の shot の撮影結果は残す |
| `--headed` | ブラウザを表示して実行する |

`build_deck.py`:

| オプション | 内容 |
|---|---|
| `--captures DIR` | 撮影結果を使う。文言は scenario.json から読むので、文言だけの修正なら撮り直し不要。`peeks` の並びや撮影条件（`goto` / `target` / `mark` / `pad` / `wait_ms`）を変えた場合は、その shot を撮り直す |
| `--base 既存.pptx` | 既存資料に差し込む。そのタイトルのみレイアウト・本文領域・テーマ色・フォント・フッターに合わせる |
| `--insert-at N` | 差し込む位置（N枚目として入る。省略時は末尾）。セクションがあれば直前のスライドのセクションに入る |
| `--sections` | `cover,overview,steps,summary` から選ぶ。既定は通常が全部、差し込みは表紙なし、`--only` 指定時は steps のみ |
| `--only id1,id2` | 指定した shot のスライドだけ作る。番号は全体の通し番号のまま |
| `--max-per-slide N` | 1スライドの赤枠の上限（既定 5） |
