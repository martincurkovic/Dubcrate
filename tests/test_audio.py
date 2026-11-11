import shutil
import subprocess
import wave

import numpy as np
import pytest

from dubcrate.audio import decode, devices
from dubcrate.audio.decode import DecodedAudio, DecodeError, decode_file
from dubcrate.audio.devices import SAMPLE_RATE, buffer_msec
from dubcrate.audio.player import AudioPlayer, PlaybackState, render_block

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="ffmpeg not installed"
)


def tone(frames=44_100, amplitude=10_000):
    return np.full((frames, 2), amplitude, dtype=np.int16)


def blocks(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.int16).reshape(-1, 2)


# -- render_block: what the audio thread runs ------------------------------------


def test_silence_without_a_track_or_while_paused():
    assert not blocks(render_block(PlaybackState(), 256)).any()
    state = PlaybackState(samples=tone(), playing=False, gain=0.0)
    assert not blocks(render_block(state, 256)).any() and state.frame == 0


def test_playing_at_full_gain_copies_the_samples_and_advances():
    state = PlaybackState(
        samples=np.arange(2000, dtype=np.int16).repeat(2).reshape(-1, 2),
        playing=True,
        gain=1.0,
    )
    out = blocks(render_block(state, 100))
    assert (out[:, 0] == np.arange(100)).all() and state.frame == 100
    assert blocks(render_block(state, 50))[0, 0] == 100  # carries on where it stopped


def test_starting_fades_in_across_the_first_block():
    state = PlaybackState(samples=tone(), playing=True, gain=0.0)
    out = blocks(render_block(state, 200))[:, 0].astype(int)
    assert out[0] == 0 and abs(out[-1] - 10_000) < 100
    assert (np.diff(out) >= 0).all()  # smoothly up, no click
    assert state.gain == 1.0


def test_pausing_fades_out_then_stops_advancing():
    state = PlaybackState(samples=tone(), playing=True, gain=1.0)
    render_block(state, 100)
    state.playing = False
    fade = blocks(render_block(state, 200))[:, 0].astype(int)
    assert fade[0] > 9_000 and fade[-1] == 0 and (np.diff(fade) <= 0).all()
    stopped_at = state.frame
    assert not blocks(render_block(state, 200)).any() and state.frame == stopped_at


def test_the_end_of_the_track_pads_with_silence_and_marks_it_finished():
    state = PlaybackState(samples=tone(frames=150), playing=True, gain=1.0)
    out = blocks(render_block(state, 200))
    assert (out[:150] == 10_000).all() and not out[150:].any()
    assert state.finished and not state.playing and state.frame == 150
    assert not blocks(render_block(state, 200)).any()


def test_volume_scales_the_output_and_changes_smoothly():
    state = PlaybackState(samples=tone(), playing=True, gain=0.5, volume=0.5)
    assert (blocks(render_block(state, 100)) == 5_000).all()
    state.volume = 1.0
    out = blocks(render_block(state, 200))[:, 0].astype(int)
    assert out[0] < out[-1] and abs(out[-1] - 10_000) < 100


def test_output_never_overflows_int16():
    state = PlaybackState(
        samples=tone(amplitude=32_767), playing=True, gain=1.0, volume=1.0
    )
    assert blocks(render_block(state, 64)).max() == 32_767


# -- the player, through a fake device ------------------------------------------------


class FakeDevice:
    def __init__(self, rate, msec, device_id):
        self.args = (rate, msec, device_id)
        self.gen = None
        self.stopped = self.closed = False

    def start(self, gen):
        self.gen = gen

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def pull(self, frames):
        return self.gen.send(frames)


@pytest.fixture
def player(qapp):
    opened: list[FakeDevice] = []

    def factory(rate, msec, device_id):
        device = FakeDevice(rate, msec, device_id)
        opened.append(device)
        return device

    p = AudioPlayer(device_factory=factory)
    p.opened = opened
    p.events = events = {"state": [], "position": [], "finished": 0, "failed": []}
    p.state_changed.connect(events["state"].append)
    p.position_changed.connect(events["position"].append)
    p.finished.connect(lambda: events.__setitem__("finished", events["finished"] + 1))
    p.failed.connect(events["failed"].append)
    yield p
    p.shutdown()


def audio(seconds=2.0):
    return DecodedAudio(tone(int(SAMPLE_RATE * seconds)))


def test_nothing_happens_without_a_loaded_track(player):
    player.play()
    player.seek(1.0)
    assert (
        not player.loaded
        and not player.playing
        and player.opened == []
        and player.duration == 0.0
    )


def test_play_opens_the_device_at_the_cd_rate_and_the_configured_buffer(player):
    player.configure(None, 512)
    player.load(audio())
    assert player.opened == []  # nothing is opened until it is needed
    player.play()
    [device] = player.opened
    assert device.args == (SAMPLE_RATE, buffer_msec(512), None)
    assert player.playing and player.events["state"] == [True]
    assert len(device.pull(1024)) == 1024 * 2 * 2  # frames x channels x 2 bytes


def test_position_follows_what_the_device_has_pulled(player):
    player.load(audio(2.0))
    player.play()
    device = player.opened[0]
    for _ in range(4):
        device.pull(SAMPLE_RATE // 4)
    assert abs(player.position - 1.0) < 1e-6 and player.duration == 2.0
    player._on_tick()
    assert abs(player.events["position"][-1] - 1.0) < 1e-6


def test_pause_fades_out_and_play_resumes_from_the_same_place(player):
    player.load(audio())
    player.play()
    device = player.opened[0]
    device.pull(1000)
    player.pause()
    assert not player.playing and player.events["state"] == [True, False]
    device.pull(1000)  # the fade-out block still advances a little
    at = player.position
    assert not blocks(device.pull(1000)).any() and player.position == at
    player.play()
    assert blocks(device.pull(1000)).any() and player.position > at
    assert len(player.opened) == 1  # same device throughout


def test_toggle_flips_between_playing_and_paused(player):
    player.load(audio())
    player.toggle()
    assert player.playing
    player.toggle()
    assert not player.playing


def test_seek_jumps_and_is_clamped_to_the_track(player):
    player.load(audio(2.0))
    player.seek(1.5)
    assert player.position == 1.5 and player.events["position"][-1] == 1.5
    player.seek(99.0)
    assert player.position == 2.0
    player.seek(-5.0)
    assert player.position == 0.0


def test_seeking_while_playing_fades_in_from_silence(player):
    player.load(audio())
    player.play()
    device = player.opened[0]
    device.pull(500)
    player.seek(1.0)
    out = blocks(device.pull(300))[:, 0]
    assert out[0] == 0 and out[-1] > 9_000


def test_reaching_the_end_reports_finished_and_stops_the_clock(player):
    player.load(audio(0.01))
    player.play()
    player.opened[0].pull(1000)
    player._on_tick()
    assert player.events["finished"] == 1 and not player.playing
    assert player.events["state"] == [True, False]
    player.play()  # playing again restarts from the beginning
    assert player.position == 0.0 and player.playing


def test_loading_another_track_stops_the_first(player):
    player.load(audio())
    player.play()
    player.load(audio(1.0))
    assert not player.playing and player.position == 0.0 and player.duration == 1.0
    player.load(audio(1.0), autoplay=True)
    assert player.playing


def test_unload_releases_the_device(player):
    player.load(audio())
    player.play()
    device = player.opened[0]
    player.unload()
    assert device.stopped and device.closed and not player.loaded and not player.playing


def test_changing_the_buffer_reopens_the_device_even_mid_track(player):
    player.configure(None, 1024)
    player.load(audio())
    player.play()
    player.opened[0].pull(500)
    player.configure(None, 256)
    first, second = player.opened
    assert first.closed and second.args[1] == buffer_msec(256)
    assert (
        abs(player.position - 500 / SAMPLE_RATE) < 1e-6
    )  # the place in the track survives
    player.configure(None, 256)  # unchanged: no churn
    assert len(player.opened) == 2


def test_a_device_that_will_not_open_is_reported_not_raised(qapp):
    def broken(rate, msec, device_id):
        raise RuntimeError("no such device")

    p = AudioPlayer(device_factory=broken)
    failures = []
    p.failed.connect(failures.append)
    p.load(audio())
    p.play()
    assert not p.playing and failures and "no such device" in failures[0]


def test_volume_is_clamped_and_applied(player):
    player.load(audio())
    player.set_volume(2.0)
    assert player.volume == 1.0
    player.set_volume(-1)
    assert player.volume == 0.0
    player.set_volume(0.5)
    player.play()
    device = player.opened[0]
    device.pull(2000)  # ramp up
    assert abs(int(blocks(device.pull(100))[0, 0]) - 5_000) <= 1


def test_shutdown_is_safe_to_repeat(player):
    player.load(audio())
    player.play()
    device = player.opened[0]
    player.shutdown()
    player.shutdown()
    assert device.closed and not player.playing


# -- devices ----------------------------------------------------------------------------


def test_buffer_size_conversion():
    assert buffer_msec(1024) == 23 and buffer_msec(32) == 1 and buffer_msec(2048) == 46
    assert buffer_msec(1024, 48_000) == 21
    assert devices.DEFAULT_BUFFER in devices.BUFFER_SIZES


def test_a_saved_device_that_vanished_falls_back_to_the_default(monkeypatch):
    monkeypatch.setattr(devices, "list_output_devices", list)
    assert devices.resolve_output_device(None) is None
    assert devices.resolve_output_device("deadbeef") is None
    assert devices.find_output_device("") is None


def test_a_saved_device_is_found_by_its_id(monkeypatch):
    fake = {"name": "Interface", "id": "opaque"}
    monkeypatch.setattr(devices, "list_output_devices", lambda: [fake])
    monkeypatch.setattr(devices, "device_id_string", lambda device: "abc123")
    assert devices.find_output_device("abc123") is fake
    assert devices.resolve_output_device("abc123") is fake


# -- decoding ---------------------------------------------------------------------------------


def write_wav(path, seconds=0.5, rate=44_100, channels=2):
    t = np.arange(int(rate * seconds)) / rate
    data = (np.sin(2 * np.pi * 440 * t) * 12_000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.repeat(data[:, None], channels, axis=1).tobytes())


def test_decoding_a_wav_gives_cd_format_stereo(tmp_path):
    write_wav(tmp_path / "a.wav", seconds=0.5)
    got = decode_file(str(tmp_path / "a.wav"))
    assert got.samples.dtype == np.int16 and got.samples.shape == (22_050, 2)
    assert got.sample_rate == SAMPLE_RATE and abs(got.duration - 0.5) < 0.01
    assert np.abs(got.samples).max() > 11_000
    assert got.mono().dtype == np.float32 and 0.3 < np.abs(got.mono()).max() < 0.4


def test_other_rates_and_mono_are_converted(tmp_path):
    write_wav(tmp_path / "m.wav", seconds=0.5, rate=22_050, channels=1)
    got = decode_file(str(tmp_path / "m.wav"))
    assert got.samples.shape[1] == 2 and abs(got.duration - 0.5) < 0.02
    assert (got.samples[:, 0] == got.samples[:, 1]).all()


@needs_ffmpeg
def test_formats_miniaudio_cannot_read_fall_back_to_ffmpeg(tmp_path):
    src = tmp_path / "x.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1",
            "-c:a",
            "aac",
            str(src),
        ],
        check=True,
    )
    got = decode_file(str(src))
    assert got.samples.shape[1] == 2 and 0.9 < got.duration < 1.2


def test_garbage_is_a_decode_error(tmp_path):
    (tmp_path / "bad.mp3").write_bytes(b"this is not audio")
    with pytest.raises(DecodeError):
        decode_file(str(tmp_path / "bad.mp3"))


def test_missing_ffmpeg_is_a_clear_error(tmp_path, monkeypatch):
    (tmp_path / "bad.m4a").write_bytes(b"nope")
    monkeypatch.setattr(decode.shutil, "which", lambda _name: None)
    with pytest.raises(DecodeError, match="ffmpeg not found"):
        decode_file(str(tmp_path / "bad.m4a"))
