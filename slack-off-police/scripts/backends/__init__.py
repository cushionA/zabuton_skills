"""
サボり判定に使うエージェント（Claude Code / Codex CLI）を切り替えるレイヤ。

どちらのエージェントがスキルを起動したかは setup.py の --agent で設定ファイルに書き込む。
`auto` のときだけ環境変数と PATH から推測するが、推測は当たらないこともあるので
スキル側では必ず明示的に渡すこと。
"""
import os

from . import claude_backend, codex_backend

# 順序は auto 推測時の優先順位を兼ねる
BACKENDS = {
    "claude": claude_backend,
    "codex": codex_backend,
}

# セッション中のエージェントが立てる環境変数。ユーザーが常時 export しがちな
# CODEX_HOME / ANTHROPIC_API_KEY のようなものは判定材料にしない。
ENV_MARKERS = {
    "claude": ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"),
    "codex": ("CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_THREAD_ID",
              "CODEX_MANAGED_BY_NPM"),
}


def detect_agent():
    """
    環境変数 → CLIの有無 の順で、いま動いているエージェントを推測する。
    どちらも判断できなければ None。
    """
    override = os.environ.get("SLACK_OFF_POLICE_AGENT", "").strip().lower()
    if override in BACKENDS:
        return override
    for name, keys in ENV_MARKERS.items():
        if any(os.environ.get(k) for k in keys):
            return name
    for name, backend in BACKENDS.items():
        if backend.cli_path():
            return name
    return None


def available_agents():
    return [name for name, backend in BACKENDS.items() if backend.cli_path()]


def resolve_backend(agent="auto"):
    """
    バックエンドモジュールを返す。CLIが見つからない場合は None（判定を諦める＝killしない）。
    """
    name = (agent or "auto").strip().lower()
    if name == "auto":
        name = detect_agent()
    backend = BACKENDS.get(name)
    if backend is None or backend.cli_path() is None:
        return None
    return backend


def transcript_prompts(dir_path, since_ts, include_text=True):
    """
    どのエージェントで質問したかは問わないので、全バックエンドを横断して集める。
    設定した agent とは関係なく、Claude Code と Codex の両方のログを見る。
    """
    prompts, count = [], 0
    for backend in BACKENDS.values():
        found, found_count = backend.transcript_prompts(dir_path, since_ts, include_text)
        prompts.extend(found)
        count += found_count
    return prompts, count
