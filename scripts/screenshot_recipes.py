"""The pictures `scripts/make_screenshots.py` knows how to make: one `@shot` recipe each, run offscreen against a made-up
music collection (`scripts/demo_music.py`) in a temporary folder and library. Nothing of the owner's is read.

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
BROWSE = HOUSE  # what the track list shows in the pictures


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
    """The made-up collection and the library imported from it, made once per run under `workdir`."""

    def __init__(self, workdir: Path):
        import demo_music

        from dubcrate.cd import DISC_74_SECTORS
        from dubcrate.library import Library
        from dubcrate.library.folder import scan_folder
        from dubcrate.library.lengths import measure_tracks

        # a pretend home folder: the Explorer starts from it, so none of the owner's own folders or drives show
        self.home = workdir.resolve() / "home" / "dj"
        self.music = self.home / "Music"
        print("making the demo music (about a minute)...")
        waves = demo_music.build_demo_music(self.music)
        self.previews = {os.path.realpath(path): wave for path, wave in waves.items()}

        self.lib = Library.open(workdir / "library.db")
        crate = self.music / SOURCE
        scan = scan_folder(crate)
        self.lib.replace_source("folder", str(crate), SOURCE, scan.tracks, scan.nodes)
        self.source = next(s for s in self.lib.sources() if s.name == SOURCE)
        self.nodes = {n.name: n for n in self.lib.nodes(self.source.id)}
        self.discs: dict[str, int] = {}

        lib = self.lib
        gigs = lib.create_disc_folder("This Weekend")
        warm = lib.create_disc("Friday Warm Up", folder_id=gigs)
        peak = lib.create_disc(
            "Saturday Peak", capacity_sectors=DISC_74_SECTORS, folder_id=gigs
        )
        usb = lib.create_disc("Car USB", kind="mp3", mp3_bitrate=320)
        self.discs.update({"warm": warm, "peak": peak, "usb": usb})
        for disc, names in ((warm, (WARM_UP, HOUSE)), (peak, (HOUSE,))):
            for name in (
                names
            ):  # (the second playlist tops the disc up with whatever still fits)
                tracks = self.lib.tracks(node_id=self.nodes[name].id)
                measure_tracks(
                    lib, [t.id for t in tracks]
                )  # exact lengths first, so "fit" is exact
                lib.add_to_disc(disc, [t.id for t in tracks], fit=True)
        for folder, name in (
            ("Warm", WARM_UP),
            ("UK Garage", GARAGE),
            ("Throwbacks", THROWBACKS),
        ):
            tracks = self.lib.tracks(node_id=self.nodes[name].id)[:9]
            measure_tracks(lib, [t.id for t in tracks])
            lib.add_to_disc(
                usb, [t.id for t in tracks], dir_id=lib.create_disc_dir(usb, folder)
            )
        lib.create_disc("Open Format Practice")  # an empty one, as a new disc looks

    def node(self, name: str):
        return self.nodes[name]

    def close(self):
        self.lib.close()


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
    ctx.open_playlist(window, BROWSE)
    ctx.load_track(window, 2)
    window.refresh_discs()
    window.disc_tree.expandAll()
    ctx.settle()
    return window


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
    folder = next(d for d in ctx.lib.disc_dirs(usb) if d.name == "UK Garage")
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
    window.splitter.setSizes([320, 860, 320])
    misc = ctx.demo.music / "Misc MP3s"
    window.pin_folder(str(misc))
    window.source_tabs.set_current("explorer")
    window.explorer_tree.select_path(
        str(ctx.demo.music / SOURCE / WARM_UP)
    )  # (opens Demo Crate in the tree)
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
    # (the made-up tracks are quiet: at -10 every one is held back, so the gentle preset)
    dialog.levels_combo.setCurrentIndex(dialog.levels_combo.findData("-14"))
    dialog.levels_check.setChecked(True)
    ctx.wait_for(lambda: not dialog._measuring)
    dialog.confirm_check.setChecked(True)
    ctx.settle(6)
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
