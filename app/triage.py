"""トリアージエンジン。
判定順: ラリー(同一相手の連続受信) → 即対応(S客 or 用件キーワード) → まとめ。
ルールは意図的に単純・可読に保つ。LLM判定は将来ここに差せるが、
「なぜ鳴ったか」を本人に説明できることを優先する。
"""
import re
import time

from . import config, db

# 用件キーワード(来店・日程・金銭・急ぎ・不機嫌)
URGENT_PATTERNS = [
    (r"(今日|今夜|今から|これから).{0,12}(行|寄|向か|飲み)", "来店の申し出"),
    (r"(予約|席|何時|空いて)", "来店・席の確認"),
    (r"(同伴|アフター)", "同伴の相談"),
    (r"(明日|今週|来週|曜日).{0,10}(行|会|どう|空い)", "日程の相談"),
    (r"(振込|支払|払う|請求|ツケ|売掛|金)", "金銭の話"),
    (r"(至急|急ぎ|すぐ|早く)", "急ぎの気配"),
    (r"(怒|ふざけ|舐め|もういい|最悪)", "不機嫌の気配"),
]


def classify(contact_code: str, text: str, now: float | None = None) -> tuple[str, str]:
    """(category, reason) を返す。category は urgent / rally / batch。"""
    now = now or time.time()

    # 1) ラリー: 同一相手から RALLY_WINDOW_MIN 分以内の連続受信
    last = db.last_message_ts(contact_code)
    if last is not None and (now - last) <= config.RALLY_WINDOW_MIN * 60:
        return "rally", f"{config.RALLY_WINDOW_MIN}分以内の連続受信"

    # 2) 用件キーワード
    for pat, reason in URGENT_PATTERNS:
        if re.search(pat, text):
            return "urgent", reason

    # 3) S客は内容によらず即対応
    c = db.get_contact(contact_code)
    if c and c.get("rank") == "S":
        return "urgent", "S客からの受信"

    # 4) それ以外はまとめ箱
    return "batch", "雑談・近況"


def churn_risk(contact: dict, now: float | None = None) -> bool:
    """来店周期の超過(離脱兆候)。"""
    now = now or time.time()
    if not contact.get("cycle_days") or not contact.get("last_visit_ts"):
        return False
    return (now - contact["last_visit_ts"]) > contact["cycle_days"] * 86400 * 1.5
