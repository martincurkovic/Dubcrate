"""Made-up music for the screenshots (`scripts/make_screenshots.py`): a small DJ collection that exists nowhere else.

Every track is a short synthesised house loop (kick, clap, hats, bass, pad, arpeggio, with an intro, drops and a
breakdown, so its waveform has some shape) encoded to MP3 with invented titles and artists and real tags (title,
artist, album, genre, BPM, key). `build_demo_music(root)` writes them into playlist folders under `root` and
returns each file's whole-track waveform, for the track list's Preview column.

Nothing here touches the owner's music, library or settings: it only writes where it is told to, and the same
seed gives the same collection every run (titles, tempos, lengths; the audio is deterministic too).
"""

import random
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SR = 44_100

ARTISTS = [
    "Nova Static", "Harbour Lights", "Midnight Parade", "Velvet Circuit", "Kiko & The Tides",
    "Aurora Dock", "Paper Satellites", "Neon Alley", "Lunar Mechanic", "Sunday Drive",
    "Cobalt Rooms", "Mara Vale", "Dex Orion", "Fuller & Pike", "Jonah Reyes", "Tilda Moss",
    "Echo Bureau", "Sol Ferreira", "North Pier", "The Late Shift", "Ruby Lowe", "Atlas Bloom",
    "Coral & Kane", "Mono Garden", "Pale Fire Club", "Vera Knox",
]  # fmt: skip
TITLES = [
    "Afterglow", "Slow Burn", "City Lights", "Open Windows", "Rewind", "Heat Haze", "Late Bus",
    "Overtime", "Golden Hour", "Running Back", "Skyline", "Soft Focus", "After Hours", "Low Tide",
    "Static Love", "Paper Planes", "Ocean Avenue", "Midnight Run", "Signal", "Daybreak",
    "Safe Harbour", "Moving On", "Polaroid", "Velvet Sky", "Neon Rain", "Come Closer", "Spare Key",
    "First Light", "Sundown", "Back To You", "Satellite", "Fever Dream", "Quiet Riot Act",
    "Glasshouse", "Tidal", "Honey Trap", "Blue Hour", "Wildflower", "Last Train", "Hideaway",
]  # fmt: skip
SUFFIXES = ["(Extended Mix)", "(Club Mix)", "(Radio Edit)", "(Dub Mix)"]
CAMELOT = [f"{n}{m}" for n in range(1, 13) for m in "AB"]


@dataclass(frozen=True)
class Playlist:
    folder: str
    genres: tuple[str, ...]
    bpm: tuple[int, int]
    minutes: tuple[float, float]
    count: int
    tagged: bool = True  # False: some files have no BPM or key tag, as music that never went through a DJ app


PLAYLISTS = [
    Playlist("Warm Up", ("Deep House", "Chill House"), (104, 118), (4.4, 5.8), 14),
    Playlist(
        "House",
        ("House", "Tech House", "Progressive House"),
        (122, 128),
        (4.2, 5.2),
        20,
    ),
    Playlist("UK Garage", ("UK Garage",), (130, 134), (3.4, 4.3), 12),
    Playlist("Throwbacks", ("Dance", "Disco House"), (118, 128), (3.6, 4.6), 12),
]
MISC = Playlist(
    "Misc MP3s", ("Pop", "Dance", "House"), (100, 126), (3.2, 4.4), 8, tagged=False
)


@dataclass(frozen=True)
class DemoTrack:
    path: Path
    title: str
    artist: str
    album: str
    genre: str
    bpm: int
    key: str
    seconds: float
    seed: int
    tagged: bool


def catalog(root: Path, playlists: list[Playlist], seed: int = 7) -> list[DemoTrack]:
    """The tracks of `playlists` (unique titles across all of them), as they will be written under `root`."""
    rng = random.Random(seed)
    names = [f"{t} {s}" for t in TITLES for s in SUFFIXES]
    rng.shuffle(names)
    tracks = []
    for playlist in playlists:
        for i in range(playlist.count):
            title = names.pop()
            artist = rng.choice(ARTISTS)
            bpm = rng.randint(*playlist.bpm)
            tracks.append(
                DemoTrack(
                    path=root
                    / playlist.folder
                    / f"{artist} - {title.split(' (')[0]}.mp3",
                    title=title,
                    artist=artist,
                    album=f"{playlist.folder} Sessions",
                    genre=rng.choice(playlist.genres),
                    bpm=bpm,
                    key=rng.choice(CAMELOT),
                    seconds=rng.uniform(*playlist.minutes) * 60,
                    seed=rng.randrange(1 << 30),
                    tagged=playlist.tagged
                    or i % 3
                    != 0,  # an untagged playlist still has some BPM and key tags
                )
            )
    return tracks


# -- the sound -----------------------------------------------------------------------------------------------------------------


def _envelope(n: int, rate: float) -> np.ndarray:
    return np.exp(-np.arange(n) / SR * rate)


def _kick() -> np.ndarray:
    t = np.arange(int(0.32 * SR)) / SR
    freq = 46 + 110 * np.exp(-t * 32)
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 8)
    return (body + 0.35 * np.exp(-t * 400)).astype(np.float32)  # (the click)


def _noise(rng: np.random.Generator, n: int) -> np.ndarray:
    return rng.standard_normal(n).astype(np.float32)


def _add(buffer: np.ndarray, at: int, signal: np.ndarray) -> None:
    """Mix `signal` into `buffer` from sample `at`, cut off at the end of the buffer."""
    end = min(len(buffer), at + len(signal))
    if end > at:
        buffer[at:end] += signal[: end - at]


def synth(track: DemoTrack) -> np.ndarray:
    """Mono float32 audio for `track`: about `track.seconds` long, a whole number of bars at its tempo."""
    rng = np.random.default_rng(track.seed)
    beat = SR * 60 / track.bpm
    bars = max(32, round(track.seconds / (beat * 4 / SR)))
    total = int(bars * 4 * beat) + SR  # (a second of tail)
    kick, bass, mid, high, pad = (np.zeros(total, np.float32) for _ in range(5))
    k = _kick()
    hat = np.diff(_noise(rng, int(0.05 * SR) + 1)) * _envelope(int(0.05 * SR), 70)
    clap = _noise(rng, int(0.16 * SR)) * _envelope(int(0.16 * SR), 22)
    clap = np.convolve(
        clap, np.ones(3) / 3, mode="same"
    )  # (less hiss, more body: it sits in the mids)

    # the arrangement, as shares of the track: (share, kick, clap, hats, bass, pad/arp)
    plan = [
        (0.12, 1, 0, 1, 0, 0), (0.08, 1, 1, 1, 0, 1), (0.22, 1, 1, 1, 1, 1), (0.14, 0, 0, 0, 0, 1),
        (0.06, 1, 1, 1, 0, 1), (0.28, 1, 1, 1, 1, 1), (0.10, 1, 0, 1, 0, 0),
    ]  # fmt: skip
    flags = []
    for share, *on in plan:
        flags += [on] * max(1, round(bars * share))
    flags = (flags + [plan[-1][1:]] * bars)[:bars]

    root = 55 * 2 ** (random.Random(track.seed).randrange(0, 12) / 12)
    scale = [0, 3, 5, 7, 10, 12, 15, 17]  # (minor pentatonic-ish)
    stab_t = np.arange(int(0.2 * SR)) / SR
    arp_t = np.arange(int(beat / 4)) / SR
    for bar, (has_kick, has_clap, has_hat, has_bass, has_pad) in enumerate(flags):
        start = bar * 4 * beat
        for b in range(4):
            at = int(start + b * beat)
            off = int(at + beat / 2)  # the off-beat
            if has_kick:
                _add(kick, at, k)
            if has_clap and b in (1, 3):
                _add(mid, at, 0.55 * clap)
            if has_hat:
                _add(high, off, 0.5 * hat)
            if has_bass:  # an off-beat stab, so the kick has the beat to itself
                f = root * (1 if b % 2 == 0 else 2 ** (3 / 12))
                stab = (
                    np.sin(2 * np.pi * f * stab_t)
                    + 0.3 * np.sin(4 * np.pi * f * stab_t)
                ) * np.exp(-stab_t * 9)
                _add(bass, off, 0.55 * stab)
            if has_pad:  # a 16th-note arpeggio
                for s in range(4):
                    f = (
                        root
                        * 4
                        * 2 ** (scale[(bar * 3 + b * 2 + s * 5) % len(scale)] / 12)
                    )
                    _add(
                        pad,
                        int(at + s * beat / 4),
                        0.16 * np.sin(2 * np.pi * f * arp_t) * np.exp(-arp_t * 14),
                    )
        if has_pad:  # and a soft chord under the bar
            tt = np.arange(int(4 * beat)) / SR
            chord = (
                sum(
                    np.sin(2 * np.pi * root * 4 * 2 ** (d / 12) * tt) for d in (0, 3, 7)
                )
                * 0.05
            )
            _add(
                pad,
                int(start),
                chord * np.minimum(1.0, np.minimum(tt * 4, (tt[-1] - tt) * 4)),
            )

    mix = 0.9 * kick + 0.7 * mid + 0.6 * high + bass + pad
    mix /= max(1e-6, float(np.abs(mix).max())) / 0.89
    return mix.astype(np.float32)


# -- writing the files ---------------------------------------------------------------------------------------------------------


def _write(track: DemoTrack, wave_for) -> tuple[Path, object]:
    audio = synth(track)
    track.path.parent.mkdir(parents=True, exist_ok=True)
    tags = {
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "genre": track.genre,
    }
    if track.tagged:
        tags |= {"TBPM": str(track.bpm), "TKEY": track.key}
    meta = [arg for k, v in tags.items() for arg in ("-metadata", f"{k}={v}")]
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
         "-ac", "2", "-c:a", "libmp3lame", "-b:a", "128k", *meta, str(track.path)],
        input=audio.tobytes(), check=True,
    )  # fmt: skip
    return track.path, wave_for(audio)


def build_demo_music(root: Path, with_misc: bool = True) -> dict[Path, object]:
    """Write the made-up collection under `root`; returns {file: its whole-track Waveform}."""
    from dubcrate.waveform import compute_waveform

    tracks = catalog(root / "Demo Crate", PLAYLISTS) + (
        catalog(root, [MISC], seed=11) if with_misc else []
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(
            pool.map(lambda t: _write(t, lambda a: compute_waveform(a, SR)), tracks)
        )
