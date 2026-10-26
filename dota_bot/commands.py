from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from .input_control import InputResult, accept_match
from .state import StateStore

log = logging.getLogger(__name__)

ACCEPT_RESULT_MESSAGES = {
    InputResult.ACCEPTED: "✅ Матч принят",
    InputResult.UNSUPPORTED: "Автопринятие работает только на Windows.",
    InputResult.PROCESS_NOT_FOUND: "Dota 2 не запущена.",
    InputResult.WINDOW_NOT_FOUND: "Не нашёл окно Dota 2.",
    InputResult.NOT_FOREGROUND: "Dota 2 не в фокусе — переключись в игру и нажми ещё раз.",
    InputResult.ERROR: "Не удалось отправить нажатие.",
}

HELP_TEXT = (
    "🎮 <b>Бот наблюдения за матчами Dota 2</b>\n\n"
    "Как только матч будет найден, я пришлю уведомление сюда.\n"
    "Если включено автопринятие, в уведомлении будет кнопка «Принять матч».\n\n"
    "Команды:\n"
    "/on — включить уведомления\n"
    "/off — выключить уведомления"
)


def normalize_command(text: str) -> str:
    """Первое слово команды без упоминания бота: '/on@Bot' -> '/on'.

    Пустая строка, если текст — не команда (не начинается с '/')."""
    parts = str(text or "").strip().split()
    if not parts or not parts[0].startswith("/"):
        return ""
    return parts[0].split("@", 1)[0].lower()


def command_reply(state: StateStore, text: str) -> str | None:
    """Обрабатывает команду пользователя. Возвращает текст ответа или None."""
    command = normalize_command(text)
    if command == "/start":
        return HELP_TEXT
    if command == "/on":
        state.set_flag("notifications_enabled", True)
        state.save()
        return "🔔 Уведомления включены."
    if command == "/off":
        state.set_flag("notifications_enabled", False)
        state.save()
        return "🔕 Уведомления выключены."
    return None


def accept_result(config, chat_id: str) -> tuple[str, bool] | None:
    """Выполняет локальное автопринятие. (текст ответа, принято?) или None."""
    if not config.remote_control.enabled:
        return ("Автопринятие выключено.", False)
    # В релей-режиме chat_id живёт на сервере и входящие уже отфильтрованы
    # релеем; в прямом режиме проверяем владельца вручную.
    if config.telegram.chat_id and str(chat_id) != str(config.telegram.chat_id):
        return ("Кнопка доступна только владельцу бота.", False)
    result = accept_match(
        config.remote_control.process_name,
        config.remote_control.focus_window,
    )
    log.info("Запрос автопринятия: %s", result.value)
    return (ACCEPT_RESULT_MESSAGES[result], result is InputResult.ACCEPTED)


# ── обёртки для python-telegram-bot (прямой режим) ──

def _state(context: ContextTypes.DEFAULT_TYPE) -> StateStore | None:
    return context.application.bot_data.get("state")


def _start_code(update: Update) -> str | None:
    if update.message is None:
        return None
    parts = str(update.message.text or "").strip().split()
    if not parts or parts[0].split("@", 1)[0].lower() != "/start":
        return None
    return parts[1] if len(parts) > 1 else None


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pairing = context.application.bot_data.get("pairing")
    code = _start_code(update)
    if pairing is not None and code is not None:
        if update.effective_chat is None:
            return
        pairing.load()
        chat_id = str(update.effective_chat.id)
        if pairing.claim(code, chat_id):
            await update.message.reply_text("✅ Telegram привязан. Возвращайся в консоль — мастер продолжит настройку.")
        else:
            await update.message.reply_text("Такой код не найден или уже использован. Запусти setup ещё раз — мастер покажет действующий код.")
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    if query.data != "accept_match":
        await query.answer()
        return

    config = context.application.bot_data.get("config")
    if config is None:
        await query.answer("Ошибка конфигурации.", show_alert=True)
        return
    chat_id = query.message.chat_id if query.message else None
    result = accept_result(config, str(chat_id))
    if result is None:
        await query.answer()
        return
    text, accepted = result
    await query.answer(text, show_alert=not accepted)
    if accepted:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            log.warning("Не удалось убрать кнопку принятия", exc_info=True)


async def cmd_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _state(context)
    if state is None:
        return
    reply = command_reply(state, "/on")
    if reply:
        await update.message.reply_text(reply)


async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _state(context)
    if state is None:
        return
    reply = command_reply(state, "/off")
    if reply:
        await update.message.reply_text(reply)
