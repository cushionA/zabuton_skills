"""
罵倒メッセージのテンプレート。すべてこのプロジェクト用に書き下ろしたオリジナル文言。
特定の人物・キャラクターの発言を模したものではない。
"""
import random

INSULTS = [
    "おいおい、また指が止まってるぞ。{target} 見てる場合か？",
    "進捗ゼロで {target} 開いてる度胸、褒めてやろうか？いや叱る。",
    "サボり検知。{target} は問答無用で落とす。",
    "お前がやると言った作業、進んでないのはとっくにバレてるからな。",
    "手が止まってる時間、{target} には流れてるらしいな。",
    "言い訳はプロセスと一緒に終了させる。",
]


def get_insult(target_name):
    template = random.choice(INSULTS)
    return template.format(target=target_name)
