from __future__ import annotations

import logging
import time

from ..models import EventType, MatchEvent
from .base import BaseDetector

log = logging.getLogger(__name__)

# Зелёная кнопка «Принять» в HSV (Dota 2: насыщенный зелёный).
GREEN_HSV_LOWER = (35, 80, 80)
GREEN_HSV_UPPER = (90, 255, 255)

# Где на экране искать: accept-диалог появляется в центре экрана.
CENTER_X1_FRAC = 0.20
CENTER_X2_FRAC = 0.80
CENTER_Y1_FRAC = 0.30
CENTER_Y2_FRAC = 0.80

# Пропорции кнопки относительно центрального региона.
BUTTON_MIN_W_FRAC = 0.10
BUTTON_MAX_W_FRAC = 0.90
BUTTON_MIN_H_FRAC = 0.05
BUTTON_MAX_H_FRAC = 0.55
BUTTON_MIN_AREA_FRAC = 0.003  # минимум 0.3% площади региона
BUTTON_MIN_ASPECT = 1.8
BUTTON_MAX_ASPECT = 9.0


def _deps():
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    import pyautogui  # type: ignore

    return cv2, np, pyautogui


def has_accept_button(img_bgr) -> tuple[bool, dict]:
    """Ищет зелёную кнопку «Принять» в центральном регионе скриншота.

    Принимает полный скриншот экрана (OpenCV BGR). Возвращает
    (найдено ли, статистика для диагностики).
    """
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    stats: dict = {"screen_w": 0, "screen_h": 0, "green_px": 0, "candidates": 0}
    h, w = img_bgr.shape[:2]
    stats["screen_w"], stats["screen_h"] = w, h
    if w < 1024 or h < 768:
        return False, stats

    rx1, rx2 = int(w * CENTER_X1_FRAC), int(w * CENTER_X2_FRAC)
    ry1, ry2 = int(h * CENTER_Y1_FRAC), int(h * CENTER_Y2_FRAC)
    region = img_bgr[ry1:ry2, rx1:rx2]
    rh, rw = region.shape[:2]

    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array(GREEN_HSV_LOWER, dtype=np.uint8),
        np.array(GREEN_HSV_UPPER, dtype=np.uint8),
    )
    green_px = int(mask.sum() // 255)
    stats["green_px"] = green_px
    if green_px < 2000:
        return False, stats

    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    min_area = rw * rh * BUTTON_MIN_AREA_FRAC
    for c in cnts:
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        bx, by, bw, bh = cv2.boundingRect(c)
        stats["candidates"] += 1
        if not (rw * BUTTON_MIN_W_FRAC <= bw <= rw * BUTTON_MAX_W_FRAC):
            continue
        if not (rh * BUTTON_MIN_H_FRAC <= bh <= rh * BUTTON_MAX_H_FRAC):
            continue
        aspect = bw / max(bh, 1)
        if not (BUTTON_MIN_ASPECT <= aspect <= BUTTON_MAX_ASPECT):
            continue
        fill = area / max(bw * bh, 1)
        if fill < 0.55:
            continue
        return True, stats
    return False, stats


class AcceptConfirmationStateMachine:
    """Подтверждает сигнал кнопки «Принять» серией стабильных опросов.

    Одиночный ложный срабатывание анализа экрана не должно отправлять
    уведомление: кнопка должна оставаться на экране `confirmations` опросов
    подряд. Повторное подтверждение в пределах cooldown подавляется.
    """

    def __init__(self, confirmations: int = 3, cooldown_seconds: float = 90.0):
        self._confirmations = max(1, confirmations)
        self._cooldown_seconds = cooldown_seconds
        self._positive_count = 0
        self._last_emit_at: float | None = None

    def observe(self, has_accept: bool, now: float) -> bool:
        if not has_accept:
            self._positive_count = 0
            return False
        self._positive_count += 1
        if self._positive_count < self._confirmations:
            return False
        if (
            self._last_emit_at is not None
            and now - self._last_emit_at < self._cooldown_seconds
        ):
            return False
        self._last_emit_at = now
        return True

    def reset(self) -> None:
        self._positive_count = 0


class AcceptUiDetector(BaseDetector):
    """Детектирует экран принятия матча по скриншоту центра экрана.

    Dota 2 (SDL) не отдаёт интерфейс через UI Automation, поэтому детекция
    идёт по изображению: зелёная кнопка «Принять» всегда появляется в центре
    экрана, её форма и цвет узнаются по цветовой маске. Привязки к окну,
    PID или фокусу нет — детектор смотрит на экран как человек.
    """

    name = "accept_button"

    def __init__(
        self,
        sink,
        poll_interval_ms: int = 250,
        cooldown_seconds: float = 90.0,
        required_confirmations: int = 3,
    ):
        super().__init__(sink)
        self._poll_interval = max(poll_interval_ms, 150) / 1000
        self._confirmation = AcceptConfirmationStateMachine(
            confirmations=required_confirmations,
            cooldown_seconds=cooldown_seconds,
        )

    def run(self) -> None:
        try:
            cv2, np, pyautogui = _deps()
        except Exception as exc:
            log.error(
                "accept_button: не хватает зависимостей (%s). "
                "Выполни: .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt",
                exc,
            )
            return
        log.info("Детектор кнопки «Принять» запущен: анализ экрана каждые %.0f мс", self._poll_interval * 1000)
        last_heartbeat = 0.0
        while not self.stopping:
            try:
                screenshot = pyautogui.screenshot()
                img = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
                has_accept, stats = has_accept_button(img)
                now = time.time()
                if now - last_heartbeat >= 10:
                    last_heartbeat = now
                    log.info(
                        "accept_button: экран %dx%d, зелёных пикселей в центре: %d, кандидатов: %d",
                        stats["screen_w"], stats["screen_h"],
                        stats["green_px"], stats["candidates"],
                    )
                if self._confirmation.observe(has_accept, now):
                    log.info("Обнаружена кнопка «Принять» — матч найден")
                    self.emit(
                        MatchEvent(
                            type=EventType.MATCH_FOUND,
                            source=self.name,
                            detected_at=now,
                        )
                    )
            except Exception:
                log.debug("Ошибка accept_button", exc_info=True)
            time.sleep(self._poll_interval)
