"""Tests for /video command and videocfg callbacks."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot
import sessions


@pytest.mark.asyncio
async def test_cmd_video_shows_keyboard(sessions_file):
    msg = MagicMock()
    msg.from_user.id = 7001
    msg.answer = AsyncMock()

    await bot.cmd_video(msg)

    msg.answer.assert_awaited_once()
    kwargs = msg.answer.await_args.kwargs
    assert kwargs.get("reply_markup") is not None
    assert "720p" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_videocfg_duration_persists(sessions_file, monkeypatch):
    uid = 7002
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "videocfg:duration:10"
    callback.message = MagicMock()
    callback.answer = AsyncMock()

    monkeypatch.setattr(bot, "safe_edit_text", AsyncMock())
    await bot.handle_video_config(callback)

    cfg = sessions.get_video_config(uid)
    assert cfg["duration"] == 10


@pytest.mark.asyncio
async def test_videocfg_guard_same_duration(sessions_file, monkeypatch):
    uid = 7003
    sessions.set_video_config(uid, duration=15)
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "videocfg:duration:15"
    callback.message = MagicMock()
    callback.answer = AsyncMock()

    monkeypatch.setattr(bot, "safe_edit_text", AsyncMock())
    await bot.handle_video_config(callback)

    callback.answer.assert_awaited_once()
    assert "Ya está activa" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_videocfg_aspect_persists(sessions_file, monkeypatch):
    uid = 7004
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "videocfg:aspect:9:16"
    callback.message = MagicMock()
    callback.answer = AsyncMock()

    monkeypatch.setattr(bot, "safe_edit_text", AsyncMock())
    await bot.handle_video_config(callback)

    cfg = sessions.get_video_config(uid)
    assert cfg["aspect_ratio"] == "9:16"


@pytest.mark.asyncio
async def test_videocfg_guard_same_aspect(sessions_file, monkeypatch):
    uid = 7005
    sessions.set_video_config(uid, aspect_ratio="16:9")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "videocfg:aspect:16:9"
    callback.message = MagicMock()
    callback.answer = AsyncMock()

    monkeypatch.setattr(bot, "safe_edit_text", AsyncMock())
    await bot.handle_video_config(callback)

    callback.answer.assert_awaited_once()
    assert "Ya está activa" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_videocfg_resolution_persists(sessions_file, monkeypatch):
    uid = 7006
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "videocfg:resolution:480p"
    callback.message = MagicMock()
    callback.answer = AsyncMock()

    monkeypatch.setattr(bot, "safe_edit_text", AsyncMock())
    await bot.handle_video_config(callback)

    cfg = sessions.get_video_config(uid)
    assert cfg["resolution"] == "480p"


@pytest.mark.asyncio
async def test_videocfg_model_persists(sessions_file, monkeypatch):
    uid = 9001
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "videocfg:model:grok-imagine-video-1.5"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    await bot.handle_video_config(callback)

    cfg = sessions.get_video_config(uid)
    assert cfg["model"] == "grok-imagine-video-1.5"


async def test_videocfg_guard_same_model(sessions_file, monkeypatch):
    uid = 9002
    sessions.set_video_config(uid, model="grok-imagine-video")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "videocfg:model:grok-imagine-video"
    callback.message = MagicMock()
    callback.answer = AsyncMock()

    await bot.handle_video_config(callback)

    callback.answer.assert_awaited_once()
    assert "activo" in callback.answer.await_args.args[0].lower()


async def test_videocfg_guard_same_resolution(sessions_file, monkeypatch):
    uid = 7007
    sessions.set_video_config(uid, resolution="720p")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "videocfg:resolution:720p"
    callback.message = MagicMock()
    callback.answer = AsyncMock()

    monkeypatch.setattr(bot, "safe_edit_text", AsyncMock())
    await bot.handle_video_config(callback)

    callback.answer.assert_awaited_once()
    assert "Ya está activa" in callback.answer.await_args.args[0]