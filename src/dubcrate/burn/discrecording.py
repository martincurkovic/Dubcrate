"""macOS burn backend using Apple's DiscRecording framework (via PyObjC).

This is the same engine the Finder and the Burn app use. Audio goes down in
session-at-once mode (the CD equivalent of disc-at-once) so CD-TEXT can be
written and there are no gaps between tracks.
"""

import logging
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if sys.platform != "darwin":  # pragma: no cover
    raise ImportError("DiscRecording is only available on macOS")

import DiscRecording as DR
from Foundation import NSDate, NSRunLoop

from dubcrate.burn.base import (
    BurnError,
    BurnOptions,
    DataDisc,
    Disc,
    Drive,
    MediaInfo,
    Progress,
)
from dubcrate.cd import cdtext_clean
from dubcrate.iso import IsoError, build_iso, image_layout

log = logging.getLogger(__name__)

_POLL_SECONDS = 0.5


def list_devices() -> list:
    return list(DR.DRDevice.devices())


def describe_device(device) -> dict:
    """Summarise a DRDevice: name, support level, CD capabilities, media state."""
    info = device.info() or {}
    status = device.status() or {}
    caps = info.get(DR.DRDeviceWriteCapabilitiesKey) or {}
    media = status.get(DR.DRDeviceMediaInfoKey) or {}
    return {
        "name": device.displayName(),
        "bsd": device.bsdName(),
        "support": info.get(DR.DRDeviceSupportLevelKey),
        "firmware": info.get(DR.DRDeviceFirmwareRevisionKey),
        "max_speed_kps": status.get(DR.DRDeviceMaximumWriteSpeedKey),
        "can_write_cdr": bool(caps.get(DR.DRDeviceCanWriteCDRKey)),
        "can_write_cdtext": bool(caps.get(DR.DRDeviceCanWriteCDTextKey)),
        "can_write_sao": bool(caps.get(DR.DRDeviceCanWriteCDSAOKey)),
        "can_test_write": bool(caps.get(DR.DRDeviceCanTestWriteCDKey)),
        "media_present": bool(device.mediaIsPresent()),
        "media_type": media.get(DR.DRDeviceMediaTypeKey),
        "media_blank": media.get(DR.DRDeviceMediaIsBlankKey),
        "media_free_blocks": media.get(DR.DRDeviceMediaBlocksFreeKey),
    }


def _pump(seconds: float) -> None:
    """Sleep while servicing the run loop so DiscRecording can deliver status."""
    NSRunLoop.currentRunLoop().runUntilDate_(
        NSDate.dateWithTimeIntervalSinceNow_(seconds)
    )


def build_cdtext(disc: Disc):
    block = DR.DRCDTextBlock.alloc().initWithLanguage_encoding_(
        "en", DR.DRCDTextEncodingISOLatin1Modified
    )
    # Track index 0 holds the disc-level text; tracks are 1-based after that.
    block.setObject_forKey_ofTrack_(cdtext_clean(disc.title), DR.DRCDTextTitleKey, 0)
    block.setObject_forKey_ofTrack_(
        cdtext_clean(disc.performer), DR.DRCDTextPerformerKey, 0
    )
    for index, track in enumerate(disc.tracks, start=1):
        block.setObject_forKey_ofTrack_(
            cdtext_clean(track.title), DR.DRCDTextTitleKey, index
        )
        block.setObject_forKey_ofTrack_(
            cdtext_clean(track.performer), DR.DRCDTextPerformerKey, index
        )
    return block


def build_tracks(disc: Disc, pregap_sectors: int = 0) -> list:
    """Create a DRTrack per disc track, with `pregap_sectors` before tracks 2+.

    DiscRecording defaults to 2 s of silence before every track. Track 1 keeps
    its mandatory lead-in, so we only override the rest. The pregap is NOT marked
    required: the LiteOn eSAU108 refuses a required 0 ("doesn't support the
    requested pregap length", 2026-11-15, found by the first real gapless burn),
    but accepts it asked for politely, and the disc comes out gapless (`drutil toc`:
    tracks exactly their lengths apart).
    """
    tracks = []
    for index, t in enumerate(disc.tracks):
        track = DR.DRTrack.trackForAudioFile_(str(t.wav.resolve()))
        if track is None:
            raise BurnError(f"DiscRecording could not open {t.wav}")
        if index > 0:
            props = dict(track.properties())
            props[DR.DRPreGapLengthKey] = pregap_sectors
            props[DR.DRPreGapIsRequiredKey] = False
            track.setProperties_(props)
        tracks.append(track)
    return tracks


def drutil_burn_command(image: Path, drive: int, options: BurnOptions) -> list[str]:
    """The `drutil` command that burns the disc image at `image` as it is (`drutil` says of a file argument: "valid
    image files only: .dmg, .iso, ...: the contents of the image file are burned"). `drive` is its 1-based number in
    `drutil list`. Every option is spelled as `drutil`'s manual spells it: **it ignores one it doesn't know**, so a
    wrong spelling would burn with the default instead of failing. The disc is closed (`-noappendable`): a CDJ
    won't read an open one. **NOT tried on a real burn yet.**"""
    cmd = ["drutil", "-drive", str(drive), "burn"]
    cmd.append("-test" if options.simulate else "-notest")
    cmd.append("-verify" if options.verify else "-noverify")
    cmd.append("-eject" if options.eject else "-mount")
    cmd.append("-noappendable")
    if options.speed:
        cmd += ["-speed", str(options.speed)]
    cmd.append(str(image))
    return cmd


class _DrutilProgress:
    """`drutil`'s output as progress lines: a line with a percentage is passed on each time it passes a multiple of
    ten (it redraws a bar many times a second), any other line once, as it is."""

    def __init__(self, progress: Progress) -> None:
        self._progress = progress
        self._last_text = ""
        self._last_tenth = -1

    def feed(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        percent = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        if percent:
            tenth = int(float(percent.group(1))) // 10
            if tenth != self._last_tenth:
                self._last_tenth = tenth
                self._progress(f"burning {tenth * 10}%")
            return
        if text != self._last_text:
            self._last_text = text
            self._progress(text)


class DiscRecordingBurner:
    name = "discrecording"

    def _pick_device(self, wanted: str | None):
        devices = list_devices()
        if not devices:
            raise BurnError("No optical drives found")
        if wanted is None:
            return devices[0]
        for dev in devices:
            if wanted in (dev.bsdName(), dev.displayName()):
                return dev
        raise BurnError(f"no drive matching {wanted!r}")

    def drives(self) -> list[Drive]:
        """The optical drives DiscRecording sees: the id is the BSD name (`disk4`), the name the display name."""
        return [
            Drive(str(dev.bsdName()), str(dev.displayName() or dev.bsdName()))
            for dev in list_devices()
        ]

    def media_info(self, device: str | None) -> MediaInfo:
        """The disc in the drive, as DiscRecording reports it."""
        summary = describe_device(self._pick_device(device))
        free = summary["media_free_blocks"]
        blank = summary["media_blank"]
        return MediaInfo(
            drive=summary["name"] or "",
            present=summary["media_present"],
            blank=None if blank is None else bool(blank),
            free_sectors=int(free) if free else None,
        )

    def burn(self, disc: Disc, options: BurnOptions, progress: Progress) -> None:
        device = self._pick_device(options.device)
        summary = describe_device(device)
        progress(f"drive: {summary['name']} (support level: {summary['support']})")
        if not summary["media_present"]:
            raise BurnError("no disc in the drive")
        if not summary["can_write_cdtext"]:
            raise BurnError("drive reports it cannot write CD-TEXT")

        props = {
            DR.DRBurnStrategyKey: DR.DRBurnStrategyCDSAO,
            DR.DRBurnStrategyIsRequiredKey: True,
            DR.DRCDTextKey: build_cdtext(disc),
        }
        self._run(
            device, options, props, build_tracks(disc, options.pregap_sectors), progress
        )

    def burn_data(
        self, disc: DataDisc, options: BurnOptions, progress: Progress
    ) -> None:
        """Write an MP3 CD: Dubcrate's own ISO 9660 + Joliet image of the staging folder, as one data track.

        **The image is ours, not DiscRecording's**: the CDJ's ID for an MP3 disc is made from the image's creation
        date, label, size and where its last file starts, so Dubcrate must know the image to make the card's entry
        for the disc (`build.image_record`, as for a `cdrdao` burn). DiscRecording makes its own filesystem from a
        folder, with dates and a layout we don't control, so the finished image is handed to `drutil burn`, which
        burns an image file as it is. **NOT tried on a real burn yet.**"""
        device = self._pick_device(options.device)
        summary = describe_device(device)
        progress(f"drive: {summary['name']} (support level: {summary['support']})")
        if not summary["media_present"]:
            raise BurnError("no disc in the drive")
        drive = [dev.bsdName() for dev in list_devices()].index(device.bsdName()) + 1
        with tempfile.TemporaryDirectory(prefix="dubcrate-") as tmp:
            progress(f"making the disc image of “{disc.title}”…")
            disc.created = disc.created or datetime.now().astimezone()
            image = Path(tmp) / "disc.iso"
            try:
                build_iso(disc.folder, disc.title, image, disc.created)
            except IsoError as exc:
                raise BurnError(str(exc)) from exc
            try:
                disc.layout = image_layout(image, disc.folder)
            except (
                IsoError
            ) as exc:  # (a folder in a folder: written all the same, just not recorded)
                log.info("the image's layout was not read: %s", exc)
            needs = image.stat().st_size // 2048
            free = summary["media_free_blocks"]
            progress(f"the disc image is {needs} sectors")
            if free and needs > int(free):
                raise BurnError(
                    f"the disc in the drive holds {int(free)} sectors but this one needs {needs}"
                )
            self._burn_image(image, drive, options, progress)

    @staticmethod
    def _burn_image(
        image: Path, drive: int, options: BurnOptions, progress: Progress
    ) -> None:
        """Burn `image` with `drutil` and follow its output. Raises `BurnError` if it fails: a non-zero exit, or a
        line that says the burn failed (what `drutil` does on a failed burn is not known: both are taken)."""
        cmd = drutil_burn_command(image, drive, options)
        progress(f"$ {' '.join(cmd)}")
        # (stdin is closed: nobody is typing if it asks for something)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        seen: list[str] = []
        filtered = _DrutilProgress(progress)
        for line in proc.stdout:  # (text mode: a "\r" ends a line too)
            seen.append(line.strip())
            filtered.feed(line)
        status = proc.wait()
        said = [line for line in seen if line]
        failed = status != 0 or any("fail" in line.lower() for line in said)
        if failed:
            detail = " | ".join(said[-3:]) or f"exit status {status}"
            raise BurnError(f"drutil burn failed: {detail}")

    def _run(self, device, options: BurnOptions, extra: dict, layout, progress) -> None:
        """Configure a burn, start it and follow it to the end."""
        burn = DR.DRBurn.burnForDevice_(device)
        speed = DR.DRDeviceBurnSpeedMax
        if options.speed:
            speed = DR.DRDeviceBurnSpeedCD1x * options.speed
        props = {
            DR.DRBurnRequestedSpeedKey: speed,
            DR.DRBurnUnderrunProtectionKey: True,
            DR.DRBurnVerifyDiscKey: options.verify,
            DR.DRBurnTestingKey: options.simulate,
            DR.DRBurnAppendableKey: False,
            **extra,
        }
        # Said either way: DiscRecording ejects when it is not told otherwise, and a burn that is going to
        # check the disc (`burn_check`) needs it left in the drive.
        props[DR.DRBurnCompletionActionKey] = (
            DR.DRBurnCompletionActionEject
            if options.eject
            else DR.DRBurnCompletionActionMount
        )
        burn.setProperties_(props)
        burn.writeLayout_(layout)

        last = None
        while True:
            _pump(_POLL_SECONDS)
            status = burn.status() or {}
            state = status.get(DR.DRStatusStateKey)
            percent = status.get(DR.DRStatusPercentCompleteKey)
            # DiscRecording reports -1.0 while progress is indeterminate.
            known = percent is not None and float(percent) >= 0
            line = f"{state}" + (f" {float(percent) * 100:5.1f}%" if known else "")
            if line != last:
                progress(line)
                last = line
            if state == DR.DRStatusStateDone:
                return
            if state == DR.DRStatusStateFailed:
                detail = status.get(DR.DRErrorStatusErrorStringKey) or status
                raise BurnError(f"DiscRecording burn failed: {detail}")
