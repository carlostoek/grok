"""Cancel in-flight batch jobs (faceswap, album edit, regenerate)."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot
import sessions

RESULT_URL = "https://kieai.redpandaai.co/static/result.png"
KIE_META = {"task_id": "t1", "index": 0, "provider": "kie"}


def _status_message():
    msg = MagicMock()
    msg.edit_text = AsyncMock()
    msg.delete = AsyncMock()
    msg.text = "status"
    msg.caption = None
    return msg


@pytest.mark.asyncio
async def test_cancel_job_with_no_active_job():
    callback = MagicMock()
    callback.from_user.id = 5001
    callback.data = "cancel_job"
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.text = "nada"
    callback.message.caption = None
    callback.message.edit_text = AsyncMock()

    await bot.handle_cancel_job(callback)

    callback.answer.assert_awaited_once()
    assert "No hay proceso" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cancel_job_sets_event():
    uid = 5002
    event = bot._start_job(uid, "faceswap")
    assert not event.is_set()

    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cancel_job"
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.text = "Face swap\n[░░░░░░░░░░] 0/3 (0%)"
    callback.message.caption = None
    callback.message.edit_text = AsyncMock()

    await bot.handle_cancel_job(callback)

    assert event.is_set()
    callback.answer.assert_awaited_once_with("Cancelando…")
    callback.message.edit_text.assert_awaited()


@pytest.mark.asyncio
async def test_faceswap_batch_stops_on_cancel(sessions_file, tmp_path):
    uid = 5101
    bot.get_user_state(uid)["model"] = "faceswap"
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source-bytes")
    bot.get_user_state(uid)["source_path"] = str(source)

    anchor = MagicMock()
    anchor.reply_media_group = AsyncMock()
    anchor.reply_photo = AsyncMock()
    status = _status_message()

    call_count = {"n": 0}

    def _fake_swap(source_path, target_path, output_dir):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Cancel after first image completes.
            bot._request_cancel_job(uid)
        output_dir.mkdir(parents=True, exist_ok=True)
        result = output_dir / target_path.name
        result.write_bytes(b"swap-bytes")
        return result

    async def _fake_download(_bot, file_id, temp_dir):
        temp_dir.mkdir(parents=True, exist_ok=True)
        path = temp_dir / f"{file_id}.jpg"
        path.write_bytes(b"target")
        return path

    with patch.object(bot, "_faceswap_replicate_single", side_effect=_fake_swap):
        with patch("bot.download.download_telegram_photo", side_effect=_fake_download):
            with patch("bot.asyncio.sleep", new_callable=AsyncMock):
                await bot._execute_faceswap_batch(
                    anchor,
                    ["p1", "p2", "p3"],
                    user_id=uid,
                    status_msg=status,
                )

    # First image finished, cancel fired during it → second item not started
    # (cancel checked at loop start after first item's finally).
    # Actually cancel is set during first swap; after swap we check cancel and break
    # without appending that result... wait, cancel is set INSIDE first swap after
    # call_count++, so when first returns we check cancel and break WITHOUT appending.
    # So 0 results delivered. Adjust expectation: cancel mid-first-image discards it.
    assert call_count["n"] == 1
    anchor.reply_media_group.assert_not_awaited()
    final = status.edit_text.await_args_list[-1]
    assert "Cancelado" in final.args[0]
    assert uid not in bot._active_jobs


@pytest.mark.asyncio
async def test_faceswap_batch_delivers_partial_then_cancels(sessions_file, tmp_path):
    """Cancel between images keeps already-finished results."""
    uid = 5102
    bot.get_user_state(uid)["model"] = "faceswap"
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source-bytes")
    bot.get_user_state(uid)["source_path"] = str(source)

    anchor = MagicMock()
    anchor.reply_media_group = AsyncMock()
    anchor.reply_photo = AsyncMock()
    status = _status_message()

    call_count = {"n": 0}

    def _fake_swap(source_path, target_path, output_dir):
        call_count["n"] += 1
        output_dir.mkdir(parents=True, exist_ok=True)
        result = output_dir / target_path.name
        result.write_bytes(b"swap-bytes")
        return result

    async def _fake_download(_bot, file_id, temp_dir):
        temp_dir.mkdir(parents=True, exist_ok=True)
        path = temp_dir / f"{file_id}.jpg"
        path.write_bytes(b"target")
        return path

    original_sleep = asyncio.sleep

    async def _sleep_and_cancel(seconds):
        # After first image rate-limit sleep, cancel remaining.
        bot._request_cancel_job(uid)
        return None

    with patch.object(bot, "_faceswap_replicate_single", side_effect=_fake_swap):
        with patch("bot.download.download_telegram_photo", side_effect=_fake_download):
            with patch("bot.asyncio.sleep", side_effect=_sleep_and_cancel):
                await bot._execute_faceswap_batch(
                    anchor,
                    ["p1", "p2", "p3"],
                    user_id=uid,
                    status_msg=status,
                )

    assert call_count["n"] == 1
    anchor.reply_media_group.assert_awaited_once()
    sent = anchor.reply_media_group.await_args.args[0]
    assert len(sent) == 1
    final = status.edit_text.await_args_list[-1].args[0]
    assert "Cancelado" in final
    assert "1/3" in final


@pytest.mark.asyncio
async def test_album_edit_stops_on_cancel(sessions_file):
    uid = 5201
    bot.get_user_state(uid)["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")

    anchor = MagicMock()
    anchor.from_user.id = uid
    anchor.chat.id = 9001
    status = _status_message()
    anchor.reply = AsyncMock(return_value=status)
    anchor.answer_photo = AsyncMock(return_value=MagicMock(message_id=99))
    anchor.answer = AsyncMock()

    gen_calls = {"n": 0}

    async def _gen(*args, **kwargs):
        gen_calls["n"] += 1
        if gen_calls["n"] == 1:
            bot._request_cancel_job(uid)
        return [RESULT_URL], None, KIE_META

    with patch.object(bot, "generate_image", side_effect=_gen):
        with patch.object(
            bot,
            "_download_telegram_file_id",
            new_callable=AsyncMock,
            return_value=BytesIO(b"img"),
        ):
            with patch.object(
                bot,
                "download_url",
                new_callable=AsyncMock,
                return_value=(b"png-bytes", None),
            ):
                ok = await bot._process_album_edit_from_file_ids(
                    anchor,
                    "make it sunny",
                    ["f1", "f2", "f3"],
                    user_id=uid,
                )

    assert ok is True
    # Cancel during first generate → result discarded, no second call
    assert gen_calls["n"] == 1
    final = status.edit_text.await_args_list[-1].args[0]
    assert "Cancelado" in final
    assert uid not in bot._active_jobs


@pytest.mark.asyncio
async def test_regenerate_respects_cancel(generation_refs_file):
    uid = 5301
    chat_id = 8001
    msg_id = 42
    sessions.save_generation_ref(
        chat_id,
        msg_id,
        provider="kie",
        kind="image",
        prompt="a cat",
        regen={
            "model_key": "grok",
            "imagine_provider": "kie",
            "imagine_variant": "standard",
            "prompt": "a cat",
            "mode": "text",
            "user_id": uid,
        },
    )

    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "regen"
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.photo = [MagicMock()]
    callback.message.chat.id = chat_id
    callback.message.message_id = msg_id
    status = _status_message()
    callback.message.answer = AsyncMock(return_value=status)

    async def _slow_gen(*args, **kwargs):
        bot._request_cancel_job(uid)
        return [RESULT_URL], None, KIE_META

    with patch.object(bot, "generate_image", side_effect=_slow_gen):
        with patch.object(bot, "process_image_result", new_callable=AsyncMock) as proc:
            await bot.handle_regenerate_image(callback)

    proc.assert_not_awaited()
    final = status.edit_text.await_args_list[-1].args[0]
    assert "cancelada" in final.lower()
    assert uid not in bot._active_jobs


@pytest.mark.asyncio
async def test_cancel_keyboard_has_button():
    kb = bot._cancel_job_keyboard()
    assert kb.inline_keyboard[0][0].callback_data == "cancel_job"
    assert "Cancelar" in kb.inline_keyboard[0][0].text
