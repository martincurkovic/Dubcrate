"""The screens the UI snapshot harness (scripts/ui_snapshots.py) renders.

Each scene is a function that builds one window or dialog, already in the state worth looking at (a disc that is too
long, a burn that has just been cancelled, ...), from the made-up collection in `Demo` (scripts/screenshot_recipes.py).
A scene returns the widget to photograph, or None when the screen is only what it raises (a message box).

A scene never blocks: the harness replaces every modal (QDialog.exec, QMessageBox and QInputDialog, QMenu.exec) with a
recorder that PHOTOGRAPHS the box as it stands and answers with its safe default (No, Cancel), so code that would
pop a question just carries on and the question shows up in the gallery under the screen. A scene can also add a
picture of its own (`ctx.snap("a menu", widget)`, `ctx.snap_menu("right-click on a disc", menu)`).
To add a screen, write a function and decorate it with @scene. Keep every scene free of side effects on the demo
library (or put them right with `ctx.on_cleanup`): the next scene is built from the same one.

The scene list is also the checklist of the app's windows: when a window or dialog is added, add its scene here.
"""

import json
from dataclasses import dataclass

import screenshot_recipes as recipes
from screenshot_recipes import BROWSE, GARAGE, SIZE, SOURCE, WARM_UP  # noqa: F401 - scenes use them

# sizes to try for windows the user can resize: (label, width, height). A window that cannot shrink that far is
# shown at its own minimum instead, and the gallery says so. Heights are what is left of the screen once the menu
# bar, title bar and dock are taken off.
SMALL = ("1280x720 screen", 1280, 630)
LAPTOP = ("1440x900 screen", 1440, 810)
FULL_HD = ("1920x1080 screen", 1920, 990)
ALL_SCREENS = (SMALL, LAPTOP, FULL_HD)
NATURAL = ("natural size", None, None)  # the window as it opens
SMALLEST = ("smallest size", 0, 0)  # the window as small as it can be made
BOTH = (NATURAL, SMALLEST)


@dataclass(frozen=True)
class Scene:
    id: str
    area: str  # groups scenes in the gallery (and orders them)
    title: str
    build: object  # (SnapContext) -> QWidget | None
    sizes: tuple = ()  # window sizes to try; empty = the window's own size
    kind: str = "window"  # "window", "dialog", "message" or "menu" (informational)
    about: str = ""  # one line: what state the screen is in


SCENES: list[Scene] = []
AREAS = (
    "Main window",
    "Player",
    "Discs",
    "Burn window",
    "Label window",
    "Sync tab",
    "Settings",
    "Messages and questions",
    "Menus",
    "Help, About and updates",
    "Start-up and errors",
)


def scene(id, area, title, sizes=(), kind="window", about=""):
    def register(build):
        assert area in AREAS, f"{id}: unknown area {area!r}"
        assert all(s.id != id for s in SCENES), f"duplicate scene id {id}"
        SCENES.append(Scene(id, area, title, build, tuple(sizes), kind, about))
        return build

    return register


def ordered():
    """The scenes by area (the order of AREAS), each area in the order it was written."""
    return sorted(SCENES, key=lambda s: AREAS.index(s.area))


CURRENT: list = []  # the SnapContext of the screen being built, for the modal recorder


@dataclass
class Focus:
    """A scene's result when only part of a window matters: `host` is shown (and resized, for the screen
    sizes), `target` (the host or a widget inside it) is what is photographed and measured."""

    host: object
    target: object


@dataclass
class Extra:
    label: str
    pixmap: object
    texts: list
    issues: list


class SnapContext(recipes.Context):
    """`screenshot_recipes.Context` plus what the gallery needs: pictures a screen raises, and things that can't be drawn."""

    def __init__(self, app, scheme, demo):
        super().__init__(app, scheme, demo)
        self.extras: list[Extra] = []
        self.skipped: list[str] = []

    # -- pictures of the boxes and menus a screen raises -----------------------------------------------

    def snap(self, label, widget):
        """Photograph `widget` (a dialog or message box, built but not exec'd) as an extra picture of this screen."""
        from PySide6.QtTest import QTest
        from ui_snapshot_checks import collect_texts, find_layout_issues

        widget.show()
        QTest.qWait(80)
        self.settle(4)
        self.extras.append(
            Extra(
                label,
                widget.grab(),
                collect_texts(widget),
                find_layout_issues(widget),
            )
        )
        widget.hide()

    def snap_menu(self, label, menu):
        """Photograph a QMenu as it would pop up."""
        from PySide6.QtCore import QPoint

        menu.popup(QPoint(40, 40))
        self.snap(label, menu)
        menu.close()

    def note_skipped(self, text):
        if text not in self.skipped:
            self.skipped.append(text)

    # -- windows -----------------------------------------------------------------------------------------

    def window(self, size=SIZE, burner=None, lib=None):
        """`Context.window`, optionally on another library (an empty one, for a first start)."""
        saved = self.lib
        if lib is not None:
            self.lib = lib
        try:
            return super().window(size, burner)
        finally:
            self.lib = saved

    def temp_disc(self, name, **kw):
        """A disc that exists for this screen only (it is deleted afterwards)."""
        disc_id = self.lib.create_disc(name, **kw)
        self.on_cleanup(lambda: self._drop_disc(disc_id))
        return disc_id

    def _drop_disc(self, disc_id):
        from dubcrate.library.store import LibraryError

        try:
            self.lib.delete_disc(disc_id)
        except LibraryError:
            pass


# -- the recorder that replaces every modal ------------------------------------------------------------


def install_modal_capture():
    """Make every modal photograph itself into the current screen's extras and answer with a safe default."""
    from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMenu, QMessageBox

    def snap(widget, label=None):
        ctx = CURRENT[-1] if CURRENT else None
        if ctx is None:
            return
        kind = type(widget).__name__
        title = widget.windowTitle() if hasattr(widget, "windowTitle") else ""
        if not label and not title and isinstance(widget, QMessageBox):
            first = " ".join(widget.text().split())[
                :48
            ]  # (macOS gives message boxes no window title)
            label = f"Message: {first}" if first else "Message"
        ctx.snap(label or (f"{title}" if title else kind), widget)

    def box_static(icon, answer):
        def handler(parent, title, text, buttons=None, default=None, *_a, **_k):
            standard = QMessageBox.StandardButton
            box = QMessageBox(icon, str(title), str(text), buttons or standard.Ok)
            if default is not None and default != standard.NoButton:
                box.setDefaultButton(default)
            snap(box, f"{title}: {' '.join(str(text).split())[:40]}")
            if answer == "question":
                return (
                    standard.No
                    if (buttons is None or buttons & standard.No)
                    else standard.Cancel
                )
            return standard.Ok

        return staticmethod(handler)

    QMessageBox.question = box_static(QMessageBox.Icon.Question, "question")
    QMessageBox.information = box_static(QMessageBox.Icon.Information, "ok")
    QMessageBox.warning = box_static(QMessageBox.Icon.Warning, "ok")
    QMessageBox.critical = box_static(QMessageBox.Icon.Critical, "ok")

    def exec_dialog(self, *_args):
        snap(self)
        return 0

    QDialog.exec = exec_dialog
    QMessageBox.exec = exec_dialog

    def exec_menu(self, *_args):
        snap(self)

    QMenu.exec = exec_menu

    def get_text(parent, title, label, *args, **kwargs):
        text = kwargs.get("text", "")
        box = QInputDialog()
        box.setWindowTitle(str(title))
        box.setLabelText(str(label))
        box.setTextValue(str(text))
        snap(box, f"{title}: {label}")
        return "", False

    QInputDialog.getText = staticmethod(get_text)

    def chooser(kind, result):
        def handler(parent=None, caption="", *_a, **_k):
            if CURRENT:
                CURRENT[-1].note_skipped(
                    f"{kind}: “{caption}” (the system's own window)"
                )
            return result

        return staticmethod(handler)

    QFileDialog.getExistingDirectory = chooser("Folder chooser", "")
    QFileDialog.getOpenFileName = chooser("File chooser", ("", ""))
    QFileDialog.getSaveFileName = chooser("Save-as chooser", ("", ""))


# -- the scenes ----------------------------------------------------------------------------------------


def _library(ctx, size=LAPTOP):
    """The Library tab as a user sees it: the window's own pane widths (the screenshot recipes force wider ones), a playlist
    shown, a track loaded, the discs expanded."""
    window = ctx.window((size[1], size[2]))
    ctx.open_playlist(window, BROWSE)
    ctx.load_track(window, 2)
    recipes._stage_cues(window)
    window.refresh_discs()
    window.disc_tree.expandAll()
    ctx.settle(6)
    return window


# -- Main window -------------------------------------------------------------------------------------


@scene(
    "main-library",
    "Main window",
    "Library: a playlist, a loaded track, the discs",
    sizes=ALL_SCREENS,
    about="The window as it is used: a playlist shown, a track loaded in the player, the discs on the right.",
)
def _main_library(ctx):
    return _library(ctx)


@scene(
    "main-first-run",
    "Main window",
    "A first start: nothing imported yet",
    sizes=(LAPTOP,),
    about="An empty library, as the app looks the first time it is opened.",
)
def _main_first_run(ctx):
    from dubcrate.library import Library

    lib = Library.open(":memory:")
    ctx.on_cleanup(lib.close)
    return ctx.window((LAPTOP[1], LAPTOP[2]), lib=lib)


@scene(
    "main-all-tracks",
    "Main window",
    "All Tracks, nothing loaded in the player",
    sizes=(LAPTOP,),
    about="The whole library with no playlist picked and an empty player.",
)
def _main_all_tracks(ctx):
    window = ctx.window((LAPTOP[1], LAPTOP[2]))
    ctx.wait_for(lambda: window.track_model.rowCount() > 0)
    ctx.stage_previews(window)
    return window


@scene(
    "main-search-no-match",
    "Main window",
    "A search with no matches",
    sizes=(LAPTOP,),
    about="Typing something that matches no track: how an empty list reads.",
)
def _main_search_none(ctx):
    window = ctx.window((LAPTOP[1], LAPTOP[2]))
    window.search.setText("zzzzzz")
    window.refresh_tracks()
    ctx.settle(8)
    return window


@scene(
    "main-explorer",
    "Main window",
    "Explorer tab with a pinned folder",
    sizes=(LAPTOP,),
    about="The Explorer beside a pinned folder's tab, a folder of loose MP3s shown.",
)
def _main_explorer(ctx):
    window = ctx.window((LAPTOP[1], LAPTOP[2]))
    misc = ctx.demo.music / "Misc MP3s"
    window.pin_folder(str(misc))
    window.source_tabs.set_current("explorer")
    window.explorer_tree.select_path(str(ctx.demo.music / SOURCE / WARM_UP))
    ctx.settle()
    window.explorer_tree.select_path(str(misc))
    ctx.wait_for(
        lambda: (
            window.track_model.rowCount() > 0
            and not window.statusBar().currentMessage()
        )
    )
    ctx.stage_previews(window)
    return window


@scene(
    "main-busy-import",
    "Main window",
    "Busy: an import running, with Cancel",
    sizes=(LAPTOP,),
    about="The status bar while a folder is being scanned: the progress bar, the count and the Cancel button.",
)
def _main_busy(ctx):
    from dubcrate.cancel import CancelToken

    window = _library(ctx)
    window._set_busy(True, "Scanning Demo Crate…", CancelToken())
    window._on_progress(40, 120)
    ctx.settle(4)
    return window


@scene(
    "main-status-badges",
    "Main window",
    "Status bar: update available and library writable",
    sizes=(LAPTOP,),
    about="The two badges in the status bar: the blue 'Update available' button and the orange 'Library writable' badge.",
)
def _main_badges(ctx):
    from dubcrate.updates import UpdateInfo

    window = _library(ctx)
    bar = window.statusBar()
    bar.set_update(
        UpdateInfo(
            "0.2.0", "v0.2.0", "Dubcrate 0.2.0", "https://example.invalid", "notes"
        )
    )
    bar.set_library_badge(True)
    bar.showMessage("Added 3 tracks to “Friday Warm Up”")
    ctx.settle(4)
    return window


@scene(
    "main-badge-readonly",
    "Main window",
    "Status bar: library read-only",
    sizes=(LAPTOP,),
    about="The yellow 'Library read-only' badge (a rekordbox library is imported but editing is off).",
)
def _main_badge_ro(ctx):
    window = _library(ctx)
    window.statusBar().set_library_badge(False)
    ctx.settle(4)
    return window


# -- Player ------------------------------------------------------------------------------------------


def _strip(ctx, window):
    return Focus(window, window.player_strip)


@scene(
    "player-empty",
    "Player",
    "Player with no track loaded",
    sizes=(LAPTOP,),
    about="The player strip before anything is loaded.",
)
def _player_empty(ctx):
    window = ctx.window((LAPTOP[1], LAPTOP[2]))
    return _strip(ctx, window)


@scene(
    "player-loaded-plain",
    "Player",
    "Player: a folder track (no cues, no beat grid)",
    sizes=(LAPTOP,),
    about="A track from a plain folder: the waveform with no phrases, cues or beat grid.",
)
def _player_plain(ctx):
    window = ctx.window((LAPTOP[1], LAPTOP[2]))
    ctx.open_playlist(window, BROWSE)
    ctx.load_track(window, 2)
    return _strip(ctx, window)


@scene(
    "player-cues-memory",
    "Player",
    "Player: Memory list, cues and a loop",
    sizes=(LAPTOP,),
    about="A track with memory cues, hot cues and a memory loop; the Memory tab open.",
)
def _player_memory(ctx):
    return _strip(ctx, _library(ctx))


@scene(
    "player-cues-hot",
    "Player",
    "Player: Hot Cue tab",
    sizes=(LAPTOP,),
    about="The same track with the Hot Cue tab open.",
)
def _player_hot(ctx):
    window = _library(ctx)
    window.player_strip.side.tabs[1].click()
    ctx.settle(6)
    return _strip(ctx, window)


@scene(
    "player-info",
    "Player",
    "Player: Info tab",
    sizes=(LAPTOP,),
    about="The Info tab: the track's tags and the (painted) note where cover art would be.",
)
def _player_info(ctx):
    window = _library(ctx)
    window.player_strip.side.tabs[2].click()
    ctx.settle(6)
    return _strip(ctx, window)


@scene(
    "player-loop-cue",
    "Player",
    "Player: cue point, auto loop and quantise",
    sizes=(LAPTOP,),
    about="CUE pressed away from the start, an 8 beat auto loop on, quantise lit.",
)
def _player_loop(ctx):
    window = _library(ctx)
    preview = window.preview
    preview.player.pause()
    window.player_strip.quantise_button.click()
    preview.player.seek(8.0)
    preview.cue_pressed()
    preview.player.seek(76.0)
    window.player_strip.loop_size_forward.click()
    window.player_strip.loop_size.click()
    ctx.settle(8)
    return _strip(ctx, window)


@scene(
    "player-playing",
    "Player",
    "Player: playing",
    sizes=(LAPTOP,),
    about="The play button in its playing state (the sound card is a silent stand-in).",
)
def _player_playing(ctx):
    window = _library(ctx)
    window.preview.player.play()
    ctx.settle(8)
    return _strip(ctx, window)


@scene(
    "player-zoomed-out",
    "Player",
    "Player: waveform zoomed all the way out",
    sizes=(LAPTOP,),
    about="The zoomed waveform at 32 seconds, the widest zoom.",
)
def _player_zoomed_out(ctx):
    window = _library(ctx)
    window.player_strip.detail.set_window_seconds(32.0)
    ctx.settle(6)
    return _strip(ctx, window)


@scene(
    "player-look-rgb",
    "Player",
    "Player: RGB waveform look",
    sizes=(LAPTOP,),
    about="The RGB waveform look (Settings > Appearance > Waveform style).",
)
def _player_rgb(ctx):
    from dubcrate import settings
    from dubcrate.ui import wave_style

    window = _library(ctx)
    settings.set_wave_style("rgb")
    wave_style.notifier.changed.emit()
    ctx.settle(8)
    return _strip(ctx, window)


@scene(
    "player-look-blue",
    "Player",
    "Player: Blue waveform look",
    sizes=(LAPTOP,),
    about="The Blue waveform look.",
)
def _player_blue(ctx):
    from dubcrate import settings
    from dubcrate.ui import wave_style

    window = _library(ctx)
    settings.set_wave_style("blue")
    wave_style.notifier.changed.emit()
    ctx.settle(8)
    return _strip(ctx, window)


# -- Discs -------------------------------------------------------------------------------------------


@scene(
    "disc-audio",
    "Discs",
    "An audio disc open",
    sizes=(LAPTOP,),
    about="Saturday Peak (a 74-minute disc): its list, the capacity bar and the buttons.",
)
def _disc_audio(ctx):
    window = _library(ctx)
    window.open_disc(ctx.demo.discs["peak"])
    ctx.settle(8)
    return window


@scene(
    "disc-audio-over",
    "Discs",
    "An audio disc that is too long",
    sizes=(LAPTOP,),
    about="A disc with more music than it holds: the over-length warning in the bar, the Discs list and the track list.",
)
def _disc_over(ctx):
    window = _library(ctx)
    disc = ctx.temp_disc("Too Much Music", capacity_sectors=60_000)
    tracks = ctx.lib.tracks(node_id=ctx.demo.node(BROWSE).id)
    ctx.lib.add_to_disc(disc, [t.id for t in tracks])
    window.refresh_discs()
    window.open_disc(disc)
    ctx.settle(8)
    return window


@scene(
    "disc-audio-empty",
    "Discs",
    "A new, empty disc",
    sizes=(LAPTOP,),
    about="A disc with no tracks yet.",
)
def _disc_empty(ctx):
    window = _library(ctx)
    window.open_disc(
        next(d.id for d in ctx.lib.discs() if d.name == "Open Format Practice")
    )
    ctx.settle(8)
    return window


@scene(
    "disc-mp3-root",
    "Discs",
    "An MP3 disc: its root",
    sizes=(LAPTOP,),
    about="Car USB, an MP3 disc, with its folders listed under it in the Discs list.",
)
def _disc_mp3_root(ctx):
    window = _library(ctx)
    window.open_disc(ctx.demo.discs["usb"])
    window.refresh_discs()
    window.disc_tree.expandAll()
    ctx.settle(8)
    return window


@scene(
    "disc-mp3-folder",
    "Discs",
    "An MP3 disc: a folder open",
    sizes=(LAPTOP,),
    about="A folder of the MP3 disc: its files, the path above them and the folder button in the tree.",
)
def _disc_mp3_folder(ctx):
    from dubcrate.ui.models import find_selection

    window = _library(ctx)
    usb = ctx.demo.discs["usb"]
    folder = next(d for d in ctx.lib.disc_dirs(usb) if d.name == "UK Garage")
    window.disc_view.set_disc(usb, folder.id)
    window.center.setCurrentIndex(1)
    window.refresh_discs()
    window.disc_tree.expandAll()
    window.disc_tree.setCurrentIndex(
        find_selection(window.disc_tree.model(), ("disc_dir", usb, folder.id))
    )
    ctx.settle(8)
    return window


@scene(
    "disc-mp3-over",
    "Discs",
    "An MP3 disc that is too big",
    sizes=(LAPTOP,),
    about="An MP3 disc over its size: the bar in megabytes.",
)
def _disc_mp3_over(ctx):
    window = _library(ctx)
    disc = ctx.temp_disc(
        "Big MP3 Disc", kind="mp3", mp3_bitrate=320, capacity_sectors=2_500
    )
    tracks = ctx.lib.tracks(node_id=ctx.demo.node(BROWSE).id)
    ctx.lib.add_to_disc(disc, [t.id for t in tracks])
    window.refresh_discs()
    window.open_disc(disc)
    ctx.settle(8)
    return window


# -- Burn window -------------------------------------------------------------------------------------


class StandInBurner:
    """A burner that never writes. `hold` (an Event) keeps `burn` waiting until the screen is done, to show the
    window mid-burn; `fail` makes it raise a BurnError; `media_error` makes the drive check fail."""

    name = "stand-in"

    def __init__(self, media=None, drives=(), fail="", hold=None, media_error=""):
        from dubcrate.burn import MediaInfo

        self.media = media or MediaInfo(
            drive="USB CD Writer", present=True, blank=True, free_sectors=359_849
        )
        self._drives = list(drives)
        self.fail, self.hold, self.media_error = fail, hold, media_error

    def drives(self):
        return list(self._drives)

    def media_info(self, device):
        from dubcrate.burn import BurnError

        if self.media_error:
            raise BurnError(self.media_error)
        return self.media

    def burn(self, disc, options, progress):
        from dubcrate.burn import BurnError

        progress("Preparing")
        if self.hold is not None:
            progress("Writing  12.0%")
            self.hold.wait(30)
        if self.fail:
            raise BurnError(self.fail)
        progress("Writing 100.0%")
        progress("Finishing")

    burn_data = burn


def _stand_in_render(hold=None):
    """A `render_plan` that does not decode anything: it says what it is on, can wait (for the screen that shows the
    window while a disc is being prepared) and stops for a Cancel."""
    import time
    from pathlib import Path

    from dubcrate.build import Rendered
    from dubcrate.burn.base import DataDisc, Disc, DiscTrack

    def render(plan, workdir, say, cancel=None):
        total = len(plan.tracks)
        for n, t in enumerate(plan.tracks[:3], start=1):
            say(f"rendering {n}/{total}: {t.title}")
        if hold is not None:
            say(f"rendering 4/{total}: {plan.tracks[3].title}")
            while not hold.is_set() and not (cancel and cancel.cancelled):
                time.sleep(0.02)
            if cancel is not None:
                cancel.check()
        if plan.kind == "mp3":
            return Rendered(DataDisc(title=plan.name, folder=Path(workdir)), 0, {})
        tracks = [
            DiscTrack(Path("x.wav"), t.title, t.artist, 5000) for t in plan.tracks
        ]
        return Rendered(
            Disc(title=plan.name, performer=plan.performer, tracks=tracks), 0, {}
        )

    return render


def _burn_window(
    ctx, disc_id=None, burner=None, lib=None, render=None, toc=True, export_only=False
):
    """The burn window as `MainWindow.burn_current_disc` makes it, on a stand-in burner. The renderer is a
    stand-in too (decoding a whole disc is not what is being looked at); the TOC check after a burn is the real
    one, fed the planned TOC as if the drive had read it."""
    from unittest import mock

    from dubcrate import burn_check
    from dubcrate import cdj_card as cc
    from dubcrate.ui.burn_dialog import BurnDialog

    lib = lib or ctx.lib
    burner = burner or StandInBurner()
    patches = [
        mock.patch("dubcrate.ui.burn_dialog.render_plan", render or _stand_in_render()),
        mock.patch("dubcrate.ui.burn_dialog.can_read_toc", lambda: toc),
        mock.patch(
            "dubcrate.ui.burn_dialog.check_burn",
            lambda sectors, gap, say: burn_check.check_burn(
                sectors,
                gap,
                say,
                read=lambda **_k: cc.toc_from_lengths(sectors, gap),
                eject=lambda: None,
            ),
        ),
    ]
    for p in patches:
        p.start()
        ctx.on_cleanup(p.stop)
    dialog = BurnDialog(
        lib,
        disc_id or ctx.demo.discs["warm"],
        burner_factory=lambda: burner,
        export_only=export_only,
    )
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.wait_for(lambda: not dialog._busy)
    return dialog


def _isolated_disc(ctx, tracks=5, **kw):
    """A disc made for this screen alone (a burn marks its disc as burned, and the demo discs are used again)."""
    disc = ctx.temp_disc("Friday Night", **kw)
    found = ctx.lib.tracks(node_id=ctx.demo.node(BROWSE).id)[:tracks]
    ctx.lib.add_to_disc(disc, [t.id for t in found])
    return disc


def _release(ctx, hold, dialog):
    """Let a held burn finish and wait for its worker before the window is torn down."""

    def done():
        hold.set()
        try:
            ctx.wait_for(lambda: not dialog._busy, timeout=10)
        except TimeoutError:
            pass

    ctx.on_cleanup(done)


@scene("burn-fresh", "Burn window", "Burn: just opened", kind="dialog",
       about="Drive checked, blank disc found: Burn is live.")  # fmt: skip
def _burn_fresh(ctx):
    return _burn_window(ctx)


@scene("burn-levels-gentle", "Burn window", "Burn: level matching on (Gentle)", kind="dialog",
       about="Normalise ticked at -14 LUFS, measured on the real demo tracks: the range of changes is shown.")  # fmt: skip
def _burn_levels(ctx):
    dialog = _burn_window(ctx)
    dialog.levels_combo.setCurrentIndex(dialog.levels_combo.findData("-14"))
    dialog.levels_check.setChecked(True)
    ctx.wait_for(lambda: not dialog._measuring, timeout=120)
    ctx.settle(4)
    return dialog


@scene("burn-levels-held", "Burn window", "Burn: level matching, some tracks held back", kind="dialog",
       about="Normalise at -10 LUFS on quiet tracks: the warning that gains were held back for lack of headroom.")  # fmt: skip
def _burn_levels_held(ctx):
    dialog = _burn_window(ctx)
    dialog.levels_check.setChecked(True)
    ctx.wait_for(lambda: not dialog._measuring, timeout=120)
    ctx.settle(4)
    return dialog


@scene("burn-mp3", "Burn window", "Burn: an MP3 disc", kind="dialog",
       about="An MP3 CD: the title says MP3 CD, there is no levels row or gap, and the size is in megabytes.")  # fmt: skip
def _burn_mp3(ctx):
    dialog = _burn_window(ctx, ctx.demo.discs["usb"])
    ctx.settle(4)
    return dialog


@scene("burn-two-drives", "Burn window", "Burn: two drives, a choice", kind="dialog",
       about="Two drives connected: the drive menu appears beside the drive line.")  # fmt: skip
def _burn_two_drives(ctx):
    from dubcrate.burn import Drive

    burner = StandInBurner(
        drives=[Drive("disk3", "LITE-ON eSAU108"), Drive("disk4", "ASUS SDRW-08D2S")]
    )
    dialog = _burn_window(ctx, burner=burner)
    ctx.settle(4)
    return dialog


def _media_scene(id, title, about, media=None, error=""):
    @scene(id, "Burn window", title, kind="dialog", about=about)
    def build(ctx):
        from dubcrate.burn import MediaInfo

        found = MediaInfo(**media) if media else None
        dialog = _burn_window(ctx, burner=StandInBurner(media=found, media_error=error))
        ctx.settle(4)
        return dialog

    return build


_media_scene("burn-no-disc", "Burn: no disc in the drive",
             "The drive is empty.", {"drive": "USB CD Writer", "present": False})  # fmt: skip
_media_scene("burn-disc-not-blank", "Burn: the disc isn't blank",
             "A written disc is in the drive.", {"drive": "USB CD Writer", "present": True, "blank": False, "free_sectors": 0})  # fmt: skip
_media_scene("burn-disc-too-small", "Burn: the disc is too small",
             "A blank 74-minute disc for a longer playlist.", {"drive": "USB CD Writer", "present": True, "blank": True, "free_sectors": 20_000})  # fmt: skip
_media_scene("burn-drive-unreachable", "Burn: the drive can't be reached",
             "The drive check fails with the burner's own message.", None,
             "DiscRecording found no optical drives")  # fmt: skip


@scene("burn-disc-empty", "Burn window", "Burn: the disc is empty", kind="dialog",
       about="A disc with no tracks: the red list of problems and no Burn.")  # fmt: skip
def _burn_empty(ctx):
    return _burn_window(ctx, ctx.temp_disc("Nothing Yet"))


@scene("burn-disc-over", "Burn window", "Burn: the disc is over its size", kind="dialog",
       about="More music than the disc holds.")  # fmt: skip
def _burn_over(ctx):
    disc = ctx.temp_disc("Too Much Music", capacity_sectors=60_000)
    ctx.lib.add_to_disc(
        disc, [t.id for t in ctx.lib.tracks(node_id=ctx.demo.node(BROWSE).id)]
    )
    return _burn_window(ctx, disc)


@scene("burn-files-missing", "Burn window", "Burn: files missing or gone from the source", kind="dialog",
       about="Tracks whose files have moved, or that are no longer in their source.")  # fmt: skip
def _burn_missing(ctx):
    from dubcrate.library import Library, TrackRecord

    lib = Library.open(":memory:")
    ctx.on_cleanup(lib.close)
    records = [
        TrackRecord(ext_id=str(i), path=f"/Volumes/DJ Drive/Crates/track-{i}.mp3", title=f"Lost Track {i}", artist="Some Artist", duration=240.0)
        for i in range(3)
    ]  # fmt: skip
    lib.replace_source("folder", "/Volumes/DJ Drive/Crates", "Crates", records)
    disc = lib.create_disc("Gig Disc")
    lib.add_to_disc(disc, [t.id for t in lib.tracks()])
    lib.replace_source(
        "folder", "/Volumes/DJ Drive/Crates", "Crates", records[:2]
    )  # (one has left its source)
    return _burn_window(ctx, disc, lib=lib)


@scene("burn-unsupported", "Burn window", "Burn: this computer can't burn", kind="dialog",
       about="No burn backend here (Linux without cdrdao): the message and Export… as the way out.")  # fmt: skip
def _burn_unsupported(ctx):
    from unittest import mock

    patch = mock.patch(
        "dubcrate.ui.burn_dialog.backend_problem",
        lambda name: (
            "Dubcrate burns with cdrdao here, and it was not found. Install it with your package manager"
            " (for example `sudo apt install cdrdao`), then restart Dubcrate."
        ),
    )
    patch.start()
    ctx.on_cleanup(patch.stop)
    return _burn_window(ctx)


@scene("burn-preparing", "Burn window", "Burn: preparing the disc (Cancel offered)", kind="dialog",
       about="Making the files, with the Cancel button: nothing has been written yet.")  # fmt: skip
def _burn_preparing(ctx):
    import threading

    hold = threading.Event()
    dialog = _burn_window(ctx, ctx.demo.discs["warm"], render=_stand_in_render(hold))
    dialog.burn_button.click()
    ctx.wait_for(lambda: "rendering 4/" in dialog.log.toPlainText())
    _release(ctx, hold, dialog)
    return dialog


@scene("burn-writing", "Burn window", "Burn: writing (no Cancel)", kind="dialog",
       about="The laser is on: the Cancel button has gone because a disc can't be stopped half written.")  # fmt: skip
def _burn_writing(ctx):
    import threading

    hold = threading.Event()
    disc = _isolated_disc(ctx)
    dialog = _burn_window(ctx, disc, burner=StandInBurner(hold=hold))
    dialog.burn_button.click()
    ctx.wait_for(lambda: "Writing  12.0%" in dialog.log.toPlainText())
    _release(ctx, hold, dialog)
    return dialog


@scene("burn-finished", "Burn window", "Burn: finished", kind="dialog",
       about="A real burn done: the table-of-contents check, Print Labels… beside Export…, the disc marked as burned.")  # fmt: skip
def _burn_finished(ctx):
    dialog = _burn_window(ctx, _isolated_disc(ctx))
    dialog.burn_button.click()
    ctx.wait_for(lambda: dialog._finished)
    ctx.settle(4)
    return dialog


@scene("burn-failed", "Burn window", "Burn: failed", kind="dialog",
       about="The drive reports an error while writing.")  # fmt: skip
def _burn_failed(ctx):
    burner = StandInBurner(
        fail="The drive reported an error while writing (the disc may be damaged). Try another disc."
    )
    dialog = _burn_window(ctx, _isolated_disc(ctx), burner=burner)
    dialog.burn_button.click()
    ctx.wait_for(lambda: "failed" in dialog.status_label.text())
    return dialog


@scene("burn-cancelled", "Burn window", "Burn: cancelled while preparing", kind="dialog",
       about="Cancel pressed while the files were being made: nothing was written.")  # fmt: skip
def _burn_cancelled(ctx):
    import threading

    hold = threading.Event()
    dialog = _burn_window(ctx, ctx.demo.discs["warm"], render=_stand_in_render(hold))
    dialog.burn_button.click()
    ctx.wait_for(lambda: "rendering 4/" in dialog.log.toPlainText())
    dialog.cancel_burn_button.click()
    ctx.wait_for(lambda: "Cancelled." in dialog.log.toPlainText())
    return dialog


@scene("burn-export-done", "Burn window", "Burn: exported as files", kind="dialog",
       about="Export… finished (a stand-in writes nothing): the status line and the log.")  # fmt: skip
def _burn_export(ctx):
    from pathlib import Path
    from types import SimpleNamespace
    from unittest import mock

    result = SimpleNamespace(
        folder=Path("/Users/dj/Desktop/Friday Warm Up"),
        readme=Path("README.txt"),
        lengths={},
        waveforms={},
    )

    def export(plan, parent, say):
        say("rendering 1/16: Overtime (Radio Edit)")
        say("writing disc.toc")
        return result

    for p in (
        mock.patch(
            "dubcrate.ui.burn_dialog.ask_export_folder",
            lambda parent: "/Users/dj/Desktop",
        ),
        mock.patch("dubcrate.ui.burn_dialog.export_plan", export),
    ):
        p.start()
        ctx.on_cleanup(p.stop)
    dialog = _burn_window(ctx, export_only=True)
    dialog.export_button.click()
    ctx.wait_for(lambda: "Exported to" in dialog.status_label.text())
    return dialog


# -- Label window ------------------------------------------------------------------------------------


def _label_window(ctx, *disc_ids, parent=None):
    """The label window with `disc_ids` ticked (the others unticked)."""
    from PySide6.QtCore import Qt

    from dubcrate.ui.label_dialog import LabelDialog

    dialog = LabelDialog(ctx.lib, parent, picked=disc_ids[0] if disc_ids else None)
    for item in dialog._disc_items():
        on = item.data(0, Qt.ItemDataRole.UserRole) in disc_ids
        item.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.settle(10)
    return dialog


LABEL_SIZES = (("1120x780 window", 1120, 780), ("900x620 window", 900, 620), SMALLEST)


@scene("labels-default", "Label window", "Labels: one disc ticked", LABEL_SIZES, kind="dialog",
       about="The window as it opens from Disc > Print Labels…, with the disc ticked and the preview drawn.")  # fmt: skip
def _labels_default(ctx):
    return _label_window(ctx, ctx.demo.discs["warm"])


@scene("labels-nothing-ticked", "Label window", "Labels: nothing ticked", sizes=(NATURAL,), kind="dialog",
       about="No disc ticked: the hint where the preview would be.")  # fmt: skip
def _labels_nothing(ctx):
    return _label_window(ctx)


@scene("labels-two-discs", "Label window", "Labels: two discs on one page", sizes=(NATURAL,), kind="dialog",
       about="Two discs share a sheet of sticker paper.")  # fmt: skip
def _labels_two(ctx):
    return _label_window(ctx, ctx.demo.discs["warm"], ctx.demo.discs["peak"])


@scene("labels-mp3-folders", "Label window", "Labels: an MP3 disc with folders", sizes=(NATURAL,), kind="dialog",
       about="Folders of an MP3 disc as headings in the list.")  # fmt: skip
def _labels_mp3(ctx):
    return _label_window(ctx, ctx.demo.discs["usb"])


@scene("labels-all-columns", "Label window", "Labels: BPM, key, year and duration columns, three text columns", sizes=(NATURAL,), kind="dialog",
       about="Every optional column on and the list spread over three columns.")  # fmt: skip
def _labels_columns(ctx):
    dialog = _label_window(ctx, ctx.demo.discs["peak"])
    dialog.year_check.setChecked(True)
    dialog.duration_check.setChecked(True)
    dialog.columns_combo.setCurrentIndex(dialog.columns_combo.findData(3))
    ctx.settle(10)
    return dialog


@scene("labels-too-small", "Label window", "Labels: text too small to read", sizes=(NATURAL,), kind="dialog",
       about="A label too short for the disc's tracks: the warning under the settings.")  # fmt: skip
def _labels_small(ctx):
    dialog = _label_window(ctx, ctx.demo.discs["warm"])
    dialog.per_disc_spin.setValue(1)
    dialog.height_spin.set_mm(20)
    ctx.settle(10)
    return dialog


@scene("labels-letter-inches", "Label window", "Labels: Letter paper, sizes in inches", sizes=(NATURAL,), kind="dialog",
       about="Settings > Appearance > Units set to inches, paper changed to Letter.")  # fmt: skip
def _labels_inches(ctx):
    from dubcrate import settings

    settings.set_units("in")
    dialog = _label_window(ctx, ctx.demo.discs["warm"])
    dialog.page_combo.setCurrentIndex(dialog.page_combo.findText("Letter", flags=__import__("PySide6.QtCore").QtCore.Qt.MatchFlag.MatchStartsWith))  # fmt: skip
    ctx.settle(10)
    return dialog


@scene("labels-several-pages", "Label window", "Labels: several pages", sizes=(NATURAL,), kind="dialog",
       about="Small labels with many copies, so the page buttons and 'Page 1 of N' show.")  # fmt: skip
def _labels_pages(ctx):
    dialog = _label_window(
        ctx, ctx.demo.discs["warm"], ctx.demo.discs["peak"], ctx.demo.discs["usb"]
    )
    dialog.copies_spin.setValue(6)
    dialog.width_spin.set_mm(100)
    dialog.height_spin.set_mm(70)
    ctx.settle(10)
    return dialog


# -- Sync tab ----------------------------------------------------------------------------------------


def _sync_window(ctx):
    """The Sync tab on the staged card of the screenshot recipes (invented cues, one disc already written)."""
    return recipes._sync(ctx)


def _card_path(ctx):
    return ctx.demo.home.parent.parent / "volumes" / "CDJ-CARD"


@scene("sync-card", "Sync tab", "Sync: a card and the discs", sizes=ALL_SCREENS,
       about="A CDJ card on the left, the audio discs and what a sync would do for each, one disc picked.")  # fmt: skip
def _sync_card(ctx):
    return _sync_window(ctx)


@scene("sync-track-waveform", "Sync tab", "Sync: a track picked, its waveform", sizes=(LAPTOP,),
       about="A track picked in the middle: its waveform drawn as the CDJ's display draws it.")  # fmt: skip
def _sync_wave(ctx):
    window = _sync_window(ctx)
    table = window.sync_view.browser.table
    table.setCurrentItem(table.topLevelItem(3))
    ctx.settle(8)
    return window


@scene("sync-no-card", "Sync tab", "Sync: no card connected", sizes=(LAPTOP,),
       about="Nothing in the card list.")  # fmt: skip
def _sync_none(ctx):
    from unittest import mock

    patch = mock.patch("dubcrate.ui.sync_view.mounted_volumes", return_value=[])
    patch.start()
    ctx.on_cleanup(patch.stop)
    window = ctx.window((LAPTOP[1], LAPTOP[2]))
    window.source_tabs.set_current("sync")
    ctx.settle(8)
    return window


@scene("sync-card-menu", "Sync tab", "Sync: the card's right-click menu", kind="menu", sizes=(LAPTOP,),
       about="Right-click on a card in the list.")  # fmt: skip
def _sync_card_menu(ctx):
    window = _sync_window(ctx)
    panel = window.card_panel
    ctx.snap_menu("right-click on a card", panel.card_menu(_card_path(ctx)))
    ctx.snap_menu("right-click on the empty list", panel.card_menu(None))
    return window


@scene("sync-card-unset", "Sync tab", "Sync: cards that are not set up yet", sizes=(LAPTOP,),
       about="A card with no PIONEER folder is listed under the cards, greyed, with a Format… button; one with files on it warns.")  # fmt: skip
def _sync_unset(ctx):
    from dubcrate import card_format as cf

    window = _sync_window(ctx)
    panel = window.card_panel

    def disk(device, name, size):
        return cf.CardDisk(device, name, size, "USB", True, True, False, False, True)

    staged = [
        cf.UnsetCard(disk("disk5", "STORAGE DEVICE", 2_013_265_920), False),
        cf.UnsetCard(disk("disk6", "Generic Card Reader", 1_900_000_000), True),
    ]
    panel._find_unset = lambda: staged  # (every look the tab makes finds the same two)
    panel._unset = staged
    panel._populate(panel.current_card())
    ctx.settle(8)
    return window


@scene("sync-confirm", "Sync tab", "Sync: asking before it writes", kind="message", sizes=(LAPTOP,),
       about="The question the Sync button asks, from the real text for the ticked disc.")  # fmt: skip
def _sync_confirm(ctx):
    window = _sync_window(ctx)
    window.sync_view._request_sync()
    ctx.settle(8)
    return window


@scene("sync-import-cues", "Sync tab", "Sync: Import Cues… review", BOTH, kind="dialog",
       about="What changed on the player (invented findings): added, moved, deleted cues, loops and hot cues.")  # fmt: skip
def _sync_import(ctx):
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

    disc_id = ctx.demo.discs["peak"]
    entries = ctx.lib.disc_entries(disc_id)
    tracks = [
        TrackFinding(entries[1].track, 2, entries[1].title, entries[1].artist, [Row(ADD, 41500), Row(LOOP, 97000, 101000), Row(REMOVE, 12000)]),
        TrackFinding(entries[4].track, 5, entries[4].title, entries[4].artist, [Row(MOVE, 18000, old_ms=15500), Row(HOT, 64000, slot=1, ticked=False)]),
    ]  # fmt: skip
    summary = ctx.lib.disc(disc_id)
    finding = DiscFinding(
        disc_id,
        summary.name,
        f"CDJ CDs / {summary.name}",
        tracks,
        kept=1,
        has_baseline=True,
    )
    dialog = CardImportDialog([finding])
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.settle(8)
    return dialog


def _toc_manager(ctx, *, filled=True):
    from dubcrate.card_discs import disc_toc
    from dubcrate.ui.toc_manager_dialog import TocManagerDialog

    disc_id = ctx.temp_disc("Banger Finishers 002")
    ctx.lib.add_to_disc(
        disc_id, [e.track.id for e in ctx.lib.disc_entries(ctx.demo.discs["peak"])]
    )
    entries = ctx.lib.disc_entries(disc_id)
    planned = disc_toc(entries)
    if filled:
        starts, lead_out = planned
        later = lambda n: ([starts[0], *(x + n for x in starts[1:])], lead_out + n)
        ctx.lib.add_disc_toc(disc_id, planned, later(150), source="manual")
        ctx.lib.add_disc_toc(disc_id, planned, later(1), source="burn")
        old = ctx.lib.disc_tocs(disc_id)[-1]
        ctx.lib.set_disc_toc_enabled(old.id, False)
        # a record made for another plan (the playlist was edited since): only an edit can leave one, so put it in
        ctx.lib._conn.execute(
            "INSERT INTO disc_toc (disc_id, planned, actual, source, saved_at) VALUES (?, ?, ?, 'burn', ?)",
            (
                disc_id,
                "[[150, 9999], 20000]",
                json.dumps([list(later(2)[0]), later(2)[1]]),
                "2026-11-10T09:00:00",
            ),
        )
        ctx.lib._conn.commit()
    dialog = TocManagerDialog(ctx.lib, disc_id, None, read_toc=lambda: planned)
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.settle(8)
    return dialog


@scene("toc-manager", "Sync tab", "TOC Manager: several TOCs, one unticked", sizes=(NATURAL,), kind="dialog",
       about="A disc with a read-back TOC, one read from another copy (2 s gaps), one switched off.")  # fmt: skip
def _toc_manager_filled(ctx):
    return _toc_manager(ctx)


@scene("toc-manager-empty", "Sync tab", "TOC Manager: nothing recorded", sizes=(NATURAL,), kind="dialog",
       about="A disc with no recorded TOC: Sync uses the plan's.")  # fmt: skip
def _toc_manager_empty(ctx):
    return _toc_manager(ctx, filled=False)


def _disc_image(ctx, *, recorded=True):
    from dubcrate.library.models import DiscImage
    from dubcrate.ui.disc_image_dialog import DiscImageDialog

    disc_id = ctx.temp_disc("Banger Finishers 002", kind="mp3")
    ctx.lib.add_to_disc(
        disc_id, [e.track.id for e in ctx.lib.disc_entries(ctx.demo.discs["peak"])]
    )
    if recorded:
        ctx.lib.set_disc_image(
            disc_id,
            DiscImage(
                "2026-11-23T19:47:51+11:00", "BANGER FINISHERS 0", 15, 3, 80245, 84029
            ),
        )
    dialog = DiscImageDialog(ctx.lib, disc_id, None)
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.settle(8)
    return dialog


@scene("disc-image", "Sync tab", "Disc ID: an MP3 disc's image recorded", sizes=(NATURAL,), kind="dialog",
       about="An MP3 disc burned here: the image's date, label and numbers the CDJ's ID is made from.")  # fmt: skip
def _disc_image_recorded(ctx):
    return _disc_image(ctx)


@scene("disc-image-empty", "Sync tab", "Disc ID: nothing recorded", sizes=(NATURAL,), kind="dialog",
       about="A disc burned before the record was kept: read it from the disc in the drive or an image file.")  # fmt: skip
def _disc_image_empty(ctx):
    return _disc_image(ctx, recorded=False)


def _format_disks():
    from dubcrate import card_format as cf

    return [
        cf.CardDisk("disk4", "Generic STORAGE DEVICE", 2_013_265_920, "USB", True, True, False, False, True),
        cf.CardDisk("disk5", "SDXC card", 128_000_000_000, "USB", True, True, False, False, True),
        cf.CardDisk("disk0", "APPLE SSD", 500_000_000_000, "PCI-Express", True, False, False, True, True),
    ]  # fmt: skip


def _format_dialog(ctx, disks=_format_disks, formatter=None):
    from dubcrate.ui.format_card_dialog import FormatCardDialog

    dialog = FormatCardDialog(None, list_disks=disks, formatter=formatter)
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.wait_for(
        lambda: dialog.list.count() > 0 or "No removable" in dialog.status.text()
    )
    return dialog


@scene("format-card-list", "Sync tab", "Format a Card: disks listed", BOTH, kind="dialog",
       about="A usable 2 GB card, a card that is too big (SDXC) and the system disk, with the reasons.")  # fmt: skip
def _format_list(ctx):
    return _format_dialog(ctx)


@scene("format-card-ready", "Sync tab", "Format a Card: ticked, ready to erase", sizes=(NATURAL,), kind="dialog",
       about="The usable card picked and the 'I understand' box ticked.")  # fmt: skip
def _format_ready(ctx):
    dialog = _format_dialog(ctx)
    dialog.understand.setChecked(True)
    ctx.settle(4)
    return dialog


@scene("format-card-none", "Sync tab", "Format a Card: no card found", sizes=(NATURAL,), kind="dialog",
       about="No removable disk at all.")  # fmt: skip
def _format_none(ctx):
    return _format_dialog(ctx, disks=list)


@scene("format-card-done", "Sync tab", "Format a Card: done", sizes=(NATURAL,), kind="dialog",
       about="The card formatted (a stand-in formatter): the result line and Close.")  # fmt: skip
def _format_done(ctx):
    from pathlib import Path
    from types import SimpleNamespace

    def formatter(device, disk):
        return SimpleNamespace(
            volume=Path("/Volumes/DISK_IMG"),
            folder=Path("/Volumes/DISK_IMG/PIONEER/CDJ"),
            disk=disk,
        )

    dialog = _format_dialog(ctx, formatter=formatter)
    dialog.understand.setChecked(True)
    dialog.format_button.click()
    ctx.wait_for(lambda: dialog._done)
    return dialog


@scene("format-card-failed", "Sync tab", "Format a Card: failed", sizes=(NATURAL,), kind="dialog",
       about="The system refused to format the card.")  # fmt: skip
def _format_failed(ctx):
    from dubcrate import card_format

    def formatter(device, disk):
        raise card_format.FormatError(
            "diskutil said: Unable to unmount the disk. Close anything using the card and try again."
        )

    dialog = _format_dialog(ctx, formatter=formatter)
    dialog.understand.setChecked(True)
    dialog.format_button.click()
    ctx.wait_for(lambda: "Couldn't format" in dialog.result.text())
    return dialog


# -- Settings ----------------------------------------------------------------------------------------


def _settings_tab(id, tab):
    @scene(f"settings-{id}", "Settings", f"Settings: {tab}", BOTH, kind="dialog",
           about=f"The {tab} tab of Settings.")  # fmt: skip
    def build(ctx):
        from PySide6.QtWidgets import QTabWidget

        from dubcrate.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog()
        tabs = dialog.findChild(QTabWidget)
        for i in range(tabs.count()):
            if tabs.tabText(i) == tab:
                tabs.setCurrentIndex(i)
        dialog.show()
        ctx.on_cleanup(dialog.close)
        ctx.settle(8)
        return dialog

    return build


for _id, _tab in (
    ("audio", "Audio"),
    ("discs", "Discs"),
    ("burning", "Burning"),
    ("library", "Library"),
    ("appearance", "Appearance"),
):
    _settings_tab(_id, _tab)


# -- Messages and questions ---------------------------------------------------------------------------
# Each of these calls the real code that raises the box; the recorder photographs it and answers No / Cancel.


def _plain_window(ctx):
    return ctx.window((LAPTOP[1], LAPTOP[2]))


def _message_scene(id, title, about, trigger):
    """A scene that is only the box (or boxes) a call raises on the main window."""

    @scene(id, "Messages and questions", title, kind="message", about=about)
    def build(ctx):
        window = _plain_window(ctx)
        trigger(ctx, window)
        ctx.settle(6)

    return build


def _first_disc_folder(ctx):
    return next(f for f in ctx.lib.disc_folders() if f.name == "This Weekend").id


def _finder_howto(ctx, window):
    from pathlib import Path

    from dubcrate.ui import track_finder_ui

    where = Path("/Users/dj/Desktop/Dubcrate Track Finder.html")
    track_finder_ui.show_how_to_open(
        window,
        where,
        f"Saved a finder for 612 track(s) on 14 disc(s) to {where}",
        "3 track(s) have no waveform at all.",
        offer_hide=True,
    )


_message_scene("msg-finder-howto", "Message: how to open the track finder on a phone",
               "After Disc > Export Track Finder…: where it was saved, anything left out, the steps for an iPhone and the Don't show this again tick.",
               _finder_howto)  # fmt: skip
_message_scene("msg-card-ready", "Message: the card is formatted",
               "Shown over the main window when Format a Card finishes, after that window has closed itself.",
               lambda ctx, w: __import__("dubcrate.ui.format_card_dialog", fromlist=["announce_ready"]).announce_ready(w, "DISK_IMG"))  # fmt: skip
_message_scene("msg-delete-disc", "Question: delete a disc",
               "Delete Disc… on a disc.",
               lambda ctx, w: w.disc_view.delete_disc(ctx.demo.discs["warm"]))  # fmt: skip
_message_scene("msg-delete-disc-folder", "Question: delete a folder of discs",
               "Delete a folder in the Discs list: it says how many discs and folders go with it.",
               lambda ctx, w: w.disc_view.delete_folder(_first_disc_folder(ctx)))  # fmt: skip
_message_scene("msg-delete-mp3-folder", "Question: delete a folder from an MP3 disc",
               "Delete Folder from Disc… on an MP3 folder: it says how many tracks come off.",
               lambda ctx, w: w.disc_view.delete_dir(ctx.demo.discs["usb"], ctx.lib.disc_dirs(ctx.demo.discs["usb"])[0].id))  # fmt: skip
_message_scene("msg-make-audio-cd", "Question: make an MP3 disc an audio CD",
               "Make Audio CD on a disc that has folders.",
               lambda ctx, w: w.disc_view.set_disc_kind(ctx.demo.discs["usb"], "audio"))  # fmt: skip


def _offer_mp3(ctx, window):
    disc = ctx.temp_disc("Too Much Music", capacity_sectors=60_000)
    ctx.lib.add_to_disc(
        disc, [t.id for t in ctx.lib.tracks(node_id=ctx.demo.node(BROWSE).id)]
    )
    window.disc_view._offer_mp3(
        disc, undo=ctx.lib.disc_snapshot(disc)
    )  # as after an add: with Cancel


_message_scene("msg-offer-mp3", "Question: make this an MP3 CD?",
               "Offered when an audio disc goes over what a CD holds.", _offer_mp3)  # fmt: skip


def _already_used(ctx, window):
    window.open_disc(ctx.demo.discs["warm"])
    ids = [e.track.id for e in ctx.lib.disc_entries(ctx.demo.discs["warm"])][:3]
    window.disc_view.add_tracks(
        ids
    )  # (tracks already on this disc: the "Already used" box)


_message_scene("msg-already-used", "Question: tracks already used",
               "Adding tracks that are already on a disc: Add Anyway, Skip Duplicates or Cancel.", _already_used)  # fmt: skip
_message_scene("msg-burn-no-disc-open", "Message: burn with no disc open",
               "Disc > Burn Disc… with nothing open.", lambda ctx, w: w.burn_current_disc())  # fmt: skip


def _import_rekordbox(ctx, window):
    from pathlib import Path
    from unittest import mock

    with mock.patch(
        "dubcrate.ui.main_window.default_master_db",
        lambda: Path("/Users/dj/Library/Pioneer/rekordbox/master.db"),
    ):
        window.import_rekordbox()


_message_scene("msg-import-rekordbox", "Question: import the rekordbox library found here",
               "File > Import rekordbox Library… when rekordbox's library is found.", _import_rekordbox)  # fmt: skip
_message_scene("msg-import-failed", "Message: an import failed",
               "An import that could not read its source.",
               lambda ctx, w: w._import_failed("Couldn't read the rekordbox library: the file is not a rekordbox database."))  # fmt: skip
_message_scene("msg-remove-source", "Question: remove a source",
               "Remove Source… on the imported folder.", lambda ctx, w: w.remove_selected_source())  # fmt: skip


def _split(ctx, window):
    ctx.open_playlist(window, BROWSE)
    window.split_shown()


_message_scene("msg-split-name", "Input: name for split discs",
               "Disc > Split Shown Tracks Across Discs…: the name box.", _split)  # fmt: skip


def _folder_into_itself(ctx, window):
    usb = ctx.demo.discs["usb"]
    folder = ctx.lib.disc_dirs(usb)[0].id
    window.disc_view.move_dirs(usb, [folder], folder)


_message_scene("msg-cant-move-folder", "Message: a folder can't go inside itself",
               "A refused move of an MP3 folder, with the library's own wording.", _folder_into_itself)  # fmt: skip


def _cant_add(ctx, window):
    tiny = ctx.temp_disc("Tiny", capacity_sectors=1_000)
    ids = [t.id for t in ctx.lib.tracks(node_id=ctx.demo.node(BROWSE).id)][:2]
    window.disc_view.add_tracks(ids, strict=True, disc_id=tiny)


_message_scene("msg-cant-add-tracks", "Message: tracks don't fit",
               "A strict add (drag onto a disc) that doesn't fit, with the library's own wording.", _cant_add)  # fmt: skip


@scene("msg-label-save-failed", "Messages and questions", "Message: the labels PDF couldn't be written", kind="message",
       about="Save PDF… to a place that can't be written.")  # fmt: skip
def _label_save_failed(ctx):
    from unittest import mock

    dialog = _label_window(ctx, ctx.demo.discs["warm"])
    with mock.patch(
        "dubcrate.ui.label_dialog.ask_pdf_path",
        lambda parent: "/nonexistent-folder/labels.pdf",
    ):
        dialog.save_pdf()


@scene("msg-update-none", "Messages and questions", "Message: no updates available", kind="message",
       about="Check for Updates when you are up to date.")  # fmt: skip
def _update_none(ctx):
    from PySide6.QtWidgets import QWidget

    from dubcrate.ui.update_check import UpdateChecker

    host = QWidget()
    ctx.on_cleanup(host.close)
    UpdateChecker(host)._show(None, True, host)


@scene("msg-update-failed", "Messages and questions", "Message: the update check failed", kind="message",
       about="Check for Updates when GitHub can't find the release list (a 404: the project moved), through the real check.")  # fmt: skip
def _update_failed(ctx):
    import urllib.error
    from unittest import mock

    from PySide6.QtWidgets import QWidget

    from dubcrate import updates
    from dubcrate.ui.update_check import UpdateChecker

    def not_found(*_a, **_k):
        raise urllib.error.HTTPError(
            "https://api.github.com/", 404, "Not Found", {}, None
        )

    host = QWidget()
    ctx.on_cleanup(host.close)
    real = updates.check_for_update
    with mock.patch(
        "dubcrate.ui.update_check.check_for_update", lambda: real(urlopen=not_found)
    ):
        UpdateChecker(host).start(manual=True)
        ctx.wait_for(lambda: bool(ctx.extras))


@scene("msg-enable-editing", "Messages and questions", "Question: edit your rekordbox library?", kind="message",
       about="Asked the first time a memory cue is edited on a rekordbox track.")  # fmt: skip
def _enable_editing(ctx):
    from dubcrate.ui.rekordbox_editor import ask_enable_editing

    ask_enable_editing()


# -- Menus ------------------------------------------------------------------------------------------


def _menu_scene(id, title, about, build_menus):
    @scene(id, "Menus", title, kind="menu", about=about)
    def build(ctx):
        window = _plain_window(ctx)
        build_menus(ctx, window)

    return build


def _snap_menu_tree(ctx, label, menu):
    """A menu and, as pictures of their own, the submenus in it."""
    menu.aboutToShow.emit()
    ctx.snap_menu(label, menu)
    for action in menu.actions():
        if action.menu() is not None:
            _snap_menu_tree(
                ctx, f"{label} > {action.text().replace('&', '')}", action.menu()
            )


def _menu_bar(ctx, window):
    for action in window.menuBar().actions():
        _snap_menu_tree(ctx, f"{action.text().replace('&', '')} menu", action.menu())


_menu_scene("menus-bar", "The menu bar's menus", "File, Disc, View and Help, with their submenus.", _menu_bar)  # fmt: skip


def _disc_tree_menus(ctx, window):
    from dubcrate.ui.models import find_selection

    window.refresh_discs()
    window.disc_tree.expandAll()
    model = window.disc_tree.model()
    usb = ctx.demo.discs["usb"]
    folder = next(f for f in ctx.lib.disc_dirs(usb) if f.name == "UK Garage")
    for label, selection in (
        ("right-click on an audio disc", ("disc", ctx.demo.discs["warm"])),
        ("right-click on an MP3 disc", ("disc", usb)),
        ("right-click on a folder of an MP3 disc", ("disc_dir", usb, folder.id)),
        ("right-click on a folder of discs", ("disc_folder", _first_disc_folder(ctx))),
    ):
        _snap_menu_tree(
            ctx, label, window.disc_tree_menu(find_selection(model, selection))
        )
    from PySide6.QtCore import QModelIndex

    _snap_menu_tree(
        ctx, "right-click on empty space", window.disc_tree_menu(QModelIndex())
    )
    ctx.snap_menu(
        "the + button on a disc",
        window.new_disc_kind_menu(("disc", ctx.demo.discs["warm"])),
    )


_menu_scene("menus-disc-tree", "The Discs list's menus", "Right-click on each kind of row, and the + button's choice.", _disc_tree_menus)  # fmt: skip


def _track_menus(ctx, window):
    ctx.open_playlist(window, BROWSE)
    ctx.snap_menu(
        "right-click on the track list's column headings", window.column_menu()
    )
    window.open_disc(ctx.demo.discs["usb"])
    window.disc_view.table.selectRow(0)
    ctx.snap_menu("right-click on tracks of an MP3 disc", window.disc_view.table_menu())
    window.open_disc(ctx.demo.discs["warm"])
    window.disc_view.table.selectRow(0)
    ctx.snap_menu(
        "right-click on tracks of an audio disc", window.disc_view.table_menu()
    )


_menu_scene("menus-tracks", "The track lists' menus", "Column headings of the library, and the open disc's tracks.", _track_menus)  # fmt: skip


def _tab_menus(ctx, window):
    misc = str(ctx.demo.music / "Misc MP3s")
    window.pin_folder(misc)
    window.pin_folder(str(ctx.demo.music / SOURCE))
    ctx.snap_menu(
        "right-click on a pinned folder's tab", window._tab_menu(f"pin:{misc}")
    )
    window.source_tabs.set_current("explorer")
    ctx.settle(6)
    ctx.snap_menu(
        "right-click in the Explorer on a pinned folder",
        window.explorer_tree._pin_menu(misc),
    )
    ctx.snap_menu(
        "right-click in the Explorer on another folder",
        window.explorer_tree._pin_menu(str(ctx.demo.music)),
    )


_menu_scene("menus-tabs-explorer", "The tabs' and Explorer's menus", "A pinned folder's tab, and folders in the Explorer.", _tab_menus)  # fmt: skip


def _player_menus(ctx, window):
    ctx.open_playlist(window, BROWSE)
    ctx.load_track(window, 2)
    recipes._stage_cues(window)
    ctx.settle(6)
    strip = window.player_strip
    ctx.snap_menu("the beat jump size menu", strip.jump_combo.menu())
    memory = strip.side.memory
    ctx.snap_menu(
        "right-click on a memory cue", strip.side.memory_menu(memory.topLevelItem(1))
    )
    ctx.snap_menu(
        "right-click on a memory loop",
        strip.side.memory_menu(memory.topLevelItem(memory.topLevelItemCount() - 1)),
    )


_menu_scene("menus-player", "The player's menus", "Beat jump size, and the Memory list's right-click menu.", _player_menus)  # fmt: skip


# -- Help, About and updates -----------------------------------------------------------------------------


@scene("help-user-guide", "Help, About and updates", "Help: the User Guide", (("820x700 window", 820, 700), SMALLEST), kind="dialog",
       about="The guide's first page (its pictures and links as the guide renders them).")  # fmt: skip
def _help_guide(ctx):
    from dubcrate.ui.help_dialog import HelpDialog

    dialog = HelpDialog()
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.settle(10)
    return dialog


@scene("help-user-guide-search", "Help, About and updates", "Help: searching the User Guide", sizes=(NATURAL,), kind="dialog",
       about="The find bar open, with a search that has matches highlighted.")  # fmt: skip
def _help_search(ctx):
    from dubcrate.ui.help_dialog import HelpDialog

    dialog = HelpDialog()
    dialog.show()
    ctx.on_cleanup(dialog.close)
    dialog.open_search()
    dialog.search_input.setText("burn")
    ctx.settle(10)
    return dialog


@scene("help-about", "Help, About and updates", "About Dubcrate", sizes=(NATURAL,), kind="dialog",
       about="Help > About Dubcrate.")  # fmt: skip
def _help_about(ctx):
    from PySide6.QtWidgets import QWidget

    from dubcrate.ui.about_dialog import AboutDialog
    from dubcrate.ui.update_check import UpdateChecker
    from dubcrate.ui.version import icon_path

    host = QWidget()
    ctx.on_cleanup(host.close)
    dialog = AboutDialog(host, UpdateChecker(host), icon_path())
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.settle(8)
    return dialog


@scene("help-notices", "Help, About and updates", "Third-Party Notices", BOTH, kind="dialog",
       about="The licences and credits of what Dubcrate includes.")  # fmt: skip
def _help_notices(ctx):
    from dubcrate.ui.about_dialog import NoticesDialog

    dialog = NoticesDialog()
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.settle(8)
    return dialog


@scene("help-shortcuts", "Help, About and updates", "Keyboard Shortcuts", BOTH, kind="dialog",
       about="Help > Keyboard Shortcuts: built from the menus as they are now, plus the keys that have no menu item.")  # fmt: skip
def _help_shortcuts(ctx):
    from dubcrate.ui.shortcuts_dialog import ShortcutsDialog

    window = _plain_window(ctx)
    dialog = ShortcutsDialog(window)
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.settle(8)
    return dialog


def _update_info(notes):
    from dubcrate.updates import UpdateInfo

    return UpdateInfo(
        "0.2.0",
        "v0.2.0",
        "Dubcrate 0.2.0",
        "https://github.com/example/dubcrate/releases/tag/v0.2.0",
        notes,
    )


@scene("update-available", "Help, About and updates", "Update available (with release notes)", BOTH, kind="dialog",
       about="The notice with the release's notes in Markdown.")  # fmt: skip
def _update_available(ctx):
    from dubcrate.ui.update_dialog import UpdateAvailableDialog

    notes = (
        "## What's new\n\n- **60 FPS waveform** with a *Light* 30 FPS setting for slower computers\n"
        "- Cancel a burn while the disc is being prepared\n- Year on index labels\n\n"
        "## Fixes\n\n- The zoomed-out waveform no longer shimmers\n- `CON` and other names Windows reserves are safe in MP3 folder names\n"
    )
    dialog = UpdateAvailableDialog(_update_info(notes))
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.settle(8)
    return dialog


@scene("update-available-no-notes", "Help, About and updates", "Update available (no release notes)", sizes=(NATURAL,), kind="dialog",
       about="A release with no description.")  # fmt: skip
def _update_available_plain(ctx):
    from dubcrate.ui.update_dialog import UpdateAvailableDialog

    dialog = UpdateAvailableDialog(_update_info(""))
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.settle(8)
    return dialog


# -- Start-up and errors ------------------------------------------------------------------------------


@scene("start-splash", "Start-up and errors", "The start-up splash", sizes=(NATURAL,), kind="window",
       about="The splash shown while the app starts (it draws itself and ignores the theme).")  # fmt: skip
def _splash(ctx):
    from dubcrate.ui.splash import StartupSplash

    splash = StartupSplash("0.1.0", None, activating=True)
    splash.show()
    ctx.on_cleanup(splash.close)
    ctx.settle(8)
    return splash


@scene("start-missing-tools", "Start-up and errors", "ffmpeg not found", sizes=BOTH, kind="dialog",
       about="Shown after start-up when ffmpeg and ffprobe are missing.")  # fmt: skip
def _missing_tools(ctx):
    from dubcrate.ui.tools_dialog import MissingToolsDialog

    dialog = MissingToolsDialog(["ffmpeg", "ffprobe"])
    dialog.show()
    ctx.on_cleanup(dialog.close)
    ctx.settle(8)
    return dialog


@scene("start-something-went-wrong", "Start-up and errors", "Something went wrong", kind="message",
       about="The message shown for an unexpected error (the error in one line, Show Log File).")  # fmt: skip
def _crash(ctx):
    from dubcrate.ui import crash_notice

    crash_notice.ask_about_error(
        None,
        crash_notice.describe(
            ValueError, ValueError("could not convert string to float: 'x'")
        ),
    )
