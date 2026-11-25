"""Building a distributable pack without reproducing protected works.

Principle: the output folder holds two layers.

* ``sources/<doc>/``: the full extraction (text, tables, figures). Internal and
  private use, as a working reference. **Never distributed as is** unless the
  licence allows it (``distribution = "full"``).
* ``sources/<doc>/derived/``: derived content, written from the source: values,
  formulas, structured parameters, rephrased summaries, with references to the
  original section and page. This is the layer that gets distributed.

``quarrix dist`` reads each document's ``meta.json`` and applies:

==================  ==========================================================
distribution        what goes into the pack
==================  ==========================================================
``none``            nothing (or ``exclude: true``)
``derived``         bibliographic reference, section outline (titles and
                    pages), the ``derived/`` folder, and only the tables
                    listed in ``share.tables``
``full``            everything except the items listed in ``redact``; requires
                    a licence status of ``public-domain``, ``open`` or
                    ``permission``, otherwise the document falls back to ``derived``
==================  ==========================================================
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from quarrix import __version__
from quarrix.config import Config

FULL_OK = {"public-domain", "open", "permission"}


def _effective_mode(meta: dict) -> str:
    if meta.get("exclude"):
        return "none"
    lic = meta.get("licence", {})
    mode = lic.get("distribution", "derived")
    if mode == "full" and lic.get("status") not in FULL_OK:
        return "derived"
    return mode if mode in ("none", "derived", "full") else "derived"


def _strip_outline(outline: list[dict], redacted: set[str]) -> list[dict]:
    out = []
    for s in outline:
        if s["id"] in redacted or any(s["id"].startswith(r + ".") for r in redacted):
            continue
        out.append({"id": s["id"], "title": s["title"], "page": s["page"],
                    "children": _strip_outline(s.get("children", []), redacted)})
    return out


def _in_redacted(section: str, redacted: set[str]) -> bool:
    return any(section == r or section.startswith(r + ".") for r in redacted)


def build_dist(cfg: Config, make_zip: bool = True) -> dict:
    src_root = cfg.sources_dir
    pack = cfg.dist_dir / "quarrix-pack"
    if pack.exists():
        shutil.rmtree(pack)
    (pack / "documents").mkdir(parents=True)

    report = {"included": [], "skipped": []}
    catalog = []
    for meta_path in sorted(src_root.glob("*/meta.json")):
        doc_dir = meta_path.parent
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        mode = _effective_mode(meta)
        if mode == "none":
            report["skipped"].append(meta["id"])
            continue
        docj = json.loads((doc_dir / "document.json").read_text(encoding="utf-8"))
        redact = meta.get("redact", {})
        red_sec = set(redact.get("sections", []))
        red_tab = set(redact.get("tables", []))
        red_fig = set(redact.get("figures", []))
        dest = pack / "documents" / meta["id"]
        dest.mkdir(parents=True)

        reference = {
            "id": meta["id"],
            "title": meta.get("title"),
            "publisher": meta["source"].get("publisher"),
            "edition": meta["source"].get("edition"),
            "year": meta["source"].get("year"),
            "reference": meta["source"].get("reference"),
            "sha256": meta["source"]["sha256"],
            "licence": {k: meta["licence"].get(k) for k in ("status", "holder", "spdx", "notes")},
            "distribution": mode,
            "classification": meta.get("classification", {}),
        }
        _dump(dest / "reference.json", reference)
        _dump(dest / "outline.json", _strip_outline(docj.get("outline", []), red_sec))

        if mode == "derived":
            shared = meta.get("share", {}).get("tables", [])
            allowed = {t["id"] for t in docj["tables"]} if shared == "all" else set(shared)
        else:
            allowed = {t["id"] for t in docj["tables"]}
        tables = [t for t in docj["tables"]
                  if t["id"] in allowed and t["id"] not in red_tab
                  and not _in_redacted(t["section"], red_sec)]
        for t in tables:
            (dest / "tables").mkdir(exist_ok=True)
            shutil.copy2(doc_dir / t["file"], dest / t["file"])
        _dump(dest / "tables.json", tables)

        if mode == "full":
            figures = [f for f in docj["figures"] if f["id"] not in red_fig
                       and not _in_redacted(f["section"], red_sec) and f.get("file")]
            for f in figures:
                (dest / "figures").mkdir(exist_ok=True)
                shutil.copy2(doc_dir / f["file"], dest / f["file"])
            _dump(dest / "figures.json", figures)
            blocks = [b for b in docj["blocks"]
                      if not _in_redacted(b.get("section", ""), red_sec)
                      and b.get("id") not in red_tab | red_fig]
            _dump(dest / "content.json", blocks)

        derived = doc_dir / "derived"
        if derived.is_dir():
            shutil.copytree(derived, dest / "derived")

        catalog.append({"id": meta["id"], "title": meta.get("title"), "distribution": mode,
                        "classification": meta.get("classification", {}),
                        "tags": meta.get("tags", []), "path": f"documents/{meta['id']}"})
        report["included"].append(meta["id"])

    manifest = {
        "name": "quarrix-pack",
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool_version": __version__,
        "documents": catalog,
        "files": {
            str(p.relative_to(pack)).replace("\\", "/"): _sha256(p)
            for p in sorted(pack.rglob("*")) if p.is_file()
        },
    }
    _dump(pack / "manifest.json", manifest)
    (pack / "NOTICE.md").write_text(_notice(catalog, pack), encoding="utf-8")

    if make_zip:
        zpath = cfg.dist_dir / "quarrix-pack.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(pack.rglob("*")):
                if p.is_file():
                    zf.write(p, p.relative_to(pack.parent))
        report["zip"] = str(zpath)
    report["pack"] = str(pack)
    return report


def publish(cfg: Config, report: dict) -> Path:
    """Copy the compiled pack to ``publish_dir`` (for example a public Git repository).

    Only ``quarrix-pack/`` is copied: never ``sources/`` nor ``index.json``.
    """
    if cfg.publish_dir is None:
        raise ValueError("publish_dir is not configured (destination folder of the pack).")
    pub = cfg.publish_dir.resolve()
    data = cfg.output.resolve()
    if pub == data or data in pub.parents or pub in data.parents:
        raise ValueError("publish_dir must be a folder separate from the output folder.")
    dest = pub / "quarrix-pack"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(report["pack"], dest)
    return dest


def _notice(catalog: list[dict], pack: Path) -> str:
    lines = [
        "# Notice: sources and copyright",
        "",
        "This pack contains derived data (values, parameters, formulas, rephrased",
        "summaries) and references to the original documents. It does not replace",
        "those documents: refer to the official source for any normative use.",
        "",
    ]
    for entry in catalog:
        ref = json.loads((pack / entry["path"] / "reference.json").read_text(encoding="utf-8"))
        parts = [ref.get("title") or entry["id"]]
        for k in ("publisher", "edition", "year", "reference"):
            if ref.get(k):
                parts.append(str(ref[k]))
        holder = ref["licence"].get("holder")
        lines.append(f"- {', '.join(parts)}" + (f" © {holder}" if holder else "")
                     + f" ({entry['distribution']})")
    return "\n".join(lines) + "\n"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
