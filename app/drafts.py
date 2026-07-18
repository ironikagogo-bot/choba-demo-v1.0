"""返信下書き生成。
ANTHROPIC_API_KEY があれば Claude API、なければテンプレートにフォールバック
(APIキーなしでもパイロットの動線検証ができるように)。
"""
import json
import re

import requests

from . import config, db
from .style_profile import profile_prompt_block, contact_profile_block

SYSTEM = """あなたは本人の「返信の下書き係」。本人になりきって、届いたLINEへの返信案を作る。相手は基本、気心の知れた友人・知り合い。既定は"友達へのLINE"の温度で書く。

【既定トーン＝かなりくだけた友人感】
- タメ口ベース。短く、崩して、句読点は少なめ。きれいな作文にしない。
- 水商売・接客・営業の定型句を使わない。禁止例:「お待ちしております」「ご用意しておきます」「楽しみにしております」「〜させていただきます」の連発、「嬉しいです」の多用、過剰な持ち上げ、堅い時候の挨拶。
- 友達に送るくらいの軽さ。「〜だね」「〜しよ」「りょ」「おっけー」みたいな自然な口語。絵文字は本人の実例に出る範囲で控えめに(数合わせで盛らない)。
- 一言で済むならそれでいい。長くしない。

【最優先ルール(既定トーンより上)】
- 「本人が実際に書いた文」の実例があれば、既定トーンより実例を優先して声を写す。数値プロファイルは"参考"で、当てにいく目標ではない(数字合わせはわざとらしくなる)。
- 【距離感】の指定があれば絶対に従う。とくに"敬語厳守"は安全のため何より優先し、タメ口・友達口調の案を一切出さない。

わざとらしさを消す:
- 実例より丁寧・完全に書かない。！や「笑」を数合わせで盛らない。決まり文句を実例に無いのに足さない。
- 気の利いた一言を無理に作らない。滑るくらいなら短く自然な受けにする。
- 事実や約束を捏造しない。日時・場所・金額は本人が確定できない限り断定せず、ふわっと。
- 相手の言葉を1つ拾うと自然。

- 相手の「地雷・注意(ネガ)」は"避ける配慮"にのみ使い、その語句や否定的評価(例:ケチ・恐妻家)を本文に絶対書かない。
- 相手の「喜ぶ・強み(ポジ)」は事実がある範囲で自然に活かす(不自然に持ち上げない)。

出力はJSONのみ: {"drafts":[{"tone":"...","text":"..."},{"tone":"...","text":"..."}]}
2案は毛色を少し変える(例: 片方は最小限の一言、もう片方は少しだけ足す)。toneは短い日本語ラベル。前置き・説明・コードブロック記号は禁止。"""

REGISTER_RULE = {
    "keigo_only": "【距離感=敬語厳守・最優先】この相手には必ず敬語。タメ口・砕けた表現・友達口調は一切禁止。2案とも敬語で(堅い相手への事故防止)。",
    "keigo": "【距離感=敬語】この相手は敬語基調で丁寧に。友達口調にはしない。",
    "mix": "【距離感=混在】敬語とタメ口が混ざる間柄。2案のうち片方を少し砕けた案に。",
    "casual": "【距離感=タメ口】親しい相手。友達口調で崩してよい。",
}


def _template_drafts(contact: dict, text: str, reason: str) -> list[dict]:
    """オフライン用の素朴なテンプレート(APIキー無しの動線検証用・かなりくだけた友人トーン)。"""
    if "来店" in reason or "席" in reason:
        return [
            {"tone": "軽く", "text": "ほんと！？来てくれるの嬉しい〜、席とっとくね。何人くらい？"},
            {"tone": "最小", "text": "おっけー、空けとく！何時ごろ来れそう？"},
        ]
    if "日程" in reason or "同伴" in reason:
        return [
            {"tone": "軽く", "text": "いいね、行こ〜。今週なら火曜か木曜がわたし動きやすいけどどう？"},
            {"tone": "最小", "text": "りょ！日にち決まったら教えて、空けとくね"},
        ]
    return [
        {"tone": "軽く", "text": "おつかれ〜！その話、今度ゆっくり聞かせてよ"},
        {"tone": "共感", "text": "そうなんだ〜。連絡くれてありがと、また近いうち会お！"},
    ]


def generate(message_id: int) -> list[dict]:
    msg = db.get_message(message_id)
    if not msg:
        return []
    contact = db.get_contact(msg["contact"]) or {"code": msg["contact"], "rank": "B"}

    # ラリー(連投)は相手からの一連の受信をまとめて1つの返信にする(最新1通だけに返さない)
    thread_text = msg["text"]
    if msg.get("category") == "rally":
        try:
            sibs = [x for x in db.open_messages()
                    if x["contact"] == msg["contact"] and x["category"] == "rally"]
            if len(sibs) > 1:
                sibs.sort(key=lambda x: x["ts"])
                thread_text = "\n".join(x["text"] for x in sibs)
        except Exception:
            pass

    if not config.ANTHROPIC_API_KEY:
        drafts = _template_drafts(contact, msg["text"], msg["reason"])
        db.save_drafts(message_id, drafts)
        return drafts

    profile = db.get_profile("_global") or {}
    per_contact = db.get_profile(contact["code"]) or {}
    cp_block = contact_profile_block(per_contact)
    user_prompt = (
        f"{profile_prompt_block(profile)}\n\n"
        f"{cp_block}\n\n" if cp_block else f"{profile_prompt_block(profile)}\n\n"
    )
    _pos = (contact.get("note_pos") or "").strip()
    _neg = (contact.get("note_neg") or "").strip()
    if _pos:
        user_prompt += f"\nこの相手が喜ぶ・強み(自然に活かす): {_pos}"
    if _neg:
        user_prompt += f"\nこの相手の地雷・注意(触れず避ける。本文にこの語句を絶対書かない): {_neg}"
    _reg = REGISTER_RULE.get((contact.get("register") or "").strip())
    if _reg:
        user_prompt += "\n" + _reg
    if (contact.get("kind") or "") == "staff":
        _st = (contact.get("stand") or "").strip()
        _tone = {"senior": "相手は店の先輩(ママ/黒服など)。丁寧め・敬語寄りで短く。",
                 "junior": "相手は後輩(ヘルプ)。タメ口で軽く、ねぎらいを一言。"}.get(_st, "相手は店の同僚。フラットに短く。")
        user_prompt += ("\n【店内・同僚モード】これは客ではなく店の同僚への連絡。営業トーン・接客の定型句は禁止。"
                        "用件に即した短い実務返信にする。" + _tone +
                        " 時間・席・人数などの調整は断定せず『〜で大丈夫？』と確認で返す。")
    user_prompt += (
        f"相手: {contact['code']}(ランク{contact.get('rank','B')})\n"
        f"受信区分: {msg['reason']}\n"
        f"相手からのメッセージ:「{thread_text}」\n\n"
        "返信下書きを2案、JSONで。"
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.ANTHROPIC_MODEL,
                "max_tokens": 500,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=30,
        )
        r.raise_for_status()
        out = "".join(b.get("text", "") for b in r.json().get("content", []))
        out = re.sub(r"```(json)?", "", out).strip()
        drafts = json.loads(out).get("drafts", [])[:3]
        if not drafts:
            raise ValueError("empty drafts")
    except Exception:
        drafts = _template_drafts(contact, msg["text"], msg["reason"])
    db.save_drafts(message_id, drafts)
    return drafts
