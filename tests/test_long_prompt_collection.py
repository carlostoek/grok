"""Long prompt collection: caption > 1020 → text follow-up."""

from __future__ import annotations

import asyncio
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot
import config_flow
import sessions
from conftest import make_fsm_context

RESULT_URL = "https://kieai.redpandaai.co/static/result.png"
KIE_META = {"task_id": "t1", "index": 0, "provider": "kie"}
VALID_CAPTION = "change the background to a sunset beach"
LONG_PROMPT = "x" * 1021
SHORT_PROMPT = "x" * 1020
FOLLOW_UP_PROMPT = "a valid follow-up prompt for editing"


def _make_user_message(**kwargs):
    msg = MagicMock()
    msg.from_user.id = kwargs.get("user_id", 1001)
    msg.text = kwargs.get("text", FOLLOW_UP_PROMPT)
    msg.caption = kwargs.get("caption")
    msg.photo = kwargs.get("photo")
    msg.reply_to_message = kwargs.get("reply_to_message")
    msg.media_group_id = kwargs.get("media_group_id")
    msg.answer = AsyncMock()
    msg.edit_text = AsyncMock()
    return msg


def _make_photo_caption_message(caption, file_id="p1", model="grok", user_id=1001):
    msg = _make_user_message(caption=caption, photo=[MagicMock(file_id=file_id)], user_id=user_id)
    bot.get_user_state(user_id)["model"] = model
    return msg


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


async def _drain_album_tasks(tasks: list[asyncio.Task] | None = None):
    await asyncio.sleep(0)
    if tasks:
        await asyncio.gather(*tasks)


def test_long_prompt_state_helpers():
    state = {}
    bot._set_long_prompt_collection(
        state,
        file_ids=["p1", "p2"],
        integrate_mode=True,
        is_video=True,
    )
    assert bot._is_awaiting_long_prompt_text(state) is True
    assert state["pending_edit_file_ids"] == ["p1", "p2"]
    assert state["pending_edit_integrate_mode"] is True
    assert state["pending_edit_is_video"] is True

    bot._clear_long_prompt_collection(state)
    assert bot._is_awaiting_long_prompt_text(state) is False
    assert state["pending_edit_file_ids"] is None
    assert state["pending_edit_integrate_mode"] is False
    assert state["pending_edit_is_video"] is False


def test_prompt_needs_collection_at_1021():
    assert bot._prompt_needs_long_text_collection(SHORT_PROMPT) is False
    assert bot._prompt_needs_long_text_collection(LONG_PROMPT) is True


@pytest.mark.asyncio
async def test_photo_caption_over_1020_triggers_collection(sessions_file):
    msg = _make_photo_caption_message(LONG_PROMPT)
    sessions.set_grok_imagine_config(1001, "kie", "standard")

    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        await bot.handle_photo_caption(msg)

    mock_gen.assert_not_called()
    state = bot.get_user_state(1001)
    assert state["awaiting_long_prompt_text"] is True
    assert state["pending_edit_file_ids"] == ["p1"]
    msg.answer.assert_awaited_once()
    assert "mensaje de texto" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_photo_caption_under_1020_direct_generate(sessions_file):
    msg = _make_photo_caption_message(VALID_CAPTION)
    sessions.set_grok_imagine_config(1001, "kie", "standard")

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"bytes")):
        with patch.object(
            bot,
            "generate_image",
            new_callable=AsyncMock,
            return_value=([RESULT_URL], None, KIE_META),
        ) as mock_gen:
            with patch.object(bot, "process_image_result", new_callable=AsyncMock):
                await bot.handle_photo_caption(msg)

    mock_gen.assert_awaited_once()
    state = bot.get_user_state(1001)
    assert state["awaiting_long_prompt_text"] is False


@pytest.mark.asyncio
async def test_grok_video_long_caption_sets_is_video(sessions_file):
    msg = _make_photo_caption_message(LONG_PROMPT, model="grok_video")

    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        with patch.object(bot, "_do_generate_video", new_callable=AsyncMock) as mock_video:
            await bot.handle_photo_caption(msg)

    mock_gen.assert_not_called()
    mock_video.assert_not_called()
    state = bot.get_user_state(1001)
    assert state["pending_edit_is_video"] is True
    assert "animar" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_album_long_caption_saves_all_file_ids(album_tasks, sessions_file):
    uid = 1101
    bot.get_user_state(uid)["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")

    messages = [
        _make_album_message(user_id=uid, message_id=10, caption=LONG_PROMPT, file_id="p10"),
        _make_album_message(user_id=uid, message_id=11, file_id="p11"),
        _make_album_message(user_id=uid, message_id=12, file_id="p12"),
    ]

    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        for msg in messages:
            await bot.handle_album(msg)
        await _drain_album_tasks(album_tasks)

    mock_gen.assert_not_called()
    state = bot.get_user_state(uid)
    assert state["awaiting_long_prompt_text"] is True
    assert state["pending_edit_file_ids"] == ["p10", "p11", "p12"]
    messages[0].answer.assert_awaited_once()
    assert "álbum" in messages[0].answer.await_args.args[0]


@pytest.mark.asyncio
async def test_integrate_long_caption_saves_integrate_mode(sessions_file):
    caption = "/s " + LONG_PROMPT
    msg = _make_photo_caption_message(caption)
    sessions.set_grok_imagine_config(1001, "kie", "standard")

    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        await bot.handle_photo_caption(msg)

    mock_gen.assert_not_called()
    state = bot.get_user_state(1001)
    assert state["pending_edit_integrate_mode"] is True


@pytest.mark.asyncio
async def test_handle_text_completes_single_edit(sessions_file):
    uid = 1201
    state = bot.get_user_state(uid)
    state["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    bot._set_long_prompt_collection(
        state,
        file_ids=["p1"],
        integrate_mode=False,
        is_video=False,
    )

    msg = _make_user_message(text=FOLLOW_UP_PROMPT, user_id=uid)

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"bytes")):
        with patch.object(
            bot,
            "generate_image",
            new_callable=AsyncMock,
            return_value=([RESULT_URL], None, KIE_META),
        ) as mock_gen:
            with patch.object(bot, "process_image_result", new_callable=AsyncMock):
                await bot.handle_text(msg)

    mock_gen.assert_awaited_once()
    assert mock_gen.await_args.args[1] == FOLLOW_UP_PROMPT
    assert state["awaiting_long_prompt_text"] is False


@pytest.mark.asyncio
async def test_handle_text_completes_i2v(sessions_file):
    uid = 1202
    state = bot.get_user_state(uid)
    state["model"] = "grok_video"
    bot._set_long_prompt_collection(
        state,
        file_ids=["p1"],
        integrate_mode=False,
        is_video=True,
    )

    msg = _make_user_message(text=FOLLOW_UP_PROMPT, user_id=uid)

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"bytes")):
        with patch.object(bot, "_do_generate_video", new_callable=AsyncMock) as mock_video:
            await bot.handle_text(msg)

    mock_video.assert_awaited_once()
    assert state["awaiting_long_prompt_text"] is False


@pytest.mark.asyncio
async def test_handle_text_completes_album_batch(album_tasks, sessions_file):
    uid = 1203
    state = bot.get_user_state(uid)
    state["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    bot._set_long_prompt_collection(
        state,
        file_ids=["p10", "p11"],
        integrate_mode=False,
        is_video=False,
    )

    msg = _make_user_message(text=FOLLOW_UP_PROMPT, user_id=uid)
    status = MagicMock()
    status.edit_text = AsyncMock()
    msg.reply = AsyncMock(return_value=status)

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"bytes")):
        with patch.object(
            bot,
            "generate_image",
            new_callable=AsyncMock,
            return_value=([RESULT_URL], None, KIE_META),
        ) as mock_gen:
            with patch.object(bot, "process_image_result", new_callable=AsyncMock):
                await bot.handle_text(msg)

    assert mock_gen.await_count == 2
    assert state["awaiting_long_prompt_text"] is False


@pytest.mark.asyncio
async def test_handle_text_invalid_prompt_keeps_state(sessions_file):
    uid = 1204
    state = bot.get_user_state(uid)
    state["model"] = "grok"
    bot._set_long_prompt_collection(
        state,
        file_ids=["p1"],
        integrate_mode=False,
        is_video=False,
    )

    msg = _make_user_message(text="no", user_id=uid)

    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        await bot.handle_text(msg)

    mock_gen.assert_not_called()
    assert state["awaiting_long_prompt_text"] is True
    assert state["pending_edit_file_ids"] == ["p1"]


@pytest.mark.asyncio
async def test_handle_text_collection_skips_confirm_flow(sessions_file):
    uid = 1205
    state = bot.get_user_state(uid)
    state["model"] = "grok"
    bot._set_long_prompt_collection(
        state,
        file_ids=["p1"],
        integrate_mode=False,
        is_video=False,
    )

    msg = _make_user_message(text=FOLLOW_UP_PROMPT, user_id=uid)

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"bytes")):
        with patch.object(
            bot,
            "generate_image",
            new_callable=AsyncMock,
            return_value=([RESULT_URL], None, KIE_META),
        ):
            with patch.object(bot, "process_image_result", new_callable=AsyncMock):
                await bot.handle_text(msg)

    kwargs = msg.answer.await_args_list[0].kwargs
    assert kwargs.get("reply_markup") is None


@pytest.mark.asyncio
async def test_model_switch_clears_long_prompt_state(sessions_file):
    uid = 2002
    user_state = bot.get_user_state(uid)
    user_state["model"] = "grok_video"
    user_state["pending_prompt"] = "stale prompt"
    bot._set_long_prompt_collection(
        user_state,
        file_ids=["p1", "p2"],
        integrate_mode=True,
        is_video=True,
    )

    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:model:seedream"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    fsm_state = make_fsm_context(
        fsm_state=config_flow._state_key(config_flow.ConfigStates.select_model),
    )
    bot._CONFIG_DEPS["safe_edit_text"] = AsyncMock()
    await bot.handle_cfg_model(callback, fsm_state)

    assert user_state["pending_prompt"] is None
    assert user_state["awaiting_long_prompt_text"] is False
    assert user_state["pending_edit_file_ids"] is None
    assert user_state["pending_edit_integrate_mode"] is False
    assert user_state["pending_edit_is_video"] is False
    assert user_state["model"] == "seedream"


def test_format_result_caption_truncates_long_prompt():
    long_prompt = "y" * 3000
    caption = bot._format_result_caption("Prompt", long_prompt)
    assert len(caption) <= bot.TELEGRAM_MAX_CAPTION_LEN
    assert caption.endswith("…")
    assert "<b>Prompt:</b>" in caption


def test_format_result_caption_html_entities():
    # Each & expands to &amp; — truncation must not split entities
    prompt = "&" * 400
    caption = bot._format_result_caption("Edit", prompt)
    assert len(caption) <= bot.TELEGRAM_MAX_CAPTION_LEN
    assert "&amp;" in caption
    assert "&am" not in caption.replace("&amp;", "")
    assert caption.endswith("…")


@pytest.mark.asyncio
async def test_handle_text_over_4096_keeps_state(sessions_file):
    uid = 1206
    state = bot.get_user_state(uid)
    state["model"] = "grok"
    bot._set_long_prompt_collection(
        state,
        file_ids=["p1"],
        integrate_mode=False,
        is_video=False,
    )

    msg = _make_user_message(text="x" * (bot.TELEGRAM_MAX_TEXT_LEN + 1), user_id=uid)

    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        await bot.handle_text(msg)

    mock_gen.assert_not_called()
    assert state["awaiting_long_prompt_text"] is True
    assert state["pending_edit_file_ids"] == ["p1"]


@pytest.mark.asyncio
async def test_new_long_caption_replaces_pending_file_ids(sessions_file):
    uid = 1207
    state = bot.get_user_state(uid)
    state["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    bot._set_long_prompt_collection(
        state,
        file_ids=["old-photo"],
        integrate_mode=False,
        is_video=False,
    )

    msg = _make_photo_caption_message(LONG_PROMPT, file_id="new-photo", user_id=uid)

    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        await bot.handle_photo_caption(msg)

    mock_gen.assert_not_called()
    assert state["pending_edit_file_ids"] == ["new-photo"]
    assert state["awaiting_long_prompt_text"] is True


@pytest.mark.asyncio
async def test_handle_text_completes_integrate_edit(sessions_file, tmp_path):
    uid = 1208
    sessions.set_grok_imagine_config(uid, "xai", "quality")
    ref_path = tmp_path / "ref.jpg"
    ref_path.write_bytes(b"integrate-ref")
    sessions.set_integrate_ref(uid, str(ref_path))

    state = bot.get_user_state(uid)
    state["model"] = "grok"

    bot._set_long_prompt_collection(
        state,
        file_ids=["p1"],
        integrate_mode=True,
        is_video=False,
    )

    msg = _make_user_message(text=FOLLOW_UP_PROMPT, user_id=uid)

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"bytes")):
        with patch.object(
            bot,
            "generate_image",
            new_callable=AsyncMock,
            return_value=([RESULT_URL], None, None),
        ) as mock_gen:
            with patch.object(bot, "process_image_result", new_callable=AsyncMock):
                await bot.handle_text(msg)

    mock_gen.assert_awaited_once()
    assert mock_gen.await_args.kwargs["reference_image"] is not None
    assert mock_gen.await_args.kwargs["reference_image"].getvalue() == b"integrate-ref"
    assert state["awaiting_long_prompt_text"] is False


@pytest.mark.asyncio
async def test_generation_failure_keeps_collection_state(sessions_file):
    uid = 1209
    state = bot.get_user_state(uid)
    state["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    bot._set_long_prompt_collection(
        state,
        file_ids=["p1"],
        integrate_mode=False,
        is_video=False,
    )

    msg = _make_user_message(text=FOLLOW_UP_PROMPT, user_id=uid)
    status = MagicMock()
    status.edit_text = AsyncMock()
    msg.answer = AsyncMock(return_value=status)

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"bytes")):
        with patch.object(
            bot,
            "generate_image",
            new_callable=AsyncMock,
            return_value=(None, "provider failed", None),
        ):
            await bot.handle_text(msg)

    assert state["awaiting_long_prompt_text"] is True
    assert state["pending_edit_file_ids"] == ["p1"]


@pytest.mark.asyncio
async def test_short_caption_clears_stale_collection_state(sessions_file):
    uid = 1210
    state = bot.get_user_state(uid)
    state["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    bot._set_long_prompt_collection(
        state,
        file_ids=["stale-photo"],
        integrate_mode=False,
        is_video=False,
    )

    msg = _make_photo_caption_message(VALID_CAPTION, file_id="fresh-photo", user_id=uid)

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"bytes")):
        with patch.object(
            bot,
            "generate_image",
            new_callable=AsyncMock,
            return_value=([RESULT_URL], None, KIE_META),
        ) as mock_gen:
            with patch.object(bot, "process_image_result", new_callable=AsyncMock):
                await bot.handle_photo_caption(msg)

    mock_gen.assert_awaited_once()
    assert state["awaiting_long_prompt_text"] is False
    assert state["pending_edit_file_ids"] is None


@pytest.mark.asyncio
async def test_reply_to_bot_message_completes_collection(sessions_file):
    uid = 1212
    state = bot.get_user_state(uid)
    state["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    bot._set_long_prompt_collection(
        state,
        file_ids=["p1"],
        integrate_mode=False,
        is_video=False,
    )

    bot_instruction = MagicMock()
    bot_instruction.photo = None
    bot_instruction.text = "El caption es demasiado largo..."
    msg = _make_user_message(text=FOLLOW_UP_PROMPT, user_id=uid, reply_to_message=bot_instruction)
    status = MagicMock()
    status.edit_text = AsyncMock()
    msg.answer = AsyncMock(return_value=status)

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"bytes")):
        with patch.object(
            bot,
            "generate_image",
            new_callable=AsyncMock,
            return_value=([RESULT_URL], None, KIE_META),
        ) as mock_gen:
            with patch.object(bot, "process_image_result", new_callable=AsyncMock):
                await bot.handle_reply_edit(msg)

    mock_gen.assert_awaited_once()
    assert mock_gen.await_args.args[1] == FOLLOW_UP_PROMPT
    assert state["awaiting_long_prompt_text"] is False


@pytest.mark.asyncio
async def test_photo_no_caption_reminds_pending_collection(sessions_file):
    uid = 1213
    state = bot.get_user_state(uid)
    state["model"] = "grok"
    bot._set_long_prompt_collection(
        state,
        file_ids=["p1"],
        integrate_mode=False,
        is_video=False,
    )

    msg = _make_user_message(photo=[MagicMock(file_id="p-new")], user_id=uid)
    msg.caption = None
    msg.media_group_id = None

    await bot.handle_photo_no_caption(msg)

    msg.answer.assert_awaited_once()
    assert "mensaje de texto" in msg.answer.await_args.args[0]
    assert state["awaiting_long_prompt_text"] is True
    assert state["pending_edit_file_ids"] == ["p1"]


@pytest.mark.asyncio
async def test_reply_text_completes_collection(sessions_file):
    uid = 1211
    state = bot.get_user_state(uid)
    state["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    bot._set_long_prompt_collection(
        state,
        file_ids=["p1"],
        integrate_mode=False,
        is_video=False,
    )

    replied = MagicMock()
    replied.photo = [MagicMock(file_id="other")]
    msg = _make_user_message(text=FOLLOW_UP_PROMPT, user_id=uid, reply_to_message=replied)
    status = MagicMock()
    status.edit_text = AsyncMock()
    msg.answer = AsyncMock(return_value=status)

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"bytes")):
        with patch.object(
            bot,
            "generate_image",
            new_callable=AsyncMock,
            return_value=([RESULT_URL], None, KIE_META),
        ) as mock_gen:
            with patch.object(bot, "process_image_result", new_callable=AsyncMock):
                await bot.handle_reply_edit(msg)

    mock_gen.assert_awaited_once()
    assert mock_gen.await_args.args[1] == FOLLOW_UP_PROMPT
    assert state["awaiting_long_prompt_text"] is False


def test_text_prompt_accepts_4096_chars():
    assert bot._validate_prompt("x" * bot.TELEGRAM_MAX_TEXT_LEN) is None
    err = bot._validate_prompt("x" * (bot.TELEGRAM_MAX_TEXT_LEN + 1))
    assert err is not None
    assert str(bot.TELEGRAM_MAX_TEXT_LEN) in err