"""Tests for command handlers (/start, /estado)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import bot
import sessions


@pytest.mark.asyncio
async def test_cmd_start_mentions_video_mode_and_config(sessions_file):
    msg = MagicMock()
    msg.from_user.id = 8001
    msg.answer = AsyncMock()
    bot.get_user_state(8001)["model"] = "grok_video"

    await bot.cmd_start(msg)

    text = msg.answer.await_args.args[0]
    assert "video" in text.lower()
    assert "/config" in text


@pytest.mark.asyncio
async def test_cmd_model_alias_matches_config(sessions_file):
    msg = MagicMock()
    msg.from_user.id = 8004
    msg.answer = AsyncMock()
    state = MagicMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()

    await bot.cmd_model(msg, state)

    msg.answer.assert_awaited_once()
    assert "Selecciona el modelo" in msg.answer.await_args.args[0]


def test_is_bot_command_message_detects_slash_commands():
    msg = MagicMock()
    msg.text = "/config"
    msg.entities = [MagicMock(type="bot_command", offset=0)]
    assert bot._is_bot_command_message(msg) is True
    assert bot._is_generation_prompt_message(msg) is False


def test_is_generation_prompt_message_allows_plain_text():
    msg = MagicMock()
    msg.text = "a cat in a hat"
    msg.reply_to_message = None
    msg.entities = []
    assert bot._is_bot_command_message(msg) is False
    assert bot._is_generation_prompt_message(msg) is True


@pytest.mark.asyncio
async def test_handle_text_ignores_config_command(sessions_file):
    msg = MagicMock()
    msg.from_user.id = 8010
    msg.text = "/config"
    msg.reply_to_message = None
    msg.entities = [MagicMock(type="bot_command", offset=0)]
    msg.answer = AsyncMock()

    state = bot.get_user_state(8010)
    state["model"] = "grok"

    await bot.handle_text(msg)

    msg.answer.assert_not_awaited()
    assert state.get("pending_prompt") is None


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