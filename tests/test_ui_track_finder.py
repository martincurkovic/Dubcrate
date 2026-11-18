"""Disc > Export Track Finder: every disc as one HTML page, from the window."""

import json
import re

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
    assert win.shown == []


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
