"""Integrate reference (/cambiar_referencia + /s caption) for Grok Imagine."""

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
INTEGRATE_PROMPT = "integra el personaje en la escena"
INTEGRATE_CAPTION = f"/s {INTEGRATE_PROMPT}"


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


def test_parse_integrate_caption():
    assert bot._parse_integrate_caption("/s hola mundo") == (True, "hola mundo")
    assert bot._parse_integrate_caption("/s\nhola mundo") == (True, "hola mundo")
    assert bot._parse_integrate_caption("sin prefijo") == (False, "sin prefijo")
    assert bot._parse_integrate_caption("/s") == (True, "")


@pytest.mark.asyncio
async def test_cambiar_referencia_sets_awaiting(sessions_file):
    uid = 1201
    bot.get_user_state(uid)["model"] = "grok"
    msg = MagicMock()
    msg.from_user.id = uid
    msg.answer = AsyncMock()

    await bot.cmd_cambiar_referencia(msg)

    assert bot.get_user_state(uid)["integrate_ref_awaiting"] is True
    msg.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_integrate_ref_photo_persists(tmp_path, monkeypatch, sessions_file):
    uid = 1202
    refs_dir = tmp_path / "integrate_refs"
    monkeypatch.setattr(bot, "INTEGRATE_REFS_DIR", refs_dir)
    bot.get_user_state(uid)["integrate_ref_awaiting"] = True

    msg = MagicMock()
    msg.from_user.id = uid
    msg.photo = [MagicMock(file_id="ref-photo")]
    msg.answer = AsyncMock()

    file_mock = MagicMock()
    file_mock.file_path = "photos/ref.jpg"
    bot.bot.get_file = AsyncMock(return_value=file_mock)
    bot.bot.download_file = AsyncMock(return_value=BytesIO(b"ref-bytes"))

    await bot._handle_integrate_ref_photo(msg)

    ref_path = refs_dir / f"{uid}.jpg"
    assert ref_path.exists()
    assert ref_path.read_bytes() == b"ref-bytes"
    assert sessions.get_session(uid)["integrate_ref_path"] == str(ref_path)
    assert bot.get_user_state(uid)["integrate_ref_awaiting"] is False


@pytest.mark.asyncio
async def test_grok_album_integrate_calls_with_reference(album_tasks, sessions_file, tmp_path):
    uid = 1203
    sessions.set_grok_imagine_config(uid, "xai", "quality")
    bot.get_user_state(uid)["model"] = "grok"

    ref_path = tmp_path / "ref.jpg"
    ref_path.write_bytes(b"fixed-ref")
    sessions.set_integrate_ref(uid, str(ref_path))

    messages = [
        _make_album_message(user_id=uid, message_id=10, caption=INTEGRATE_CAPTION, file_id="p10"),
        _make_album_message(user_id=uid, message_id=11, file_id="p11"),
    ]

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"rand")):
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
    for call in mock_gen.await_args_list:
        assert call.args[1] == INTEGRATE_PROMPT
        assert call.kwargs["reference_image"] is not None
        assert call.kwargs["reference_image"].getvalue() == b"fixed-ref"


@pytest.mark.asyncio
async def test_grok_album_without_s_no_reference(album_tasks, sessions_file, tmp_path):
    uid = 1204
    bot.get_user_state(uid)["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")

    ref_path = tmp_path / "ref.jpg"
    ref_path.write_bytes(b"fixed-ref")
    sessions.set_integrate_ref(uid, str(ref_path))

    caption = "change the background to a sunset beach"
    messages = [
        _make_album_message(user_id=uid, message_id=20, caption=caption, file_id="p20"),
        _make_album_message(user_id=uid, message_id=21, file_id="p21"),
    ]

    with patch.object(bot, "_download_telegram_file_id", new_callable=AsyncMock, return_value=BytesIO(b"rand")):
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
    for call in mock_gen.await_args_list:
        assert call.kwargs.get("reference_image") is None


@pytest.mark.asyncio
async def test_integrate_long_caption_saves_integrate_mode(sessions_file):
    long_prompt = "x" * 1021
    caption = f"/s {long_prompt}"
    msg = MagicMock()
    msg.from_user.id = 1207
    msg.caption = caption
    msg.photo = [MagicMock(file_id="p-long")]
    msg.media_group_id = None
    msg.answer = AsyncMock()
    bot.get_user_state(1207)["model"] = "grok"
    sessions.set_grok_imagine_config(1207, "kie", "standard")

    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        await bot.handle_photo_caption(msg)

    mock_gen.assert_not_called()
    state = bot.get_user_state(1207)
    assert state["awaiting_long_prompt_text"] is True
    assert state["pending_edit_integrate_mode"] is True
    assert state["pending_edit_file_ids"] == ["p-long"]


@pytest.mark.asyncio
async def test_grok_album_integrate_without_ref_errors(album_tasks, sessions_file):
    uid = 1205
    bot.get_user_state(uid)["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "xai", "quality")

    messages = [
        _make_album_message(user_id=uid, message_id=30, caption=INTEGRATE_CAPTION, file_id="p30"),
        _make_album_message(user_id=uid, message_id=31, file_id="p31"),
    ]

    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        for msg in messages:
            await bot.handle_album(msg)
        await _drain_album_tasks(album_tasks)

    mock_gen.assert_not_called()
    messages[0].answer.assert_awaited()
    assert "referencia" in messages[0].answer.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_grok_album_integrate_kie_provider_errors(album_tasks, sessions_file, tmp_path):
    uid = 1206
    bot.get_user_state(uid)["model"] = "grok"
    sessions.set_grok_imagine_config(uid, "kie", "standard")

    ref_path = tmp_path / "ref.jpg"
    ref_path.write_bytes(b"fixed-ref")
    sessions.set_integrate_ref(uid, str(ref_path))

    messages = [
        _make_album_message(user_id=uid, message_id=40, caption=INTEGRATE_CAPTION, file_id="p40"),
        _make_album_message(user_id=uid, message_id=41, file_id="p41"),
    ]

    with patch.object(bot, "generate_image", new_callable=AsyncMock) as mock_gen:
        for msg in messages:
            await bot.handle_album(msg)
        await _drain_album_tasks(album_tasks)

    mock_gen.assert_not_called()
    messages[0].answer.assert_awaited()
    assert "xai" in messages[0].answer.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_generate_xai_multi_image_uses_images_array(no_sleep):
    model = {"id": "grok-imagine-image-quality"}
    captured: dict = {}

    class FakeResp:
        status = 200

        async def json(self):
            return {"data": [{"url": "https://example.com/out.png"}]}

        async def text(self):
            return ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeSession:
        def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["body"] = json
            return FakeResp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    with patch("bot.aiohttp.ClientSession", FakeSession):
        output, err = await bot._generate_xai(
            model,
            INTEGRATE_PROMPT,
            BytesIO(b"random"),
            reference_image=BytesIO(b"fixed"),
        )

    assert err is None
    assert output == ["https://example.com/out.png"]
    assert "images" in captured["body"]
    assert len(captured["body"]["images"]) == 2
    assert "image" not in captured["body"]