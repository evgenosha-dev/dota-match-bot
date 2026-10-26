import pytest

from dota_bot.detectors.acceptwin import (
    AcceptConfirmationStateMachine,
    has_accept_button,
)

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


def _window_with_green_button(
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
    assert has_accept_button(_window_with_green_button()) is True


def test_no_button_no_detection():
    img = np.full((1080, 1920, 3), 30, np.uint8)
    assert has_accept_button(img) is False


def test_small_window_rejected():
    img = np.full((300, 400, 3), 30, np.uint8)
    cv2.rectangle(img, (130, 130), (270, 170), (60, 180, 60), -1)
    assert has_accept_button(img) is False


def test_single_observation_does_not_emit():
    machine = AcceptConfirmationStateMachine(confirmations=3, cooldown_seconds=90.0)

    assert machine.observe(True, now=0.0) is False


def test_false_resets_confirmation_series():
    machine = AcceptConfirmationStateMachine(confirmations=3, cooldown_seconds=90.0)

    assert machine.observe(True, now=0.0) is False
    assert machine.observe(True, now=0.25) is False
    assert machine.observe(False, now=0.5) is False
    # Серия сброшена: нужны три новых подряд.
    assert machine.observe(True, now=0.75) is False
    assert machine.observe(True, now=1.0) is False
    assert machine.observe(True, now=1.25) is True


def test_three_stable_observations_emit_once():
    machine = AcceptConfirmationStateMachine(confirmations=3, cooldown_seconds=90.0)

    assert machine.observe(True, now=0.0) is False
    assert machine.observe(True, now=0.25) is False
    assert machine.observe(True, now=0.5) is True
    # Дальше — в пределах cooldown, не должно быть повторного события.
    assert machine.observe(True, now=0.75) is False
    assert machine.observe(True, now=1.0) is False


def test_repeat_after_cooldown_emits_again():
    machine = AcceptConfirmationStateMachine(confirmations=3, cooldown_seconds=90.0)

    assert machine.observe(True, now=0.0) is False
    assert machine.observe(True, now=0.25) is False
    assert machine.observe(True, now=0.5) is True
    assert machine.observe(True, now=100.0) is True


def test_confirmations_less_than_one_behaves_as_one():
    machine = AcceptConfirmationStateMachine(confirmations=0, cooldown_seconds=90.0)

    assert machine.observe(True, now=0.0) is True


def test_reset_clears_series():
    machine = AcceptConfirmationStateMachine(confirmations=3, cooldown_seconds=90.0)

    machine.observe(True, now=0.0)
    machine.observe(True, now=0.25)
    machine.reset()
    assert machine.observe(True, now=0.5) is False
