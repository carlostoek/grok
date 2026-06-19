"""Tests for process_video_result delivery and error paths."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest

import bot

VIDEO_URL = "https://cdn.x.ai/generated.mp4"
VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 128


def _make_messages():
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    status_msg.delete = AsyncMock()
    message = MagicMock()
    message.answer_video = AsyncMock()
    return status_msg, message


@pytest.mark.asyncio
async def test_empty_url_shows_error():
    status_msg, message = _make_messages()
    await bot.process_video_result("", "prompt", status_msg, message, "Prompt")
    status_msg.edit_text.assert_awaited_once()
    assert "URL" in status_msg.edit_text.await_args.args[0]
    message.answer_video.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_failure_shows_error():
    status_msg, message = _make_messages()
    with patch.object(bot, "download_url", new_callable=AsyncMock, return_value=(None, "falló la descarga")):
        await bot.process_video_result(VIDEO_URL, "prompt", status_msg, message, "Prompt")

    status_msg.edit_text.assert_awaited_once_with("falló la descarga")
    message.answer_video.assert_not_awaited()


@pytest.mark.asyncio
async def test_oversized_video_sends_url_fallback():
    status_msg, message = _make_messages()
    huge = b"x" * (bot.TELEGRAM_MAX_VIDEO_BYTES + 1)
    with patch.object(bot, "download_url", new_callable=AsyncMock, return_value=(huge, None)):
        await bot.process_video_result(VIDEO_URL, "prompt", status_msg, message, "Prompt")

    status_msg.edit_text.assert_awaited_once()
    text = status_msg.edit_text.await_args.args[0]
    assert "demasiado grande" in text
    assert VIDEO_URL in text
    message.answer_video.assert_not_awaited()


@pytest.mark.asyncio
async def test_success_sends_video_and_deletes_status():
    status_msg, message = _make_messages()
    with patch.object(bot, "download_url", new_callable=AsyncMock, return_value=(VIDEO_BYTES, None)):
        await bot.process_video_result(VIDEO_URL, "a prompt", status_msg, message, "Prompt")

    message.answer_video.assert_awaited_once()
    status_msg.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_telegram_send_failure_falls_back_to_url():
    status_msg, message = _make_messages()
    message.answer_video.side_effect = TelegramBadRequest(
        method="sendVideo",
        message="file too large",
    )
    with patch.object(bot, "download_url", new_callable=AsyncMock, return_value=(VIDEO_BYTES, None)):
        await bot.process_video_result(VIDEO_URL, "prompt", status_msg, message, "Prompt")

    status_msg.edit_text.assert_awaited_once()
    text = status_msg.edit_text.await_args.args[0]
    assert "Telegram" in text
    assert VIDEO_URL in text
    status_msg.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_url_blocks_redirect_to_bad_host():
    class FakeResponse:
        status = 200
        url = "https://evil.example.com/video.mp4"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        @property
        def content(self):
            return self

        def iter_chunked(self, _size):
            async def _gen():
                yield b"data"

            return _gen()

    class FakeSession:
        def get(self, *args, **kwargs):
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    with patch("bot.aiohttp.ClientSession", return_value=FakeSession()):
        data, err = await bot.download_url(
            "https://cdn.x.ai/video.mp4",
            enforce_host_allowlist=True,
        )

    assert data is None
    assert "origen no permitido" in err