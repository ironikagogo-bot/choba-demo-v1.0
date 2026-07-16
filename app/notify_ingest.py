"""Androidの通知(LINE)を帳場の受信に変換する層。

Android側(サブ端末)の転送アプリ or 専用アプリが、LINEの着信通知を
{package, title, text} として帳場に POST してくる。それを (contact, message) に
解釈し、重複を除いて受信パイプラインに渡す。

LINEの通知フォーマットはバージョン・端末で揺れるので、パーサーは実データで調整前提
(生の title/text はデスクコンソールに出して、AXツリーの時と同じく実物合わせする)。

このモジュールにも送信機能は無い(読み取り→取り込みのみ)。
"""
import re
import time

LINE_PACKAGES = ("jp.naver.line.android",)   # LINE (Android) のパッケージ名

# 無視すべき要約/集約通知の本文パターン(相手・本文が特定できないもの)
_SUMMARY_PATTERNS = [
    re.compile(r"^\s*\d+\s*件"),                 # 「3件の…」
    re.compile(r"新しいメッセージ"),
    re.compile(r"メッセージが届いています"),
    re.compile(r"新着メッセージ"),
]

# グループトークの本文が "送信者: 本文" 形式のとき分離する
_GROUP_SPLIT = re.compile(r"^(?P<sender>[^:：]{1,30})[\s]*[:：][\s]*(?P<msg>.+)$", re.S)


def parse_line_notification(title: str, text: str, package: str | None = None) -> dict | None:
    """LINE通知 → {"contact","message"} を返す。取り込むべきでなければ None。

    - package が LINE 以外なら None(パッケージ指定がある場合のみ判定)
    - 要約/集約通知(相手不明)は None
    - 1対1: title=相手名, text=本文
    - グループ: title=グループ名, text="送信者: 本文" → contact=送信者, message=本文
    """
    title = (title or "").strip()
    text = (text or "").strip()

    if package:
        p = package.lower()
        if not any(pkg in p for pkg in LINE_PACKAGES) and "line" not in p:
            return None

    if not title and not text:
        return None
    # アプリ名だけの通知や要約は捨てる
    if title in ("LINE", "") and (not text or _is_summary(text)):
        return None
    if _is_summary(text):
        return None

    # グループらしき本文(送信者: 本文)を分離
    m = _GROUP_SPLIT.match(text)
    if m and title and m.group("sender").strip() != title:
        # タイトル(グループ名)と送信者が違う → グループとみなし、送信者を相手にする
        return {"contact": m.group("sender").strip(), "message": m.group("msg").strip()}

    return {"contact": title or "(不明)", "message": text}


def _is_summary(text: str) -> bool:
    return any(p.search(text) for p in _SUMMARY_PATTERNS)


class NotifyDedup:
    """通知の"再掲"(同一メッセージの重複POST)だけを弾く。本物の連投は通す。

    設計方針(本人の要望「本物のメッセージを消さない」を最優先):
    - 通知の一意ID(アプリが送る key+ts)が既出なら、同一通知の再掲とみなして弾く。
    - ts が端末で揺れる対策として、同一(相手,本文)を"短い窓(既定3秒)"内に見た場合も再掲扱い。
      → LINEの再掲はほぼ同時(ミリ秒〜数秒)なので拾える。
    - 旧「相手の直前と同じ本文なら常に弾く」ルールは廃止。
      本物の『はい』『はい』を消していたため。3秒より離れた同一本文は通す。
    """

    def __init__(self, window_sec: float = 3.0):
        self.window_sec = window_sec
        self._seen = {}        # (contact, message) -> ts
        self._seen_ids = {}    # msg_id(key+ts) -> ts

    def should_process(self, contact: str, message: str,
                       now: float | None = None, msg_id: str | None = None) -> bool:
        now = now if now is not None else time.time()
        # 古い記録を掃除(ID側は少し長めに保持=同一通知の再掲は間隔が空くこともある)
        for k, ts in list(self._seen.items()):
            if now - ts > self.window_sec:
                del self._seen[k]
        for k, ts in list(self._seen_ids.items()):
            if now - ts > 60:
                del self._seen_ids[k]
        # 1) 通知の一意ID(key+ts)が既出 → 同一通知の再掲
        if msg_id:
            if msg_id in self._seen_ids:
                return False
            self._seen_ids[msg_id] = now
        # 2) ts揺れ対策: 同一(相手,本文)を短い窓内に見た → 再掲とみなす
        pair = (contact, message)
        if pair in self._seen:
            return False
        self._seen[pair] = now
        return True
