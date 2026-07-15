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
async def test_photo_caption_i2v_long_caption_triggers_collection():
    long_caption = "x" * 1021
    msg = _make_user_message(caption=long_caption, photo=[MagicMock(file_id="p1")])
    bot.get_user_state(1001)["model"] = "grok_video"

    with patch.object(bot, "_do_generate_video", new_callable=AsyncMock) as mock_gen:
        await bot.handle_photo_caption(msg)

    mock_gen.assert_not_called()
    state = bot.get_user_state(1001)
    assert state["awaiting_long_prompt_text"] is True
    assert state["pending_edit_is_video"] is True
    msg.answer.assert_awaited_once()
    assert "mensaje de texto" in msg.answer.await_args.args[0]


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
async def test_faceswap_photo_shows_confirmation(sessions_file, tmp_path):
    uid = 1201
    bot.get_user_state(uid)["model"] = "faceswap"
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source-bytes")
    bot.get_user_state(uid)["source_path"] = str(source)

    msg = _make_user_message(user_id=uid, photo=[MagicMock(file_id="p-fs")])
    with patch.object(bot, "_process_batch_replicate_sync") as mock_batch:
        await bot.handle_photo_no_caption(msg)

    mock_batch.assert_not_called()
    msg.answer.assert_awaited_once()
    assert "Confirmas hacer face swap" in msg.answer.await_args.args[0]
    assert bot.get_user_state(uid)["pending_faceswap_file_ids"] == ["p-fs"]


@pytest.mark.asyncio
async def test_faceswap_confirm_yes_executes_swap(sessions_file, tmp_path):
    uid = 1202
    bot.get_user_state(uid)["model"] = "faceswap"
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source-bytes")
    bot.get_user_state(uid)["source_path"] = str(source)
    bot.get_user_state(uid)["pending_faceswap_file_ids"] = ["p-confirm"]

    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "confirm:yes"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.message.reply_media_group = AsyncMock()
    callback.answer = AsyncMock()

    target = tmp_path / "target.jpg"
    target.write_bytes(b"target-bytes")
    output = tmp_path / "swap.jpg"
    output.write_bytes(b"out")

    with patch.object(
        bot,
        "_faceswap_replicate_single",
        return_value=output,
    ) as mock_swap:
        with patch(
            "bot.download.download_telegram_photo",
            new_callable=AsyncMock,
            return_value=target,
        ):
            with patch("bot.asyncio.sleep", new_callable=AsyncMock):
                await bot.handle_confirm_generation(callback)

    mock_swap.assert_called_once()
    assert bot.get_user_state(uid)["pending_faceswap_file_ids"] is None
    edit_calls = [call.args[0] for call in callback.message.edit_text.await_args_list]
    assert any("Procesando face swap" in text for text in edit_calls)
    callback.message.reply_media_group.assert_awaited_once()


def test_faceswap_replicate_wait_respects_api_limit():
    assert bot.REPLICATE_WAIT_SEC == 60


def test_faceswap_progress_bar_shows_visual_fill():
    bar = bot._faceswap_progress_bar(5, 10)
    assert "5/10" in bar
    assert "50%" in bar
    assert "█" in bar
    assert "░" in bar


def test_faceswap_progress_message_includes_current_step():
    text = bot._faceswap_progress_message(2, 6, current=3)
    assert "2/6" in text
    assert "Imagen 3/6" in text


@pytest.mark.asyncio
async def test_faceswap_batch_partial_failure_still_delivers(sessions_file, tmp_path):
    uid = 1204
    bot.get_user_state(uid)["model"] = "faceswap"
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source-bytes")
    bot.get_user_state(uid)["source_path"] = str(source)
    bot.get_user_state(uid)["pending_faceswap_file_ids"] = ["p1", "p2", "p3"]

    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "confirm:yes"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.message.reply_media_group = AsyncMock()
    callback.answer = AsyncMock()

    outputs = []

    def _fake_swap(source_path, target_path, output_dir):
        if target_path.name.endswith("3.jpg"):
            raise TimeoutError("replicate timeout")
        output_dir.mkdir(parents=True, exist_ok=True)
        result = output_dir / target_path.name
        result.write_bytes(b"swap-bytes")
        outputs.append(result)
        return result

    async def _fake_download(_bot, file_id, temp_dir):
        temp_dir.mkdir(parents=True, exist_ok=True)
        path = temp_dir / f"{file_id}.jpg"
        path.write_bytes(b"target")
        return path

    with patch.object(bot, "_faceswap_replicate_single", side_effect=_fake_swap):
        with patch("bot.download.download_telegram_photo", side_effect=_fake_download):
            with patch("bot.asyncio.sleep", new_callable=AsyncMock):
                await bot.handle_confirm_generation(callback)

    callback.message.reply_media_group.assert_awaited_once()
    sent_media = callback.message.reply_media_group.await_args.args[0]
    assert len(sent_media) == 2
    final_status = callback.message.edit_text.await_args_list[-1].args[0]
    assert "[█" in final_status
    assert "2/3" in final_status
    assert "Fallos" in final_status
    assert "imagen 3" in final_status


@pytest.mark.asyncio
async def test_faceswap_confirm_no_clears_pending(sessions_file):
    callback = MagicMock()
    callback.from_user.id = 1203
    callback.data = "confirm:no"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    bot.get_user_state(1203)["pending_faceswap_file_ids"] = ["p1", "p2"]

    await bot.handle_confirm_generation(callback)

    assert bot.get_user_state(1203)["pending_faceswap_file_ids"] is None
    callback.message.edit_text.assert_awaited_once_with("Generacion cancelada.")


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
        "Ya no hay nada pendiente. Envia una imagen o prompt nuevo."
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
    user_state["pending_faceswap_file_ids"] = ["p-stale"]
    bot._set_long_prompt_collection(
        user_state,
        file_ids=["p1"],
        integrate_mode=False,
        is_video=True,
    )

    from conftest import make_fsm_context
    import config_flow

    fsm_state = make_fsm_context(
        fsm_state=config_flow._state_key(config_flow.ConfigStates.select_model),
    )
    bot._CONFIG_DEPS["safe_edit_text"] = AsyncMock()
    await bot.handle_cfg_model(callback, fsm_state)

    assert user_state["pending_prompt"] is None
    assert user_state["pending_faceswap_file_ids"] is None
    assert user_state["awaiting_long_prompt_text"] is False
    assert user_state["pending_edit_file_ids"] is None
    assert user_state["pending_edit_integrate_mode"] is False
    assert user_state["pending_edit_is_video"] is False
    assert user_state["model"] == "seedream"