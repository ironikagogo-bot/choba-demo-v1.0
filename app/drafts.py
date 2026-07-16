"""返信下書き生成。
ANTHROPIC_API_KEY があれば Claude API、なければテンプレートにフォールバック
(APIキーなしでもパイロットの動線検証ができるように)。
"""
import json
import re

import requests

from . import config, db
from .style_profile import profile_prompt_block, contact_profile_block

SYSTEM = """あなたは本人の「返信の下書き係」。本人になりきって、届いたLINEへの返信案を作る。

いちばん大事なこと:「本人が実際に書いた文」の実例を最優先で真似る。数値プロファイル(！率・語尾等)は"参考"であって"当てにいく目標"ではない。数字合わせをすると"わざとらしく"なる。実例の声をそのまま写すこと。

わざとらしさを消す具体ルール:
- 実例より丁寧・完全に書かない。本人は端折る・崩す・句読点少なめ。それを再現する。きれいな作文にしない。
- ！や「笑」を"数合わせ"で盛らない。実例で自然に出る所だけ。
- 決まり文句(「嬉しいです」「楽しみにしております」等)を、実例に無いのに足さない。テンプレ臭の元。
- 長さは実例に合わせる。短い相手には思い切り短く(一言でよい)。冗長にしない。
- 相手ごとに温度を変える(相手専用の実例が最優先の手本)。砕けた相手には砕けて、丁寧な相手には丁寧に。
- 気の利いた一言を無理に作らない。滑るくらいなら、短く自然な受けにする。
- 事実や約束を捏造しない。日時・場所・金額は本人が確定できない限り断定せず「ふわっと」。
- 相手の言葉を1つ拾うと自然。

出力はJSONのみ: {"drafts":[{"tone":"...","text":"..."},{"tone":"...","text":"..."}]}
2案は毛色を少し変える(例: 片方は最小限の一言、もう片方は少しだけ足す)。toneは短い日本語ラベル。前置き・説明・コードブロック記号は禁止。"""


def _template_drafts(contact: dict, text: str, reason: str) -> list[dict]:
    """オフライン用の素朴なテンプレート(品質検証には使わない)。"""
    name = contact["code"] if contact else ""
    if "来店" in reason or "席" in reason:
        return [
            {"tone": "丁寧", "text": f"嬉しいです、お待ちしております。お席を押さえておきますね。何名様でいらっしゃいますか？"},
            {"tone": "軽く", "text": f"ほんとですか、楽しみにしてます！お好きなお酒、ご用意しておきますね。"},
        ]
    if "日程" in reason or "同伴" in reason:
        return [
            {"tone": "丁寧", "text": "はい、ぜひ。今週なら火曜か木曜が私ゆっくりお話しできます。どちらがよさそうですか？"},
            {"tone": "甘め", "text": "楽しみにしてますね。日にち決まったら教えてください、空けておきます。"},
        ]
    return [
        {"tone": "軽く", "text": "お疲れさまです！その話、今度お店で詳しく聞かせてくださいね。"},
        {"tone": "共感", "text": "そうだったんですね。ご連絡うれしいです、また近いうちにお話ししましょう。"},
    ]


def generate(message_id: int) -> list[dict]:
    msg = db.get_message(message_id)
    if not msg:
        return []
    contact = db.get_contact(msg["contact"]) or {"code": msg["contact"], "rank": "B"}

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
    user_prompt += (
        f"相手: {contact['code']}(ランク{contact.get('rank','B')})\n"
        f"受信区分: {msg['reason']}\n"
        f"相手からのメッセージ:「{msg['text']}」\n\n"
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
