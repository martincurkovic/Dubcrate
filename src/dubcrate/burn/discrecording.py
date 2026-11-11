"""macOS burn backend using Apple's DiscRecording framework (via PyObjC).

This is the same engine the Finder and the Burn app use. Audio goes down in
session-at-once mode (the CD equivalent of disc-at-once) so CD-TEXT can be
written and there are no gaps between tracks.
"""

import sys

if sys.platform != "darwin":  # pragma: no cover
    raise ImportError("DiscRecording is only available on macOS")

import DiscRecording as DR
from Foundation import NSDate, NSRunLoop

from dubcrate.burn.base import BurnError, BurnOptions, Disc, MediaInfo, Progress
from dubcrate.cd import cdtext_clean

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
    its mandatory lead-in, so we only override the rest. The pregap is marked
    required so the burn fails, rather than silently falling back to 2 s, if
    the drive can't honour it.
    """
    tracks = []
    for index, t in enumerate(disc.tracks):
        track = DR.DRTrack.trackForAudioFile_(str(t.wav.resolve()))
        if track is None:
            raise BurnError(f"DiscRecording could not open {t.wav}")
        if index > 0:
            props = dict(track.properties())
            props[DR.DRPreGapLengthKey] = pregap_sectors
            props[DR.DRPreGapIsRequiredKey] = True
            track.setProperties_(props)
        tracks.append(track)
    return tracks


class DiscRecordingBurner:
    name = "discrecording"

    def _pick_device(self, wanted: str | None):
        devices = list_devices()
        if not devices:
            raise BurnError("DiscRecording found no optical drives")
        if wanted is None:
            return devices[0]
        for dev in devices:
            if wanted in (dev.bsdName(), dev.displayName()):
                return dev
        raise BurnError(f"no drive matching {wanted!r}")

    def media_info(self, device: str | None) -> MediaInfo:
        """The disc in the drive, as DiscRecording reports it."""
        summary = describe_device(self._pick_device(device))
        free = summary["media_free_blocks"]
        blank = summary["media_blank"]
        return MediaInfo(
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

        burn = DR.DRBurn.burnForDevice_(device)
        speed = DR.DRDeviceBurnSpeedMax
        if options.speed:
            speed = DR.DRDeviceBurnSpeedCD1x * options.speed
        props = {
            DR.DRBurnRequestedSpeedKey: speed,
            DR.DRBurnStrategyKey: DR.DRBurnStrategyCDSAO,
            DR.DRBurnStrategyIsRequiredKey: True,
            DR.DRBurnUnderrunProtectionKey: True,
            DR.DRBurnVerifyDiscKey: options.verify,
            DR.DRBurnTestingKey: options.simulate,
            DR.DRBurnAppendableKey: False,
            DR.DRCDTextKey: build_cdtext(disc),
        }
        if options.eject:
            props[DR.DRBurnCompletionActionKey] = DR.DRBurnCompletionActionEject
        burn.setProperties_(props)
        burn.writeLayout_(build_tracks(disc, options.pregap_sectors))

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
