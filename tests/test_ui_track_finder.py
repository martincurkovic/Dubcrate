"""Disc > Export Track Finder: every disc as one HTML page, from the window."""

import json
import re

import numpy as np
import pytest
from PySide6.QtWidgets import QMessageBox

from dubcrate import settings
from dubcrate.library import Library, TrackRecord
from dubcrate.ui import track_finder_ui
from dubcrate.ui.main_window import MainWindow


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    shown = []
    for name in ("information", "warning"):
        monkeypatch.setattr(
            QMessageBox,
            name,
            staticmethod(lambda _p, t, x, _n=name: shown.append((_n, t, x))),
        )
    records = [
        TrackRecord(
            ext_id=f"t{i}",
            path=f"/m/t{i}.mp3",
            title=f"Song {i}",
            artist=f"Artist {i}",
            duration=66.0,
            bpm=124.0,
            key="Am",
            year=2001,
        )
        for i in range(3)
    ]
    lib = Library.open(":memory:")
    lib.replace_source("folder", "/m", "Crate", records)
    settings.set_hide_finder_howto(True)  # (the how-to box has its own tests below)
    win = MainWindow(lib)
    win.shown = shown
    yield win, lib
    win.close()
    win.deleteLater()
    lib.close()


def make_disc(win, lib, name="Night"):
    disc = lib.create_disc(name)
    lib.add_to_disc(disc, [t.id for t in lib.tracks(sort="title")][:2])
    win.refresh_discs()
    return disc


def page_data(path):
    text = path.read_text(encoding="utf-8")
    [raw] = re.findall(r'<script id="data" type="application/json">(.*?)</script>', text, re.DOTALL)  # fmt: skip
    return json.loads(raw)


def disc_menu_texts(win):
    [menu] = [a.menu() for a in win.menuBar().actions() if a.text().endswith("Disc")]
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def test_the_disc_menu_has_export_track_finder(window):
    win, _lib = window
    assert "Export Track Finder…" in disc_menu_texts(win)
    assert "phone" in win.act_track_finder.toolTip()


def test_it_asks_for_a_file_and_writes_every_disc(window, tmp_path, monkeypatch):
    win, lib = window
    make_disc(win, lib, "Night")
    make_disc(win, lib, "Day")
    out = tmp_path / "finder.html"
    monkeypatch.setattr(track_finder_ui, "ask_save_file", lambda _p: str(out))
    win.act_track_finder.trigger()
    data = page_data(out)
    assert [d["name"] for d in data["discs"]] == ["Day", "Night"]
    assert "Saved a finder for 4 track(s) on 2 disc(s)" in win.statusBar().currentMessage()  # fmt: skip
    [(_kind, _title, text)] = win.shown  # (these songs have no file, so no waveform)
    assert "4 track(s) have no waveform" in text


def test_keys_follow_the_notation_chosen_in_settings(window, tmp_path, monkeypatch):
    win, lib = window
    make_disc(win, lib)
    out = tmp_path / "finder.html"
    monkeypatch.setattr(track_finder_ui, "ask_save_file", lambda _p: str(out))
    win.act_track_finder.trigger()
    assert "8A" in page_data(out)["discs"][0]["tracks"][0][4]
    out.unlink()
    settings.set_key_notation("musical")
    win.act_track_finder.trigger()
    assert "A min" in page_data(out)["discs"][0]["tracks"][0][4]


def test_cancelling_the_chooser_writes_nothing(window, tmp_path, monkeypatch):
    win, lib = window
    make_disc(win, lib)
    monkeypatch.setattr(track_finder_ui, "ask_save_file", lambda _p: "")
    win.act_track_finder.trigger()
    assert list(tmp_path.glob("*.html")) == []
    assert win.shown == []


def test_no_discs_says_so_and_asks_for_nothing(window):
    win, _lib = window
    win.act_track_finder.trigger()  # the chooser would raise: conftest refuses it
    [(kind, title, text)] = win.shown
    assert (kind, title) == ("information", "Export Track Finder")
    assert "nothing to list" in text


def test_an_empty_disc_is_mentioned_after_the_export(window, tmp_path, monkeypatch):
    win, lib = window
    make_disc(win, lib)
    lib.create_disc("Blank")
    win.refresh_discs()
    monkeypatch.setattr(track_finder_ui, "ask_save_file", lambda _p: str(tmp_path / "f.html"))  # fmt: skip
    win.act_track_finder.trigger()
    [(_kind, _title, text)] = win.shown
    assert "Blank" in text


def test_an_unwritable_place_is_a_warning_not_a_crash(window, tmp_path, monkeypatch):
    win, lib = window
    make_disc(win, lib)
    monkeypatch.setattr(track_finder_ui, "ask_save_file", lambda _p: str(tmp_path / "nope" / "f.html"))  # fmt: skip
    win.act_track_finder.trigger()
    [(kind, _title, text)] = win.shown
    assert kind == "warning" and "Could not write" in text


# -- analysing waveforms on the way (a worker, with progress and Cancel) ----------------------


@pytest.fixture
def real_files(window, tmp_path):
    """The window's library with files that exist, so their waveforms can be analysed."""
    win, lib = window
    music = tmp_path / "music"
    music.mkdir()
    records = []
    for i in range(2):
        f = music / f"s{i}.mp3"
        f.write_bytes(b"x")
        records.append(
            TrackRecord(
                ext_id=f"r{i}",
                path=str(f),
                title=f"Real {i}",
                duration=66.0,
                file_size=1,
            )
        )
    lib.replace_source("folder", str(music), "Real", records)
    disc = lib.create_disc("Night")
    lib.add_to_disc(
        disc, [t.id for t in lib.tracks(sort="path") if t.title.startswith("Real")]
    )
    win.refresh_discs()
    return win, lib


def test_missing_waveforms_are_analysed_then_the_page_is_written(
    real_files, tmp_path, monkeypatch, wait_for
):
    from dubcrate import track_finder as tf

    win, lib = real_files

    class Tone:
        def mono(self):
            return (0.4 * np.sin(np.arange(44100 * 2) * 0.05)).astype(np.float32)

    monkeypatch.setattr(tf, "decode_file", lambda path: Tone())
    out = tmp_path / "finder.html"
    monkeypatch.setattr(track_finder_ui, "ask_save_file", lambda _p: str(out))
    win.act_track_finder.trigger()
    assert wait_for(out.exists)
    tracks = page_data(out)["discs"][0]["tracks"]
    assert all(t[11].startswith("3:") for t in tracks)  # a detailed waveform for each
    # and kept: the next export has nothing to analyse
    assert tf.needs_analysis(lib) == []
    assert wait_for(lambda: not win._busy)
    assert win.shown == []  # nothing was missing


def test_cancelling_keeps_the_waveforms_done_and_writes_nothing(
    real_files, tmp_path, monkeypatch, wait_for
):
    from dubcrate import track_finder as tf
    from dubcrate.cancel import Cancelled
    from dubcrate.waveform import Waveform

    win, lib = real_files
    first = tf.needs_analysis(lib)[0]

    def stopped(tracks, report=None, cancel=None, workers=2):
        raise Cancelled(
            ({first.id: Waveform(np.full((10, 3), 0.5, np.float32), 0.0)}, [])
        )

    monkeypatch.setattr(tf, "analyse_waves", stopped)
    out = tmp_path / "finder.html"
    monkeypatch.setattr(track_finder_ui, "ask_save_file", lambda _p: str(out))
    win.act_track_finder.trigger()
    assert wait_for(lambda: "cancelled" in win.statusBar().currentMessage())
    assert not out.exists()
    assert [t.id for t in tf.needs_analysis(lib)] != [
        first.id
    ]  # the one that was done is kept
    assert len(tf.needs_analysis(lib)) == 1
    assert not win._busy


# -- the box that says how to open the page on a phone ----------------------------------------


def test_after_an_export_one_box_says_where_it_is_what_was_left_out_and_how_to_open_it(
    window, tmp_path, monkeypatch
):
    win, lib = window
    make_disc(win, lib)
    lib.create_disc("Blank")
    win.refresh_discs()
    settings.set_hide_finder_howto(False)
    seen = []
    monkeypatch.setattr(
        track_finder_ui,
        "show_how_to_open",
        lambda *a, **k: seen.append((a[1:], k)),
    )
    out = tmp_path / "f.html"
    monkeypatch.setattr(track_finder_ui, "ask_save_file", lambda _p: str(out))
    win.act_track_finder.trigger()
    [((path, summary, notes), kwargs)] = seen
    assert path == out and "Saved a finder for" in summary
    assert "Blank" in notes and kwargs == {"offer_hide": True}
    assert win.shown == []  # no second box


def test_once_hidden_the_how_to_is_left_out_but_what_was_left_out_still_shows(
    window, tmp_path, monkeypatch
):
    win, lib = window
    make_disc(win, lib)
    lib.create_disc("Blank")
    win.refresh_discs()
    monkeypatch.setattr(track_finder_ui, "show_how_to_open", lambda *a, **k: pytest.fail("shown"))  # fmt: skip
    monkeypatch.setattr(track_finder_ui, "ask_save_file", lambda _p: str(tmp_path / "f.html"))  # fmt: skip
    win.act_track_finder.trigger()  # (the fixture has it hidden)
    [(_kind, _title, text)] = win.shown
    assert "Blank" in text


def run_box(monkeypatch, tick=False, **kwargs):
    """Build the real box and 'close' it with Ok, noting what it holds."""
    from PySide6.QtWidgets import QMessageBox

    held = {}

    def fake_exec(self):
        held["text"], held["more"] = self.text(), self.informativeText()
        held["buttons"] = [b.text() for b in self.buttons()]
        box = self.checkBox()
        held["tick"] = None if box is None else box.text()
        if box is not None:
            box.setChecked(tick)
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(track_finder_ui, "show_how_to_open", track_finder_ui._real_show_how_to_open)  # fmt: skip
    from pathlib import Path

    track_finder_ui.show_how_to_open(None, Path("/x/Finder.html"), "Saved it.", "A note.", **kwargs)  # fmt: skip
    return held


def test_the_box_has_the_steps_the_file_and_a_way_to_show_it(qapp, monkeypatch):
    held = run_box(monkeypatch, offer_hide=True)
    assert held["text"] == "Saved it.\n\nA note."
    assert "AirDrop" in held["more"] and "Open in Sitecase" in held["more"]
    assert "Show in Folder" in held["buttons"] and "OK" in held["buttons"]
    assert "Don't show this again" in held["tick"]


def test_the_tick_is_remembered_only_when_it_is_ticked(qapp, monkeypatch):
    settings.set_hide_finder_howto(False)
    run_box(monkeypatch, tick=False, offer_hide=True)
    assert settings.get_hide_finder_howto() is False
    run_box(monkeypatch, tick=True, offer_hide=True)
    assert settings.get_hide_finder_howto() is True


def test_help_menu_shows_the_box_again_without_the_tick_or_a_file(window, monkeypatch):
    win, _lib = window
    seen = []
    monkeypatch.setattr(track_finder_ui, "show_how_to_open", lambda *a, **k: seen.append((a, k)))  # fmt: skip
    [help_menu] = [
        a.menu() for a in win.menuBar().actions() if a.text().endswith("Help")
    ]
    assert "Track Finder on a Phone…" in [a.text().replace("&", "") for a in help_menu.actions()]  # fmt: skip
    win.act_finder_howto.trigger()
    assert seen == [((win,), {})]
