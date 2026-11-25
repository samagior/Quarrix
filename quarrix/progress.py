"""Progress tracking and cancellation of an extraction."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Callable

from quarrix.config import user_config_path


class Cancelled(Exception):
    """The user cancelled the current extraction."""


class CancelToken:
    """Cancellation flag shared between the window and the extraction thread."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def reset(self) -> None:
        self._event.clear()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled()


@dataclass
class Progress:
    fraction: float  # 0.0 – 1.0
    eta_seconds: float | None  # estimated remaining time, None if unknown
    stage: str = ""  # short text: "page 12/80", "layout analysis"…
    estimated: bool = False  # True when the percentage is an estimate (Docling)


ProgressFn = Callable[[Progress], None]


class PageTimer:
    """Compute percentage and remaining time from the pages processed."""

    def __init__(self, total: int, report: ProgressFn | None, stage: str = "page"):
        self.total = max(1, total)
        self.report = report
        self.stage = stage
        self.start = time.monotonic()

    def update(self, done: int) -> None:
        if self.report is None:
            return
        elapsed = time.monotonic() - self.start
        eta = elapsed / done * (self.total - done) if done else None
        self.report(Progress(done / self.total, eta, f"{self.stage} {done}/{self.total}"))


# ------------------------------------------------------------- remembered speed

_SPEED_FILE = "speed.json"
DEFAULT_SECONDS_PER_PAGE = {"docling": 2.0, "pymupdf": 0.05}


def _speed_path():
    return user_config_path().parent / _SPEED_FILE


def seconds_per_page(engine: str) -> float:
    """Average speed measured on this computer (seconds per page), to estimate durations."""
    try:
        data = json.loads(_speed_path().read_text(encoding="utf-8"))
        return float(data[engine])
    except Exception:  # noqa: BLE001 - missing or unreadable file: default value
        return DEFAULT_SECONDS_PER_PAGE.get(engine, 1.0)


def record_speed(engine: str, pages: int, seconds: float) -> None:
    """Moving average of the speed, to refine the next estimates."""
    if pages <= 0 or seconds <= 0:
        return
    path = _speed_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        data = {}
    measured = seconds / pages
    old = data.get(engine)
    data[engine] = measured if old is None else 0.7 * float(old) + 0.3 * measured
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass
