"""Security-related tests: allowlist, prompt limits, logging."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import types

import bot


@pytest.mark.asyncio
async def test_allowlist_blocks_via_middleware(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_TELEGRAM_IDS", {111})
    msg = MagicMock()
    msg.from_user.id = 222
    msg.answer = AsyncMock()

    middleware = bot.AllowlistMiddleware()
    handler = AsyncMock()
    await middleware(handler, msg, {})

    handler.assert_not_awaited()
    assert "permiso" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_allowlist_blocks_when_user_id_missing(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_TELEGRAM_IDS", {111})
    msg = MagicMock()
    msg.from_user = None
    msg.answer = AsyncMock()

    middleware = bot.AllowlistMiddleware()
    handler = AsyncMock()
    await middleware(handler, msg, {})

    handler.assert_not_awaited()
    assert "permiso" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_allowlist_blocks_cfg_callback(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_TELEGRAM_IDS", {111})
    callback = MagicMock()
    callback.from_user = MagicMock(id=222)
    callback.data = "cfg:model:grok"
    callback.answer = AsyncMock()
    callback.__class__ = types.CallbackQuery

    middleware = bot.AllowlistMiddleware()
    handler = AsyncMock()
    await middleware(handler, callback, {})

    handler.assert_not_awaited()
    callback.answer.assert_awaited_once_with(
        "No tienes permiso para usar este bot.",
        show_alert=True,
    )


def test_validate_prompt_max_length():
    err = bot._validate_prompt("x" * (bot.TELEGRAM_MAX_TEXT_LEN + 1))
    assert err is not None
    assert str(bot.TELEGRAM_MAX_TEXT_LEN) in err


def test_log_xai_error_does_not_log_body(capsys):
    bot._log_xai_error(500, request_id="req-secret")
    out = capsys.readouterr().out
    assert "status=500" in out
    assert "req-secret" in out
    assert "sensitive body" not in out


