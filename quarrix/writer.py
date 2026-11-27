"""Writing an extracted document to the output folder.

Tree produced for each PDF::

    <output_dir>/sources/<slug>/
        document.md      for humans and AI (hierarchy as Markdown headings)
        document.json    ordered blocks + section tree + table/figure index
        meta.json        provenance, SHA-256 fingerprint, licence, distribution rules
        tables/t001.csv  one CSV per table
        figures/f001.png one image per figure or chart
        native.json.gz   raw engine export (Docling), only with ``native_export = true``

``meta.json`` is never overwritten if it already exists: licence information
entered by hand survives a new extraction.

Output is kept small so that an output folder can live in a Git repository
(GitHub warns at 50 MB per file and refuses 100 MB): JSON is written with one
block per line, table cells only go to their CSV, bounding boxes are dropped
unless ``geometry = true``, and the raw Docling export (which repeats every table
cell with its geometry and can reach hundreds of MB) is off by default.
"""

from __future__ import annotations

import csv
import gzip
import io
import hashlib
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from quarrix import __version__
from quarrix.config import Config
from quarrix.doctypes import Classification, DocType, load_registry
from quarrix.model import Block, ExtractedDocument, Section

SCHEMA_VERSION = "2.0"

log = logging.getLogger(__name__)
NATIVE_FILES = ("native.json", "native.json.gz")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].rstrip("-") or "document"


def document_slug(pdf: Path, digest: str) -> str:
    return f"{slugify(pdf.stem)}-{digest[:8]}"


def _md_escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", "<br>")


def table_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, *body = rows
    out = ["| " + " | ".join(_md_escape_cell(c) for c in header) + " |",
           "|" + "---|" * width]
    out += ["| " + " | ".join(_md_escape_cell(c) for c in r) + " |" for r in body]
    return "\n".join(out)


def render_markdown(doc: ExtractedDocument, meta: dict) -> str:
    lines = [
        "---",
        f"quarrix_id: {meta['id']}",
        f"title: {json.dumps(doc.title, ensure_ascii=False)}",
        f"source_file: {json.dumps(meta['source']['filename'], ensure_ascii=False)}",
        f"sha256: {meta['source']['sha256']}",
        f"pages: {doc.page_count}",
        f"engine: {doc.engine}",
        *_classification_frontmatter(meta),
        f"licence_status: {meta['licence']['status']}",
        f"distribution: {meta['licence']['distribution']}",
        "---",
        "",
    ]
    used_captions = {b.caption for b in doc.blocks if b.type in ("table", "figure") and b.caption}
    page = 0
    for b in doc.blocks:
        if b.page != page:
            page = b.page
            lines.append(f"<!-- page {page} -->")
            lines.append("")
        if b.type == "heading" and b.level == 0:
            lines.append(f"# {b.text}")
        elif b.type == "heading":
            # The document title uses "#": sections start at "##".
            level = min(6, (b.section.count(".") + 2) if b.section else 2)
            lines.append(f"{'#' * level} {b.text}")
            lines.append(f"<!-- section {b.section} -->")
        elif b.type == "paragraph":
            lines.append(b.text)
        elif b.type == "list_item":
            lines.append(f"- {re.sub(r'^([•–▪◦●-]|\d+[.)]|[a-z][.)])\s+', '', b.text)}")
        elif b.type == "caption":
            if b.text in used_captions:
                continue  # rendered with its table or figure
            lines.append(f"*{b.text}*")
        elif b.type == "table":
            title = b.caption or f"Table {b.id}"
            lines.append(f"**{title}** <!-- table {b.id}, file {b.file} -->")
            lines.append("")
            lines.append(table_to_markdown(b.rows or []))
        elif b.type == "figure":
            alt = b.caption or f"Figure {b.id}"
            lines.append(f"![{alt}]({b.file}) <!-- figure {b.id} -->")
            if b.caption:
                lines.append(f"*{b.caption}*")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _classification_frontmatter(meta: dict) -> list[str]:
    c = meta.get("classification", {})
    return [
        f"category: {c.get('category', '')}",
        f"author: {json.dumps(c.get('author', ''), ensure_ascii=False)}",
        f"distributor: {json.dumps(c.get('distributor', ''), ensure_ascii=False)}",
        f"doc_type: {c.get('type', '')}",
        f"subjects: {json.dumps(c.get('subjects', []), ensure_ascii=False)}",
    ]


def _default_meta(pdf: Path, digest: str, slug: str, doc: ExtractedDocument, cfg: Config,
                  doctype: DocType | None = None) -> dict:
    status = cfg.licence.status
    distribution = cfg.licence.distribution
    if doctype is not None:
        # The document type suggests the licence status (e.g. code -> proprietary).
        if status == "unknown":
            status = doctype.licence_status
        distribution = doctype.distribution or distribution
    return {
        "schema": SCHEMA_VERSION,
        "id": slug,
        "title": doc.title,
        "source": {
            "filename": pdf.name,
            "sha256": digest,
            "pages": doc.page_count,
            "pdf_metadata": doc.pdf_metadata,
            # To fill in: publisher, standard, edition, year, URL, ISBN…
            "publisher": doc.pdf_metadata.get("author", ""),
            "edition": "",
            "year": "",
            "reference": "",
        },
        "licence": {
            # unknown | public-domain | open | permission | proprietary
            "status": status,
            "holder": cfg.licence.holder,
            "spdx": "",
            "notes": "",
            # none: never distributed · derived: only derived data is distributed ·
            # full: text and figures may be distributed (public domain, open licence)
            "distribution": distribution,
        },
        "exclude": False,  # true: skipped by "quarrix dist"
        "redact": {"sections": [], "tables": [], "figures": []},
        # "derived" mode: value tables allowed in the pack ("all" or a list of ids)
        "share": {"tables": []},
        "classification": Classification().to_dict(),
        "tags": [],
    }


def write_document(pdf: Path, doc: ExtractedDocument, cfg: Config, digest: str | None = None,
                   classification: Classification | None = None,
                   doctype: DocType | None = None) -> Path:
    digest = digest or sha256_file(pdf)
    slug = document_slug(pdf, digest)
    out = cfg.sources_dir / slug
    for sub in ("tables", "figures"):
        d = out / sub
        if d.is_dir():  # new extraction: start from a clean folder
            for f in d.iterdir():
                if f.is_file():
                    f.unlink()
        d.mkdir(parents=True, exist_ok=True)

    sections = doc.assign_ids_and_sections()

    for b in doc.blocks:
        if b.type == "table":
            b.file = f"tables/{b.id}.csv"
            with (out / b.file).open("w", newline="", encoding="utf-8-sig") as fh:
                csv.writer(fh).writerows(b.rows or [])
        elif b.type == "figure" and b.image_bytes:
            b.file = f"figures/{b.id}.png"
            (out / b.file).write_bytes(optimize_png(b.image_bytes))

    meta_path = out / "meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["title"] = meta.get("title") or doc.title
    else:
        meta = _default_meta(pdf, digest, slug, doc, cfg, doctype)
    current = Classification.from_dict(meta.get("classification"))
    if doctype is not None and not current.type:
        current = Classification(category=doctype.category, type=doctype.id,
                                 subjects=list(doctype.subjects)).merged_over(current)
    if classification is not None:  # explicit user choice: takes precedence
        current = classification.merged_over(current)
    meta["classification"] = current.to_dict()
    meta["extraction"] = {
        "tool": "quarrix",
        "tool_version": __version__,
        "engine": doc.engine,
        "doc_type": doctype.id if doctype else "",
        "docling_settings": doctype.docling.to_dict() if doctype else {},
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _dump(meta_path, meta)

    _dump_document(out / "document.json",
                   build_document_json(doc, sections, meta, geometry=cfg.geometry))
    (out / "document.md").write_text(render_markdown(doc, meta), encoding="utf-8")
    for name in NATIVE_FILES:  # never leave a stale (and possibly huge) export behind
        (out / name).unlink(missing_ok=True)
    if doc.native is not None and cfg.native_export:
        with gzip.open(out / "native.json.gz", "wt", encoding="utf-8") as fh:
            json.dump(doc.native, fh, ensure_ascii=False, separators=(",", ":"))

    update_index(cfg)
    return out


def build_document_json(doc: ExtractedDocument, sections: list[Section], meta: dict,
                        geometry: bool = False) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "id": meta["id"],
        "title": doc.title,
        "pages": doc.page_count,
        "engine": doc.engine,
        "outline": [s.to_dict() for s in sections],
        "tables": [
            {"id": b.id, "page": b.page, "section": b.section, "caption": b.caption,
             "file": b.file, "n_rows": len(b.rows or []),
             "n_cols": max((len(r) for r in b.rows or []), default=0)}
            for b in doc.blocks if b.type == "table"
        ],
        "figures": [
            {"id": b.id, "page": b.page, "section": b.section, "caption": b.caption, "file": b.file}
            for b in doc.blocks if b.type == "figure"
        ],
        "blocks": [b.to_dict(geometry=geometry, rows=False) for b in doc.blocks],
    }


def update_index(cfg: Config) -> Path:
    """Regenerate ``<output_dir>/index.json``, the catalog of all extracted documents."""
    entries = []
    if cfg.sources_dir.is_dir():
        for meta_path in sorted(cfg.sources_dir.glob("*/meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            docj = meta_path.parent / "document.json"
            counts = {}
            if docj.is_file():
                d = json.loads(docj.read_text(encoding="utf-8"))
                counts = {"tables": len(d.get("tables", [])), "figures": len(d.get("figures", [])),
                          "sections": _count_sections(d.get("outline", []))}
            entries.append({
                "id": meta["id"],
                "title": meta.get("title", ""),
                "path": f"sources/{meta_path.parent.name}",
                "pages": meta.get("source", {}).get("pages"),
                "licence_status": meta.get("licence", {}).get("status"),
                "distribution": meta.get("licence", {}).get("distribution"),
                "exclude": meta.get("exclude", False),
                "classification": Classification.from_dict(meta.get("classification")).to_dict(),
                "tags": meta.get("tags", []),
                **counts,
            })
    index = {"schema": SCHEMA_VERSION, "facets": _facets(entries), "documents": entries}
    path = cfg.output / "index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _dump(path, index)
    return path


def _facets(entries: list[dict]) -> dict:
    """Counts per category, author, distributor, type and subject, to filter the catalog."""
    facets: dict[str, dict[str, int]] = {k: {} for k in
                                         ("category", "author", "distributor", "type", "subjects")}
    for e in entries:
        c = e["classification"]
        for key in ("category", "author", "distributor", "type"):
            if c.get(key):
                facets[key][c[key]] = facets[key].get(c[key], 0) + 1
        for s in c.get("subjects", []):
            facets["subjects"][s] = facets["subjects"].get(s, 0) + 1
    return {k: dict(sorted(v.items())) for k, v in facets.items()}


def classify_document(cfg: Config, doc_id: str, classification: Classification) -> dict:
    """Change the classification of an already extracted document (no re-extraction)."""
    out = cfg.sources_dir / doc_id
    meta_path = out / "meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"Document not found: {doc_id}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    reg = load_registry(cfg.output_dir)
    if classification.type and classification.type not in reg.types:
        raise ValueError(f"Unknown type: {classification.type}")
    new = classification.merged_over(Classification.from_dict(meta.get("classification")))
    meta["classification"] = new.to_dict()
    _dump(meta_path, meta)
    md = out / "document.md"
    if md.is_file():
        text = md.read_text(encoding="utf-8")
        lines = [ln for ln in text.split("\n")]
        if lines and lines[0] == "---":
            end = lines.index("---", 1)
            keep = [ln for ln in lines[1:end] if not ln.startswith(
                ("category:", "author:", "distributor:", "doc_type:", "subjects:"))]
            pos = next((i for i, ln in enumerate(keep) if ln.startswith("licence_status:")), len(keep))
            keep[pos:pos] = _classification_frontmatter(meta)
            md.write_text("\n".join(["---", *keep, *lines[end:]]), encoding="utf-8")
    update_index(cfg)
    return meta


def known_values(cfg: Config) -> dict[str, list[str]]:
    """Authors, distributors and subjects already used, for auto-completion."""
    empty = {"author": [], "distributor": [], "subjects": []}
    if cfg.output_dir is None:
        return empty
    path = cfg.output_dir / "index.json"
    if not path.is_file():
        return empty
    facets = json.loads(path.read_text(encoding="utf-8")).get("facets", {})
    return {k: list(facets.get(k, {})) for k in ("author", "distributor", "subjects")}


def _count_sections(outline: list[dict]) -> int:
    return sum(1 + _count_sections(s.get("children", [])) for s in outline)


def optimize_png(data: bytes) -> bytes:
    """Recompress a PNG losslessly (Pillow, when installed); keep the smaller one."""
    try:
        from PIL import Image
    except ImportError:
        return data
    try:
        with Image.open(io.BytesIO(data)) as img:
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
    except Exception:  # noqa: BLE001 - keep the original image
        return data
    out = buf.getvalue()
    return out if len(out) < len(data) else data


def large_files(folder: Path, limit_mb: float) -> list[tuple[str, float]]:
    """Files of ``folder`` larger than ``limit_mb`` (relative path, size in MB)."""
    found = []
    for f in sorted(folder.rglob("*")):
        if f.is_file() and (size := f.stat().st_size / 1e6) > limit_mb:
            found.append((f.relative_to(folder).as_posix(), round(size, 1)))
    return found


def compact_document(doc_dir: Path, geometry: bool = False, keep_native: bool = False) -> tuple[int, int]:
    """Shrink an existing extraction without re-extracting it.

    Rewrites ``document.json`` in the compact layout (table cells that are already
    in a CSV and, unless ``geometry``, bounding boxes are dropped), removes the raw
    Docling export (or gzips it with ``keep_native``) and recompresses the PNGs.
    ``meta.json`` is not touched. Returns the folder size before and after, in bytes.
    """
    before = _folder_size(doc_dir)
    docj = doc_dir / "document.json"
    if docj.is_file():
        d = json.loads(docj.read_text(encoding="utf-8"))
        for b in d.get("blocks", []):
            if b.get("type") == "table" and b.get("file"):
                b.pop("rows", None)
            if not geometry:
                b.pop("bbox", None)
            elif b.get("bbox"):
                b["bbox"] = [round(v, 1) for v in b["bbox"]]
        d["schema"] = SCHEMA_VERSION
        _dump_document(docj, d)
    native = doc_dir / "native.json"
    if native.is_file():
        if keep_native:
            with native.open("rb") as src, gzip.open(doc_dir / "native.json.gz", "wb") as dst:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    dst.write(chunk)
        native.unlink()
    elif not keep_native:
        (doc_dir / "native.json.gz").unlink(missing_ok=True)
    for png in (doc_dir / "figures").glob("*.png"):
        data = png.read_bytes()
        smaller = optimize_png(data)
        if len(smaller) < len(data):
            png.write_bytes(smaller)
    return before, _folder_size(doc_dir)


def _folder_size(folder: Path) -> int:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())


def _dump_document(path: Path, data: dict) -> None:
    """Write ``document.json`` compactly: one line per block, outline and index.

    Much smaller than an indented dump, still valid JSON, readable and
    diff-friendly (a changed block changes one line).
    """
    def one(v) -> str:
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))

    lines = ["{"]
    items = list(data.items())
    for i, (key, value) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        if isinstance(value, list) and value:
            lines.append(f"{one(key)}:[")
            lines += [one(v) + ("," if j < len(value) - 1 else "") for j, v in enumerate(value)]
            lines.append("]" + comma)
        else:
            lines.append(f"{one(key)}:{one(value)}{comma}")
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
