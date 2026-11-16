"""The rekordbox memory-cue writer, on synthetic databases only (never a real library)."""

import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pyrekordbox.masterdb import models
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import Session

from dubcrate import rekordbox_write as rw
from dubcrate.library.store import path_key

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
A = "/Music/a.mp3"
B = "/Music/b.mp3"


@pytest.fixture(autouse=True)
def _rekordbox_closed(monkeypatch):
    """pyrekordbox's commit refuses while a rekordbox is running: tests must not depend on the machine."""
    monkeypatch.setattr("pyrekordbox.masterdb.database.get_rekordbox_pid", lambda: 0)
    monkeypatch.setattr("pyrekordbox.utils.get_rekordbox_pid", lambda: 0)


def cue(cid, content, ms, kind=0, out=-1, comment="", deleted=0, **extra):
    return models.DjmdCue(
        ID=cid,
        ContentID=content,
        ContentUUID=f"uuid-{content}",
        UUID=str(uuid.uuid4()),
        InMsec=ms,
        InFrame=int(ms * 0.15),
        InMpegFrame=0,
        InMpegAbs=0,
        OutMsec=out,
        OutFrame=0,
        OutMpegFrame=0,
        OutMpegAbs=0,
        Kind=kind,
        Color=extra.pop("Color", -1),
        ColorTableIndex=0,
        ActiveLoop=0,
        Comment=comment,
        BeatLoopSize=0,
        CueMicrosec=0,
        InPointSeekInfo="",
        OutPointSeekInfo="",
        rb_local_deleted=deleted,
        created_at=extra.pop("created_at", T0),
        updated_at=T0,
        **extra,
    )


def content(cid, path, cue_updated="3"):
    return models.DjmdContent(
        ID=cid,
        UUID=f"uuid-{cid}",
        FolderPath=path,
        Title=Path(path).stem,
        rb_local_usn=50,
        CueUpdated=cue_updated,
        created_at=T0,
        updated_at=T0,
    )


@pytest.fixture
def library(tmp_path) -> Path:
    path = tmp_path / "rb" / "master.db"
    path.parent.mkdir()
    engine = create_engine(f"sqlite:///{path}")
    # rekordbox's own file allows NULL almost everywhere (the model says NOT NULL): copy the tables so.
    meta = MetaData()
    for table in models.Base.metadata.sorted_tables:
        for column in table.to_metadata(meta).columns:
            column.nullable = column.nullable or not column.primary_key
    meta.create_all(engine)
    with Session(engine) as s:
        s.add(
            models.AgentRegistry(
                registry_id="localUpdateCount",
                int_1=100,
                int_2=0,
                id_1="",
                id_2="",
                str_1="",
                str_2="",
                text_1="",
                text_2="",
                date_1=T0,
                date_2=T0,
                created_at=T0,
                updated_at=T0,
            )
        )
        s.add_all([content("1", A), content("2", B)])
        s.add_all(
            [
                cue("10", "1", 1000, comment="intro"),
                cue("11", "1", 5000),
                cue("12", "1", 9000, kind=1),  # hot cue A: never touched
                cue("13", "1", 12000, kind=0, out=16000),  # a memory loop: left alone
                cue("14", "1", 7000, deleted=1),  # already deleted
                cue("20", "2", 2000),
            ]
        )
        s.commit()
    engine.dispose()
    return path


def raw(path: Path, sql: str, *args):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql, args).fetchall()


def live(path: Path, content_id: str) -> list[tuple[int, str]]:
    rows = raw(
        path,
        "SELECT InMsec, Comment FROM djmdCue WHERE ContentID = ? AND Kind = 0 "
        "AND rb_local_deleted = 0 AND OutMsec <= InMsec ORDER BY InMsec",
        content_id,
    )
    return [(int(a), b or "") for a, b in rows]


def write(library, edits, tmp_path, **kw):
    return rw.apply_write(library, edits, tmp_path / "backups", unlock=False, **kw)


def test_plan_reports_changes_without_writing(library):
    before = library.read_bytes()
    plan = rw.plan_write(
        library, {path_key(A): [(1000, "intro"), (6000, "drop")]}, unlock=False
    )
    assert [c.title for c in plan.changes] == ["a"]
    change = plan.changes[0]
    assert change.before == [(1000, "intro"), (5000, "")]
    assert change.after == [(1000, "intro"), (6000, "drop")]
    assert library.read_bytes() == before


def test_unchanged_unknown_and_ambiguous(library):
    edits = {
        path_key(A): [(1000, "intro"), (5000, "")],  # the same as rekordbox has
        path_key("/Music/zzz.mp3"): [(1, "")],
    }
    plan = rw.plan_write(library, edits, unlock=False)
    assert plan.empty
    assert plan.unchanged == [path_key(A)]
    assert plan.skipped[0][0] == path_key("/Music/zzz.mp3")

    engine = create_engine(f"sqlite:///{library}")
    with Session(engine) as s:
        s.add(content("3", A))
        s.commit()
    engine.dispose()
    plan = rw.plan_write(library, {path_key(A): [(1, "")]}, unlock=False)
    assert plan.skipped and "2 times" in plan.skipped[0][1]


def test_write_adds_moves_and_removes(library, tmp_path):
    key = path_key(A)
    out = write(
        library, {key: [(1000, "intro"), (6000, "drop"), (20000, "end")]}, tmp_path
    )
    assert out.written == [key]
    assert (out.cues_added, out.cues_moved, out.cues_removed) == (1, 1, 0)
    assert live(library, "1") == [(1000, "intro"), (6000, "drop"), (20000, "end")]
    # the hot cue, the memory loop and the other track are as they were
    assert raw(
        library, "SELECT InMsec, Kind FROM djmdCue WHERE ID IN ('12', '13')"
    ) == [
        (9000, 1),
        (12000, 0),
    ]
    assert live(library, "2") == [(2000, "")]
    assert out.backup is not None and (out.backup / "master.db").is_file()


def test_removed_cue_is_soft_deleted(library, tmp_path):
    out = write(library, {path_key(A): [(1000, "intro")]}, tmp_path)
    assert out.cues_removed == 1
    assert raw(library, "SELECT rb_local_deleted FROM djmdCue WHERE ID = '11'") == [
        (1,)
    ]
    assert raw(library, "SELECT COUNT(*) FROM djmdCue") == [
        (6,)
    ]  # the row is still there


def test_all_cues_deleted(library, tmp_path):
    write(library, {path_key(A): []}, tmp_path)
    assert live(library, "1") == []
    assert raw(library, "SELECT InMsec FROM djmdCue WHERE ID = '12'") == [(9000,)]


def test_new_cue_row_shape_and_bookkeeping(library, tmp_path):
    engine = create_engine(f"sqlite:///{library}")
    with Session(engine) as s:  # the newest memory cue rekordbox made
        s.add(
            cue("30", "2", 3000, Color=255, created_at=datetime(2026, 5, 5, tzinfo=UTC))
        )
        s.commit()
    engine.dispose()
    write(
        library, {path_key(A): [(1000, "intro"), (5000, ""), (6543, "new")]}, tmp_path
    )
    row = raw(
        library,
        "SELECT Kind, Color, ColorTableIndex, ActiveLoop, OutMsec, InFrame, Comment, "
        "rb_local_deleted, rb_local_usn, created_at, ContentUUID FROM djmdCue WHERE InMsec = 6543",
    )[0]
    assert row[:8] == (0, 255, 0, 0, -1, 981, "new", 0)
    counter = raw(library, "SELECT int_1 FROM agentRegistry")[0][0]
    assert 100 < row[8] <= counter  # pyrekordbox counts each change it saw
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} \+00:00", row[9])
    assert row[10] == "uuid-1"
    cue_updated, usn, stamp = raw(
        library,
        "SELECT CueUpdated, rb_local_usn, updated_at FROM djmdContent WHERE ID = '1'",
    )[0]
    assert cue_updated == "4" and 100 < usn <= counter
    assert stamp != "2026-01-01 12:00:00.000 +00:00"
    assert raw(
        library, "SELECT CueUpdated, rb_local_usn FROM djmdContent WHERE ID = '2'"
    ) == [("3", 50)]


def test_label_only_change_keeps_the_row(library, tmp_path):
    out = write(library, {path_key(A): [(1000, "start"), (5000, "")]}, tmp_path)
    assert (out.cues_added, out.cues_removed) == (0, 0)
    assert raw(library, "SELECT ID FROM djmdCue WHERE Comment = 'start'") == [("10",)]


def test_nothing_to_write_makes_no_backup(library, tmp_path):
    out = write(library, {path_key(A): [(1000, "intro"), (5000, "")]}, tmp_path)
    assert out.backup is None and out.written == []
    assert out.done_keys == [path_key(A)]
    assert not (tmp_path / "backups").exists()


def test_refuses_while_rekordbox_runs(library, tmp_path):
    before = library.read_bytes()
    with pytest.raises(rw.WriteError, match="running"):
        write(library, {path_key(A): []}, tmp_path, running=lambda: True)
    assert library.read_bytes() == before


def test_refuses_an_unknown_schema(library, tmp_path):
    with sqlite3.connect(library) as conn:
        conn.execute("ALTER TABLE djmdCue ADD COLUMN Shiny INTEGER")
    before = library.read_bytes()
    with pytest.raises(rw.WriteError, match="Shiny"):
        write(library, {path_key(A): []}, tmp_path)
    assert library.read_bytes() == before
    assert not (tmp_path / "backups").exists()


def test_a_failed_write_puts_the_library_back(library, tmp_path, monkeypatch):
    def boom(self, autoinc=True):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("pyrekordbox.masterdb.database.MasterDatabase.commit", boom)
    before = live(library, "1")
    with pytest.raises(rw.WriteError, match="put back"):
        write(library, {path_key(A): [(1, "x")]}, tmp_path)
    assert live(library, "1") == before


def test_a_bad_read_back_restores_the_backup(library, tmp_path, monkeypatch):
    original = rw._write_changes

    def wrong(db, targets, plan):
        counts = original(db, targets, plan)
        for cues in (c for _, c in targets.values()):
            for row in cues:
                row.InMsec = 123  # something the read-back must notice
        return counts

    monkeypatch.setattr(rw, "_write_changes", wrong)
    before = library.read_bytes()
    with pytest.raises(rw.WriteError, match="read back"):
        write(library, {path_key(A): [(1000, "intro"), (6000, "")]}, tmp_path)
    assert live(library, "1") == [(1000, "intro"), (5000, "")]
    assert raw(library, "SELECT int_1 FROM agentRegistry") == [(100,)]
    assert len(before) > 0


def test_restore_removes_sidecars_the_backup_lacks(tmp_path):
    db = tmp_path / "master.db"
    db.write_bytes(b"new")
    (tmp_path / "master.db-wal").write_bytes(b"wal")
    backup = tmp_path / "bk"
    backup.mkdir()
    (backup / "master.db").write_bytes(b"old")
    rw.restore(backup, db)
    assert db.read_bytes() == b"old"
    assert not (tmp_path / "master.db-wal").exists()


# -- memory loops: only ever added, in the shape rekordbox 7.2.19 wrote for loops the owner made ----------


def loops_of(path: Path, content_id: str):
    rows = raw(
        path,
        "SELECT InMsec, OutMsec, Comment FROM djmdCue WHERE ContentID = ? AND Kind = 0 "
        "AND rb_local_deleted = 0 AND OutMsec > InMsec ORDER BY InMsec",
        content_id,
    )
    return [(int(a), int(b), c or "") for a, b, c in rows]


def test_plan_reports_a_loop_to_add_and_leaves_the_memory_cues_alone(library):
    before = library.read_bytes()
    plan = rw.plan_write(
        library, {}, loops={path_key(A): [(20000, 24000, "chorus")]}, unlock=False
    )
    [change] = plan.changes
    assert change.new_loops == [(20000, 24000, "chorus")]
    assert change.before == change.after  # (its memory cues are not part of this)
    assert change.before_loops == [(12000, 16000, "")]
    assert change.after_loops == [(12000, 16000, ""), (20000, 24000, "chorus")]
    assert library.read_bytes() == before


def test_a_loop_is_written_as_the_row_rekordbox_wrote_for_its_own_loops(
    library, tmp_path
):
    key = path_key(A)
    out = write(library, {}, tmp_path, loops={key: [(20000, 24000, "chorus")]})
    assert (out.written, out.loops_added, out.cues_added) == ([key], 1, 0)
    row = raw(
        library,
        "SELECT Kind, InMsec, OutMsec, InFrame, OutFrame, InMpegFrame, InMpegAbs, OutMpegFrame, OutMpegAbs, "
        "Color, ColorTableIndex, ActiveLoop, BeatLoopSize, CueMicrosec, Comment, rb_local_deleted, "
        "ContentUUID, rb_local_usn FROM djmdCue WHERE InMsec = 20000",
    )[0]
    # (no frame numbers, a hand-set loop's BeatLoopSize 0: as rekordbox 7.2.19 wrote them)
    assert row[:16] == (0, 20000, 24000, 0, None, 0, 0, None, None, 255, 0, 0, 0, None, "chorus", 0)  # fmt: skip
    assert row[16] == "uuid-1" and row[17] is not None and row[17] > 100
    assert raw(library, "SELECT CueUpdated FROM djmdContent WHERE ID = '1'") == [("4",)]
    assert raw(library, "SELECT CueUpdated FROM djmdContent WHERE ID = '2'") == [("3",)]
    assert loops_of(library, "1") == [(12000, 16000, ""), (20000, 24000, "chorus")]
    assert live(library, "1") == [
        (1000, "intro"),
        (5000, ""),
    ]  # (the memory cues are as they were)


def test_what_it_writes_reads_back_as_a_memory_loop_and_not_a_hot_cue(
    library, tmp_path
):
    from dubcrate.library.rekordbox import cue_from_row

    write(library, {}, tmp_path, loops={path_key(A): [(20000, 24000, "")]})
    engine = create_engine(f"sqlite:///{library}")
    with Session(engine) as s:
        row = s.query(models.DjmdCue).filter_by(InMsec=20000).one()
        found = cue_from_row(row)
    engine.dispose()
    assert (found.kind, found.slot, found.time, found.end) == ("loop", None, 20.0, 24.0)


def test_a_loop_rekordbox_already_has_is_not_added_again_even_a_few_ms_off(
    library, tmp_path
):
    key = path_key(A)
    plan = rw.plan_write(library, {}, loops={key: [(12020, 15990, "")]}, unlock=False)
    assert plan.empty and plan.unchanged == [key]
    out = write(library, {}, tmp_path, loops={key: [(12020, 15990, "")]})
    assert out.backup is None and out.done_keys == [key]
    assert loops_of(library, "1") == [(12000, 16000, "")]


def test_adding_a_loop_changes_no_other_row(library, tmp_path):
    before = raw(library, "SELECT * FROM djmdCue WHERE ID != '30' ORDER BY ID")
    write(library, {}, tmp_path, loops={path_key(A): [(20000, 24000, "")]})
    after = raw(library, "SELECT * FROM djmdCue WHERE ID IN ('10','11','12','13','14','20') ORDER BY ID")  # fmt: skip
    assert after == [r for r in before if r[0] in ("10", "11", "12", "13", "14", "20")]


def test_a_deleted_loop_does_not_count_and_a_hot_cues_loop_is_not_a_memory_loop(
    library, tmp_path
):
    engine = create_engine(f"sqlite:///{library}")
    with Session(engine) as s:
        s.add(
            cue("40", "1", 30000, kind=0, out=34000, deleted=1)
        )  # a deleted memory loop
        s.add(cue("41", "1", 40000, kind=2, out=44000))  # hot cue B saved as a loop
        s.commit()
    engine.dispose()
    key = path_key(A)
    plan = rw.plan_write(library, {}, loops={key: [(30000, 34000, ""), (40000, 44000, "")]}, unlock=False)  # fmt: skip
    assert plan.changes[0].new_loops == [(30000, 34000, ""), (40000, 44000, "")]
    write(library, {}, tmp_path, loops={key: [(30000, 34000, ""), (40000, 44000, "")]})
    assert loops_of(library, "1") == [(12000, 16000, ""), (30000, 34000, ""), (40000, 44000, "")]  # fmt: skip
    assert raw(library, "SELECT Kind, rb_local_deleted FROM djmdCue WHERE ID IN ('40', '41') ORDER BY ID") == [(0, 1), (2, 0)]  # fmt: skip


def test_a_backwards_or_empty_loop_is_skipped_and_the_same_loop_twice_is_one(
    library, tmp_path
):
    key = path_key(A)
    out = write(library, {}, tmp_path, loops={key: [(9000, 8000, ""), (5000, 5000, ""), (20000, 24000, "a"), (20000, 24000, "a")]})  # fmt: skip
    assert out.loops_added == 1
    assert loops_of(library, "1") == [(12000, 16000, ""), (20000, 24000, "a")]


def test_loops_and_memory_cue_edits_are_written_together_in_one_transaction(
    library, tmp_path
):
    key = path_key(A)
    out = write(library, {key: [(1000, "intro"), (6000, "")]}, tmp_path, loops={key: [(20000, 24000, "")]})  # fmt: skip
    assert (out.cues_added, out.cues_moved, out.loops_added) == (0, 1, 1)
    assert live(library, "1") == [(1000, "intro"), (6000, "")]
    assert loops_of(library, "1") == [(12000, 16000, ""), (20000, 24000, "")]
    assert raw(library, "SELECT CueUpdated FROM djmdContent WHERE ID = '1'") == [
        ("4",)
    ]  # (once, not twice)


def test_a_loop_for_a_file_that_is_not_in_rekordbox_is_skipped(library, tmp_path):
    out = write(library, {}, tmp_path, loops={path_key("/Music/none.mp3"): [(1000, 2000, "")]})  # fmt: skip
    assert out.written == [] and out.plan.skipped[0][0] == path_key("/Music/none.mp3")


def test_a_bad_loop_read_back_restores_the_backup(library, tmp_path, monkeypatch):
    original = rw._write_changes

    def wrong(db, targets, plan):
        counts = original(db, targets, plan)
        for row in db.session.query(models.DjmdCue).filter_by(InMsec=20000):
            row.OutMsec = 99999  # something the read-back must notice
        return counts

    monkeypatch.setattr(rw, "_write_changes", wrong)
    with pytest.raises(rw.WriteError, match="loops read back"):
        write(library, {}, tmp_path, loops={path_key(A): [(20000, 24000, "")]})
    assert loops_of(library, "1") == [(12000, 16000, "")]
    assert raw(library, "SELECT int_1 FROM agentRegistry") == [(100,)]
    assert raw(library, "SELECT COUNT(*) FROM djmdCue WHERE InMsec = 20000") == [(0,)]


def test_a_failed_commit_leaves_no_loop_behind(library, tmp_path, monkeypatch):
    def boom(self, autoinc=True):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("pyrekordbox.masterdb.database.MasterDatabase.commit", boom)
    with pytest.raises(rw.WriteError, match="put back"):
        write(library, {}, tmp_path, loops={path_key(A): [(20000, 24000, "")]})
    assert loops_of(library, "1") == [(12000, 16000, "")]


# -- memory loops deleted in Dubcrate: the row is soft deleted, as a memory cue's is ---------------------


def test_plan_reports_a_loop_to_delete_and_changes_nothing(library):
    before = library.read_bytes()
    plan = rw.plan_write(library, {}, remove_loops={path_key(A): [(12000, 16000)]}, unlock=False)  # fmt: skip
    [change] = plan.changes
    assert change.removed_loops == [(12000, 16000, "")] and change.new_loops == []
    assert change.after_loops == []
    assert library.read_bytes() == before


def test_a_loop_is_deleted_by_a_soft_delete_and_nothing_else_changes(library, tmp_path):
    key = path_key(A)
    out = write(library, {}, tmp_path, remove_loops={key: [(12000, 16000)]})
    assert (out.written, out.loops_removed, out.loops_added) == ([key], 1, 0)
    assert raw(library, "SELECT rb_local_deleted, InMsec, OutMsec FROM djmdCue WHERE ID = '13'") == [(1, 12000, 16000)]  # fmt: skip
    assert raw(library, "SELECT COUNT(*) FROM djmdCue") == [
        (6,)
    ]  # (the row is still there)
    assert loops_of(library, "1") == []
    assert live(library, "1") == [
        (1000, "intro"),
        (5000, ""),
    ]  # the memory cues are as they were
    assert raw(library, "SELECT InMsec, Kind, rb_local_deleted FROM djmdCue WHERE ID = '12'") == [(9000, 1, 0)]  # fmt: skip
    assert raw(library, "SELECT CueUpdated FROM djmdContent WHERE ID = '1'") == [("4",)]
    assert raw(library, "SELECT CueUpdated FROM djmdContent WHERE ID = '2'") == [("3",)]
    usn = raw(library, "SELECT rb_local_usn FROM djmdCue WHERE ID = '13'")[0][0]
    assert usn is not None and usn > 100  # (pyrekordbox counted the change)


def test_a_deletion_matches_within_a_few_milliseconds_and_takes_every_row_there(library, tmp_path):  # fmt: skip
    engine = create_engine(f"sqlite:///{library}")
    with Session(engine) as s:
        s.add(
            cue("40", "1", 12010, kind=0, out=15995)
        )  # a second row for the same loop
        s.commit()
    engine.dispose()
    out = write(library, {}, tmp_path, remove_loops={path_key(A): [(12005, 16000)]})
    assert out.loops_removed == 2 and loops_of(library, "1") == []


def test_a_deletion_of_a_loop_rekordbox_does_not_have_is_nothing_to_write(library, tmp_path):  # fmt: skip
    key = path_key(A)
    plan = rw.plan_write(library, {}, remove_loops={key: [(30000, 34000)]}, unlock=False)  # fmt: skip
    assert plan.empty and plan.unchanged == [key]
    out = write(library, {}, tmp_path, remove_loops={key: [(30000, 34000)]})
    assert out.backup is None and out.done_keys == [key]


def test_a_hot_cues_loop_and_a_deleted_loop_are_never_taken_for_a_memory_loop(library, tmp_path):  # fmt: skip
    engine = create_engine(f"sqlite:///{library}")
    with Session(engine) as s:
        s.add(cue("41", "1", 40000, kind=2, out=44000))  # hot cue B saved as a loop
        s.commit()
    engine.dispose()
    out = write(library, {}, tmp_path, remove_loops={path_key(A): [(40000, 44000)]})
    assert out.backup is None  # nothing matched: a hot cue's loop is not a memory loop
    assert raw(library, "SELECT rb_local_deleted FROM djmdCue WHERE ID = '41'") == [
        (0,)
    ]


def test_a_loop_can_be_added_and_another_deleted_in_one_write(library, tmp_path):
    key = path_key(A)
    out = write(library, {}, tmp_path, loops={key: [(20000, 24000, "new")]}, remove_loops={key: [(12000, 16000)]})  # fmt: skip
    assert (out.loops_added, out.loops_removed) == (1, 1)
    assert loops_of(library, "1") == [(20000, 24000, "new")]
    assert raw(library, "SELECT CueUpdated FROM djmdContent WHERE ID = '1'") == [
        ("4",)
    ]  # (once)


def test_deleting_and_adding_the_same_loop_leaves_the_one_added(library, tmp_path):
    key = path_key(A)
    write(library, {}, tmp_path, loops={key: [(12000, 16000, "again")]}, remove_loops={key: [(12000, 16000)]})  # fmt: skip
    assert loops_of(library, "1") == [(12000, 16000, "again")]


def test_a_bad_read_back_of_a_deletion_restores_the_backup(library, tmp_path, monkeypatch):  # fmt: skip
    original = rw._write_changes

    def wrong(db, targets, plan):
        counts = original(db, targets, plan)
        for row in db.session.query(models.DjmdCue).filter_by(ID="13"):
            row.rb_local_deleted = 0  # the deletion did not hold
        return counts

    monkeypatch.setattr(rw, "_write_changes", wrong)
    with pytest.raises(rw.WriteError, match="loops read back"):
        write(library, {}, tmp_path, remove_loops={path_key(A): [(12000, 16000)]})
    assert loops_of(library, "1") == [(12000, 16000, "")]
    assert raw(library, "SELECT int_1 FROM agentRegistry") == [(100,)]


def test_commit_is_called_only_by_the_writer():
    """The one place that may write to rekordbox: a `.commit(` anywhere else is a new way to."""
    src = Path(rw.__file__).parent
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name == "rekordbox_write.py" or "pyrekordbox" not in text:
            continue
        assert "commit(autoinc" not in text, path
        assert ".session.commit" not in text, path


def test_library_lists_and_clears_edited_sets(tmp_path):
    from dubcrate.library import Library

    lib = Library.open(tmp_path / "lib.db")
    lib.set_memory_cues(A, [(5000, "b"), (1000, "a")])
    lib.set_memory_cues(B, [])
    assert lib.memory_cue_sets() == {
        path_key(A): [(1000, "a"), (5000, "b")],
        path_key(B): [],
    }
    lib.clear_memory_cue_key(path_key(A))
    assert lib.memory_cue_sets() == {path_key(B): []}


def test_cli_dry_run_then_write(library, tmp_path, monkeypatch, capsys):
    import argparse

    from dubcrate import cli_rekordbox
    from dubcrate.library import Library

    real_open = rw._open
    monkeypatch.setattr(rw, "_open", lambda path, unlock: real_open(path, False))
    monkeypatch.setattr(rw, "default_backup_dir", lambda: tmp_path / "bk")
    monkeypatch.setattr(cli_rekordbox, "default_backup_dir", lambda: tmp_path / "bk")
    lib = Library.open(tmp_path / "lib.db")
    lib.set_memory_cues(A, [(1000, "intro"), (8000, "drop")])
    args = argparse.Namespace(
        db=str(library), backup_dir=None, keep_edits=False, yes=False, loops=False
    )

    assert cli_rekordbox.cmd_write_memory_cues(lib, args) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out and "0:08.000 'drop'" in out
    assert live(library, "1") == [(1000, "intro"), (5000, "")]
    assert lib.memory_cue_sets()  # still there

    args.yes = True
    assert cli_rekordbox.cmd_write_memory_cues(lib, args) == 0
    assert live(library, "1") == [(1000, "intro"), (8000, "drop")]
    assert lib.memory_cue_sets() == {}  # dropped: rekordbox has them now
    assert (tmp_path / "bk").is_dir()


def test_cli_with_nothing_edited(tmp_path, capsys):
    import argparse

    from dubcrate import cli_rekordbox
    from dubcrate.library import Library

    lib = Library.open(tmp_path / "lib.db")
    args = argparse.Namespace(db=None, backup_dir=None, keep_edits=False, yes=True, loops=False)  # fmt: skip
    assert cli_rekordbox.cmd_write_memory_cues(lib, args) == 0
    assert "nothing to write" in capsys.readouterr().out


def test_the_library_lists_and_clears_saved_loops(tmp_path):
    from dubcrate.library import Library

    lib = Library.open(tmp_path / "lib.db")
    lib.set_memory_loops(A, [(9000, 12000, "b"), (1000, 3000, "a")])
    lib.set_memory_loops(B, [(500, 900, "")])
    assert lib.memory_loop_sets() == {
        path_key(A): [(1000, 3000, "a"), (9000, 12000, "b")],
        path_key(B): [(500, 900, "")],
    }
    lib.clear_memory_loop_key(path_key(A))
    assert lib.memory_loop_sets() == {path_key(B): [(500, 900, "")]}


def test_cli_writes_loops_only_with_the_loops_option(
    library, tmp_path, monkeypatch, capsys
):
    import argparse

    from dubcrate import cli_rekordbox
    from dubcrate.library import Library

    real_open = rw._open
    monkeypatch.setattr(rw, "_open", lambda path, unlock: real_open(path, False))
    monkeypatch.setattr(cli_rekordbox, "default_backup_dir", lambda: tmp_path / "bk")
    lib = Library.open(tmp_path / "lib.db")
    lib.set_memory_loops(A, [(20000, 24000, "chorus")])
    args = argparse.Namespace(db=str(library), backup_dir=None, keep_edits=False, yes=True, loops=False)  # fmt: skip

    assert cli_rekordbox.cmd_write_memory_cues(lib, args) == 0  # (no --loops: nothing)
    out = capsys.readouterr().out
    assert "nothing to write" in out and "--loops" in out
    assert loops_of(library, "1") == [(12000, 16000, "")]

    args.loops, args.yes = True, False
    assert cli_rekordbox.cmd_write_memory_cues(lib, args) == 0  # (a dry run shows it)
    out = capsys.readouterr().out
    assert (
        "Dry run" in out and "adds:" in out and "0:20.000 to 0:24.000 'chorus'" in out
    )
    assert loops_of(library, "1") == [(12000, 16000, "")] and lib.memory_loop_sets()

    args.yes = True
    assert cli_rekordbox.cmd_write_memory_cues(lib, args) == 0
    assert "1 loop(s) added" in capsys.readouterr().out
    assert loops_of(library, "1") == [(12000, 16000, ""), (20000, 24000, "chorus")]
    assert lib.memory_loop_sets() == {}  # (dropped: rekordbox has it now)


def test_the_library_lists_and_clears_deleted_loops(tmp_path):
    from dubcrate.library import Library

    lib = Library.open(tmp_path / "lib.db")
    lib.set_memory_loops_removed(A, [(9000, 12000), (1000, 3000), (1000, 3000), (5, 5)])
    assert lib.memory_loops_removed(A) == [(1000, 3000), (9000, 12000)]  # once each, in time order, no empty ones  # fmt: skip
    lib.set_memory_loops_removed(B, [(500, 900)])
    assert lib.memory_loop_removal_sets() == {
        path_key(A): [(1000, 3000), (9000, 12000)],
        path_key(B): [(500, 900)],
    }
    lib.clear_memory_loop_removal_key(path_key(A))
    assert lib.memory_loop_removal_sets() == {path_key(B): [(500, 900)]}
    lib.clear_memory_loops(B)  # (Reset forgets them too)
    assert lib.memory_loop_removal_sets() == {}


def test_cli_deletes_loops_only_with_the_loops_option(library, tmp_path, monkeypatch, capsys):  # fmt: skip
    import argparse

    from dubcrate import cli_rekordbox
    from dubcrate.library import Library

    real_open = rw._open
    monkeypatch.setattr(rw, "_open", lambda path, unlock: real_open(path, False))
    monkeypatch.setattr(cli_rekordbox, "default_backup_dir", lambda: tmp_path / "bk")
    lib = Library.open(tmp_path / "lib.db")
    lib.set_memory_loops_removed(A, [(12000, 16000)])
    args = argparse.Namespace(db=str(library), backup_dir=None, keep_edits=False, yes=True, loops=False)  # fmt: skip

    assert cli_rekordbox.cmd_write_memory_cues(lib, args) == 0  # (no --loops: nothing)
    out = capsys.readouterr().out
    assert "nothing to write" in out and "--loops" in out
    assert loops_of(library, "1") == [(12000, 16000, "")]

    args.loops, args.yes = True, False
    assert cli_rekordbox.cmd_write_memory_cues(lib, args) == 0  # (a dry run shows it)
    out = capsys.readouterr().out
    assert "Dry run" in out and "deletes:" in out and "0:12.000 to 0:16.000" in out
    assert loops_of(library, "1") == [(12000, 16000, "")] and lib.memory_loop_removal_sets()  # fmt: skip

    args.yes = True
    assert cli_rekordbox.cmd_write_memory_cues(lib, args) == 0
    assert "1 deleted" in capsys.readouterr().out
    assert loops_of(library, "1") == []
    assert lib.memory_loop_removal_sets() == {}  # (dropped: rekordbox has it gone)
