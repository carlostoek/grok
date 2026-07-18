"""Album batch editing for Grok Imagine (media group + caption)."""

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
VALID_CAPTION = "change the background to a sunset beach"


@pytest.fixture
def no_sleep():
    async def _noop(_seconds):
        return None

    with patch("bot.asyncio.sleep", new=_noop):
        yield


@pytest.fixture
def album_tasks(no_sleep):
    tasks: list[asyncio.Task] = []
    original_create_task = asyncio.create_task

    def _capture(coro):
        task = original_create_task(coro)
        tasks.append(task)
        return task

    with patch("asyncio.create_task", side_effect=_capture):
        yield tasks


@pytest.fixture(autouse=True)
def clear_album_cache():
    bot._album_cache.clear()
    yield
    bot._album_cache.clear()


def _make_album_message(**kwargs):
    msg = MagicMock()
    msg.from_user.id = kwargs.get("user_id", 1001)
    msg.chat.id = kwargs.get("chat_id", 2001)
    msg.message_id = kwargs.get("message_id", 1)
    msg.caption = kwargs.get("caption")
    msg.media_group_id = kwargs.get("media_group_id", "mg-1")
    photo = kwargs.get("photo")
    if photo is None:
        photo = [MagicMock(file_id=kwargs.get("file_id", f"p{msg.message_id}"))]
    msg.photo = photo
    msg.answer = AsyncMock()
    status = MagicMock()
    status.edit_text = AsyncMock()
    status.delete = AsyncMock()
    msg.reply = AsyncMock(return_value=status)
    msg.edit_text = AsyncMock()
    return msg


async def _drain_album_tasks(tasks: list[asyncio.Task] | None = None):
    await asyncio.sleep(0)
    if tasks:
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_handle_photo_caption_ignores_media_group(no_sleep, sessions_file):
    msg = _make_album_message(caption=VALID_CAPTION, media_group_id="mg-99")
    bot.get_user_state(1001)["model"] = "grok"
    sessions.set_grok_imagine_config(1001, "kie", "standard")

    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        await bot.handle_photo_caption(msg)

    mock_gen.assert_not_called()


@pytest.mark.asyncio
async def test_grok_album_collects_messages(album_tasks, sessions_file):
    uid = 1101
    bot.get_user_state(uid)["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")

    messages = [
        _make_album_message(user_id=uid, message_id=10, caption=VALID_CAPTION, file_id="p10"),
        _make_album_message(user_id=uid, message_id=11, file_id="p11"),
        _make_album_message(user_id=uid, message_id=12, file_id="p12"),
    ]

    with patch.object(bot, "generate_image", new_callable=AsyncMock, return_value=([RESULT_URL], None, KIE_META)) as mock_gen:
        with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"bytes")):
            with patch.object(bot, "process_image_result", new_callable=AsyncMock):
                for msg in messages:
                    await bot.handle_album(msg)
                await _drain_album_tasks(album_tasks)

    assert mock_gen.await_count == 3


@pytest.mark.asyncio
async def test_grok_album_extracts_caption_from_first_message(album_tasks, sessions_file):
    uid = 1102
    bot.get_user_state(uid)["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    expected = "make the sky purple and add stars"

    messages = [
        _make_album_message(user_id=uid, message_id=20, caption=expected, file_id="p20"),
        _make_album_message(user_id=uid, message_id=21, caption=None, file_id="p21"),
    ]

    with patch.object(bot, "generate_image", new_callable=AsyncMock, return_value=([RESULT_URL], None, KIE_META)) as mock_gen:
        with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"bytes")):
            with patch.object(bot, "process_image_result", new_callable=AsyncMock):
                for msg in messages:
                    await bot.handle_album(msg)
                await _drain_album_tasks(album_tasks)

    for call in mock_gen.await_args_list:
        assert call.args[1] == expected


@pytest.mark.asyncio
async def test_grok_album_requires_caption(album_tasks, sessions_file):
    uid = 1103
    bot.get_user_state(uid)["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")

    messages = [
        _make_album_message(user_id=uid, message_id=30, caption=None, file_id="p30"),
        _make_album_message(user_id=uid, message_id=31, caption=None, file_id="p31"),
    ]

    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        for msg in messages:
            await bot.handle_album(msg)
        await _drain_album_tasks(album_tasks)

    mock_gen.assert_not_called()
    messages[0].answer.assert_awaited_once()
    help_text = messages[0].answer.await_args.args[0]
    assert "caption" in help_text.lower()


@pytest.mark.asyncio
async def test_grok_album_sequential_kie_calls(album_tasks, sessions_file):
    uid = 1104
    bot.get_user_state(uid)["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")

    image_payloads = [BytesIO(b"img-a"), BytesIO(b"img-b")]
    messages = [
        _make_album_message(user_id=uid, message_id=40, caption=VALID_CAPTION, file_id="p40"),
        _make_album_message(user_id=uid, message_id=41, file_id="p41"),
    ]

    with patch.object(
        bot,
        "_download_telegram_file_id",
        new_callable=AsyncMock,
        side_effect=image_payloads,
    ):
        with patch.object(
            bot,
            "generate_image",
            new_callable=AsyncMock,
            return_value=([RESULT_URL], None, KIE_META),
        ) as mock_gen:
            with patch.object(bot, "process_image_result", new_callable=AsyncMock):
                for msg in messages:
                    await bot.handle_album(msg)
                await _drain_album_tasks(album_tasks)

    assert mock_gen.await_count == 2
    prompts = [call.args[1] for call in mock_gen.await_args_list]
    assert prompts == [VALID_CAPTION, VALID_CAPTION]
    image_args = [call.args[2] for call in mock_gen.await_args_list]
    assert image_args[0].getvalue() == b"img-a"
    assert image_args[1].getvalue() == b"img-b"


@pytest.mark.asyncio
async def test_grok_album_kie_uses_upload_not_ref(album_tasks, sessions_file):
    uid = 1105
    bot.get_user_state(uid)["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")

    messages = [
        _make_album_message(user_id=uid, message_id=50, caption=VALID_CAPTION, file_id="p50"),
        _make_album_message(user_id=uid, message_id=51, file_id="p51"),
    ]

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"bytes")):
        with patch.object(
            bot,
            "generate_image",
            new_callable=AsyncMock,
            return_value=([RESULT_URL], None, KIE_META),
        ) as mock_gen:
            with patch.object(bot, "process_image_result", new_callable=AsyncMock):
                for msg in messages:
                    await bot.handle_album(msg)
                await _drain_album_tasks(album_tasks)

    for call in mock_gen.await_args_list:
        assert "kie_source_ref" not in call.kwargs


@pytest.mark.asyncio
async def test_grok_album_saves_generation_ref_per_output(album_tasks, generation_refs_file, sessions_file):
    uid = 1106
    chat_id = 2106
    bot.get_user_state(uid)["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")

    messages = [
        _make_album_message(
            user_id=uid, chat_id=chat_id, message_id=60, caption=VALID_CAPTION, file_id="p60"
        ),
        _make_album_message(user_id=uid, chat_id=chat_id, message_id=61, file_id="p61"),
    ]
    sent_ids = [601, 602]
    sent_idx = {"i": 0}

    async def _answer_photo(*_args, **_kwargs):
        sent = MagicMock()
        sent.message_id = sent_ids[sent_idx["i"]]
        sent_idx["i"] += 1
        return sent

    messages[0].answer_photo = AsyncMock(side_effect=_answer_photo)

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"bytes")):
        with patch.object(
            bot,
            "generate_image",
            new_callable=AsyncMock,
            return_value=([RESULT_URL], None, KIE_META),
        ):
                with patch.object(bot, "download_url", new_callable=AsyncMock, return_value=(b"png", None)):
                    for msg in messages:
                        await bot.handle_album(msg)
                    await _drain_album_tasks(album_tasks)

    assert sessions.get_generation_ref(chat_id, 601) is not None
    assert sessions.get_generation_ref(chat_id, 602) is not None


@pytest.mark.asyncio
async def test_grok_album_stops_on_error(album_tasks, sessions_file):
    uid = 1107
    bot.get_user_state(uid)["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")

    messages = [
        _make_album_message(user_id=uid, message_id=70, caption=VALID_CAPTION, file_id="p70"),
        _make_album_message(user_id=uid, message_id=71, file_id="p71"),
        _make_album_message(user_id=uid, message_id=72, file_id="p72"),
    ]
    status_msg = messages[0].reply.return_value

    side_effects = [
        ([RESULT_URL], None, KIE_META),
        (None, "Kie task failed", None),
    ]

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"bytes")):
        with patch.object(bot, "generate_image", new_callable=AsyncMock, side_effect=side_effects) as mock_gen:
            with patch.object(bot, "process_image_result", new_callable=AsyncMock):
                for msg in messages:
                    await bot.handle_album(msg)
                await _drain_album_tasks(album_tasks)

    assert mock_gen.await_count == 2
    status_msg.edit_text.assert_awaited()
    final_status = status_msg.edit_text.await_args_list[-1].args[0]
    assert "1/3 completadas" in final_status
    assert "error en imagen 2" in final_status


@pytest.mark.asyncio
async def test_faceswap_album_shows_confirmation(album_tasks, sessions_file, tmp_path):
    uid = 1108
    bot.get_user_state(uid)["model"] = "faceswap"
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source-bytes")
    bot.get_user_state(uid)["source_path"] = str(source)

    msg = _make_album_message(user_id=uid, message_id=80, file_id="p80")

    with patch.object(bot, "_process_batch_replicate_sync", return_value={"processed": 1}) as mock_batch:
        with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
            await bot.handle_album(msg)
            await _drain_album_tasks(album_tasks)

    mock_batch.assert_not_called()
    mock_gen.assert_not_called()
    msg.answer.assert_awaited_once()
    assert "Confirmas hacer face swap" in msg.answer.await_args.args[0]
    assert bot.get_user_state(uid)["pending_faceswap_file_ids"] == ["p80"]
    assert msg.answer.await_args.kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_grok_album_xai_provider(album_tasks, sessions_file):
    uid = 1109
    sessions.set_grok_imagine_config(uid, "xai", "quality")
    bot.get_user_state(uid)["model"] = "grok"

    messages = [
        _make_album_message(user_id=uid, message_id=90, caption=VALID_CAPTION, file_id="p90"),
        _make_album_message(user_id=uid, message_id=91, file_id="p91"),
    ]

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"bytes")):
        with patch.object(
            bot,
            "generate_image",
            new_callable=AsyncMock,
            return_value=([RESULT_URL], None, None),
        ) as mock_gen:
            with patch.object(bot, "process_image_result", new_callable=AsyncMock):
                for msg in messages:
                    await bot.handle_album(msg)
                await _drain_album_tasks(album_tasks)

    assert mock_gen.await_count == 2
    model = mock_gen.await_args_list[0].args[0]
    assert model["provider"] == "xai"


@pytest.mark.asyncio
async def test_grok_album_long_caption_triggers_collection(album_tasks, sessions_file):
    uid = 1111
    bot.get_user_state(uid)["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    long_caption = "x" * 1021

    messages = [
        _make_album_message(user_id=uid, message_id=101, caption=long_caption, file_id="p101"),
        _make_album_message(user_id=uid, message_id=102, file_id="p102"),
    ]

    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        for msg in messages:
            await bot.handle_album(msg)
        await _drain_album_tasks(album_tasks)

    mock_gen.assert_not_called()
    state = bot.get_user_state(uid)
    assert state["awaiting_long_prompt_text"] is True
    assert state["pending_edit_file_ids"] == ["p101", "p102"]
    messages[0].answer.assert_awaited_once()
    assert "mensaje de texto" in messages[0].answer.await_args.args[0]


@pytest.mark.asyncio
async def test_handle_album_ignored_for_seedream(no_sleep, sessions_file):
    uid = 1110
    bot.get_user_state(uid)["model"] = "seedream"

    msg = _make_album_message(user_id=uid, message_id=100, caption=VALID_CAPTION, file_id="p100")

    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        with patch.object(bot, "_process_grok_album_after_delay", new_callable=AsyncMock) as mock_grok:
            await bot.handle_album(msg)
            await _drain_album_tasks()

    mock_gen.assert_not_called()
    mock_grok.assert_not_called()
    assert len(bot._album_cache) == 0