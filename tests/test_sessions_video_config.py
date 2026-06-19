"""Tests for persisted video configuration in sessions.py."""

from __future__ import annotations

import json

import sessions


def test_get_video_config_defaults(sessions_file):
    cfg = sessions.get_video_config(999)
    assert cfg["model"] == sessions.DEFAULT_VIDEO_MODEL
    assert cfg == {
        "duration": 5,
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "model": sessions.DEFAULT_VIDEO_MODEL,
    }


def test_get_video_config_invalid_model_falls_back(sessions_file):
    sessions_file.write_text(
        json.dumps(
            {
                "999": {
                    "video_duration": 5,
                    "video_aspect_ratio": "16:9",
                    "video_resolution": "720p",
                    "video_model": "grok-imagine-video-99",
                }
            }
        )
    )
    cfg = sessions.get_video_config(999)
    assert cfg["model"] == sessions.DEFAULT_VIDEO_MODEL


def test_set_video_config_persists_model(sessions_file):
    sessions.set_video_config(777, model="grok-imagine-video-1.5")
    assert sessions.get_video_config(777)["model"] == "grok-imagine-video-1.5"


def test_get_video_config_invalid_duration_falls_back(sessions_file):
    sessions_file.write_text(
        json.dumps({"999": {"video_duration": 99, "video_aspect_ratio": "16:9", "video_resolution": "720p"}})
    )
    cfg = sessions.get_video_config(999)
    assert cfg["duration"] == sessions.DEFAULT_VIDEO_DURATION


def test_get_video_config_invalid_aspect_ratio_falls_back(sessions_file):
    sessions_file.write_text(
        json.dumps({"999": {"video_duration": 5, "video_aspect_ratio": "99:1", "video_resolution": "720p"}})
    )
    cfg = sessions.get_video_config(999)
    assert cfg["aspect_ratio"] == sessions.DEFAULT_VIDEO_ASPECT_RATIO


def test_get_video_config_invalid_resolution_falls_back(sessions_file):
    sessions_file.write_text(
        json.dumps({"999": {"video_duration": 5, "video_aspect_ratio": "16:9", "video_resolution": "8K"}})
    )
    cfg = sessions.get_video_config(999)
    assert cfg["resolution"] == sessions.DEFAULT_VIDEO_RESOLUTION


def test_get_video_config_1080p_falls_back_to_default(sessions_file):
    sessions_file.write_text(
        json.dumps({"999": {"video_duration": 5, "video_aspect_ratio": "16:9", "video_resolution": "1080p"}})
    )
    cfg = sessions.get_video_config(999)
    assert cfg["resolution"] == sessions.DEFAULT_VIDEO_RESOLUTION


def test_get_video_config_accepts_3_2_and_2_3_aspect_ratios(sessions_file):
    sessions_file.write_text(
        json.dumps({"999": {"video_duration": 5, "video_aspect_ratio": "3:2", "video_resolution": "720p"}})
    )
    assert sessions.get_video_config(999)["aspect_ratio"] == "3:2"

    sessions_file.write_text(
        json.dumps({"999": {"video_duration": 5, "video_aspect_ratio": "2:3", "video_resolution": "720p"}})
    )
    assert sessions.get_video_config(999)["aspect_ratio"] == "2:3"


def test_ensure_full_migrates_missing_video_fields(sessions_file):
    sessions_file.write_text(json.dumps({"999": {"model": "grok"}}))
    rec = sessions.get_session(999)
    assert rec["video_duration"] == sessions.DEFAULT_VIDEO_DURATION
    assert rec["video_aspect_ratio"] == sessions.DEFAULT_VIDEO_ASPECT_RATIO
    assert rec["video_resolution"] == sessions.DEFAULT_VIDEO_RESOLUTION
    assert rec["video_model"] == sessions.DEFAULT_VIDEO_MODEL


def test_set_video_config_persists_fields(sessions_file):
    sessions.set_video_config(555, duration=15, aspect_ratio="3:2", resolution="480p")
    cfg = sessions.get_video_config(555)
    assert cfg == {
        "duration": 15,
        "aspect_ratio": "3:2",
        "resolution": "480p",
        "model": sessions.DEFAULT_VIDEO_MODEL,
    }


def test_record_video_hourly_usage_persists(sessions_file):
    from unittest.mock import patch

    uid = 556
    now = 3_000_000.0
    with patch("sessions.time.time", return_value=now):
        sessions.record_video_hourly_usage(uid)
    data = json.loads(sessions_file.read_text())
    assert len(data["556"]["video_hourly_timestamps"]) == 1


def test_set_model_new_record_includes_video_defaults(sessions_file):
    sessions.set_model(123, "grok_video")
    data = json.loads(sessions_file.read_text())
    rec = data["123"]
    assert rec["video_duration"] == sessions.DEFAULT_VIDEO_DURATION
    assert rec["video_aspect_ratio"] == sessions.DEFAULT_VIDEO_ASPECT_RATIO
    assert rec["video_resolution"] == sessions.DEFAULT_VIDEO_RESOLUTION