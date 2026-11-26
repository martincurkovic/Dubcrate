"""The pictures `scripts/make_screenshots.py` knows how to make: one `@shot` recipe each, run offscreen against a made-up
music collection (`scripts/demo_music.py`) in a temporary folder and library. Nothing of the owner's is read, unless
`--real` is given: then the same pictures are made from the owner's own tracks in their rekordbox library, only those a
local picks file names (`scripts/real_music.py`: read-only, a snapshot of the library, links to the files).

A recipe gets a `Context`, builds and shows whatever its picture needs and returns the widget (or pixmap) to photograph.
Pictures go to src/dubcrate/ui/help/screenshots: Help > User Guide shows them (they ship inside the app) and the README points
at the same files. Every picture manual.md mentions must have a recipe here (`tests/test_make_screenshots.py` checks).

This module imports no Qt and no Dubcrate module at the top, so the test can read the recipe table without a window;
each recipe imports what it needs when it runs.
"""

import os
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

SHOTS: dict[str, "Shot"] = {}
SIZE = (
    1500,
    900,
)  # the main window, in points: wide enough for all three panes and the player strip
TALL = (1560, 960)  # for the pictures with a window on top of it

SOURCE = "Demo Crate"  # the imported folder: its sub-folders are the playlists
WARM_UP, HOUSE, GARAGE, THROWBACKS = "Warm Up", "House", "UK Garage", "Throwbacks"
BROWSE = HOUSE  # (the made-up collection's: the UI gallery's scenes import these; a recipe uses `demo.names`)
MISC = "Misc MP3s"  # the Explorer's pinned folder of loose files


@dataclass(frozen=True)
class Names:
    """What the pictures call the imported library and its four playlists (the made-up ones, or the picks file's)."""

    source: str
    warm_up: str
    house: str
    garage: str
    throwbacks: str

    @property
    def browse(self) -> str:  # (the playlist the track list shows)
        return self.house


DEMO_NAMES = Names(SOURCE, WARM_UP, HOUSE, GARAGE, THROWBACKS)


@dataclass
class Shot:
    name: str
    description: str
    recipe: callable
    scheme: str  # "dark", "light" or "system6": the theme the app is in while it is built and photographed


def shot(name: str, description: str, scheme: str = "dark"):
    def register(fn):
        SHOTS[name] = Shot(name, description, fn, scheme)
        return fn

    return register


class Silent:
    """A stand-in sound card: the preview player runs, nothing is played."""

    def start(self, gen):
        pass

    def stop(self):
        pass

    def close(self):
        pass


class Demo:
    """The made-up collection and the library imported from it, made once per run under `workdir`.

    `real` collections (`real_music.make_real_demo`) subclass it: `_names` and `_collect` say what is imported, the
    discs are planned the same way from whatever the four playlists hold."""

    real = False
    show_art: frozenset[str] = (
        frozenset()
    )  # (titles whose cover picture may be shown: only real collections have any)
    hero_tab = "memory"  # the side panel's page in the main window pictures

    def __init__(self, workdir: Path):
        from dubcrate.library import Library

        # a pretend home folder: the Explorer starts from it, so none of the owner's own folders or drives show
        self.home = workdir.resolve() / "home" / "dj"
        self.music = self.home / "Music"
        self.previews: dict[str, object] = {}
        self.names = self._names()
        # what the Explorer picture pins, and the folder it opens in the tree first
        self.explorer_folder = self.music / MISC
        self.explorer_open = self.music / self.names.source / self.names.warm_up
        self.lib = Library.open(workdir / "library.db")
        self._collect()
        self.source = next(s for s in self.lib.sources() if s.name == self.names.source)
        self.nodes = {n.name: n for n in self.lib.nodes(self.source.id)}
        self.tempo = {  # (a track's tag tempo by file, for the staged beat grid of tracks rekordbox hasn't analysed)
            os.path.realpath(t.path): t.bpm
            for t in self.lib.tracks()
            if t.bpm and not t.analysis_path
        }
        self.discs: dict[str, int] = {}
        self._plan_discs()

    def _names(self) -> Names:
        return DEMO_NAMES

    def _collect(self) -> None:
        import demo_music

        from dubcrate.library.folder import scan_folder

        print("making the demo music (about a minute)...")
        waves = demo_music.build_demo_music(self.music)
        self.previews = {os.path.realpath(path): wave for path, wave in waves.items()}
        crate = self.music / self.names.source
        scan = scan_folder(crate)
        self.lib.replace_source("folder", str(crate), self.names.source, scan.tracks, scan.nodes)  # fmt: skip

    def _plan_discs(self) -> None:
        from dubcrate.cd import DISC_74_SECTORS
        from dubcrate.library.lengths import measure_tracks

        lib, names = self.lib, self.names
        gigs = lib.create_disc_folder("This Weekend")
        warm = lib.create_disc("Friday Warm Up", folder_id=gigs)
        peak = lib.create_disc(
            "Saturday Peak", capacity_sectors=DISC_74_SECTORS, folder_id=gigs
        )
        usb = lib.create_disc("Car USB", kind="mp3", mp3_bitrate=320)
        self.discs.update({"warm": warm, "peak": peak, "usb": usb})
        for disc, playlists in (
            (warm, (names.warm_up, names.house)),
            (peak, (names.house,)),
        ):
            for name in (
                playlists
            ):  # (the second playlist tops the disc up with whatever still fits)
                tracks = lib.tracks(node_id=self.nodes[name].id)
                measure_tracks(
                    lib, [t.id for t in tracks]
                )  # exact lengths first, so "fit" is exact
                lib.add_to_disc(disc, [t.id for t in tracks], fit=True)
        self.usb_folders = (
            ("Warm", names.warm_up),
            ("UK Garage", names.garage),
            ("Throwbacks", names.throwbacks),
        )  # (the MP3 disc's folders and the playlist each is filled from)
        for folder, name in self.usb_folders:
            tracks = lib.tracks(node_id=self.nodes[name].id)[:9]
            measure_tracks(lib, [t.id for t in tracks])
            lib.add_to_disc(
                usb, [t.id for t in tracks], dir_id=lib.create_disc_dir(usb, folder)
            )
        lib.create_disc("Open Format Practice")  # an empty one, as a new disc looks

    def node(self, name: str):
        return self.nodes[name]

    def close(self):
        self.lib.close()


def make_demo(workdir: Path, picks=None) -> Demo:
    """The made-up collection, or (with `picks`, a `real_music.Picks`) the owner's own tracks that they name."""
    if picks is None:
        return Demo(workdir)
    import real_music

    return real_music.make_real_demo(workdir, picks)


class Context:
    """What a recipe is given: the QApplication, the demo library and helpers for building windows and waiting."""

    def __init__(self, app, scheme, demo):
        self.app = app
        self.scheme = scheme
        self.demo = demo
        self.lib = demo.lib
        self._cleanups = []

    def settle(self, rounds=4):
        for _ in range(rounds):
            self.app.processEvents()

    def wait_for(self, predicate, timeout=30.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                self.settle()
                return
            time.sleep(0.01)
        raise TimeoutError("the picture's window never got what it was waiting for")

    def window(self, size=SIZE, burner=None):
        """A MainWindow on the demo library with a silent player, shown, on the Library tab. Its Explorer starts from
        the pretend home folder and sees no drives."""
        from unittest import mock

        from dubcrate.audio.player import AudioPlayer
        from dubcrate.ui.main_window import MainWindow

        patches = ExitStack()
        fake_home = self.demo.home
        patches.enter_context(
            mock.patch.object(Path, "home", classmethod(lambda cls: fake_home))
        )
        patches.enter_context(
            mock.patch("dubcrate.ui.explorer.mounted_volumes", return_value=[])
        )
        patches.enter_context(  # (no real disk is asked about: the card list shows only what a scene stages)
            mock.patch("dubcrate.ui.sync_view._real_unset", return_value=[])
        )
        if not self.demo.real:  # (made-up files have no rekordbox analysis: give them a beat grid and phrases)
            patches.enter_context(
                mock.patch("dubcrate.ui.preview.load_media", self._staged_media())
            )
        self.on_cleanup(patches.close)
        window = MainWindow(
            self.lib,
            player=AudioPlayer(device_factory=lambda *a: Silent()),
            burner_factory=(lambda: burner) if burner else None,
        )
        window.resize(*size)
        window.show()
        self.settle()
        self.on_cleanup(window.close)
        return window

    def _staged_media(self):
        """`preview.load_media` with a staged analysis for a made-up track: a beat grid at its tag's tempo and phrases that
        follow how the file was synthesised (what rekordbox's analysis gives a real track)."""
        import dataclasses

        import demo_music

        from dubcrate.analysis import Analysis
        from dubcrate.ui import preview

        original = preview.load_media
        tempo = self.demo.tempo

        def staged(path, analysis_root, analysis_path):
            media = original(path, analysis_root, analysis_path)
            bpm = tempo.get(os.path.realpath(path))
            if media.analysis is None and bpm:
                times, numbers, phrases = demo_music.grid_for(bpm, media.audio.duration)  # fmt: skip
                media = dataclasses.replace(
                    media,
                    analysis=Analysis(
                        beat_times=times, beat_numbers=numbers, bpm=bpm, phrases=phrases
                    ),
                )
            return media

        return staged

    def stage_previews(self, window):
        """Fill the track list's Preview column for the rows shown. The app only fills it from rekordbox's own analysis
        (for a plain folder a track's waveform appears when it is loaded), so this stands in for that."""
        model = window.track_model
        for row in range(model.rowCount()):
            track = model.track_at(row)
            wave = self.demo.previews.get(os.path.realpath(track.path))
            if wave is not None:
                model.set_preview(track.id, wave)
        self.settle()
        if self.demo.real:  # (rekordbox tracks fill their own Preview strips and hot cue marks, on a timer and a worker)

            def filled():
                window._request_previews()
                rows = [model.track_at(r) for r in range(model.rowCount())]
                return all(
                    model.has_preview(t.id)
                    and (not t.analysis_path or model.has_cues(t.id))
                    for t in rows
                )

            self.wait_for(filled, timeout=90)

    def open_playlist(self, window, name):
        from dubcrate.ui.models import find_selection

        node = self.demo.node(name)
        window.tree.setCurrentIndex(
            find_selection(window.tree.model(), ("node", node.id, self.demo.source.id))
        )
        self.settle()
        self.wait_for(lambda: window.track_model.rowCount() > 0)
        self.stage_previews(window)

    def load_track(self, window, row, at=0.4):
        """Load the track in `row` of the list into the player (not playing), the playhead `at` of the way in."""
        track = window.track_model.track_at(row)
        window.track_table.selectRow(row)
        window.preview.load(track, play=False)
        self.wait_for(lambda: window.preview.player.loaded)
        if (
            self.demo.real and track.analysis_path
        ):  # (rekordbox's grid and cues arrive with the analysis)
            self.wait_for(lambda: window.preview.deck.has_grid, timeout=60)
            self.wait_for(
                lambda: window.preview.cues.cues_for(track, announce=False) is not None,
                timeout=60,
            )
        window.preview.player.seek(window.preview.player.duration * at)
        self.settle(8)

    def on_cleanup(self, fn):
        self._cleanups.append(fn)

    def cleanup(self):
        for fn in reversed(self._cleanups):
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - tidying up must not hide the picture's own result
                print(f"  (cleanup problem: {e})")
        self._cleanups.clear()
        for widget in self.app.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        self.settle()


# -- the building blocks pictures share ---------------------------------------------------------------------------------------


def _main(ctx, size=SIZE):
    """The library with a playlist shown, a track loaded and the discs on the right."""
    window = ctx.window(size)
    window.splitter.setSizes(
        [320, 860, 320]
    )  # wide enough that the track list's columns all show
    ctx.open_playlist(window, ctx.demo.names.browse)
    ctx.load_track(window, 2)
    if not ctx.demo.real:  # (a real track has rekordbox's own cues)
        _stage_cues(window)
    _hero_tab(ctx, window)
    window.refresh_discs()
    window.disc_tree.expandAll()
    ctx.settle()
    return window


def _hero_tab(ctx, window):
    """A real collection may show the loaded track's cover picture (the Info page) in the main window pictures, and only
    for a track its picks file allows: any other cover stays out of every picture."""
    demo = ctx.demo
    track = window.preview.track
    if (
        demo.real
        and demo.hero_tab == "info"
        and track is not None
        and " ".join(track.title.casefold().split()) in demo.show_art
    ):
        window.player_strip.side.tabs[2].click()
        ctx.settle(8)


def _stage_cues(window):
    """Staged: the made-up tracks are not in rekordbox, so the loaded one is given invented cues (the player's flags,
    its hot cue buttons and its memory / hot cue lists show them)."""
    from dubcrate.analysis import Cue

    d = window.player_strip._duration
    window.player_strip.show_cues(
        (
            Cue("memory", 0.1),
            Cue("memory", d * 0.25),
            Cue("memory", d * 0.5),
            Cue("loop", d * 0.55, end=d * 0.55 + 4.0),
            Cue("hot", d * 0.12, slot=1),
            Cue("hot", d * 0.4, slot=2),
            Cue("hot", d * 0.7, slot=3),
        )
    )


def _over(window, dialog):
    """The window with `dialog` shown on top of it, as a picture: a shadow, and the window a little dimmed behind it.
    (Grabbing the window alone leaves out a dialog, which is a window of its own.)"""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QPainter, QPainterPath

    base = window.grab()
    top = dialog.grab()
    ratio = base.devicePixelRatio()
    width, height = base.width() / ratio, base.height() / ratio
    w, h = top.width() / ratio, top.height() / ratio
    x, y = (width - w) / 2, (height - h) / 2
    painter = QPainter(base)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillRect(QRectF(0, 0, width, height), QColor(0, 0, 0, 80))
    painter.setPen(Qt.PenStyle.NoPen)
    for grow in range(18, 0, -1):  # a soft shadow, a little lower than the dialog
        painter.setBrush(QColor(0, 0, 0, 6))
        painter.drawRoundedRect(
            QRectF(x - grow, y - grow + 8, w + 2 * grow, h + 2 * grow),
            10 + grow,
            10 + grow,
        )
    corners = QPainterPath()
    corners.addRoundedRect(QRectF(x, y, w, h), 10, 10)
    painter.setClipPath(corners)
    painter.drawPixmap(QRectF(x, y, w, h).topLeft(), top)
    painter.setClipping(False)
    painter.setPen(QColor(255, 255, 255, 50))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(x, y, w, h), 10, 10)
    painter.end()
    return base


# -- the pictures (src/dubcrate/ui/help/screenshots) --------------------------------------------------------------------------


@shot("main-window-dark", "the main window, dark (the README's first picture)", "dark")
def _main_dark(ctx):
    return _main(ctx)


@shot("main-window-light", "the main window, light", "light")
def _main_light(ctx):
    return _main(ctx)


@shot(
    "main-window-system6", "the main window in the Macintosh System 6 look", "system6"
)
def _main_system6(ctx):
    return _main(ctx)


@shot(
    "player",
    "the player: a cue point set, an auto loop on and the quantise button lit, over a track with cues",
)
def _player(ctx):
    """The staged main window's player strip (invented cues; the loaded track's tempo is from its tags) with CUE pressed
    away from the start, an auto loop on and quantise on: what the session's own markers look like beside rekordbox's."""
    window = _main(ctx)
    preview = window.preview
    preview.player.pause()
    window.player_strip.quantise_button.click()
    preview.player.seek(8.0)
    preview.cue_pressed()  # (stopped away from the cue point: sets it, on the beat)
    preview.player.seek(76.0)
    window.player_strip.loop_size_forward.click()  # 8 beats
    window.player_strip.loop_size.click()
    window.player_strip.side.tabs[1].click()  # the hot cue list
    ctx.settle(8)
    return window.player_strip


@shot(
    "disc-open", "a disc open in the middle: its list, the capacity bar and the buttons"
)
def _disc_open(ctx):
    window = _main(ctx)
    window.open_disc(ctx.demo.discs["peak"])
    ctx.settle(8)
    return window


@shot("mp3-disc", "an MP3 disc with its folders, in the middle and in the disc tree")
def _mp3_disc(ctx):
    from dubcrate.ui.models import find_selection

    window = _main(ctx)
    usb = ctx.demo.discs["usb"]
    garage_folder = ctx.demo.usb_folders[1][0]
    folder = next(d for d in ctx.lib.disc_dirs(usb) if d.name == garage_folder)
    window.disc_view.set_disc(
        usb, folder.id
    )  # a folder of the disc: its files, with its own count
    window.center.setCurrentIndex(1)
    window.refresh_discs()
    window.disc_tree.expandAll()
    window.disc_tree.setCurrentIndex(
        find_selection(window.disc_tree.model(), ("disc_dir", usb, folder.id))
    )
    ctx.settle(8)
    return window


@shot(
    "explorer",
    "the Explorer tab beside a pinned folder's tab, a folder of loose MP3s shown",
)
def _explorer(ctx):
    window = ctx.window()
    window.splitter.setSizes(
        [340, 840, 320]
    )  # (a wider tree than usual: the folder names are long)
    misc = ctx.demo.explorer_folder
    window.pin_folder(str(misc))
    window.source_tabs.set_current("explorer")
    window.explorer_tree.select_path(
        str(ctx.demo.explorer_open)
    )  # (opens the imported folder in the tree)
    ctx.settle()
    window.explorer_tree.select_path(str(misc))
    ctx.wait_for(
        lambda: (
            window.track_model.rowCount() > 0
            and not window.statusBar().currentMessage()
        )
    )
    ctx.stage_previews(window)
    ctx.load_track(window, 0)
    return window


@shot("labels", "Print Labels…: the label window over the main window")
def _labels(ctx):
    from PySide6.QtCore import Qt

    window = _main(ctx, TALL)
    window.open_disc(ctx.demo.discs["peak"])
    window.print_labels(ctx.demo.discs["peak"])
    dialog = window.label_dialog
    dialog.resize(1120, 780)
    for (
        item
    ) in dialog._disc_items():  # two discs share a page, as they would on sticker paper
        if "Friday" in item.text(0):
            item.setCheckState(0, Qt.CheckState.Checked)
    ctx.settle(10)
    return _over(window, dialog)


@shot(
    "burn", "Burn…: the burn window with the drive checked and a blank CD-R confirmed"
)
def _burn(ctx):
    from dubcrate.burn import MediaInfo

    class StandInBurner:  # (never writes: see the module note)
        def drives(self):
            return []  # (one drive at most: no drive menu in the picture)

        def media_info(self, device):
            return MediaInfo(
                drive="USB CD Writer", present=True, blank=True, free_sectors=359_849
            )

        def burn(self, disc, options, progress):
            raise AssertionError("a picture must never burn")

    window = ctx.window(burner=StandInBurner())
    window.open_disc(ctx.demo.discs["warm"])
    window.burn_current_disc()
    dialog = window.burn_dialog
    ctx.settle(10)
    # normalising on, as the guide describes it (the tracks are measured on a worker)
    # (the made-up tracks are quiet: at -10 every one is held back, so the gentle preset; real masters are loud enough)
    preset = "-10" if ctx.demo.real else "-14"
    dialog.levels_combo.setCurrentIndex(dialog.levels_combo.findData(preset))
    dialog.levels_check.setChecked(True)
    ctx.wait_for(lambda: not dialog._measuring, timeout=240)
    ctx.settle(6)
    return dialog


@shot(
    "sync",
    "the Sync tab: a CDJ card on the left, the audio discs and what a sync would do for each",
)
def _sync(ctx):
    """Staged: the made-up tracks are not in rekordbox, so they are given invented cues; the card is an empty
    folder of the right shape, with Saturday Peak already written to it (cues and waveforms) so one disc says "In sync"."""
    from unittest import mock

    from PySide6.QtCore import Qt

    from dubcrate import cdj_card as cc
    from dubcrate.analysis import Cue
    from dubcrate.card_apply import apply_sync
    from dubcrate.card_discs import RekordboxCues, prepare_disc
    from dubcrate.ui import sync_view

    def invented(self, track):
        if track.id % 3 == 0:  # a few tracks have none, as in a real library
            return ()
        d = track.duration
        return [
            Cue("memory", 0.1),
            Cue("hot", d * 0.12, slot=1),
            Cue("hot", d * 0.4, slot=2),
            Cue("hot", d * 0.7, slot=3),
            Cue("loop", d * 0.55, end=d * 0.55 + 4.0, slot=4),
        ]

    patches = ExitStack()
    if not ctx.demo.real:  # (a real track has its own cues in rekordbox)
        patches.enter_context(mock.patch.object(RekordboxCues, "for_track", invented))
    ctx.on_cleanup(patches.close)
    work = ctx.demo.home.parent.parent
    card = work / "volumes" / "CDJ-CARD"
    folder = card / "PIONEER" / "CDJ"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / cc.INDEX_NAME).write_bytes(cc.IndexFile().to_bytes())
    from dubcrate.library.waves import ensure_waveforms

    ids = [  # the made-up tracks are real (synthesised) files: their waveforms are analysed for real
        e.track.id for d in ctx.demo.discs.values() for e in ctx.lib.disc_entries(d)
    ]
    ensure_waveforms(ctx.lib, ids)
    peak = prepare_disc(
        ctx.lib, ctx.demo.discs["peak"], RekordboxCues(ctx.lib), waves=True
    )
    apply_sync(card, [peak.request], work / "card-backups")
    patches.enter_context(
        mock.patch("dubcrate.ui.sync_view.mounted_volumes", return_value=[card])
    )
    patches.enter_context(  # (the pretend card is a folder on the Mac's disk: say it is a 2 GB card)
        mock.patch("dubcrate.ui.sync_view.card_capacity", return_value=2_013_265_920)
    )

    window = _main(ctx)
    window.source_tabs.set_current("sync")
    ctx.settle(8)
    view = window.sync_view
    # (a real library's cues are read on a worker before the discs are listed: wait for the list)
    ctx.wait_for(lambda: any("Warm" in i.text(0) for i in view._disc_items()), timeout=180)  # fmt: skip
    warm = next(i for i in view._disc_items() if "Warm" in i.text(0))
    warm.setCheckState(sync_view.COL_NAME, Qt.CheckState.Checked)
    view.tree.setCurrentItem(warm)
    view.tree.setFocus()  # (not the search box, whose focus ring would draw the eye)
    ctx.settle(8)
    return window


@shot(
    "sync-waveforms",
    "the Sync tab with a track picked: its waveform at the bottom as the CDJ will draw it",
)
def _sync_waveforms(ctx):
    """The same staged Sync tab, with the fourth track of the picked disc picked (made-up music, real analysis)."""
    window = _sync(ctx)
    table = window.sync_view.browser.table
    table.setCurrentItem(table.topLevelItem(3))
    ctx.settle(8)
    return window


@shot(
    "format-card",
    "Format a Card…: the window that sets up a blank SD card (invented disks: nothing is listed or erased)",
)
def _format_card(ctx):
    from dubcrate import card_format as cf
    from dubcrate.ui.format_card_dialog import FormatCardDialog

    def listing():
        return [
            cf.CardDisk("disk4", "Generic STORAGE DEVICE", 2_013_265_920, "USB", True, True, False, False, True),
            cf.CardDisk("disk5", "SDXC card", 128_000_000_000, "USB", True, True, False, False, True),
        ]  # fmt: skip

    dialog = FormatCardDialog(ctx.window(), list_disks=listing)
    dialog.resize(580, 500)
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.settle(8)
    return dialog


@shot("settings", "Settings…: the Audio tab")
def _settings(ctx):
    from dubcrate.ui.settings_dialog import SettingsDialog

    window = ctx.window()
    dialog = SettingsDialog(window)
    dialog.resize(
        640, 400
    )  # taller than its minimum, which clips the grey explanations
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.settle(8)
    return dialog


@shot(
    "settings-library",
    "Settings…: the Library tab, with the choice to save memory cue changes into rekordbox",
)
def _settings_library(ctx):
    from dubcrate.ui.settings_dialog import SettingsDialog

    window = ctx.window()
    dialog = SettingsDialog(window)
    tabs = dialog.findChild(__import__("PySide6.QtWidgets").QtWidgets.QTabWidget)
    for i in range(tabs.count()):
        if tabs.tabText(i) == "Library":
            tabs.setCurrentIndex(i)
    dialog.resize(640, 560)
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.settle(8)
    return dialog


@shot(
    "card-import",
    "Sync > Import Cues…: the review of what changed on the player (invented), over the Sync tab",
)
def _card_import(ctx):
    """The made-up tracks have no cues on a card, so the findings are invented from a disc's real tracks."""
    from dubcrate.card_import import (
        ADD,
        HOT,
        LOOP,
        MOVE,
        REMOVE,
        DiscFinding,
        Row,
        TrackFinding,
    )
    from dubcrate.ui.card_import_dialog import CardImportDialog

    window = _sync(ctx)
    disc_id = ctx.demo.discs["peak"]
    entries = ctx.lib.disc_entries(disc_id)
    tracks = [
        TrackFinding(entries[1].track, 2, entries[1].title, entries[1].artist, [Row(ADD, 41500), Row(LOOP, 97000, 101000), Row(REMOVE, 12000)]),
        TrackFinding(entries[4].track, 5, entries[4].title, entries[4].artist, [Row(MOVE, 18000, old_ms=15500), Row(HOT, 64000, slot=1, ticked=False)]),
    ]  # fmt: skip
    summary = ctx.lib.disc(disc_id)
    finding = DiscFinding(disc_id, summary.name, f"CDJ CDs / {summary.name}", tracks, kept=1, has_baseline=True)  # fmt: skip
    dialog = CardImportDialog([finding], window)
    dialog.resize(600, 420)
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.settle(8)
    return _over(window, dialog)
