"""Index labels for a CD case, printed on sticker paper (A4, Letter, ...) (pure: no Qt, no printer).

A disc's track list is turned into `Label`s: the tracks are spread over `per_disc` labels (a
case with two areas to stick on, say; 0 lets Dubcrate pick the fewest that stay readable), each
with the disc's name as a heading and a row per track: number, title, artist, and the approximate
BPM and key when the library knows them. A long list can run in up to `columns` columns, and the
folders of an MP3 disc are headings in the list, kept together on a label where they fit.
`sheet_for` works out how many labels of the chosen size fit on a page, and `paginate`
places labels from several discs on the same pages so little sticker paper is wasted.

What is printed for a track is the disc's own text (`DiscEntry.title` / `.artist`), as on the CD, never the library's.
Sizes are millimetres.
"""

from dataclasses import dataclass, replace

from dubcrate import keys
from dubcrate.library.models import DiscDir, DiscEntry

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
MAX_COLUMNS = 3
MAX_COPIES = 10

PADDING_MM = 2.0  # blank edge inside a label
MAX_FONT_MM = 4.2  # about 12 pt: short lists are not blown up
MIN_FONT_MM = 1.9  # about 5.4 pt: smaller than this is not readable
AUTO_MIN_MM = (
    6.0 * 25.4 / 72
)  # Auto picks the fewest labels that keep the text at least 6 pt
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
    per_disc: int = (
        0  # labels a disc's track list is spread over; 0: the fewest that stay readable
    )
    columns: int = 1  # the most columns a label's list may run in (fewer are used when they do the job)
    copies: int = (
        1  # how many times each disc's labels are printed (one per copy burned)
    )
    margin_mm: float = 10.0  # unprintable edge of the page
    gap_mm: float = 2.0  # between labels, to cut along
    show_bpm: bool = True
    show_key: bool = True
    show_duration: bool = False  # the track's length, M:SS
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
            per_disc=int(_clamp(self.per_disc, 0, MAX_PER_DISC)),
            columns=int(_clamp(self.columns, 1, MAX_COLUMNS)),
            copies=int(_clamp(self.copies, 1, MAX_COPIES)),
            margin_mm=_clamp(self.margin_mm, 0.0, 30.0),
            gap_mm=_clamp(self.gap_mm, 0.0, 20.0),
        )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class LabelRow:
    number: int  # the track's number in its folder (on the disc itself when it has none), from 1
    title: str
    artist: str
    bpm: str = ""  # whole beats per minute, "" when unknown
    key: str = ""  # in the notation chosen in Settings, "" when unknown
    length: str = ""  # the track's own length as M:SS, "" when unknown


@dataclass(frozen=True)
class FolderRow:
    """A folder of an MP3 disc, as a heading line in the list: its path, "Sets / Friday"."""

    name: str


Item = LabelRow | FolderRow

SIDE_COLUMNS = (("BPM", "bpm"), ("Key", "key"), ("Time", "length"))


@dataclass(frozen=True)
class Label:
    heading: str  # the disc's name, with "1/2" when spread over several labels
    columns: tuple[
        tuple[Item, ...], ...
    ]  # the list, top to bottom in each column, left to right
    font_mm: float  # the same for every label of a disc
    fits: bool  # False: even the smallest readable text does not fit the height

    @property
    def rows(self) -> tuple[LabelRow, ...]:
        """Every track on the label, in order (folder headings left out)."""
        return tuple(
            item
            for column in self.columns
            for item in column
            if isinstance(item, LabelRow)
        )

    @property
    def has_bpm(self) -> bool:
        return any(row.bpm for row in self.rows)

    @property
    def has_key(self) -> bool:
        return any(row.key for row in self.rows)

    @property
    def has_length(self) -> bool:
        return any(row.length for row in self.rows)

    @property
    def side_columns(self) -> tuple[tuple[str, str], ...]:
        """The optional columns this label shows at its right edge, left to right, as (heading,
        `LabelRow` attribute): those with something in them on this label."""
        return tuple(
            (heading, attr)
            for heading, attr in SIDE_COLUMNS
            if any(getattr(row, attr) for row in self.rows)
        )


@dataclass(frozen=True)
class Placed:
    """A label on a page, its top-left corner in millimetres from the page's."""

    x_mm: float
    y_mm: float
    label: Label


def clock_text(seconds: float | None) -> str:
    """A track's length as M:SS, whole seconds rounded down as the library's Time column does,
    "" when there is none."""
    if not seconds or seconds <= 0:
        return ""
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


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


@dataclass(frozen=True)
class _Block:
    """A run of tracks under one folder heading (None: the tracks on the disc's root)."""

    heading: FolderRow | None
    rows: tuple[LabelRow, ...]


def _blocks(
    entries: list[DiscEntry], dirs: list[DiscDir], options: LabelOptions, notation: str
) -> list[_Block]:
    """The disc's tracks grouped by folder: the root first, then the folders depth first with
    siblings by name, as a player lists them. Tracks are numbered in their folder, in disc
    order, as the files are named when burned. A folder with no tracks of its own has no block."""
    by_id = {d.id: d for d in dirs}
    numbers: dict[int | None, int] = {}
    grouped: dict[int | None, list[LabelRow]] = {}
    for entry in entries:
        number = numbers[entry.dir_id] = numbers.get(entry.dir_id, 0) + 1
        grouped.setdefault(entry.dir_id, []).append(
            LabelRow(
                number=number,
                title=entry.title,
                artist=entry.artist,
                bpm=bpm_text(entry.track.bpm) if options.show_bpm else "",
                key=keys.display(entry.track.key, notation) if options.show_key else "",
                length=clock_text(entry.track.duration)
                if options.show_duration
                else "",
            )
        )

    def path(dir_id: int) -> str:
        names = []
        while dir_id in by_id:
            names.append(by_id[dir_id].name)
            dir_id = by_id[dir_id].parent_id
        return " / ".join(reversed(names))

    blocks = [_Block(None, tuple(grouped.pop(None, [])))]

    def walk(parent: int | None) -> None:
        for d in sorted(
            (d for d in dirs if d.parent_id == parent), key=lambda d: d.name.casefold()
        ):
            if d.id in grouped:
                blocks.append(_Block(FolderRow(path(d.id)), tuple(grouped.pop(d.id))))
            walk(d.id)

    walk(None)
    return [b for b in blocks if b.rows]


def _pack(
    blocks: list[_Block], capacity: int, together: bool = True
) -> list[list[Item]]:
    """The blocks as labels' lists of at most `capacity` lines (a folder heading is a line). With
    `together`, a folder that fits a label on its own is not split between two; one too long for
    that is (or any, without `together`), with its heading repeated "(cont.)". A heading is never
    left as the last line of a label."""
    capacity = max(capacity, 2)
    packed: list[list[Item]] = [[]]

    def room() -> int:
        return capacity - len(packed[-1])

    for block in blocks:
        if block.heading is None:
            for row in block.rows:
                if room() == 0:
                    packed.append([])
                packed[-1].append(row)
            continue
        size = 1 + len(block.rows)
        if together and size > room() and size <= capacity and packed[-1]:
            packed.append([])
        rest, first = list(block.rows), True
        while rest:
            if room() < 2 and packed[-1]:
                packed.append([])
            take = min(len(rest), room() - 1)
            name = block.heading.name if first else f"{block.heading.name} (cont.)"
            packed[-1] += [FolderRow(name), *rest[:take]]
            rest, first = rest[take:], False
            if rest:
                packed.append([])
    return [lines for lines in packed if lines]


def _spread(
    blocks: list[_Block], parts: int, together: bool = True
) -> list[list[Item]]:
    """The tracks as at most `parts` labels' lists. Without folders they are cut into near-equal
    runs; with folders, at the smallest label length that gets them onto `parts` labels."""
    if all(b.heading is None for b in blocks):
        return split_evenly([row for b in blocks for row in b.rows], parts)
    total = sum(len(b.rows) + (b.heading is not None) for b in blocks)
    for capacity in range(-(-total // parts), total + 1):
        packed = _pack(blocks, capacity, together)
        if len(packed) <= parts:
            return packed
    return _pack(blocks, total, together)


def split_columns(items: list[Item], columns: int) -> list[list[Item]]:
    """One label's list as up to `columns` columns of near-equal length, in order. A folder
    heading is never left at the foot of a column: it moves to the head of the next."""
    per = -(-len(items) // columns)
    result, start = [], 0
    for c in range(columns):
        end = len(items) if c == columns - 1 else min(len(items), start + per)
        while end > start + 1 and isinstance(items[end - 1], FolderRow):
            end -= 1
        if end > start:
            result.append(items[start:end])
        start = end
    return result


def _size(rows: int, options: LabelOptions) -> tuple[float, bool, bool]:
    """(text height, whether it fits, whether it is good enough to stop looking) for a label
    whose fullest column has `rows` lines. A size chosen by hand is used as it is; Auto fills the
    label, never below MIN_FONT_MM, and is good enough from AUTO_MIN_MM."""
    room = room_mm(rows, options.height_mm)
    if options.font_pt > 0:
        size = options.font_pt * MM_PER_PT
        fits = size <= room + 1e-9
        return size, fits, fits
    size = min(MAX_FONT_MM, room)
    return max(size, MIN_FONT_MM), size >= MIN_FONT_MM, size >= AUTO_MIN_MM


def _labels(
    name: str, lists: list[list[Item]], columns: int, options: LabelOptions
) -> tuple[list[Label], bool]:
    """Labels from each label's list of lines, in `columns` columns, with one text size for all
    of them, set by the fullest column. The flag: this is a good layout (see `_size`)."""
    split = [split_columns(items, columns) for items in lists]
    fullest = max(len(column) for cols in split for column in cols)
    size, fits, good = _size(fullest, options)
    return [
        Label(
            name if len(split) == 1 else f"{name}  {i}/{len(split)}",
            tuple(tuple(column) for column in cols),
            size,
            fits,
        )
        for i, cols in enumerate(split, start=1)
    ], good


def build_labels(
    name: str,
    entries: list[DiscEntry],
    options: LabelOptions,
    notation: str = keys.CAMELOT,
    dirs: list[DiscDir] | None = None,
) -> list[Label]:
    """The labels for one disc, in order; `dirs` are the folders of an MP3 disc. Empty when the
    disc has no tracks.

    `per_disc` labels are made (Auto, 0: the fewest, up to MAX_PER_DISC, that give readable text),
    and as few columns as will do, up to `columns`. Fewest labels come first: a layout with more
    columns beats one with more labels. Folders are kept whole on a label where that is readable;
    a folder is cut (and its heading repeated) rather than use another column or label."""
    blocks = _blocks(entries, dirs or [], options, notation)
    if not blocks:
        return []
    parts_to_try = (
        [options.per_disc] if options.per_disc else range(1, MAX_PER_DISC + 1)
    )
    best: list[Label] = []
    for parts in parts_to_try:
        spreads = [_spread(blocks, parts)]  # folders kept whole where they fit, else:
        split = _spread(
            blocks, parts, together=False
        )  # balanced, cutting folders if need be
        if split != spreads[0]:
            spreads.append(split)
        for columns in range(1, options.columns + 1):
            for lists in spreads:
                made, good = _labels(name, lists, columns, options)
                if good:
                    return made
        best = made  # the most columns, the last spread: what is left if none was good
    return best


def column_widths(
    title_w: float, artist_w: float, available: float, title_share: float = 0.5
) -> tuple[float, float]:
    """How wide the title and artist columns are, given the widest title and artist and the room.
    Whatever fits is left whole; if not, the shorter of the two keeps its width up to its share of
    the room (`title_share` for the title, the rest for the artist) and the longer gets the rest,
    so one long artist doesn't squeeze every short title."""
    if title_w + artist_w <= available:
        return title_w, artist_w
    title_cap = available * title_share
    artist_cap = available - title_cap
    if title_w <= title_cap:
        return title_w, available - title_w
    if artist_w <= artist_cap:
        return available - artist_w, artist_w
    return title_cap, artist_cap


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


def paginate(labels: list[Label], sheet: Sheet) -> list[list[Placed]]:
    """`labels` laid out across pages, left to right and top to bottom."""
    pages: list[list[Placed]] = []
    for i, label in enumerate(labels):
        page, slot = divmod(i, sheet.per_page)
        if page == len(pages):
            pages.append([])
        pages[page].append(Placed(*sheet.position(slot), label))
    return pages
