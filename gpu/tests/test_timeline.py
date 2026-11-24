import logging

from glasses_reader.timeline import FrameClock


def _clock(fps=24.0):
    c = FrameClock(fps, first_segment_start_ms=1_000_000)
    c.new_segment(1_000_000)
    return c


def test_first_frame_is_zero_and_contiguous():
    c = _clock()
    out = [c.stamp(100.0 + i / 24) for i in range(5)]       # container clock need not start at 0
    assert out[0] == (0.0, 1_000_000, 0.0)
    assert [round(o[0], 6) for o in out] == [round(i / 24, 6) for i in range(5)]
    assert all(o[2] == 0.0 for o in out)
    assert out[3][1] == 1_000_000 + round(3 / 24 * 1000)


def test_gap_inside_segment_reports_missing_time():
    c = _clock()
    c.stamp(0.0)
    c.stamp(1 / 24)
    _, _, gap = c.stamp(1 / 24 + 1.0)                       # 1 s hole
    assert abs(gap - (1.0 - 1 / 24)) < 1e-9


def test_small_jitter_is_not_a_gap():
    c = _clock()
    c.stamp(0.0)
    assert c.stamp(2.9 / 24)[2] == 0.0                      # under 3 frame intervals
    assert c.stamp(2.9 / 24 + 3.5 / 24)[2] > 0.0


def test_gap_across_segment_boundary_uses_wall_clock_names():
    c = _clock()
    for i in range(24):
        c.stamp(i / 24)                                     # last frame at 23/24 s
    c.new_segment(1_000_000 + 5000)                         # next file starts 5 s later
    pts, wall, gap = c.stamp(0.0)
    assert pts == 5.0 and wall == 1_005_000
    assert abs(gap - (5.0 - 23 / 24 - 1 / 24)) < 1e-9


def test_back_to_back_segments_have_no_gap():
    c = _clock()
    for i in range(24):
        c.stamp(i / 24)
    c.new_segment(1_000_000 + 1000)                         # exactly one second later
    assert c.stamp(7.0)[2] == 0.0


def test_overlap_is_logged(caplog):
    c = _clock()
    for i in range(24):
        c.stamp(i / 24)
    c.new_segment(1_000_000 + 500)                          # starts before the previous ended
    with caplog.at_level(logging.WARNING, logger="glasses_reader"):
        c.stamp(0.0)
    assert "overlap" in caplog.text
