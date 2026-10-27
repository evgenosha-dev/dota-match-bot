from __future__ import annotations

import logging
import time

from ..input_control import is_process_running
from ..models import EventType, MatchEvent
from .base import BaseDetector

log = logging.getLogger(__name__)

# Зелёная кнопка «Принять» в HSV. Реальная кнопка в Dota 2 — тёмный
# зелёный с градиентом: края уходят в S~50/V~45, поэтому пороги мягкие.
GREEN_HSV_LOWER = (35, 50, 45)
GREEN_HSV_UPPER = (90, 255, 255)
BUTTON_MIN_FILL = 0.40

# Где на экране искать: accept-диалог появляется в центре экрана.
CENTER_X1_FRAC = 0.20
CENTER_X2_FRAC = 0.80
CENTER_Y1_FRAC = 0.30
CENTER_Y2_FRAC = 0.80

# Пропорции кнопки относительно центрального региона.
# Значения выверены по реальным отладочным кадрам: настоящая кнопка
# «Принять» — это плотный (fill ~0.95) широкий (aspect ~5) прямоугольник
# в центре региона. Ложные кандидаты (зелёные полосы интерфейса меню,
# элементы других игр) либо прижаты к краю региона, либо редкие по fill
# (≤0.83), либо вытянутые вертикально (aspect < 3).
BUTTON_MIN_W_FRAC = 0.15
BUTTON_MAX_W_FRAC = 0.90
BUTTON_MIN_H_FRAC = 0.05
BUTTON_MAX_H_FRAC = 0.55
BUTTON_MIN_AREA_FRAC = 0.003  # минимум 0.3% площади региона
BUTTON_MIN_ASPECT = 3.0
BUTTON_MAX_ASPECT = 9.0
BUTTON_MIN_FILL = 0.70
# Кандидат, касающийся края региона, — артефакт захвата (полоса по краю
# экрана), а не кнопка: настоящая кнопка всегда отступает от краёв.
BUTTON_EDGE_MARGIN_FRAC = 0.02


def _deps():
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    import pyautogui  # type: ignore

    return cv2, np, pyautogui


def _project_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


def _save_debug_frame(debug_dir, img_bgr) -> None:
    try:
        import cv2  # type: ignore

        debug_dir.mkdir(parents=True, exist_ok=True)
        # Оставляем только последние 10 кадров.
        frames = sorted(debug_dir.glob("accept_*.jpg"))
        for stale in frames[:-9]:
            stale.unlink(missing_ok=True)
        name = time.strftime("accept_%Y%m%d_%H%M%S.jpg")
        cv2.imwrite(str(debug_dir / name), img_bgr)
    except Exception:
        log.debug("Не удалось сохранить отладочный кадр", exc_info=True)


def has_accept_button(img_bgr) -> tuple[bool, dict]:
    """Ищет зелёную кнопку «Принять» в центральном регионе скриншота.

    Принимает полный скриншот экрана (OpenCV BGR). Возвращает
    (найдено ли, статистика для диагностики).
    """
    h, w = img_bgr.shape[:2]
    stats: dict = {
        "screen_w": w,
        "screen_h": h,
        "green_px": 0,
        "candidates": 0,
        "candidate_details": [],
    }
    if w < 1024 or h < 768:
        return False, stats

    rx1, rx2 = int(w * CENTER_X1_FRAC), int(w * CENTER_X2_FRAC)
    ry1, ry2 = int(h * CENTER_Y1_FRAC), int(h * CENTER_Y2_FRAC)
    found, region_stats = has_accept_button_in_region(img_bgr[ry1:ry2, rx1:rx2])
    stats.update(region_stats)
    return found, stats


def has_accept_button_in_region(region) -> tuple[bool, dict]:
    """Анализирует уже вырезанный центральный регион экрана (OpenCV BGR).

    Рабочая лошадка детектора: полный скриншот не нужен, основное время
    уходит на захват, поэтому в цикле опроса снимается только регион.
    Возвращает (найдено ли, статистика для диагностики).
    """
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    stats: dict = {
        "green_px": 0,
        "candidates": 0,
        "candidate_details": [],
    }
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
    margin_x = rw * BUTTON_EDGE_MARGIN_FRAC
    margin_y = rh * BUTTON_EDGE_MARGIN_FRAC
    for c in cnts:
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        bx, by, bw, bh = cv2.boundingRect(c)
        stats["candidates"] += 1
        if bx < margin_x or by < margin_y:
            continue
        if bx + bw > rw - margin_x or by + bh > rh - margin_y:
            continue
        detail = {
            "w": int(bw),
            "h": int(bh),
            "wf": round(bw / rw, 3),
            "hf": round(bh / rh, 3),
            "aspect": round(bw / max(bh, 1), 2),
            "fill": round(area / max(bw * bh, 1), 3),
        }
        stats["candidate_details"].append(detail)
        if not (rw * BUTTON_MIN_W_FRAC <= bw <= rw * BUTTON_MAX_W_FRAC):
            continue
        if not (rh * BUTTON_MIN_H_FRAC <= bh <= rh * BUTTON_MAX_H_FRAC):
            continue
        aspect = bw / max(bh, 1)
        if not (BUTTON_MIN_ASPECT <= aspect <= BUTTON_MAX_ASPECT):
            continue
        # Плотность зелёных пикселей внутри bounding-rect. contourArea для
        # полых контуров завышает fill, поэтому считаем прямо по маске:
        # настоящая кнопка плотная (fill ~0.95), «рамки» и панели — нет.
        fill_px = int(mask[by : by + bh, bx : bx + bw].sum()) // 255
        if fill_px / max(bw * bh, 1) < BUTTON_MIN_FILL:
            continue
        return True, stats
    return False, stats


class AcceptConfirmationStateMachine:
    """Подтверждает сигнал кнопки «Принять» серией стабильных опросов.

    Одиночное ложное срабатывание анализа экрана не должно отправлять
    уведомление: кнопка должна оставаться на экране `confirmations` опросов
    подряд. Кратковременные мерцания (анимация затухания кнопки) не завершают
    эпизод — для этого нужно `absence_polls` подряд опросов без кнопки.
    Событие эмитится один раз за эпизод; повторно — только после того, как
    кнопка реально исчезла и появилась заново (отмена матча + новый поиск).
    """

    def __init__(self, confirmations: int = 3, absence_polls: int = 4):
        self._confirmations = max(1, confirmations)
        self._absence_polls = max(1, absence_polls)
        self._positive_count = 0
        self._absent_count = 0
        self._emitted_for_episode = False
        self._episode_absent_since: float | None = None

    def observe(self, has_accept: bool, now: float) -> tuple[bool, bool]:
        """Обрабатывает один опрос. Возвращает (emit, fresh).

        emit — серия подтверждена и это первое срабатывание за эпизод.
        fresh — перед появлением кнопки она отсутствовала достаточно долго,
        чтобы считать событие новым матчем (а не мерцанием того же диалога).
        """
        if not has_accept:
            self._absent_count += 1
            self._positive_count = 0
            if self._absent_count >= self._absence_polls:
                self._emitted_for_episode = False
                self._episode_absent_since = now
            return False, False
        self._absent_count = 0
        self._positive_count += 1
        if self._positive_count < self._confirmations or self._emitted_for_episode:
            return False, False
        self._emitted_for_episode = True
        fresh = (
            self._episode_absent_since is None
            or now - self._episode_absent_since >= 2.0
        )
        return True, fresh

    def reset(self) -> None:
        self._positive_count = 0
        self._absent_count = 0
        self._emitted_for_episode = False
        self._episode_absent_since = None


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
        required_confirmations: int = 2,
        process_names: list[str] | None = None,
    ):
        super().__init__(sink)
        self._poll_interval = max(poll_interval_ms, 150) / 1000
        self._confirmation = AcceptConfirmationStateMachine(
            confirmations=required_confirmations,
        )
        # Ворота: анализируем экран, только когда Dota 2 запущена. Экран
        # другой игры тоже может содержать похожую зелёную кнопку — без
        # этой проверки такая кнопка выглядела бы как «матч найден».
        self._process_names = [n for n in (process_names or []) if n.strip()]
        self._last_gate_logged: bool | None = None

    def _dota_running(self) -> bool:
        if not self._process_names:
            return True
        for name in self._process_names:
            running = is_process_running(name)
            if running:
                return True
            if running is None:
                # Платформа без поддержки Win32 — не блокируем анализ.
                return True
        return False

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
        log.info(
            "Детектор кнопки «Принять» запущен: анализ экрана каждые %.0f мс, "
            "ворота по процессу: %s",
            self._poll_interval * 1000,
            ", ".join(self._process_names) if self._process_names else "выключены",
        )
        last_heartbeat = 0.0
        last_debug_save = 0.0
        debug_dir = _project_root() / "logs" / "debug"
        while not self.stopping:
            try:
                if not self._dota_running():
                    # Dota не запущена: сбрасываем серию подтверждений,
                    # чтобы накопленные «кнопки» другой игры не всплыли
                    # потом при запуске Dota, и ничего не эмитим.
                    self._confirmation.observe(False, time.time())
                    if self._last_gate_logged is not False:
                        self._last_gate_logged = False
                        log.info("Dota 2 не запущена — анализ экрана приостановлен")
                    time.sleep(self._poll_interval)
                    continue
                if self._last_gate_logged is False:
                    self._last_gate_logged = True
                    log.info("Dota 2 запущена — анализ экрана возобновлён")
                screen_w, screen_h = pyautogui.size()
                rx = int(screen_w * CENTER_X1_FRAC)
                ry = int(screen_h * CENTER_Y1_FRAC)
                rw = int(screen_w * (CENTER_X2_FRAC - CENTER_X1_FRAC))
                rh = int(screen_h * (CENTER_Y2_FRAC - CENTER_Y1_FRAC))
                # Захватываем только центральный регион: полный скриншот
                # занимает ~100 мс, регион — в разы меньше.
                screenshot = pyautogui.screenshot(region=(rx, ry, rw, rh))
                img = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
                has_accept, stats = has_accept_button_in_region(img)
                stats["screen_w"], stats["screen_h"] = screen_w, screen_h
                now = time.time()
                if now - last_heartbeat >= 10:
                    last_heartbeat = now
                    details = "; ".join(
                        "w={w} h={h} wf={wf} hf={hf} asp={aspect} fill={fill}".format(**d)
                        for d in stats["candidate_details"][:3]
                    )
                    log.info(
                        "accept_button: экран %dx%d, зелёных пикселей в центре: %d, кандидатов: %d%s",
                        stats["screen_w"], stats["screen_h"],
                        stats["green_px"], stats["candidates"],
                        f" ({details})" if details else "",
                    )
                if has_accept and now - last_debug_save >= 30:
                    # Сохраняем то, что реально видит детектор, — по этим
                    # кадрам можно точно подкрутить пороги.
                    last_debug_save = now
                    _save_debug_frame(debug_dir, img)
                emit, fresh = self._confirmation.observe(has_accept, now)
                if emit:
                    log.info(
                        "Обнаружена кнопка «Принять» — матч найден%s",
                        " (новый эпизод)" if fresh else " (тот же эпизод)",
                    )
                    self.emit(
                        MatchEvent(
                            type=EventType.MATCH_FOUND,
                            source=self.name,
                            detected_at=now,
                            extra={"fresh": fresh},
                        )
                    )
            except Exception:
                log.debug("Ошибка accept_button", exc_info=True)
            time.sleep(self._poll_interval)
