"""Exporting discs as playlists from the window: Disc > Export Playlists and the Discs list's right-click."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from dubcrate.library import Library, TrackRecord
from dubcrate.ui import playlist_export_ui
from dubcrate.ui.main_window import MainWindow
from dubcrate.ui.models import find_selection


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    shown = []
    for name in ("information", "warning"):
        monkeypatch.setattr(
            QMessageBox,
            name,
            staticmethod(lambda _p, t, x, _n=name: shown.append((_n, t, x))),
        )
    music = tmp_path / "music"
    music.mkdir()
    records = []
    for i in range(3):
        file = music / f"t{i}.mp3"
        file.write_bytes(b"x")
        records.append(TrackRecord(ext_id=f"t{i}", path=str(file), title=f"Song {i}", artist=f"Artist {i}", duration=66.0))  # fmt: skip
    lib = Library.open(":memory:")
    lib.replace_source("folder", str(music), "Crate", records)
    for track in lib.tracks():
        lib.set_cd_sectors(track.id, 5000)
    win = MainWindow(lib)
    win.shown = shown
    yield win, lib
    win.close()
    win.deleteLater()
    lib.close()


def texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def make_disc(win, lib, name="Night", folder_id=None):
    disc = lib.create_disc(name, folder_id=folder_id)
    lib.add_to_disc(disc, [t.id for t in lib.tracks(sort="title")][:2])
    win.refresh_discs()
    return disc


def test_the_disc_menu_has_export_playlists_for_every_disc(window):
    win, _lib = window
    assert texts(win.export_playlists_menu) == [
        "All Discs as rekordbox XML…",
        "All Discs as M3U…",
    ]


def test_all_discs_as_xml_asks_for_a_file_and_writes_it(window, tmp_path, monkeypatch):
    win, lib = window
    make_disc(win, lib)
    asked = []
    out = tmp_path / "all.xml"

    def ask(_parent, title, name, fmt):
        asked.append((title, name, fmt))
        return str(out)

    monkeypatch.setattr(playlist_export_ui, "ask_save_file", ask)
    win.export_playlists_menu.actions()[0].trigger()
    assert asked == [("Export rekordbox XML", "Dubcrate Playlists", "xml")]
    assert 'Name="Night"' in out.read_text(encoding="utf-8")
    assert "Exported 1 playlist(s), 2 track(s)" in win.statusBar().currentMessage()


def test_all_discs_as_m3u_makes_a_new_folder(window, tmp_path, monkeypatch):
    win, lib = window
    make_disc(win, lib)
    monkeypatch.setattr(playlist_export_ui, "ask_save_folder", lambda _p: str(tmp_path))
    win.export_playlists_menu.actions()[1].trigger()
    assert [p.name for p in (tmp_path / "Dubcrate Playlists").iterdir()] == [
        "Night.m3u8"
    ]


def test_cancelling_the_chooser_writes_nothing(window, tmp_path, monkeypatch):
    win, lib = window
    make_disc(win, lib)
    monkeypatch.setattr(playlist_export_ui, "ask_save_file", lambda *_a: "")
    monkeypatch.setattr(playlist_export_ui, "ask_save_folder", lambda _p: "")
    for action in win.export_playlists_menu.actions():
        action.trigger()
    assert list(tmp_path.glob("Dubcrate*")) == []
    assert win.shown == []


def test_nothing_to_export_says_so_and_asks_for_nothing(window):
    win, lib = window
    lib.create_disc("Empty")
    win.refresh_discs()
    win.export_playlists_menu.actions()[0].trigger()  # the chooser is refused if opened
    [(kind, title, text)] = win.shown
    assert (kind, title) == ("information", "Export Playlists")
    assert "nothing to export" in text


def test_a_disc_right_click_exports_that_disc_as_one_file(
    window, tmp_path, monkeypatch
):
    win, lib = window
    disc = make_disc(win, lib, "Night")
    make_disc(win, lib, "Other")
    menu = win.disc_tree_menu(find_selection(win.disc_tree.model(), ("disc", disc)))
    export = next(a for a in menu.actions() if a.text() == "Export Playlist")
    assert texts(export.menu()) == ["rekordbox XML…", "M3U…"]
    asked = []
    out = tmp_path / "night.m3u8"
    monkeypatch.setattr(
        playlist_export_ui,
        "ask_save_file",
        lambda _p, title, name, fmt: asked.append((name, fmt)) or str(out),
    )
    export.menu().actions()[1].trigger()
    assert asked == [("Night", "m3u")]
    assert out.read_text(encoding="utf-8").startswith("#EXTM3U")
    assert "Other" not in out.read_text(encoding="utf-8")


def test_a_disc_folder_right_click_exports_its_discs(window, tmp_path, monkeypatch):
    win, lib = window
    folder = lib.create_disc_folder("Sets")
    make_disc(win, lib, "In", folder_id=folder)
    make_disc(win, lib, "Out")
    menu = win.disc_tree_menu(
        find_selection(win.disc_tree.model(), ("disc_folder", folder))
    )
    export = next(a for a in menu.actions() if a.text() == "Export Playlists")
    out = tmp_path / "sets.xml"
    monkeypatch.setattr(playlist_export_ui, "ask_save_file", lambda *_a: str(out))
    export.menu().actions()[0].trigger()
    text = out.read_text(encoding="utf-8")
    assert 'Name="Sets"' in text and 'Name="In"' in text and 'Name="Out"' not in text


def test_left_out_tracks_are_listed_after_the_export(window, tmp_path, monkeypatch):
    win, lib = window
    disc = make_disc(win, lib)
    first = lib.disc_entries(disc)[0].track
    Path(first.path).unlink()
    records = [
        TrackRecord(
            ext_id=t.ext_id, path=t.path, title=t.title, artist=t.artist, duration=66.0
        )
        for t in lib.tracks()
    ]
    lib.replace_source("folder", lib.sources()[0].path, "Crate", records)
    out = tmp_path / "x.xml"
    monkeypatch.setattr(playlist_export_ui, "ask_save_file", lambda *_a: str(out))
    win.export_playlists(fmt="xml", disc_id=disc)
    [(kind, _title, text)] = win.shown
    assert kind == "information" and "left out" in text and first.title in text
