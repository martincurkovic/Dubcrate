"""After an audio burn: does the disc's table of contents match the plan? Then eject it (Qt-free).

A disc's ID on the CDJ's card is its TOC, and the Sync tab computes it from the planned track lengths
(`cdj_card.toc_from_lengths`), which had never been compared with a burned disc. So the burn leaves
the disc in the drive (`BurnOptions.eject=False`), asks the drive for the disc's real TOC
(`drive_toc.read_drive_toc`), says whether the two agree, and then opens the tray itself.

**This must never turn a good burn into a failure**: every problem is said in the log and carried on
from, and the disc is ejected whatever happened. A Mac only (`drive_toc.can_read_toc`).
The DiscRecording burner says so explicitly (`DRBurnCompletionActionMount` instead of eject: it ejects when not told
otherwise), which also lets macOS mount the audio disc, so **Music may offer to open it** if the system is set to.
**Not run on a real burn yet**: the first one will say whether gapless discs match the plan.
"""

import logging
import time
from collections.abc import Callable, Sequence

from dubcrate import cdj_card as cc
from dubcrate.card_discs import toc_differences
from dubcrate.drive_toc import DriveTocError, read_drive_toc
from dubcrate.eject import EjectError, eject_disc

log = logging.getLogger(__name__)

ATTEMPTS = 8  # a disc that has just been burned takes a while to be readable
WAIT_SECONDS = 3.0


def check_burn(
    track_sectors: Sequence[int],
    pregap_sectors: int,
    say: Callable[[str], None],
    *,
    read: Callable[..., tuple[list[int], int]] = read_drive_toc,
    eject: Callable[[], None] = eject_disc,
    sleep: Callable[[float], None] = time.sleep,
) -> bool | None:
    """Compare the burned disc's TOC with the plan, say what was found, and eject the disc.

    `track_sectors` are the burned tracks' lengths and `pregap_sectors` the silence put before
    tracks 2 and up. Returns True if they agree, False if not, None if it couldn't be checked.
    Never raises."""
    verdict: bool | None = None
    try:
        planned = cc.toc_from_lengths(track_sectors, pregap_sectors)
        actual = read(sleep=sleep, attempts=ATTEMPTS, wait=WAIT_SECONDS)
        differences = toc_differences(planned, actual)
        if differences:
            verdict = False
            log.warning("burned disc's TOC differs from the plan: %s", differences)
            say(
                "The disc's table of contents is not what was planned ("
                + "; ".join(differences[:3])
                + (", ..." if len(differences) > 3 else "")
                + "). The disc itself is fine. It matters only for syncing cues to the CDJ's "
                "card, which would not recognise this disc from the plan: use the command line "
                "(`dubcrate card sync … --from-drive`) with the disc in the drive, and please "
                "report this."
            )
        else:
            verdict = True
            say(
                "Checked the disc's table of contents: it is exactly as planned, so syncing "
                "cues to the CDJ's card will recognise it."
            )
    except DriveTocError as exc:
        log.info("couldn't check the burned disc's TOC: %s", exc)
        say(f"Couldn't check the disc's table of contents: {exc}")
    except Exception:
        log.exception("checking the burned disc's TOC failed")
        say("Couldn't check the disc's table of contents (see the log).")
    try:
        eject()
    except EjectError as exc:
        say(f"{exc} Eject the disc yourself.")
    except Exception:
        log.exception("ejecting the burned disc failed")
        say("Couldn't eject the disc: eject it yourself.")
    return verdict
