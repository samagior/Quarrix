import json
from pathlib import Path

import pytest

from quarrix.cli import main as cli
from quarrix.config import Config
from quarrix.doctypes import Classification, DocType, default_registry, load_registry
from quarrix.pipeline import Extractor
from quarrix.writer import classify_document

from make_sample import build


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(output_dir=tmp_path / "data", engine="pymupdf")


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    return build(tmp_path / "sample-code.pdf")


def test_default_types():
    reg = default_registry()
    for tid in ("building-code", "standard", "datasheet", "specification", "scanned-document", "other"):
        assert tid in reg.types
    code = reg.get("building-code")
    assert code.category == "government"
    assert code.licence_status == "proprietary"
    assert code.docling.headings.numbering_keywords["part"] == ["part", "partie"]
    assert reg.get("scanned-document").docling.force_full_page_ocr
    assert reg.get("missing").id == "other"
    assert {"government", "organization", "standards-body", "manufacturer"} <= set(reg.categories)


def test_registry_created_and_roundtrip(cfg):
    reg = load_registry(cfg.output_dir)
    assert (cfg.output_dir / "types.toml").is_file()
    t = DocType("thermal-report", "Thermal report", category="professional")
    t.docling.ocr = True
    t.docling.headings.numbering_keywords = {"chapter": ["chapter"]}
    reg.add_type(t)
    reg.save()
    again = load_registry(cfg.output_dir)
    assert again.types["thermal-report"].docling.ocr is True
    assert again.types["thermal-report"].docling.headings.numbering_keywords == {"chapter": ["chapter"]}
    with pytest.raises(ValueError):
        again.remove_type("other")
    with pytest.raises(ValueError):
        again.add_type(DocType("mauvais id!", "x"))


def test_extract_with_classification(cfg, sample):
    cls = Classification(author="Example Code Council", type="building-code", subjects=["assemblies", "building envelope"])
    r = Extractor(cfg).run(sample, classification=cls)
    assert r.ok, r.message
    meta = json.loads((r.output / "meta.json").read_text(encoding="utf-8"))
    c = meta["classification"]
    assert c["type"] == "building-code"
    assert c["category"] == "government"  # suggested by the type
    assert c["author"] == "Example Code Council"
    assert c["subjects"] == ["assemblies", "building envelope"]
    assert meta["licence"]["status"] == "proprietary"
    assert meta["extraction"]["doc_type"] == "building-code"
    md = (r.output / "document.md").read_text(encoding="utf-8")
    assert "doc_type: building-code" in md and '"Example Code Council"' in md
    index = json.loads((cfg.output_dir / "index.json").read_text(encoding="utf-8"))
    assert index["facets"]["author"] == {"Example Code Council": 1}
    assert index["facets"]["subjects"]["assemblies"] == 1


def test_unknown_type_rejected(cfg, sample):
    r = Extractor(cfg).run(sample, classification=Classification(type="missing"))
    assert not r.ok


def test_reclassify_without_reextraction(cfg, sample):
    out = Extractor(cfg).run(sample).output
    meta = classify_document(cfg, out.name, Classification(type="standard", author="Example Standards Institute"))
    assert meta["classification"]["type"] == "standard"
    assert meta["classification"]["author"] == "Example Standards Institute"
    md = (out / "document.md").read_text(encoding="utf-8")
    assert md.count("doc_type:") == 1 and "doc_type: standard" in md
    # Dropping an already extracted PDF with a classification updates it without re-extracting
    r = Extractor(cfg).run(sample, classification=Classification(subjects=["charges"]))
    assert "classification updated" in r.message
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["classification"]["subjects"] == ["charges"]
    assert meta["classification"]["author"] == "Example Standards Institute"


def test_engine_cached_per_docling_settings(cfg):
    ex = Extractor(cfg)
    reg = ex.registry
    a = ex.engine_for(reg.get("building-code"))
    assert ex.engine_for(reg.get("building-code")) is a
    assert len(ex._engines) == 1
    ex.engine_for(reg.get("scanned-document"))
    assert len(ex._engines) == 2


def test_cli_extract_and_list(cfg, sample, capsys):
    root = str(cfg.output_dir)
    assert cli(["--output-dir", root, "--engine", "pymupdf", "extract", str(sample),
                "--type", "building-code", "--author", "Example Code Council", "--subject", "assemblies"]) == 0
    capsys.readouterr()
    assert cli(["--output-dir", root, "list", "--subject", "assemblies"]) == 0
    out = capsys.readouterr().out
    assert "building-code" in out and "Example Code Council" in out
    assert cli(["--output-dir", root, "list", "--type", "standard"]) == 0
    assert capsys.readouterr().out.strip() == ""
    assert cli(["--output-dir", root, "types"]) == 0
    assert "building-code" in capsys.readouterr().out
