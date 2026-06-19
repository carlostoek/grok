"""Tests for video rate limiting and _do_generate_video error paths."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aioresponses import aioresponses

import bot
import sessions

MODEL = bot.MODELS["grok_video"]
GEN_URL = "https://api.x.ai/v1/videos/generations"
POLL_URL = "https://api.x.ai/v1/videos/req-rate"
VIDEO_URL = "https://cdn.x.ai/rate.mp4"


def _make_message(user_id=6001):
    msg = MagicMock()
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    msg.edit_text = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_concurrency_blocks_second_job():
    uid = 6001
    bot._video_active_jobs.add(uid)
    msg = _make_message(uid)
    status = MagicMock()
    status.edit_text = AsyncMock()

    await bot._do_generate_video(
        msg,
        MODEL,
        "prompt",
        user_id=uid,
        status_msg=status,
        reply_message=msg,
    )

    status.edit_text.assert_awaited_once()
    assert "en curso" in status.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_hourly_limit_blocks_request(sessions_file):
    uid = 6002
    now = 1_000_000.0
    sessions_file.write_text(
        json.dumps(
            {
                "6002": {
                    "video_hourly_timestamps": [now - i * 100 for i in range(bot.VIDEO_MAX_PER_HOUR)],
                }
            }
        )
    )
    msg = _make_message(uid)
    status = MagicMock()
    status.edit_text = AsyncMock()

    with patch("sessions.time.time", return_value=now):
        await bot._do_generate_video(
            msg,
            MODEL,
            "prompt",
            user_id=uid,
            status_msg=status,
            reply_message=msg,
        )

    status.edit_text.assert_awaited_once()
    assert "límite" in status.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_oversized_image_does_not_consume_hourly_quota(sessions_file):
    uid = 6003
    msg = _make_message(uid)
    status = MagicMock()
    status.edit_text = AsyncMock()
    big = BytesIO(b"x" * (bot.I2V_MAX_IMAGE_BYTES + 1))

    await bot._do_generate_video(
        msg,
        MODEL,
        "prompt",
        image_data=big,
        user_id=uid,
        status_msg=status,
        reply_message=msg,
    )

    assert sessions.count_video_hourly_usage(uid) == 0
    status.edit_text.assert_awaited_once()
    assert "demasiado grande" in status.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_generate_video_error_releases_concurrency():
    uid = 6004
    msg = _make_message(uid)
    status = MagicMock()
    status.edit_text = AsyncMock()

    with patch.object(
        bot,
        "generate_video",
        new_callable=AsyncMock,
        return_value=(None, "falló la generación"),
    ):
        await bot._do_generate_video(
            msg,
            MODEL,
            "prompt",
            user_id=uid,
            status_msg=status,
            reply_message=msg,
        )

    assert uid not in bot._video_active_jobs
    status.edit_text.assert_awaited_once_with("falló la generación")


@pytest.mark.asyncio
async def test_successful_flow_records_hourly_after_post(sessions_file):
    uid = 6005
    msg = _make_message(uid)
    status = MagicMock()
    status.edit_text = AsyncMock()
    status.delete = AsyncMock()
    msg.answer_video = AsyncMock()
    msg.delete = status.delete

    async def noop_sleep(_seconds):
        return None

    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-rate"})
        mocked.get(
            POLL_URL,
            payload={
                "status": "done",
                "video": {"url": VIDEO_URL, "respect_moderation": True},
            },
        )
        mocked.get(VIDEO_URL, body=b"\x00\x00\x00\x18ftypmp42")
        with patch("bot.asyncio.sleep", new=noop_sleep):
            await bot._do_generate_video(
                msg,
                MODEL,
                "prompt",
                user_id=uid,
                status_msg=status,
                reply_message=msg,
            )

    assert sessions.count_video_hourly_usage(uid) == 1
    assert uid not in bot._video_active_jobs
    assert bot._video_global_active_count == 0