from dota_bot.input_control import InputResult, accept_match


class FakeController:
    available = True

    def __init__(self, pid=100, foreground=100, hwnd=555, focus_ok=True, focus_effective=True):
        self.pid = pid
        self.foreground = foreground
        self.hwnd = hwnd
        self.focus_ok = focus_ok
        self.focus_effective = focus_effective
        self.entered = 0
        self.focused: list[int] = []

    def find_pid(self, process_name):
        return self.pid

    def foreground_pid(self):
        return self.foreground

    def find_main_window(self, pid):
        return self.hwnd

    def focus(self, hwnd):
        self.focused.append(hwnd)
        if self.focus_ok and self.focus_effective:
            self.foreground = self.pid
        return self.focus_ok

    def send_enter(self):
        self.entered += 1


def test_accepts_when_already_foreground():
    controller = FakeController()

    assert accept_match(controller=controller) is InputResult.ACCEPTED
    assert controller.entered == 1
    assert controller.focused == []


def test_focuses_window_before_accepting():
    controller = FakeController(foreground=1)

    assert accept_match(controller=controller) is InputResult.ACCEPTED
    assert controller.focused == [555]
    assert controller.entered == 1


def test_does_not_input_when_focus_disabled():
    controller = FakeController(foreground=1)

    assert accept_match(focus_window=False, controller=controller) is InputResult.NOT_FOREGROUND
    assert controller.entered == 0


def test_does_not_input_when_focus_fails():
    controller = FakeController(foreground=1, focus_ok=False)

    assert accept_match(controller=controller) is InputResult.NOT_FOREGROUND
    assert controller.entered == 0


def test_does_not_input_when_window_never_activated():
    controller = FakeController(foreground=1, focus_effective=False)

    assert accept_match(controller=controller) is InputResult.NOT_FOREGROUND
    assert controller.entered == 0


def test_reports_missing_process():
    controller = FakeController(pid=None)

    assert accept_match(controller=controller) is InputResult.PROCESS_NOT_FOUND
    assert controller.entered == 0


def test_reports_missing_window():
    controller = FakeController(foreground=1, hwnd=None)

    assert accept_match(controller=controller) is InputResult.WINDOW_NOT_FOUND
    assert controller.entered == 0


def test_reports_unsupported_platform():
    controller = FakeController()
    controller.available = False

    assert accept_match(controller=controller) is InputResult.UNSUPPORTED
    assert controller.entered == 0


def test_reports_error_on_exception():
    class BrokenController(FakeController):
        def find_pid(self, process_name):
            raise RuntimeError("boom")

    controller = BrokenController()

    assert accept_match(controller=controller) is InputResult.ERROR
    assert controller.entered == 0
