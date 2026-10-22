from dota_bot.detectors.foreground import ForegroundStateMachine


def make_machine(**kwargs) -> ForegroundStateMachine:
    params = {
        "targets": ["dota2.exe"],
        "confirmation_delay_ms": 500,
        "cooldown_seconds": 60,
    }
    params.update(kwargs)
    return ForegroundStateMachine(**params)


def test_transition_from_other_process_triggers():
    machine = make_machine()
    now = 1000.0

    assert machine.observe("chrome.exe", now) is False
    assert machine.observe("dota2.exe", now + 0.1) is False
    assert machine.observe("dota2.exe", now + 0.7) is True


def test_no_trigger_without_leaving_dota():
    machine = make_machine()
    now = 1000.0

    assert machine.observe("dota2.exe", now) is False
    assert machine.observe("dota2.exe", now + 1.0) is False
    assert machine.observe("dota2.exe", now + 2.0) is False


def test_brief_flicker_does_not_trigger():
    machine = make_machine()
    now = 1000.0

    machine.observe("chrome.exe", now)
    machine.observe("dota2.exe", now + 0.1)
    assert machine.observe("chrome.exe", now + 0.2) is False
    assert machine.observe("dota2.exe", now + 0.3) is False
    assert machine.observe("dota2.exe", now + 0.9) is True


def test_cooldown_blocks_repeated_alerts():
    machine = make_machine()
    now = 1000.0

    machine.observe("chrome.exe", now)
    machine.observe("dota2.exe", now + 0.1)
    assert machine.observe("dota2.exe", now + 0.7) is True

    machine.observe("chrome.exe", now + 1.0)
    machine.observe("dota2.exe", now + 1.1)
    assert machine.observe("dota2.exe", now + 1.7) is False


def test_detector_arms_only_after_other_process_seen():
    machine = make_machine()
    now = 1000.0

    assert machine.is_armed is False
    machine.observe("dota2.exe", now)
    assert machine.is_armed is False
    machine.observe("chrome.exe", now + 1)
    assert machine.is_armed is True


def test_target_names_are_case_insensitive():
    machine = make_machine()
    now = 1000.0

    machine.observe("chrome.exe", now)
    machine.observe("DOTA2.EXE", now + 0.1)
    assert machine.observe("DOTA2.EXE", now + 0.7) is True
