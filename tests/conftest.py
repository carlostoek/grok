"""Shared pytest fixtures. Sets dummy env vars before importing bot."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "000000000:TEST-TOKEN-FOR-UNIT-TESTS")
os.environ.setdefault("REPLICATE_API_TOKEN", "r8_test")
os.environ.setdefault("XAI_API_KEY", "xai-test-key")

import pytest  # noqa: E402

import bot  # noqa: E402
import sessions  # noqa: E402


@pytest.fixture(autouse=True)
def reset_runtime_state():
    bot.user_state.clear()
    bot._video_active_jobs.clear()
    bot._video_global_active_count = 0
    bot._video_hourly_pending.clear()
    bot._video_global_hourly_pending = 0
    yield


@pytest.fixture
def sessions_file(tmp_path, monkeypatch):
    path = tmp_path / "sessions.json"
    monkeypatch.setattr(sessions, "SESSIONS_FILE", path)
    return path