"""Mocked end-to-end test: confirm → generate → poll → deliver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aioresponses import aioresponses

import bot

GEN_URL = "https://api.x.ai/v1/videos/generations"
POLL_URL = "https://api.x.ai/v1/videos/req-e2e"
VIDEO_URL = "https://cdn.x.ai/e2e-video.mp4"
VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 128


@pytest.mark.asyncio
async def test_confirm_to_delivery_chain(sessions_file):
    user_id = 4242
    prompt = "a cat lounging in a sunbeam"
    callback = MagicMock()
    callback.from_user.id = user_id
    callback.data = "confirm:yes"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.message.answer_video = AsyncMock()
    callback.message.delete = AsyncMock()
    callback.answer = AsyncMock()

    bot.get_user_state(user_id)["model"] = "grok_video"
    bot.get_user_state(user_id)["pending_prompt"] = prompt

    async def noop_sleep(_seconds):
        return None

    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-e2e"})
        mocked.get(
            POLL_URL,
            payload={
                "status": "done",
                "video": {"url": VIDEO_URL, "respect_moderation": True},
            },
        )
        mocked.get(VIDEO_URL, body=VIDEO_BYTES)

        with patch("bot.asyncio.sleep", new=noop_sleep):
            with patch.object(bot, "safe_edit_text", new_callable=AsyncMock):
                await bot.handle_confirm_generation(callback)

    callback.message.answer_video.assert_awaited_once()
    callback.message.delete.assert_awaited_once()
    sent = callback.message.answer_video.await_args.args[0]
    assert sent.filename == "generated.mp4"