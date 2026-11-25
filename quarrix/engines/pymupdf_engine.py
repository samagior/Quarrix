"""Lightweight engine based on PyMuPDF.

Fast and without any AI model. The heading hierarchy comes from the PDF bookmarks
(table of contents) when present, otherwise from font size and weight. Tables are
detected with ``page.find_tables()``; figures (raster images and vector charts)
are rendered to PNG by clipping the page.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pymupdf

from quarrix.model import Block, ExtractedDocument
from quarrix.progress import CancelToken, PageTimer, ProgressFn

CAPTION_RE = re.compile(r"^\s*(fig(ure)?\.?|table|tableau|chart|graph|graphique|diagram|schéma|drawing|plan)\s*[\dA-Z]", re.I)
MIN_FIGURE_AREA = 0.02  # fraction of the page area
MAX_HEADING_CHARS = 160
FIGURE_MARGIN = 18.0  # points around a chart in which to look for its labels


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _inside(inner, outer, tol: float = 2.0) -> bool:
    return (
        inner[0] >= outer[0] - tol
        and inner[1] >= outer[1] - tol
        and inner[2] <= outer[2] + tol
        and inner[3] <= outer[3] + tol
    )


def _overlap_ratio(a, b) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    area = (a[2] - a[0]) * (a[3] - a[1]) or 1.0
    return (x1 - x0) * (y1 - y0) / area


def _line_style(line: dict) -> tuple[str, float, bool]:
    """Text, dominant font size and weight of a line."""
    sizes: Counter[float] = Counter()
    bold_chars = total_chars = 0
    for span in line["spans"]:
        n = len(span["text"].strip())
        sizes[round(span["size"], 1)] += n
        total_chars += n
        if span["flags"] & 16 or "bold" in span["font"].lower():
            bold_chars += n
    text = "".join(span["text"] for span in line["spans"])
    size = sizes.most_common(1)[0][0] if sizes else 0.0
    return text, size, total_chars > 0 and bold_chars / total_chars > 0.6


def _split_block(block: dict) -> list[tuple[str, float, bool, tuple]]:
    """Split a PyMuPDF block into groups of lines with the same style.

    PyMuPDF sometimes merges a heading and the next paragraph into one block;
    a change of size or weight marks a new unit.
    """
    groups: list[list] = []
    for line in block.get("lines", []):
        text, size, bold = _line_style(line)
        if not text.strip():
            continue
        if groups and groups[-1][1] == size and groups[-1][2] == bold:
            g = groups[-1]
            g[0].append(text)
            b = g[3]
            g[3] = (min(b[0], line["bbox"][0]), min(b[1], line["bbox"][1]),
                    max(b[2], line["bbox"][2]), max(b[3], line["bbox"][3]))
        else:
            groups.append([[text], size, bold, tuple(line["bbox"])])
    return [("\n".join(g[0]), g[1], g[2], g[3]) for g in groups]


def _words_in(words: list, rect) -> str:
    """Text of the words whose centre falls in ``rect``, in reading order."""
    x0, y0, x1, y1 = rect
    sel = [w for w in words if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1]
    sel.sort(key=lambda w: (w[5], w[6], w[7]))  # block, line, word
    return " ".join(w[4] for w in sel).strip()


def _table_rows(tab, words: list) -> list[list[str]]:
    rows = []
    for row in tab.rows:
        rows.append([_words_in(words, c) if c else "" for c in row.cells])
    if not rows or not any(any(r) for r in rows):
        rows = [[(c or "").strip() for c in r] for r in tab.extract()]
    return rows


class PyMuPDFEngine:
    name = "pymupdf"

    def __init__(self, image_scale: float = 2.0):
        self.image_scale = image_scale

    def extract(self, path: Path, progress: ProgressFn | None = None,
                cancel: CancelToken | None = None, pages: int = 0) -> ExtractedDocument:
        doc = pymupdf.open(path)
        try:
            return self._extract(doc, path, progress, cancel)
        finally:
            doc.close()

    # ---------------------------------------------------------------- private

    def _extract(self, doc: pymupdf.Document, path: Path, progress: ProgressFn | None = None,
                 cancel: CancelToken | None = None) -> ExtractedDocument:
        toc = doc.get_toc(simple=True)  # [[level, title, page], ...]
        toc_lookup = {(_norm(t), p): lvl for lvl, t, p in toc if t.strip()}
        toc_titles = {_norm(t): lvl for lvl, t, p in toc if t.strip()}

        timer = PageTimer(doc.page_count, progress)
        pages = []
        for i, page in enumerate(doc, start=1):
            if cancel is not None:
                cancel.check()
            pages.append(self._page_items(page))
            timer.update(i)
        body_size = self._body_size(pages)
        heading_levels = self._heading_levels(pages, body_size)

        blocks: list[Block] = []
        for page_no, items in enumerate(pages, start=1):
            for item in items:
                if item["kind"] != "text":
                    blocks.append(item["block"])
                    continue
                text, size, bold = item["text"], item["size"], item["bold"]
                flat = re.sub(r"\s+", " ", text).strip()
                if not flat:
                    continue
                key = _norm(flat)
                level = toc_lookup.get((key, page_no)) or toc_titles.get(key)
                if level is None and not toc and len(flat) <= MAX_HEADING_CHARS:
                    level = heading_levels.get((size, bold))
                if level is not None:
                    blocks.append(Block("heading", page_no, flat, level=level, bbox=item["bbox"]))
                elif CAPTION_RE.match(flat):
                    blocks.append(Block("caption", page_no, flat, bbox=item["bbox"]))
                elif re.match(r"^\s*([•\-–▪◦●]|\d+[.)]|[a-z][.)])\s+", text):
                    blocks.append(Block("list_item", page_no, flat, bbox=item["bbox"]))
                else:
                    blocks.append(Block("paragraph", page_no, _join_lines(text), bbox=item["bbox"]))

        _attach_captions(blocks)
        meta = {k: v for k, v in (doc.metadata or {}).items() if v}
        first = next((b for b in blocks if b.type == "heading"), None)
        if first and first.page == 1 and _norm(first.text) == _norm(meta.get("title", "")):
            first.level = 0  # document title, outside section numbering
        title = meta.get("title") or next(
            (b.text for b in blocks if b.type == "heading"), path.stem
        )
        return ExtractedDocument(
            title=title, page_count=doc.page_count, engine=self.name,
            blocks=blocks, pdf_metadata=meta,
        )

    def _page_items(self, page: pymupdf.Page) -> list[dict]:
        page_no = page.number + 1
        page_area = abs(page.rect) or 1.0
        items: list[dict] = []

        words = page.get_text("words")

        # Tables
        table_boxes = []
        try:
            tables = page.find_tables().tables
        except Exception:  # noqa: BLE001 - table detection is not critical
            tables = []
        for tab in tables:
            rows = _table_rows(tab, words)
            if not rows or sum(1 for r in rows for c in r if c) < 2:
                continue
            bbox = tuple(tab.bbox)
            table_boxes.append(bbox)
            items.append({"kind": "table", "y": bbox[1], "x": bbox[0],
                          "block": Block("table", page_no, rows=rows, bbox=bbox)})

        # Figures: raster images + groups of vector drawings (charts)
        candidates = []
        for img in page.get_images(full=True):
            try:
                candidates.extend(tuple(r) for r in page.get_image_rects(img[0]))
            except Exception:  # noqa: BLE001
                pass
        try:
            candidates.extend(tuple(r) for r in page.cluster_drawings())
        except Exception:  # noqa: BLE001
            pass
        figure_boxes: list[tuple] = []
        for bbox in sorted(candidates, key=lambda b: -(b[2] - b[0]) * (b[3] - b[1])):
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            if area / page_area < MIN_FIGURE_AREA:
                continue
            if any(_overlap_ratio(bbox, t) > 0.5 for t in table_boxes):
                continue  # table borders
            if any(_inside(bbox, f) for f in figure_boxes):
                continue
            figure_boxes.append(bbox)
        # Absorb axis labels and short legends around each chart.
        text_blocks = [b for b in page.get_text("dict", sort=True)["blocks"] if b.get("type") == 0]
        grown = []
        for fb in figure_boxes:
            m = FIGURE_MARGIN
            zone = (fb[0] - m, fb[1] - m, fb[2] + m, fb[3] + m)
            box = list(fb)
            for tb in text_blocks:
                t = "".join(s["text"] for l in tb["lines"] for s in l["spans"]).strip()
                if len(t) <= 40 and not CAPTION_RE.match(t) and _inside(tb["bbox"], zone):
                    box = [min(box[0], tb["bbox"][0]), min(box[1], tb["bbox"][1]),
                           max(box[2], tb["bbox"][2]), max(box[3], tb["bbox"][3])]
            grown.append(tuple(box))
        figure_boxes = grown
        for bbox in figure_boxes:
            clip = pymupdf.Rect(bbox) & page.rect
            pix = page.get_pixmap(matrix=pymupdf.Matrix(self.image_scale, self.image_scale), clip=clip)
            items.append({"kind": "figure", "y": bbox[1], "x": bbox[0],
                          "block": Block("figure", page_no, image_bytes=pix.tobytes("png"), bbox=bbox)})

        # Text outside tables and figures
        for blk in text_blocks:
            for text, size, bold, bbox in _split_block(blk):
                if any(_overlap_ratio(bbox, t) > 0.5 for t in table_boxes):
                    continue
                if any(_inside(bbox, f) for f in figure_boxes) and not CAPTION_RE.match(text.strip()):
                    continue  # axis labels, legends inside a chart
                items.append({"kind": "text", "y": bbox[1], "x": bbox[0], "bbox": bbox,
                              "text": text, "size": size, "bold": bold})

        items.sort(key=lambda it: (round(it["y"], 0), it["x"]))
        return items

    @staticmethod
    def _body_size(pages: list[list[dict]]) -> float:
        sizes: Counter[float] = Counter()
        for items in pages:
            for it in items:
                if it["kind"] == "text":
                    sizes[it["size"]] += len(it["text"])
        return sizes.most_common(1)[0][0] if sizes else 10.0

    @staticmethod
    def _heading_levels(pages: list[list[dict]], body: float) -> dict[tuple[float, bool], int]:
        """Map (size, bold) -> heading level, from largest to smallest."""
        styles: Counter[tuple[float, bool]] = Counter()
        for items in pages:
            for it in items:
                if it["kind"] != "text":
                    continue
                flat = it["text"].strip()
                if not flat or len(flat) > MAX_HEADING_CHARS or "\n" in flat.strip() and len(flat) > 80:
                    continue
                if it["size"] > body + 0.5 or (it["bold"] and it["size"] >= body - 0.1):
                    styles[(it["size"], it["bold"])] += 1
        ranked = sorted(styles, key=lambda s: (-s[0], not s[1]))[:6]
        return {style: i + 1 for i, style in enumerate(ranked)}


def _join_lines(text: str) -> str:
    """Join the lines of a paragraph, handling hyphenation."""
    text = re.sub(r"-\n(?=[a-zà-ÿ])", "", text)
    return re.sub(r"\s*\n\s*", " ", text).strip()


def _attach_captions(blocks: list[Block]) -> None:
    """Attach each caption to the nearest table or figure on the same page."""
    for i, blk in enumerate(blocks):
        if blk.type != "caption":
            continue
        is_table = blk.text.lower().startswith(("tableau", "table"))
        wanted = ("table",) if is_table else ("figure",)
        for j in (i + 1, i - 1, i + 2, i - 2):
            if 0 <= j < len(blocks):
                target = blocks[j]
                if target.type in wanted and target.page == blk.page and not target.caption:
                    target.caption = blk.text
                    break
