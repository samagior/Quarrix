"""Writing an extracted document to the output folder.

Tree produced for each PDF::

    <output_dir>/sources/<slug>/
        document.md      for humans and AI (hierarchy as Markdown headings)
        document.json    ordered blocks + section tree + table/figure index
        meta.json        provenance, SHA-256 fingerprint, licence, distribution rules
        tables/t001.csv  one CSV per table
        figures/f001.png one image per figure or chart
        native.json      native engine export (Docling), when available

``meta.json`` is never overwritten if it already exists: licence information
entered by hand survives a new extraction.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from quarrix import __version__
from quarrix.config import Config
from quarrix.doctypes import Classification, DocType, load_registry
from quarrix.model import Block, ExtractedDocument, Section

SCHEMA_VERSION = "1.0"


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
            (out / b.file).write_bytes(b.image_bytes)

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

    _dump(out / "document.json", build_document_json(doc, sections, meta))
    (out / "document.md").write_text(render_markdown(doc, meta), encoding="utf-8")
    if doc.native is not None:
        _dump(out / "native.json", doc.native)

    update_index(cfg)
    return out


def build_document_json(doc: ExtractedDocument, sections: list[Section], meta: dict) -> dict:
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
        "blocks": [b.to_dict() for b in doc.blocks],
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


def _dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
