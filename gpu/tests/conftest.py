from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))

HAS_AV = importlib.util.find_spec("av") is not None
needs_av = pytest.mark.skipif(not HAS_AV, reason="PyAV not installed")


@pytest.fixture(scope="session")
def fake_one(tmp_path_factory):
    """One segment, with audio. Built once: encoding takes a few seconds."""
    if not HAS_AV:
        pytest.skip("PyAV not installed")
    from fake_session import make_fake_session

    root = tmp_path_factory.mktemp("fake1")
    truth = make_fake_session(root)
    return root, truth


@pytest.fixture(scope="session")
def fake_two(tmp_path_factory):
    """Two segments with a 4 s wall-clock gap between them."""
    if not HAS_AV:
        pytest.skip("PyAV not installed")
    from fake_session import make_fake_session

    root = tmp_path_factory.mktemp("fake2")
    truth = make_fake_session(root, segments=2, gap_s=4.0)
    return root, truth
