"""
VS Code（および同系エディタ）の操作履歴を読み、コード編集以外の作業活動を拾うモジュール。

拾えるもの:
  - チャットでの質問     : workspaceStorage 配下の chatSessions/*.jsonl と chat.ChatSessionStore.index
  - 資料・ファイルの閲覧 : state.vscdb の history.entries（MRU）をポーリング間で差分比較
  - 保存された編集       : User/History（ローカル履歴）のタイムスタンプ
  - 詳細な閲覧イベント   : 同梱の拡張機能が書き出す JSONL（任意。入っていれば使う）

エディタが閉じている / 対象ディレクトリのワークスペースが無い場合は None を返すだけで、
監視ループ側は今までどおり git 差分だけで動く。
"""
import json
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

MAX_ITEMS = 8
MAX_ITEM_CHARS = 160
MAX_PROMPT_CHARS = 300
EXT_LOG_NAME = "vscode-activity.jsonl"
EXT_LOG_MAX_LINES = 4000

# %APPDATA% 直下のエディタ名。VS Code 派生（Cursor など）も同じ構造を持つ。
EDITOR_DIRS = (
    "Code", "Code - Insiders", "VSCodium", "Cursor", "Windsurf", "Trae", "Antigravity IDE",
)


def state_home():
    override = os.environ.get("SLACK_OFF_POLICE_HOME")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".slack-off-police")


def _user_dirs():
    roots = []
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return roots
    for name in EDITOR_DIRS:
        user_dir = os.path.join(appdata, name, "User")
        if os.path.isdir(user_dir):
            roots.append(user_dir)
    return roots


def _uri_to_path(uri):
    if not isinstance(uri, str) or not uri.startswith("file:"):
        return None
    path = unquote(urlparse(uri).path)
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return os.path.normpath(path)


def _same_tree(a, b):
    try:
        a, b = os.path.normcase(os.path.abspath(a)), os.path.normcase(os.path.abspath(b))
    except (OSError, ValueError):
        return False
    return a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep)


def _workspace_storages(dir_path):
    """
    監視対象ディレクトリを開いているワークスペースの storage ディレクトリを返す。
    """
    found = []
    for user_dir in _user_dirs():
        root = os.path.join(user_dir, "workspaceStorage")
        if not os.path.isdir(root):
            continue
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir():
                continue
            try:
                with open(os.path.join(entry.path, "workspace.json"), encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                continue
            folder = _uri_to_path(meta.get("folder") or meta.get("workspace"))
            if folder and _same_tree(folder, dir_path):
                found.append(entry.path)
    return found


def _read_db(db_path, keys):
    """
    state.vscdb から指定キーを読む。VS Code を開いたまま読めるよう read-only で開き、
    それでも駄目なら一時ディレクトリにコピーして読む。
    """
    def query(path):
        con = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)
        try:
            out = {}
            for key in keys:
                row = con.execute("select value from ItemTable where key=?", (key,)).fetchone()
                if row:
                    out[key] = row[0]
            return out
        finally:
            con.close()

    try:
        return query(db_path)
    except sqlite3.Error:
        pass
    try:
        with tempfile.TemporaryDirectory(prefix="slack-off-police-db-") as tmp:
            copied = os.path.join(tmp, "state.vscdb")
            shutil.copy2(db_path, copied)
            return query(copied)
    except (OSError, sqlite3.Error):
        return {}


def _json_or_none(raw):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _one_line(text, limit=MAX_ITEM_CHARS):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(text)).strip()[:limit]


def _iter_chat_requests(node):
    """
    chatSessions の JSONL はスナップショット行と差分パッチ行が混ざる。
    形を決め打ちせず、message.text を持つ辞書を再帰的に拾う。
    """
    if isinstance(node, dict):
        message = node.get("message")
        if isinstance(message, dict) and isinstance(message.get("text"), str):
            yield node.get("timestamp"), message["text"]
        for value in node.values():
            yield from _iter_chat_requests(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_chat_requests(value)


def _chat_activity(storages, since_ts, include_text):
    since_ms = since_ts * 1000
    prompts, sessions = [], 0

    for storage in storages:
        chat_dir = os.path.join(storage, "chatSessions")
        if os.path.isdir(chat_dir):
            for entry in os.scandir(chat_dir):
                try:
                    stat = entry.stat()
                    if not entry.is_file() or stat.st_mtime < since_ts:
                        continue
                    with open(entry.path, encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                except OSError:
                    continue
                if not include_text:
                    # 本文を見ない設定では、質問の有無をファイルの更新でしか判断できない
                    sessions += 1
                    continue
                for line in lines:
                    obj = _json_or_none(line)
                    if obj is None:
                        continue
                    for ts, text in _iter_chat_requests(obj):
                        # timestamp を持たない差分行はファイルのmtimeで代用する
                        ts = ts if isinstance(ts, (int, float)) else stat.st_mtime * 1000
                        if ts >= since_ms and text.strip():
                            prompts.append(_one_line(text, MAX_PROMPT_CHARS))

        raw = _read_db(os.path.join(storage, "state.vscdb"), ["chat.ChatSessionStore.index"])
        index = _json_or_none(raw.get("chat.ChatSessionStore.index"))
        if isinstance(index, dict):
            for meta in (index.get("entries") or {}).values():
                if not isinstance(meta, dict) or meta.get("isEmpty"):
                    continue
                last = meta.get("lastMessageDate")
                if isinstance(last, (int, float)) and last >= since_ms:
                    sessions += 1

    seen, unique = set(), []
    for text in prompts:
        if text not in seen:
            seen.add(text)
            unique.append(text)
    return unique[:MAX_ITEMS], sessions


def _saved_files(dir_path, since_ts):
    """
    ローカル履歴（タイムラインの実体）から、監視対象ディレクトリ配下の保存イベントを拾う。
    git 管理外のディレクトリでも効くのが利点。
    """
    since_ms = since_ts * 1000
    saved = []
    for user_dir in _user_dirs():
        root = os.path.join(user_dir, "History")
        if not os.path.isdir(root):
            continue
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_dir():
                    continue
                meta_path = os.path.join(entry.path, "entries.json")
                if os.path.getmtime(meta_path) < since_ts:
                    continue
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                continue
            path = _uri_to_path(meta.get("resource"))
            if not path or not _same_tree(path, dir_path):
                continue
            for item in meta.get("entries") or []:
                ts = item.get("timestamp") if isinstance(item, dict) else None
                if isinstance(ts, (int, float)) and ts >= since_ms:
                    saved.append(os.path.basename(path))
                    break
    return sorted(set(saved))[:MAX_ITEMS]


def _mru_files(storages):
    current = []
    for storage in storages:
        raw = _read_db(os.path.join(storage, "state.vscdb"), ["history.entries"])
        entries = _json_or_none(raw.get("history.entries"))
        if not isinstance(entries, list):
            continue
        for item in entries:
            editor = item.get("editor") if isinstance(item, dict) else None
            path = _uri_to_path((editor or {}).get("resource"))
            if path:
                current.append(path)
    return current


def _load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path, state):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except OSError:
        pass


def _ext_events(since_ts):
    """
    同梱の VS Code 拡張が書き出すイベントログ。入っていなければ空。
    """
    path = os.path.join(state_home(), EXT_LOG_NAME)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-EXT_LOG_MAX_LINES:]
    except OSError:
        return {}

    counts, files = {}, []
    since_ms = since_ts * 1000
    for line in lines:
        event = _json_or_none(line)
        if not isinstance(event, dict):
            continue
        ts = event.get("ts")
        if not isinstance(ts, (int, float)) or ts < since_ms:
            continue
        kind = str(event.get("type", "?"))[:16]
        counts[kind] = counts.get(kind, 0) + 1
        name = event.get("file")
        if isinstance(name, str) and name:
            files.append(os.path.basename(name))
    return {"counts": counts, "files": sorted(set(files))[:MAX_ITEMS]}


def collect(dir_path, since_ts, include_chat_text=True):
    """
    前回チェック以降のエディタ活動を集める。
    エディタ由来のデータが1つも見つからなければ None を返す。
    """
    storages = _workspace_storages(dir_path)
    ext = _ext_events(since_ts)
    saved = _saved_files(dir_path, since_ts)

    if not storages and not ext and not saved:
        return None

    prompts, chat_sessions = _chat_activity(storages, since_ts, include_chat_text)

    state_path = os.path.join(state_home(), "mru-state.json")
    state = _load_state(state_path)
    previous = state.get("mru") or []
    current = _mru_files(storages)
    opened = [p for p in current[:MAX_ITEMS * 2] if p not in previous]
    state["mru"] = current[:64]
    _save_state(state_path, state)

    return {
        "chat_prompts": prompts,
        "chat_sessions": chat_sessions,
        "saved_files": saved,
        "opened_files": [os.path.basename(p) for p in opened][:MAX_ITEMS],
        "ext_counts": ext.get("counts", {}),
        "ext_files": ext.get("files", []),
        # 初回は前回スナップショットが無く、MRU全件が「たった今開いた」に見えてしまう
        "first_run": not previous,
    }


def has_editor_activity(activity):
    if not activity:
        return False
    if activity["chat_prompts"] or activity["chat_sessions"] or activity["saved_files"]:
        return True
    if activity["ext_counts"] or activity["ext_files"]:
        return True
    return bool(activity["opened_files"]) and not activity["first_run"]


def format_editor(activity):
    """
    判定器に渡すエディタ活動の部分。中身は信用できないデータとして扱うこと。
    """
    if not activity:
        return ""

    lines = []
    if activity["chat_prompts"]:
        lines.append("chat_prompts:")
        lines.extend("- " + p for p in activity["chat_prompts"])
    elif activity["chat_sessions"]:
        lines.append("chat_sessions_touched: " + str(activity["chat_sessions"]))
    if activity["opened_files"] and not activity["first_run"]:
        lines.append("opened_files: " + ", ".join(_one_line(f, 60) for f in activity["opened_files"]))
    if activity["saved_files"]:
        lines.append("saved_files: " + ", ".join(_one_line(f, 60) for f in activity["saved_files"]))
    if activity["ext_counts"]:
        lines.append("editor_events: " + ", ".join(
            "%s=%d" % (k, v) for k, v in sorted(activity["ext_counts"].items())))
    if activity["ext_files"]:
        lines.append("editor_files: " + ", ".join(_one_line(f, 60) for f in activity["ext_files"]))
    return "\n".join(lines)


EDITOR_PROCESSES = {
    "code.exe", "code - insiders.exe", "codium.exe", "vscodium.exe",
    "cursor.exe", "windsurf.exe", "trae.exe", "antigravity.exe",
}


def is_editor_running():
    """
    VS Code系のエディタが起動しているか。閉じられたら作業セッション終了とみなす側で使う。
    判定できなかった場合は True を返す（監視を勝手に止めないため）。
    """
    import psutil

    try:
        for process in psutil.process_iter(["name"]):
            if (process.info.get("name") or "").lower() in EDITOR_PROCESSES:
                return True
    except psutil.Error:
        return True
    return False
