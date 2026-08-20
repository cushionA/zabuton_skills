"""
罵倒メッセージを最前面のダイアログで出す。
pythonw で起動すると print はどこにも出ないため、表示はこちらが担当する。
MessageBox はモーダルでブロックするので、監視ループを止めないよう別スレッドで開く。
"""
import ctypes
import threading

_MB_OK = 0x0
_MB_ICONWARNING = 0x30
_MB_TOPMOST = 0x40000
_MB_SETFOREGROUND = 0x10000


def show_popup(title, text):
    def _show():
        try:
            ctypes.windll.user32.MessageBoxW(
                0, text, title,
                _MB_OK | _MB_ICONWARNING | _MB_TOPMOST | _MB_SETFOREGROUND,
            )
        except Exception:
            pass

    threading.Thread(target=_show, daemon=True).start()
