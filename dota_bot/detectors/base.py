from __future__ import annotations

import logging
import queue
import threading
from typing import Protocol

from ..models import MatchEvent

log = logging.getLogger(__name__)


class EventSink(Protocol):
    def put(self, event: MatchEvent) -> None: ...


class BaseDetector:
    """Общий каркас детектора: фоновый поток и очередь событий."""

    name = "base"

    def __init__(self, sink: EventSink):
        self._sink = sink
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def emit(self, event: MatchEvent) -> None:
        self._sink.put(event)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_guarded, name=f"detector-{self.name}", daemon=True)
        self._thread.start()
        log.info("Детектор %s запущен", self.name)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        log.info("Детектор %s остановлен", self.name)

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def _run_guarded(self) -> None:
        try:
            self.run()
        except Exception:
            log.exception("Детектор %s завершился с ошибкой", self.name)

    def run(self) -> None:
        raise NotImplementedError
