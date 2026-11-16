"""Write memory cues, and memory loops, edited in Dubcrate into rekordbox's `master.db` (Qt-free).

**The one place Dubcrate writes to a rekordbox library**, and only memory cues and memory loops: the owner
allowed exactly this (`AGENTS.md`, "Hard rules"). Hot cues (and the loops saved in a hot cue), tracks, playlists
and everything else are never touched. **Loops are added and deleted, never changed**: a saved loop becomes a
`Kind` 0 row with an out point (the shape rekordbox 7.2.19 itself wrote for three loops the owner made, read
2026-11-16: no frame numbers, `BeatLoopSize` 0 as for a loop set by hand), and a loop deleted in Dubcrate is its
row **soft deleted** (`rb_local_deleted = 1`, as for a memory cue). A hot cue's loop is not a memory loop and is
never matched (`plan_write(loops=, remove_loops=)`, `apply_write(loops=, remove_loops=)`).
The order is the safety, as in `card_apply`:

1. **Planning reads a snapshot copy** (`plan_write`: what would change, nothing written). rekordbox must not
   be running (`rekordbox_running`), and the tables must be the ones pyrekordbox knows (`check_schema`), or
   nothing is written.
2. **The database's files are copied aside first** (`master.db`, `-wal`, `-shm`, `masterPlaylists6.xml`).
3. The change is made through pyrekordbox's own `commit(autoinc=True)`, as rbxport does, in ONE transaction:
   it bumps rekordbox's `localUpdateCount` and gives each changed row its `rb_local_usn`. A cue that goes is
   **soft deleted** (`rb_local_deleted = 1`, as rekordbox does), a moved one is changed in place, a new one
   copies the shape of the newest memory cue rekordbox itself made. The track's `CueUpdated` counter and
   `updated_at` are bumped, as rekordbox does when its cues change.
4. **It reads the file back** from a fresh snapshot (the cues are what was asked, the database passes
   `PRAGMA integrity_check`, no other track's cues changed) and **puts the copy back** if anything is wrong.

Only a file that is in rekordbox under exactly one row is written (matched on `store.path_key`, never name or
content); the rest are reported as skipped and keep their edits in Dubcrate.
**Tried on synthetic databases only** (`tests/test_rekordbox_write.py`); rekordbox 7.2.19 is the version the
owner runs. `dev_docs/rekordbox-cue-writes.md` has the measured row shapes and the live-test checklist.
"""

import logging
import math
import shutil
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dubcrate.library.cue_edits import SAME_LOOP_MS
from dubcrate.library.paths import default_library_path
from dubcrate.library.rekordbox import (
    RekordboxError,
    _snapshot,
    default_master_db,
    hot_slot,
)
from dubcrate.library.store import path_key

log = logging.getLogger(__name__)

FRAMES_PER_MS = 0.15  # `InFrame` is 150 a second; older rows have floor(InMsec x 0.15)
CHECKED_TABLES = ("djmdCue", "djmdContent", "agentRegistry")
# The shape of a memory cue when the library has none to copy (its newest ones read like this).
FALLBACK_SHAPE: dict[str, Any] = {
    "Color": 255,
    "ColorTableIndex": 0,
    "ActiveLoop": 0,
}
# The columns of a memory loop that are not copied from a memory cue: as rekordbox 7.2.19 wrote them (three loops
# the owner made on 2026-11-16: 4 beats, 8 beats and one set by hand). `BeatLoopSize` is `beats << 16 | 1` for a
# loop made with a beat size and 0 for one set by hand: a loop saved in Dubcrate has no beat size, so 0.
LOOP_SHAPE: dict[str, Any] = {
    "InFrame": 0,
    "InMpegFrame": 0,
    "InMpegAbs": 0,
    "OutFrame": None,
    "OutMpegFrame": None,
    "OutMpegAbs": None,
    "BeatLoopSize": 0,
    "CueMicrosec": None,
}


class WriteError(RekordboxError):
    """A write that was refused or undone: the message says why and whether the library is as it was."""


def default_backup_dir() -> Path:
    """Where rekordbox's files are copied before a write: `rekordbox-backups`, next to the library."""
    return default_library_path().parent / "rekordbox-backups"


def rekordbox_running() -> bool:
    """Whether rekordbox is open (it keeps `master.db` in use and would overwrite a change)."""
    from pyrekordbox.utils import get_rekordbox_pid

    return bool(get_rekordbox_pid())


@dataclass
class TrackChange:
    """One track's memory cues before and after: (milliseconds, label), in time order."""

    key: str
    content_id: str
    title: str
    path: str
    before: list[tuple[int, str]]
    after: list[tuple[int, str]]
    # memory loops, (start ms, end ms, label): what rekordbox has and what it will have (the old ones and the new)
    before_loops: list[tuple[int, int, str]] = field(default_factory=list)
    after_loops: list[tuple[int, int, str]] = field(default_factory=list)
    new_loops: list[tuple[int, int, str]] = field(
        default_factory=list
    )  # the ones to add
    removed_loops: list[tuple[int, int, str]] = field(
        default_factory=list
    )  # the ones to soft delete (rows rekordbox has)

    @property
    def changed(self) -> bool:
        return (
            self.before != self.after
            or bool(self.new_loops)
            or bool(self.removed_loops)
        )

    @property
    def added(self) -> int:
        return len(_unmatched(self.after, self.before))

    @property
    def removed(self) -> int:
        return len(_unmatched(self.before, self.after))


@dataclass
class WritePlan:
    changes: list[TrackChange] = field(default_factory=list)  # tracks that would change
    unchanged: list[str] = field(
        default_factory=list
    )  # keys whose edit equals what rekordbox has
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (key, why)

    @property
    def empty(self) -> bool:
        return not self.changes


@dataclass
class WriteOutcome:
    plan: WritePlan
    backup: (
        Path | None
    )  # the copy of the database's files; None when there was nothing to write
    written: list[str] = field(default_factory=list)  # keys written
    cues_added: int = 0
    cues_moved: int = 0  # changed in place
    cues_removed: int = 0
    loops_added: int = 0
    loops_removed: int = 0

    @property
    def done_keys(self) -> list[str]:
        """The edits rekordbox now agrees with: written, or already the same (Dubcrate may drop them)."""
        return [*self.written, *self.plan.unchanged]


def _unmatched(
    mine: Sequence[tuple[int, str]], other: Sequence[tuple[int, str]]
) -> list[tuple[int, str]]:
    """What is in `mine` and not in `other`, comparing the time only (a label change is not an add)."""
    spare = [ms for ms, _ in other]
    left = []
    for item in mine:
        if item[0] in spare:
            spare.remove(item[0])
        else:
            left.append(item)
    return left


def _open(path: Path, unlock: bool) -> Any:
    try:
        from pyrekordbox.masterdb import MasterDatabase
    except ImportError as exc:  # pragma: no cover
        raise WriteError(f"pyrekordbox is unavailable: {exc}") from exc
    try:
        return MasterDatabase(path=path, unlock=unlock)
    except Exception as exc:
        raise WriteError(f"could not open {path}: {exc}") from exc


def check_schema(db: Any) -> None:
    """Refuse a database whose tables differ from what pyrekordbox writes (a newer rekordbox changed them).

    Goes through the open session: the real file is encrypted, plain `sqlite3` can't read it."""
    from pyrekordbox.masterdb import models
    from sqlalchemy import text

    try:
        for name in CHECKED_TABLES:
            known = {c.name for c in models.Base.metadata.tables[name].columns}
            rows = db.session.execute(text(f'PRAGMA table_info("{name}")')).fetchall()
            found = {r[1] for r in rows}
            if found != known:
                odd = sorted(found ^ known)
                raise WriteError(
                    f"the {name} table is not the one this version of Dubcrate knows "
                    f"(differs in {', '.join(odd)}): nothing was written"
                )
    except WriteError:
        raise
    except Exception as exc:
        raise WriteError(f"could not check the database's tables: {exc}") from exc


def _is_plain_memory(row: Any) -> bool:
    """A memory cue that is not deleted, not a hot cue and not a loop (a memory loop is left alone)."""
    if row.rb_local_deleted:
        return False
    if hot_slot(row.Kind or 0) is not None or (row.Kind or 0) != 0:
        return False
    out = row.OutMsec if row.OutMsec is not None else -1
    return not out > (row.InMsec or 0)


def _memory_rows(db: Any, content_id: str) -> list[Any]:
    from pyrekordbox.masterdb import models

    rows = db.session.query(models.DjmdCue).filter_by(ContentID=content_id).all()
    return sorted(
        (r for r in rows if _is_plain_memory(r)), key=lambda r: (r.InMsec or 0, r.ID)
    )


def _listed(rows: Sequence[Any]) -> list[tuple[int, str]]:
    return [(int(r.InMsec or 0), str(r.Comment or "")) for r in rows]


def _is_memory_loop(row: Any) -> bool:
    """A loop that is not deleted and not a hot cue's: `Kind` 0 with an out point after its in point."""
    if row.rb_local_deleted or (row.Kind or 0) != 0:
        return False
    out = row.OutMsec if row.OutMsec is not None else -1
    return out > (row.InMsec or 0)


def _loop_rows(db: Any, content_id: str) -> list[Any]:
    from pyrekordbox.masterdb import models

    rows = db.session.query(models.DjmdCue).filter_by(ContentID=content_id).all()
    return sorted(
        (r for r in rows if _is_memory_loop(r)), key=lambda r: (r.InMsec or 0, r.ID)
    )


def _listed_loops(rows: Sequence[Any]) -> list[tuple[int, int, str]]:
    return sorted(
        (int(r.InMsec or 0), int(r.OutMsec or 0), str(r.Comment or "")) for r in rows
    )


def _new_loops(
    wanted: Sequence[tuple[int, int, str]], have: Sequence[tuple[int, int, str]]
) -> list[tuple[int, int, str]]:
    """The wanted loops rekordbox has no loop for (both ends within `SAME_LOOP_MS` of one it has). A loop that
    ends before it starts is skipped, and the same loop twice is one."""
    spare = list(have)
    adding: list[tuple[int, int, str]] = []
    for start, end, label in sorted(wanted):
        if end <= start:
            continue
        near = next(
            (
                h
                for h in spare
                if abs(h[0] - start) < SAME_LOOP_MS and abs(h[1] - end) < SAME_LOOP_MS
            ),
            None,
        )
        if near is not None:
            spare.remove(near)
            continue
        adding.append((int(start), int(end), str(label)))
        spare.append(
            (start, end, label)
        )  # (a second copy of it in `wanted` is this one)
    return adding


def _loops_to_remove(
    removal: Sequence[tuple[int, int]], have: Sequence[tuple[int, int, str]]
) -> list[tuple[int, int, str]]:
    """The loops rekordbox has that a deletion names: both ends within `SAME_LOOP_MS` of a (start, end) in `removal`.
    Every such row goes (a loop listed twice is one loop to the user)."""
    return [
        h
        for h in have
        if any(
            abs(h[0] - start) < SAME_LOOP_MS and abs(h[1] - end) < SAME_LOOP_MS
            for start, end in removal
        )
    ]


def _resolve(
    db: Any,
    edits: Mapping[str, Sequence[tuple[int, str]]],
    loops: Mapping[str, Sequence[tuple[int, int, str]]] | None = None,
    remove_loops: Mapping[str, Sequence[tuple[int, int]]] | None = None,
) -> tuple[WritePlan, dict[str, tuple[Any, list[Any]]]]:
    """Match each edit (and each track's loops to add or delete) to its rekordbox track. Returns the plan and, per
    changed key, (content row, cue rows)."""
    from pyrekordbox.masterdb import models

    loops = loops or {}
    remove_loops = remove_loops or {}
    keys = set(edits) | set(loops) | set(remove_loops)
    by_key: dict[str, list[Any]] = {}
    for content in db.session.query(models.DjmdContent).all():
        if content.rb_local_deleted or not content.FolderPath:
            continue
        key = path_key(str(content.FolderPath))
        if key in keys:
            by_key.setdefault(key, []).append(content)
    plan = WritePlan()
    targets: dict[str, tuple[Any, list[Any]]] = {}
    for key in sorted(keys):
        rows = by_key.get(key, [])
        if not rows:
            plan.skipped.append((key, "not a track in the rekordbox library"))
            continue
        if len(rows) > 1:
            plan.skipped.append(
                (
                    key,
                    f"rekordbox lists this file {len(rows)} times: not guessing which",
                )
            )
            continue
        content = rows[0]
        cues = _memory_rows(db, str(content.ID))
        before = _listed(cues)
        # (a track with only loops to add keeps its memory cues as they are)
        after = (
            sorted((int(ms), str(label)) for ms, label in edits[key])
            if key in edits
            else before
        )
        before_loops = _listed_loops(_loop_rows(db, str(content.ID)))
        removing = _loops_to_remove(remove_loops.get(key, ()), before_loops)
        staying = [h for h in before_loops if h not in removing]
        adding = _new_loops(loops.get(key, ()), staying)
        if before == after and not adding and not removing:
            plan.unchanged.append(key)
            continue
        plan.changes.append(
            TrackChange(
                key,
                str(content.ID),
                str(content.Title or ""),
                str(content.FolderPath),
                before,
                after,
                before_loops,
                sorted([*staying, *adding]),
                adding,
                removing,
            )
        )
        targets[key] = (content, cues)
    return plan, targets


def plan_write(
    master_db: Path,
    edits: Mapping[str, Sequence[tuple[int, str]]],
    *,
    loops: Mapping[str, Sequence[tuple[int, int, str]]] | None = None,
    remove_loops: Mapping[str, Sequence[tuple[int, int]]] | None = None,
    unlock: bool = True,
) -> WritePlan:
    """What a write of `edits` (memory cues by `path_key`), `loops` (memory loops to add, by `path_key`) and
    `remove_loops` ((start ms, end ms) of memory loops to delete) would change, from a snapshot copy; writes nothing."""
    if not master_db.is_file():
        raise WriteError(f"no rekordbox master.db found at {master_db}")
    with tempfile.TemporaryDirectory(prefix="dubcrate-rbw-") as tmp:
        copy = _snapshot(master_db, Path(tmp))
        db = _open(copy, unlock)
        try:
            check_schema(db)
            return _resolve(db, edits, loops, remove_loops)[0]
        except WriteError:
            raise
        except Exception as exc:
            raise WriteError(f"could not read the cues in {master_db}: {exc}") from exc
        finally:
            db.close()


def _now() -> datetime:
    """Now in UTC to the millisecond, which is how rekordbox stores a time."""
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def _template(db: Any) -> dict[str, Any]:
    """The columns a new memory cue copies from the newest one the library has (else `FALLBACK_SHAPE`)."""
    from pyrekordbox.masterdb import models

    rows = db.session.query(models.DjmdCue).filter_by(Kind=0).all()
    made = [r for r in rows if _is_plain_memory(r) and (r.Color or 0) >= 0]
    newest = max(made, key=lambda r: str(r.created_at), default=None)
    if newest is None:
        return dict(FALLBACK_SHAPE)
    shape = {name: getattr(newest, name) for name in FALLBACK_SHAPE}
    return {k: (v if v is not None else FALLBACK_SHAPE[k]) for k, v in shape.items()}


def _set_time(row: Any, ms: int) -> None:
    row.InMsec = ms
    row.InFrame = math.floor(ms * FRAMES_PER_MS)


def _write_changes(
    db: Any, targets: Mapping[str, tuple[Any, list[Any]]], plan: WritePlan
) -> tuple[int, int, int, int, int]:
    """Make the changes in the open session (nothing is committed here). Returns (cues added, moved, removed,
    loops added, loops removed)."""
    from pyrekordbox.masterdb import models

    now = _now()
    shape = _template(db)
    added = moved = removed = loops_added = loops_removed = 0
    for change in plan.changes:
        content, cues = targets[change.key]
        # Same time: keep the row (a label change updates its comment).
        spare = list(cues)
        wanted = list(change.after)
        for ms, label in list(wanted):
            match = next((r for r in spare if r.InMsec == ms), None)
            if match is None:
                continue
            spare.remove(match)
            wanted.remove((ms, label))
            if str(match.Comment or "") != label:
                match.Comment = label
                match.updated_at = now
                moved += 1
        # What is left: pair in time order, so a moved cue changes in place; extras go or are new.
        for row, (ms, label) in zip(list(spare), list(wanted), strict=False):
            _set_time(row, ms)
            row.Comment = label
            row.updated_at = now
            spare.remove(row)
            wanted.remove((ms, label))
            moved += 1
        for row in spare:
            row.rb_local_deleted = 1
            row.updated_at = now
            removed += 1
        for ms, label in wanted:
            db.session.add(
                models.DjmdCue(
                    ID=str(db.generate_unused_id(models.DjmdCue)),
                    ContentID=str(content.ID),
                    ContentUUID=content.UUID,
                    UUID=str(uuid.uuid4()),
                    InMsec=ms,
                    InFrame=math.floor(ms * FRAMES_PER_MS),
                    InMpegFrame=0,
                    InMpegAbs=0,
                    OutMsec=-1,
                    OutFrame=0,
                    OutMpegFrame=0,
                    OutMpegAbs=0,
                    Kind=0,
                    Comment=label,
                    created_at=now,
                    updated_at=now,
                    **shape,
                )
            )
            added += 1
        for start, end, label in change.new_loops:
            db.session.add(
                models.DjmdCue(
                    ID=str(db.generate_unused_id(models.DjmdCue)),
                    ContentID=str(content.ID),
                    ContentUUID=content.UUID,
                    UUID=str(uuid.uuid4()),
                    InMsec=start,
                    OutMsec=end,
                    Kind=0,
                    Comment=label,
                    created_at=now,
                    updated_at=now,
                    **LOOP_SHAPE,
                    **shape,
                )
            )
            loops_added += 1
        if change.removed_loops:
            rows = _loop_rows(db, str(content.ID))
            for start, end, label in change.removed_loops:
                row = next(
                    (
                        r
                        for r in rows
                        if not r.rb_local_deleted
                        and int(r.InMsec or 0) == start
                        and int(r.OutMsec or 0) == end
                        and str(r.Comment or "") == label
                    ),
                    None,
                )
                if row is not None:  # soft delete, as rekordbox does
                    row.rb_local_deleted = 1
                    row.updated_at = now
                    loops_removed += 1
        count = int(str(content.CueUpdated or "0").strip() or 0)
        content.CueUpdated = str(count + 1)
        content.updated_at = now
    return added, moved, removed, loops_added, loops_removed


def _others(db: Any, skip: set[str]) -> dict[str, list[tuple[int, int, str, int]]]:
    """Every cue of every track not in `skip`: for checking that nothing else moved."""
    from pyrekordbox.masterdb import models

    found: dict[str, list[tuple[int, int, str, int]]] = {}
    for r in db.session.query(models.DjmdCue).all():
        if str(r.ContentID) in skip:
            continue
        found.setdefault(str(r.ContentID), []).append(
            (r.InMsec or 0, r.Kind or 0, str(r.Comment or ""), int(r.rb_local_deleted))
        )
    return {k: sorted(v) for k, v in found.items()}


KEEP_BACKUPS = 20  # kept backups (`master-*` folders) before the oldest are deleted


def _new_backup_dir(backup_dir: Path) -> Path:
    """A new, empty `master-<time>` folder (a second write in the same second gets a number)."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):
        path = backup_dir / (f"master-{stamp}" if n == 1 else f"master-{stamp}-{n}")
        try:
            path.mkdir()
        except FileExistsError:
            continue
        return path
    raise WriteError(f"could not make a backup folder in {backup_dir}")


def prune_backups(backup_dir: Path, keep: int = KEEP_BACKUPS) -> None:
    """Delete the oldest `master-*` folders beyond `keep` (only folders of that name: nothing else is touched)."""
    old = sorted(
        (p for p in backup_dir.glob("master-*") if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
    )
    for path in old[: max(0, len(old) - keep)]:
        shutil.rmtree(path, ignore_errors=True)


def restore(backup: Path, master_db: Path) -> None:
    """Put a backup's files back over `master_db` (and remove sidecars the backup didn't have)."""
    for suffix in ("-wal", "-shm"):
        master_db.with_name(master_db.name + suffix).unlink(missing_ok=True)
    for item in backup.iterdir():
        if item.is_file():
            shutil.copy2(item, master_db.with_name(item.name))


def _verify(
    master_db: Path,
    plan: WritePlan,
    expect_counts: tuple[int, int],
    before_others: Mapping[str, list[tuple[int, int, str, int]]],
    unlock: bool,
) -> None:
    """Read the written database back from a fresh snapshot; raises `WriteError` if it isn't right."""
    from sqlalchemy import text

    with tempfile.TemporaryDirectory(prefix="dubcrate-rbw-") as tmp:
        copy = _snapshot(master_db, Path(tmp))
        db = _open(copy, unlock)
        try:
            verdict = [
                tuple(r) for r in db.session.execute(text("PRAGMA integrity_check"))
            ]
            if verdict != [("ok",)]:
                raise WriteError(f"the database failed its integrity check: {verdict}")
            if _counts_in(db) != expect_counts:
                raise WriteError(
                    "the number of tracks or cue rows is not what it should be"
                )
            for change in plan.changes:
                got = _listed(_memory_rows(db, change.content_id))
                if got != change.after:
                    raise WriteError(
                        f"{change.title or change.path}: the cues read back as {got}, "
                        f"not {change.after}"
                    )
                loops = _listed_loops(_loop_rows(db, change.content_id))
                if loops != change.after_loops:
                    raise WriteError(
                        f"{change.title or change.path}: the loops read back as {loops}, "
                        f"not {change.after_loops}"
                    )
            touched = {c.content_id for c in plan.changes}
            if _others(db, touched) != before_others:
                raise WriteError("the cues of a track that was not edited have changed")
        finally:
            db.close()


def apply_write(
    master_db: Path,
    edits: Mapping[str, Sequence[tuple[int, str]]],
    backup_dir: Path,
    *,
    loops: Mapping[str, Sequence[tuple[int, int, str]]] | None = None,
    remove_loops: Mapping[str, Sequence[tuple[int, int]]] | None = None,
    unlock: bool = True,
    running: Callable[[], bool] = rekordbox_running,
    keep_backup: bool = True,
) -> WriteOutcome:
    """Write `edits` (memory cues by `path_key`) and add `loops` (memory loops by `path_key`: (start ms, end ms,
    label), only ever added) into `master_db`: back it up, write, read back, undo on failure.

    Raises `WriteError` with the library as it was (the message says so) if anything goes wrong; a write that
    is refused up front touches nothing. The backup is always made first (the read-back's undo needs it);
    `keep_backup=False` deletes it once the write has been read back (the app keeps one a session, not one a
    cue), and a kept one prunes the oldest beyond `KEEP_BACKUPS`."""
    if running():
        raise WriteError("rekordbox is running: close it first, then try again")
    plan = plan_write(
        master_db, edits, loops=loops, remove_loops=remove_loops, unlock=unlock
    )
    if plan.empty:
        return WriteOutcome(plan, None)
    backup = _new_backup_dir(backup_dir)
    try:
        _snapshot(master_db, backup)
    except OSError as exc:
        raise WriteError(f"could not copy the database aside first: {exc}") from exc
    if (backup / master_db.name).stat().st_size != master_db.stat().st_size:
        raise WriteError("the copy of the database is not the size of the original")
    log.info("rekordbox write: backed up %s to %s", master_db, backup)

    try:
        db = _open(master_db, unlock)
        try:
            check_schema(db)
            plan, targets = _resolve(
                db, edits, loops, remove_loops
            )  # again, in the session that writes
            touched = {c.content_id for c in plan.changes}
            before_others = _others(db, touched)
            rows_before = _counts_in(db)
            added, moved, removed, loops_added, loops_removed = _write_changes(
                db, targets, plan
            )
            db.commit(autoinc=True)
        except BaseException:
            try:
                db.session.rollback()
            finally:
                db.close()
            raise
        db.close()
        _verify(
            master_db,
            plan,
            (rows_before[0], rows_before[1] + added + loops_added),
            before_others,
            unlock,
        )
    except Exception as exc:
        log.exception("rekordbox write failed; restoring %s", backup)
        try:
            restore(backup, master_db)
        except OSError as undo:
            raise WriteError(
                f"{exc}. Putting the copy back failed too ({undo}): copy the files in "
                f"{backup} over {master_db.parent} yourself"
            ) from exc
        raise WriteError(
            f"{exc}. The library was put back as it was (copy kept in {backup})"
        ) from exc
    if keep_backup:
        prune_backups(backup_dir)
    else:
        shutil.rmtree(backup, ignore_errors=True)
    log.info(
        "rekordbox write: %d track(s), +%d ~%d -%d cue(s), +%d -%d loop(s)",
        len(plan.changes),
        added,
        moved,
        removed,
        loops_added,
        loops_removed,
    )
    return WriteOutcome(
        plan,
        backup if keep_backup else None,
        written=[c.key for c in plan.changes],
        cues_added=added,
        cues_moved=moved,
        cues_removed=removed,
        loops_added=loops_added,
        loops_removed=loops_removed,
    )


def _counts_in(db: Any) -> tuple[int, int]:
    from pyrekordbox.masterdb import models

    return (
        db.session.query(models.DjmdContent).count(),
        db.session.query(models.DjmdCue).count(),
    )


def default_database() -> Path:
    path = default_master_db()
    if path is None:
        raise WriteError("no rekordbox master.db found")
    return path
