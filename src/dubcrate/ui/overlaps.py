"""The "already used" notice shown before tracks are added to a disc."""

from dubcrate.library.models import Track, TrackUsage

MAX_LISTED = 8


def _where(usage: TrackUsage, disc_id: int | None) -> str:
    parts: list[str] = []
    if disc_id is not None and any(d.id == disc_id for d in usage.discs):
        parts.append("already on this disc")
    others = [f"“{d.name}”" for d in usage.discs if d.id != disc_id]
    if others:
        parts.append("planned on " + ", ".join(others))
    if usage.burns:
        parts.append(
            "burned on "
            + ", ".join(f"“{b.name}” ({b.burned_at[:10]})" for b in usage.burns)
        )
    return "; ".join(parts)


def overlap_message(
    tracks: list[Track], usage: dict[int, TrackUsage], disc_id: int | None
) -> str | None:
    """Describe which of `tracks` are already on a disc; None if none are."""
    used = [t for t in tracks if t.id in usage]
    if not used:
        return None
    count = (
        f"{len(used)} of the {len(tracks)} tracks are"
        if len(tracks) > 1
        else "This track is"
    )
    lines = [f"{count} already used:", ""]
    for t in used[:MAX_LISTED]:
        name = f"{t.artist} - {t.title}" if t.artist else t.title
        lines.append(f"• {name}: {_where(usage[t.id], disc_id)}")
    if len(used) > MAX_LISTED:
        lines.append(f"…and {len(used) - MAX_LISTED} more")
    lines += ["", "Add anyway?"]
    return "\n".join(lines)
