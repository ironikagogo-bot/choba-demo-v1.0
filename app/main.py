"""帳場 バックエンド API。

起動:  uvicorn app.main:app --reload --port 8000
UI:    http://localhost:8000/
デモ受信: python scripts/demo_feed.py  (別ターミナル)
"""
import os
import time

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import campaign, config, db, deskservice, drafts, push, triage
from .style_profile import extract_profile
from .provisioning import linking, MockProvisioner, DeskState

app = FastAPI(title="帳場 pilot API")
db.init()
linking.init()

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# パイロット: プロビジョナはモック(道4スパイクで RealProvisioner に差し替え)
PROVISIONER = MockProvisioner()
DESKS: dict[str, object] = {}   # user_id -> Desk (パイロットはメモリ保持)

# 初期データ(空DBのときのみ)
if not db.list_contacts():
    if config.DEMO:
        # クラウドの「見せる用」ダミー顧客(タグ・誕生日・来店記録つき=全機能が動いて見える)
        _now = time.time()
        _demo = [
            # code, rank, cycle, tags, birthday(MM-DD), 前回来店(日前 or None)
            ("T.会長", "S", 10, "VIP", "07-20", 2),
            ("K.専務", "A", 14, "常連", "", 40),      # ご無沙汰(周期超過)
            ("M.先生", "A", 21, "", "07-22", None),   # 誕生日近い
            ("Y.社長", "B", 30, "", "", 1),           # 直近来店=お礼対象
            ("S.部長", "B", 30, "常連", "", 90),       # ご無沙汰
            ("H.常務", "S", 12, "VIP", "", 5),
        ]
        for code, rank, cyc, tags, bd, days in _demo:
            db.upsert_contact(code, rank, cyc, "", tags, bd)
            if days is not None:
                db.set_last_visit(code, _now - days * 86400)
    else:
        for code, rank, cyc in [("T.会長", "S", 10), ("K.専務", "A", 14),
                                ("M.先生", "A", 21), ("Y.社長", "B", 30), ("S.部長", "B", 30)]:
            db.upsert_contact(code, rank, cyc)


class Incoming(BaseModel):
    contact: str
    text: str


class Action(BaseModel):
    action: str  # replied / stamped / deferred / skipped


@app.post("/api/incoming")
def incoming(body: Incoming):
    """手動の受信登録(開発・検証用)。デスク経由と同じパイプラインを通る。"""
    mid, cat, reason = deskservice.ingest(body.contact, body.text)
    return {"id": mid, "category": cat, "reason": reason}


# ---------- 仮想デスク(デモ盤: 本番と同じ流れ・LINEを読む部分だけ台本) ----------

class DeskStart(BaseModel):
    user_id: str = "demo"
    speed: float = 1.0   # 台本の速度倍率。1.0=約90秒で1日分。0.5で倍速


@app.post("/api/desk/start")
def desk_start(body: DeskStart):
    """デスク開設: 仮想PC起動(模擬) → LINEログインQR提示。"""
    desk = deskservice.SERVICE.start(body.user_id, body.speed)
    return {
        "desk_state": desk.state.value,
        "line_login_qr": desk.line_login_qr,
        "guide": [
            "本番: このQRをLINEアプリで読み、『PCでログイン』を承認します",
            "デモ: 下のボタンを押すと承認が再現されます",
        ],
        "notice": "実接続はLINE利用規約上グレーな領域を含みます。説明と同意の上での提供になります。",
    }


@app.post("/api/desk/approve")
def desk_approve():
    """LINEログイン承認(デモではタップで再現)→ 監視開始、受信が流れ始める。"""
    try:
        state = deskservice.SERVICE.approve_login()
    except RuntimeError:
        raise HTTPException(409, "デスクが未開設です")
    return {"desk_state": state.value, "active": state == DeskState.ACTIVE}


@app.get("/api/desk/status")
def desk_status():
    """デスク状態と裏側コンソール(デスクの動きログ)。"""
    return deskservice.SERVICE.status()


@app.post("/api/desk/replay")
def desk_replay():
    """受信デモをもう一度最初から流す(受信箱はリセット)。"""
    try:
        deskservice.SERVICE.replay()
    except RuntimeError:
        raise HTTPException(409, "デスクが稼働していません")
    return {"ok": True}


@app.post("/api/desk/stop")
def desk_stop():
    deskservice.SERVICE.stop()
    return {"ok": True}


# ---------- Android通知の受信口(本命構成) ----------
# Androidのサブ端末(同一アカウント)の転送アプリ/専用アプリが、LINEの着信通知を
# ここに POST する。JSON でも form でも受け付ける(転送アプリの実装差を吸収)。
#   例(JSON): {"token":"...","package":"jp.naver.line.android","title":"相手名","text":"本文"}

@app.post("/api/android/notify")
async def android_notify(request: Request):
    # クエリパラメータ・JSON・form のどれでも受ける(転送アプリの実装差を吸収)。
    # クエリパラメータはURLエンコード済みで、長文・改行・記号に強い(推奨)。
    data = {}
    for k, v in request.query_params.items():
        if v and not data.get(k):
            data[k] = v
    try:
        body = await request.json()
    except Exception:
        try:
            body = dict(await request.form())
        except Exception:
            body = {}
    if isinstance(body, dict):
        for k, v in body.items():
            if v and not data.get(k):
                data[k] = str(v)
    if config.INGEST_TOKEN and str(data.get("token", "")) != config.INGEST_TOKEN:
        raise HTTPException(401, "bad token")
    res = deskservice.SERVICE.android_ingest(
        str(data.get("package", "") or ""),
        str(data.get("title", "") or ""),
        str(data.get("text", "") or ""),
        ticker=str(data.get("ticker", "") or ""),
        big_text=str(data.get("big_text", "") or ""),
        sub_text=str(data.get("sub_text", "") or ""),
        text_lines=str(data.get("text_lines", "") or ""),
    )
    return res


@app.get("/api/inbox")
def inbox():
    msgs = db.open_messages()
    out = {"urgent": [], "rally": {}, "batch": [], "batch_time": config.BATCH_TIME}
    for m in msgs:
        if m["category"] == "urgent":
            out["urgent"].append(m)
        elif m["category"] == "rally":
            out["rally"].setdefault(m["contact"], []).append(m)
        else:
            out["batch"].append(m)
    return out


@app.post("/api/messages/{mid}/drafts")
def make_drafts(mid: int):
    if not db.get_message(mid):
        raise HTTPException(404)
    existing = db.get_drafts(mid)
    if existing:
        # デスクが受信時に先行生成済み(即対応)。再生成せずそれを出す
        return {"drafts": [{"text": d["text"], "tone": d["tone"]} for d in existing],
                "mode": "prepared"}
    return {"drafts": drafts.generate(mid),
            "mode": "llm" if config.ANTHROPIC_API_KEY else "template"}


@app.post("/api/messages/{mid}/action")
def act(mid: int, body: Action):
    msg = db.get_message(mid)
    if not msg:
        raise HTTPException(404)
    if body.action not in ("replied", "stamped", "deferred", "skipped"):
        raise HTTPException(400, "bad action")
    db.set_status(mid, body.action)
    # 来店系の即対応に返信したら仮予約として自動記録(入力ゼロ原則)
    if body.action == "replied" and "来店" in msg["reason"]:
        db.add_event(msg["contact"], "visit", f"{msg['contact']} 来店(仮)", "tentative")
    return {"ok": True}


@app.post("/api/profile/import")
async def import_profile(file: UploadFile, self_name: str = "自分",
                         contact: str | None = None, auto_register: bool = True):
    """LINEトーク履歴(.txt)を取り込む。
    - 本人全体の文体(_global)を抽出・保存
    - contact 指定時: その相手専用プロファイルも作成
    - contact 未指定 & auto_register: 履歴に登場する相手を顧客として自動登録し、
      各相手の専用プロファイルを作成(=顧客登録と学習を一度に行う本命フロー)
    """
    from .style_profile import discover_contacts, extract_contact_profile, Profile
    text = (await file.read()).decode("utf-8", errors="replace")

    result = {"registered": [], "profiled": []}

    if contact:
        # 特定相手のスレッドから本人文体 + 相手別プロファイル
        p = extract_profile(text, self_name=self_name)
        if p.n_messages == 0:
            raise HTTPException(422, "本人のメッセージを検出できませんでした。self_name を確認してください。")
        db.save_profile("_global", p.to_dict())
        cp = extract_contact_profile(text, contact, self_name=self_name)
        db.save_profile(contact, cp)
        if not db.get_contact(contact):
            db.upsert_contact(contact, "B")
            result["registered"].append(contact)
        result["profiled"].append(contact)
        result["global_msgs"] = p.n_messages
        return result

    # 全体取り込み: 本人文体 + 全相手の自動登録&学習
    p = extract_profile(text, self_name=self_name)
    if p.n_messages == 0:
        raise HTTPException(422, "本人のメッセージを検出できませんでした。self_name を確認してください。")
    db.save_profile("_global", p.to_dict())
    result["global_msgs"] = p.n_messages

    for name in discover_contacts(text, self_name=self_name):
        cp = extract_contact_profile(text, name, self_name=self_name)
        db.save_profile(name, cp)
        result["profiled"].append(name)
        if auto_register and not db.get_contact(name):
            db.upsert_contact(name, "B")
            result["registered"].append(name)
    return result


class ContactIn(BaseModel):
    code: str
    rank: str = "B"
    cycle_days: int | None = None
    note: str = ""
    tags: str = ""
    birthday: str = ""   # 'MM-DD'(任意)


@app.post("/api/contacts")
def add_contact(body: ContactIn):
    db.upsert_contact(body.code, body.rank, body.cycle_days, body.note,
                      body.tags, body.birthday)
    if body.cycle_days:
        db.set_cycle(body.code, body.cycle_days)
    return db.get_contact(body.code)


@app.post("/api/contacts/{code}/visit")
def record_visit(code: str):
    """来店を記録(お礼・ご無沙汰判定の元データ)。"""
    if not db.get_contact(code):
        raise HTTPException(404)
    db.set_last_visit(code)
    return db.get_contact(code)


class RankIn(BaseModel):
    rank: str


@app.put("/api/contacts/{code}/rank")
def update_rank(code: str, body: RankIn):
    if not db.get_contact(code):
        raise HTTPException(404)
    if body.rank not in ("S", "A", "B"):
        raise HTTPException(400, "rank は S/A/B")
    db.set_rank(code, body.rank)
    return db.get_contact(code)


@app.get("/api/profile")
def profile(contact: str = "_global"):
    return db.get_profile(contact) or {}


@app.get("/api/events")
def events():
    return db.list_events()


@app.get("/api/contacts")
def contacts():
    return db.list_contacts()


# ---------- 一斉下書き(キャンペーン) ----------
def _split(s: str):
    return [x for x in (s or "").replace("　", ",").split(",") if x.strip()]


@app.get("/api/campaign/recipients")
def campaign_recipients(ranks: str = "", tags: str = "", mode: str = "greeting"):
    """選択中のあて先プレビュー(生成はしない)。"""
    recips = campaign.select_recipients(_split(ranks), _split(tags), mode)
    return {"count": len(recips), "season": campaign.season_label(), "recipients": recips}


class CampaignIn(BaseModel):
    mode: str = "greeting"        # greeting=営業のきっかけ / thanks=来店お礼
    ranks: list[str] = []
    tags: list[str] = []
    template: str = ""
    codes: list[str] = []         # 指定時はこの相手だけ(UIの個別チェックを反映)


@app.post("/api/campaign/generate")
def campaign_generate(body: CampaignIn):
    """あて先ぶんの下書きを一人ずつ違う内容で一括生成(送信も保存もしない)。"""
    return campaign.generate(body.mode, body.ranks, body.tags, body.template,
                             codes=body.codes or None)


@app.get("/campaign")
def campaign_page():
    return FileResponse(os.path.join(STATIC_DIR, "campaign.html"))


@app.get("/customers")
def customers_page():
    return FileResponse(os.path.join(STATIC_DIR, "customers.html"))


@app.get("/demo")
def demo_page():
    """サーバー不要でも動く静的UXデモ(友人に見せる用・単体で完結)。"""
    return FileResponse(os.path.join(STATIC_DIR, "demo.html"))


class OnboardStart(BaseModel):
    user_id: str


@app.post("/api/onboard/start")
def onboard_start(body: OnboardStart):
    """①端末リンクQR発行 + ②仮想デスク起動(LINEログインQR取得)。"""
    link = linking.create_link_token(body.user_id)
    desk = PROVISIONER.provision(body.user_id)
    DESKS[body.user_id] = desk
    return {
        "device_link": link,                    # ①: 本人iPhoneを帳場に紐付け
        "line_login_qr": desk.line_login_qr,    # ②: 本人がLINEアプリで読む(PCログイン承認)
        "desk_state": desk.state.value,
        "guide": [
            "手順1: この端末リンクQRを読むと、あなたのスマホに通知が届くようになります",
            "手順2: LINEログインQRをLINEアプリで読み、『PCでログイン』を承認してください",
            "手順3: 完了。以降デスクが受信を見張ります(読み取りのみ・送信はしません)",
        ],
        "notice": "手順2はPCでLINEを開くのと同じ操作です。規約上グレーな領域を含むため、説明と同意の上でご利用ください。",
    }


@app.post("/api/onboard/poll")
def onboard_poll(body: OnboardStart):
    """②のLINEログインが承認されたか確認。"""
    desk = DESKS.get(body.user_id)
    if not desk:
        raise HTTPException(404, "desk not found")
    state = PROVISIONER.poll_login(desk)
    return {"desk_state": state.value,
            "active": state == DeskState.ACTIVE}


@app.get("/api/onboard/health")
def onboard_health(user_id: str):
    """デスク死活。SESSION_LOST ならフェイルセーフ(LINE通知を戻す案内)を返す。"""
    desk = DESKS.get(user_id)
    if not desk:
        raise HTTPException(404)
    state = PROVISIONER.health(desk)
    failsafe = state in (DeskState.SESSION_LOST, DeskState.REAUTH_REQUIRED)
    return {"desk_state": state.value, "failsafe": failsafe,
            "message": "デスクが停止しました。LINEアプリの通知を一時的にオンに戻してください。" if failsafe else "正常"}


@app.get("/link")
def link_page(token: str):
    """①QRを読んだ本人のiPhoneが着地するページ(端末紐付け)。"""
    try:
        res = linking.claim_link_token(token)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "device_id": res["device_id"], "message": "この端末を帳場に紐付けました。"}


# ---------- Web Push (③ デスク → 本人スマホ通知) ----------

@app.get("/api/push/public_key")
def push_public_key():
    """PWA が購読時に使う VAPID (Voluntary Application Server Identification) 公開鍵。"""
    return {"available": push.AVAILABLE, "key": push.public_key()}


@app.post("/api/push/subscribe")
def push_subscribe(sub: dict):
    if not isinstance(sub.get("endpoint"), str) or not sub["endpoint"]:
        raise HTTPException(400, "endpoint がありません")
    db.save_subscription(sub)
    return {"ok": True, "count": len(db.list_subscriptions())}


class Unsubscribe(BaseModel):
    endpoint: str


@app.post("/api/push/unsubscribe")
def push_unsubscribe(body: Unsubscribe):
    db.delete_subscription(body.endpoint)
    return {"ok": True}


@app.post("/api/push/test")
def push_test():
    """登録済み端末へテスト通知(到達確認用)。"""
    sent = push.notify("帳場", "通知テスト：この経路で即対応をお知らせします", url="/")
    return {"sent": sent, "subscriptions": len(db.list_subscriptions())}


# ---------- PWA 配信 ----------
# sw.js は「/」スコープで動かすためルート直下で配信する(/static 配下だとスコープが狭まる)

@app.get("/sw.js")
def service_worker():
    return FileResponse(os.path.join(STATIC_DIR, "sw.js"), media_type="application/javascript")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(os.path.join(STATIC_DIR, "manifest.json"),
                        media_type="application/manifest+json")


@app.get("/")
def index():
    """本人が触る PWA (Progressive Web App)。"""
    return FileResponse(os.path.join(STATIC_DIR, "app.html"))


@app.get("/dev")
def dev_ui():
    """旧・簡易テストUI(開発用に残置)。"""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/simu")
def simulation():
    """営業用シミュレーション(『ある休日の1日』ウォークスルー。自己完結・API不要)。"""
    return FileResponse(os.path.join(STATIC_DIR, "simulation.html"))
