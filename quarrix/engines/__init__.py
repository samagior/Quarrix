"""Extraction engine selection."""

from __future__ import annotations

import logging

from quarrix.config import Config
from quarrix.doctypes import DoclingSettings

log = logging.getLogger(__name__)


def get_engine(cfg: Config, settings: DoclingSettings | None = None):
    """Return the requested engine; "auto" prefers Docling when it is installed.

    ``settings`` comes from the document type: each type can tune Docling
    differently (OCR, table mode, heading numbering…).
    """
    from quarrix.engines import docling_engine

    settings = settings or DoclingSettings(
        ocr=cfg.ocr, images_scale=cfg.image_scale, chart_data=cfg.chart_data
    )
    if cfg.engine in ("auto", "docling") and docling_engine.is_available():
        from quarrix.engines.process import ProcessEngine
        from quarrix.progress import seconds_per_page

        # Separate process: cancellable, with estimated progress.
        opts = {**settings.to_dict(), "keep_native": cfg.native_export}
        return ProcessEngine("docling", docling_engine.make_engine, opts,
                             settings.key() + f"native={cfg.native_export}",
                             lambda: seconds_per_page("docling"))
    if cfg.engine == "docling":
        raise RuntimeError(
            "The Docling engine is not installed. Run: pip install \"quarrix[docling]\""
        )
    from quarrix.engines.pymupdf_engine import PyMuPDFEngine

    if cfg.engine == "auto":
        log.info("Docling not installed: using the PyMuPDF engine.")
    return PyMuPDFEngine(image_scale=settings.images_scale)
