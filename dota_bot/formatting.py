from __future__ import annotations

from html import escape

from .gamedata import game_mode_label, lobby_type_label, region_label
from .models import EventType, MatchEvent

TEAM_LABELS = {"radiant": "Свет", "dire": "Тьма"}
TEAM_ICONS = {"radiant": "🟢", "dire": "🔴"}


def _duration(seconds: int | None) -> str | None:
    if not seconds or seconds <= 0:
        return None
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def accept_keyboard() -> dict:
    return {
        "inline_keyboard": [[{"text": "✅ Принять матч", "callback_data": "accept_match"}]]
    }


def format_match_found(event: MatchEvent, remote_accept: bool = False) -> str:
    lines = ["🚨 <b>Матч найден!</b>", ""]

    if event.source == "gsi":
        pass
    elif event.source == "steam":
        lines.append("Steam зафиксировал переход в игру Dota 2.")
    elif event.source == "accept_button":
        if remote_accept:
            lines.append(
                "Найден матч в очереди поиска.\n"
                "<b>Нажми «Принять матч» в этом сообщении.</b>"
            )
        else:
            lines.append(
                "Найден матч в очереди поиска.\n"
                "<b>Открой игру и нажми «Принять».</b>"
            )
    else:
        lines.append("Обнаружен новый матч.")

    lines.append("")
    if event.hero:
        lines.append(f"🧩 <b>Герой:</b> {escape(event.hero)}")
    if event.team:
        label = TEAM_LABELS.get(event.team.lower(), event.team)
        lines.append(f"⚔️ <b>Команда:</b> {escape(label)}")
    if event.match_id:
        lines.append(f"🆔 <b>Match ID:</b> <code>{escape(str(event.match_id))}</code>")
    if event.game_state:
        lines.append(f"🕹 <b>Состояние:</b> <code>{escape(event.game_state)}</code>")
    return "\n".join(lines)


def format_match_finished(event: MatchEvent) -> str:
    if event.victory is True:
        headline = "🏆 <b>Победа!</b>"
    elif event.victory is False:
        headline = "💀 <b>Поражение</b>"
    else:
        headline = "🏁 <b>Матч завершён</b>"

    lines = [headline, ""]
    if event.hero:
        lines.append(f"🧩 <b>Герой:</b> {escape(event.hero)}")
    if event.team:
        label = TEAM_LABELS.get(event.team.lower(), event.team)
        lines.append(f"⚔️ <b>Команда:</b> {escape(label)}")
    duration = _duration(event.duration_seconds)
    if duration:
        lines.append(f"⏱ <b>Длительность:</b> {duration}")
    mode = game_mode_label(event.game_mode)
    if mode:
        lines.append(f"🎮 <b>Режим:</b> {escape(mode)}")
    lobby = lobby_type_label(event.lobby_type)
    if lobby:
        lines.append(f"🏷 <b>Лобби:</b> {escape(lobby)}")
    region = region_label(event.region)
    if region:
        lines.append(f"🌍 <b>Регион:</b> {escape(region)}")
    if event.radiant_score is not None and event.dire_score is not None:
        lines.append(
            f"🔢 <b>Счёт:</b> Свет {event.radiant_score} : {event.dire_score} Тьма"
        )
    if event.kills is not None or event.deaths is not None or event.assists is not None:
        lines.append(
            f"🗡 <b>K/D/A:</b> {event.kills or 0}/{event.deaths or 0}/{event.assists or 0}"
        )
    if event.gpm is not None:
        lines.append(f"💰 <b>GPM:</b> {event.gpm}")
    if event.xpm is not None:
        lines.append(f"✨ <b>XPM:</b> {event.xpm}")
    for team_key, icon in (("radiant", TEAM_ICONS["radiant"]), ("dire", TEAM_ICONS["dire"])):
        team_players = [player for player in event.players if player.team == team_key]
        if not team_players:
            continue
        lines.append("")
        lines.append(f"{icon} <b>{TEAM_LABELS[team_key]}</b>")
        for player in team_players:
            parts: list[str] = []
            if player.name:
                parts.append(escape(player.name))
            if player.hero:
                parts.append(f"<i>{escape(player.hero)}</i>")
            if (
                player.kills is not None
                or player.deaths is not None
                or player.assists is not None
            ):
                parts.append(
                    f"{player.kills or 0}/{player.deaths or 0}/{player.assists or 0}"
                )
            lines.append("• " + (" — ".join(parts) if parts else "—"))
            if player.top_heroes:
                top = ", ".join(
                    f"{escape(name)}×{count}" for name, count in player.top_heroes
                )
                lines.append(f"    ⭐ {top} <i>(50 игр)</i>")
    if event.match_id:
        lines.append("")
        lines.append(f"🆔 <b>Match ID:</b> <code>{escape(str(event.match_id))}</code>")
    return "\n".join(lines)


def format_event(event: MatchEvent) -> str:
    if event.type is EventType.MATCH_FINISHED:
        return format_match_finished(event)
    return format_match_found(event)


def format_test_message() -> str:
    return (
        "✅ <b>Бот работает.</b>\n\n"
        "Как только Dota 2 найдёт матч, я пришлю уведомление сюда."
    )
