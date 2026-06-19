"""Tests for command handlers (/start, /estado)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import bot
import sessions


@pytest.mark.asyncio
async def test_cmd_start_mentions_video_mode(sessions_file):
    msg = MagicMock()
    msg.from_user.id = 8001
    msg.answer = AsyncMock()
    bot.get_user_state(8001)["model"] = "grok_video"

    await bot.cmd_start(msg)

    text = msg.answer.await_args.args[0]
    assert "video" in text.lower()


@pytest.mark.asyncio
async def test_cmd_estado_shows_video_config(sessions_file):
    uid = 8002
    sessions.set_video_config(uid, duration=10, aspect_ratio="9:16", resolution="480p")
    bot.get_user_state(uid)["model"] = "grok_video"

    msg = MagicMock()
    msg.from_user.id = uid
    msg.answer = AsyncMock()

    await bot.cmd_estado(msg)

    text = msg.answer.await_args.args[0]
    assert "10s" in text
    assert "9:16" in text
    assert "480p" in text