"""Security-related tests: allowlist, global concurrency, prompt limits, logging."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot
import sessions

MODEL = bot.MODELS["grok_video"]


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
async def test_global_concurrency_blocks_when_full(monkeypatch):
    monkeypatch.setattr(bot, "VIDEO_MAX_GLOBAL_CONCURRENT", 1)
    bot._video_active_jobs.add(9999)
    bot._video_global_active_count = 1

    uid = 3333
    msg = MagicMock()
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

    assert "ocupado" in status.edit_text.await_args.args[0]


def test_validate_prompt_max_length():
    err = bot._validate_prompt("x" * (bot.MAX_PROMPT_LEN + 1))
    assert err is not None
    assert str(bot.MAX_PROMPT_LEN) in err


def test_log_xai_error_does_not_log_body(capsys):
    bot._log_xai_error(500, request_id="req-secret")
    out = capsys.readouterr().out
    assert "status=500" in out
    assert "req-secret" in out
    assert "sensitive body" not in out


@pytest.mark.asyncio
async def test_persisted_hourly_limit_survives_restart(sessions_file):
    uid = 4444
    now = 2_000_000.0
    sessions_file.write_text(
        json.dumps(
            {
                "4444": {
                    "video_hourly_timestamps": [now - 100 * i for i in range(bot.VIDEO_MAX_PER_HOUR)],
                }
            }
        )
    )
    with patch("sessions.time.time", return_value=now):
        assert sessions.count_video_hourly_usage(uid) == bot.VIDEO_MAX_PER_HOUR