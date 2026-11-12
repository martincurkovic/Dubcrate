"""Print index labels: pick audio discs, size the labels, preview the pages, print or save a PDF.

Several discs go on the same pages (labels are packed one after another, left to right and top
to bottom) so a sheet of sticker paper isn't wasted on a disc or two. The track text comes from
`dubcrate.labels`, the drawing from `label_paint`, and the same painter feeds the preview, the
printer and the PDF.

`ask_printer` and `ask_pdf_path` are the only places a real print panel or file chooser opens,
so tests patch them (`conftest.py` refuses them otherwise).
"""

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPdfWriter, QPixmap
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dubcrate import labels, settings
from dubcrate.library import Library
from dubcrate.library.models import DiscSummary
from dubcrate.ui import label_paint

logger = logging.getLogger(__name__)


def ask_printer(printer: QPrinter, parent: QWidget | None) -> bool:
    """Show the system's print panel for `printer`; True if the user pressed Print."""
    return QPrintDialog(printer, parent).exec() == QDialog.DialogCode.Accepted


def ask_pdf_path(parent: QWidget | None) -> str:
    """Ask where to save the PDF; "" if cancelled."""
    path, _filter = QFileDialog.getSaveFileName(
        parent,
        "Save Labels as PDF",
        str(Path.home() / "Dubcrate labels.pdf"),
        "PDF (*.pdf)",
    )
    return path


def disc_paths(lib: Library) -> dict[int, str]:
    """Each disc's id and its name with the folders it is in: "Sets / Friday / Opening"."""
    folders = {f.id: f for f in lib.disc_folders()}

    def folder_path(folder_id: int | None) -> list[str]:
        names = []
        while folder_id is not None:
            folder = folders[folder_id]
            names.append(folder.name)
            folder_id = folder.parent_id
        return names[::-1]

    return {d.id: " / ".join([*folder_path(d.folder_id), d.name]) for d in lib.discs()}


class LabelDialog(QDialog):
    def __init__(self, lib: Library, parent=None, picked: int | None = None):
        super().__init__(parent)
        self._lib = lib
        self._pages: list[list[labels.Placed]] = []
        self._page = 0
        self._options = settings.get_label_options()
        self.setWindowTitle("Print Labels")
        self.resize(1000, 720)

        self.disc_list = QListWidget()
        self.disc_list.setMinimumWidth(240)
        paths = disc_paths(lib)
        for disc in lib.discs():
            if disc.kind != "audio":
                continue  # an MP3 CD holds far too many tracks to list on a label
            item = QListWidgetItem(f"{paths[disc.id]}  ({disc.track_count})")
            item.setData(Qt.ItemDataRole.UserRole, disc.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            on = disc.id == picked and disc.track_count > 0
            item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            self.disc_list.addItem(item)
        all_button = QPushButton("All")
        none_button = QPushButton("None")
        all_button.clicked.connect(lambda: self._tick_all(Qt.CheckState.Checked))
        none_button.clicked.connect(lambda: self._tick_all(Qt.CheckState.Unchecked))

        o = self._options
        self.page_combo = QComboBox()
        for name, (w, h) in labels.PAGE_SIZES.items():
            self.page_combo.addItem(f"{name} ({w:g} × {h:g} mm)", name)
        self.page_combo.setCurrentIndex(max(self.page_combo.findData(o.page), 0))
        self.page_combo.setToolTip("The paper in the printer.")
        page_w, page_h = o.page_mm
        self.width_spin = self._mm_spin(o.width_mm, labels.MIN_LABEL_MM, page_w)
        self.height_spin = self._mm_spin(o.height_mm, labels.MIN_LABEL_MM, page_h)
        self.font_spin = QDoubleSpinBox()
        self.font_spin.setDecimals(1)
        self.font_spin.setSingleStep(0.5)
        self.font_spin.setRange(0, labels.MAX_FONT_PT)
        self.font_spin.setSuffix(" pt")
        self.font_spin.setSpecialValueText("Auto")  # 0
        self.font_spin.setValue(o.font_pt)
        self.font_spin.setToolTip(
            "Auto makes the text as large as fits, up to a limit. Choose a size to fix it: a list that"
            " is too long for the label at that size is flagged."
        )
        self._last_font = self.font_spin.value()
        self.per_disc_spin = QSpinBox()
        self.per_disc_spin.setRange(1, labels.MAX_PER_DISC)
        self.per_disc_spin.setValue(o.per_disc)
        self.per_disc_spin.setToolTip(
            "Spread each disc's track list over this many labels: one disc, several areas to stick them on."
        )
        self.copies_spin = QSpinBox()
        self.copies_spin.setRange(1, labels.MAX_COPIES)
        self.copies_spin.setValue(o.copies)
        self.copies_spin.setToolTip(
            "Print each disc's labels this many times: one set for each copy you burned."
        )
        self.margin_spin = self._mm_spin(o.margin_mm, 0, 30)
        self.gap_spin = self._mm_spin(o.gap_mm, 0, 20)
        self.skip_spin = QSpinBox()
        self.skip_spin.setRange(0, 200)
        self.skip_spin.setToolTip(
            "Leave this many places empty at the start of the first page, for a sheet you have already cut labels from."
        )
        self.bpm_key_check = QCheckBox("Show BPM and key")
        self.bpm_key_check.setChecked(o.show_bpm_key)
        self.bpm_key_check.setToolTip(
            "Only tracks that have them (from rekordbox) show a value. The key is written in the notation chosen in Settings."
        )
        self.outline_check = QCheckBox("Draw a thin outline to cut along")
        self.outline_check.setChecked(o.outline)

        form = QFormLayout()
        form.addRow("Paper", self.page_combo)
        form.addRow("Label width (mm)", self.width_spin)
        form.addRow("Label height (mm)", self.height_spin)
        form.addRow("Font size", self.font_spin)
        form.addRow("Spread each disc over", self.per_disc_spin)
        form.addRow("Copies of each", self.copies_spin)
        form.addRow("Page margin (mm)", self.margin_spin)
        form.addRow("Gap between labels (mm)", self.gap_spin)
        form.addRow("Places already used", self.skip_spin)
        form.addRow(self.bpm_key_check)
        form.addRow(self.outline_check)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("usageLabel")
        self.summary_label.setWordWrap(True)
        self.problems_label = QLabel()
        self.problems_label.setObjectName("usageLabel")
        self.problems_label.setProperty("level", "critical")
        self.problems_label.setWordWrap(True)

        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(280, 400)
        # sized by the window, not by the picture in it: the page is redrawn to fit (resizeEvent)
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        self.prev_button = QPushButton("◀")
        self.next_button = QPushButton("▶")
        self.page_label = QLabel()
        self.prev_button.clicked.connect(lambda: self._show_page(self._page - 1))
        self.next_button.clicked.connect(lambda: self._show_page(self._page + 1))

        self.print_button = QPushButton("Print…")
        self.print_button.setDefault(True)
        self.print_button.clicked.connect(self.print_labels)
        self.pdf_button = QPushButton("Save PDF…")
        self.pdf_button.clicked.connect(self.save_pdf)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)

        left = QVBoxLayout()
        left.addWidget(QLabel("Discs (audio CDs)"))
        left.addWidget(self.disc_list, 1)
        pick_row = QHBoxLayout()
        pick_row.addWidget(all_button)
        pick_row.addWidget(none_button)
        pick_row.addStretch(1)
        left.addLayout(pick_row)
        left.addLayout(form)
        left.addWidget(self.summary_label)
        left.addWidget(self.problems_label)

        nav = QHBoxLayout()
        nav.addStretch(1)
        nav.addWidget(self.prev_button)
        nav.addWidget(self.page_label)
        nav.addWidget(self.next_button)
        nav.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.pdf_button)
        buttons.addWidget(self.print_button)
        buttons.addWidget(close_button)
        right = QVBoxLayout()
        right.addWidget(self.preview, 1)
        right.addLayout(nav)
        right.addLayout(buttons)

        layout = QHBoxLayout(self)
        layout.addLayout(left)
        layout.addLayout(right, 1)

        for spin in (
            self.width_spin,
            self.height_spin,
            self.margin_spin,
            self.gap_spin,
        ):
            spin.valueChanged.connect(self.refresh)
        self.font_spin.valueChanged.connect(self._font_changed)
        for spin in (self.per_disc_spin, self.copies_spin, self.skip_spin):
            spin.valueChanged.connect(self.refresh)
        self.page_combo.currentIndexChanged.connect(self._page_size_changed)
        self.bpm_key_check.toggled.connect(self.refresh)
        self.outline_check.toggled.connect(self.refresh)
        self.disc_list.itemChanged.connect(self.refresh)
        self.refresh()

    @staticmethod
    def _mm_spin(value: float, low: float, high: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(1)
        spin.setSingleStep(1.0)
        spin.setValue(value)
        return spin

    def _font_changed(self, value: float) -> None:
        """Sizes below the smallest are skipped: up from Auto goes to the smallest size, and down
        from it goes to Auto."""
        if 0 < value < labels.MIN_FONT_PT:
            value = labels.MIN_FONT_PT if value > self._last_font else 0.0
            self.font_spin.setValue(value)  # (comes back here, then refreshes)
            return
        self._last_font = value
        self.refresh()

    def _page_size_changed(self, *_args) -> None:
        """Labels can't be bigger than the paper: the width and height limits follow it."""
        page_w, page_h = labels.PAGE_SIZES[self.page_combo.currentData()]
        self.width_spin.setMaximum(page_w)  # (a value above the new limit is lowered)
        self.height_spin.setMaximum(page_h)
        self.refresh()

    def _tick_all(self, state: Qt.CheckState) -> None:
        self.disc_list.blockSignals(True)
        for i in range(self.disc_list.count()):
            item = self.disc_list.item(i)
            if state == Qt.CheckState.Unchecked or self._track_count(item) > 0:
                item.setCheckState(state)
        self.disc_list.blockSignals(False)
        self.refresh()

    def _disc_id(self, item: QListWidgetItem) -> int:
        return item.data(Qt.ItemDataRole.UserRole)

    def _track_count(self, item: QListWidgetItem) -> int:
        return self._lib.disc(self._disc_id(item)).track_count

    def ticked_discs(self) -> list[DiscSummary]:
        return [
            self._lib.disc(self._disc_id(self.disc_list.item(i)))
            for i in range(self.disc_list.count())
            if self.disc_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def options(self) -> labels.LabelOptions:
        return labels.LabelOptions(
            page=self.page_combo.currentData(),
            font_pt=self.font_spin.value(),
            width_mm=self.width_spin.value(),
            height_mm=self.height_spin.value(),
            per_disc=self.per_disc_spin.value(),
            copies=self.copies_spin.value(),
            margin_mm=self.margin_spin.value(),
            gap_mm=self.gap_spin.value(),
            show_bpm_key=self.bpm_key_check.isChecked(),
            outline=self.outline_check.isChecked(),
        ).clamped()

    def refresh(self, *_args) -> None:
        """Re-plan the pages from the current choices and redraw the preview."""
        options = self.options()
        notation = settings.get_key_notation()
        all_labels: list[labels.Label] = []
        problems: list[str] = []
        names = disc_paths(self._lib)
        for disc in self.ticked_discs():
            made = labels.build_labels(
                names[disc.id], self._lib.disc_entries(disc.id), options, notation
            )
            if made and not made[0].fits:
                problems.append(
                    f"“{disc.name}” has too many tracks for a label this small"
                    + (" at this font size" if options.font_pt else "")
                    + ": make the label taller, spread the disc over more labels"
                    + (", or lower the font size." if options.font_pt else ".")
                )
            all_labels += made * options.copies

        sheet = labels.sheet_for(options)
        self._pages = (
            labels.paginate(all_labels, sheet, self.skip_spin.value()) if sheet else []
        )
        if sheet is None:
            problems.append(
                "A label that size does not fit on the paper with those margins."
            )
            summary = ""
        elif not all_labels:
            summary = f"{sheet.per_page} labels fit on a page. Tick the discs to print."
        else:
            summary = (
                f"{len(all_labels)} label{'s' if len(all_labels) != 1 else ''} on "
                f"{len(self._pages)} page{'s' if len(self._pages) != 1 else ''} "
                f"({sheet.columns} × {sheet.rows} = {sheet.per_page} to a page)."
            )
            if (
                not options.font_pt
            ):  # say what Auto came to, as a start for choosing one
                smallest = min(label.font_mm for label in all_labels)
                summary += f" Text is {smallest / labels.MM_PER_PT:.1f} pt."
        self.summary_label.setText(summary)
        self.problems_label.setText("\n".join(problems))
        self.problems_label.setVisible(bool(problems))
        can_print = bool(self._pages)
        self.print_button.setEnabled(can_print)
        self.pdf_button.setEnabled(can_print)
        self._show_page(min(self._page, max(len(self._pages) - 1, 0)))

    def _show_page(self, index: int) -> None:
        self._page = max(0, min(index, len(self._pages) - 1)) if self._pages else 0
        total = len(self._pages)
        self.prev_button.setEnabled(self._page > 0)
        self.next_button.setEnabled(self._page < total - 1)
        self.page_label.setText(f"Page {self._page + 1} of {total}" if total else "")
        self._draw_preview()

    def _draw_preview(self) -> None:
        """The current page (a blank one when there is nothing to print), as big as the pane allows."""
        page = self._pages[self._page] if self._pages else []
        room = self.preview.size()
        options = self.options()
        page_w, page_h = options.page_mm
        dpi = min(
            (room.width() - 4) * 25.4 / page_w, (room.height() - 4) * 25.4 / page_h
        )
        image = label_paint.page_image(page, options, max(int(dpi), 20))
        self.preview.setPixmap(QPixmap.fromImage(image))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._draw_preview()

    # -- output -----------------------------------------------------------

    def _remember(self) -> None:
        settings.set_label_options(self.options())

    def save_pdf(self) -> None:
        path = ask_pdf_path(self)
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        options = self.options()
        writer = QPdfWriter(path)
        writer.setPageLayout(label_paint.page_layout(options))
        writer.setTitle("Dubcrate index labels")
        if not label_paint.write_pages(writer, self._pages, options):
            logger.warning("could not write the labels PDF to %s", path)
            QMessageBox.warning(self, "Print Labels", f"Could not write {path}.")
            return
        self._remember()
        logger.info("saved %d page(s) of labels to %s", len(self._pages), path)

    def print_labels(self) -> None:
        options = self.options()
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageLayout(label_paint.page_layout(options))
        printer.setDocName("Dubcrate index labels")
        if not ask_printer(printer, self):
            return
        # the print panel lets the paper be changed, but the labels were laid out for this one
        printer.setPageLayout(label_paint.page_layout(options))
        if not label_paint.write_pages(printer, self._pages, options):
            logger.warning("the printer would not start a labels job")
            QMessageBox.warning(
                self, "Print Labels", "The printer could not start the job."
            )
            return
        self._remember()
        logger.info("printed %d page(s) of labels", len(self._pages))

    def done(self, result: int) -> None:
        self._remember()
        super().done(result)
