"""Output size: compact document.json, no raw export by default, compaction."""

import gzip
import json
from pathlib import Path

import pytest

from quarrix.config import Config
from quarrix.model import Block, ExtractedDocument
from quarrix.writer import compact_document, large_files, write_document

pytest.importorskip("reportlab")
from make_sample import build  # noqa: E402


def _doc(native=None) -> ExtractedDocument:
    return ExtractedDocument("Big guide", 2, "test", native=native, blocks=[
        Block("heading", 1, "1 Loads", level=1, bbox=(10.123, 20.456, 300.0, 40.0)),
        Block("paragraph", 1, "Some text.", bbox=(10.0, 50.0, 300.0, 80.0)),
        Block("table", 2, rows=[["Material", "Weight"], ["Concrete", "24.0"]]),
    ])


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    return build(tmp_path / "big-guide.pdf")


def test_document_json_is_compact(pdf, tmp_path):
    cfg = Config(output_dir=tmp_path / "out")
    out = write_document(pdf, _doc(native={"cells": [1] * 1000}), cfg)
    text = (out / "document.json").read_text(encoding="utf-8")
    d = json.loads(text)
    assert d["schema"] == "2.0"
    table = next(b for b in d["blocks"] if b["type"] == "table")
    assert "rows" not in table and table["file"] == "tables/t001.csv"
    assert all("bbox" not in b for b in d["blocks"])
    assert '"blocks":[' in text and '\n{"type":"heading"' in text  # one block per line
    assert not (out / "native.json").exists() and not (out / "native.json.gz").exists()


def test_geometry_and_native_on_request(pdf, tmp_path):
    cfg = Config(output_dir=tmp_path / "out", geometry=True, native_export=True)
    out = write_document(pdf, _doc(native={"cells": [1, 2]}), cfg)
    d = json.loads((out / "document.json").read_text(encoding="utf-8"))
    assert d["blocks"][0]["bbox"] == [10.1, 20.5, 300.0, 40.0]
    with gzip.open(out / "native.json.gz", "rt", encoding="utf-8") as fh:
        assert json.load(fh) == {"cells": [1, 2]}


def test_compact_existing_extraction(pdf, tmp_path):
    cfg = Config(output_dir=tmp_path / "out")
    out = write_document(pdf, _doc(), cfg)
    # simulate an extraction written by an older version
    d = json.loads((out / "document.json").read_text(encoding="utf-8"))
    for b in d["blocks"]:
        b["bbox"] = [1.23456, 2.0, 3.0, 4.0]
        if b["type"] == "table":
            b["rows"] = [["Material", "Weight"], ["Concrete", "24.0"]] * 500
    (out / "document.json").write_text(json.dumps(d, indent=2), encoding="utf-8")
    (out / "native.json").write_text(json.dumps({"x": list(range(50000))}, indent=2))
    meta_before = (out / "meta.json").read_text(encoding="utf-8")

    before, after = compact_document(out)
    assert after < before / 4
    assert not (out / "native.json").exists()
    d = json.loads((out / "document.json").read_text(encoding="utf-8"))
    assert all("bbox" not in b and "rows" not in b for b in d["blocks"])
    assert (out / "meta.json").read_text(encoding="utf-8") == meta_before


def test_large_files(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 2_000_000)
    (tmp_path / "b.bin").write_bytes(b"x" * 10)
    assert large_files(tmp_path, 1) == [("a.bin", 2.0)]
