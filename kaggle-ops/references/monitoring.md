# KaggleジョブのCloud監視とChatGPT通知

常設監視はChatGPT Cloudの時間単位Automationで実行し、Kaggleへの状態照会とdurable snapshotの更新は同じCloud run内で行う。固定対象はGitHubの`.github/kaggle-jobs/` registry、保存先は`kaggle-monitor-state` branchの`kaggle-monitor.json`。GitHub Actions workflowは任意の代替経路で、既定では`KAGGLE_MONITOR_ENABLED=true`になるまで動かない。

## Cloud Automationの動作

`kaggle_cloud_monitor.py plan` が返す設定は`Kaggle Cloud Monitor`、`RRULE:FREQ=HOURLY`、`condition_watch`。planは登録内容の設計図であり、登録成功や実行成功の証明ではない。各runは添付Cloud環境に既存の個人用`KAGGLE_API_TOKEN`があることを確認し、値を表示・複製・保存しない。

各runはGitHub connectorでregistry全体と各JSON entryを読み、`kaggle-monitor-state:kaggle-monitor.json` を取得する。snapshotの明示的なnot-foundだけを初回として扱う。それ以外の読み取り・restore失敗時はKaggleへ問い合わせず、retry状態を維持する。snapshotを`kaggle_cloud_monitor.py restore`で復元してから、固定registryに載ったref/versionだけを`kaggle_monitor.py poll`でlive照会する。Kaggle jobのsubmitやcancel、registryの拡張はしない。

terminal statusは`complete`、`error`、`cancel_acknowledged`。`running`、`cancel_requested`、待機期限切れは完了扱いにしない。status取得に失敗した場合は完了を推測せず、以前のterminal eventを保存してretryする。all-reviewや採点不能でもterminal eventを落とさない。

noticeを送る前に、この会話に実際に存在するassistant最終回答から同じ`KAGGLE_EVENT:<event_id>`、ref、versionを照合する。一致すれば、その最終回答本文のSHA-256と弱いprovenanceだけを記録する。task metadata、用意した文章、Automation登録結果から過去の最終回答を推測しない。一致する回答がなければterminal eventをpendingのままsnapshotへ残す。

poll後に`kaggle_cloud_monitor.py snapshot`で`kaggle-monitor.json`を作り、GitHub connectorで現在blob SHAを条件にcompare-and-swap更新する。競合時は最新値を読み直してreconcileし、force overwriteしない。snapshot publishに失敗した場合はterminal eventをpendingのまま残し、infrastructure errorとして扱う。

各pending eventへの最終回答には、完全な`KAGGLE_EVENT:<event_id>`、ref、version、terminal statusと理由を含め、実験の結果品質は未検証と伝える。端末への配送を主張しない。conversationへ回答が掲載されたこととユーザー端末での受信は別であり、`delivery_status`は`unknown`のままにする。同じ実イベントを再通知しないため、次runは実際の先行最終回答のhash receiptを確認してからsnapshotを更新する。すべて成功しpending eventがなければ黙って終了する。

2026-10-24の実scheduled runはregistryを読み、固定jobのlive pollとcompare-and-swap snapshot更新を完了し、pending event 0件を記録した。これはそのrunの成功を示す。将来のrunや端末での通知受信を保証する記録ではない。

## GitHub Actionsを使う場合

`.github/workflows/kaggle-monitor.yml` は毎時17分のscheduleと手動dispatchを定義しているが、`KAGGLE_MONITOR_ENABLED` repository variableが文字列`true`の場合だけjobを実行する。Actions secret `KAGGLE_API_TOKEN`とstate branchの書き込み権限が別途必要になる。Cloud Automationと同じ対象を同時に監視すると重複pollやstate競合の原因になるため、運用経路を一方に固定する。

## 公開Notebookのsession ID照合とキャンセル

通常のKaggle API status応答はref/versionに対するstatusとfailure messageのみで、session IDを返さない。公開Notebookに限り、Kaggle frontendの`GetKernelViewModel`をowner/slugと可視version番号で読み取る経路を確認した。要求は`authorUserName`、`kernelSlug`、`kernelVersionId: 0`、`versionNumber: N`。応答の`author.userName`と`kernel.author.userName`がowner、`kernel.slug`がslug、`currentVersionNumber`と`kernelRun.kernelVersionNumber`がN、`kernelRun.kernelId`が`kernel.id`と一致することを確認し、`kernelRun.id`をsession IDとして扱う。現在版を照会するときは`versionNumber`を省略し、返されたversion値を照合する。公開ページから匿名`CLIENT-TOKEN`と`XSRF-TOKEN` cookieを通常のHTTPで取得し、同じcookie jarでfrontend RPCを呼ぶためinteractive loginは不要。この経路はKaggle frontendの変更で動かなくなる可能性がある。Private Notebookのsubmit/statusはKaggle API tokenを使い、interactive login不要だが、private ref/versionからsession IDを返す公開API経路は確認できていない。検証に失敗したりlookupが拒否された場合はIDを推測しない。

frontend RPCはWeb session cookieとXSRF tokenで呼ばれ、Kaggle API tokenからこれらへ交換する公式手順は確認できていない。Private Notebookに対する公式SDK/API tokenのref/version→session-ID lookupもない。ブラウザーloginへ移行せず、session IDを得られないprivate jobの`cancel`はfail closedする。kernel ID、kernelVersionId、可視version numberをsession IDとして使わない。

## 運用コマンド

`REPO`は監視registryとdurable snapshotを置く固定GitHub repositoryに置き換える。`plan`のschedule/promptをCloud Automationへ登録し、Cloud runではGitHub connectorからregistryとsnapshotを読み書きする。snapshotの明示的なnot-foundは初回として空状態から始め、取得できた場合はローカルへ保存してから`restore`する。not-found以外のsnapshot取得失敗ではpollしない。

```bash
python3 -B .agents/skills/kaggle-ops/scripts/kaggle_cloud_monitor.py plan --repository REPO --registry-ref main --state-ref kaggle-monitor-state
python3 -B .agents/skills/kaggle-ops/scripts/kaggle_cloud_monitor.py restore --snapshot /tmp/kaggle-monitor.json --output .lab-output/kaggle-monitor
.deps/kaggle-venv/bin/python -B .agents/skills/kaggle-ops/scripts/kaggle_monitor.py poll --registry .github/kaggle-jobs --output .lab-output/kaggle-monitor
python3 -B .agents/skills/kaggle-ops/scripts/kaggle_cloud_monitor.py snapshot --output .lab-output/kaggle-monitor
python3 -B .agents/skills/kaggle-ops/scripts/kaggle_cloud_monitor.py pending --output .lab-output/kaggle-monitor
```

Cancel後は保存済みjobをregistryへ再登録してcancel audit fieldsを反映し、生成・更新されたentryを固定registry branch（通常`main`）へcommitする。`register`自体はremote registryを更新しない。Cloud monitorもregistryを自動変更しない。

```bash
python3 -B .agents/skills/kaggle-ops/scripts/kaggle_monitor.py register --job results/job-001/job.json --registry .github/kaggle-jobs --repository REPO
```

## per-job通知helperとstate形式

`kaggle_monitor.py register` は固定ref/versionのregistry JSONを作るだけで、Automationを登録しない。`automation-plan`と`record-automation`はper-job Automationを使う場合のhelperであり、常設Cloud monitorのregistry/snapshot運用とは区別する。同じjobに複数monitorを登録しない。

`kaggle_cloud_monitor.py restore` はschema version 1のsnapshotだけを受け取り、ローカルにあるterminal eventとconversation receiptを上書きで消さない。`pending`はconversation receiptのないterminal eventを列挙する。`acknowledge`は実際のassistant最終回答を読み、正確なevent marker、ref、versionを確認してから本文hashを保存する。receiptは暗号学的な発信者証明や配送確認ではない。

1回限りの試行、Automation登録成功、run完了、最終回答のconversation掲載、端末での受信を同じ状態として記録しない。配送receiptが得られない場合の`delivery_status`は`unknown`である。
