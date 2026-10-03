# Playwright実行結果のインポート

入力はPlaywright TestのJSONレポートと、実行時に保存したPNG。レポート内にbase64で添付されたPNGも使える。スキル側が候補の一覧・画像を確認し、合意した画面と順序で資料化する。利用者がインポート計画や座標JSONを手書きする必要はない。

HTMLレポート、`trace.zip`、コンソールの文字ログを直接読む変換器ではない。JSONに画像がなければ画面を復元できない。記録にない中間状態や操作対象の位置を推測せず、必要な画像を利用者に確認する。

## 利用者が用意するもの

- JSONレポートと、その実行の `test-results/`（`outputDir` を変えていればそのディレクトリ）。画像はレポート内の相対階層を保つ
- 資料にしたい手順と取得項目。項目No.・型・画面外項目は従来のデータレイアウトと同じ
- 撮影済み画像をそのまま資料へ使用してよいか、隠す必要のある情報がないか

既存の結果に画像があれば、そのまま利用する。今後の実行でJSONを残す設定例は次のとおり。スキルがこの設定を理由にテストを再実行することはない。

```javascript
import { defineConfig } from '@playwright/test';

export default defineConfig({
  reporter: [['json', { outputFile: 'report.json' }]],
  outputDir: 'test-results',
  use: { screenshot: 'on' },
});
```

`screenshot: 'on'` は各操作の画面を全て残す設定ではない。手順途中の画面が必要なら、利用者が合意した地点で `testInfo.attach` にPNGを添付する。

```javascript
await testInfo.attach('会員価格を表示した商品詳細', {
  body: await page.screenshot(),
  contentType: 'image/png',
});
```

これは任意の記録方法で、既存画像のインポートにはテストコードの変更もPlaywrightのインストールも不要。ログイン済み画面を使う場合も、スキルは保存画像を読むだけで、ログイン操作や認証情報の再利用はしない。

## 1. 画像候補を確認する

以下の `S` はスキルのディレクトリ、`W` は公開対象外の作業ディレクトリ。

```text
python -X utf8 S/scripts/import_results.py W/report.json --artifacts W/test-results --list
```

JSONの `attachments` に、候補ID（`r001`など）・テスト名・プロジェクト・リトライ番号・結果・添付名・パスが並ぶ。リスト作成時にはPNGをコピーしない。同名のテストやリトライも別候補として扱う。失敗実行の画像は識別用に一覧へ出るが、インポート対象は成功実行のPNGのみ。

`--artifacts` は受け取った `outputDir` の場所。レポートが元のPCの絶対パスを持っていても、記録されたプロジェクトの `outputDir` からの相対位置を使い、このディレクトリ内へ対応付ける。同名ファイルを探し回ることはない。異なる `outputDir` を持つプロジェクトは結果一式を分けて取り込む。

JSONレポートには通常のクリック・入力の全履歴や対象座標が含まれない。`test.step` の名前があってもPNGとの対応は記録されていないため、添付名やテスト名だけから操作・順序を確定しない。画面を確認し、利用する候補・順番・示す項目を合意する。既に指定済みなら再確認は不要。

## 2. スキル側でインポート計画を作る

選んだPNGだけを確認用ディレクトリへ取り出す。JSON内のbase64画像も同じコマンドで開けるようになる。

```text
python -X utf8 S/scripts/import_results.py W/report.json --artifacts W/test-results --extract r001,r002 --out W/review-images
```

取り出したPNGを開き、C方式と同じく画像上で赤枠を確認する。`overlay.py grid` の目盛りも利用できる。`box` はPNGの左上を原点とする画像ピクセルの `[x, y, 幅, 高さ]`。CSS座標を流用しない。

`W/import-plan.json` の例。以下の座標は例示であり、受け取った画像を確認して置き換える。

```json
{
  "title": "会員価格の取得手順",
  "site": "社内テストサイト",
  "data_layout": [
    {"no": 1, "name": "会員価格", "type": "数値"}
  ],
  "shots": [
    {
      "attachment": "r001",
      "id": "member_price",
      "screen": "ログイン後の商品詳細",
      "title": "会員価格を確認",
      "scale": 1,
      "steps": [
        {"kind": "data", "item": 1, "box": [420, 260, 180, 40]}
      ]
    }
  ]
}
```

- `shots` に列挙した添付だけを、計画の順で取り込む。候補IDは同一レポートの `--list` 出力を使い、レポートが変わったら一覧から確認し直す
- `id` は英数字で始まる80文字以内の英数字・`_`・`-`。`screen` は必須。`title`・`data_title`・`notes`（文字列の配列）は任意
- `steps` は `op`（操作）・`data`（取得項目）・`mark`（注記）。全てに `box` が必要。`op` と `mark` は `text`、`data` はデータレイアウトの `item` が必要。`detail` も付けられる
- `scale` は既知の撮影倍率。省略時は1。`box` に倍率を再乗算しない。`page_top` はページ上端を含むと分かる場合に指定できる
- `goto`・`target`・`action` は不要。レポートのエラー文、標準出力、ソース、認証状態は資料に転記しない

## 3. 選択した画像を取り込み、資料を生成する

```text
python -X utf8 S/scripts/import_results.py W/report.json --artifacts W/test-results --plan W/import-plan.json --out W/imported
python S/scripts/overlay.py boxes W/imported/scenario.json
```

出力は `W/imported/scenario.json` と選択したPNGだけ。元のレポート・画像は変更しない。出力先は新しいディレクトリにする。既存ディレクトリや不正な枠・画像・添付IDはエラーとなり、既存結果を上書きしない。成功したリトライの画像を使った場合はWARNを確認する。

生成先 `W/out/` を事前作成し、撮影結果のマージを使わずにビルドする。

```text
python S/scripts/build_deck.py W/imported/scenario.json -o W/out/deck.pptx
python S/scripts/render_preview.py W/out/deck.pptx --out W/preview
```

`capture.py` や元テストの実行は不要。生成後は通常の全スライドQAを行う。添付画像自体に含まれる個人情報などは自動では隠れないため、必要なら利用者が用意した加工済み画像を C として使う。

仕様の参照元: [Playwright JSON reporter](https://playwright.dev/docs/test-reporters#json-reporter)、[testInfo.attach](https://playwright.dev/docs/api/class-testinfo#test-info-attach)、[スクリーンショット設定](https://playwright.dev/docs/api/class-testoptions#test-options-screenshot)。
