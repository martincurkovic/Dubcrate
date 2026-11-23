"""A CDJ card (or one of its files) in words, for `dubcrate card show` and the dump spike (Qt-free)."""

from collections.abc import Mapping

from dubcrate import cdj_card as cc
from dubcrate.card_sync import DiscPlan, Status


def msf(position: int) -> str:
    """Absolute frames as a TOC prints them: `77:55.58`."""
    seconds, frame = divmod(position, cc.FRAMES_PER_SECOND)
    return f"{seconds // 60}:{seconds % 60:02d}.{frame:02d}"


def into_track(d: cc.DiscId, track: int, position: int) -> str:
    """The time into a track, where the ID gives its start (track 1, track 2, the last)."""
    starts = {1: cc.LEAD_IN_FRAMES, 2: d.second_start, d.track_count: d.last_start}
    if track not in starts:
        return ""
    return f" ({msf(position - starts[track])} into the track)"


def describe_id(d: cc.DiscId) -> str:
    return (
        f"{d.track_count} tracks, flags {d.flags:#06x}, lead-out {msf(d.lead_out)}, "
        f"track 2 at {msf(d.second_start)}, last track at {msf(d.last_start)}"
    )


def describe_track_file(f: cc.TrackFile, *, waveforms: bool = True) -> list[str]:
    """Every field of a disc's file, one line each; `waveforms` adds the amplitudes."""
    out = [f"info {f.info}", f"disc  {describe_id(f.disc_id)}", f"cues  {len(f.cues)}"]
    for c in f.cues:
        what = (
            "loop"
            if c.is_loop
            else "cue "
            if c.kind == cc.KIND_CUE
            else f"kind {c.kind:#x}"
        )
        end = f" to {msf(c.end)}" if c.end else ""
        flag = f"  flag {c.flag:#x}" if c.flag else ""
        into = into_track(f.disc_id, c.track, c.position)
        out.append(
            f"  #{c.index} {what} track {c.track} at {msf(c.position)}{into}{end}{flag}"
        )
    for n, h in enumerate(f.hot_cues):
        if h is None:
            out.append(f"hot {'ABC'[n]}  -")
        else:
            out.append(
                f"hot {'ABC'[n]}  slot {h.slot} track {h.track} at {msf(h.position)}"
                f"{into_track(f.disc_id, h.track, h.position)}"
                f"  kind {h.kind:#x} end {h.end} extra {h.extra} tail {h.tail}"
            )
    for tag, records in (("W001", f.coarse), ("W002", f.fine)):
        for r in records:
            filled = sum(1 for b in r.data if b)
            out.append(
                f"{tag} track {r.track} reserved {r.reserved.hex()} ({filled}/50 bytes set)"
            )
            if waveforms:
                levels = (
                    r.data.hex()
                    if tag == "W001"
                    else "".join(f"{n:x}" for n in r.nibbles())
                )
                out.append(f"  {levels}")
    out.append(f"pad   {f.pad.hex()}")
    return out


def describe_index(index: cc.IndexFile) -> list[str]:
    out = [f"index version {index.version}, {len(index.entries)} discs"]
    out += [f"  {e.name}  {describe_id(e.disc_id)}" for e in index.entries]
    return out


def describe_card(
    index: cc.IndexFile,
    files: Mapping[str, cc.TrackFile | None],
    *,
    waveforms: bool = True,
) -> list[str]:
    out = describe_index(index)
    for entry in index.entries:
        out.append(f"== {entry.file_name}")
        f = files.get(entry.name)
        out.extend(
            ["  (no file yet)"]
            if f is None
            else describe_track_file(f, waveforms=waveforms)
        )
    return out


def track_list(tracks: list[int]) -> str:
    """Track numbers as a person writes them: `1-4, 7, 9-10`."""
    runs: list[tuple[int, int]] = []
    for n in sorted(tracks):
        if runs and n == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], n)
        else:
            runs.append((n, n))
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def describe_plan(plan: DiscPlan) -> list[str]:
    """What a sync will do for one disc, in words: the status, the cues and waveforms added, and what stays."""
    where = f"card file {plan.name}" if plan.name else "the card"
    out = [f"{plan.status.value} ({where})"]
    for cue in plan.added:
        kind = "loop" if cue.is_loop else "cue "
        end = f" to {msf(cue.end)}" if cue.end else ""
        into = into_track(plan.disc_id, cue.track, cue.position)
        out.append(f"  + {kind} track {cue.track} at {msf(cue.position)}{into}{end}")
    for old, new in plan.moved:
        kind = "loop" if new.is_loop else "cue "
        into = into_track(plan.disc_id, new.track, new.position)
        out.append(
            f"  ~ {kind} track {new.track} moved from {msf(old.position)} to {msf(new.position)}{into}"
        )
    if plan.status is not Status.NOTHING:
        out.append(
            f"  {plan.kept} already on the card kept, {plan.already_there} wanted "
            f"cues were there already, {len(plan.overflow)} didn't fit"
        )
    if plan.waves_added:
        n = len(plan.waves_added)
        out.append(
            f"  + waveform{'s' if n != 1 else ''} for track{'s' if n != 1 else ''} "
            f"{track_list(plan.waves_added)}"
        )
    if plan.waves_kept:
        n = len(plan.waves_kept)
        out.append(
            f"  {n} track{'s' if n != 1 else ''} already {'have' if n != 1 else 'has'} a waveform "
            f"on the card ({track_list(plan.waves_kept)}): left alone"
        )
    if plan.waves_pending:
        n = len(plan.waves_pending)
        out.append(
            f"  {n} track{'s' if n != 1 else ''} still to be analysed for a waveform "
            f"({track_list(plan.waves_pending)})"
        )
    for want in plan.overflow:
        out.append(
            f"  ! no room (the player holds {cc.MAX_CUES} per disc): track {want.track} "
            f"at {msf(want.position)}"
        )
    out += [f"  ! {note}" for note in plan.notes]
    return out
