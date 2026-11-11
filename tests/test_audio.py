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


# -- scratching: the head follows a target the GUI moves ------------------------------

MOUSE_HZ = 60  # how often a drag reports its position
BLOCK = 1024  # a typical audio block
WRAP = 32_768


def sawtooth(frames=SAMPLE_RATE * 8):
    """Sample n is n modulo 32768 (in both channels): what was read is recoverable from the
    output, and a steady slope per block IS the playback speed."""
    values = (np.arange(frames, dtype=np.int64) % WRAP).astype(np.int16)
    return np.stack([values, values], axis=1)


def scratching_state(frame=1000, volume=1.0, velocity=0.0, gain=None) -> PlaybackState:
    """A track being held, with the head at `frame` and moving at `velocity` frames a second."""
    playing_level = volume if gain is None else gain
    state = PlaybackState(
        samples=sawtooth(), frame=frame, volume=volume, gain=playing_level
    )
    state.scratching = True
    state.scratch_pos = float(frame)
    state.scratch_target = float(frame)
    state.scratch_vel = velocity
    state.scratch_gain = playing_level
    return state


def slope(block_values: np.ndarray) -> float:
    """Frames of the source read per output frame: the playback speed, from the sawtooth.

    The sawtooth wraps, and reading across the wrap interpolates between its top and
    bottom, which is one huge bogus step: steps that no speed could make are dropped.
    """
    steps = (np.diff(block_values) + WRAP / 2) % WRAP - WRAP / 2  # undo the wrap
    steps = steps[
        np.abs(steps) <= 64
    ]  # (the fastest scratch reads 16 frames per frame)
    return float(np.polyfit(np.arange(len(steps)), np.cumsum(steps), 1)[0])


def drag(state, speed, seconds, block=BLOCK, mouse_hz=MOUSE_HZ, start=None):
    """Drag steadily at `speed` x normal for `seconds`, with the mouse reporting at its own
    rate and the audio pulled in blocks (the two never line up). Returns, per block, the
    playback speed heard and where the hand was minus where the head is (frames)."""
    from dubcrate.audio.player import note_scratch_target

    origin = state.scratch_target if start is None else start
    t, next_event = 0.0, 0.0
    speeds, behind = [], []
    for _ in range(round(seconds * SAMPLE_RATE / block)):
        end = t + block / SAMPLE_RATE
        while next_event <= end:  # mouse events that arrived during this block
            note_scratch_target(
                state, origin + speed * SAMPLE_RATE * next_event, next_event
            )
            next_event += 1.0 / mouse_hz
        out = blocks(render_block(state, block, now=end))[:, 0].astype(float)
        speeds.append(slope(out))
        behind.append(origin + speed * SAMPLE_RATE * end - state.scratch_pos)
        t = end
    return speeds, behind


def test_a_steady_drag_plays_at_a_steady_speed_whatever_the_mouse_timing():
    # The bug this guards: a head that jumped to the target every block alternated
    # 0.72x and 1.44x on a steady drag, because mouse events and blocks don't line up.
    speeds, _ = drag(scratching_state(frame=2000), speed=1.0, seconds=1.5)
    settled = np.array(speeds[10:])
    assert settled.min() > 0.97 and settled.max() < 1.03


def test_the_speed_is_just_as_steady_at_other_mouse_rates_and_buffer_sizes():
    for mouse_hz in (30, 120):
        for block in (512, 2048):
            speeds, _ = drag(
                scratching_state(frame=2000), 1.0, 1.5, block=block, mouse_hz=mouse_hz
            )
            settled = np.array(speeds[len(speeds) // 3 :])
            assert settled.min() > 0.93 and settled.max() < 1.07, (mouse_hz, block)


def test_the_audio_does_not_trail_the_hand():
    # With the lag of a plain spring (~90 ms to be this smooth) a scratch feels sluggish.
    _, behind = drag(scratching_state(frame=2000), speed=1.0, seconds=1.5)
    typical = abs(np.mean(behind[15:])) / SAMPLE_RATE * 1000
    assert typical < 25  # milliseconds of audio behind where the hand is


def test_scratching_plays_the_audio_forward_at_the_speed_of_the_drag():
    speeds, _ = drag(scratching_state(frame=2000), speed=2.0, seconds=1.5)
    assert np.mean(speeds[10:]) == pytest.approx(2.0, abs=0.04)


def test_scratching_backwards_plays_the_audio_in_reverse():
    speeds, _ = drag(scratching_state(frame=200_000), speed=-1.0, seconds=1.5)
    assert np.mean(speeds[10:]) == pytest.approx(-1.0, abs=0.04)


def test_a_slow_drag_is_a_low_pitch_not_a_stretched_one():
    # Like vinyl: half speed reads half the frames per frame (an octave down).
    speeds, _ = drag(scratching_state(frame=2000), speed=0.5, seconds=2.0)
    assert np.mean(speeds[15:]) == pytest.approx(0.5, abs=0.03)


def test_a_stationary_target_is_silence_not_a_held_note():
    state = scratching_state(frame=3000)
    assert not blocks(render_block(state, 256)).any()
    assert state.scratch_pos == 3000


def test_the_head_stops_quickly_when_the_hand_does_and_never_runs_on_far():
    from dubcrate.audio.player import note_scratch_target

    state = scratching_state(frame=2000)
    drag(state, speed=1.0, seconds=0.5)
    held = state.scratch_target  # the hand stops dead here
    reached = []
    clock = 0.5
    for _ in range(60):
        clock += 256 / SAMPLE_RATE
        render_block(state, 256, now=clock)
        reached.append(state.scratch_pos)
    # It runs on a little: a block plays audio from the near future, so the target is predicted
    # about 40 ms ahead, and a hand that stops inside that window was over-predicted (about
    # 50 ms of audio at normal speed, measured).
    assert max(reached) - held < 0.06 * SAMPLE_RATE
    assert reached[-1] == pytest.approx(reached[-2], abs=0.5)  # and then rests
    assert abs(reached[-1] - held) < 0.05 * SAMPLE_RATE
    assert (
        abs(state.scratch_vel) < 0.001 * SAMPLE_RATE
    )  # under a thousandth of normal speed
    del note_scratch_target


def test_the_head_never_moves_faster_than_the_speed_limit():
    from dubcrate.audio.player import MAX_SCRATCH_RATE

    state = scratching_state(frame=1000)
    state.scratch_target = 300_000  # a huge flick
    advance = []
    for _ in range(120):
        before = state.scratch_pos
        render_block(state, BLOCK)
        advance.append((state.scratch_pos - before) / BLOCK)
    assert max(advance) <= MAX_SCRATCH_RATE * 1.05
    assert max(advance) > MAX_SCRATCH_RATE * 0.5  # but it does go fast: it is chasing


def test_a_pause_in_the_mouse_events_does_not_make_the_audio_run_away():
    from dubcrate.audio.player import note_scratch_target

    state = scratching_state(frame=2000)
    t = 0.0
    for i in range(6):  # a drag at 1x, 60 Hz
        t = i / MOUSE_HZ
        note_scratch_target(state, 2000 + SAMPLE_RATE * t, t)
    last = 2000 + SAMPLE_RATE * t
    clock = t
    for _ in range(80):  # then no more events for a long time (the hand froze)
        clock += BLOCK / SAMPLE_RATE
        render_block(state, BLOCK, now=clock)
    assert state.scratch_pos - last < 0.05 * SAMPLE_RATE  # extrapolation is short-lived


def test_the_drag_velocity_comes_from_the_events_not_from_two_that_arrive_together():
    from dubcrate.audio.player import note_scratch_target

    state = scratching_state(frame=2000)
    note_scratch_target(state, 2000, 0.000)
    note_scratch_target(state, 2800, 0.001)  # two events delivered back to back
    assert state.scratch_ff == 0.0  # too close in time to tell a speed from
    note_scratch_target(state, 3600, 0.020)
    assert state.scratch_ff == pytest.approx((3600 - 2000) / 0.020)


def test_grabbing_a_playing_track_carries_on_smoothly_instead_of_clicking():
    state = scratching_state(frame=5000, velocity=float(SAMPLE_RATE))  # was playing
    first = blocks(render_block(state, 256))[:, 0].astype(float)
    assert first[0] == pytest.approx(5000, abs=2)  # picks up exactly where it was
    assert first[10] > first[0]  # still moving forward, then easing off
    assert (np.diff(first) >= 0).all()


def test_starting_from_rest_fades_in_and_the_volume_applies():
    state = scratching_state(frame=2000, volume=0.5)
    state.scratch_target = 2000 + 20_000
    out = blocks(render_block(state, 2048))[:, 0].astype(float)
    assert out[0] == pytest.approx(0.0, abs=2)  # begins silent, no click
    assert out.max() <= 32_000 * 0.5 + 1  # and the volume scales it


def test_scratching_past_either_end_stays_in_the_track_and_never_crashes():
    state = scratching_state(frame=10)
    state.scratch_target = -5000
    for _ in range(10):
        assert len(render_block(state, 128)) == 128 * 4
    assert (
        state.scratch_pos == pytest.approx(0.0, abs=1e-3) and state.scratch_vel == 0.0
    )
    total = len(sawtooth())
    end = scratching_state(frame=total - 50)
    end.scratch_target = total + 90_000
    for _ in range(10):
        render_block(end, 128)
    assert end.scratch_pos <= total


def test_scratching_without_a_track_is_silence():
    state = PlaybackState()
    state.scratching = True
    assert not blocks(render_block(state, 64)).any()


def test_a_scratch_leaves_a_playing_track_playing(player):
    player.load(DecodedAudio(sawtooth()))
    player.play()
    device = player.opened[0]
    device.pull(1000)
    player.begin_scratch()
    assert player.scratching and player.playing  # held, not stopped
    before = player.position
    device.pull(1000)
    assert player.position == pytest.approx(
        before, abs=0.01
    )  # it slows at once; nothing runs on
    player.scratch_to(before + 0.5)
    for _ in range(40):
        device.pull(1024)
    assert player.position == pytest.approx(before + 0.5, abs=0.001)
    player.end_scratch()
    assert not player.scratching and player.playing
    at = player.position
    for _ in range(3):
        device.pull(1024)
    assert player.position > at  # carries on playing from where the drag left it


def test_a_scratch_on_a_paused_track_stays_paused_and_is_heard(player):
    player.load(DecodedAudio(sawtooth()))
    assert player.opened == []
    player.begin_scratch()
    assert len(player.opened) == 1 and not player.playing  # opened the device itself
    device = player.opened[0]
    player.scratch_to(0.25)
    heard = [blocks(device.pull(1024)).any() for _ in range(15)]
    assert any(heard)
    player.end_scratch()
    assert not player.playing and player.position == pytest.approx(0.25, abs=0.002)
    device.pull(1024)  # the short fade-out
    at = player.position
    assert not blocks(device.pull(1024)).any() and player.position == at


def test_scratch_to_is_clamped_to_the_track(player):
    player.load(DecodedAudio(sawtooth(SAMPLE_RATE)))  # one second
    player.begin_scratch()
    player.scratch_to(99.0)
    for _ in range(80):
        player.opened[0].pull(1024)
    assert player.position <= 1.0
    player.scratch_to(-9.0)
    for _ in range(80):
        player.opened[0].pull(1024)
    assert player.position == pytest.approx(0.0, abs=1e-3)


def test_scratch_calls_without_a_scratch_or_a_track_do_nothing(player):
    player.scratch_to(1.0)
    player.end_scratch()
    player.begin_scratch()
    assert not player.scratching and player.opened == []
    player.load(DecodedAudio(sawtooth()))
    player.scratch_to(1.0)  # no scratch in progress
    assert player.position == 0.0


def test_loading_a_new_track_ends_any_scratch(player):
    player.load(DecodedAudio(sawtooth()))
    player.begin_scratch()
    player.load(DecodedAudio(sawtooth()))
    assert not player.scratching and player.position == 0.0
