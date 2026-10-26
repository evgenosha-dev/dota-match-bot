from dota_bot.detectors.acceptwin import AcceptConfirmationStateMachine


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
