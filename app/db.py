"""SQLite 永続層。パイロット規模(数名)前提の素直な実装。"""
import json
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts(
  code TEXT PRIMARY KEY,          -- 表示名(コードネーム推奨)
  rank TEXT NOT NULL DEFAULT 'B', -- S/A/B
  cycle_days INTEGER,             -- 来店周期(日)
  last_visit_ts REAL,
  note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact TEXT NOT NULL,
  text TEXT NOT NULL,
  ts REAL NOT NULL,
  category TEXT NOT NULL,         -- urgent / rally / batch
  reason TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',  -- open / replied / stamped / deferred / skipped
  FOREIGN KEY(contact) REFERENCES contacts(code)
);
CREATE TABLE IF NOT EXISTS drafts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL,
  text TEXT NOT NULL,
  tone TEXT DEFAULT '',
  FOREIGN KEY(message_id) REFERENCES messages(id)
);
CREATE TABLE IF NOT EXISTS style_profile(
  contact TEXT PRIMARY KEY,       -- '_global' = 本人全体の文体
  profile_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events(  -- 予約・同伴など、成績の元データ
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact TEXT NOT NULL,
  kind TEXT NOT NULL,             -- visit / dohan / anniversary
  label TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'tentative',  -- tentative / confirmed
  created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS push_subscriptions(  -- Web Push 購読(本人スマホ)
  endpoint TEXT PRIMARY KEY,
  subscription_json TEXT NOT NULL,
  created_ts REAL NOT NULL
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        # 後付けカラムの移行(既存DBでも安全に)。既にあれば無視。
        for ddl in ("tags TEXT DEFAULT ''", "birthday TEXT DEFAULT ''"):
            try:
                c.execute(f"ALTER TABLE contacts ADD COLUMN {ddl}")
            except sqlite3.OperationalError:
                pass


def upsert_contact(code: str, rank: str = "B", cycle_days=None, note: str = "",
                   tags: str = "", birthday: str = ""):
    with conn() as c:
        c.execute(
            "INSERT INTO contacts(code,rank,cycle_days,note,tags,birthday) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET rank=excluded.rank, note=excluded.note, "
            "tags=excluded.tags, birthday=excluded.birthday",
            (code, rank, cycle_days, note, tags, birthday),
        )


def set_tags(code: str, tags: str):
    with conn() as c:
        c.execute("UPDATE contacts SET tags=? WHERE code=?", (tags, code))


def set_last_visit(code: str, ts=None):
    """来店を記録(お礼・ご無沙汰判定の元データ)。eventsにも1件残す。"""
    ts = ts or time.time()
    with conn() as c:
        c.execute("UPDATE contacts SET last_visit_ts=? WHERE code=?", (ts, code))
        c.execute("INSERT INTO events(contact,kind,label,status,created_ts) "
                  "VALUES(?,?,?,?,?)", (code, "visit", "来店", "confirmed", ts))


def set_rank(code: str, rank: str):
    with conn() as c:
        c.execute("UPDATE contacts SET rank=? WHERE code=?", (rank, code))


def set_cycle(code: str, cycle_days: int):
    with conn() as c:
        c.execute("UPDATE contacts SET cycle_days=? WHERE code=?", (cycle_days, code))


def get_contact(code: str):
    with conn() as c:
        r = c.execute("SELECT * FROM contacts WHERE code=?", (code,)).fetchone()
        return dict(r) if r else None


def list_contacts():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM contacts ORDER BY rank, code")]


def last_message_ts(contact: str):
    with conn() as c:
        r = c.execute(
            "SELECT ts FROM messages WHERE contact=? ORDER BY ts DESC LIMIT 1", (contact,)
        ).fetchone()
        return r["ts"] if r else None


def add_message(contact: str, text: str, category: str, reason: str, ts=None) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO messages(contact,text,ts,category,reason) VALUES(?,?,?,?,?)",
            (contact, text, ts or time.time(), category, reason),
        )
        return cur.lastrowid


def open_messages():
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM messages WHERE status='open' ORDER BY ts ASC")]


def get_message(mid: int):
    with conn() as c:
        r = c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        return dict(r) if r else None


def set_status(mid: int, status: str):
    with conn() as c:
        c.execute("UPDATE messages SET status=? WHERE id=?", (status, mid))


def save_drafts(mid: int, drafts):
    with conn() as c:
        c.execute("DELETE FROM drafts WHERE message_id=?", (mid,))
        for d in drafts:
            c.execute("INSERT INTO drafts(message_id,text,tone) VALUES(?,?,?)",
                      (mid, d["text"], d.get("tone", "")))


def get_drafts(mid: int):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM drafts WHERE message_id=?", (mid,))]


def save_profile(contact: str, profile: dict):
    with conn() as c:
        c.execute(
            "INSERT INTO style_profile(contact,profile_json) VALUES(?,?) "
            "ON CONFLICT(contact) DO UPDATE SET profile_json=excluded.profile_json",
            (contact, json.dumps(profile, ensure_ascii=False)),
        )


def get_profile(contact: str = "_global"):
    with conn() as c:
        r = c.execute("SELECT profile_json FROM style_profile WHERE contact=?", (contact,)).fetchone()
        return json.loads(r["profile_json"]) if r else None


def clear_demo_messages():
    """デモ再生用: 受信・下書き・実績を消す(顧客とプロファイルは残す)。"""
    with conn() as c:
        c.execute("DELETE FROM drafts")
        c.execute("DELETE FROM messages")
        c.execute("DELETE FROM events")


def save_subscription(sub: dict):
    """Web Push 購読を保存(同一 endpoint は上書き)。"""
    with conn() as c:
        c.execute(
            "INSERT INTO push_subscriptions(endpoint,subscription_json,created_ts) VALUES(?,?,?) "
            "ON CONFLICT(endpoint) DO UPDATE SET subscription_json=excluded.subscription_json",
            (sub["endpoint"], json.dumps(sub, ensure_ascii=False), time.time()),
        )


def delete_subscription(endpoint: str):
    with conn() as c:
        c.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))


def list_subscriptions():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM push_subscriptions")]


def add_event(contact: str, kind: str, label: str, status: str = "tentative"):
    with conn() as c:
        c.execute("INSERT INTO events(contact,kind,label,status,created_ts) VALUES(?,?,?,?,?)",
                  (contact, kind, label, status, time.time()))


def list_events():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM events ORDER BY created_ts DESC")]
