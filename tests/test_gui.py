import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from quarrix.config import Config  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_window_classification_panel(app, tmp_path):
    from quarrix.gui import MainWindow

    w = MainWindow(Config(output_dir=tmp_path / "data", engine="pymupdf"))
    p = w.panel
    p.type.setCurrentIndex(p.type.findData("building-code"))
    assert p.category.currentText() == "government"
    p.author.setCurrentText("Example Code Council")
    p.subjects.setText("assemblies, building envelope")
    c = p.classification()
    assert (c.type, c.author, c.subjects) == ("building-code", "Example Code Council", ["assemblies", "building envelope"])
    w.close()


def test_types_dialog_creates_type(app, tmp_path):
    from quarrix.doctypes import load_registry
    from quarrix.types_dialog import TypesDialog, text_to_keywords

    reg = load_registry(tmp_path / "data")
    dlg = TypesDialog(reg)
    dlg.registry.add_type(__import__("quarrix.doctypes", fromlist=["DocType"]).DocType("essai", "Essai"))
    dlg._refresh_list("essai")
    dlg.d_ocr.setChecked(True)
    dlg.h_kw.setPlainText("chapter: chapter\narticle: art., article")
    dlg._save()
    again = load_registry(tmp_path / "data")
    assert again.types["essai"].docling.ocr
    assert again.types["essai"].docling.headings.numbering_keywords["article"] == ["art.", "article"]
    assert text_to_keywords("bad line\nx: ") == {}


def test_format_eta():
    from quarrix.gui import format_eta

    assert format_eta(None) == "time remaining unknown"
    assert format_eta(42) == "about 42 s left"
    assert format_eta(125) == "about 2 min 05 s left"
    assert format_eta(3725) == "about 1 h 02 min left"


def test_queue_progress_and_cancel_all(app, tmp_path):
    import time as _t

    from make_sample import build
    from quarrix.gui import COL_STATE, MainWindow

    pdfs = [build(tmp_path / f"d{i}.pdf") for i in range(3)]
    w = MainWindow(Config(output_dir=tmp_path / "data", engine="pymupdf"))
    w.warn_classification = False
    w.enqueue(pdfs)
    assert w.btn_cancel.isEnabled() and w.btn_cancel_all.isEnabled()
    w.cancel_all()  # clears the queue; the current document stops or finishes
    deadline = _t.monotonic() + 10
    while w._current is not None and _t.monotonic() < deadline:
        app.processEvents()
        _t.sleep(0.02)
    states = [w.table.item(r, COL_STATE).text() for r in range(3)]
    assert states[1:] == ["Cancelled", "Cancelled"]
    assert states[0] in ("Cancelled", "Done")
    assert not w.btn_cancel.isEnabled()
    w.close()


def test_classification_warnings():
    from quarrix.doctypes import Classification
    from quarrix.gui import classification_warnings

    empty = Classification(type="other")
    assert len(classification_warnings(empty, None)) == 2
    code = Classification(category="government", author="Example Code Council", type="building-code")
    assert classification_warnings(code, None) == []
    same = classification_warnings(code, Classification.from_dict(code.to_dict()))
    assert same == ["the classification is the same as for the previous drop"]
    other = Classification(category="government", author="Example Code Council", type="standard")
    assert classification_warnings(other, code) == []


def test_drop_alert_and_classification_columns(app, tmp_path):
    from make_sample import build
    from quarrix.gui import COL_AUTHOR, COL_SUBJECTS, COL_TYPE, MainWindow

    pdf = build(tmp_path / "d.pdf")
    w = MainWindow(Config(output_dir=tmp_path / "data", engine="pymupdf"))
    asked = []
    answer = [False]
    w.ask_classification = lambda cls, warnings, n: asked.append(warnings) or answer[0]

    w.enqueue([pdf])  # classification not set, refused: nothing is added
    assert len(asked) == 1 and w.table.rowCount() == 0

    p = w.panel
    p.type.setCurrentIndex(p.type.findData("building-code"))
    p.author.setCurrentText("Example Code Council")
    p.subjects.setText("assemblies")
    w.enqueue([pdf])  # classification set: no warning
    assert len(asked) == 1 and w.table.rowCount() == 1
    assert w.table.item(0, COL_TYPE).text() == w.registry.get("building-code").label
    assert w.table.item(0, COL_AUTHOR).text() == "Example Code Council"
    assert w.table.item(0, COL_SUBJECTS).text() == "assemblies"

    answer[0] = True
    w.enqueue([pdf])  # same classification: warning, accepted
    assert len(asked) == 2 and "same as" in asked[-1][0]
    assert w.table.rowCount() == 2
    w.cancel_all()
    w.close()


def test_drop_asks_output_dir_when_none(app, tmp_path):
    from make_sample import build
    from quarrix.gui import MainWindow

    pdf = build(tmp_path / "d.pdf")
    w = MainWindow(Config(engine="pymupdf"))  # no output folder chosen
    w.warn_classification = False
    asked = []
    w.choose_root = lambda: asked.append(1) or False  # the user closes the dialog
    w.enqueue([pdf])
    assert asked == [1] and w.table.rowCount() == 0
    assert "none" in w.root_label.text()
    w.close()
