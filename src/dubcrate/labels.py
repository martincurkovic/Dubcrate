"""Index labels for a CD case, printed on sticker paper (A4, Letter, ...) (pure: no Qt, no printer).

A disc's track list is turned into `Label`s: the tracks are spread over `per_disc` labels (a
case with two areas to stick on, say), each with the disc's name as a heading and a row per
track: number, title, artist, and the approximate BPM and key when the library knows them.
`sheet_for` works out how many labels of the chosen size fit on a page, and `paginate`
places labels from several discs on the same pages so little sticker paper is wasted.

Only audio discs get labels: an MP3 CD holds too many tracks to list. What is printed for a
track is the disc's own text (`DiscEntry.title` / `.artist`), as on the CD, never the library's.
Sizes are millimetres.
"""

from dataclasses import dataclass, replace

from dubcrate import keys
from dubcrate.library.models import DiscEntry

# Paper the labels can be printed on: name -> (width, height) in millimetres, portrait.
PAGE_SIZES: dict[str, tuple[float, float]] = {
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "A6": (105.0, 148.0),
    "Letter": (215.9, 279.4),  # US
    "Legal": (215.9, 355.6),  # US
}
DEFAULT_PAGE = "A4"

MIN_LABEL_MM = 20.0
MAX_PER_DISC = 8
MAX_COPIES = 10

PADDING_MM = 2.0  # blank edge inside a label
MAX_FONT_MM = 4.2  # about 12 pt: short lists are not blown up
MIN_FONT_MM = 1.9  # about 5.4 pt: smaller than this is not readable
MIN_FONT_PT = 4.0  # the smallest size that can be chosen by hand
MAX_FONT_PT = 24.0
MM_PER_PT = 25.4 / 72
HEADING_ROWS = 1.5  # the heading is this many text lines tall
ROW_SPACING = 1.25  # a text line is this many font heights tall


@dataclass(frozen=True)
class LabelOptions:
    """What the user chooses in the print window (and what is remembered between runs)."""

    page: str = DEFAULT_PAGE  # a key of PAGE_SIZES
    font_pt: float = (
        0.0  # text size in points; 0: as large as fits (see `font_size_mm`)
    )
    width_mm: float = 100.0
    height_mm: float = 50.0
    per_disc: int = 1  # labels a disc's track list is spread over
    copies: int = (
        1  # how many times each disc's labels are printed (one per copy burned)
    )
    margin_mm: float = 10.0  # unprintable edge of the page
    gap_mm: float = 2.0  # between labels, to cut along
    show_bpm_key: bool = True
    outline: bool = True  # a thin line round each label, to cut along

    @property
    def page_mm(self) -> tuple[float, float]:
        """(width, height) of the paper in millimetres."""
        return PAGE_SIZES.get(self.page, PAGE_SIZES[DEFAULT_PAGE])

    def clamped(self) -> "LabelOptions":
        """The same options with every number put inside what makes sense: a label is never
        bigger than the page, and an unknown paper is A4."""
        page = self.page if self.page in PAGE_SIZES else DEFAULT_PAGE
        page_w, page_h = PAGE_SIZES[page]
        return replace(
            self,
            page=page,
            font_pt=0.0
            if self.font_pt <= 0
            else _clamp(self.font_pt, MIN_FONT_PT, MAX_FONT_PT),
            width_mm=_clamp(self.width_mm, MIN_LABEL_MM, page_w),
            height_mm=_clamp(self.height_mm, MIN_LABEL_MM, page_h),
            per_disc=int(_clamp(self.per_disc, 1, MAX_PER_DISC)),
            copies=int(_clamp(self.copies, 1, MAX_COPIES)),
            margin_mm=_clamp(self.margin_mm, 0.0, 30.0),
            gap_mm=_clamp(self.gap_mm, 0.0, 20.0),
        )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class LabelRow:
    number: int  # the track's number on the disc, from 1
    title: str
    artist: str
    bpm: str = ""  # whole beats per minute, "" when unknown
    key: str = ""  # in the notation chosen in Settings, "" when unknown


@dataclass(frozen=True)
class Label:
    heading: str  # the disc's name, with "1/2" when spread over several labels
    rows: tuple[LabelRow, ...]
    font_mm: float  # the same for every label of a disc
    fits: bool  # False: even the smallest readable text does not fit the height

    @property
    def has_bpm(self) -> bool:
        return any(row.bpm for row in self.rows)

    @property
    def has_key(self) -> bool:
        return any(row.key for row in self.rows)


@dataclass(frozen=True)
class Placed:
    """A label on a page, its top-left corner in millimetres from the page's."""

    x_mm: float
    y_mm: float
    label: Label


def bpm_text(bpm: float | None) -> str:
    """The BPM rounded to a whole number, "" when there is none."""
    return str(round(bpm)) if bpm and bpm > 0 else ""


def split_evenly[T](items: list[T], parts: int) -> list[list[T]]:
    """`items` in order as at most `parts` runs of near-equal length (earlier runs are the longer),
    never an empty run."""
    parts = max(1, min(parts, len(items)))
    size, extra = divmod(len(items), parts)
    runs, start = [], 0
    for i in range(parts):
        end = start + size + (1 if i < extra else 0)
        runs.append(items[start:end])
        start = end
    return runs


def room_mm(rows: int, height_mm: float) -> float:
    """The biggest text height at which `rows` track rows and the heading fit a label of `height_mm`."""
    lines = HEADING_ROWS + ROW_SPACING * max(rows, 1)
    return (height_mm - 2 * PADDING_MM) / lines


def font_size_mm(rows: int, height_mm: float) -> float:
    """The automatic text height: what fills the label, at most MAX_FONT_MM. May come out below
    MIN_FONT_MM: the caller says it does not fit."""
    return min(MAX_FONT_MM, room_mm(rows, height_mm))


def build_labels(
    name: str,
    entries: list[DiscEntry],
    options: LabelOptions,
    notation: str = keys.CAMELOT,
) -> list[Label]:
    """The labels for one audio disc, in order. Empty when the disc has no tracks."""
    if not entries:
        return []
    runs = split_evenly(entries, options.per_disc)
    # one text size for all of a disc's labels, set by the fullest
    fullest = max(len(run) for run in runs)
    if (
        options.font_pt > 0
    ):  # chosen by hand: used as it is, and flagged if the list won't fit
        size = options.font_pt * MM_PER_PT
        fits = size <= room_mm(fullest, options.height_mm) + 1e-9
    else:
        size = font_size_mm(fullest, options.height_mm)
        fits = size >= MIN_FONT_MM
        size = max(size, MIN_FONT_MM)
    labels = []
    for i, run in enumerate(runs, start=1):
        rows = tuple(
            LabelRow(
                number=entry.position + 1,
                title=entry.title,
                artist=entry.artist,
                bpm=bpm_text(entry.track.bpm) if options.show_bpm_key else "",
                key=keys.display(entry.track.key, notation)
                if options.show_bpm_key
                else "",
            )
            for entry in run
        )
        heading = name if len(runs) == 1 else f"{name}  {i}/{len(runs)}"
        labels.append(Label(heading, rows, size, fits))
    return labels


def column_widths(
    title_w: float, artist_w: float, available: float
) -> tuple[float, float]:
    """How wide the title and artist columns are, given the widest title and artist and the room.
    Whatever fits is left whole; if not, the shorter of the two keeps its width up to half the room
    and the longer gets the rest, so one long artist doesn't squeeze every short title."""
    if title_w + artist_w <= available:
        return title_w, artist_w
    half = available / 2
    if title_w <= half:
        return title_w, available - title_w
    if artist_w <= half:
        return available - artist_w, artist_w
    return half, half


@dataclass(frozen=True)
class Sheet:
    """The grid of labels that fits a page."""

    options: LabelOptions
    columns: int
    rows: int

    @property
    def per_page(self) -> int:
        return self.columns * self.rows

    def position(self, slot: int) -> tuple[float, float]:
        """Top-left corner of grid slot `slot` (0 is the top-left label, then along the row)."""
        row, column = divmod(slot, self.columns)
        o = self.options
        return (
            o.margin_mm + column * (o.width_mm + o.gap_mm),
            o.margin_mm + row * (o.height_mm + o.gap_mm),
        )


def _fit_count(room: float, size: float, gap: float) -> int:
    return int((room + gap + 1e-9) // (size + gap))


def sheet_for(options: LabelOptions) -> Sheet | None:
    """The grid for `options`, or None when not even one label fits inside the margins."""
    o = options
    page_w, page_h = o.page_mm
    columns = _fit_count(page_w - 2 * o.margin_mm, o.width_mm, o.gap_mm)
    rows = _fit_count(page_h - 2 * o.margin_mm, o.height_mm, o.gap_mm)
    return Sheet(o, columns, rows) if columns >= 1 and rows >= 1 else None


def paginate(labels: list[Label], sheet: Sheet, skip: int = 0) -> list[list[Placed]]:
    """`labels` laid out across pages, left to right and top to bottom. `skip` leaves the first
    slots of the first page empty, for a sheet that has had some labels cut from it already."""
    skip = max(0, min(skip, sheet.per_page - 1))
    pages: list[list[Placed]] = []
    for i, label in enumerate(labels):
        page, slot = divmod(skip + i, sheet.per_page)
        if page == len(pages):
            pages.append([])
        pages[page].append(Placed(*sheet.position(slot), label))
    return pages
