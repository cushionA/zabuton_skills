# 入力サイズと転送

Notebookのコード、入力Dataset、回収する出力は別々に容量を管理する。MBは1,000,000 bytes、MiBは1,048,576 bytesとし、元のbytesも記録する。

## 提出前の確認

1. `kernel-metadata.json` の `code_file` が指す最終ファイルを確認し、画像・巨大なラベル一覧・manifest・重み・保存済みセル出力が混ざっていないか確かめる。
2. helperはKaggleへ送る形でNotebookコードpayloadを計測し、1 MiB未満であることを要求する。Notebookのセル出力を除き、Kaggle SDKの送信時と同じJSON形式にした値を測る。日本語などの非ASCII文字はエスケープされ、payloadが元ファイルより大きくなることがある。この1 MiBはローカルの事前ガードで、Kaggleサービスの上限を確認した値ではない。
3. gzipで圧縮してもbase64では約4/3に増える。圧縮前だけで判断せず、埋め込み文字列と最終コードのpayload bytesを確認する。コードに収まらない入力はDatasetへ分離する。ガードを引き上げて再送しない。
4. 100 MB超の入力はprivate Datasetに置く。100 MB以下でも大きなmanifestや画像は分離してよい。NotebookにはDataset参照、設定、小さいコード、manifestやアーカイブのSHAだけを残す。
5. Datasetのready状態とversionを記録し、`dataset_sources` で接続する。実行時にDatasetからmanifestを読み、SHAを確認してから入力を使う。更新時は過去versionを保持する。

必要に応じてアーカイブを分割し、各ファイルのbytes・SHAと、展開後のファイル数・容量を記録する。KaggleがZIPを自動展開する場合もあるため、ZIPと展開済みディレクトリのどちらも扱える構成にする。元ZIPが残っていない場合、ZIPのSHAを再確認したとは扱わず、manifestと各入力ファイルのSHAで照合する。

入力や重みは `/tmp` などへ、回収する小さい結果・設定・ログは `/kaggle/working` へ置く。helperの出力回収には合計1 GBの上限がある。これは入力Datasetの容量制限ではない。

## 失敗の切り分け

稼働中ログのReadTimeoutや成果物0件だけでは、入力容量が原因とは判断できない。statusやGPU枠の消費だけでも、どの段階まで進んだか、モデルが成功したかは分からない。

大きな入力を使うNotebookでは、入力探索・コピー・SHA検証・モデル取得・推論の各段階で開始と終了をflushして記録する。ファイル数・bytes・経過時間も、小さい記録ファイルに残す。APIから稼働中ログが取れるとは限らないため、完了後に回収できる記録を残しておく。
