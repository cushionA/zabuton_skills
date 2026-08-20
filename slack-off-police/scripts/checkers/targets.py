"""
Windows専用: 開いているウィンドウのタイトルとプロセス名を取得し、
サボり対象キーワードにマッチするものを検出する。

ブラウザのタブ単位ではなく「プロセス単位」でのマッチになる点に注意。
（例: "YouTube"を対象にすると、YouTubeのタブを含むブラウザのプロセスがヒットし、
  killすると同じブラウザの他ウィンドウも巻き添えで落ちる）
"""
import os
import re

import psutil
import win32gui
import win32process

# killするとOSが不安定になる/デスクトップが死ぬプロセス。targetsに何を書かれても除外する。
PROTECTED_PROCESSES = {
    "system", "system idle process", "registry", "memory compression",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "svchost.exe", "explorer.exe", "dwm.exe", "fontdrvhost.exe",
    "ctfmon.exe", "sihost.exe", "taskhostw.exe", "runtimebroker.exe",
    "shellexperiencehost.exe", "searchhost.exe", "startmenuexperiencehost.exe",
    "textinputhost.exe", "applicationframehost.exe",
}


def _protected_pids():
    """自分自身と祖先プロセス（Claude Codeやターミナル）を巻き込まないようにする。"""
    pids = {0, 4}
    try:
        proc = psutil.Process(os.getpid())
        pids.add(proc.pid)
        for parent in proc.parents():
            pids.add(parent.pid)
    except psutil.Error:
        pass
    return pids


def _compile(target):
    """
    部分一致だが、英数字に挟まれた位置ではマッチさせない。
    これが無いと "X" が "explorer.exe" にマッチしてデスクトップごと落ちる。
    日本語キーワードは前後が英数字にならないので実質そのまま部分一致になる。
    """
    return re.compile(
        r"(?<![0-9A-Za-z])" + re.escape(target) + r"(?![0-9A-Za-z])",
        re.IGNORECASE,
    )


def get_open_windows():
    """
    [{hwnd, pid, title, process_name}, ...] を返す。
    タイトルが空のウィンドウ（バックグラウンドの隠しウィンドウ等）は除外する。
    """
    results = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return True
        try:
            proc = psutil.Process(pid)
            proc_name = proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            proc_name = ""
        results.append({
            "hwnd": hwnd,
            "pid": pid,
            "title": title,
            "process_name": proc_name,
        })
        return True

    win32gui.EnumWindows(callback, None)
    return results


def find_matches(targets):
    """
    targets: ["YouTube", "Steam", "X", ...]（大文字小文字を区別しない）
    マッチしたウィンドウ情報のリストを返す。保護対象プロセスは除外する。
    """
    patterns = [_compile(t) for t in targets if t.strip()]
    protected_pids = _protected_pids()
    matched = []

    for w in get_open_windows():
        if w["pid"] in protected_pids:
            continue
        if w["process_name"].lower() in PROTECTED_PROCESSES:
            continue
        haystacks = (w["title"], w["process_name"])
        if any(p.search(h) for p in patterns for h in haystacks):
            matched.append(w)

    return matched
