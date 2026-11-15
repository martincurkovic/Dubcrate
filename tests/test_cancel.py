"""Stopping long jobs: the token, the shared loop, what each job hands back, and the task layer (no drive, no ffmpeg)."""

import threading
import time
from pathlib import Path

import pytest

from dubcrate import loudness as loudness_module
from dubcrate import render
from dubcrate.cancel import Cancelled, CancelToken, cancellable_map
from dubcrate.library import Library, TrackRecord, folder, lengths, levels, waves
from dubcrate.library import rekordbox as rekordbox_module
from dubcrate.ui.tasks import run_async


def test_a_token_starts_clear_is_set_by_cancel_and_check_raises_with_the_partial():
    token = CancelToken()
    assert not token.cancelled
    token.check("x")  # (nothing happens)
    token.cancel()
    assert token.cancelled
    with pytest.raises(Cancelled) as stopped:
        token.check({"done": 3})
    assert stopped.value.partial == {"done": 3}


def test_the_loop_gives_results_in_order_whatever_order_they_finish_in():
    def slow_first(n):
        time.sleep(0.05 if n == 0 else 0)
        return n * n

    assert list(cancellable_map(slow_first, range(8), workers=4)) == [
        n * n for n in range(8)
    ]


def test_once_cancelled_the_loop_starts_no_more_work_and_raises():
    started = []

    def work(n):
        started.append(n)
        time.sleep(0.02)
        return n

    token = CancelToken()
    taken = []
    with pytest.raises(Cancelled):
        for result in cancellable_map(work, range(60), workers=2, cancel=token):
            taken.append(result)
            if len(taken) == 3:
                token.cancel()
    assert (
        taken == [0, 1, 2] and len(started) < 20
    )  # (those already being read finish; 57 never start)


def test_an_exception_in_a_job_is_raised_in_order_and_leaves_no_queue_behind():
    def work(n):
        if n == 2:
            raise ValueError("bad file")
        return n

    seen = []
    with pytest.raises(ValueError, match="bad file"):
        seen.extend(cancellable_map(work, range(5), workers=1))
    assert seen == [0, 1]


# -- what each job hands back ------------------------------------------------------------------------


def tracks_in(lib, n=30):
    lib.replace_source(
        "folder", "/m", "M",
        [TrackRecord(ext_id=f"t{k}", path=f"/m/t{k}.mp3", title=f"t{k}", duration=60.0, file_size=1) for k in range(n)],
    )  # fmt: skip
    # (the files aren't there, so every track is `missing`: stand it in as readable for the jobs that skip those)
    return [
        t.__class__(**{**t.__dict__, "missing": False}) for t in lib.tracks(sort="path")
    ]


def cancel_after(token, count):
    seen = []

    def report(done, total):
        seen.append(done)
        if done >= count:
            token.cancel()

    return report


def test_measuring_lengths_hands_back_what_it_measured_when_cancelled(monkeypatch):
    monkeypatch.setattr(
        lengths,
        "probe",
        lambda path: (time.sleep(0.01), type("I", (), {"duration": 60.0})())[1],
    )
    with Library.open(":memory:") as lib:
        tracks = tracks_in(lib)
    token = CancelToken()
    with pytest.raises(Cancelled) as stopped:
        lengths.probe_lengths(tracks, cancel_after(token, 4), workers=2, cancel=token)
    found, problems = stopped.value.partial
    assert (
        4 <= len(found) < 30 and problems == []
    )  # a length is a length, however the job ended


def test_measuring_loudness_hands_back_what_it_measured_when_cancelled(monkeypatch):
    monkeypatch.setattr(
        levels,
        "measure",
        lambda path: (time.sleep(0.01), loudness_module.Loudness(-12.0, -1.0))[1],
    )
    with Library.open(":memory:") as lib:
        tracks = tracks_in(lib)
    token = CancelToken()
    with pytest.raises(Cancelled) as stopped:
        levels.measure_loudness(tracks, cancel_after(token, 3), workers=2, cancel=token)
    found, _ = stopped.value.partial
    assert 3 <= len(found) < 30


def test_analysing_waveforms_hands_back_what_it_analysed_when_cancelled(monkeypatch):
    monkeypatch.setattr(waves, "analyse", lambda path: (time.sleep(0.01), [5] * 100)[1])
    with Library.open(":memory:") as lib:
        tracks = tracks_in(lib)
    token = CancelToken()
    with pytest.raises(Cancelled) as stopped:
        waves.measure_waveforms(tracks, cancel_after(token, 5), workers=2, cancel=token)
    found, _ = stopped.value.partial
    assert 5 <= len(found) < 30 and all(v == [5] * 100 for v in found.values())


def test_a_job_that_was_not_cancelled_still_returns_everything(monkeypatch):
    monkeypatch.setattr(waves, "analyse", lambda path: [5] * 100)
    with Library.open(":memory:") as lib:
        tracks = tracks_in(lib, 6)
    found, problems = waves.measure_waveforms(tracks, cancel=CancelToken())
    assert len(found) == 6 and problems == []


def test_scanning_a_folder_stops_when_cancelled_and_hands_back_nothing(
    tmp_path, monkeypatch
):
    for n in range(40):
        (tmp_path / f"{n:02d}.mp3").write_bytes(b"x")
    info = render.SourceInfo(Path("x"), 60.0, 44100, 16, "t", "a", "")
    monkeypatch.setattr(folder, "probe", lambda path: (time.sleep(0.01), info)[1])
    token = CancelToken()
    with pytest.raises(Cancelled) as stopped:
        folder.scan_folder(tmp_path, cancel_after(token, 3), workers=2, cancel=token)
    assert stopped.value.partial is None  # (half an import is of no use)


def test_reading_rekordbox_stops_when_already_cancelled(tmp_path, monkeypatch):
    db = tmp_path / "master.db"
    db.write_bytes(b"x")
    monkeypatch.setattr(rekordbox_module, "_snapshot", lambda source, into: source)

    class Rows:
        def __init__(self, path=None):
            pass

        def get_content(self):
            return iter([object()] * 5)

        def close(self):
            pass

    monkeypatch.setattr("pyrekordbox.masterdb.MasterDatabase", Rows)
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        rekordbox_module.read_master_db(db, cancel=token)


# -- the task layer ---------------------------------------------------------------------------------


def test_a_cancelled_task_reaches_on_cancel_with_what_it_had(qapp, wait_for):
    token = CancelToken()
    got = []

    def job(report):
        token.cancel()
        token.check(("half", 2))

    run_async(
        job,
        lambda _r: got.append("done"),
        lambda m: got.append(("failed", m)),
        lambda *_: None,
        got.append,
    )
    assert wait_for(lambda: bool(got))
    assert got == [("half", 2)]


def test_a_cancelled_task_nobody_listens_for_is_reported_as_a_failure(qapp, wait_for):
    got = []

    def job():
        raise Cancelled()

    run_async(job, lambda _r: got.append("done"), got.append)
    assert wait_for(lambda: bool(got))
    assert got == ["Cancelled."]


def test_a_task_from_another_thread_can_be_cancelled_from_the_gui_side(qapp, wait_for):
    token = CancelToken()
    started = threading.Event()
    got = []

    def job():
        started.set()
        while True:
            token.check("stopped")
            time.sleep(0.005)

    run_async(job, None, None, None, got.append)
    assert started.wait(5)
    token.cancel()
    assert wait_for(lambda: bool(got)) and got == ["stopped"]
