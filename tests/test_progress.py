import time
from pathlib import Path

import pymupdf
import pytest

from quarrix.config import Config
from quarrix.engines.process import ProcessEngine
from quarrix.pipeline import Extractor
from quarrix.progress import CancelToken, Cancelled, record_speed, seconds_per_page

import slow_engine
from make_sample import build


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(output_dir=tmp_path / "data", engine="pymupdf")


@pytest.fixture
def multipage(tmp_path: Path) -> Path:
    one = build(tmp_path / "one.pdf")
    out = tmp_path / "multi.pdf"
    doc = pymupdf.open()
    src = pymupdf.open(one)
    for _ in range(5):
        doc.insert_pdf(src)
    doc.save(out)
    return out


def test_pymupdf_reports_progress(cfg, multipage):
    seen = []
    r = Extractor(cfg).run(multipage, progress=seen.append)
    assert r.ok
    fracs = [p.fraction for p in seen]
    assert fracs == sorted(fracs) and fracs[-1] == 1.0
    assert len(seen) == 5 and seen[-1].stage == "page 5/5"
    assert seen[-1].eta_seconds == 0 and not seen[-1].estimated


def test_cancel_before_writing(cfg, multipage):
    token = CancelToken()
    token.cancel()
    r = Extractor(cfg).run(multipage, cancel=token)
    assert r.cancelled and not r.ok and r.message == "cancelled"
    assert not list((cfg.output_dir / "sources").glob("*/document.json")) \
        if (cfg.output_dir / "sources").exists() else True


def test_cancel_mid_document(cfg, multipage):
    token = CancelToken()

    def report(p):
        if p.fraction >= 0.4:
            token.cancel()

    r = Extractor(cfg).run(multipage, progress=report, cancel=token)
    assert r.cancelled


def _process_engine(delay):
    return ProcessEngine("slow", slow_engine.make, {"delay": delay}, f"d{delay}", lambda: 0.5)


def test_process_engine_runs_and_estimates(tmp_path):
    eng = _process_engine(0.8)
    try:
        seen = []
        doc = eng.extract(tmp_path / "x.pdf", progress=seen.append, pages=2)
        assert doc.title == "slow"
        assert seen[-1].fraction == 1.0
        assert any(p.estimated and 0 < p.fraction <= 0.95 for p in seen[:-1])
        # the process is reused (models kept in memory)
        pid = eng._proc.pid
        eng.extract(tmp_path / "x.pdf")
        assert eng._proc.pid == pid
    finally:
        eng.stop()


def test_process_engine_cancel_kills_worker(tmp_path):
    eng = _process_engine(30)
    token = CancelToken()
    try:
        eng._ensure()
        proc = eng._proc
        start = time.monotonic()

        def report(p):
            if time.monotonic() - start > 0.5:
                token.cancel()

        with pytest.raises(Cancelled):
            eng.extract(tmp_path / "x.pdf", progress=report, cancel=token, pages=10)
        assert time.monotonic() - start < 5
        assert not proc.is_alive()
        assert eng._proc is None  # restarted for the next document
    finally:
        eng.stop()


def test_speed_memory():
    assert seconds_per_page("docling") == 2.0  # default value
    record_speed("docling", 10, 10.0)
    assert seconds_per_page("docling") == pytest.approx(1.0)
    record_speed("docling", 10, 30.0)
    assert seconds_per_page("docling") == pytest.approx(0.7 * 1.0 + 0.3 * 3.0)
