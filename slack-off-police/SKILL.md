---
name: slack-off-police
description: >
  ユーザーが「勉強/作業をサボらないように監視してほしい」「デバイスの操作権を渡すからサボったら止めてくれ」
  「進捗が無ければYouTubeやゲームを落としてくれ」といった要求をした場合に使うスキル。
  対象ディレクトリの進捗（git diff / ファイル更新）と、作業活動（Claude CodeやCodexへの質問履歴、
  VS Codeでの資料閲覧）を定期チェックし、手が動いていなければブラウザのウィンドウタイトルや
  実行中プロセスをスキャンして、登録済みの「サボり対象」（YouTube、Steam、Twitchなど）を検出する。
  さらに起動元エージェントのCLI（Claude Codeなら claude -p、Codexなら codex exec）を呼んで
  作業テーマと照らし、本当にサボりかを判定させたうえで、煽り文のポップアップ表示＋音声再生＋
  プロセスの強制終了を行う。Windows環境専用。「サボり監視」「勉強サボり」「進捗チェック 強制終了」
  「distraction blocker」「self-control app kill process」のような文脈でも積極的にこのスキルを使うこと。
---

# サボり警察 (slack-off-police)

ユーザーの作業ディレクトリを監視し、進捗が無い状態でサボり対象アプリ/サイトが
開かれていたら、問答無用で強制終了する自己監視ツール。**破壊的な操作（プロセスkill）を
確認なしで実行する**ため、必ず以下のヒアリング手順を踏んでから起動すること。

Claude Code と Codex CLI のどちらから使ってもよい。**判定に使うCLIは、いま動いている
自分自身のものを指定する**（Claude Code なら `--agent claude`、Codex なら `--agent codex`）。

## 全体フロー

```
[初回]
1. 対象ディレクトリに .slack-off-police.yaml があるか確認
2. 無ければ、チャットで直接ヒアリングする（scripts/setup.py にinput()は無い。
   対話はスキルを起動したエージェント自身が担当し、決まった値をCLI引数として渡す）
3. scripts/setup.py --dir ... --targets "..." --theme "..." --agent <自分> を実行し設定保存
4. タスクスケジューラに登録するか、その場でバックグラウンド起動する

[2回目以降・同じディレクトリ]
5. .slack-off-police.yaml があればヒアリングをスキップし、そのまま起動
   （ただし targets と rules は一度読み上げて確認する。他人のリポジトリに設定ファイルが
     紛れ込んでいる可能性があるため）
6. ユーザーが「設定を聞き直して」「もう一回ヒアリングして」と言った場合のみ、
   手順2〜3をやり直す（既存設定を上書き）
```

## 判定の流れ

監視ループ1回あたりの動きは次の通り。**知性的に判断するのは「作業していたか」の1点だけ**で、
そこから先の警告とkillは機械的に処理する。

```
エディタ起動中?
   ├ いいえ（まだ一度も起動していない）─→ 待機。判定もしない
   ├ いいえ（さっきまで開いていた）    ─→ 作業セッション終了とみなして監視を終わる
   └ はい
        ↓
   証拠を集める（コード差分・操作履歴・ウィンドウ・作業状況・時間の文脈）
        ↓
   自分のCLIに「この期間、作業していたか」を判定させる（claude -p / codex exec）
     ├ 呼び出し失敗    ─→ 見逃す（フェイルセーフ。誤爆のほうが害が大きい）
     ├ 作業していた    ─→ 何もしない
     └ 作業していない  ─→ ここから先は機械的:
            1回目   : 警告ポップアップ＋音声
            2回連続 : そのうえで kill対象があれば強制終了（無ければ警告だけ）
```

**休憩の忖度はしない。** 休憩するときはユーザーが自分で監視を止める前提なので、判定器は
「この期間に作業していたか」だけを見る。エディタを閉じればそこで監視も終わる。

進捗フラグ（変更行数が `min_lines` 以上か）は判定材料の一つとして渡すだけで、
それ自体では見逃しにならない。空行を1行足しただけの変更は進捗とみなされず、差分ごと判定器に回る。

kill対象のウィンドウが開いていなくても、作業の痕跡が無ければ警告は出る（落とす相手が無いので警告だけ）。
判定は毎サイクル1回LLMを呼ぶので、15分間隔なら1時間あたり4回。エディタを閉じている間は呼ばない。

**コードを1行も書いていなくてもサボりとは限らない**ので、判定器には次の7ブロックを渡し、
「いま何をしているか」を推定させたうえでサボりかどうかを判定させる。

| ブロック | 中身 | 出どころ |
| --- | --- | --- |
| `work_theme` | 長期の作業テーマ | 初回ヒアリングの固定文字列 |
| `rules` | ユーザーが決めた例外ルール | `--rules`（自然言語） |
| `context` | 現在時刻・曜日・監視開始からの経過・警告回数・直近4回の判定履歴 | 監視ループが保持 |
| `work_state` | ブランチ名・直近3コミット・未コミット変更・`TASKS.md` 等 | 監視対象ディレクトリ |
| `window_titles` | 開いているウィンドウ | ウィンドウ列挙 |
| `diff` | コードの差分 | git |
| `activity` | AIへの質問・資料閲覧・保存 | トランスクリプトとVS Code履歴 |

判定器は `slacking` に加えて **`doing`（いま何をしているかの推定、30字）** を返す。これはログに出て、
次回の判定では `context` の履歴として渡る。「30分前は資料を読んでいた」「直前も同じ動画で警告した」
を踏まえた判定になる。

`context` は監視プロセスのメモリ上にしか無いので、監視を再起動すると履歴はリセットされる。

## 手順1: 依存関係の確認

Windows専用。初回のみ、ユーザーの環境に Python 3.9以上と以下のパッケージが入っているか確認し、
無ければインストールを提案する。

```bash
pip install psutil pywin32 pyyaml
```

サボり判定には自分自身のCLI（`claude` または `codex`）を使う。PATH に無い場合、
判定器は毎回失敗し（フェイルセーフでkillされなくなる）ので、無ければ `--no-llm` を提案すること。

## 手順2: ヒアリング（対話はエージェント自身が行う）

`.slack-off-police.yaml` が対象ディレクトリに存在しない場合、以下をユーザーに**チャットで**質問する。
（スクリプトのinput()ではなく、会話の中で聞くこと）

1. 監視対象ディレクトリ（フルパス）
2. **作業テーマ**（例: 「Rustで自作CLIツールを書く」「基本情報技術者試験の勉強」）
   - サボり判定器がこれを基準に「その動画は作業に関係あるか」を判断する。必ず聞くこと
3. 進捗の証拠にする方法: `git_diff`（gitリポジトリの差分/コミット） or `mtime`（ファイル更新日時のみ）
   - 特に指定が無ければ `git_diff` を提案（gitが無ければ自動でmtimeにフォールバックする実装になっている）
4. 判定間隔（分）。特に指定が無ければ15分を提案
5. **落としていい対象**（アプリ名やサイト名。カンマ区切りで複数可。例: `YouTube,Steam,Twitch`）
   - ここは必ずユーザー自身に明言させること。エージェント側で勝手に対象を広げない
   - `X` のような1〜2文字のキーワードは誤爆しやすいので、指定されたら一度確認する
6. 作業活動を判定材料にしてよいか（既定: 使う）
   - 「質問文をLLMに渡したくない」と言われたら `--no-chat-text`（件数だけ渡す）
   - 「エディタは一切見ないでほしい」と言われたら `--no-vscode`
   - エージェントのトランスクリプトは、対象ディレクトリで投げた質問だけを読む
7. **例外ルール**（任意だが必ず聞く）。自然言語のまま `--rules` に渡す
   - 例: 「12時台の昼休みは自由」「25分作業したら5分休憩OK」「公式カンファレンスの動画は許可」
   - ここがAIを使う最大の理由。ルールが無いと「テーマと関係あるか」しか見られない
8. （任意）鳴らす音声ファイルのパス。ユーザーが用意していなければ省略可能
   （音声ファイルはリポジトリに含まれていないので、`assets/README.md` を参照するよう伝える）

回答が出揃ったら、以下のように設定を保存する。`--agent` には**自分自身**を渡すこと。

```bash
# Claude Code から使う場合
python <skill-path>/scripts/setup.py --dir "<対象ディレクトリ>" --agent claude --progress git_diff --interval 15 --targets "YouTube,Steam" --theme "<作業テーマ>"

# Codex から使う場合
python <skill-path>/scripts/setup.py --dir "<対象ディレクトリ>" --agent codex --progress git_diff --interval 15 --targets "YouTube,Steam" --theme "<作業テーマ>"
```

主なオプション:

| オプション | 既定 | 意味 |
| --- | --- | --- |
| `--agent` | `auto` | 判定に使うCLI（`claude` / `codex` / `auto`）。自分自身を明示すること |
| `--model` | 空 | 判定モデル（claudeは既定 `sonnet`、codexは各自の既定） |
| `--theme` | 空 | 作業テーマ。判定器の基準になる |
| `--rules` | 空 | ユーザーが決めた例外ルール（自然言語）。判定基準として最優先される |
| `--min-lines` | 3 | この行数未満の変更は進捗とみなさず判定器に回す |
| `--no-vscode` | — | VS Codeの操作履歴を一切読まない |
| `--no-chat-text` | — | 質問文をLLMに渡さず件数だけ渡す（AIエージェント・エディタ内チャット共通） |
| `--no-editor-gate` | — | エディタを閉じても監視を続ける（既定は閉じたら監視終了） |
| `--no-llm` | — | LLM判定を使わず、進捗も操作履歴も無ければ警告 |
| `--no-warn` | — | 警告フェーズを挟まず1回目でkill |

これで `<対象ディレクトリ>/.slack-off-police.yaml` が生成される。

`--agent auto` は環境変数（`CLAUDECODE` など）とPATH上のCLIから推測するだけで、
タスクスケジューラから起動されたときは当たらないことがある。必ず明示すること。

## 手順3: 監視の起動

常駐させるのが基本なので、タスクスケジューラに登録してログオン時起動にする。
`pythonw` は標準出力を捨てるため `--log` を必ず渡すこと。
**ログの出力先は監視対象ディレクトリの外にすること**（自分のログ書き込みが「進捗あり」と
誤判定されるため。monitor.py 側でも弾いている）。

```powershell
$py = Join-Path (Split-Path (Get-Command python).Source) "pythonw.exe"
$cmd = "`"$py`" `"<skill-path>\scripts\monitor.py`" --dir `"<対象ディレクトリ>`" --log `"$env:TEMP\slack-off-police.log`""
schtasks /Create /TN "SlackOffPolice" /SC ONLOGON /RL LIMITED /F /TR $cmd
schtasks /Run /TN "SlackOffPolice"
```

`/RL LIMITED` かつユーザーのログオンセッションで動かすこと。SYSTEM で動かすと
ウィンドウの列挙もポップアップ表示もできなくなる。

停止・解除:

```powershell
schtasks /End /TN "SlackOffPolice"
schtasks /Delete /TN "SlackOffPolice" /F
Stop-Process -Name pythonw
```

その場で試すだけなら、スケジューラを使わず直接起動してもよい:

```powershell
Start-Process pythonw -WindowStyle Hidden -ArgumentList @(
  '<skill-path>\scripts\monitor.py',
  '--dir', '<対象ディレクトリ>',
  '--log', "$env:TEMP\slack-off-police.log",
  '--audio', '<音声ファイルのパス（あれば）>'
)
```

音声ファイルを使わない場合は `--audio` を省略してよい（ポップアップとkillのみ実行される）。

起動したら、ユーザーに以下を伝える:
- 何分間隔で判定するか
- kill対象に設定した名前と、作業テーマ
- 判定にどちらのCLI（claude / codex）を使うか
- 作業活動（AIへの質問履歴・VS Codeの操作履歴）を見るかどうか
- **休憩するときは自分で監視を止めること**（休憩は忖度しない設計）
- エディタを閉じると監視も終わること
- 1回目は警告のみ、**2回連続でサボり判定されると確認なしでkillされる**こと
  （未保存ファイルが消えるリスクがあることを一度だけ明言する）
- 止め方（`schtasks /End` または `Stop-Process -Name pythonw`）

## 手順4（任意）: VS Code 拡張機能を入れる

拡張機能なしでも、エージェントとVS Codeが自分で保存しているデータから次を読める。

| 見たいもの | 読む場所 |
| --- | --- |
| AIエージェントへの質問 | `~/.claude/projects/<cwd>/*.jsonl`、`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` |
| AIチャットでの質問 | `workspaceStorage\<id>\chatSessions\*.jsonl` と `chat.ChatSessionStore.index` |
| 資料・ファイルの閲覧 | `state.vscdb` の `history.entries`（最近開いた順のリストを毎回比較） |
| 保存した編集 | `%APPDATA%\Code\User\History`（ローカル履歴。git管理外でも効く） |

対象ディレクトリを開いているワークスペースだけを見る。VS Code / Insiders /
VSCodium / Cursor / Windsurf などの派生も同じ構造なので同様に読む。

「読んでいる時間」まで見たい場合は同梱の拡張機能を入れる。開いた瞬間・スクロール・
選択・保存が秒単位で記録される。ファイルの中身とチャット本文は記録しない。

```powershell
$dest = "$env:USERPROFILE\.vscode\extensions\slack-off-police-watcher-0.1.0"
Copy-Item -Recurse -Force "<skill-path>\vscode-extension" $dest
```

コピー後に VS Code を再起動する。詳細は `vscode-extension/README.md`。

## 再ヒアリングしたい場合

ユーザーが「対象を変えたい」「もう一回聞いて」と言ったら、手順2をやり直し、
`setup.py` を再実行して `.slack-off-police.yaml` を上書きする。

## 注意事項（ユーザーに伝えるべきこと）

- これは自己責任の自己監視ツール。誤検知で無関係なウィンドウを巻き込む可能性がある
- マッチはウィンドウのタイトル／プロセス名に対して行うが、killは**プロセス単位**になる。
  例えば「YouTube」を対象にすると、YouTubeのタブを開いているブラウザのプロセスごと落ちるので、
  同じブラウザで開いていた他のウィンドウ・タブも全部巻き込まれる
- キーワードは英数字に挟まれた位置ではマッチしない実装にしてある
  （`X` が `explorer.exe` にヒットしてデスクトップごと落ちる事故を防ぐため）。
  それでも `X` のような短いキーワードは誤爆しやすいので、ユーザーに一言確認すること
- explorer.exe や svchost.exe などのシステムプロセス、および監視スクリプト自身とその親プロセス
  （Claude Code / Codex / ターミナル）は、targetsに何を書かれてもkillしない
- **AIエージェントやVS Codeに投げた質問文は、既定ではLLMに渡る**。嫌がられたら `--no-chat-text`
  （件数だけ渡す）を使う。読むのは監視対象ディレクトリで投げた質問だけ
- 音声ファイルは同梱していない。`assets/README.md` を参照し、各自で用意すること
- 実運用より「記事・ネタ用の実装」を想定している。長時間の放置運用は非推奨

## 実装上の制約（触るときに壊さないこと）

### サボり判定器（scripts/backends/, scripts/checkers/judge.py）

- **ウィンドウタイトルもエディタ履歴も信用できないデータ**。任意のWebページが `document.title` を、
  任意のファイル名やチャット本文が活動ログの中身を決められる。判定材料は stdin で渡し、
  ツールは無効化している（claudeは `--tools ""`、codexは `--sandbox read-only`）。
  LLMには真偽値と煽り文だけを返させ、**kill対象の選定は必ずローカルの `find_matches()` に固定する**。
  LLMに対象リストを作らせてはいけない
- **`INSTRUCTION_LINES` の各要素に改行と `< > & | ^` を入れてはいけない**。claudeバックエンドでは
  これがコマンドライン引数（`--system-prompt`）に載り、Windowsでは `claude.CMD` 経由で
  cmd.exe が引数を再解釈するため、改行でコマンドラインが打ち切られて `--output-format json` が消え、
  角括弧以外の記号はリダイレクトとして解釈される
- **`CLAUDECODE` 環境変数を落としてから claude を呼ぶ**。Claude Code から起動すると継承され、
  「入れ子セッションは起動できない」で失敗する
- **判定に失敗したら None を返し、呼び出し側はkillしない**。誤爆のほうが害が大きい
- 出力形式は claude なら `--json-schema`、codex なら `--output-schema` で固定する。
  `--json-schema` を持たない古い claude CLI では、スキーマをプロンプトに書く旧方式に自動で退避する

### バックエンドの追加

`scripts/backends/<name>.py` に `NAME` / `LABEL` / `DEFAULT_MODEL` / `cli_path()` /
`run(instruction_lines, payload, model)` を実装し、`backends/__init__.py` の `BACKENDS` に
登録すれば、判定に使えるエージェントを増やせる。`run()` は
`{"slacking": bool, "reason": str, "taunt": str}` か `None` を返すこと。

### 判定材料の信用度（scripts/checkers/judge.py）

- `[rules]` はユーザーが決めた基準なので判定には使うが、**出力形式や指示自体は書き換えさせない**。
  kill対象の選定はそもそもLLMを通らないので、ルールに何を書かれてもプロセス選択は乗っ取られない
- `[work_state]` はコミットメッセージや `TASKS.md` の中身、つまりリポジトリ側の文字列なので
  信用できないデータ側に置く。他人のリポジトリを開いているときに効く
- 信用できるブロックを先、外部が中身を決めるブロックを後ろに置く順序を崩さないこと
- `[context]` は監視ループがローカルで作る。LLMが返した doing と reason をそのまま次回に渡すので、
  溜めすぎないこと（`Timeline.MAX_HISTORY` は4）

### エージェントのトランスクリプト（scripts/backends/*.transcript_prompts）

- Claude Code は `~/.claude/projects/<cwdの英数字以外をハイフンに潰した名前>/*.jsonl`、
  Codex は `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` に質問履歴を持つ。どちらも
  ユーザー発話にISO8601のタイムスタンプが付くので、前回チェック以降のものだけを拾える
- **設定した agent に関係なく両方読む**。判定はClaudeでも、質問はCodexに投げているかもしれない
- Codexのログは1ファイルに複数セッションが入るので、`session_meta` の `cwd` を追いながら読む
- IDE統合が差し込む `<ide_opened_file>` のようなタグ付きメッセージはユーザーの質問ではないので弾く
- 判定器自身の呼び出しは `--no-session-persistence` / `--ephemeral` で記録されない。
  **これを外すと、自分が投げた判定プロンプトが「ユーザーの質問」として次の判定に混ざる**

### VS Code 履歴の読み取り（scripts/checkers/vscode_activity.py）

- `state.vscdb` は VS Code が開いたまま読む必要があるので **read-only で開く**。
  失敗したら一時ディレクトリにコピーして読む（ロックしないため）
- `history.entries` にタイムスタンプは無い。前回のスナップショットとの差分で「新しく開いた」を判定する。
  **初回は前回スナップショットが無く全件が新規に見える**ので、`first_run` のときは閲覧を活動に数えない
- エディタやワークスペースが見つからない場合は `None` を返す。監視ループは今までどおり git 差分だけで動く

判定1回あたり10〜20秒、各CLIのサブスク枠を消費する（API課金ではない）。
毎サイクル呼ぶので、15分間隔・8時間の作業で30回強。エディタを閉じている間は呼ばない。
`--no-llm` を指定すると判定器を使わず、進捗または操作履歴があれば見逃し、無ければ警告になる。
