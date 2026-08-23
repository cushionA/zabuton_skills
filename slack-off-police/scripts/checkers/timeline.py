"""
判定の時間的な文脈を作るモジュール。

「いま何時か」「前回からどれだけ経ったか」「直前の判定は何だったか」が無いと、
判定器は休憩の妥当性も再犯かどうかも判断できない。監視ループが毎回ここに結果を記録し、
次の判定にそのまま渡す。
"""
from collections import deque
from datetime import datetime

MAX_HISTORY = 4
WEEKDAYS = "月火水木金土日"


class Timeline:
    def __init__(self, interval_min):
        self.interval_min = interval_min
        self.started = datetime.now()
        self.entries = deque(maxlen=MAX_HISTORY)

    def record(self, result, doing="", reason=""):
        self.entries.appendleft({
            "at": datetime.now(),
            "result": result,
            "doing": doing,
            "reason": reason,
        })

    def format_for_prompt(self, strikes=0, progress_note=""):
        now = datetime.now()
        elapsed = int((now - self.started).total_seconds() // 60)
        lines = [
            "now: " + now.strftime("%Y-%m-%d %H:%M") + " (" + WEEKDAYS[now.weekday()] + ")",
            "check_interval_min: %d" % self.interval_min,
            "monitoring_since: %s (%d分前)" % (self.started.strftime("%H:%M"), elapsed),
            "warning_strikes: %d" % strikes,
        ]
        if progress_note:
            lines.append("progress_since_last_check: " + progress_note)
        if self.entries:
            lines.append("recent_checks (新しい順):")
            for entry in self.entries:
                text = "- " + entry["at"].strftime("%H:%M") + " " + entry["result"]
                if entry["doing"]:
                    text += " / " + entry["doing"]
                if entry["reason"]:
                    text += " / " + entry["reason"]
                lines.append(text)
        else:
            lines.append("recent_checks: (監視開始直後で履歴なし)")
        return "\n".join(lines)
