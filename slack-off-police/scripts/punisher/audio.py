"""
Windows標準のwinmm(MCI)を使って音声ファイルを再生する。
playsoundなどの追加pipライブラリ無しで mp3/wav を再生できる。
再生に失敗しても監視ループ自体は止めない。
"""
import ctypes
import os

_ALIAS = "slackoffpolice_audio"


def play_audio(path):
    if not path:
        return
    if not os.path.exists(path):
        print(f"[audio] 音声ファイルが見つかりません: {path}")
        return
    try:
        winmm = ctypes.windll.winmm
        # 既存のaliasが残っていたら閉じる（前回再生の後始末）
        winmm.mciSendStringW(f"close {_ALIAS}", None, 0, None)
        winmm.mciSendStringW(f'open "{path}" alias {_ALIAS}', None, 0, None)
        winmm.mciSendStringW(f"play {_ALIAS}", None, 0, None)
    except Exception as e:
        print(f"[audio] 再生に失敗しました: {e}")
