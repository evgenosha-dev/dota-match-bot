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


def _state(context: ContextTypes.DEFAULT_TYPE) -> StateStore | None:
    return context.application.bot_data.get("state")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    if query.data != "accept_match":
        await query.answer()
        return

    config = context.application.bot_data.get("config")
    if config is None or not config.remote_control.enabled:
        await query.answer("Автопринятие выключено.", show_alert=True)
        return
    chat_id = query.message.chat_id if query.message else None
    if str(chat_id) != str(config.telegram.chat_id):
        await query.answer("Кнопка доступна только владельцу бота.", show_alert=True)
        return

    result = await asyncio.to_thread(
        accept_match,
        config.remote_control.process_name,
        config.remote_control.focus_window,
    )
    log.info("Запрос автопринятия: %s", result.value)
    await query.answer(
        ACCEPT_RESULT_MESSAGES[result],
        show_alert=result is not InputResult.ACCEPTED,
    )
    if result is InputResult.ACCEPTED:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            log.warning("Не удалось убрать кнопку принятия", exc_info=True)


async def cmd_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _state(context)
    if state is None:
        return
    state.set_flag("notifications_enabled", True)
    state.save()
    await update.message.reply_text("🔔 Уведомления включены.")


async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _state(context)
    if state is None:
        return
    state.set_flag("notifications_enabled", False)
    state.save()
    await update.message.reply_text("🔕 Уведомления выключены.")
