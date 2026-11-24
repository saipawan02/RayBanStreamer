import logging
import shutil

import numpy as np
import pytest

from glasses_reader import find_gaps, imu_window, iter_timed_frames, load_audio, open_session
from glasses_reader.backends import backend_names, select_backend
from glasses_reader.timeline import read_video_pts

from .conftest import needs_av

pytestmark = needs_av


def test_single_segment_frames(fake_one):
    root, truth = fake_one
    s = open_session(truth["session_id"], str(root))
    assert len(s.segments) == 1 and s.offsets.source == "anchors"
    frames = list(iter_timed_frames(s, backend="pyav"))
    pts = np.array([f.pts_s for f in frames])
    assert len(frames) == round(truth["total_s"] * truth["fps"])
    assert pts[0] == 0.0 and np.all(np.diff(pts) > 0)
    assert all((f.height, f.width) == (224, 128) for f in frames)
    assert all(f.gap_before_s == 0.0 for f in frames)
    assert frames[0].wall_ms == 1_795_531_201_123


def test_two_segments_gap_is_marked(fake_two):
    root, truth = fake_two
    s = open_session(truth["session_id"], str(root))
    assert len(s.segments) == 2
    frames = list(iter_timed_frames(s, backend="pyav"))
    gaps = [(f.pts_s, f.gap_before_s) for f in frames if f.gap_before_s > 0]
    assert len(gaps) == 1
    pts, gap = gaps[0]
    assert abs(gap - truth["gap_s"]) < 0.05
    assert abs(pts - (8.0 + truth["gap_s"])) < 0.05           # second file starts 12 s after the first
    assert np.all(np.diff([f.pts_s for f in frames]) > 0)
    assert {f.segment for f in frames} == {0, 1}


def test_audio_matches_video_duration_and_gaps_are_not_filled(fake_one, fake_two):
    root, truth = fake_one
    s = open_session(truth["session_id"], str(root))
    x, t = load_audio(s, 16000)
    assert x.dtype == np.float32 and len(x) == len(t)
    assert abs((t[-1] - t[0]) - truth["total_s"]) < 0.5
    assert find_gaps(t, 16000) == []

    root2, truth2 = fake_two
    s2 = open_session(truth2["session_id"], str(root2))
    x2, t2 = load_audio(s2, 16000)
    gaps = find_gaps(t2, 16000, min_gap_s=0.2)
    assert len(gaps) == 1 and abs((gaps[0][1] - gaps[0][0]) - truth2["gap_s"]) < 0.1
    assert len(x2) < (t2[-1] - t2[0]) * 16000                 # silence was not inserted


def test_imu_window_is_sorted_and_on_video_time(fake_one):
    root, truth = fake_one
    s = open_session(truth["session_id"], str(root))
    w = imu_window(s, 5.0, 6.0)
    assert w["t_video_s"].is_monotonic_increasing
    assert w["t_video_s"].min() >= 5.0 and w["t_video_s"].max() < 6.0
    assert 55 <= len(w) <= 65                                  # about 60 Hz for one second


def test_no_segments_yields_nothing_and_says_why(tmp_path, fake_one, caplog):
    root, truth = fake_one
    shutil.copytree(root / "data", tmp_path / "data")           # IMU and events, no recordings
    s = open_session(truth["session_id"], str(tmp_path))
    with caplog.at_level(logging.WARNING, logger="glasses_reader"):
        assert list(iter_timed_frames(s)) == []
    assert "no recording segments" in caplog.text
    x, t = load_audio(s)
    assert len(x) == 0 and len(t) == 0 and len(s.imu) > 0


def test_truncated_final_segment_decodes_what_exists(tmp_path, fake_one):
    root, truth = fake_one
    shutil.copytree(root, tmp_path / "r")
    seg = next((tmp_path / "r" / "recordings" / "glasses").glob("*.mp4"))
    data = seg.read_bytes()
    seg.write_bytes(data[: len(data) // 2])                     # chop mid-file
    s = open_session(truth["session_id"], str(tmp_path / "r"))
    n = len(list(iter_timed_frames(s, backend="pyav")))
    assert 0 < n < int(truth["total_s"] * truth["fps"])
    assert 0 < len(read_video_pts(seg))


def test_backend_registry():
    assert backend_names() == ["ffmpeg", "pyav"]
    assert select_backend("pyav").name == "pyav"
    with pytest.raises(ValueError):
        select_backend("nope")


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg/ffprobe not installed (run this on the GPU server)")
def test_ffmpeg_backend_matches_pyav_reference(fake_one):
    root, truth = fake_one
    s = open_session(truth["session_id"], str(root))
    ref = [f for _, f in zip(range(100), select_backend("pyav").decode(s.segments[0]), strict=False)]
    got = [f for _, f in zip(range(100), select_backend("ffmpeg").decode(s.segments[0]), strict=False)]
    assert len(ref) == len(got)
    for a, b in zip(ref, got, strict=True):
        assert abs(a.pts_s - b.pts_s) < 0.001
        assert np.abs(a.image.astype(int) - b.image.astype(int)).mean() < 3
