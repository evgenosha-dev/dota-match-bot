"""Центральный релей для общего Telegram-бота Dota Match Bot.

Один небольшой сервис, который:
- принимает обновления общего бота через webhook (он — единственный
  потребитель обновлений, поэтому любое число агентов работает без 409);
- связывает личные ключи агентов с chat_id через коды привязки;
- пересылает уведомления агентов в чаты пользователей;
- складывает команды (/on, /off, /start) и нажатия кнопки «Принять матч»
  во входящие, которые агенты забирают опросом.

Развёртывание: любой хостинг с Python + Flask (см. README, бесплатный
вариант — Hugging Face Spaces через Docker). Переменные окружения:

    TELEGRAM_TOKEN   токен общего бота (хранится ТОЛЬКО здесь)
    BASE_URL         публичный адрес сервиса, напр. https://user.pythonanywhere.com
    WEBHOOK_SECRET   секрет пути вебхука (генерируется, если пусто)
    ADMIN_KEY        ключ для /setup-webhook и /admin/*
    DB_PATH          файл SQLite (по умолчанию relay.db рядом с этим файлом)
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import string
import time
from pathlib import Path

import httpx
from flask import Flask, jsonify, request

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper()),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("relay")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET") or secrets.token_urlsafe(16)
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
DB_PATH = Path(os.environ.get("DB_PATH") or Path(__file__).with_name("relay.db"))
API = "https://api.telegram.org"
CODE_ALPHABET = string.ascii_uppercase + "23456789"
CODE_ALPHABET = "".join(c for c in CODE_ALPHABET if c not in "ILO01")

app = Flask(__name__)
_bot_username: str | None = None


# ── хранилище ──

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                code TEXT UNIQUE,
                chat_id TEXT,
                created_at REAL,
                last_message_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_chat ON events(chat_id, id);
            """
        )


def new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


# ── Telegram API ──

def tg_call(method: str, **payload) -> dict:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN не задан")
    response = httpx.post(
        f"{API}/bot{TELEGRAM_TOKEN}/{method}",
        json={k: v for k, v in payload.items() if v is not None},
        timeout=15.0,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API {method}: {data.get('description')}")
    return data


def bot_username() -> str:
    global _bot_username
    if _bot_username is None:
        try:
            me = tg_call("getMe").get("result") or {}
            _bot_username = str(me.get("username") or "bot")
        except Exception as exc:
            log.warning("getMe не удался: %s", exc)
            _bot_username = "bot"
    return _bot_username


# ── авторизация агентов ──

def bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip() or None
    return None


def session_for(token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    with db() as conn:
        return conn.execute(
            "SELECT * FROM sessions WHERE token = ?", (token,)
        ).fetchone()


def require_session() -> sqlite3.Row | tuple:
    session = session_for(bearer_token())
    if session is None:
        return jsonify({"error": "unauthorized"}), 401
    return session


def require_admin() -> bool:
    return bool(ADMIN_KEY) and bearer_token() == ADMIN_KEY


# ── привязка ──

@app.post("/pair")
def pair_create():
    session = require_session()
    if isinstance(session, tuple):
        return session
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE token = ?", (session["token"],)
        ).fetchone()
        if row is None:
            code = new_code()
            conn.execute(
                "INSERT INTO sessions(token, code, chat_id, created_at) VALUES (?, ?, NULL, ?)",
                (session["token"], code, time.time()),
            )
        else:
            code = row["code"]
    return jsonify({"code": code, "chat_id": None, "bot_username": bot_username()})


@app.get("/pair")
def pair_status():
    session = require_session()
    if isinstance(session, tuple):
        return session
    return jsonify(
        {"code": session["code"], "chat_id": session["chat_id"], "bot_username": bot_username()}
    )


# ── входящие команды ──

@app.get("/inbox")
def inbox():
    session = require_session()
    if isinstance(session, tuple):
        return session
    chat_id = session["chat_id"]
    if not chat_id:
        return jsonify({"cursor": 0, "items": []})
    try:
        after = max(0, int(request.args.get("after", 0)))
    except ValueError:
        after = 0

    with db() as conn:
        rows = conn.execute(
            "SELECT id, type, payload FROM events WHERE chat_id = ? AND id > ? ORDER BY id LIMIT 100",
            (chat_id, after),
        ).fetchall()
    items = [{"id": r["id"], "type": r["type"], **json.loads(r["payload"])} for r in rows]
    cursor = rows[-1]["id"] if rows else after
    return jsonify({"cursor": cursor, "items": items})


# ── отправка уведомлений от имени общего бота ──

@app.post("/send")
def send():
    session = require_session()
    if isinstance(session, tuple):
        return session
    if not session["chat_id"]:
        return jsonify({"ok": False, "error": "not paired"}), 409
    payload = request.get_json(silent=True) or {}
    data = tg_call(
        "sendMessage",
        chat_id=session["chat_id"],
        text=str(payload.get("text") or ""),
        parse_mode=payload.get("parse_mode") or "HTML",
        reply_markup=payload.get("reply_markup"),
    )
    message_id = (data.get("result") or {}).get("message_id")
    if isinstance(message_id, int):
        with db() as conn:
            conn.execute(
                "UPDATE sessions SET last_message_id = ? WHERE token = ?",
                (message_id, session["token"]),
            )
    return jsonify({"ok": True, "result": {"message_id": message_id}})


@app.post("/edit")
def edit():
    session = require_session()
    if isinstance(session, tuple):
        return session
    message_id = session["last_message_id"]
    if not session["chat_id"] or not message_id:
        return jsonify({"ok": False, "error": "nothing to edit"}), 409
    payload = request.get_json(silent=True) or {}
    ok = True
    if payload.get("text") is not None:
        try:
            tg_call(
                "editMessageText",
                chat_id=session["chat_id"],
                message_id=message_id,
                text=str(payload["text"]),
                parse_mode=payload.get("parse_mode") or "HTML",
            )
        except Exception as exc:
            log.warning("editMessageText не удался: %s", exc)
            ok = False
    if "reply_markup" in payload:
        markup = None if payload.get("reply_markup") == "remove" else payload.get("reply_markup")
        try:
            tg_call(
                "editMessageReplyMarkup",
                chat_id=session["chat_id"],
                message_id=message_id,
                reply_markup=markup,
            )
        except Exception as exc:
            log.warning("editMessageReplyMarkup не удался: %s", exc)
            ok = False
    return jsonify({"ok": ok})


@app.post("/answer")
def answer():
    session = require_session()
    if isinstance(session, tuple):
        return session
    payload = request.get_json(silent=True) or {}
    try:
        tg_call(
            "answerCallbackQuery",
            callback_query_id=str(payload.get("callback_id") or ""),
            text=str(payload.get("text") or ""),
            show_alert=bool(payload.get("alert")),
        )
        return jsonify({"ok": True})
    except Exception as exc:
        log.warning("answerCallbackQuery не удался: %s", exc)
        return jsonify({"ok": False})


# ── webhook Telegram ──

def bind_chat(code: str, chat_id: str) -> bool:

    with db() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE code = ?", (code,)).fetchone()
        if row is None or row["chat_id"]:
            return False
        conn.execute(
            "UPDATE sessions SET chat_id = ? WHERE token = ?", (chat_id, row["token"])
        )
    try:
        tg_call(
            "sendMessage",
            chat_id=chat_id,
            text="✅ Telegram привязан. Возвращайся в мастер настройки — он продолжит сам.",
            parse_mode="HTML",
        )
    except Exception as exc:
        log.warning("Подтверждение привязки не отправлено: %s", exc)
    return True


def add_event(chat_id: str, kind: str, **payload) -> None:

    with db() as conn:
        conn.execute(
            "INSERT INTO events(chat_id, type, payload, created_at) VALUES (?, ?, ?, ?)",
            (chat_id, kind, json.dumps(payload, ensure_ascii=False), time.time()),
        )


@app.post(f"/telegram/{WEBHOOK_SECRET}")
def telegram_webhook(secret: str):
    if secret != WEBHOOK_SECRET:
        return jsonify({"error": "forbidden"}), 403
    update = request.get_json(silent=True) or {}

    message = update.get("message") or update.get("edited_message") or {}
    if message:
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id") or "")
        if not chat_id or chat.get("type") != "private":
            return jsonify({"ok": True})
        text = str(message.get("text") or "").strip()
        parts = text.split()
        command = parts[0].split("@", 1)[0].lower() if parts else ""
        if command == "/start" and len(parts) > 1:
            code = parts[1].strip().upper()
            if not bind_chat(code, chat_id):
                try:
                    tg_call(
                        "sendMessage",
                        chat_id=chat_id,
                        text="Такой код не найден или уже использован. Запусти мастер настройки ещё раз — он покажет действующий код.",
                    )
                except Exception as exc:
                    log.warning("Ответ по неверному коду не отправлен: %s", exc)
            return jsonify({"ok": True})
        if command in {"/start", "/on", "/off"}:
            add_event(chat_id, "command", text=text)
        return jsonify({"ok": True})

    query = update.get("callback_query") or {}
    if query:
        msg = query.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id") or "")
        if query.get("data") == "accept_match" and chat_id:
            add_event(
                chat_id,
                "accept",
                callback_id=str(query.get("id") or ""),
                message_id=msg.get("message_id"),
                from_user=(query.get("from") or {}).get("id"),
            )
    return jsonify({"ok": True})


# ── администрирование ──

@app.get("/health")
def health():
    return jsonify({"ok": True, "bot_username": bot_username()})


@app.post("/setup-webhook")
def setup_webhook():
    if not require_admin():
        return jsonify({"error": "forbidden"}), 403
    if not BASE_URL:
        return jsonify({"error": "BASE_URL не задан"}), 400
    url = f"{BASE_URL}/telegram/{WEBHOOK_SECRET}"
    try:
        tg_call("setWebhook", url=url, allowed_updates=["message", "callback_query"])
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})
    log.info("Webhook установлен: %s", url)
    return jsonify({"ok": True, "webhook": url})


@app.get("/admin/sessions")
def admin_sessions():
    if not require_admin():
        return jsonify({"error": "forbidden"}), 403
    with db() as conn:
        rows = conn.execute(
            "SELECT token, code, chat_id, created_at FROM sessions ORDER BY created_at"
        ).fetchall()
    return jsonify(
        {
            "sessions": [
                {
                    "token": r["token"][:8] + "…",
                    "code": r["code"],
                    "chat_id": r["chat_id"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
        }
    )


init_db()
if TELEGRAM_TOKEN and BASE_URL:
    try:
        result = tg_call(
            "setWebhook",
            url=f"{BASE_URL}/telegram/{WEBHOOK_SECRET}",
            allowed_updates=["message", "callback_query"],
        )
        log.info("Webhook при старте: %s", result.get("description"))
    except Exception as exc:
        log.warning("Автоустановка webhook не удалась (%s) — используй POST /setup-webhook", exc)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 7860)))
