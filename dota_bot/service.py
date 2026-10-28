from __future__ import annotations

import logging
import queue
import sys
import time
from dataclasses import replace
from typing import Any

from .config import BotConfig
from .detectors.base import BaseDetector
from .detectors.gsi import GsiDetector
from .detectors.opendota import OpenDotaDetector
from .detectors.steam import SteamDetector
from .detectors.acceptwin import AcceptUiDetector
from .formatting import accept_keyboard, format_event, format_match_found
from .heroes import HeroCatalog
from .models import EventType, MatchEvent
from .state import StateStore
from .telegram_client import TelegramClient

log = logging.getLogger(__name__)

ENRICH_WINDOW_SECONDS = 900
FINISHED_DEDUPE_SECONDS = 21600
# Короткое in-memory окно только для не-fresh повторов внутри одного эпизода
# кнопки. Свежий эпизод (окно «Принять» исчезло и появилось новое) всегда
# уведомляет — иначе отменённый и мгновенно перезапущенный поиск терялся.
FOUND_REPEAT_WINDOW_SECONDS = 60
# Минимальный разрыв между уведомлением и повторным эпизодом кнопки,
# чтобы считать его новым попом после отмены (а не мерцанием анимации).
REPOLL_NOTIFY_MIN_GAP_SECONDS = 5.0

# Уведомление «матч найден» отправляется только при обнаружении реальной
# кнопки «Принять» через UI Automation. Сигналы вроде «Dota вышла на
# передний план» сюда не входят — они порождали ложные уведомления при
# каждом переключении окон.
MATCH_FOUND_SOURCES = frozenset({"accept_button"})


class MatchService:
    """Связывает детекторы, дедупликацию и отправку уведомлений."""

    def __init__(self, config: BotConfig, notifier: TelegramClient | None, state: StateStore):
        self.config = config
        self.notifier = notifier
        self.state = state
        self._queue: queue.Queue[MatchEvent] = queue.Queue()
        self._detectors: list[BaseDetector] = []
        self._heroes = HeroCatalog(config.state_path.parent / "cache" / "heroes.json")
        self._last_found_message: dict[str, Any] | None = None
        self._finished_messages: dict[str, dict[str, Any]] = {}
        self._started_at = time.time()
        self._in_match = False
        self._match_started_at: float | None = None
        self._last_found_at: float | None = None
        self._found_match_id: str | None = None
        # True после отправки «матч найден», пока непонятно, состоится ли
        # матч. Если кнопка появляется снова, а в матч мы не вошли — это
        # повторный поп после отмены: уведомляем в любом случае.
        self._accept_pending = False

    # ── детекторы ──
    def build_detectors(self) -> None:
        cfg = self.config
        if sys.platform == "win32":
            try:
                self._detectors.append(
                    AcceptUiDetector(
                        self._queue,
                        poll_interval_ms=250,
                        required_confirmations=2,
                        process_names=[cfg.remote_control.process_name],
                    )
                )
            except Exception:
                log.debug("Не удалось добавить acceptwin детектор", exc_info=True)
        if cfg.gsi.enabled:
            self._detectors.append(
                GsiDetector(
                    self._queue,
                    host=cfg.gsi.host,
                    port=cfg.gsi.port,
                    account_id=cfg.resolved_account_id,
                )
            )
        if cfg.steam.enabled and cfg.resolved_steamid64:
            self._detectors.append(
                SteamDetector(
                    self._queue,
                    api_key=cfg.steam.api_key,
                    steamid64=cfg.resolved_steamid64,
                    base_url=cfg.steam.base_url,
                    poll_interval_seconds=cfg.steam.poll_interval_seconds,
                )
            )
        if cfg.opendota.enabled and cfg.resolved_account_id:
            self._detectors.append(
                OpenDotaDetector(
                    self._queue,
                    account_id=cfg.resolved_account_id,
                    base_url=cfg.opendota.base_url,
                    poll_interval_seconds=cfg.opendota.poll_interval_seconds,
                    hero_catalog=self._heroes,
                )
            )

    def start(self) -> None:
        self.build_detectors()
        for detector in self._detectors:
            detector.start()
        log.info("Активных детекторов: %d", len(self._detectors))

    def stop(self) -> None:
        for detector in self._detectors:
            try:
                detector.stop()
            except Exception:
                log.exception("Ошибка остановки детектора %s", detector.name)
        self._detectors = []

    # ── обработка событий ──
    def _notifications_enabled(self) -> bool:
        return self.state.get_flag("notifications_enabled", True)

    def _send(self, text: str, reply_markup: dict | None = None) -> int | None:
        if self.notifier is None:
            log.info("[DRY-RUN] %s", text.replace("\n", " | "))
            return None
        try:
            response = self.notifier.send_message(
                self.config.telegram.chat_id, text, reply_markup=reply_markup
            )
        except Exception as exc:
            log.error("Не удалось отправить уведомление: %s", exc)
            return None
        message_id = (response.get("result") or {}).get("message_id")
        self.state.bump("notifications_sent")
        return message_id if isinstance(message_id, int) else None

    def _handle_match_found(self, event: MatchEvent) -> None:
        if self._in_match:
            log.debug("Пропущено уведомление о найденном матче: уже в матче")
            return
        now = time.time()
        if self._match_started_at and now - self._match_started_at < 300:
            log.debug("Пропущено: матч был недавно")
            return
        fresh = bool(event.extra.get("fresh"))
        # Порог против мерцания: повторный эпизод в первые пару секунд после
        # уведомления — это анимация затухания кнопки, а не новый матч.
        repop_after_decline = (
            self._accept_pending
            and self._last_found_at is not None
            and now - self._last_found_at >= REPOLL_NOTIFY_MIN_GAP_SECONDS
        )
        if repop_after_decline:
            # Прошлый матч не состоялся (кто-то не принял), а кнопка
            # появилась снова — новый поп, уведомляем в любом случае,
            # независимо от окна дедупликации.
            log.info("Кнопка «Принять» снова — прошлый матч не состоялся, уведомляем повторно")
        elif fresh:
            # Кнопка исчезла и появилась заново (отмена матча + новый поиск):
            # это новый матч — уведомляем всегда, окно дедупликации неуместно.
            log.info("Новый эпизод кнопки «Принять» — уведомляем без окна дедупликации")
        elif self._last_found_at and now - self._last_found_at < FOUND_REPEAT_WINDOW_SECONDS:
            log.info("Пропущено дублирующее уведомление о найденном матче")
            return
        self.state.bump("matches_found")
        remote = self.config.remote_control.enabled
        message_id = self._send(
            format_match_found(event, remote_accept=remote),
            accept_keyboard() if remote else None,
        )
        self._last_found_message = {
            "message_id": message_id,
            "sent_at": time.time(),
            "event": event,
        }
        self._last_found_at = now
        self._found_match_id = event.match_id
        self._accept_pending = True
        self.state.set_last_event(
            {"type": event.type.value, "source": event.source, "at": event.detected_at}
        )
        self.state.save()

    def _handle_match_started(self, event: MatchEvent) -> None:
        # own=False — просмотр чужого матча: уведомления не подавляем,
        # сообщение о принятии не трогаем.
        own = bool(event.extra.get("own", True))
        self._accept_pending = False
        if own:
            self._in_match = True
            self._match_started_at = time.time()
        self._last_found_at = None
        self._found_match_id = None
        self.state.clear("match_found")
        if not own:
            return
        if not self._last_found_message or not self._last_found_message.get("message_id"):
            return
        enriched = event
        if event.hero_id is not None:
            hero_name = self._heroes.name(event.hero_id)
            if hero_name:
                enriched = replace(enriched, hero=hero_name)
        elif event.hero and event.hero.startswith("npc_dota_hero_"):
            enriched = replace(enriched, hero=None)
        last = self._last_found_message
        remote = self.config.remote_control.enabled
        if last and last.get("message_id") and time.time() - float(last["sent_at"]) < ENRICH_WINDOW_SECONDS:
            text = format_match_found(enriched, remote_accept=remote)
            if not text.strip():
                try:
                    if remote:
                        self.notifier.edit_message_reply_markup(
                            self.config.telegram.chat_id, int(last["message_id"])
                        )
                    else:
                        self.notifier.edit_message(
                            self.config.telegram.chat_id, int(last["message_id"]), "✅ Матч принят"
                        )
                except Exception:
                    pass
                return
            if self.notifier is not None:
                try:
                    ok = self.notifier.edit_message(
                        self.config.telegram.chat_id, int(last["message_id"]), text
                    )
                except Exception as exc:
                    log.warning("Не удалось обновить сообщение: %s", exc)
                    ok = False
                if ok:
                    log.info("Уведомление о матче дополнено данными из GSI")
                    last["event"] = enriched
                    if remote:
                        try:
                            self.notifier.edit_message_reply_markup(
                                self.config.telegram.chat_id, int(last["message_id"])
                            )
                        except Exception as exc:
                            log.debug("Не удалось убрать кнопку принятия: %s", exc)
                    self.state.save()
                    return
            else:
                log.info("[DRY-RUN] дополненное сообщение: %s", text.replace("\n", " | "))
                return
        return

    def _handle_match_finished(self, event: MatchEvent) -> None:
        own = bool(event.extra.get("own", True))
        self._accept_pending = False
        if own:
            self._in_match = False
            self._match_started_at = None
        self._last_found_at = None
        self._found_match_id = None
        self.state.clear("match_found")
        text = format_event(event)
        match_key = event.match_id or event.source
        existing = self._finished_messages.get(match_key)
        if existing and time.time() - float(existing["sent_at"]) < FINISHED_DEDUPE_SECONDS:
            if len(text) <= len(str(existing["text"])) or not existing.get("message_id"):
                return
            updated = False
            if self.notifier is not None:
                try:
                    updated = self.notifier.edit_message(
                        self.config.telegram.chat_id, int(existing["message_id"]), text
                    )
                except Exception as exc:
                    log.warning("Не удалось дополнить сообщение о матче: %s", exc)
            if updated:
                log.info("Сообщение о матче дополнено составами")
                existing["text"] = text
                existing["sent_at"] = time.time()
                self.state.set_last_event(
                    {"type": event.type.value, "source": event.source, "at": event.detected_at}
                )
                self.state.save()
            return

        key = f"match_finished:{match_key}"
        if not self.state.should_notify(key, FINISHED_DEDUPE_SECONDS):
            return
        self.state.mark(key)
        self.state.bump("matches_finished")
        message_id = self._send(text)
        self._finished_messages[match_key] = {
            "message_id": message_id,
            "sent_at": time.time(),
            "text": text,
        }
        cutoff = time.time() - FINISHED_DEDUPE_SECONDS
        self._finished_messages = {
            k: v for k, v in self._finished_messages.items() if float(v["sent_at"]) >= cutoff
        }
        self.state.set_last_event(
            {"type": event.type.value, "source": event.source, "at": event.detected_at}
        )
        self.state.save()

    def handle_event(self, event: MatchEvent) -> None:
        log.info("Событие %s от детектора %s", event.type.value, event.source)
        if not self._notifications_enabled():
            log.info("Уведомления выключены — событие проигнорировано")
            return
        if event.type is EventType.MATCH_FOUND:
            if event.source not in MATCH_FOUND_SOURCES:
                log.debug("MATCH_FOUND от %s проигнорирован: только по кнопке «Принять»", event.source)
                return
            if not self._in_match and (not self._match_started_at or time.time() - self._match_started_at > 300):
                if self.config.telegram.notify_match_found:
                    self._handle_match_found(event)
            else:
                log.debug("MATCH_FOUND игнорирован")
            return
        elif event.type is EventType.MATCH_STARTED:
            if self.config.telegram.notify_match_found:
                self._handle_match_started(event)
        elif event.type is EventType.MATCH_FINISHED:
            self._in_match = False
            self._match_started_at = time.time()
            self._last_found_at = None
            self._found_match_id = None
            self.state.clear("match_found")
            if self.config.telegram.notify_match_finished:
                self._handle_match_finished(event)

    # ── цикл ──
    def run_forever(self) -> None:
        while True:
            try:
                event = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self.handle_event(event)
            except Exception:
                log.exception("Ошибка обработки события")

    def status(self) -> dict[str, Any]:
        last_event = self.state.last_event
        since = None
        if last_event and last_event.get("at"):
            since = int(time.time() - float(last_event["at"]))
        return {
            "uptime_seconds": int(time.time() - self._started_at),
            "detectors": [detector.name for detector in self._detectors],
            "notifications_enabled": self._notifications_enabled(),
            "last_event": last_event,
            "last_event_age_seconds": since,
            "matches_found": self.state.get_stat("matches_found"),
            "matches_finished": self.state.get_stat("matches_finished"),
            "notifications_sent": self.state.get_stat("notifications_sent"),
        }
