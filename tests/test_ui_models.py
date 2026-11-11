import json

from PySide6.QtCore import QMimeData, QModelIndex, Qt

from dubcrate.library import Library, NodeRecord, TrackRecord
from dubcrate.library.models import DiscEntry
from dubcrate.ui.capacity_bar import CapacityBar, CapacitySummary, disc_spans
from dubcrate.ui.models import (
    DISC_ROWS_MIME,
    NAME_ROLE,
    SELECTION_ROLE,
    TRACK_COLUMNS,
    TRACK_MIME,
    DiscModel,
    TrackTableModel,
    build_source_model,
    find_selection,
)


def make_lib(count=4):
    lib = Library.open(":memory:")
    records = [
        TrackRecord(ext_id=f"t{i}", path=f"/nowhere/t{i}.mp3", title=f"Title {i}", artist=f"Artist {i}",
                    bpm=120 + i, key="8A", duration=120.0 + i, genre="House")
        for i in range(count)
    ]  # fmt: skip
    nodes = [
        NodeRecord("f", None, "Folder", True),
        NodeRecord("p", "f", "Playlist", False, tuple(r.ext_id for r in records)),
    ]
    lib.replace_source("rekordbox", "/db", "My rekordbox", records, nodes)
    return lib


def col(title):
    return next(i for i, c in enumerate(TRACK_COLUMNS) if c.title == title)


def cell(model, row, title, role=Qt.ItemDataRole.DisplayRole):
    return model.data(model.index(row, col(title)), role)


# -- track table ----------------------------------------------------------------


def test_track_table_shows_the_fields_a_dj_picks_by(qapp):
    lib = make_lib()
    model = TrackTableModel()
    model.set_tracks(lib.tracks(sort="title"))
    assert model.rowCount() == 4
    assert [model.headerData(i, Qt.Orientation.Horizontal) for i in range(model.columnCount())] == [
        "", "Title", "Artist", "BPM", "Key", "Time", "Preview", "Genre",
    ]  # fmt: skip
    assert cell(model, 1, "Title") == "Title 1"
    assert cell(model, 1, "BPM") == "121.0"
    assert cell(model, 1, "Time") == "2:01"
    assert cell(model, 1, "Key") == "8A" and cell(model, 1, "Genre") == "House"
    assert (
        cell(model, 0, "BPM", Qt.ItemDataRole.TextAlignmentRole)
        & Qt.AlignmentFlag.AlignRight
    )
    lib.close()


def test_missing_files_are_dimmed_and_explained(qapp):
    lib = make_lib()  # the paths don't exist, so every track is missing
    model = TrackTableModel()
    model.set_tracks(lib.tracks())
    assert cell(model, 0, "Title", Qt.ItemDataRole.ForegroundRole) is not None
    assert "File not found" in cell(model, 0, "Title", Qt.ItemDataRole.ToolTipRole)
    lib.close()


def test_the_marker_column_shows_tracks_already_on_the_disc(qapp):
    lib = make_lib()
    model = TrackTableModel()
    tracks = lib.tracks(sort="title")
    model.set_tracks(tracks)
    model.set_on_disc({tracks[1].id})
    assert [model.data(model.index(r, 0)) for r in range(4)] == ["", "●", "", ""]
    lib.close()


def test_dragging_track_rows_carries_their_ids_in_view_order(qapp):
    lib = make_lib()
    model = TrackTableModel()
    tracks = lib.tracks(sort="title")
    model.set_tracks(tracks)
    mime = model.mimeData([model.index(2, 1), model.index(0, 1), model.index(2, 2)])
    assert json.loads(bytes(mime.data(TRACK_MIME)).decode()) == [
        tracks[0].id,
        tracks[2].id,
    ]
    assert model.supportedDragActions() == Qt.DropAction.CopyAction
    lib.close()


# -- disc list ------------------------------------------------------------------


def disc_model(lib, count=3):
    disc_id = lib.create_disc("D")
    lib.add_to_disc(disc_id, [t.id for t in lib.tracks(sort="title")][:count])
    model = DiscModel()
    model.set_entries(lib.disc_entries(disc_id))
    return model


def test_disc_list_shows_position_time_and_artist(qapp):
    lib = make_lib()
    model = disc_model(lib)
    assert model.rowCount() == 3
    assert model.data(model.index(1, 0)) == "2"
    assert (
        model.data(model.index(1, 0), Qt.ItemDataRole.DecorationRole) is not None
    )  # the swatch
    assert model.data(model.index(1, 1)).startswith(
        "~"
    )  # not measured yet: an estimate
    assert model.data(model.index(1, 2)) == "Artist 1"
    lib.set_cd_sectors(model.entry_at(1).track.id, 75 * 61)
    model.set_entries(lib.disc_entries(lib.discs()[0].id))
    assert model.data(model.index(1, 1)) == "1:01"
    lib.close()


def test_a_drop_from_the_track_table_asks_for_the_tracks_to_be_added_there(qapp):
    lib = make_lib()
    model = disc_model(lib)
    got = []
    model.tracks_dropped.connect(lambda ids, row: got.append((ids, row)))
    mime = QMimeData()
    mime.setData(TRACK_MIME, json.dumps([7, 8]).encode())
    assert model.dropMimeData(mime, Qt.DropAction.CopyAction, 1, 0, QModelIndex())
    assert model.dropMimeData(
        mime, Qt.DropAction.CopyAction, -1, -1, model.index(2, 0)
    )  # onto a row
    assert model.dropMimeData(
        mime, Qt.DropAction.CopyAction, -1, -1, QModelIndex()
    )  # onto empty space
    assert got == [([7, 8], 1), ([7, 8], 2), ([7, 8], 3)]
    lib.close()


def test_dragging_rows_inside_the_list_asks_for_a_move(qapp):
    lib = make_lib()
    model = disc_model(lib)
    got = []
    model.rows_moved.connect(lambda rows, row: got.append((rows, row)))
    mime = model.mimeData([model.index(2, 0), model.index(0, 3)])
    assert json.loads(bytes(mime.data(DISC_ROWS_MIME)).decode()) == [0, 2]
    model.dropMimeData(mime, Qt.DropAction.MoveAction, 1, 0, QModelIndex())
    assert got == [([0, 2], 1)]
    lib.close()


def test_the_disc_model_never_deletes_rows_itself(qapp):
    lib = make_lib()
    model = disc_model(lib)
    assert model.removeRows(0, 1) is False and model.rowCount() == 3
    assert (
        model.flags(QModelIndex()) & Qt.ItemFlag.ItemIsDropEnabled
    )  # dropping on empty space works
    lib.close()


# -- source tree ----------------------------------------------------------------


def test_source_tree_nests_nodes_under_their_source_with_counts(qapp):
    lib = make_lib()
    model = build_source_model(lib)
    root = model.invisibleRootItem()
    assert [root.child(r).text() for r in range(root.rowCount())] == [
        "All Tracks",
        "My rekordbox",
    ]
    assert model.item(0, 1).text() == "4"
    source = root.child(1)
    assert (
        source.child(0).text() == "Folder"
        and source.child(0).child(0).text() == "Playlist"
    )
    assert source.child(0).child(0, 1).text() == "4"
    assert source.child(0, 1).text() == ""  # a folder holds no tracks of its own
    assert source.child(0).child(0).data(NAME_ROLE) == "Playlist"
    lib.close()


def test_find_selection_locates_nested_items(qapp):
    lib = make_lib()
    model = build_source_model(lib)
    [source] = lib.sources()
    node = next(n for n in lib.nodes(source.id) if n.name == "Playlist")
    index = find_selection(model, ("node", node.id, source.id))
    assert index.isValid() and index.data(SELECTION_ROLE) == (
        "node",
        node.id,
        source.id,
    )
    assert find_selection(model, ("source", source.id)).isValid()
    assert not find_selection(model, ("node", 999, source.id)).isValid()
    lib.close()


# -- capacity bar ---------------------------------------------------------------


def entries(lengths, pregap_exact=True, folder=None):
    lib = Library.open(":memory:")

    def path(i):
        if folder is None:
            return f"/nowhere/{i}.mp3"
        file = folder / f"{i}.mp3"
        file.write_bytes(b"x")
        return str(file)

    lib.replace_source("folder", "/m", "M", [
        TrackRecord(ext_id=f"t{i}", path=path(i), title=f"T{i}", artist="A", duration=1) for i in range(len(lengths))
    ])  # fmt: skip
    tracks = lib.tracks(sort="path")
    for track, sectors in zip(tracks, lengths, strict=True):
        if pregap_exact:
            lib.set_cd_sectors(track.id, sectors)
    disc_id = lib.create_disc("D", capacity_sectors=1000)
    lib.add_to_disc(disc_id, [t.id for t in tracks])
    return lib, disc_id, lib.disc_entries(disc_id)


def test_spans_are_one_per_track_with_gaps_between(qapp):
    lib, _, ents = entries([100, 200, 300])
    assert [s.kind for s in disc_spans(ents, 0)] == ["track"] * 3
    spans = disc_spans(ents, 150)
    assert [s.kind for s in spans] == ["track", "gap", "track", "gap", "track"]
    assert [s.weight for s in spans] == [100, 150, 200, 150, 300]
    assert spans[0].tooltip == "1. A – T0\n0:01"
    lib.close()


def test_the_bar_fills_in_proportion_to_capacity_when_not_full(qapp):
    lib, _, ents = entries([250, 250])
    bar = CapacityBar()
    bar.resize(1000, 16)
    bar.set_spans(disc_spans(ents), 1000)
    rects = bar.span_rects()
    assert (
        abs(rects[0].width() - 0.25 * 999) < 1
        and abs(sum(r.width() for r in rects) - 0.5 * 999) < 1
    )
    assert bar.over_x() is None
    lib.close()


def test_the_bar_shows_everything_and_marks_the_end_when_over(qapp):
    lib, _, ents = entries([600, 600])
    bar = CapacityBar()
    bar.resize(1000, 16)
    bar.set_spans(disc_spans(ents), 1000)
    assert abs(sum(r.width() for r in bar.span_rects()) - 999) < 1  # scaled to fit
    assert abs(bar.over_x() - (0.5 + 1000 / 1200 * 999)) < 2
    lib.close()


def test_a_tiny_track_still_shows_as_a_hairline(qapp):
    lib, _, ents = entries([1, 900])
    bar = CapacityBar()
    bar.resize(1000, 16)
    bar.set_spans(disc_spans(ents), 1000)
    assert bar.span_rects()[0].width() >= 1.0
    lib.close()


def test_summary_text_and_level(qapp, tmp_path):
    lib, disc_id, ents = entries([300, 300], folder=tmp_path)
    summary_widget = CapacitySummary()
    summary_widget.show_disc(lib.disc(disc_id), ents)
    assert summary_widget.label.property("level") == "normal"
    assert (
        "2 tracks" in summary_widget.label.text()
        and "free" in summary_widget.label.text()
    )

    lib.update_disc(disc_id, capacity_sectors=500)
    summary_widget.show_disc(lib.disc(disc_id), lib.disc_entries(disc_id))
    assert summary_widget.label.property("level") == "critical"
    assert "over by" in summary_widget.label.text()
    lib.close()


def test_summary_warns_about_missing_tracks_and_flags_estimates(qapp):
    lib, disc_id, ents = entries(
        [300], pregap_exact=False
    )  # not measured, and the file isn't there
    widget = CapacitySummary()
    widget.show_disc(lib.disc(disc_id), ents)
    assert widget.label.text().startswith("~")
    assert (
        widget.label.property("level") == "warning" and "missing" in widget.label.text()
    )
    widget.clear()
    assert widget.label.text() == "" and widget.bar.spans == []
    lib.close()


def test_disc_entry_type_is_what_the_model_expects():
    assert DiscEntry.__dataclass_fields__.keys() >= {"position", "track"}
