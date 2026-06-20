"""Additional handler and integration tests."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aioresponses import aioresponses

import bot
import sessions

MODEL = bot.MODELS["grok_video"]
GEN_URL = "https://api.x.ai/v1/videos/generations"
POLL_URL = "https://api.x.ai/v1/videos/req-i2v"
VIDEO_URL = "https://cdn.x.ai/i2v.mp4"


@pytest.mark.asyncio
async def test_photo_no_caption_grok_video_help():
    msg = MagicMock()
    msg.from_user.id = 9001
    msg.caption = None
    msg.media_group_id = None
    msg.photo = [MagicMock()]
    msg.answer = AsyncMock()
    bot.get_user_state(9001)["model"] = "grok_video"

    await bot.handle_photo_no_caption(msg)

    text = msg.answer.await_args.args[0]
    assert "caption" in text.lower()
    assert "imagen a video" in text.lower()


@pytest.mark.asyncio
async def test_text_prompt_too_long_rejected():
    msg = MagicMock()
    msg.from_user.id = 9002
    msg.text = "x" * (bot.MAX_PROMPT_LEN + 5)
    msg.reply_to_message = None
    msg.answer = AsyncMock()
    bot.get_user_state(9002)["model"] = "grok_video"

    await bot.handle_text(msg)

    assert str(bot.MAX_PROMPT_LEN) in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_do_generate_video_exception_handler():
    uid = 9003
    msg = MagicMock()
    status = MagicMock()
    status.edit_text = AsyncMock()

    with patch.object(bot, "generate_video", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        await bot._do_generate_video(
            msg,
            MODEL,
            "prompt",
            user_id=uid,
            status_msg=status,
            reply_message=msg,
        )

    status.edit_text.assert_awaited_with("Error inesperado. Intenta de nuevo.")


@pytest.mark.asyncio
async def test_e2e_i2v_photo_caption(sessions_file):
    msg = MagicMock()
    msg.from_user.id = 9004
    msg.caption = "make the waves move gently"
    msg.photo = [MagicMock(file_id="p1")]
    msg.answer = AsyncMock()
    msg.answer_video = AsyncMock()
    sessions.set_grok_imagine_config(9004, "xai", "quality")
    bot.get_user_state(9004)["model"] = "grok_video"

    image_bytes = MagicMock()
    image_bytes.read.return_value = b"jpeg"
    image_bytes.seek = MagicMock()

    async def noop_sleep(_seconds):
        return None

    with patch.object(bot, "_download_telegram_photo", new_callable=AsyncMock, return_value=BytesIO(b"jpeg")):
        with aioresponses() as mocked:
            mocked.post(GEN_URL, payload={"request_id": "req-i2v"})
            mocked.get(
                POLL_URL,
                payload={
                    "status": "done",
                    "video": {"url": VIDEO_URL, "respect_moderation": True},
                },
            )
            mocked.get(VIDEO_URL, body=b"\x00\x00\x00\x18ftypmp42")
            with patch("bot.asyncio.sleep", new=noop_sleep):
                await bot.handle_photo_caption(msg)

    msg.answer_video.assert_awaited_once()


@pytest.mark.asyncio
async def test_e2e_i2v_api_error_shows_message(sessions_file):
    msg = MagicMock()
    msg.from_user.id = 9005
    msg.caption = "animate this scene nicely"
    msg.photo = [MagicMock(file_id="p2")]
    status = MagicMock()
    status.edit_text = AsyncMock()
    msg.answer = AsyncMock(return_value=status)
    sessions.set_grok_imagine_config(9005, "xai", "quality")
    bot.get_user_state(9005)["model"] = "grok_video"

    with patch.object(bot, "_download_telegram_photo", new_callable=AsyncMock, return_value=BytesIO(b"jpeg")):
        with aioresponses() as mocked:
            mocked.post(GEN_URL, status=500, body="fail")
            await bot.handle_photo_caption(msg)

    status.edit_text.assert_awaited()
    assert "generación de video" in status.edit_text.await_args.args[0]