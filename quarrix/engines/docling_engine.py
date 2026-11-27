"""Recommended engine based on Docling (IBM).

Docling analyses the layout with local AI models: reading order, headings and
their level (bookmarks, "2.3.1" numbering, styles), table structure (merged
cells included) and figures. Models are downloaded once on first use; after
that everything runs offline.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from quarrix.doctypes import DoclingSettings
from quarrix.model import Block, ExtractedDocument

log = logging.getLogger(__name__)


def is_available() -> bool:
    try:
        import docling  # noqa: F401
    except ImportError:
        return False
    return True


def make_engine(settings: dict) -> "DoclingEngine":
    """Factory used in the worker process (must stay at module level)."""
    return DoclingEngine(DoclingSettings.from_dict(settings),
                         keep_native=bool(settings.get("keep_native", False)))


def build_pipeline_options(s: DoclingSettings):
    """Translate a document type's settings into Docling pipeline options."""
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode

    opts = PdfPipelineOptions()
    opts.do_ocr = s.ocr or s.force_full_page_ocr
    if opts.do_ocr:
        if s.force_full_page_ocr:
            opts.ocr_options.force_full_page_ocr = True
        if s.ocr_lang:
            opts.ocr_options.lang = list(s.ocr_lang)
    opts.do_table_structure = True
    opts.table_structure_options.mode = (
        TableFormerMode.FAST if s.table_mode == "fast" else TableFormerMode.ACCURATE
    )
    opts.generate_picture_images = True
    opts.images_scale = s.images_scale
    opts.do_picture_classification = s.picture_classification
    opts.do_formula_enrichment = s.formulas
    if s.chart_data and hasattr(opts, "do_chart_extraction"):
        opts.do_chart_extraction = True  # chart -> CSV via a local vision model

    # Heading hierarchy: bookmarks, numbering (keywords per type), styles.
    hh = getattr(opts, "heading_hierarchy_options", None)
    if hh is not None:
        h = s.headings
        hh.enabled = True
        hh.use_bookmarks = h.use_bookmarks
        hh.use_numbering = h.use_numbering
        hh.use_style = h.use_style
        hh.max_level = h.max_level
        if h.numbering_keywords:
            hh.numbering_keywords = h.numbering_keywords
        if h.numbering_schemes:
            hh.numbering_schemes = h.numbering_schemes
    return opts


class DoclingEngine:
    name = "docling"

    def __init__(self, settings: DoclingSettings | None = None, keep_native: bool = False):
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import DocumentConverter, PdfFormatOption

        self.settings = settings or DoclingSettings()
        self.keep_native = keep_native  # raw export is large: only build it when asked
        opts = build_pipeline_options(self.settings)
        self._converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )

    def extract(self, path: Path) -> ExtractedDocument:
        from docling_core.types.doc import (
            ListItem,
            PictureItem,
            SectionHeaderItem,
            TableItem,
            TextItem,
            TitleItem,
        )

        result = self._converter.convert(str(path))
        ddoc = result.document
        blocks: list[Block] = []
        title = ""
        caption_refs: set[str] = set()

        for item, _depth in ddoc.iterate_items():
            page = item.prov[0].page_no if getattr(item, "prov", None) else 0
            bbox = None
            if getattr(item, "prov", None):
                b = item.prov[0].bbox
                bbox = (b.l, b.t, b.r, b.b)

            if isinstance(item, TableItem):
                rows = _table_rows(item, ddoc)
                cap = item.caption_text(ddoc) or ""
                caption_refs.update(c.cref for c in item.captions)
                blocks.append(Block("table", page, rows=rows, caption=cap, bbox=bbox))
            elif isinstance(item, PictureItem):
                img = item.get_image(ddoc)
                data = None
                if img is not None:
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    data = buf.getvalue()
                cap = item.caption_text(ddoc) or ""
                caption_refs.update(c.cref for c in item.captions)
                blocks.append(Block("figure", page, image_bytes=data, caption=cap, bbox=bbox))
            elif isinstance(item, TitleItem):
                title = title or item.text
                blocks.append(Block("heading", page, item.text, level=0, bbox=bbox))
            elif isinstance(item, SectionHeaderItem):
                # Docling: level 1 = first level below the document title.
                blocks.append(Block("heading", page, item.text, level=max(1, item.level), bbox=bbox))
            elif isinstance(item, ListItem):
                blocks.append(Block("list_item", page, item.text, bbox=bbox))
            elif isinstance(item, TextItem):
                if item.self_ref in caption_refs or not item.text.strip():
                    continue
                label = str(getattr(item.label, "value", item.label))
                if label in ("page_header", "page_footer"):
                    continue
                kind = "caption" if label == "caption" else "paragraph"
                blocks.append(Block(kind, page, item.text, bbox=bbox))

        native = None
        if self.keep_native:
            native = ddoc.export_to_dict()
            _strip_images(native)
        meta = {}
        try:
            import pymupdf

            with pymupdf.open(path) as pdf:
                meta = {k: v for k, v in (pdf.metadata or {}).items() if v}
        except Exception:  # noqa: BLE001
            pass
        return ExtractedDocument(
            title=meta.get("title") or title or path.stem,
            page_count=ddoc.num_pages(),
            engine=self.name,
            blocks=blocks,
            pdf_metadata=meta,
            native=native,
        )


def _table_rows(item, ddoc) -> list[list[str]]:
    """Table grid; merged cells are repeated in each slot."""
    grid = item.data.grid
    if grid:
        return [[(cell.text or "").strip() for cell in row] for row in grid]
    df = item.export_to_dataframe(ddoc)
    return [list(map(str, df.columns))] + [[str(v) for v in r] for r in df.itertuples(index=False)]


def _strip_images(node) -> None:
    """Strip base64 images from the native export (they are already in figures/)."""
    if isinstance(node, dict):
        img = node.get("image")
        if isinstance(img, dict) and "uri" in img:
            img["uri"] = ""
        for v in node.values():
            _strip_images(v)
    elif isinstance(node, list):
        for v in node:
            _strip_images(v)
