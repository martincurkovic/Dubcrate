"""Write a sync onto the CDJ-1000MK3's card: plan it fresh, copy the card's files aside, write (Qt-free).

The one place that writes a plan: the Sync tab and `dubcrate card sync --yes` both come here, so
they can't differ. The order is the safety: **the card is read again and planned just now** (the
list on screen may be stale), **its files are copied aside before anything is written**, and
`cdj_card.write_card` writes each file through a temporary name, the index last, and reads
everything back and compares. Cues already on the card, its hot cues and its waveforms are kept
(`card_sync.merge_cues`).

**No file written this way has been loaded by a player yet** (`dev_docs/card-sync.md`, Phase 0).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from dubcrate import cdj_card as cc
from dubcrate.card_sync import CardPlan, DiscRequest, plan_card
from dubcrate.library.paths import default_library_path


def default_backup_dir() -> Path:
    """Where a card's files are copied before a write: `card-backups`, next to the library."""
    return default_library_path().parent / "card-backups"


@dataclass
class SyncOutcome:
    plan: CardPlan
    backup: (
        Path | None
    )  # the copy of the card's files; None when there was nothing to write
    written: list[str]  # the disc files written (the index is written too, after them)

    @property
    def wrote(self) -> bool:
        return bool(self.written)


def apply_sync(
    card: Path, requests: Sequence[DiscRequest], backup_dir: Path | None = None
) -> SyncOutcome:
    """Sync `requests` onto `card`. Raises `cdj_card.CardFormatError` (a plain message) for anything
    a user can cause: not a card, a card taken out, a card that can't be written."""
    try:
        index, files = cc.read_card(card)
        plan = plan_card(index, files, requests)
        if not plan.changes:
            return SyncOutcome(plan, None, [])
        backup = cc.backup_card(card, backup_dir or default_backup_dir())
        writes = plan.writes()
        cc.write_card(card, plan.index, writes)
    except OSError as exc:  # (a card pulled out part way, a read-only card, a full one)
        raise cc.CardFormatError(
            f"The card couldn't be read or written: {exc}"
        ) from exc
    return SyncOutcome(plan, backup, [f"{name}.PDJ" for name in sorted(writes)])
