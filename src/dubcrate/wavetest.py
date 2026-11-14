"""A disc of test signals for finding how the CDJ-1000MK3 turns audio into its waveform (Qt-free, numpy only).

The player writes a 50-value (`W001`) and a 100-value (`W002`) record per track as it plays, each value
0 to 15, and the rule that turns audio into those values is not known (`dev_docs/cdj1000mk3-card.md`,
"The waveform"). This module makes the audio that finds it: every track is built from **segments of
known signals** (a staircase of levels, equal-RMS shapes, bursts, ...) so that the values the player
records can be compared with what was in each column. `dev_docs/wave-test-disc.md` says how to burn and
play it; `wavefit.py` reads the result.

The audio is **deterministic** and described by a plain-data manifest (`manifest`, `tracks_from_manifest`),
so the fitter regenerates the exact samples that were burned instead of trusting nominal levels. Every
track is a whole number of sectors long (a multiple of 588 samples), so the disc's tracks begin and end
exactly where the TOC says.
"""

import json
import math
import re
import subprocess
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

RATE = 44_100
SECTOR = 588  # samples (a CD sector)
FULL = 32767.0
COARSE = 50  # columns in a track's W001 record
FINE = 100  # and in its W002 record
DISC_TITLE = "Dubcrate wave test"

Params = dict[str, Any]


@dataclass(frozen=True)
class Segment:
    """A stretch of a track: where it is (seconds), what signal it holds and how loud."""

    start: float
    end: float
    kind: str  # silence, dither, sine, square, triangle, noise, pink, impulses, bursts, clicks, ramp_*
    params: Params = field(default_factory=dict)
    label: str = ""

    @property
    def samples(self) -> tuple[int, int]:
        return round(self.start * RATE), round(self.end * RATE)


@dataclass(frozen=True)
class TrackSpec:
    number: int
    title: str
    seconds: float
    note: str  # what this track is for, in a line
    segments: tuple[Segment, ...]
    seed: int = 0

    @property
    def samples(self) -> int:
        return round(self.seconds * RATE)

    @property
    def sectors(self) -> int:
        return self.samples // SECTOR


def db(x: float) -> float:
    return 10.0 ** (x / 20.0)


# -- the signals -------------------------------------------------------------------------------------


def _stereo(
    sig: np.ndarray, params: Params, rng: np.random.Generator | None = None
) -> np.ndarray:
    """Put a mono signal (peak or RMS already set) on the two channels: `gains` = [left dB, right dB],
    None for a silent channel; `invert_r` flips the right channel. `independent` draws a second signal."""
    gains = params.get("gains", [0.0, 0.0])
    out = np.zeros((len(sig), 2))
    for ch, gain in enumerate(gains):
        if gain is not None:
            out[:, ch] = sig * db(gain)
    if params.get("invert_r"):
        out[:, 1] = -out[:, 1]
    return out


def _pink(n: int, rng: np.random.Generator) -> np.ndarray:
    """Pink noise (power falling 3 dB an octave), scaled to unit RMS."""
    spectrum = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1.0 / RATE)
    freqs[0] = freqs[1]
    out = np.fft.irfft(spectrum / np.sqrt(freqs), n)
    return out / np.sqrt(np.mean(out**2))


def synth(seg: Segment, rng: np.random.Generator) -> np.ndarray:
    """A segment's audio as floats in units of full scale, shape (n, 2). Unrounded and undithered."""
    a, b = seg.samples
    n = b - a
    t = np.arange(n) / RATE
    p = seg.params
    kind = seg.kind
    if kind in ("silence", "clicks"):
        out = np.zeros((n, 2))
        for at in p.get(
            "at", []
        ):  # single-sample impulses, `at` seconds into the segment
            out[round(at * RATE), :] = db(p.get("peak_db", 0.0))
        return out
    if kind == "dither":
        return np.zeros((n, 2))
    peak = db(p.get("peak_db", 0.0))
    if kind == "sine":
        return _stereo(peak * np.sin(2 * math.pi * p["freq"] * t), p)
    if kind == "square":
        return _stereo(
            peak * np.where(np.sin(2 * math.pi * p["freq"] * t) >= 0, 1.0, -1.0), p
        )
    if kind == "triangle":
        return _stereo(
            peak * (2 / math.pi) * np.arcsin(np.sin(2 * math.pi * p["freq"] * t)), p
        )
    if kind == "noise":
        rms = db(p["rms_db"])
        if p.get("independent"):
            out = rms * rng.standard_normal((n, 2))
            gl, gr = p.get("gains", [0.0, 0.0])
            out[:, 0] *= db(gl) if gl is not None else 0.0
            out[:, 1] *= db(gr) if gr is not None else 0.0
            return np.clip(out, -1.0, 1.0)
        return np.clip(_stereo(rms * rng.standard_normal(n), p), -1.0, 1.0)
    if kind == "pink":
        return np.clip(_stereo(db(p["rms_db"]) * _pink(n, rng), p), -1.0, 1.0)
    if (
        kind == "impulses"
    ):  # a train of rectangular pulses: `period` s apart, `width` s wide
        sig = np.where((t % p["period"]) < p["width"], 1.0, 0.0)
        return _stereo(peak * sig, p)
    if (
        kind == "bursts"
    ):  # a sine gated on for `duty` of every `period`, with short raised-cosine edges
        gate = np.where((t % p["period"]) < p["period"] * p.get("duty", 0.5), 1.0, 0.0)
        edge = max(1, round(p.get("edge_ms", 1.0) * RATE / 1000))
        gate = np.convolve(
            gate, np.hanning(2 * edge + 1) / np.hanning(2 * edge + 1).sum(), mode="same"
        )
        return _stereo(peak * gate * np.sin(2 * math.pi * p["freq"] * t), p)
    if (
        kind == "ramp_sine"
    ):  # peak level moving in dB (linear in time) between two levels
        level = db(p["from_db"] + (p["to_db"] - p["from_db"]) * t / (n / RATE))
        phase = 2 * math.pi * p["freq"] * t
        return _stereo(level * np.sin(phase), p)
    if kind == "ramp_lin":  # peak amplitude moving linearly (0..1 of full scale)
        level = p["from_lin"] + (p["to_lin"] - p["from_lin"]) * t / (n / RATE)
        return _stereo(level * np.sin(2 * math.pi * p["freq"] * t), p)
    if kind == "ramp_noise":  # gaussian noise whose RMS moves in dB
        level = db(p["from_db"] + (p["to_db"] - p["from_db"]) * t / (n / RATE))
        return np.clip(_stereo(level * rng.standard_normal(n), p), -1.0, 1.0)
    raise ValueError(f"unknown segment kind {kind!r}")


def render(spec: TrackSpec) -> np.ndarray:
    """The track as 16-bit stereo, shape (samples, 2). Tonal and noise segments are TPDF-dithered by one
    least-significant bit (so a very quiet tone is a tone, not distortion); silence is digital zero."""
    audio = np.zeros((spec.samples, 2))
    dithered = np.zeros(spec.samples, dtype=bool)
    for i, seg in enumerate(spec.segments):
        rng = np.random.default_rng([spec.seed, spec.number, i])
        a, b = seg.samples
        audio[a:b] = synth(seg, rng)
        if seg.kind not in ("silence", "clicks"):
            dithered[a:b] = True
    scaled = audio * FULL
    rng = np.random.default_rng([spec.seed, spec.number, 10_000])
    tpdf = rng.random((spec.samples, 2), dtype=np.float32) - rng.random(
        (spec.samples, 2), dtype=np.float32
    )
    scaled += tpdf * dithered[:, None]
    return np.clip(np.rint(scaled), -32768, 32767).astype(np.int16)


# -- the tracks ----------------------------------------------------------------------------------------


def _steps(
    n: int, seconds: float, make: Callable[[int], tuple[str, Params, str]]
) -> tuple[Segment, ...]:
    """`n` segments of `seconds`, the k-th (from 0) described by `make(k)` = (kind, params, label)."""
    out = []
    for k in range(n):
        kind, params, label = make(k)
        out.append(Segment(k * seconds, (k + 1) * seconds, kind, params, label))
    return tuple(out)


def _seq(
    seconds: float, parts: list[tuple[str, Params, str]], total: float
) -> tuple[Segment, ...]:
    """The parts one after another, `seconds` each, and silence to fill the track to `total`."""
    out = list(_steps(len(parts), seconds, lambda k: parts[k]))
    end = len(parts) * seconds
    if end < total:
        out.append(Segment(end, total, "silence", {}, "silence"))
    return tuple(out)


def _peak_for_rms(shape: str, rms_db: float, duty: float = 0.0) -> float:
    """The peak level (dB) a shape needs to have the given RMS (dB)."""
    factor = {"sine": math.sqrt(2), "square": 1.0, "triangle": math.sqrt(3)}.get(shape)
    if factor is None:  # a pulse train of duty `duty`
        factor = 1 / math.sqrt(duty)
    peak = rms_db + 20 * math.log10(factor)
    if peak > 1e-9:
        raise ValueError(
            f"a {shape} at RMS {rms_db} dBFS would need a peak of {peak:.1f} dBFS"
        )
    return peak


def _shape_parts(rms_db: float) -> list[tuple[str, Params, str]]:
    sine = (
        "sine",
        {"freq": 1000.0, "peak_db": _peak_for_rms("sine", rms_db)},
        f"sine, RMS {rms_db:g}",
    )
    square = (
        "square",
        {"freq": 1000.0, "peak_db": _peak_for_rms("square", rms_db)},
        f"square, RMS {rms_db:g}",
    )
    tri = (
        "triangle",
        {"freq": 1000.0, "peak_db": _peak_for_rms("triangle", rms_db)},
        f"triangle, RMS {rms_db:g}",
    )
    noise = ("noise", {"rms_db": rms_db}, f"white noise, RMS {rms_db:g}")
    pink = ("pink", {"rms_db": rms_db}, f"pink noise, RMS {rms_db:g}")
    one = (
        "impulses",
        {
            "period": 0.1,
            "width": 0.001,
            "peak_db": _peak_for_rms("pulse", rms_db, 0.01),
        },
        f"1% pulses, RMS {rms_db:g}",
    )
    five = (
        "impulses",
        {
            "period": 0.1,
            "width": 0.005,
            "peak_db": _peak_for_rms("pulse", rms_db, 0.05),
        },
        f"5% pulses, RMS {rms_db:g}",
    )
    two = (
        "impulses",
        {
            "period": 0.1,
            "width": 0.002,
            "peak_db": _peak_for_rms("pulse", rms_db, 0.02),
        },
        f"2% pulses, RMS {rms_db:g}",
    )
    return [sine, square, tri, noise, pink, one, two, five]


def _db_staircase(
    top_db: float, step_db: float, n: int = 25
) -> Callable[[int], tuple[str, Params, str]]:
    def make(k: int) -> tuple[str, Params, str]:
        level = top_db - (n - 1 - k) * step_db
        return "sine", {"freq": 1000.0, "peak_db": level}, f"{level:g} dBFS peak"

    return make


def build_tracks() -> list[TrackSpec]:
    """The whole disc, in the order to play it (the most useful first)."""
    t100 = 100.0
    stair_pink = _steps(
        25,
        4.0,
        lambda k: (
            "pink",
            {"rms_db": -60 + 2 * k},
            f"pink noise RMS {-60 + 2 * k} dBFS",
        ),
    )
    freqs = [31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 12000, 16000, 20000]
    gains_modes = [
        ([0.0, None], "left only"),
        ([None, 0.0], "right only"),
        ([0.0, 0.0], "both, in phase"),
        ([0.0, 0.0], "both, right inverted"),
        ([0.0, -12.0], "left 0, right -12 dB"),
        ([-12.0, 0.0], "left -12, right 0 dB"),
    ]
    stereo_parts: list[tuple[str, Params, str]] = []
    for gains, label in gains_modes:
        stereo_parts.append(
            (
                "sine",
                {
                    "freq": 1000.0,
                    "peak_db": -12.0,
                    "gains": gains,
                    "invert_r": label.endswith("inverted"),
                },
                f"sine -12 dBFS, {label}",
            )
        )
    stereo_parts.append(
        (
            "noise",
            {"rms_db": -20.0, "independent": True},
            "independent white noise, RMS -20 each",
        )
    )
    stereo_parts.append(
        ("noise", {"rms_db": -20.0}, "the same white noise on both, RMS -20")
    )
    burst_periods = [0.1, 0.2, 0.5, 1.0, 2.0, 4.0]
    low_levels = [None, "dither", -90, -84, -78, -72, -66, -60]
    low_parts: list[tuple[str, Params, str]] = []
    for lv in low_levels:
        if lv is None:
            low_parts.append(("silence", {}, "digital silence"))
        elif lv == "dither":
            low_parts.append(("dither", {}, "dither only"))
        else:
            low_parts.append(
                ("sine", {"freq": 1000.0, "peak_db": float(lv)}, f"sine {lv} dBFS peak")
            )
    low_parts += [
        ("sine", {"freq": 1000.0, "peak_db": float(lv)}, f"sine {lv} dBFS peak")
        for lv in (-57, -54, -51, -48)
    ]

    def burst_part(period: float) -> tuple[str, Params, str]:
        return (
            "bursts",
            {"freq": 1000.0, "peak_db": -6.0, "period": period, "duty": 0.5},
            f"1 kHz bursts -6 dBFS, period {period:g} s, 50% on",
        )

    tracks = [
        TrackSpec(
            1,
            "dB staircase, 1 kHz sine, -72 to 0 dBFS in 3 dB steps",
            t100,
            "the main level scale: 25 steps of 4 s",
            _steps(25, 4.0, _db_staircase(0.0, 3.0)),
        ),
        TrackSpec(
            2,
            "Linear staircase, 1 kHz sine, 4% to 100% of full scale",
            t100,
            "the same on a linear scale: 25 steps of 4 s",
            _steps(
                25,
                4.0,
                lambda k: (
                    "sine",
                    {"freq": 1000.0, "peak_db": 20 * math.log10((k + 1) * 0.04)},
                    f"{(k + 1) * 4}% of full scale peak",
                ),
            ),
        ),
        TrackSpec(
            3,
            "Alignment: bursts of full scale 1 kHz of different lengths, and clicks",
            t100,
            "which columns light up, and how a short burst or a click counts",
            tuple(
                [Segment(0, 100, "silence", {}, "digital silence")]
                + [
                    Segment(
                        s,
                        s + d,
                        "sine",
                        {"freq": 1000.0, "peak_db": 0.0},
                        f"full scale burst {d:g} s at {s:g} s",
                    )
                    for s, d in [
                        (10.0, 2.0),
                        (20.5, 1.0),
                        (30.25, 0.5),
                        (40.1, 0.1),
                        (50.05, 0.02),
                        (60.0, 0.005),
                    ]
                ]
                + [
                    Segment(
                        70.5,
                        70.5 + 0.01,
                        "clicks",
                        {"at": [0.0], "peak_db": 0.0},
                        "one full scale sample at 70.5 s",
                    ),
                    Segment(
                        80.5,
                        80.5 + 0.01,
                        "clicks",
                        {"at": [0.0], "peak_db": 0.0},
                        "one full scale sample at 80.5 s",
                    ),
                ]
            ),
        ),
        TrackSpec(
            4,
            "dB staircase 20 dB quieter, -92 to -20 dBFS",
            t100,
            "is each track scaled to its own loudest part?",
            _steps(25, 4.0, _db_staircase(-20.0, 3.0)),
        ),
        TrackSpec(
            5,
            "Constant quiet tone, -60 dBFS",
            t100,
            "a quiet track all the way: does it reach full height?",
            (
                Segment(
                    0,
                    100,
                    "sine",
                    {"freq": 1000.0, "peak_db": -60.0},
                    "sine -60 dBFS peak",
                ),
            ),
        ),
        TrackSpec(
            6,
            "Equal RMS -20 dBFS: sine, square, triangle, noise, pulses",
            t100,
            "peak, RMS or average? 8 shapes of 12 s",
            _seq(12.0, _shape_parts(-20.0), t100),
        ),
        TrackSpec(
            7,
            "Stereo: left, right, both, inverted, unequal, noise",
            t100,
            "which channel, or the sum? 8 parts of 12 s",
            _seq(12.0, stereo_parts, t100),
        ),
        TrackSpec(
            8,
            "Frequency sweep at -12 dBFS, 31.5 Hz to 20 kHz",
            t100,
            "does the level depend on frequency? 12 parts of 8 s",
            _seq(
                8.0,
                [
                    ("sine", {"freq": f, "peak_db": -12.0}, f"{f:g} Hz at -12 dBFS")
                    for f in freqs
                ],
                t100,
            ),
        ),
        TrackSpec(
            9,
            "Ramp: 1 kHz sine, -80 to 0 dBFS, linear in dB",
            t100,
            "every level in turn, a cross-check that needs no step alignment",
            (
                Segment(
                    0,
                    100,
                    "ramp_sine",
                    {"freq": 1000.0, "from_db": -80.0, "to_db": 0.0},
                    "sine ramping -80 to 0 dBFS",
                ),
            ),
        ),
        TrackSpec(
            10,
            "Equal RMS -32 dBFS: sine, square, triangle, noise, pulses",
            t100,
            "the same shapes 12 dB quieter",
            _seq(12.0, _shape_parts(-32.0), t100),
        ),
        TrackSpec(
            11,
            "Frequency sweep at -30 dBFS, 31.5 Hz to 20 kHz",
            t100,
            "the same, quieter",
            _seq(
                8.0,
                [
                    ("sine", {"freq": f, "peak_db": -30.0}, f"{f:g} Hz at -30 dBFS")
                    for f in freqs
                ],
                t100,
            ),
        ),
        TrackSpec(
            12,
            "Gated bursts: 1 kHz at -6 dBFS, 50% on, every 0.1 to 4 s",
            t100,
            "how long does the level take to rise and fall? 6 parts of 16 s",
            _seq(16.0, [burst_part(p) for p in burst_periods], t100),
        ),
        TrackSpec(
            13,
            "Low levels: silence, dither, 1 kHz from -90 to -48 dBFS",
            t100,
            "the bottom of the scale and the floor",
            _low_levels(low_parts, t100),
        ),
        TrackSpec(
            14,
            "Ramp: 1 kHz sine, 0 to full scale, linear in amplitude",
            t100,
            "the linear counterpart of track 9",
            (
                Segment(
                    0,
                    100,
                    "ramp_lin",
                    {"freq": 1000.0, "from_lin": 0.0, "to_lin": 1.0},
                    "sine ramping 0 to full scale",
                ),
            ),
        ),
        TrackSpec(
            15,
            "Ramp: white noise, RMS -80 to -6 dBFS",
            t100,
            "noise, which has peaks above its RMS",
            (
                Segment(
                    0,
                    100,
                    "ramp_noise",
                    {"from_db": -80.0, "to_db": -6.0},
                    "noise ramping -80 to -6 dBFS RMS",
                ),
            ),
        ),
        TrackSpec(
            16,
            "Pink noise staircase, RMS -60 to -12 dBFS in 2 dB steps",
            t100,
            "music-like level steps",
            stair_pink,
        ),
        TrackSpec(
            17,
            "Constant pink noise, RMS -30 dBFS",
            t100,
            "a steady, music-like signal",
            (Segment(0, 100, "pink", {"rms_db": -30.0}, "pink noise RMS -30 dBFS"),),
        ),
        TrackSpec(
            18,
            "Digital silence",
            t100,
            "the baseline: what an empty track reads",
            (Segment(0, 100, "silence", {}, "digital silence"),),
        ),
        TrackSpec(
            19,
            "dB staircase over 200 s, 8 s steps",
            200.0,
            "does the scale depend on the track's length? (columns of 4 s)",
            _steps(25, 8.0, _db_staircase(0.0, 3.0)),
        ),
        TrackSpec(
            20,
            "dB staircase over 40 s, 1.6 s steps",
            40.0,
            "the same, short (columns of 0.8 s)",
            _steps(25, 1.6, _db_staircase(0.0, 3.0)),
        ),
    ]
    for track in tracks:
        assert track.samples % SECTOR == 0, track.title
        assert all(
            0 <= s.start < s.end <= track.seconds + 1e-9 for s in track.segments
        ), track.title
    return tracks


def _low_levels(
    parts: list[tuple[str, Params, str]], total: float
) -> tuple[Segment, ...]:
    """8 s segments for the first eight parts, then 9 s for the rest (so the track is exactly 100 s)."""
    out, at = [], 0.0
    for i, (kind, params, label) in enumerate(parts):
        length = 8.0 if i < 8 else 9.0
        out.append(Segment(at, at + length, kind, params, label))
        at += length
    assert abs(at - total) < 1e-9, at
    return tuple(out)


# -- the manifest ----------------------------------------------------------------------------------------


def manifest(tracks: list[TrackSpec]) -> dict[str, Any]:
    """Plain data that rebuilds the tracks (and so the exact audio)."""
    return {
        "disc": DISC_TITLE,
        "rate": RATE,
        "tracks": [
            {
                "number": t.number,
                "title": t.title,
                "seconds": t.seconds,
                "sectors": t.sectors,
                "note": t.note,
                "seed": t.seed,
                "segments": [
                    {
                        "start": s.start,
                        "end": s.end,
                        "kind": s.kind,
                        "params": s.params,
                        "label": s.label,
                    }
                    for s in t.segments
                ],
            }
            for t in tracks
        ],
    }


def tracks_from_manifest(data: dict[str, Any]) -> list[TrackSpec]:
    return [
        TrackSpec(
            t["number"],
            t["title"],
            t["seconds"],
            t["note"],
            tuple(
                Segment(s["start"], s["end"], s["kind"], s["params"], s["label"])
                for s in t["segments"]
            ),
            t.get("seed", 0),
        )
        for t in data["tracks"]
    ]


def write_wav(path: Path, audio: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.ascontiguousarray(audio, dtype="<i2").tobytes())


README = """Dubcrate wave test disc: a disc of test signals for finding the CDJ-1000MK3's waveform scale.

1. Import this folder into Dubcrate (File > Import Folder...), make a disc of all the tracks in
   order, and burn it. In the burn window leave **Normalise OFF** (it would change the levels this
   disc is made of). A CD-R or a CD-RW will do.
2. Put the SD card in the player, load this disc and PLAY EVERY TRACK ALL THE WAY THROUGH at normal
   speed (the player records the waveform as it plays; skipping or searching leaves columns empty).
   Do not touch the jog wheel or pitch. The tracks are in order of importance: if you must stop,
   the first ones matter most. The whole disc is 34 minutes.
3. Eject the disc, take the card out (eject it from the Mac first), and copy its PIONEER folder.
4. uv run spikes/wave_test_disc.py fit manifest.json PATH_TO_THE_CARD_OR_ITS_COPY
   and send the report back.
"""


def _file_name(track: TrackSpec) -> str:
    """`01 dB staircase ... minus 72 to 0 dBFS ....flac`: the signs and decimal points kept as words."""
    title = re.sub(r"(?<=\d)\.(?=\d)", "_", re.sub(r"-(?=\d)", "minus ", track.title))
    words = re.sub(r"[^A-Za-z0-9_]+", " ", title).strip()
    return f"{track.number:02d} {words[:60].strip()}.flac"


def decode(path: Path, ffmpeg: Path) -> np.ndarray:
    """A file as 16-bit stereo at 44.1 kHz, shape (samples, 2)."""
    out = subprocess.run(
        [
            str(ffmpeg),
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "s16le",
            "-ac",
            "2",
            "-ar",
            str(RATE),
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(out, dtype="<i2").reshape(-1, 2)


def write_disc(
    out: Path, tracks: list[TrackSpec], ffmpeg: Path, say=print
) -> list[Path]:
    """Write the disc's tracks as tagged FLAC (lossless), each checked by decoding it again and
    comparing every sample, with the manifest and a README beside them. Refuses a folder that has
    files in it."""
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise FileExistsError(f"{out} is not empty")
    written = []
    for track in tracks:
        audio = render(track)
        wav = out / f".{track.number:02d}.wav"
        write_wav(wav, audio)
        flac = out / _file_name(track)
        subprocess.run(
            [
                str(ffmpeg),
                "-y",
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(wav),
                "-c:a",
                "flac",
                "-metadata",
                f"title={track.title}",
                "-metadata",
                f"artist={DISC_TITLE}",
                "-metadata",
                f"album={DISC_TITLE}",
                "-metadata",
                f"track={track.number}/{len(tracks)}",
                str(flac),
            ],
            check=True,
        )
        wav.unlink()
        if not np.array_equal(decode(flac, ffmpeg), audio):
            raise RuntimeError(f"{flac.name} does not decode to what was written")
        written.append(flac)
        say(f"{flac.name}: {track.seconds:g} s, checked")
    (out / "manifest.json").write_text(
        json.dumps(manifest(tracks), indent=1), encoding="utf-8"
    )
    (out / "README.txt").write_text(README, encoding="utf-8")
    return written
