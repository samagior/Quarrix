""""Manage types" window: create and configure document types."""

from __future__ import annotations

import copy

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from quarrix.doctypes import DEFAULT_TYPE, DocType, Registry

LICENCE_STATUSES = ["unknown", "public-domain", "open", "permission", "proprietary"]
DISTRIBUTIONS = ["derived", "none", "full"]


def _split(text: str) -> list[str]:
    return [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]


def keywords_to_text(kw: dict[str, list[str]]) -> str:
    return "\n".join(f"{k}: {', '.join(v)}" for k, v in kw.items())


def text_to_keywords(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            if k.strip() and _split(v):
                out[k.strip()] = _split(v)
    return out


class TypesDialog(QDialog):
    def __init__(self, registry: Registry, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Document types")
        self.resize(900, 640)
        self.registry = copy.deepcopy(registry)
        self._current: str | None = None

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        btn_new = QPushButton("New…")
        btn_new.clicked.connect(self._new)
        btn_dup = QPushButton("Duplicate…")
        btn_dup.clicked.connect(self._duplicate)
        btn_del = QPushButton("Delete")
        btn_del.clicked.connect(self._delete)
        left = QVBoxLayout()
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        for b in (btn_new, btn_dup, btn_del):
            row.addWidget(b)
        left.addLayout(row)

        # Type form
        self.f_id = QLineEdit()
        self.f_id.setReadOnly(True)
        self.f_label = QLineEdit()
        self.f_desc = QLineEdit()
        self.f_cat = QComboBox()
        self.f_cat.addItems(list(self.registry.categories))
        self.f_lic = QComboBox()
        self.f_lic.addItems(LICENCE_STATUSES)
        self.f_dist = QComboBox()
        self.f_dist.addItems(DISTRIBUTIONS)
        self.f_subj = QLineEdit()
        self.f_subj.setPlaceholderText("suggested subjects, comma-separated")
        general = QGroupBox("Type")
        fg = QFormLayout(general)
        fg.addRow("Id", self.f_id)
        fg.addRow("Name", self.f_label)
        fg.addRow("Description", self.f_desc)
        fg.addRow("Default category", self.f_cat)
        fg.addRow("Default licence", self.f_lic)
        fg.addRow("Distribution", self.f_dist)
        fg.addRow("Default subjects", self.f_subj)

        # Docling settings
        self.d_ocr = QCheckBox("OCR of areas without text")
        self.d_full = QCheckBox("Full-page OCR (scanned document)")
        self.d_lang = QLineEdit()
        self.d_lang.setPlaceholderText("en, fr")
        self.d_table = QComboBox()
        self.d_table.addItems(["accurate", "fast"])
        self.d_scale = QDoubleSpinBox()
        self.d_scale.setRange(0.5, 6.0)
        self.d_scale.setSingleStep(0.5)
        self.d_pics = QCheckBox("Classify figures (chart, drawing, photo…)")
        self.d_chart = QCheckBox("Read chart values (heavy model)")
        self.d_formula = QCheckBox("Recognize formulas")
        self.h_bm = QCheckBox("PDF bookmarks")
        self.h_num = QCheckBox("Numbering")
        self.h_style = QCheckBox("Heading style")
        self.h_max = QSpinBox()
        self.h_max.setRange(1, 12)
        self.h_kw = QPlainTextEdit()
        self.h_kw.setPlaceholderText("part: part\nchapter: chapter, section\narticle: article, art.")
        self.h_kw.setFixedHeight(90)
        self.h_schemes = QLineEdit()
        self.h_schemes.setPlaceholderText("division, part, chapter, subdivision, article, arabic…")
        docling = QGroupBox("Docling configuration")
        fd = QFormLayout(docling)
        fd.addRow(self.d_ocr)
        fd.addRow(self.d_full)
        fd.addRow("OCR languages", self.d_lang)
        fd.addRow("Tables", self.d_table)
        fd.addRow("Image scale", self.d_scale)
        fd.addRow(self.d_pics)
        fd.addRow(self.d_chart)
        fd.addRow(self.d_formula)
        sig = QHBoxLayout()
        for w in (self.h_bm, self.h_num, self.h_style):
            sig.addWidget(w)
        fd.addRow("Hierarchy sources", sig)
        fd.addRow("Maximum depth", self.h_max)
        fd.addRow("Numbering keywords", self.h_kw)
        fd.addRow("Level order", self.h_schemes)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(general)
        rl.addWidget(docling)
        rl.addStretch(1)

        body = QHBoxLayout()
        body.addLayout(left, 1)
        body.addWidget(right, 2)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Save")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancel")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer = QVBoxLayout(self)
        outer.addLayout(body, 1)
        outer.addWidget(buttons)
        self._refresh_list()

    # ----------------------------------------------------------------- list

    def _refresh_list(self, select: str | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for t in self.registry.types.values():
            item = QListWidgetItem(f"{t.label}  ({t.id})")
            item.setData(256, t.id)
            self.list.addItem(item)
        self.list.blockSignals(False)
        ids = list(self.registry.types)
        idx = ids.index(select) if select in ids else 0
        self._current = None
        self.list.setCurrentRow(idx)

    def _on_select(self, item, _prev) -> None:
        if self._current:
            self._store(self._current)
        if item is None:
            return
        self._current = item.data(256)
        self._load(self.registry.types[self._current])

    def _ask_id(self, title: str) -> str | None:
        tid, ok = QInputDialog.getText(self, title, "Id (letters, digits, -):")
        tid = (tid or "").strip().lower()
        if not ok or not tid:
            return None
        if tid in self.registry.types:
            QMessageBox.warning(self, title, f"The '{tid}' type already exists.")
            return None
        return tid

    def _new(self) -> None:
        tid = self._ask_id("New type")
        if tid:
            self._add(DocType(tid, tid.replace("-", " ").capitalize()))

    def _duplicate(self) -> None:
        if not self._current:
            return
        self._store(self._current)
        tid = self._ask_id("Duplicate type")
        if tid:
            t = copy.deepcopy(self.registry.types[self._current])
            t.id, t.label = tid, f"{t.label} (copy)"
            self._add(t)

    def _add(self, t: DocType) -> None:
        try:
            self.registry.add_type(t)
        except ValueError as exc:
            QMessageBox.warning(self, "Type", str(exc))
            return
        self._refresh_list(t.id)

    def _delete(self) -> None:
        if not self._current:
            return
        if self._current == DEFAULT_TYPE:
            QMessageBox.information(self, "Type", f"The '{DEFAULT_TYPE}' type is the default and stays.")
            return
        self.registry.remove_type(self._current)
        self._current = None
        self._refresh_list()

    # ----------------------------------------------------------------- form

    def _load(self, t: DocType) -> None:
        d, h = t.docling, t.docling.headings
        self.f_id.setText(t.id)
        self.f_label.setText(t.label)
        self.f_desc.setText(t.description)
        if self.f_cat.findText(t.category) < 0:
            self.f_cat.addItem(t.category)
        self.f_cat.setCurrentText(t.category)
        self.f_lic.setCurrentText(t.licence_status)
        self.f_dist.setCurrentText(t.distribution)
        self.f_subj.setText(", ".join(t.subjects))
        self.d_ocr.setChecked(d.ocr)
        self.d_full.setChecked(d.force_full_page_ocr)
        self.d_lang.setText(", ".join(d.ocr_lang))
        self.d_table.setCurrentText(d.table_mode)
        self.d_scale.setValue(d.images_scale)
        self.d_pics.setChecked(d.picture_classification)
        self.d_chart.setChecked(d.chart_data)
        self.d_formula.setChecked(d.formulas)
        self.h_bm.setChecked(h.use_bookmarks)
        self.h_num.setChecked(h.use_numbering)
        self.h_style.setChecked(h.use_style)
        self.h_max.setValue(h.max_level)
        self.h_kw.setPlainText(keywords_to_text(h.numbering_keywords))
        self.h_schemes.setText(", ".join(h.numbering_schemes or []))

    def _store(self, tid: str) -> None:
        t = self.registry.types.get(tid)
        if t is None:
            return
        d, h = t.docling, t.docling.headings
        t.label = self.f_label.text().strip() or t.id
        t.description = self.f_desc.text().strip()
        t.category = self.f_cat.currentText()
        t.licence_status = self.f_lic.currentText()
        t.distribution = self.f_dist.currentText()
        t.subjects = _split(self.f_subj.text())
        d.ocr = self.d_ocr.isChecked()
        d.force_full_page_ocr = self.d_full.isChecked()
        d.ocr_lang = _split(self.d_lang.text())
        d.table_mode = self.d_table.currentText()
        d.images_scale = self.d_scale.value()
        d.picture_classification = self.d_pics.isChecked()
        d.chart_data = self.d_chart.isChecked()
        d.formulas = self.d_formula.isChecked()
        h.use_bookmarks = self.h_bm.isChecked()
        h.use_numbering = self.h_num.isChecked()
        h.use_style = self.h_style.isChecked()
        h.max_level = self.h_max.value()
        h.numbering_keywords = text_to_keywords(self.h_kw.toPlainText())
        h.numbering_schemes = _split(self.h_schemes.text()) or None

    def _save(self) -> None:
        if self._current:
            self._store(self._current)
        self.registry.save()
        self.accept()
