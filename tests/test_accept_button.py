import pytest

from dota_bot.detectors.acceptwin import (
    AcceptConfirmationStateMachine,
    has_accept_button,
    has_accept_button_in_region,
)

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


def _screen_with_green_button(
    width: int = 1920, height: int = 1080, button_w: int = 400, button_h: int = 110
):
    img = np.full((height, width, 3), 30, np.uint8)
    x1 = (width - button_w) // 2
    y1 = (height - button_h) // 2
    cv2.rectangle(
        img, (x1, y1), (x1 + button_w, y1 + button_h), (60, 180, 60), -1
    )
    return img


def test_detects_green_accept_button():
    found, _ = has_accept_button(_screen_with_green_button())
    assert found is True


def test_detects_real_dark_green_accept_button():
    # Реальная кнопка «Принять» из Dota 2: тёмно-зелёная (RGB ~71/100/88),
    # широкая (~4.5:1). Регрессионный тест по скриншоту пользователя.
    img = np.full((1080, 1920, 3), 22, np.uint8)
    bw, bh = 620, 140
    x1, y1 = (1920 - bw) // 2, (1080 - bh) // 2 - 40
    cv2.rectangle(img, (x1, y1), (x1 + bw, y1 + bh), (85, 100, 60), -1)
    found, _ = has_accept_button(img)
    assert found is True


def test_no_button_no_detection():
    img = np.full((1080, 1920, 3), 30, np.uint8)
    found, stats = has_accept_button(img)
    assert found is False
    assert stats["green_px"] == 0


def test_button_outside_center_region_ignored():
    # Кнопка у края экрана (миникарта и пр.) не должна детектиться.
    img = np.full((1080, 1920, 3), 30, np.uint8)
    cv2.rectangle(img, (30, 900), (300, 1000), (60, 180, 60), -1)
    found, _ = has_accept_button(img)
    assert found is False


def test_small_screen_rejected():
    img = np.full((400, 640, 3), 30, np.uint8)
    found, _ = has_accept_button(img)
    assert found is False


def test_region_analysis_matches_full_screen():
    # Детектор в цикле опроса анализирует только вырезанный регион —
    # результат должен совпадать с полным скриншотом.
    img = _screen_with_green_button()
    found_full, _ = has_accept_button(img)
    h, w = img.shape[:2]
    region = img[int(h * 0.30):int(h * 0.80), int(w * 0.20):int(w * 0.80)]
    found_region, _ = has_accept_button_in_region(region)
    assert found_full is True
    assert found_region is True


def _region_canvas(width: int = 1152, height: int = 540):
    return np.full((height, width, 3), 22, np.uint8)


def test_edge_strip_is_rejected():
    # Зелёная полоса по краю региона (артефакты интерфейса меню и других
    # игр) не должна считаться кнопкой: настоящая кнопка всегда в центре.
    region = _region_canvas()
    cv2.rectangle(region, (0, 0), (1151, 42), (60, 180, 60), -1)  # полоса сверху
    found, _ = has_accept_button_in_region(region)
    assert found is False

    region = _region_canvas()
    cv2.rectangle(region, (0, 498), (1151, 539), (60, 180, 60), -1)  # снизу
    found, _ = has_accept_button_in_region(region)
    assert found is False


def test_hollow_wide_blob_is_rejected():
    # Широкий, но «пустой» внутри контур (fill низкий) — не кнопка:
    # настоящая кнопка плотная, fill ~0.95.
    region = _region_canvas()
    cv2.rectangle(region, (300, 200), (620, 264), (60, 180, 60), -1)
    cv2.rectangle(region, (316, 208), (604, 256), (22, 22, 22), -1)
    found, _ = has_accept_button_in_region(region)
    assert found is False


def test_low_aspect_blob_is_rejected():
    # Широкий прямоугольник с аспектом < 3 (например, панель меню)
    # не должен проходить: у кнопки «Принять» аспект ~5.
    region = _region_canvas()
    cv2.rectangle(region, (400, 200), (700, 360), (60, 180, 60), -1)
    found, _ = has_accept_button_in_region(region)
    assert found is False


def test_single_observation_does_not_emit():
    machine = AcceptConfirmationStateMachine(confirmations=3)

    assert machine.observe(True, now=0.0) == (False, False)


def test_false_resets_confirmation_series():
    machine = AcceptConfirmationStateMachine(confirmations=3)

    assert machine.observe(True, now=0.0) == (False, False)
    assert machine.observe(True, now=0.25) == (False, False)
    assert machine.observe(False, now=0.5) == (False, False)
    # Серия сброшена: нужны три новых подряд.
    assert machine.observe(True, now=0.75) == (False, False)
    assert machine.observe(True, now=1.0) == (False, False)
    assert machine.observe(True, now=1.25)[0] is True


def test_stable_observations_emit_once_per_episode():
    machine = AcceptConfirmationStateMachine(confirmations=3)

    assert machine.observe(True, now=0.0) == (False, False)
    assert machine.observe(True, now=0.25) == (False, False)
    emit, fresh = machine.observe(True, now=0.5)
    assert emit is True and fresh is True  # первый эпизод всегда свежий
    # Кнопка никуда не делась — повторной эмиссии быть не должно.
    assert machine.observe(True, now=0.75) == (False, False)
    assert machine.observe(True, now=1.0) == (False, False)


def test_repeat_after_absence_emits_fresh_episode():
    machine = AcceptConfirmationStateMachine(confirmations=3, absence_polls=4)

    machine.observe(True, now=0.0)
    machine.observe(True, now=0.25)
    assert machine.observe(True, now=0.5)[0] is True
    # Эпизод завершён: кнопка исчезла на 4 опроса подряд.
    for i in range(4):
        machine.observe(False, now=1.0 + i * 0.25)
    # Поиск заново — новая кнопка.
    assert machine.observe(True, now=4.0) == (False, False)
    assert machine.observe(True, now=4.25) == (False, False)
    emit, fresh = machine.observe(True, now=4.5)
    assert emit is True
    assert fresh is True  # отсутствовала больше 2 с — это новый матч


def test_short_absence_is_not_a_new_episode():
    machine = AcceptConfirmationStateMachine(confirmations=2, absence_polls=4)

    machine.observe(True, now=0.0)
    assert machine.observe(True, now=0.25)[0] is True
    # Отсутствие подтверждено, но кнопка вернулась почти сразу (<2 с).
    for i in range(4):
        machine.observe(False, now=0.5 + i * 0.25)
    assert machine.observe(True, now=1.5) == (False, False)
    emit, fresh = machine.observe(True, now=1.75)
    # Повторная эмиссия возможна, но сервис должен считать её не-fresh.
    assert emit is True
    assert fresh is False


def test_brief_flicker_does_not_end_episode():
    # Мерцание из-за анимации затухания (1-3 опроса без кнопки) не должно
    # позволить детектору эмитить повторно в пределах того же эпизода.
    machine = AcceptConfirmationStateMachine(confirmations=2, absence_polls=4)

    machine.observe(True, now=0.0)
    assert machine.observe(True, now=0.25)[0] is True
    machine.observe(False, now=0.5)
    machine.observe(False, now=0.75)
    machine.observe(True, now=1.0)
    assert machine.observe(True, now=1.25) == (False, False)


def test_confirmations_less_than_one_behaves_as_one():
    machine = AcceptConfirmationStateMachine(confirmations=0)

    assert machine.observe(True, now=0.0) == (True, True)


def test_reset_clears_series():
    machine = AcceptConfirmationStateMachine(confirmations=3)

    machine.observe(True, now=0.0)
    machine.observe(True, now=0.25)
    machine.reset()
    assert machine.observe(True, now=0.5) == (False, False)
