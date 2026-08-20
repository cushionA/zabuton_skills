"""
マッチしたウィンドウのプロセスを taskkill /F で強制終了する。
確認は挟まない（設計方針として問答無用）。
"""
import subprocess

from checkers.targets import PROTECTED_PROCESSES, _protected_pids


def kill_by_pid(pid):
    """taskkillが実際に成功した場合のみ True を返す。"""
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def kill_matches(matched_windows):
    """
    同一プロセスが複数ウィンドウにマッチしていても、killは1回で済ませる。
    実際にkillできたウィンドウ情報のリストを返す。
    """
    killed = []
    seen_pids = set()
    protected_pids = _protected_pids()

    for w in matched_windows:
        pid = w["pid"]
        if pid in seen_pids:
            continue
        seen_pids.add(pid)
        # find_matches側でも弾いているが、killは取り返しがつかないので二重に確認する
        if pid in protected_pids or w["process_name"].lower() in PROTECTED_PROCESSES:
            continue
        if kill_by_pid(pid):
            killed.append(w)
    return killed
