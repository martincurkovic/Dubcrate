"""The Preview strip of a folder track is kept across sessions, by file (path and size), not by track."""

import numpy as np
import pytest

from dubcrate.library import Library, TrackRecord
from dubcrate.library import store as store_module
from dubcrate.ui import main_window
from dubcrate.ui.main_window import MainWindow
from dubcrate.waveform import PREVIEW_COLUMNS, Waveform, pack_preview, unpack_preview


def strip(seed=3, columns=PREVIEW_COLUMNS):
    bands = np.random.default_rng(seed).random((columns, 3)).astype(np.float32)
    return Waveform(bands, 0.0)


def rec(name, size=1000, **kw):
    return TrackRecord(ext_id=name, path=f"/music/{name}.mp3", title=name, duration=60.0, file_size=size, **kw)  # fmt: skip


# -- the stored form -----------------------------------------------------------------------------


def test_a_waveform_survives_being_stored_to_a_step_of_one_in_255_and_is_small():
    wave = strip()
    packed = pack_preview(wave)
    back = unpack_preview(packed)
    assert back.bands.shape == (PREVIEW_COLUMNS, 3) and back.columns_per_second == 0.0
    assert np.abs(back.bands - wave.bands).max() <= 0.5 / 255 + 1e-6
    assert (
        len(packed) < 3600 + 100
    )  # 1200 columns of 3 bytes, not larger once compressed


@pytest.mark.parametrize("raw", [b"", b"not zlib", b"x" * 40])
def test_bytes_that_are_not_a_waveform_are_refused(raw):
    with pytest.raises(ValueError):
        unpack_preview(raw)


# -- in the library ---------------------------------------------------------------------------------


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def test_a_kept_strip_comes_back_for_the_same_file(lib):
    lib.replace_source("folder", "/music", "M", [rec("a"), rec("b")])
    a, b = lib.tracks(sort="path")
    lib.set_preview(a.path, a.file_size, strip(1))
    found = lib.previews_for([a, b])
    assert set(found) == {a.id}  # b was never kept
    assert np.abs(found[a.id].bands - strip(1).bands).max() < 0.01


def test_a_strip_is_stale_once_the_file_changes_size(lib):
    lib.replace_source("folder", "/music", "M", [rec("a", size=1000)])
    (a,) = lib.tracks()
    lib.set_preview(a.path, a.file_size, strip())
    lib.replace_source("folder", "/music", "M", [rec("a", size=1500)])
    (changed,) = lib.tracks()
    assert lib.previews_for([changed]) == {}


def test_a_strip_belongs_to_the_file_not_the_track_so_it_outlives_the_track_rows(lib):
    lib.replace_source("folder", "/music", "M", [rec("a")])
    (a,) = lib.tracks()
    lib.set_preview(a.path, a.file_size, strip())
    lib.remove_source(
        lib.sources()[0].id
    )  # the Explorer forgets its tracks at every start-up, like this
    lib.replace_source("folder", "/music", "M", [rec("a")])
    (again,) = lib.tracks()
    assert lib.tracks() == [
        again
    ]  # (the old track rows are gone: only the file is remembered)
    assert set(lib.previews_for([again])) == {again.id}


def test_a_file_named_in_another_case_or_form_is_the_same_file_on_a_mac(lib):
    lib.replace_source("folder", "/music", "M", [rec("Café")])
    (a,) = lib.tracks()
    lib.set_preview("/MUSIC/CAFÉ.mp3", a.file_size, strip())  # (NFD, capitals)
    assert set(lib.previews_for([a])) == {a.id}


def test_nothing_is_kept_for_a_file_of_unknown_size(lib):
    lib.replace_source("folder", "/music", "M", [rec("a", size=None)])
    (a,) = lib.tracks()
    lib.set_preview(a.path, a.file_size, strip())
    assert lib.previews_for([a]) == {}
    assert lib._conn.execute("SELECT COUNT(*) FROM preview_cache").fetchone()[0] == 0


def test_a_second_strip_for_a_file_replaces_the_first(lib):
    lib.replace_source("folder", "/music", "M", [rec("a")])
    (a,) = lib.tracks()
    lib.set_preview(a.path, a.file_size, strip(1))
    lib.set_preview(a.path, a.file_size, strip(2))
    assert lib._conn.execute("SELECT COUNT(*) FROM preview_cache").fetchone()[0] == 1
    assert np.abs(lib.previews_for([a])[a.id].bands - strip(2).bands).max() < 0.01


def test_the_oldest_are_forgotten_past_the_limit(lib, monkeypatch):
    monkeypatch.setattr(store_module, "MAX_CACHED_PREVIEWS", 3)
    lib.replace_source("folder", "/music", "M", [rec(f"t{n}") for n in range(6)])
    tracks = lib.tracks(sort="path")
    for t in tracks:
        lib.set_preview(t.path, t.file_size, strip(t.id))
    assert set(lib.previews_for(tracks)) == {t.id for t in tracks[-3:]}


def test_a_damaged_row_is_skipped_not_fatal(lib):
    lib.replace_source("folder", "/music", "M", [rec("a"), rec("b")])
    a, b = lib.tracks(sort="path")
    lib.set_preview(a.path, a.file_size, strip())
    lib.set_preview(b.path, b.file_size, strip())
    lib._conn.execute("UPDATE preview_cache SET bands = x'0102' WHERE path_key = ?", (store_module.path_key(a.path),))  # fmt: skip
    assert set(lib.previews_for([a, b])) == {b.id}


# -- the window --------------------------------------------------------------------------------------


def test_a_strip_computed_for_a_folder_track_is_there_in_the_next_session(
    qapp, tmp_path
):
    with Library.open(tmp_path / "lib.db") as lib:
        lib.replace_source("folder", "/music", "M", [rec("song")])
        (track,) = lib.tracks()
        first = MainWindow(lib)
        first.show()
        first._on_preview_ready(track.id, strip())  # (what playing it does)
        first.close()
        first.deleteLater()
    with Library.open(
        tmp_path / "lib.db"
    ) as lib:  # a new session: nothing held in memory
        second = MainWindow(lib)
        second.show()
        assert not second.track_model.has_preview(track.id)
        second._request_previews()
        assert second.track_model.has_preview(track.id)
        second.close()
        second.deleteLater()


def test_a_rekordbox_tracks_strip_is_not_kept_it_is_read_from_rekordbox(qapp, tmp_path):
    with Library.open(":memory:") as lib:
        lib.replace_source("rekordbox", str(tmp_path / "master.db"), "rb", [rec("rb", analysis_path="/PIONEER/1/ANLZ0000.DAT")])  # fmt: skip
        (track,) = lib.tracks()
        win = MainWindow(lib)
        win.show()
        win._on_preview_ready(track.id, strip())
        assert (
            lib._conn.execute("SELECT COUNT(*) FROM preview_cache").fetchone()[0] == 0
        )
        win.close()
        win.deleteLater()


def test_the_cache_is_asked_once_for_the_rows_on_show_and_not_again_for_ones_held(
    qapp, monkeypatch
):
    with Library.open(":memory:") as lib:
        lib.replace_source("folder", "/music", "M", [rec(f"t{n}") for n in range(5)])
        for t in lib.tracks():
            lib.set_preview(t.path, t.file_size, strip(t.id))
        win = MainWindow(lib)
        win.show()
        asked = []
        real = lib.previews_for
        monkeypatch.setattr(
            lib,
            "previews_for",
            lambda tracks: asked.append(len(list(tracks))) or real(tracks),
        )
        win._request_previews()
        win._request_previews()
        assert asked == [5]  # the second time every row already has its strip
        assert main_window  # (the module under test)
        win.close()
        win.deleteLater()
