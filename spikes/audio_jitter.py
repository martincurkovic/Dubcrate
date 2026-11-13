"""Audio jitter spike: how late do the preview player's callbacks arrive?

Throwaway driver. The audio callback is a Python function, so it needs the GIL: anything on the GUI
thread that holds the GIL for longer than one buffer lasts makes the preview crackle. This plays a
SILENT (volume 0) track through the real output device and records when each callback fires, so a
gap much longer than the buffer's period is an underrun waiting to happen. It also times the GUI
thread's slow events, slow paints and garbage-collector pauses, which is how the 30 to 45 ms full
collections (about every 6 s, triggered by the zoomed waveform's painting) were found.

    uv run spikes/audio_jitter.py bare --buffer 128          # the player alone
    uv run spikes/audio_jitter.py full --buffer 1024         # the whole window, playing
    uv run spikes/audio_jitter.py full --buffer 1024 --gc freeze   # as the app now starts

Settings, the library and the theme's icons go to a scratch folder; nothing of yours is read.
Opens the real output device (silently). No sound is made.
"""

import argparse
import gc
import itertools
import os
import statistics
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from dubcrate.audio import player as player_module
from dubcrate.audio.decode import DecodedAudio

SLOW_EVENT = 0.008  # seconds
SLOW_GC = 0.004


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("mode", choices=["bare", "full"])
    parser.add_argument("--buffer", type=int, default=1024, help="frames")
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument(
        "--gc",
        choices=["default", "freeze", "off"],
        default="default",
        help="freeze: gc.collect() then gc.freeze(), as the app does at start-up",
    )
    parser.add_argument(
        "--switch-ms",
        type=float,
        default=None,
        help="sys.setswitchinterval, in ms (Python's default is 5): how long a thread may keep the GIL "
        "before another one that is waiting gets it",
    )
    parser.add_argument(
        "--worker",
        action="store_true",
        help="also run background work (a 3-minute track's waveform, then a plain Python loop, over and"
        " over), like the decoding and measuring the app does on worker threads, and report how much it"
        " got done: what a short --switch-ms costs",
    )
    args = parser.parse_args()
    if args.switch_ms is not None:
        sys.setswitchinterval(args.switch_ms / 1000)

    scratch = Path(tempfile.mkdtemp(prefix="dubcrate-jitter-"))
    os.environ["DUBCRATE_DATA_DIR"] = str(scratch / "data")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(scratch / "settings")
    )

    t0 = time.perf_counter()
    slow_events: list[tuple[int, float, str, int]] = []

    class App(QApplication):
        def notify(self, obj, event):  # time every event the GUI thread handles
            start = time.perf_counter()
            try:
                return super().notify(obj, event)
            finally:
                took = time.perf_counter() - start
                if took > SLOW_EVENT:
                    slow_events.append(
                        (
                            round(took * 1000),
                            round(time.perf_counter() - t0, 1),
                            type(obj).__name__,
                            int(event.type().value),
                        )
                    )

    collections: list[tuple[int, float, int]] = []
    began: dict[str, float] = {}

    def on_gc(phase: str, info: dict) -> None:
        if phase == "start":
            began["t"] = time.perf_counter()
        elif (took := time.perf_counter() - began["t"]) > SLOW_GC:
            collections.append(
                (
                    round(took * 1000),
                    round(time.perf_counter() - t0, 1),
                    info["generation"],
                )
            )

    gc.callbacks.append(on_gc)

    app = App([])
    app.setOrganizationName("jitter")
    app.setApplicationName("jitter")

    starts: list[float] = []
    durations: list[float] = []
    real_render_block = player_module.render_block

    def timed(state, frames, now=None):  # when the audio thread got its turn
        start = time.perf_counter()
        block = real_render_block(state, frames, now)
        starts.append(start)
        durations.append(time.perf_counter() - start)
        return block

    player_module.render_block = timed

    seconds_of_audio = 120
    t = np.arange(44_100 * seconds_of_audio) / 44_100
    wave_samples = (np.sin(2 * np.pi * 220 * t) * 8000).astype(np.int16)
    audio = DecodedAudio(np.repeat(wave_samples[:, None], 2, axis=1))

    from dubcrate import settings

    settings.set_audio_buffer(args.buffer)
    if args.mode == "bare":
        player = player_module.AudioPlayer()
        player.configure(None, args.buffer)
        player.set_volume(0.0)
        player.load(audio, autoplay=True)
    else:
        from dubcrate.library import Library, TrackRecord
        from dubcrate.ui import theme
        from dubcrate.ui.main_window import MainWindow

        theme.apply_to_app(app, "dark")
        path = scratch / "long.wav"
        with wave.open(str(path), "wb") as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(44_100)
            out.writeframes(audio.samples.tobytes())
        lib = Library.open(":memory:")
        lib.replace_source(
            "folder",
            str(scratch),
            "Jitter",
            [
                TrackRecord(
                    ext_id="a",
                    path=str(path),
                    title="Long",
                    artist="x",
                    bpm=120,
                    duration=float(seconds_of_audio),
                )
            ],
            [],
        )
        player = player_module.AudioPlayer()
        window = MainWindow(lib, player=player)
        window.resize(1440, 920)
        window.show()
        app.processEvents()
        player.set_volume(0.0)
        window.preview.load(lib.tracks()[0])

    if args.gc == "freeze":
        gc.collect()
        gc.freeze()
    elif args.gc == "off":
        gc.disable()

    done = {"waveforms": 0, "loops": 0}
    stop = threading.Event()
    if args.worker:
        from dubcrate.waveform import compute_waveform

        noise = (
            np.random.default_rng(1).standard_normal(44_100 * 180).astype(np.float32)
        )

        def work() -> None:
            while not stop.is_set():
                compute_waveform(noise)  # numpy: mostly outside the GIL
                done["waveforms"] += 1
                total = 0
                for i in range(300_000):  # plain Python: needs the GIL all the time
                    total += i * i
                done["loops"] += 1

        threading.Thread(target=work, daemon=True).start()

    end = time.time() + args.seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.001)
    stop.set()
    player.shutdown()

    gaps = [b - a for a, b in itertools.pairwise(starts)]
    if not gaps:
        print("no callbacks were recorded")
        return
    period = statistics.median(gaps)
    ms = lambda x: f"{x * 1000:.1f}"
    worst = sorted(((g, a - starts[0]) for g, a in zip(gaps, starts, strict=False)), reverse=True)[:6]  # fmt: skip
    print(
        f"{args.mode}, buffer {args.buffer} frames ({args.buffer / 44.1:.1f} ms), gc {args.gc},"
        f" switch interval {sys.getswitchinterval() * 1000:g} ms"
    )
    print(f"  callbacks {len(starts)}, median gap {ms(period)} ms, p99 {ms(sorted(gaps)[int(len(gaps) * 0.99)])} ms, MAX {ms(max(gaps))} ms")  # fmt: skip
    if args.worker:
        print(
            f"  background work done: {done['waveforms']} waveforms, {done['loops']} Python loops"
        )
    print(f"  late (over 1.5 x the median): {sum(g > 1.5 * period for g in gaps)}")
    print(f"  worst gaps (ms, s after the first callback): {[(round(g * 1000), round(a, 1)) for g, a in worst]}")  # fmt: skip
    print(f"  callback work: median {statistics.median(durations) * 1000:.3f} ms, max {max(durations) * 1000:.2f} ms")  # fmt: skip
    print(f"  garbage collections over {SLOW_GC * 1000:.0f} ms (ms, s, generation): {collections}")  # fmt: skip
    print(f"  GUI events over {SLOW_EVENT * 1000:.0f} ms (ms, s, widget, event type): {sorted(slow_events, reverse=True)[:8]}")  # fmt: skip


if __name__ == "__main__":
    main()
