"""How long one frame of the zoomed waveform takes to paint: what 60 FPS scrolling would have to fit in.

    uv run python spikes/waveform_fps.py [--scale 2] [--seconds 360]

Offscreen, with a made-up track (`--seconds` long, a beat grid, cues and phrases) at a chosen display scale (2 is a
Retina Mac). Times `DetailView` in each look (3-band, RGB, blue) and the whole player strip's tick, then profiles the
3-band look.
Written for the 60 FPS question (`dev_docs/audio.md`, "60 FPS waveform scrolling"): 60 FPS is 16.7 ms a frame.
"""

import argparse
import cProfile
import os
import pstats
import statistics
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--scale", default="2", help="the display's scale factor (default 2)"
    )
    parser.add_argument(
        "--seconds", type=float, default=360.0, help="track length (default 360)"
    )
    args = parser.parse_args()
    os.environ["QT_SCALE_FACTOR"] = args.scale
    os.environ.setdefault("DUBCRATE_DATA_DIR", "/tmp/dubcrate-fps/data")

    import numpy as np
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        "/tmp/dubcrate-fps/settings",
    )
    from dubcrate import settings
    from dubcrate.analysis import Analysis, Cue, Phrase
    from dubcrate.ui import theme, wave_style
    from dubcrate.ui.player_strip import PlayerStrip
    from dubcrate.waveform import Waveform

    app = QApplication([])
    app.setOrganizationName("fps")
    app.setApplicationName("fps")
    theme.apply_to_app(app, "dark")
    rate, seconds = 150.0, args.seconds
    rng = np.random.default_rng(3)
    n = int(rate * seconds)
    t = np.arange(n) / rate
    env = (np.sin(2 * np.pi * t * 2.17) ** 2)[:, None]
    bands = np.clip(
        env * np.array([0.9, 0.5, 0.3]) + rng.random((n, 3)) * 0.15, 0, 1
    ).astype(np.float32)
    wave = Waveform(bands, rate)
    times = np.arange(0.5, seconds, 60 / 130)
    numbers = np.resize(np.array([1, 2, 3, 4], dtype=np.int8), len(times))
    cues = tuple(Cue("memory", s) for s in (10, 50, 90, 130)) + tuple(
        Cue("hot", 20.0 * k, slot=k) for k in range(1, 5)
    )
    phrases = tuple(
        Phrase(i * 16.0, (i + 1) * 16.0, "CHORUS 1") for i in range(int(seconds / 16))
    )
    analysis = Analysis(
        waveform=wave, preview=wave.resampled(1200, how="mean"), beat_times=times, beat_numbers=numbers,
        bpm=130.0, cues=cues, phrases=phrases,
    )  # fmt: skip
    strip = PlayerStrip()
    strip.resize(1300, 360)
    strip.show()
    app.processEvents()
    strip.show_media(seconds, wave, analysis)
    strip.show_cues(cues)
    detail = strip.detail
    dpr = detail.devicePixelRatioF()
    print(
        f"detail view {detail.width()}x{detail.height()} logical, scale {dpr:g}, zoom window {detail._window:.1f} s"
    )

    def bench(label, fn, runs=240):
        ms = []
        for i in range(runs):
            t0 = time.perf_counter()
            fn(30.0 + i * 0.033)
            ms.append((time.perf_counter() - t0) * 1000)
        ms = ms[10:]
        print(f"{label:44} mean {statistics.mean(ms):6.2f} ms  p95 {sorted(ms)[int(len(ms) * 0.95)]:6.2f}  max {max(ms):6.2f}  -> {1000 / statistics.mean(ms):4.0f} fps")  # fmt: skip

    pix = QPixmap(int(detail.width() * dpr), int(detail.height() * dpr))
    pix.setDevicePixelRatio(dpr)
    for style in ("bands", "rgb", "blue"):
        settings.set_wave_style(style)
        wave_style.notifier.changed.emit()
        bench(
            f"detail view, {style} look",
            lambda p: (detail.set_position(p), detail.render(pix)),
        )
    for style in ("bands", "rgb"):
        settings.set_wave_style(style)
        wave_style.notifier.changed.emit()
        bench(
            f"whole strip tick + event loop, {style}",
            lambda p: (strip.set_position(p), app.processEvents()),
        )

    settings.set_wave_style("bands")
    wave_style.notifier.changed.emit()
    profile = cProfile.Profile()
    profile.enable()
    for i in range(60):
        detail.set_position(30.0 + i * 0.033)
        detail.render(pix)
    profile.disable()
    print("\n3-band look, 60 frames:")
    pstats.Stats(profile).sort_stats("tottime").print_stats(4)


if __name__ == "__main__":
    main()
