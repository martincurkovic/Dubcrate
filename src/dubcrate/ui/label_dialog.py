"""Print index labels: pick discs, size the labels, preview the pages, print or save a PDF.

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
from PySide6.QtGui import QFontMetrics, QPdfWriter
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dubcrate import labels, settings
from dubcrate.library import Library
from dubcrate.library.models import DiscSummary
from dubcrate.ui import label_paint
from dubcrate.ui.label_preview import FIT, ZOOM_STEPS, PagePreview

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


def _advice(options: labels.LabelOptions) -> str:
    """What could help a list that is too long, given what is already allowed."""
    ideas = ["make the label taller"]
    if options.columns < labels.MAX_COLUMNS:
        ideas.append("allow more columns")
    if options.per_disc:
        ideas.append("spread the disc over more labels (or Auto)")
    if options.font_pt:
        ideas.append("lower the font size")
    return ideas[0] if len(ideas) == 1 else ", ".join(ideas[:-1]) + " or " + ideas[-1]


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
            kind = "  MP3" if disc.kind == "mp3" else ""
            item = QListWidgetItem(f"{paths[disc.id]}  ({disc.track_count}){kind}")
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
        self.per_disc_spin.setRange(0, labels.MAX_PER_DISC)
        self.per_disc_spin.setSpecialValueText("Auto")  # 0
        self.per_disc_spin.setValue(o.per_disc)
        self.per_disc_spin.setToolTip(
            "Spread each disc's track list over this many labels: one disc, several areas to stick them on."
            " Auto uses the fewest labels that keep the text readable (at least 6 pt, or the size you chose)."
        )
        self.columns_combo = QComboBox()
        for n in range(1, labels.MAX_COLUMNS + 1):
            self.columns_combo.addItem(str(n), n)
        self.columns_combo.setCurrentIndex(
            max(self.columns_combo.findData(o.columns), 0)
        )
        self.columns_combo.setToolTip(
            "Let a long list run in columns on a label, to need fewer labels. Each row becomes"
            " Title – Artist, so long names are cut short. A disc uses as few columns as will do."
        )
        self.copies_spin = QSpinBox()
        self.copies_spin.setRange(1, labels.MAX_COPIES)
        self.copies_spin.setValue(o.copies)
        self.copies_spin.setToolTip(
            "Print each disc's labels this many times: one set for each copy you burned."
        )
        self.margin_spin = self._mm_spin(o.margin_mm, 0, 30)
        self.gap_spin = self._mm_spin(o.gap_mm, 0, 20)
        self.bpm_check = QCheckBox("Show BPM")
        self.bpm_check.setChecked(o.show_bpm)
        self.bpm_check.setToolTip(
            "The tempo rounded to a whole number, from rekordbox: a track without one shows nothing."
        )
        self.key_check = QCheckBox("Show key")
        self.key_check.setChecked(o.show_key)
        self.key_check.setToolTip(
            "The musical key from rekordbox, written in the notation chosen in Settings: a track without one shows nothing."
        )
        self.duration_check = QCheckBox("Show duration")
        self.duration_check.setChecked(o.show_duration)
        self.duration_check.setToolTip(
            "The length of each track as minutes:seconds, the same as the Time column in the track list."
        )
        self.outline_check = QCheckBox("Draw a thin outline to cut along")
        self.outline_check.setChecked(o.outline)

        form = QFormLayout()
        form.addRow("Paper", self.page_combo)
        form.addRow("Label width (mm)", self.width_spin)
        form.addRow("Label height (mm)", self.height_spin)
        form.addRow("Font size", self.font_spin)
        form.addRow("Spread each disc over", self.per_disc_spin)
        form.addRow("Columns (up to)", self.columns_combo)
        form.addRow("Copies of each", self.copies_spin)
        form.addRow("Page margin (mm)", self.margin_spin)
        form.addRow("Gap between labels (mm)", self.gap_spin)
        form.addRow(self.bpm_check)
        form.addRow(self.key_check)
        form.addRow(self.duration_check)
        form.addRow(self.outline_check)

        # Always there, with room for their longest likely text, so a message appearing or going
        # never moves the controls above it while you edit.
        self.summary_label = self._message_label(lines=2)
        self.problems_label = self._message_label(lines=4)
        self.problems_label.setProperty("level", "critical")

        self.preview = PagePreview()
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItem("Fit page", FIT)
        for step in ZOOM_STEPS:
            self.zoom_combo.addItem(f"{step * 100:.0f}%", step)
        self._zoom_choices = self.zoom_combo.count()  # (a pinch can add one more)
        self.zoom_combo.setToolTip(
            "Zoom the preview (pinch on a trackpad, or Ctrl and the mouse wheel, do too). Drag the page to move it when zoomed in."
        )
        self.zoom_combo.currentIndexChanged.connect(self._zoom_box_changed)
        self.preview.zoom_changed.connect(self._zoom_to)
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
        left.addWidget(QLabel("Discs"))
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
        nav.addSpacing(
            self.zoom_combo.sizeHint().width() + 40
        )  # (keeps the page buttons centred)
        nav.addWidget(self.prev_button)
        nav.addWidget(self.page_label)
        nav.addWidget(self.next_button)
        nav.addStretch(1)
        nav.addWidget(QLabel("Zoom"))
        nav.addWidget(self.zoom_combo)
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
        for spin in (
            self.per_disc_spin,
            self.copies_spin,
        ):
            spin.valueChanged.connect(self.refresh)
        self.columns_combo.currentIndexChanged.connect(self.refresh)
        self.page_combo.currentIndexChanged.connect(self._page_size_changed)
        self.bpm_check.toggled.connect(self.refresh)
        self.key_check.toggled.connect(self.refresh)
        self.duration_check.toggled.connect(self.refresh)
        self.outline_check.toggled.connect(self.refresh)
        self.disc_list.itemChanged.connect(self.refresh)
        self.refresh()

    @staticmethod
    def _message_label(lines: int) -> QLabel:
        """A wrapping note that keeps `lines` lines of height whether or not it has any text."""
        label = QLabel()
        label.setObjectName("usageLabel")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        label.setMinimumHeight(QFontMetrics(label.font()).lineSpacing() * lines + 4)
        return label

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
            columns=self.columns_combo.currentData(),
            copies=self.copies_spin.value(),
            margin_mm=self.margin_spin.value(),
            gap_mm=self.gap_spin.value(),
            show_bpm=self.bpm_check.isChecked(),
            show_key=self.key_check.isChecked(),
            show_duration=self.duration_check.isChecked(),
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
                names[disc.id],
                self._lib.disc_entries(disc.id),
                options,
                notation,
                self._lib.disc_dirs(disc.id),
            )
            if made and not made[0].fits:
                problems.append(
                    f"“{disc.name}” has too many tracks for labels this small"
                    + (" at this font size" if options.font_pt else "")
                    + f": {_advice(options)}."
                )
            all_labels += made * options.copies

        sheet = labels.sheet_for(options)
        self._pages = labels.paginate(all_labels, sheet) if sheet else []
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
        shown = problems[:1] + (
            [f"…and {len(problems) - 1} more discs like it."]
            if len(problems) > 1
            else []
        )  # one at a time keeps it inside the space set aside
        self.problems_label.setText("\n".join(shown))
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
        """The current page (a blank one when there is nothing to print)."""
        page = self._pages[self._page] if self._pages else []
        self.preview.set_page(page, self.options())

    def _zoom_box_changed(self, _index: int) -> None:
        """A choice in the zoom box: zoom the preview, and drop the line a pinch made."""
        box = self.zoom_combo
        self.preview.set_zoom(box.currentData())
        if box.currentIndex() < self._zoom_choices and box.count() > self._zoom_choices:
            box.blockSignals(True)
            box.removeItem(self._zoom_choices)
            box.blockSignals(False)

    def _zoom_to(self, zoom: float) -> None:
        """The preview was zoomed by Ctrl + wheel or a pinch: show it in the zoom box too. A zoom
        that is not one of the choices gets a line of its own at the end ("137%")."""
        box = self.zoom_combo
        box.blockSignals(True)
        index = box.findData(zoom)
        if index >= 0 and index < self._zoom_choices:
            if box.count() > self._zoom_choices:
                box.removeItem(self._zoom_choices)
        else:
            if box.count() == self._zoom_choices:
                box.addItem("", zoom)
            box.setItemText(self._zoom_choices, f"{zoom * 100:.0f}%")
            box.setItemData(self._zoom_choices, zoom)
            index = self._zoom_choices
        box.setCurrentIndex(index)
        box.blockSignals(False)

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
