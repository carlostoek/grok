"""Handler routing tests for video generation flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot
import sessions


def _make_user_message(**kwargs):
    msg = MagicMock()
    msg.from_user.id = kwargs.get("user_id", 1001)
    msg.text = kwargs.get("text", "a valid video prompt here")
    msg.caption = kwargs.get("caption")
    msg.photo = kwargs.get("photo")
    msg.reply_to_message = kwargs.get("reply_to_message")
    msg.media_group_id = kwargs.get("media_group_id")
    msg.answer = AsyncMock()
    msg.edit_text = AsyncMock()
    return msg


def _make_callback(user_id=1001, prompt="a valid video prompt here"):
    callback = MagicMock()
    callback.from_user.id = user_id
    callback.data = "confirm:yes"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    bot.get_user_state(user_id)["pending_prompt"] = prompt
    bot.get_user_state(user_id)["model"] = "grok_video"
    return callback


@pytest.mark.asyncio
async def test_text_prompt_shows_confirmation_for_grok_video():
    msg = _make_user_message(text="animate a dancing robot in neon city")
    bot.get_user_state(1001)["model"] = "grok_video"

    await bot.handle_text(msg)

    msg.answer.assert_awaited_once()
    args, kwargs = msg.answer.await_args
    assert "video" in args[0]
    assert kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_confirm_routes_to_do_generate_video_with_user_id():
    callback = _make_callback()
    with patch.object(bot, "_do_generate_video", new_callable=AsyncMock) as mock_gen:
        with patch.object(bot, "safe_edit_text", new_callable=AsyncMock):
            await bot.handle_confirm_generation(callback)

    mock_gen.assert_awaited_once()
    _, kwargs = mock_gen.await_args
    assert kwargs["user_id"] == 1001
    assert kwargs["status_msg"] is callback.message
    assert kwargs["reply_message"] is callback.message


@pytest.mark.asyncio
async def test_photo_caption_i2v_rejects_short_prompt():
    msg = _make_user_message(caption="no", photo=[MagicMock(file_id="p1")])
    bot.get_user_state(1001)["model"] = "grok_video"

    await bot.handle_photo_caption(msg)

    msg.answer.assert_awaited_once()
    assert "corto" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_photo_caption_i2v_calls_do_generate_video():
    msg = _make_user_message(caption="make the water flow gently", photo=[MagicMock(file_id="p1")])
    bot.get_user_state(1001)["model"] = "grok_video"

    file_bytes = MagicMock()
    file_bytes.read.return_value = b"jpeg-bytes"
    file_bytes.seek = MagicMock()

    with patch.object(bot.bot, "get_file", new_callable=AsyncMock, return_value=MagicMock(file_path="photos/file.jpg")):
        with patch.object(bot.bot, "download_file", new_callable=AsyncMock, return_value=file_bytes):
            with patch.object(bot, "_do_generate_video", new_callable=AsyncMock) as mock_gen:
                await bot.handle_photo_caption(msg)

    mock_gen.assert_awaited_once()
    _, kwargs = mock_gen.await_args
    assert kwargs["user_id"] == 1001


@pytest.mark.asyncio
async def test_reply_to_photo_i2v_calls_do_generate_video():
    photo = [MagicMock(file_id="p2")]
    replied = MagicMock()
    replied.photo = photo
    msg = _make_user_message(
        text="zoom out slowly while waves crash",
        reply_to_message=replied,
    )
    bot.get_user_state(1001)["model"] = "grok_video"

    file_bytes = MagicMock()
    file_bytes.read.return_value = b"jpeg-bytes"
    file_bytes.seek = MagicMock()

    with patch.object(bot.bot, "get_file", new_callable=AsyncMock, return_value=MagicMock(file_path="photos/file.jpg")):
        with patch.object(bot.bot, "download_file", new_callable=AsyncMock, return_value=file_bytes):
            with patch.object(bot, "_do_generate_video", new_callable=AsyncMock) as mock_gen:
                await bot.handle_reply_edit(msg)

    mock_gen.assert_awaited_once()


@pytest.mark.asyncio
async def test_imagine_config_guard_before_mutation(sessions_file):
    uid = 3003
    bot.get_user_state(uid)
    sessions.set_grok_imagine_config(uid, "xai", "quality")

    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:variant:quality"
    callback.message = MagicMock()
    callback.answer = AsyncMock()

    from conftest import make_fsm_context
    import config_flow

    with patch.object(bot.sessions, "set_grok_imagine_config") as mock_set:
        state = make_fsm_context(
            config_model="grok",
            fsm_state=config_flow._state_key(config_flow.ConfigStates.configure),
        )
        await bot.handle_cfg_variant(callback, state)

    mock_set.assert_not_called()
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_cancel_clears_pending_prompt():
    callback = MagicMock()
    callback.from_user.id = 1002
    callback.data = "confirm:no"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    bot.get_user_state(1002)["pending_prompt"] = "cancel me"

    await bot.handle_confirm_generation(callback)

    assert bot.get_user_state(1002)["pending_prompt"] is None
    callback.message.edit_text.assert_awaited_once_with("Generacion cancelada.")
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_stale_prompt_shows_error():
    callback = MagicMock()
    callback.from_user.id = 1003
    callback.data = "confirm:yes"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    bot.get_user_state(1003)["pending_prompt"] = None
    bot.get_user_state(1003)["model"] = "grok_video"

    await bot.handle_confirm_generation(callback)

    callback.message.edit_text.assert_awaited_once_with(
        "El prompt ya no esta disponible. Envia uno nuevo."
    )
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_model_switch_clears_pending_prompt(sessions_file):
    callback = MagicMock()
    callback.from_user.id = 2002
    callback.data = "cfg:model:seedream"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    user_state = bot.get_user_state(2002)
    user_state["model"] = "grok_video"
    user_state["pending_prompt"] = "stale prompt"

    from conftest import make_fsm_context
    import config_flow

    fsm_state = make_fsm_context(
        fsm_state=config_flow._state_key(config_flow.ConfigStates.select_model),
    )
    bot._CONFIG_DEPS["safe_edit_text"] = AsyncMock()
    await bot.handle_cfg_model(callback, fsm_state)

    assert user_state["pending_prompt"] is None
    assert user_state["model"] == "seedream"