"""The "already used" notice shown before tracks are added to a disc.

Three answers: add them all anyway, skip the ones already used (when some are new), or cancel.
"""

import logging
from enum import Enum

from PySide6.QtWidgets import QMessageBox, QWidget

from dubcrate.library.models import Track, TrackUsage

log = logging.getLogger(__name__)

MAX_LISTED = 8


def _where(usage: TrackUsage, disc_id: int | None) -> str:
    parts: list[str] = []
    if disc_id is not None and any(d.id == disc_id for d in usage.discs):
        parts.append("already on this disc")
    others = [
        f"“{d.name}”" + (f" (burned {d.burned_at[:10]})" if d.burned_at else "")
        for d in usage.discs
        if d.id != disc_id
    ]
    if others:
        parts.append("on " + ", ".join(others))
    return "; ".join(parts)


class OverlapChoice(Enum):
    ADD_ALL = "add all"  # add them all, the ones already used too
    SKIP = "skip"  # add only the ones not used yet
    CANCEL = "cancel"  # add nothing


def overlap_message(
    tracks: list[Track], usage: dict[int, TrackUsage], disc_id: int | None
) -> str | None:
    """Describe which of `tracks` are already on a disc; None if none are.

    It ends by asking: "Add them anyway, or skip the duplicates?" when some of the tracks are new
    (so there is something to skip to), else "Add anyway?"."""
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
    if len(used) < len(tracks):
        lines += ["", "Add them anyway, or skip the duplicates?"]
    else:
        lines += ["", "Add anyway?"]
    return "\n".join(lines)


def ask_overlap(parent: QWidget | None, text: str, can_skip: bool) -> OverlapChoice:
    """Show the notice with its buttons: Add Anyway, Skip Duplicates (only if `can_skip`: some
    tracks are new) and Cancel. Escape is Cancel; Return is Add Anyway, as it was when this was
    a Yes/No question."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Already used")
    box.setText(text)
    add = box.addButton("Add Anyway", QMessageBox.ButtonRole.AcceptRole)
    skip = (
        box.addButton("Skip Duplicates", QMessageBox.ButtonRole.ActionRole)
        if can_skip
        else None
    )
    cancel = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(add)
    box.setEscapeButton(cancel)
    box.exec()
    clicked = box.clickedButton()
    choice = (
        OverlapChoice.ADD_ALL
        if clicked is add
        else OverlapChoice.SKIP
        if clicked is skip
        else OverlapChoice.CANCEL
    )
    log.info(
        "dialog [overlap] -> %s", choice.value
    )  # (the dialog log only sees static boxes)
    return choice
