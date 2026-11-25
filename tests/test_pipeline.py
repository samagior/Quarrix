import json
from pathlib import Path

import pytest

from quarrix.config import Config
from quarrix.dist import build_dist
from quarrix.model import Block, ExtractedDocument
from quarrix.pipeline import Extractor

pytest.importorskip("reportlab")
from make_sample import build  # noqa: E402


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    return build(tmp_path / "slab-guide.pdf")


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(output_dir=tmp_path / "data", engine="pymupdf")


def test_sections_follow_heading_hierarchy():
    doc = ExtractedDocument("t", 1, "test", blocks=[
        Block("heading", 1, "A", level=1),
        Block("heading", 1, "A.1", level=2),
        Block("paragraph", 1, "text"),
        Block("heading", 1, "A.1.1", level=3),
        Block("heading", 1, "B", level=1),
        Block("table", 1, rows=[["x"]]),
    ])
    roots = doc.assign_ids_and_sections()
    assert [s.id for s in roots] == ["1", "2"]
    assert roots[0].children[0].children[0].id == "1.1.1"
    assert doc.blocks[2].section == "1.1"
    assert doc.blocks[5].section == "2" and doc.blocks[5].id == "t001"


def test_extract_pymupdf(sample, cfg):
    r = Extractor(cfg).run(sample)
    assert r.ok, r.message
    out = r.output
    d = json.loads((out / "document.json").read_text(encoding="utf-8"))
    titles = [b["text"] for b in d["blocks"] if b["type"] == "heading"]
    assert "2.1.1 Unit weights" in titles
    # hierarchy: "2.1.1" is under "2.1", itself under "2 Loads"
    levels = {b["text"]: b.get("section", "").count(".") for b in d["blocks"] if b["type"] == "heading"}
    assert levels["2.1.1 Unit weights"] > levels["2.1 Dead loads"] > levels["2 Loads"]
    assert len(d["tables"]) == 1
    assert "Reinforced concrete" in (out / d["tables"][0]["file"]).read_text(encoding="utf-8-sig")
    assert len(d["figures"]) >= 1
    assert (out / d["figures"][0]["file"]).stat().st_size > 0
    md = (out / "document.md").read_text(encoding="utf-8")
    assert "| Reinforced concrete | 24.0 | typical |" in md
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["licence"]["distribution"] == "derived"
    assert (cfg.output_dir / "index.json").is_file()


def test_meta_survives_reextraction(sample, cfg):
    out = Extractor(cfg).run(sample).output
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    meta["licence"]["holder"] = "Example Publisher"
    (out / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    Extractor(cfg).run(sample, force=True)
    assert json.loads((out / "meta.json").read_text(encoding="utf-8"))["licence"]["holder"] == "Example Publisher"


def test_dist_derived_excludes_text_and_figures(sample, cfg):
    out = Extractor(cfg).run(sample).output
    (out / "derived").mkdir()
    (out / "derived" / "parameters.json").write_text('{"beton_arme_kN_m3": 24.0}', encoding="utf-8")
    report = build_dist(cfg)
    pack = Path(report["pack"]) / "documents" / out.name
    assert (pack / "derived" / "parameters.json").is_file()
    assert (pack / "outline.json").is_file()
    assert not (pack / "content.json").exists()
    assert not (pack / "figures").exists()
    assert json.loads((pack / "tables.json").read_text(encoding="utf-8")) == []


def test_dist_full_requires_open_licence(sample, cfg):
    out = Extractor(cfg).run(sample).output
    meta_path = out / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["licence"]["distribution"] = "full"  # status "unknown": refused
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    pack = Path(build_dist(cfg)["pack"]) / "documents" / out.name
    assert not (pack / "content.json").exists()

    meta["licence"]["status"] = "open"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    pack = Path(build_dist(cfg)["pack"]) / "documents" / out.name
    assert (pack / "content.json").exists() and (pack / "figures").is_dir()


def test_dist_skips_excluded(sample, cfg):
    out = Extractor(cfg).run(sample).output
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    meta["exclude"] = True
    (out / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    report = build_dist(cfg)
    assert report["skipped"] == [out.name]


def test_publish_copies_only_pack(sample, cfg, tmp_path):
    from quarrix.dist import publish

    Extractor(cfg).run(sample)
    pub = tmp_path / "public"
    cfg.publish_dir = pub
    report = build_dist(cfg)
    dest = publish(cfg, report)
    assert (dest / "manifest.json").is_file()
    assert not list(pub.rglob("document.md"))  # no full source published
    assert not (pub / "sources").exists()


def test_publish_refuses_private_repo(cfg):
    from quarrix.dist import publish

    cfg.publish_dir = cfg.output_dir
    with pytest.raises(ValueError):
        publish(cfg, {"pack": str(cfg.dist_dir / "quarrix-pack")})


def test_no_output_dir_is_explicit(tmp_path, capsys):
    from quarrix.cli import main
    from quarrix.config import NoOutputDir

    with pytest.raises(NoOutputDir):
        Config().sources_dir
    assert main(["extract", str(tmp_path / "x.pdf")]) == 2
    assert "output folder" in capsys.readouterr().err
    out = tmp_path / "choisi"
    sample = build(tmp_path / "s.pdf")
    assert main(["--output-dir", str(out), "--engine", "pymupdf", "extract", str(sample)]) == 0
    assert list((out / "sources").glob("*/document.md"))
