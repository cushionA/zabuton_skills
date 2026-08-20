"""
対象ディレクトリに「進捗」があったかどうかを判定するモジュール。
git_diff方式: 前回チェック以降のコミット、または前回以降に触られたファイルの変更行数を見る
mtime方式: ディレクトリ内ファイルの更新日時を見る（gitが使えない場合のフォールバックにも使う）

判定結果は (進捗あり, 差分テキスト) で返す。差分テキストは変更が閾値未満だったときに
サボり判定器へ渡して「その変更が実質的な作業か」を見てもらうためのもの。
"""
import os
import subprocess

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
MAX_DIFF_CHARS = 4000


def _git(dir_path, args):
    return subprocess.run(
        ["git"] + args,
        cwd=dir_path, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=10,
    )


def _changed_files(dir_path):
    changed, untracked = set(), set()
    for args, bucket in ((["diff", "--name-only"], changed),
                         (["diff", "--cached", "--name-only"], changed),
                         (["ls-files", "--others", "--exclude-standard"], untracked)):
        r = _git(dir_path, args)
        if r.returncode == 0:
            bucket.update(line for line in r.stdout.splitlines() if line.strip())
    return changed, untracked


def _touched_since(dir_path, names, since_timestamp):
    touched = []
    for rel in names:
        try:
            if os.path.getmtime(os.path.join(dir_path, rel)) >= since_timestamp:
                touched.append(rel)
        except OSError:
            continue
    return touched


def _changed_lines(dir_path, files):
    total = 0
    for args in (["diff", "--numstat", "--"], ["diff", "--cached", "--numstat", "--"]):
        r = _git(dir_path, args + files)
        if r.returncode != 0:
            continue
        for line in r.stdout.splitlines():
            cols = line.split("\t")
            if len(cols) >= 2:
                for col in cols[:2]:
                    if col.isdigit():
                        total += int(col)
    return total


def git_progress(dir_path, since_timestamp, min_lines):
    """
    (進捗あり, 差分テキスト) を返す。gitが使えない/リポジトリでない場合は None。
    """
    try:
        head = _git(dir_path, ["log", "-1", "--format=%ct"])
        if head.returncode != 0:
            return None
        if head.stdout.strip() and int(head.stdout.strip()) >= since_timestamp:
            return True, ""

        # git diff は時刻を持たないので、変更ファイルのmtimeで絞らないと
        # 一度でも未コミット差分ができた時点で永久に「進捗あり」になってしまう。
        changed, untracked = _changed_files(dir_path)
        if _touched_since(dir_path, untracked, since_timestamp):
            return True, ""

        files = _touched_since(dir_path, changed, since_timestamp)
        if not files:
            return False, ""

        diff = _git(dir_path, ["diff", "--"] + files)
        diff_text = diff.stdout[:MAX_DIFF_CHARS] if diff.returncode == 0 else ""
        return _changed_lines(dir_path, files) >= min_lines, diff_text

    except (subprocess.SubprocessError, FileNotFoundError, ValueError, OSError):
        return None


def mtime_progress(dir_path, since_timestamp):
    for root, dirs, files in os.walk(dir_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            try:
                if os.path.getmtime(os.path.join(root, f)) >= since_timestamp:
                    return True
            except OSError:
                continue
    return False


def check_progress(dir_path, since_timestamp, method="git_diff", min_lines=3):
    """
    (進捗あり, 差分テキスト) を返す。
    """
    if method == "git_diff":
        result = git_progress(dir_path, since_timestamp, min_lines)
        if result is not None:
            return result
        # gitが無い/リポジトリでない → mtimeにフォールバック
    return mtime_progress(dir_path, since_timestamp), ""
