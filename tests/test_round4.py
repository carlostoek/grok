"""Round 4 tests: allowlist middleware, global hourly cap, image helper."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot
import sessions

MODEL = bot.MODELS["grok_video"]


def test_image_to_data_uri_roundtrip():
    data = BytesIO(b"jpeg-bytes")
    uri = bot._image_to_data_uri(data)
    assert uri.startswith("data:image/jpeg;base64,")
    assert "jpeg-bytes" not in uri


@pytest.mark.asyncio
async def test_allowlist_middleware_blocks_photo_handler(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_TELEGRAM_IDS", {111})
    msg = MagicMock()
    msg.from_user.id = 222
    msg.caption = None
    msg.media_group_id = None
    msg.photo = [MagicMock()]
    msg.answer = AsyncMock()
    bot.get_user_state(222)["model"] = "grok_video"

    middleware = bot.AllowlistMiddleware()
    handler = AsyncMock()
    await middleware(handler, msg, {})

    handler.assert_not_awaited()
    msg.answer.assert_awaited_once()
    assert "permiso" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_global_hourly_limit_blocks_request(sessions_file, monkeypatch):
    monkeypatch.setattr(bot, "VIDEO_MAX_GLOBAL_HOURLY", 2)
    now = 4_000_000.0
    sessions_file.write_text(
        json.dumps(
            {
                "1": {"video_hourly_timestamps": [now - 10]},
                "2": {"video_hourly_timestamps": [now - 20]},
            }
        )
    )
    uid = 3
    msg = MagicMock()
    status = MagicMock()
    status.edit_text = AsyncMock()

    monkeypatch.setattr(sessions.time, "time", lambda: now)
    await bot._do_generate_video(
        msg,
        MODEL,
        "prompt",
        user_id=uid,
        status_msg=status,
        reply_message=msg,
    )

    assert "global" in status.edit_text.await_args.args[0].lower()


def test_count_global_video_hourly_usage(sessions_file):
    now = 5_000_000.0
    sessions_file.write_text(
        json.dumps(
            {
                "10": {"video_hourly_timestamps": [now - 5, now - 10]},
                "11": {"video_hourly_timestamps": [now - 15]},
            }
        )
    )
    with __import__("unittest").mock.patch("sessions.time.time", return_value=now):
        assert sessions.count_global_video_hourly_usage() == 3