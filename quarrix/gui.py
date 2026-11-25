"""Quarrix window: drag and drop PDFs to extract them."""

from __future__ import annotations

import json
import sys
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from quarrix import __version__
from quarrix.config import Config, load_config, write_user_config
from quarrix.doctypes import DEFAULT_TYPE, Classification, load_registry
from quarrix.pipeline import Extractor
from quarrix.progress import CancelToken, Progress
from quarrix.writer import known_values


# Columns of the document list.
COLUMNS = ["Document", "Type", "Category", "Author", "Subjects", "Status", "Result"]
COL_DOC, COL_TYPE, COL_CAT, COL_AUTHOR, COL_SUBJECTS, COL_STATE, COL_RESULT = range(len(COLUMNS))


def classification_warnings(cls: Classification, previous: Classification | None) -> list[str]:
    """Reasons to warn before a drop: classification not set or unchanged."""
    warnings = []
    if not cls.type or cls.type == DEFAULT_TYPE:
        warnings.append(f"the document type is not chosen ('{DEFAULT_TYPE}')")
    if not cls.author:
        warnings.append("the author / publisher is empty")
    if previous is not None and cls.to_dict() == previous.to_dict():
        warnings.append("the classification is the same as for the previous drop")
    return warnings


def format_eta(seconds: float | None) -> str:
    if seconds is None:
        return "time remaining unknown"
    s = int(round(seconds))
    if s < 60:
        return f"about {max(s, 1)} s left"
    m, s = divmod(s, 60)
    if m < 60:
        return f"about {m} min {s:02d} s left"
    h, m = divmod(m, 60)
    return f"about {h} h {m:02d} min left"


class Worker(QObject):
    """Runs extractions one after the other in a separate thread."""

    started = Signal(int)
    progressed = Signal(int, float, float, str, bool)  # row, fraction, eta (-1 unknown), stage, estimated
    finished = Signal(int, bool, str, str, bool)  # row, ok, message, output, cancelled

    def __init__(self, cfg: Config):
        super().__init__()
        self.extractor = Extractor(cfg)
        self.cancel_token = CancelToken()

    def reset(self, cfg: Config) -> None:
        self.extractor.close()
        self.extractor = Extractor(cfg)

    def shutdown(self) -> None:
        self.cancel_token.cancel()
        self.extractor.close()

    @Slot(int, str, bool, str)
    def process(self, row: int, path: str, force: bool, classification: str) -> None:
        self.cancel_token.reset()
        self.started.emit(row)
        cls = Classification.from_dict(json.loads(classification)) if classification else None
        last = [0.0]

        def report(p: Progress) -> None:
            now = time.monotonic()
            if now - last[0] < 0.2 and p.fraction < 1.0:
                return  # limit the number of UI updates
            last[0] = now
            eta = -1.0 if p.eta_seconds is None else p.eta_seconds
            self.progressed.emit(row, p.fraction, eta, p.stage, p.estimated)

        r = self.extractor.run(Path(path), force=force, classification=cls,
                               progress=report, cancel=self.cancel_token)
        self.finished.emit(row, r.ok, r.message, str(r.output or ""), r.cancelled)


class DropZone(QFrame):
    dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(160)
        self.setObjectName("drop")
        self._label = QLabel("Drop your PDFs here\n(files or folders)")
        self._label.setAlignment(Qt.AlignCenter)
        lay = QVBoxLayout(self)
        lay.addWidget(self._label)
        self._style(False)

    def _style(self, active: bool) -> None:
        color = "#2b7de9" if active else "#9aa4b2"
        bg = "rgba(43,125,233,0.08)" if active else "transparent"
        self.setStyleSheet(
            f"#drop {{ border: 2px dashed {color}; border-radius: 12px; background: {bg}; }}"
            "QLabel { font-size: 16px; color: palette(text); }"
        )

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._style(True)

    def dragLeaveEvent(self, e) -> None:
        self._style(False)

    def dropEvent(self, e: QDropEvent) -> None:
        self._style(False)
        paths = []
        for url in e.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_dir():
                paths.extend(sorted({q for q in p.rglob("*") if q.suffix.lower() == ".pdf"}))
            elif p.suffix.lower() == ".pdf":
                paths.append(p)
        if paths:
            self.dropped.emit(paths)
        e.acceptProposedAction()


class ClassificationPanel(QGroupBox):
    """Classification applied to the next PDFs dropped."""

    def __init__(self, window: "MainWindow"):
        super().__init__("Classification of the next PDFs")
        self.window = window
        self.type = QComboBox()
        self.type.currentIndexChanged.connect(self._type_changed)
        self.category = QComboBox()
        self.author = QComboBox()
        self.author.setEditable(True)
        self.author.lineEdit().setPlaceholderText("e.g. Example Standards Institute")
        self.distributor = QComboBox()
        self.distributor.setEditable(True)
        self.distributor.lineEdit().setPlaceholderText("optional")
        self.subjects = QLineEdit()
        self.subjects.setPlaceholderText("comma-separated subjects")
        btn_types = QPushButton("Manage types…")
        btn_types.clicked.connect(window.manage_types)

        form = QFormLayout()
        form.addRow("Type", self.type)
        form.addRow("Category", self.category)
        right = QFormLayout()
        right.addRow("Author / publisher", self.author)
        right.addRow("Distributor", self.distributor)
        outer = QVBoxLayout(self)
        cols = QHBoxLayout()
        cols.addLayout(form, 1)
        cols.addLayout(right, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Subjects"))
        bottom.addWidget(self.subjects, 1)
        bottom.addWidget(btn_types)
        outer.addLayout(cols)
        outer.addLayout(bottom)
        self.reload()

    def reload(self) -> None:
        reg = self.window.registry
        current = self.type.currentData()
        self.type.blockSignals(True)
        self.type.clear()
        for t in reg.types.values():
            self.type.addItem(t.label, t.id)
            self.type.setItemData(self.type.count() - 1, t.description, Qt.ToolTipRole)
        self.type.blockSignals(False)
        cats = self.category.currentText()
        self.category.clear()
        for cid, label in reg.categories.items():
            self.category.addItem(cid, cid)
            self.category.setItemData(self.category.count() - 1, label, Qt.ToolTipRole)
        known = known_values(self.window.cfg)
        for combo, key in ((self.author, "author"), (self.distributor, "distributor")):
            text = combo.currentText()
            combo.clear()
            combo.addItems([""] + known[key])
            combo.setCurrentText(text)
        words = sorted(set(reg.subjects) | set(known["subjects"]))
        comp = QCompleter(words, self)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        self.subjects.setCompleter(comp)
        idx = self.type.findData(current) if current else -1
        self.type.setCurrentIndex(idx if idx >= 0 else max(0, self.type.findData(DEFAULT_TYPE)))
        if cats:
            self.category.setCurrentText(cats)
        self._type_changed()

    def _type_changed(self) -> None:
        t = self.window.registry.get(self.type.currentData())
        self.category.setCurrentText(t.category)
        if t.subjects and not self.subjects.text().strip():
            self.subjects.setText(", ".join(t.subjects))

    def classification(self) -> Classification:
        subjects = [s.strip() for s in self.subjects.text().replace(";", ",").split(",") if s.strip()]
        return Classification(
            category=self.category.currentText(),
            author=self.author.currentText().strip(),
            distributor=self.distributor.currentText().strip(),
            type=self.type.currentData() or "",
            subjects=subjects,
        )


class MainWindow(QMainWindow):
    request = Signal(int, str, bool, str)

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle(f"Quarrix {__version__}")
        self.resize(1100, 700)
        self.registry = load_registry(cfg.output_dir)
        self.panel = ClassificationPanel(self)

        self.drop = DropZone()
        self.drop.dropped.connect(self.enqueue)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        header = self.table.horizontalHeader()
        for col in range(len(COLUMNS) - 1):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_RESULT, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self.open_row)

        self.root_label = QLabel()
        self.root_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        btn_root = QPushButton("Choose…")
        btn_root.setToolTip("Choose the folder where extractions are written")
        btn_root.clicked.connect(self.choose_root)
        btn_open = QPushButton("Open folder")
        btn_open.clicked.connect(self.open_output)
        btn_add = QPushButton("Add PDFs…")
        btn_add.clicked.connect(self.add_files)

        self.engine = QComboBox()
        self.engine.addItems(["auto", "docling", "pymupdf"])
        self.engine.setCurrentText(cfg.engine)
        self.engine.currentTextChanged.connect(self.change_engine)
        self.force = QCheckBox("Re-extract if already done")

        top = QHBoxLayout()
        top.addWidget(QLabel("Output folder:"))
        top.addWidget(self.root_label, 1)
        top.addWidget(btn_root)
        top.addWidget(btn_open)

        bottom = QHBoxLayout()
        bottom.addWidget(btn_add)
        bottom.addStretch(1)
        bottom.addWidget(self.force)
        bottom.addWidget(QLabel("Engine:"))
        bottom.addWidget(self.engine)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addLayout(top)
        lay.addWidget(self.panel)
        lay.addWidget(self.drop)
        lay.addWidget(self.table, 1)
        lay.addLayout(self._build_progress_row())
        lay.addLayout(bottom)
        self.setCentralWidget(central)

        self.thread = QThread(self)
        self.worker = Worker(cfg)
        self.worker.moveToThread(self.thread)
        self.request.connect(self.worker.process)
        self.worker.started.connect(self._on_started)
        self.worker.progressed.connect(self._on_progress)
        self.worker.finished.connect(self.done)
        self.thread.start()
        self._outputs: dict[int, str] = {}
        self._pending: deque[tuple[int, str, bool, str]] = deque()
        self._current: int | None = None
        self._done_count = 0
        self._batch_total = 0
        self._last_classification: Classification | None = None
        self.warn_classification = True  # can be turned off from the warning ("don't ask again")
        self._set_busy(False)
        self._refresh_root()

    # ---------------------------------------------------------- progress

    def _build_progress_row(self) -> QHBoxLayout:
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFormat("%p%")
        self.progress.setTextVisible(True)
        self.progress_label = QLabel("Ready")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setToolTip("Stop the current document")
        self.btn_cancel.clicked.connect(self.cancel_current)
        self.btn_cancel_all = QPushButton("Cancel all")
        self.btn_cancel_all.setToolTip("Stop the current document and clear the queue")
        self.btn_cancel_all.clicked.connect(self.cancel_all)
        row = QHBoxLayout()
        col = QVBoxLayout()
        col.addWidget(self.progress)
        col.addWidget(self.progress_label)
        row.addLayout(col, 1)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_cancel_all)
        return row

    def _set_busy(self, busy: bool) -> None:
        self.btn_cancel.setEnabled(busy)
        self.btn_cancel_all.setEnabled(busy or bool(self._pending))
        # Changing engine or folder during an extraction makes no sense.
        self.engine.setEnabled(not busy)
        if not busy and not self._pending:
            self.progress.setValue(0 if self._batch_total == 0 else 1000)
            if self._batch_total:
                self.progress_label.setText(
                    f"Done: {self._done_count}/{self._batch_total} document(s)")
            self._batch_total = self._done_count = 0

    def _queue_text(self) -> str:
        position = self._done_count + 1
        waiting = len(self._pending)
        text = f"Document {position}/{self._batch_total}"
        return text + (f" · {waiting} waiting" if waiting else "")

    def _on_started(self, row: int) -> None:
        self._set(row, COL_STATE, "Running… 0%")
        self.progress.setValue(0)
        self.progress_label.setText(f"{self._queue_text()} · starting…")

    def _on_progress(self, row: int, frac: float, eta: float, stage: str, estimated: bool) -> None:
        pct = int(frac * 100)
        self.progress.setValue(int(frac * 1000))
        self._set(row, COL_STATE, f"Running… {pct}%" + (" (est.)" if estimated else ""))
        parts = [self._queue_text(), stage, format_eta(None if eta < 0 else eta)]
        if estimated:
            parts.append("estimate")
        self.progress_label.setText(" · ".join(p for p in parts if p))

    def _start_next(self) -> None:
        if self._current is not None or not self._pending:
            self._set_busy(self._current is not None)
            return
        row, path, force, cls = self._pending.popleft()
        self._current = row
        self._set_busy(True)
        self.request.emit(row, path, force, cls)

    def cancel_current(self) -> None:
        if self._current is not None:
            self.progress_label.setText("Cancelling…")
            self.worker.cancel_token.cancel()

    def cancel_all(self) -> None:
        while self._pending:
            row, *_ = self._pending.popleft()
            self._set(row, COL_STATE, "Cancelled")
            self._batch_total -= 1
        self.cancel_current()
        self._set_busy(self._current is not None)

    def _refresh_root(self) -> None:
        if self.cfg.output_dir is None:
            self.root_label.setText("<i>none: click \"Choose…\" or drop a PDF</i>")
            self.statusBar().showMessage("Choose the folder where extractions are written.")
            return
        self.root_label.setText(str(self.cfg.output_dir))
        self.statusBar().showMessage(
            f"Output: {self.cfg.sources_dir}" + ("" if self.cfg.output_dir.exists() else "  (will be created)")
        )

    def ensure_output(self) -> bool:
        """Ask for the output folder if it has not been chosen yet."""
        return self.cfg.output_dir is not None or self.choose_root()

    def open_output(self) -> None:
        if self.ensure_output():
            self._open(self.cfg.sources_dir)

    def _set(self, row: int, col: int, text: str) -> None:
        self.table.setItem(row, col, QTableWidgetItem(text))

    def confirm_classification(self, cls: Classification, count: int) -> bool:
        """Warn if the classification is not set or has not changed; True to continue."""
        if not self.warn_classification:
            return True
        warnings = classification_warnings(cls, self._last_classification)
        if not warnings:
            return True
        return self.ask_classification(cls, warnings, count)

    def ask_classification(self, cls: Classification, warnings: list[str], count: int) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Check the classification")
        what = "this PDF" if count == 1 else f"these {count} PDFs"
        box.setText(f"Check the classification before dropping {what}:\n\n"
                    + "\n".join(f"• {w[0].upper()}{w[1:]}." for w in warnings))
        box.setInformativeText(f"Classification that will be applied:\n{self._describe(cls)}")
        go = box.addButton("Drop anyway", QMessageBox.AcceptRole)
        box.addButton("Edit classification", QMessageBox.RejectRole)
        box.setDefaultButton(go)
        again = QCheckBox("Don't ask again this session")
        box.setCheckBox(again)
        box.exec()
        if box.clickedButton() is not go:
            self.panel.setFocus()
            return False
        if again.isChecked():
            self.warn_classification = False
        return True

    def _describe(self, cls: Classification) -> str:
        t = self.registry.get(cls.type)
        lines = [f"Type: {t.label}", f"Category: {cls.category or '—'}",
                 f"Author: {cls.author or '—'}"]
        if cls.distributor:
            lines.append(f"Distributor: {cls.distributor}")
        lines.append(f"Subjects: {', '.join(cls.subjects) or '—'}")
        return "\n".join(lines)

    def _show_classification(self, row: int, cls: Classification) -> None:
        label = self.registry.get(cls.type).label if cls.type else ""
        author = cls.author + (f" / {cls.distributor}" if cls.distributor else "")
        for col, text in ((COL_TYPE, label), (COL_CAT, cls.category), (COL_AUTHOR, author),
                          (COL_SUBJECTS, ", ".join(cls.subjects))):
            item = QTableWidgetItem(text)
            item.setToolTip(self._describe(cls))
            self.table.setItem(row, col, item)

    @Slot(list)
    def enqueue(self, paths: list[Path]) -> None:
        if not paths or not self.ensure_output():
            return
        classification = self.panel.classification()
        if not self.confirm_classification(classification, len(paths)):
            self.statusBar().showMessage("Drop cancelled: adjust the classification, then drop the PDFs again.", 8000)
            return
        self._last_classification = classification
        cls = json.dumps(classification.to_dict(), ensure_ascii=False)
        for p in paths:
            row = self.table.rowCount()
            self.table.insertRow(row)
            item = QTableWidgetItem(p.name)
            item.setToolTip(str(p))
            self.table.setItem(row, COL_DOC, item)
            self._show_classification(row, classification)
            self._set(row, COL_STATE, "Waiting")
            self._pending.append((row, str(p), self.force.isChecked(), cls))
            self._batch_total += 1
        self._start_next()

    @Slot(int, bool, str, str, bool)
    def done(self, row: int, ok: bool, message: str, output: str, cancelled: bool = False) -> None:
        self._set(row, COL_STATE, "Cancelled" if cancelled else ("Done" if ok else "Error"))
        self._set(row, COL_RESULT, message)
        self._current = None
        if cancelled:
            self._batch_total -= 1
        else:
            self._done_count += 1
        self._start_next()
        if output:
            self._outputs[row] = output
            self.table.item(row, COL_RESULT).setToolTip(f"{output}\nDouble-click to open")

    def open_row(self, row: int, _col: int) -> None:
        if row in self._outputs:
            self._open(Path(self._outputs[row]))

    def add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Choose PDFs", "", "PDF (*.pdf)")
        if files:
            self.enqueue([Path(f) for f in files])

    def manage_types(self) -> None:
        from quarrix.types_dialog import TypesDialog

        if not self.ensure_output():  # types are saved in the output folder
            return
        dlg = TypesDialog(self.registry, self)
        if dlg.exec():
            self.registry = load_registry(self.cfg.output_dir)
            self.worker.reset(self.cfg)
            self.panel.reload()

    def choose_root(self) -> bool:
        start = str(self.cfg.output_dir or Path.home())
        d = QFileDialog.getExistingDirectory(self, "Output folder for extractions", start)
        if not d:
            return False
        self.cfg.output_dir = Path(d)
        write_user_config(self.cfg.output_dir)
        self.registry = load_registry(self.cfg.output_dir)
        self.worker.reset(self.cfg)
        self.panel.reload()
        self._refresh_root()
        return True

    def change_engine(self, name: str) -> None:
        self.cfg.engine = name
        self.worker.reset(self.cfg)

    @staticmethod
    def _open(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def closeEvent(self, e) -> None:
        self._pending.clear()
        self.worker.shutdown()
        self.thread.quit()
        self.thread.wait(2000)
        super().closeEvent(e)


def run_gui(cfg: Config | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Quarrix")
    win = MainWindow(cfg or load_config())
    win.show()
    return app.exec()


def main() -> int:
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
