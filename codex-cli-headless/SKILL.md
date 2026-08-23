---
name: codex-cli-headless
description: >
  スクリプト、CI、バックグラウンドプロセスから `codex exec` を非対話で呼び出す
  コードを書く、レビューする、または不具合を調べるときに使う。Codex CLIの自動化、
  stdin入力、構造化JSON出力、Windowsのsubprocess、権限を絞った単発LLM判定を扱う。
  OpenAI APIやResponses APIを直接組み込む用途には使わない。
---

# Codex CLIの非対話実行

`codex exec` を、対話UIを開かずスクリプトやCIから実行する。単発の分類、要約、
レビュー、構造化抽出など、処理の終了と出力の回収が必要な用途に使う。

Codex CLIがインストールされ、認証済みであること。Windows、macOS、Linuxに対応する。

## 適用範囲

- 1回の入力に対する最終結果だけが必要なら `codex exec` を使う
- 会話の継続が必要なら `codex exec resume`、アプリ統合ならCodex SDKかApp Serverを検討する
- サーバー製品にLLM機能を組み込む場合は、Codex CLIではなくResponses APIを使う

## 基本形

プロンプト全体をstdinで渡す場合は、最後の引数を `-` にする。任意の外部データを
コマンドライン引数へ埋め込まない。

```python
result = subprocess.run(
    [
        codex,
        "exec",
        "--ephemeral",
        "--sandbox", "read-only",
        "--skip-git-repo-check",
        "-",
    ],
    input=prompt,
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=120,
)
```

通常モードでは進捗がstderr、最終メッセージだけがstdoutに出る。全イベントが必要な
場合だけ `--json` を使い、JSON Linesとして1行ずつ処理する。

## 構造化出力

後続処理がJSONを必要とする場合は、プロンプトだけで形式を指定せず
`--output-schema <schema.json>` を使う。最終応答はstdoutからJSONとして読む。
最終応答をファイルにも残す必要がある場合だけ `--output-last-message <path>` を追加する。

```python
schema = {
    "type": "object",
    "properties": {
        "accepted": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["accepted", "reason"],
    "additionalProperties": False,
}
```

スキーマファイルは固定リソースとして同梱するか、監視対象外の一時ディレクトリへ
生成する。stdoutは終了コードが0のときだけパースし、型も呼び出し側で検証する。

## 権限と信用できない入力

- 読み取りやツール実行が不要な判定では `--sandbox read-only` を明示する
- 一時ディレクトリを `--cd` で作業ルートにし、対象リポジトリや個人ファイルを見せない
- `--ignore-user-config` を使い、ユーザー設定由来のMCPサーバーを単発判定へ持ち込まない
- セッションを残す必要がなければ `--ephemeral` を使う
- `danger-full-access` と `--dangerously-bypass-approvals-and-sandbox` は、外部で隔離された
  実行環境以外では使わない
- LLMに削除対象やkill対象を選ばせない。出力は分類値や文言に限定し、実行対象は
  信頼できるローカルロジックで決める

ウィンドウタイトル、Webコンテンツ、ログなどはプロンプトインジェクションを含み得る。
入力内の指示に従わないよう明記したうえで、空の一時ディレクトリ、読み取り専用sandbox、
ユーザー設定無効化を組み合わせる。

## 認証、モデル、失敗時の扱い

- Codex CLIは保存済み認証を再利用する。ChatGPTログインとAPIキーでは利用枠・課金が
  異なり得るため、呼び出し側から特定の課金方式を断定しない
- モデル指定が必須でなければ `--model` を省略し、利用環境の既定モデルを使う
- タイムアウト、非0終了、空出力、JSON不正、型不一致は失敗として扱う
- 失敗時に破壊的操作へ進む用途では `None` などの判定不能値を返し、安全側へ倒す

## Windows

Windows版は `%USERPROFILE%\.codex\packages\standalone\current\bin\codex.exe` が存在すれば
それを優先し、なければ `shutil.which("codex")` で解決する。`shell=True` は使わない。
外部データをstdinへ渡せば、改行やシェルメタ文字を含んでもシェル解釈されない。
`pythonw.exe` から起動する場合、標準出力は見えないため必要なログは監視対象外の
ファイルへ書く。

## 実例

このリポジトリの
[codex-slack-off-police/scripts/checkers/judge.py](../codex-slack-off-police/scripts/checkers/judge.py)
が、空の一時作業ルート、読み取り専用sandbox、JSON Schema、フェイルセーフを組み込んでいる。
