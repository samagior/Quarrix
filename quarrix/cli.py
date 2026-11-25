"""Quarrix command line.

    quarrix                        opens the drag-and-drop window
    quarrix extract a.pdf b.pdf    extracts without the window (--type, --category, --author…)
    quarrix classify ID …          changes the classification of an extracted document
    quarrix list [--type …]        lists documents, filtered by classification
    quarrix types [ID]             lists document types or shows one
    quarrix init FOLDER            prepares the output folder and remembers it
    quarrix dist [--publish-dir D] builds the distributable pack (and copies it to D)
    quarrix config                 shows the active configuration
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from quarrix import __version__
from quarrix.config import NoOutputDir, load_config, user_config_path, write_user_config
from quarrix.doctypes import Classification


def _add_classification_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("classification")
    g.add_argument("--type", dest="doc_type", help='document type (see "quarrix types")')
    g.add_argument("--category", help="government, organization, standards-body, manufacturer…")
    g.add_argument("--author", help="author or publisher")
    g.add_argument("--distributor", help="distributor or supplier")
    g.add_argument("--subject", action="append", default=[], dest="subjects",
                   help="subject (repeatable)")


def _classification_from_args(args) -> Classification | None:
    c = Classification(category=args.category or "", author=args.author or "",
                       distributor=args.distributor or "", type=args.doc_type or "",
                       subjects=list(args.subjects))
    return c if any(c.to_dict().values()) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quarrix", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="TOML configuration file")
    parser.add_argument("-o", "--output-dir", help="output folder (overrides the configuration)")
    parser.add_argument("--engine", choices=["auto", "docling", "pymupdf"])
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"quarrix {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    p_ext = sub.add_parser("extract", help="extract one or more PDFs")
    p_ext.add_argument("pdfs", nargs="+", type=Path)
    p_ext.add_argument("-f", "--force", action="store_true", help="re-extract even if already done")
    _add_classification_args(p_ext)

    p_cls = sub.add_parser("classify", help="change the classification of an extracted document")
    p_cls.add_argument("doc_id")
    _add_classification_args(p_cls)

    p_list = sub.add_parser("list", help="list extracted documents")
    p_list.add_argument("--type")
    p_list.add_argument("--category")
    p_list.add_argument("--author")
    p_list.add_argument("--subject")

    p_types = sub.add_parser("types", help="list document types")
    p_types.add_argument("type_id", nargs="?")

    p_init = sub.add_parser("init", aliases=["init-data"], help="prepare the output folder")
    p_init.add_argument("path", type=Path)
    p_init.add_argument("--lfs", action="store_true", help="store images with Git LFS")

    p_dist = sub.add_parser("dist", help="build the distributable pack")
    p_dist.add_argument("--no-zip", action="store_true")
    p_dist.add_argument("--publish", action="store_true",
                        help="copy the pack to publish_dir (e.g. a public Git repository)")
    p_dist.add_argument("--publish-dir", type=Path,
                        help="folder to copy the pack to (overrides publish_dir, implies --publish)")

    sub.add_parser("config", help="show the active configuration")
    sub.add_parser("gui", help="open the window (default)")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")

    cfg = load_config(args.config)
    if args.output_dir:
        cfg.output_dir = Path(args.output_dir).expanduser().resolve()
    if args.engine:
        cfg.engine = args.engine

    if args.cmd in (None, "gui"):
        from quarrix.gui import run_gui

        return run_gui(cfg)

    try:
        return _run(args, cfg)
    except NoOutputDir as exc:
        print(exc, file=sys.stderr)
        return 2


def _run(args, cfg) -> int:
    if args.cmd == "extract":
        from quarrix.pipeline import Extractor

        cfg.output  # clear error if no output folder is chosen
        ex = Extractor(cfg)
        cls = _classification_from_args(args)
        failures = 0
        pdfs = [p for arg in args.pdfs for p in (sorted({q for q in arg.rglob("*") if q.suffix.lower() == ".pdf"}) if arg.is_dir() else [arg])]
        for pdf in pdfs:
            r = ex.run(pdf, force=args.force, classification=cls)
            failures += not r.ok
            print(f"{'OK ' if r.ok else 'ERR'} {pdf.name} : {r.message}" + (f"\n    -> {r.output}" if r.output else ""))
        return 1 if failures else 0

    if args.cmd in ("init", "init-data"):
        from quarrix.datarepo import init_data_repo

        root = args.path.expanduser().resolve()
        for p in init_data_repo(root, lfs=args.lfs):
            print(f"created {p}")
        print(f"configuration saved: {write_user_config(root)}")
        return 0

    if args.cmd == "dist":
        from quarrix.dist import build_dist, publish

        report = build_dist(cfg, make_zip=not args.no_zip)
        if args.publish_dir:
            cfg.publish_dir = args.publish_dir.expanduser().resolve()
        if args.publish or args.publish_dir:
            report["published"] = str(publish(cfg, report))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "classify":
        from quarrix.writer import classify_document

        meta = classify_document(cfg, args.doc_id, _classification_from_args(args) or Classification())
        print(json.dumps(meta["classification"], ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "list":
        from quarrix.writer import update_index

        index = json.loads(update_index(cfg).read_text(encoding="utf-8"))
        for d in index["documents"]:
            c = d["classification"]
            if args.type and c["type"] != args.type:
                continue
            if args.category and c["category"] != args.category:
                continue
            if args.author and args.author.lower() not in c["author"].lower():
                continue
            if args.subject and args.subject not in c["subjects"]:
                continue
            print(f"{d['id']:<45} {c['category']:<14} {c['type']:<20} {c['author']:<20} "
                  f"{', '.join(c['subjects'])}")
        return 0

    if args.cmd == "types":
        from quarrix.doctypes import load_registry

        reg = load_registry(cfg.output_dir)
        if args.type_id:
            t = reg.types.get(args.type_id)
            if t is None:
                print(f"Unknown type: {args.type_id}", file=sys.stderr)
                return 1
            print(json.dumps({"id": t.id, **t.to_dict()}, ensure_ascii=False, indent=2))
        else:
            for t in reg.types.values():
                print(f"{t.id:<22} {t.label:<40} {t.category}")
            print(f"\nFile: {reg.path}")
        return 0

    if args.cmd == "config":
        print(f"file            : {cfg.source or '(none, defaults)'}")
        print(f"user config     : {user_config_path()}")
        print(f"output_dir      : {cfg.output_dir or '(not chosen)'}")
        print(f"publish_dir     : {cfg.publish_dir or '(not configured)'}")
        print(f"engine          : {cfg.engine}")
        print(f"default licence : {cfg.licence}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
