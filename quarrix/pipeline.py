"""Single extraction entry point, used by the window and the command line."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from quarrix.config import Config
from quarrix.doctypes import Classification, Registry, load_registry
from quarrix.engines import get_engine
from quarrix.progress import CancelToken, Cancelled, ProgressFn, record_speed
from quarrix.writer import document_slug, large_files, sha256_file, write_document

log = logging.getLogger(__name__)


@dataclass
class Result:
    pdf: Path
    output: Path | None
    ok: bool
    message: str
    cancelled: bool = False


class Extractor:
    """Keeps engines in memory: Docling takes several seconds to load.

    One engine is created per distinct Docling configuration (hence per document
    type with different settings), then reused.
    """

    def __init__(self, cfg: Config, registry: Registry | None = None):
        self.cfg = cfg
        self.registry = registry or load_registry(cfg.output_dir)
        self._engines: dict[str, object] = {}
        self._fallback = None

    def engine_for(self, doctype):
        key = doctype.docling.key()
        if key not in self._engines:
            self._engines[key] = get_engine(self.cfg, doctype.docling)
        return self._engines[key]

    def close(self) -> None:
        """Stop the worker processes (Docling)."""
        for engine in self._engines.values():
            stop = getattr(engine, "stop", None)
            if stop:
                stop()

    @staticmethod
    def page_count(pdf: Path) -> int:
        try:
            import pymupdf

            with pymupdf.open(pdf) as d:
                return d.page_count
        except Exception:  # noqa: BLE001
            return 0

    def run(self, pdf: Path, force: bool = False,
            classification: Classification | None = None,
            progress: ProgressFn | None = None,
            cancel: CancelToken | None = None) -> Result:
        pdf = Path(pdf)
        if pdf.suffix.lower() != ".pdf" or not pdf.is_file():
            return Result(pdf, None, False, "not a PDF file")
        digest = sha256_file(pdf)
        out = self.cfg.sources_dir / document_slug(pdf, digest)
        if classification is not None and classification.type \
                and classification.type not in self.registry.types:
            return Result(pdf, None, False, f"unknown type: {classification.type}")
        if not force and (out / "document.json").is_file():
            if classification is not None:
                from quarrix.writer import classify_document

                classify_document(self.cfg, out.name, classification)
                return Result(pdf, out, True, "already extracted: classification updated")
            return Result(pdf, out, True, "already extracted (same fingerprint), skipped")
        tid = classification.type if classification else ""
        if not tid and (out / "meta.json").is_file():
            import json

            tid = json.loads((out / "meta.json").read_text(encoding="utf-8")) \
                .get("classification", {}).get("type", "")
        doctype = self.registry.get(tid)
        pages = self.page_count(pdf)
        try:
            engine = self.engine_for(doctype)
            start = time.monotonic()
            try:
                doc = engine.extract(pdf, progress=progress, cancel=cancel, pages=pages)
                record_speed(engine.name, doc.page_count, time.monotonic() - start)
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                if self.cfg.engine != "auto" or engine.name == "pymupdf":
                    raise
                # Docling unavailable (models not downloaded, offline…): fall back.
                log.warning("Docling failed (%s), falling back to PyMuPDF.", exc)
                from quarrix.engines.pymupdf_engine import PyMuPDFEngine

                self._engines[doctype.docling.key()] = PyMuPDFEngine(
                    image_scale=doctype.docling.images_scale)
                doc = self._engines[doctype.docling.key()].extract(
                    pdf, progress=progress, cancel=cancel, pages=pages)
            if cancel is not None:
                cancel.check()
            out = write_document(pdf, doc, self.cfg, digest=digest,
                                 classification=classification, doctype=doctype)
        except Cancelled:
            return Result(pdf, None, False, "cancelled", cancelled=True)
        except Exception as exc:  # noqa: BLE001 - reported to the user
            log.exception("Extraction of %s failed", pdf)
            return Result(pdf, None, False, f"error: {exc}")
        n_tab = sum(1 for b in doc.blocks if b.type == "table")
        n_fig = sum(1 for b in doc.blocks if b.type == "figure")
        n_sec = sum(1 for b in doc.blocks if b.type == "heading")
        message = (f"{doctype.label} · {doc.page_count} p., {n_sec} headings, {n_tab} tables, "
                   f"{n_fig} figures ({doc.engine})")
        big = large_files(out, self.cfg.size_warning_mb)
        if big:
            listed = ", ".join(f"{name} {size} MB" for name, size in big)
            log.warning("Large output files in %s: %s", out, listed)
            message += f" · warning, large files: {listed}"
        return Result(pdf, out, True, message)
