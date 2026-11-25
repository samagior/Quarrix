"""Run an engine in a separate, interruptible process.

Docling processes a document in one go, without yielding between pages: its
progress cannot be followed page by page, nor can it be stopped cleanly within
the same process. It therefore runs in a persistent worker process (models are
loaded only once):

* **cancel** = terminate that process; it is restarted for the next document;
* **progress** = estimate from the page count and the average speed measured
  on this computer (``progress.seconds_per_page``).
"""

from __future__ import annotations

import multiprocessing as mp
import time
import traceback
from pathlib import Path
from typing import Callable

from quarrix.model import ExtractedDocument
from quarrix.progress import CancelToken, Cancelled, Progress, ProgressFn

EngineFactory = Callable[[dict], object]  # settings (dict) -> engine with .extract(path)


def _worker(conn, factory: EngineFactory) -> None:
    engines: dict[str, object] = {}
    while True:
        try:
            msg = conn.recv()
        except EOFError:
            return
        if msg is None:
            return
        path, settings, key = msg
        try:
            if key not in engines:
                engines[key] = factory(settings)
            doc = engines[key].extract(Path(path))
            conn.send(("ok", doc))
        except Exception as exc:  # noqa: BLE001 - sent back to the main process
            conn.send(("error", f"{exc}\n{traceback.format_exc()}"))


class ProcessEngine:
    """Wraps a slow engine: separate process, cancellation, estimated progress."""

    def __init__(self, name: str, factory: EngineFactory, settings: dict, key: str,
                 seconds_per_page: Callable[[], float]):
        self.name = name
        self._factory = factory
        self._settings = settings
        self._key = key
        self._spp = seconds_per_page
        self._proc: mp.process.BaseProcess | None = None
        self._conn = None

    def _ensure(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            return
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe()
        self._proc = ctx.Process(target=_worker, args=(child, self._factory), daemon=True)
        self._proc.start()
        child.close()
        self._conn = parent

    def stop(self) -> None:
        """Terminate the worker process (cancellation or shutdown)."""
        if self._proc is not None and self._proc.is_alive():
            self._proc.terminate()
            self._proc.join(5)
        self._proc = None
        self._conn = None

    def extract(self, path: Path, progress: ProgressFn | None = None,
                cancel: CancelToken | None = None, pages: int = 0) -> ExtractedDocument:
        self._ensure()
        self._conn.send((str(path), self._settings, self._key))
        expected = max(1.0, pages * self._spp())
        start = time.monotonic()
        while not self._conn.poll(0.25):
            if cancel is not None and cancel.cancelled:
                self.stop()
                raise Cancelled()
            if self._proc is None or not self._proc.is_alive():
                self.stop()
                raise RuntimeError(f"the {self.name} process stopped")
            if progress is not None:
                elapsed = time.monotonic() - start
                # Capped at 95%: the estimate must not announce an end that does not come.
                frac = min(0.95, elapsed / expected)
                eta = max(0.0, expected - elapsed) if elapsed < expected else None
                progress(Progress(frac, eta, f"analysis ({pages} p.)", estimated=True))
        status, payload = self._conn.recv()
        if status == "error":
            raise RuntimeError(payload.splitlines()[0] if payload else "unknown error")
        if progress is not None:
            progress(Progress(1.0, 0.0, "done", estimated=True))
        return payload

    def __del__(self):  # pragma: no cover - best-effort cleanup
        try:
            self.stop()
        except Exception:  # noqa: BLE001
            pass
