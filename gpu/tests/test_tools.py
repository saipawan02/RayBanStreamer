"""CLI tools against fake sessions."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .conftest import needs_av

TOOLS = Path(__file__).resolve().parents[2] / "tools"
pytestmark = needs_av


def run(tool, *args):
    return subprocess.run([sys.executable, str(TOOLS / tool), *map(str, args)], capture_output=True,
                          text=True, check=False)


def test_check_session_passes_on_clean_fake_and_warns_uncalibrated(fake_one):
    root, truth = fake_one
    r = run("check_session.py", truth["session_id"], "--data-root", root, "--json")
    rep = json.loads(r.stdout)
    assert r.returncode == 0, r.stdout + r.stderr
    assert rep["audio_present"] and rep["imu_rows"] == 840 and len(rep["video_segments"]) == 1
    assert 22 <= rep["video_fps_mean"] <= 26 and 55 <= rep["imu_rate_mean_hz"] <= 65
    assert any("not calibrated" in w for w in rep["warnings"])


def test_check_session_flags_audio_gap_without_explaining_event(fake_two):
    root, truth = fake_two
    r = run("check_session.py", truth["session_id"], "--data-root", root, "--json")
    rep = json.loads(r.stdout)
    assert r.returncode == 1 and len(rep["audio_gaps"]) == 1 and len(rep["video_gaps"]) == 1
    assert any("audio gap" in f for f in rep["failures"])


def test_check_session_reports_missing_video(tmp_path, fake_one):
    root, truth = fake_one
    shutil.copytree(root / "data", tmp_path / "data")
    r = run("check_session.py", truth["session_id"], "--data-root", tmp_path, "--json")
    rep = json.loads(r.stdout)
    assert r.returncode == 1 and any("video missing" in f for f in rep["failures"])


def test_estimate_offset_writes_calibration_then_check_passes(tmp_path, fake_one):
    root, truth = fake_one
    shutil.copytree(root, tmp_path / "r")
    r = run("estimate_offset.py", truth["session_id"], "--data-root", tmp_path / "r", "--write")
    assert r.returncode == 0, r.stdout + r.stderr
    meta = json.loads((tmp_path / "r" / "data" / truth["session_id"] / "session.json").read_text())
    cal = meta["calibration"]
    assert abs(cal["imu_to_video_ms"] - 120) < 40 and abs(cal["audio_to_video_ms"] + 30) < 40
    assert cal["spread_ms"] < 50 and cal["method"] == "tap_v1"
    rep = json.loads(run("check_session.py", truth["session_id"], "--data-root", tmp_path / "r",
                         "--json").stdout)
    assert rep["calibration"]["offsets_source"] == "tap_v1" and not rep["failures"]
