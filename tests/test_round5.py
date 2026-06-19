"""Round 5 tests: final cleanup coverage gaps."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aioresponses import aioresponses

import bot
import sessions

MODEL = bot.MODELS["grok_video"]
GROK_MODEL = bot.get_model(1)
GEN_URL = "https://api.x.ai/v1/videos/generations"
POLL_URL = "https://api.x.ai/v1/videos/req-r5"
VIDEO_URL = "https://cdn.x.ai/r5.mp4"
VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 128


@pytest.fixture
def no_sleep():
    async def _noop(_seconds):
        return None

    with patch("bot.asyncio.sleep", new=_noop):
        yield


@pytest.mark.asyncio
async def test_switch_to_grok_video_shows_copy(sessions_file):
    callback = MagicMock()
    callback.from_user.id = 5101
    callback.data = "model:grok_video"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    bot.get_user_state(5101)["model"] = "grok"

    with patch.object(bot, "safe_edit_text", new_callable=AsyncMock) as safe_edit:
        await bot.handle_model_selection(callback)

    text = safe_edit.await_args.args[1]
    assert "Grok Imagine Video" in text
    assert "prompt para generar un video" in text
    assert "foto con caption" in text
    assert "imagen a video" in text


@pytest.mark.asyncio
async def test_confirm_e2e_poll_failure_shows_error(sessions_file, no_sleep):
    user_id = 5102
    prompt = "a robot dancing in neon rain"
    callback = MagicMock()
    callback.from_user.id = user_id
    callback.data = "confirm:yes"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    bot.get_user_state(user_id)["model"] = "grok_video"
    bot.get_user_state(user_id)["pending_prompt"] = prompt

    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-r5"})
        mocked.get(
            POLL_URL,
            payload={"status": "failed", "error": {"message": "internal"}},
        )
        with patch.object(bot, "safe_edit_text", new_callable=AsyncMock):
            await bot.handle_confirm_generation(callback)

    callback.message.edit_text.assert_awaited()
    err_text = callback.message.edit_text.await_args.args[0]
    assert err_text == bot._xai_user_error("generación de video (failed)")


@pytest.mark.asyncio
async def test_reply_to_photo_e2e_delivery(sessions_file, no_sleep):
    user_id = 5103
    photo = [MagicMock(file_id="reply-photo")]
    replied = MagicMock()
    replied.photo = photo
    msg = MagicMock()
    msg.from_user.id = user_id
    msg.text = "slowly zoom out while clouds drift"
    msg.reply_to_message = replied
    msg.answer = AsyncMock()
    msg.answer_video = AsyncMock()
    bot.get_user_state(user_id)["model"] = "grok_video"

    with patch.object(bot, "_download_telegram_photo", new_callable=AsyncMock, return_value=BytesIO(b"jpeg")):
        with aioresponses() as mocked:
            mocked.post(GEN_URL, payload={"request_id": "req-r5"})
            mocked.get(
                POLL_URL,
                payload={
                    "status": "done",
                    "video": {"url": VIDEO_URL, "respect_moderation": True},
                },
            )
            mocked.get(VIDEO_URL, body=VIDEO_BYTES)
            await bot.handle_reply_edit(msg)

    msg.answer_video.assert_awaited_once()


@pytest.mark.asyncio
async def test_respect_moderation_false_at_root_level(no_sleep):
    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-r5"})
        mocked.get(
            POLL_URL,
            payload={
                "status": "done",
                "respect_moderation": False,
                "video": {"url": VIDEO_URL},
            },
        )
        url, err = await bot._generate_xai_video(MODEL, "prompt")

    assert url is None
    assert err == "El contenido no cumple las políticas de moderación."


@pytest.mark.asyncio
async def test_status_dedup_skips_redundant_safe_edit(no_sleep):
    status_msg = AsyncMock()

    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-r5"})
        mocked.get(POLL_URL, payload={"status": "processing"})
        mocked.get(POLL_URL, payload={"status": "processing"})
        mocked.get(
            POLL_URL,
            payload={
                "status": "done",
                "video": {"url": VIDEO_URL, "respect_moderation": True},
            },
        )
        with patch("bot.safe_edit_text", new_callable=AsyncMock) as safe_edit:
            await bot._generate_xai_video(MODEL, "prompt", status_msg=status_msg)

    processing_edits = [
        call
        for call in safe_edit.await_args_list
        if "procesando" in call.args[1]
    ]
    assert len(processing_edits) == 1


@pytest.mark.asyncio
async def test_confirm_cancel_does_not_invoke_do_generate_video():
    callback = MagicMock()
    callback.from_user.id = 5104
    callback.data = "confirm:no"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    bot.get_user_state(5104)["pending_prompt"] = "should not run"
    bot.get_user_state(5104)["model"] = "grok_video"

    with patch.object(bot, "_do_generate_video", new_callable=AsyncMock) as mock_gen:
        await bot.handle_confirm_generation(callback)

    mock_gen.assert_not_awaited()
    assert bot.get_user_state(5104)["pending_prompt"] is None


@pytest.mark.asyncio
async def test_user_id_none_uses_default_video_config(no_sleep):
    captured: dict = {}

    def capture_post(url, **kwargs):
        captured["body"] = kwargs["json"]

    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-r5"}, callback=capture_post)
        mocked.get(
            POLL_URL,
            payload={
                "status": "done",
                "video": {"url": VIDEO_URL, "respect_moderation": True},
            },
        )
        await bot._generate_xai_video(MODEL, "prompt", user_id=None)

    assert captured["body"]["duration"] == sessions.DEFAULT_VIDEO_DURATION
    assert captured["body"]["aspect_ratio"] == sessions.DEFAULT_VIDEO_ASPECT_RATIO
    assert captured["body"]["resolution"] == sessions.DEFAULT_VIDEO_RESOLUTION


@pytest.mark.asyncio
async def test_failed_post_does_not_consume_hourly_quota(sessions_file, no_sleep):
    uid = 5105
    msg = MagicMock()
    msg.from_user.id = uid
    status = MagicMock()
    status.edit_text = AsyncMock()

    with aioresponses() as mocked:
        mocked.post(GEN_URL, status=500, body="fail")
        await bot._do_generate_video(
            msg,
            MODEL,
            "prompt",
            user_id=uid,
            status_msg=status,
            reply_message=msg,
        )

    assert sessions.count_video_hourly_usage(uid) == 0
    assert bot._video_hourly_pending.get(uid, 0) == 0


@pytest.mark.asyncio
async def test_image_edit_rejects_oversized_image():
    big = BytesIO(b"x" * (bot.I2V_MAX_IMAGE_BYTES + 1))
    model = {**bot.MODELS["grok"], "provider": "xai"}

    url, err = await bot._generate_xai(model, "edit this", image_data=big)

    assert url is None
    assert "demasiado grande" in err


@pytest.mark.asyncio
async def test_hourly_reservation_blocks_race_at_limit(sessions_file, monkeypatch):
    """Second in-flight reservation fails once the per-user cap is fully reserved."""
    uid = 5106
    now = 2_000_000.0
    monkeypatch.setattr(bot, "VIDEO_MAX_PER_HOUR", 1)
    sessions_file.write_text(json.dumps({"5106": {"video_hourly_timestamps": []}}))

    with patch("sessions.time.time", return_value=now):
        first_err = await bot._reserve_video_hourly_quota(uid)
        second_err = await bot._reserve_video_hourly_quota(uid)

    assert first_err is None
    assert second_err is not None
    assert "límite" in second_err
    bot._cancel_video_hourly_reservation(uid)