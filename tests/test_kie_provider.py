"""Tests for Kie.ai provider routing, config keyboard, and API request building."""

from __future__ import annotations

import base64
import json
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiogram.exceptions import TelegramBadRequest
from aioresponses import aioresponses

import bot
import sessions

KIE_CREATE_URL = "https://api.kie.ai/api/v1/jobs/createTask"
KIE_POLL_URL = "https://api.kie.ai/api/v1/jobs/recordInfo?taskId=task-abc"
KIE_UPLOAD_URL = "https://kieai.redpandaai.co/api/file-base64-upload"
RESULT_URL = "https://kieai.redpandaai.co/static/result.png"
VIDEO_RESULT_URL = "https://static.aiquickdraw.com/result.mp4"


def _kie_success_poll_payload(url: str = RESULT_URL) -> dict:
    return {
        "code": 200,
        "data": {
            "state": "success",
            "resultJson": json.dumps({"resultUrls": [url]}),
        },
    }


@pytest.fixture
def no_sleep():
    async def _noop(_seconds):
        return None

    with patch("bot.asyncio.sleep", new=_noop):
        yield


def test_config_provider_keyboard_has_three_options(sessions_file):
    uid = 7040
    kb = bot.config_provider_keyboard(uid)
    rows = kb.inline_keyboard
    option_buttons = [btn for row in rows for btn in row if btn.callback_data.startswith("cfg:provider:")]
    assert len(option_buttons) == 3
    callbacks = {btn.callback_data for btn in option_buttons}
    assert "cfg:provider:kie" in callbacks
    assert "cfg:provider:xai" in callbacks


def test_config_variant_keyboard_has_two_options(sessions_file):
    uid = 7041
    kb = bot.config_variant_keyboard(uid)
    rows = kb.inline_keyboard
    option_buttons = [btn for row in rows for btn in row if btn.callback_data.startswith("cfg:variant:")]
    assert len(option_buttons) == 2
    callbacks = {btn.callback_data for btn in option_buttons}
    assert "cfg:variant:quality" in callbacks
    assert "cfg:variant:standard" in callbacks


def test_get_grok_imagine_config_kie_provider(sessions_file):
    uid = 7001
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    cfg = bot.get_grok_imagine_config(uid)
    assert cfg["provider"] == "kie"
    assert cfg["variant"] == "standard"
    assert cfg["id"] == "grok-imagine/text-to-image"
    assert cfg["prov_label"] == "Kie.ai"


def test_get_model_grok_video_uses_kie_provider(sessions_file):
    uid = 7002
    sessions.set_grok_imagine_config(uid, "kie", "quality")
    bot.get_user_state(uid)["model"] = "grok_video"
    model = bot.get_model(uid)
    assert model["provider"] == "kie"
    assert "Kie.ai" in model["name"]
    assert model["imagine_variant"] == "quality"


def test_get_model_grok_video_replicate_falls_back_to_xai(sessions_file):
    uid = 7003
    sessions.set_grok_imagine_config(uid, "replicate", "quality")
    bot.get_user_state(uid)["model"] = "grok_video"
    model = bot.get_model(uid)
    assert model["provider"] == "xai"
    assert model["imagine_provider"] == "replicate"


def test_get_model_grok_image_mode(sessions_file):
    uid = 7011
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    bot.get_user_state(uid)["model"] = "grok"
    model = bot.get_model(uid)
    assert model["provider"] == "kie"
    assert model["id"] == "grok-imagine/text-to-image"


@pytest.mark.parametrize(
    ("duration", "expected"),
    [(3, 6), (5, 6), (6, 6), (9, 9), (10, 10), (15, 15), (30, 30)],
)
def test_kie_map_duration(duration, expected):
    assert bot._kie_map_duration(duration) == expected


def test_config_video_keyboard_kie_filters_aspect_ratios(sessions_file):
    uid = 7012
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    cfg = sessions.get_video_config(uid)
    kb = bot.config_video_keyboard(cfg, uid)
    aspect_callbacks = [
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data.startswith("cfg:video:aspect:")
    ]
    assert "cfg:video:aspect:4:3" not in aspect_callbacks
    assert "cfg:video:aspect:16:9" in aspect_callbacks


def test_config_video_keyboard_kie_15_includes_4_3(sessions_file):
    uid = 7013
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    sessions.set_video_config(uid, model="grok-imagine-video-1.5")
    cfg = sessions.get_video_config(uid)
    kb = bot.config_video_keyboard(cfg, uid)
    aspect_callbacks = [
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data.startswith("cfg:video:aspect:")
    ]
    assert "cfg:video:aspect:4:3" in aspect_callbacks


def test_kie_download_allowlist_blocks_unknown_host():
    assert bot._is_allowed_kie_download_host("evil.example.com") is False
    assert bot._is_allowed_kie_download_host("kieai.redpandaai.co") is True
    assert bot._is_allowed_kie_asset_url("https://static.aiquickdraw.com/x.mp4") is True
    assert bot._is_allowed_kie_asset_url("http://static.aiquickdraw.com/x.mp4") is False


@pytest.mark.asyncio
async def test_generate_image_routes_to_kie(no_sleep):
    model = {
        "key": "grok",
        "provider": "kie",
        "id": "grok-imagine/text-to-image",
        "imagine_variant": "quality",
    }
    with patch.object(bot, "_generate_kie", new_callable=AsyncMock, return_value=(["url"], None, None)) as mock_kie:
        output, err, meta = await bot.generate_image(model, "a neon cat")
    mock_kie.assert_awaited_once()
    assert err is None
    assert output == ["url"]


@pytest.mark.asyncio
async def test_generate_video_routes_to_kie(no_sleep):
    model = {"key": "grok_video", "provider": "kie", "imagine_variant": "standard"}
    with patch.object(bot, "_generate_kie_video", new_callable=AsyncMock, return_value=(VIDEO_RESULT_URL, None)) as mock_kie:
        url, err = await bot.generate_video(model, "waves on the beach")
    mock_kie.assert_awaited_once()
    assert err is None
    assert url == VIDEO_RESULT_URL


@pytest.mark.asyncio
async def test_kie_t2i_create_task_body(no_sleep, sessions_file):
    model = {
        "key": "grok",
        "provider": "kie",
        "id": "grok-imagine/text-to-image",
        "imagine_variant": "standard",
    }
    captured: dict = {}

    def capture_post(url, **kwargs):
        captured["body"] = kwargs["json"]

    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}}, callback=capture_post)
        mocked.get(KIE_POLL_URL, payload=_kie_success_poll_payload())
        output, err, meta = await bot._generate_kie(model, "sunset mountains")

    assert err is None
    assert output == [RESULT_URL]
    assert meta == {"task_id": "task-abc", "index": 0, "provider": "kie"}
    assert captured["body"]["model"] == "grok-imagine/text-to-image"
    assert captured["body"]["input"]["prompt"] == "sunset mountains"
    assert captured["body"]["input"]["enable_pro"] is False
    assert captured["body"]["input"]["aspect_ratio"] == sessions.DEFAULT_VIDEO_ASPECT_RATIO


@pytest.mark.asyncio
async def test_kie_t2i_quality_uses_enable_pro(no_sleep):
    model = {
        "key": "grok",
        "provider": "kie",
        "id": "grok-imagine/text-to-image",
        "imagine_variant": "quality",
    }
    captured: dict = {}

    def capture_post(url, **kwargs):
        captured["body"] = kwargs["json"]

    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}}, callback=capture_post)
        mocked.get(KIE_POLL_URL, payload=_kie_success_poll_payload())
        await bot._generate_kie(model, "detailed portrait")

    assert captured["body"]["input"]["enable_pro"] is True


@pytest.mark.asyncio
async def test_kie_i2i_uploads_image_and_uses_image_urls(no_sleep):
    model = {
        "key": "grok",
        "provider": "kie",
        "id": "grok-imagine/text-to-image",
        "imagine_variant": "standard",
    }
    image_bytes = b"\xff\xd8\xff" + b"jpeg-bytes"
    captured: dict = {}

    def capture_create(url, **kwargs):
        captured["create"] = kwargs["json"]

    def capture_upload(url, **kwargs):
        captured["upload"] = kwargs["json"]

    with aioresponses() as mocked:
        mocked.post(
            KIE_UPLOAD_URL,
            payload={"code": 200, "success": True, "data": {"downloadUrl": "https://tempfile.redpandaai.co/uploaded.jpg"}},
            callback=capture_upload,
        )
        mocked.post(
            KIE_CREATE_URL,
            payload={"code": 200, "data": {"taskId": "task-abc"}},
            callback=capture_create,
        )
        mocked.get(KIE_POLL_URL, payload=_kie_success_poll_payload())
        output, err, meta = await bot._generate_kie(model, "add sunglasses", image_data=BytesIO(image_bytes))

    assert err is None
    assert output == [RESULT_URL]
    expected_b64 = base64.b64encode(image_bytes).decode()
    assert captured["upload"]["base64Data"] == f"data:image/jpeg;base64,{expected_b64}"
    assert captured["create"]["model"] == bot.KIE_IMAGE_I2I
    assert captured["create"]["input"]["image_urls"] == ["https://tempfile.redpandaai.co/uploaded.jpg"]
    assert captured["create"]["input"]["prompt"] == "add sunglasses"


@pytest.mark.asyncio
async def test_kie_video_t2v_maps_duration_and_builds_body(no_sleep, sessions_file):
    sessions.set_video_config(7010, duration=15, aspect_ratio="9:16", resolution="480p")
    model = {"key": "grok_video", "provider": "kie", "imagine_variant": "standard"}
    captured: dict = {}

    def capture_post(url, **kwargs):
        captured["body"] = kwargs["json"]

    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}}, callback=capture_post)
        mocked.get(
            "https://api.kie.ai/api/v1/jobs/recordInfo?taskId=task-abc",
            payload=_kie_success_poll_payload(VIDEO_RESULT_URL),
        )
        url, err = await bot._generate_kie_video(model, "ocean waves", user_id=7010)

    assert err is None
    assert url == VIDEO_RESULT_URL
    assert captured["body"]["model"] == bot.KIE_VIDEO_T2V
    assert captured["body"]["input"]["duration"] == 15
    assert captured["body"]["input"]["aspect_ratio"] == "9:16"
    assert captured["body"]["input"]["resolution"] == "480p"
    assert captured["body"]["input"]["mode"] == "normal"


@pytest.mark.asyncio
async def test_kie_video_15_i2v_uses_preview_slug(no_sleep, sessions_file):
    sessions.set_video_config(7014, model="grok-imagine-video-1.5")
    model = {"key": "grok_video", "provider": "kie", "imagine_variant": "quality"}
    captured: dict = {}

    def capture_create(url, **kwargs):
        captured["create"] = kwargs["json"]

    with aioresponses() as mocked:
        mocked.post(
            KIE_UPLOAD_URL,
            payload={"code": 200, "data": {"fileUrl": "https://kieai.redpandaai.co/frame.jpg"}},
        )
        mocked.post(
            KIE_CREATE_URL,
            payload={"code": 200, "data": {"taskId": "task-abc"}},
            callback=capture_create,
        )
        mocked.get(
            "https://api.kie.ai/api/v1/jobs/recordInfo?taskId=task-abc",
            payload=_kie_success_poll_payload(VIDEO_RESULT_URL),
        )
        url, err = await bot._generate_kie_video(
            model,
            "gentle camera pan",
            image_data=BytesIO(b"\x89PNG\r\n\x1a\nimg"),
            user_id=7014,
        )

    assert err is None
    assert url == VIDEO_RESULT_URL
    assert captured["create"]["model"] == bot.KIE_VIDEO_15_I2V
    assert "mode" not in captured["create"]["input"]


@pytest.mark.asyncio
async def test_kie_video_15_t2v_uses_base_slug(no_sleep, sessions_file):
    sessions.set_video_config(7030, model="grok-imagine-video-1.5")
    model = {"key": "grok_video", "provider": "kie"}
    captured: dict = {}

    def capture_post(url, **kwargs):
        captured["body"] = kwargs["json"]

    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}}, callback=capture_post)
        mocked.get(
            "https://api.kie.ai/api/v1/jobs/recordInfo?taskId=task-abc",
            payload=_kie_success_poll_payload(VIDEO_RESULT_URL),
        )
        url, err = await bot._generate_kie_video(model, "sunset", user_id=7030)

    assert err is None
    assert url == VIDEO_RESULT_URL
    assert captured["body"]["model"] == bot.KIE_VIDEO_T2V
    assert captured["body"]["input"]["mode"] == "normal"


@pytest.mark.asyncio
async def test_kie_video_rejects_unsupported_aspect(sessions_file):
    sessions.set_video_config(7015, aspect_ratio="4:3", model="grok-imagine-video")
    model = {"key": "grok_video", "provider": "kie"}
    url, err = await bot._generate_kie_video(model, "test", user_id=7015)
    assert url is None
    assert "aspecto" in err.lower()


@pytest.mark.asyncio
async def test_kie_missing_api_key_image():
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image"}
    with patch.object(bot, "KIE_API_KEY", ""):
        output, err, meta = await bot._generate_kie(model, "prompt")
    assert output is None
    assert "administrador" in err


@pytest.mark.asyncio
async def test_kie_video_missing_api_key():
    model = {"key": "grok_video", "provider": "kie"}
    with patch.object(bot, "KIE_API_KEY", ""):
        url, err = await bot._generate_kie_video(model, "prompt")
    assert url is None
    assert "administrador" in err


@pytest.mark.asyncio
async def test_kie_upload_http_500(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    with aioresponses() as mocked:
        mocked.post(KIE_UPLOAD_URL, status=500, body="error")
        output, err, meta = await bot._generate_kie(model, "edit", image_data=BytesIO(b"jpeg"))
    assert output is None
    assert err == bot._kie_user_error("subida de imagen")


@pytest.mark.asyncio
async def test_kie_upload_http_200_code_402(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    with aioresponses() as mocked:
        mocked.post(KIE_UPLOAD_URL, payload={"code": 402, "msg": "Credits insufficient"})
        output, err, meta = await bot._generate_kie(model, "edit", image_data=BytesIO(b"jpeg"))
    assert output is None
    assert err == bot._kie_user_error("subida de imagen")


@pytest.mark.asyncio
async def test_kie_upload_missing_file_url(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    with aioresponses() as mocked:
        mocked.post(KIE_UPLOAD_URL, payload={"code": 200, "data": {}})
        output, err, meta = await bot._generate_kie(model, "edit", image_data=BytesIO(b"jpeg"))
    assert output is None
    assert "subir la imagen" in err


@pytest.mark.asyncio
async def test_kie_upload_rejects_bad_file_url_host(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    with aioresponses() as mocked:
        mocked.post(
            KIE_UPLOAD_URL,
            payload={"code": 200, "data": {"fileUrl": "https://evil.example.com/x.jpg"}},
        )
        output, err, meta = await bot._generate_kie(model, "edit", image_data=BytesIO(b"jpeg"))
    assert output is None
    assert err == bot._kie_user_error("subida de imagen")


@pytest.mark.asyncio
async def test_kie_poll_422_then_success(no_sleep):
    model = {
        "key": "grok",
        "provider": "kie",
        "id": "grok-imagine/text-to-image",
        "imagine_variant": "standard",
    }
    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}})
        mocked.get(KIE_POLL_URL, payload={"code": 422, "msg": "recordInfo is null"})
        mocked.get(KIE_POLL_URL, payload=_kie_success_poll_payload())
        output, err, meta = await bot._generate_kie(model, "prompt")

    assert err is None
    assert output == [RESULT_URL]


@pytest.mark.asyncio
async def test_kie_poll_timeout(no_sleep):
    model = {
        "key": "grok",
        "provider": "kie",
        "id": "grok-imagine/text-to-image",
        "imagine_variant": "standard",
    }
    start = 1000.0

    def fake_monotonic():
        fake_monotonic.counter += 700
        return fake_monotonic.counter

    fake_monotonic.counter = start

    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}})
        mocked.get(KIE_POLL_URL, payload={"code": 200, "data": {"state": "generating"}}, repeat=True)
        with patch("bot.time.monotonic", side_effect=fake_monotonic):
            output, err, meta = await bot._generate_kie(model, "slow prompt")

    assert output is None
    assert "Tiempo de espera agotado" in err


@pytest.mark.asyncio
async def test_kie_poll_fail_returns_user_error(no_sleep):
    model = {
        "key": "grok",
        "provider": "kie",
        "id": "grok-imagine/text-to-image",
        "imagine_variant": "standard",
    }
    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}})
        mocked.get(
            KIE_POLL_URL,
            payload={"code": 200, "data": {"state": "fail", "failCode": 99, "failMsg": "policy"}},
        )
        output, err, meta = await bot._generate_kie(model, "prompt")

    assert output is None
    assert err == bot._kie_user_error("generación")


@pytest.mark.asyncio
async def test_kie_create_task_error(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 422, "msg": "bad input"})
        output, err, meta = await bot._generate_kie(model, "prompt")
    assert output is None
    assert err == bot._kie_user_error("inicio de tarea")


@pytest.mark.asyncio
async def test_kie_i2v_rejects_oversized_image(no_sleep):
    model = {"key": "grok_video", "provider": "kie"}
    big = BytesIO(b"x" * (bot.I2V_MAX_IMAGE_BYTES + 1))
    url, err = await bot._generate_kie_video(model, "animate", image_data=big)
    assert url is None
    assert "demasiado grande" in err


@pytest.mark.asyncio
async def test_handle_cfg_variant_accepts_kie(sessions_file):
    uid = 7020
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:variant:quality"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    from conftest import make_fsm_context
    import config_flow

    state = make_fsm_context(
        config_model="grok",
        fsm_state=config_flow._state_key(config_flow.ConfigStates.configure),
    )
    bot._CONFIG_DEPS["safe_edit_text"] = AsyncMock()
    await bot.handle_cfg_variant(callback, state)

    cfg = sessions.get_grok_imagine_config(uid)
    assert cfg["provider"] == "kie"
    assert cfg["variant"] == "quality"


@pytest.mark.asyncio
async def test_handle_cfg_provider_preserves_grok_video_mode(sessions_file):
    uid = 7021
    bot.get_user_state(uid)["model"] = "grok_video"
    sessions.set_model(uid, "grok_video")

    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:provider:kie"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    from conftest import make_fsm_context
    import config_flow

    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=config_flow._state_key(config_flow.ConfigStates.select_provider),
    )
    bot._CONFIG_DEPS["safe_edit_text"] = AsyncMock()
    await bot.handle_cfg_provider(callback, state)

    assert bot.get_user_state(uid)["model"] == "grok_video"
    assert sessions.get_session(uid)["model"] == "grok_video"


@pytest.mark.asyncio
async def test_do_generate_video_passes_kie_download_allowlist():
    msg = MagicMock()
    msg.from_user.id = 7022
    msg.answer = AsyncMock()
    status = MagicMock()
    status.edit_text = AsyncMock()
    model = {"key": "grok_video", "provider": "kie"}

    with patch.object(bot, "generate_video", new_callable=AsyncMock, return_value=(VIDEO_RESULT_URL, None)):
        with patch.object(bot, "process_video_result", new_callable=AsyncMock) as mock_proc:
            await bot._do_generate_video(
                msg,
                model,
                "waves",
                user_id=7022,
                status_msg=status,
                reply_message=msg,
            )
    mock_proc.assert_awaited_once()
    assert mock_proc.await_args.kwargs["download_allowlist"] == "kie"


@pytest.mark.asyncio
async def test_download_url_kie_allowlist_blocks_evil():
    data, err = await bot.download_url(
        "https://evil.example.com/video.mp4",
        download_allowlist="kie",
    )
    assert data is None
    assert "origen no permitido" in err


@pytest.mark.asyncio
async def test_cmd_estado_grok_video_kie_shows_effective_duration(sessions_file):
    uid = 7023
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    sessions.set_video_config(uid, duration=5)
    bot.get_user_state(uid)["model"] = "grok_video"

    msg = MagicMock()
    msg.from_user.id = uid
    msg.answer = AsyncMock()

    await bot.cmd_estado(msg)

    text = msg.answer.await_args.args[0]
    assert "Kie.ai" in text
    assert "5s → 6s" in text


def test_sessions_rejects_invalid_provider(sessions_file):
    sessions.set_grok_imagine_config(7024, "bogus", "quality")
    cfg = sessions.get_grok_imagine_config(7024)
    assert cfg["provider"] == sessions.DEFAULT_GROK_IMAGINE_PROVIDER


def test_sessions_rejects_invalid_variant(sessions_file):
    sessions.set_grok_imagine_config(7028, "xai", "bogus")
    cfg = sessions.get_grok_imagine_config(7028)
    assert cfg["variant"] == sessions.DEFAULT_GROK_IMAGINE_VARIANT


def test_maybe_reset_kie_aspect_on_model_switch(sessions_file):
    uid = 7031
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    sessions.set_video_config(uid, model="grok-imagine-video-1.5", aspect_ratio="4:3")
    reset = bot._maybe_reset_kie_aspect_ratio(uid, video_model="grok-imagine-video")
    assert reset == sessions.DEFAULT_VIDEO_ASPECT_RATIO
    assert sessions.get_video_config(uid)["aspect_ratio"] == sessions.DEFAULT_VIDEO_ASPECT_RATIO


@pytest.mark.asyncio
async def test_handle_cfg_provider_kie_resets_stale_aspect(sessions_file):
    uid = 7032
    sessions.set_grok_imagine_config(uid, "xai", "quality")
    sessions.set_video_config(uid, aspect_ratio="4:3", model="grok-imagine-video")

    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:provider:kie"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    from conftest import make_fsm_context
    import config_flow

    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=config_flow._state_key(config_flow.ConfigStates.select_provider),
    )
    safe_edit = AsyncMock()
    bot._CONFIG_DEPS["safe_edit_text"] = safe_edit
    await bot.handle_cfg_provider(callback, state)

    assert sessions.get_video_config(uid)["aspect_ratio"] == sessions.DEFAULT_VIDEO_ASPECT_RATIO
    text = safe_edit.await_args.args[1]
    assert "ajustada" in text


@pytest.mark.asyncio
async def test_handle_cfg_video_model_switch_resets_aspect(sessions_file, mock_config_safe_edit):
    uid = 7033
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    sessions.set_video_config(uid, model="grok-imagine-video-1.5", aspect_ratio="4:3")

    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:video:model:grok-imagine-video"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    from conftest import make_fsm_context
    import config_flow

    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=config_flow._state_key(config_flow.ConfigStates.configure),
    )
    await bot.handle_cfg_video(callback, state)

    assert sessions.get_video_config(uid)["aspect_ratio"] == sessions.DEFAULT_VIDEO_ASPECT_RATIO
    edit_text = mock_config_safe_edit.await_args.args[1]
    assert "ajustada" in edit_text


@pytest.mark.asyncio
async def test_kie_upload_rejects_success_false(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    with aioresponses() as mocked:
        mocked.post(KIE_UPLOAD_URL, payload={"code": 200, "success": False, "msg": "failed"})
        output, err, meta = await bot._generate_kie(model, "edit", image_data=BytesIO(b"jpeg"))
    assert output is None
    assert err == bot._kie_user_error("subida de imagen")


def test_kie_download_allowlist_includes_file_aiquickdraw():
    assert bot._is_allowed_kie_download_host("file.aiquickdraw.com") is True


def test_kie_download_allowlist_includes_tempfile_aiquickdraw():
    assert bot._is_allowed_kie_download_host("tempfile.aiquickdraw.com") is True
    assert bot._is_allowed_kie_asset_url("https://tempfile.aiquickdraw.com/out.png") is True


@pytest.mark.asyncio
async def test_kie_poll_malformed_success_missing_result_json(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}})
        mocked.get(KIE_POLL_URL, payload={"code": 200, "data": {"state": "success"}})
        output, err, meta = await bot._generate_kie(model, "prompt")
    assert output is None
    assert "resultado" in err.lower()


@pytest.mark.asyncio
async def test_kie_poll_malformed_success_bad_json(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}})
        mocked.get(
            KIE_POLL_URL,
            payload={"code": 200, "data": {"state": "success", "resultJson": "not-json"}},
        )
        output, err, meta = await bot._generate_kie(model, "prompt")
    assert output is None
    assert "interpretar" in err.lower()


@pytest.mark.asyncio
async def test_kie_poll_success_empty_result_urls(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}})
        mocked.get(
            KIE_POLL_URL,
            payload={
                "code": 200,
                "data": {
                    "state": "success",
                    "resultJson": json.dumps({"resultUrls": []}),
                },
            },
        )
        output, err, meta = await bot._generate_kie(model, "prompt")
    assert output is None
    assert "URL de resultado" in err


@pytest.mark.asyncio
async def test_kie_poll_success_blocks_evil_result_host(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}})
        mocked.get(
            KIE_POLL_URL,
            payload={
                "code": 200,
                "data": {
                    "state": "success",
                    "resultJson": json.dumps({"resultUrls": ["https://evil.example.com/x.png"]}),
                },
            },
        )
        output, err, meta = await bot._generate_kie(model, "prompt")
    assert output is None
    assert err == bot._kie_user_error("descarga de resultado")


@pytest.mark.asyncio
async def test_kie_poll_http_401_aborts_immediately(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}})
        mocked.get(KIE_POLL_URL, status=401, body="unauthorized")
        mocked.get(KIE_POLL_URL, payload=_kie_success_poll_payload())
        output, err, meta = await bot._generate_kie(model, "prompt")
    assert output is None
    assert err == bot._kie_user_error("consulta de tarea")


@pytest.mark.asyncio
async def test_kie_create_task_http_500(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, status=500, body="boom")
        output, err, meta = await bot._generate_kie(model, "prompt")
    assert output is None
    assert err == bot._kie_user_error("inicio de tarea")


@pytest.mark.asyncio
async def test_kie_create_task_missing_task_id(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {}})
        output, err, meta = await bot._generate_kie(model, "prompt")
    assert output is None
    assert "iniciar" in err.lower()


@pytest.mark.asyncio
async def test_kie_poll_5xx_then_success(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}})
        mocked.get(KIE_POLL_URL, status=503, body="busy")
        mocked.get(KIE_POLL_URL, payload=_kie_success_poll_payload())
        output, err, meta = await bot._generate_kie(model, "prompt")
    assert err is None
    assert output == [RESULT_URL]


@pytest.mark.asyncio
async def test_kie_poll_429_then_success(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}})
        mocked.get(KIE_POLL_URL, status=429, body="rate limit")
        mocked.get(KIE_POLL_URL, payload=_kie_success_poll_payload())
        output, err, meta = await bot._generate_kie(model, "prompt")
    assert err is None
    assert output == [RESULT_URL]


@pytest.mark.asyncio
async def test_kie_poll_network_error_then_success(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}})
        mocked.get(KIE_POLL_URL, exception=aiohttp.ClientError("timeout"))
        mocked.get(KIE_POLL_URL, payload=_kie_success_poll_payload())
        output, err, meta = await bot._generate_kie(model, "prompt")
    assert err is None
    assert output == [RESULT_URL]


@pytest.mark.asyncio
async def test_kie_video_status_updates_during_poll(no_sleep):
    status_msg = MagicMock()
    model = {"key": "grok_video", "provider": "kie"}
    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}})
        mocked.get(KIE_POLL_URL, payload={"code": 200, "data": {"state": "waiting"}})
        mocked.get(KIE_POLL_URL, payload=_kie_success_poll_payload(VIDEO_RESULT_URL))
        with patch.object(bot, "safe_edit_text", new_callable=AsyncMock) as safe_edit:
            await bot._generate_kie_video(model, "prompt <b>x</b>", status_msg=status_msg, user_id=None)
            assert safe_edit.await_count >= 1
            call_text = safe_edit.await_args_list[0].args[1]
            assert "<b>x</b>" not in call_text
            assert "en cola" in call_text


@pytest.mark.asyncio
async def test_kie_video_status_updates_generating(no_sleep):
    status_msg = MagicMock()
    model = {"key": "grok_video", "provider": "kie"}
    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}})
        mocked.get(KIE_POLL_URL, payload={"code": 200, "data": {"state": "generating"}})
        mocked.get(KIE_POLL_URL, payload=_kie_success_poll_payload(VIDEO_RESULT_URL))
        with patch.object(bot, "safe_edit_text", new_callable=AsyncMock) as safe_edit:
            await bot._generate_kie_video(model, "prompt", status_msg=status_msg, user_id=None)
            assert safe_edit.await_count >= 1
            assert any("procesando" in call.args[1] for call in safe_edit.await_args_list)


@pytest.mark.asyncio
async def test_kie_video_unknown_state_shows_elapsed(no_sleep):
    status_msg = MagicMock()
    model = {"key": "grok_video", "provider": "kie"}
    start = 1000.0

    def fake_monotonic():
        fake_monotonic.counter += 35
        return fake_monotonic.counter

    fake_monotonic.counter = start

    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}})
        mocked.get(KIE_POLL_URL, payload={"code": 200, "data": {"state": "weird"}})
        mocked.get(KIE_POLL_URL, payload=_kie_success_poll_payload(VIDEO_RESULT_URL))
        with patch("bot.time.monotonic", side_effect=fake_monotonic):
            with patch.object(bot, "safe_edit_text", new_callable=AsyncMock) as safe_edit:
                await bot._generate_kie_video(model, "prompt", status_msg=status_msg, user_id=None)
                assert any("transcurridos" in call.args[1] for call in safe_edit.await_args_list)


@pytest.mark.asyncio
async def test_do_generate_text_passes_kie_image_download_allowlist():
    msg = MagicMock()
    msg.from_user.id = 7034
    msg.answer = AsyncMock()
    status = MagicMock()
    status.edit_text = AsyncMock()
    model = {
        "key": "grok",
        "provider": "kie",
        "id": "grok-imagine/text-to-image",
        "name": "Grok Imagine (Kie.ai • Estándar)",
    }

    with patch.object(bot, "generate_image", new_callable=AsyncMock, return_value=([RESULT_URL], None, None)):
        with patch.object(bot, "process_image_result", new_callable=AsyncMock) as mock_proc:
            await bot._do_generate_text(msg, model, "cat")
    mock_proc.assert_awaited_once()
    assert mock_proc.await_args.kwargs["download_allowlist"] == "kie"


@pytest.mark.asyncio
async def test_download_url_kie_cdn_success():
    class FakeResponse:
        status = 200
        url = "https://static.aiquickdraw.com/video.mp4"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        @property
        def content(self):
            return self

        def iter_chunked(self, _size):
            async def _gen():
                yield b"video-bytes"

            return _gen()

    class FakeSession:
        def get(self, *args, **kwargs):
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    with patch("bot.aiohttp.ClientSession", return_value=FakeSession()):
        data, err = await bot.download_url(
            "https://static.aiquickdraw.com/video.mp4",
            download_allowlist="kie",
        )
    assert err is None
    assert data == b"video-bytes"


@pytest.mark.asyncio
async def test_download_url_kie_blocks_redirect_to_evil():
    class FakeResponse:
        status = 200
        url = "https://evil.example.com/video.mp4"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        @property
        def content(self):
            return self

        def iter_chunked(self, _size):
            async def _gen():
                yield b"data"

            return _gen()

    class FakeSession:
        def get(self, *args, **kwargs):
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    with patch("bot.aiohttp.ClientSession", return_value=FakeSession()):
        data, err = await bot.download_url(
            "https://static.aiquickdraw.com/video.mp4",
            download_allowlist="kie",
        )
    assert data is None
    assert "origen no permitido" in err


@pytest.mark.asyncio
async def test_kie_video_status_label_15_t2v_fallback():
    label = bot._kie_video_status_label("grok-imagine-video-1.5", image_to_video=False)
    assert "modelo base" in label


@pytest.mark.asyncio
async def test_cmd_imaginess_kie_shows_missing_key_warning(sessions_file):
    msg = MagicMock()
    msg.from_user.id = 7035
    msg.answer = AsyncMock()
    sessions.set_grok_imagine_config(7035, "kie", "quality")
    state = MagicMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()

    bot._CONFIG_DEPS["kie_configured"] = False
    await bot.cmd_imaginess(msg, state)

    text = msg.answer.await_args.args[0]
    assert "administrador" in text
    assert bot._KIE_PRIVACY_NOTICE in text


@pytest.mark.asyncio
async def test_handle_cfg_provider_missing_key_warning(sessions_file):
    uid = 7037
    sessions.set_grok_imagine_config(uid, "xai", "quality")

    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:provider:kie"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    from conftest import make_fsm_context
    import config_flow

    state = make_fsm_context(
        config_model="grok",
        fsm_state=config_flow._state_key(config_flow.ConfigStates.select_provider),
    )
    safe_edit = AsyncMock()
    bot._CONFIG_DEPS["kie_configured"] = False
    bot._CONFIG_DEPS["safe_edit_text"] = safe_edit
    await bot.handle_cfg_provider(callback, state)

    text = safe_edit.await_args.args[1]
    assert "administrador" in text
    assert bot._KIE_PRIVACY_NOTICE in text


@pytest.mark.asyncio
async def test_kie_i2i_rejects_oversized_image(no_sleep):
    model = {"key": "grok", "provider": "kie", "id": "grok-imagine/text-to-image", "imagine_variant": "standard"}
    big = BytesIO(b"x" * (bot.I2V_MAX_IMAGE_BYTES + 1))
    output, err, meta = await bot._generate_kie(model, "edit", image_data=big)
    assert output is None
    assert "demasiado grande" in err


@pytest.mark.asyncio
async def test_handle_cfg_video_rejects_kie_invalid_aspect(sessions_file, mock_config_safe_edit):
    uid = 7036
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    sessions.set_video_config(uid, model="grok-imagine-video", aspect_ratio="16:9")

    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:video:aspect:4:3"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    from conftest import make_fsm_context
    import config_flow

    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=config_flow._state_key(config_flow.ConfigStates.configure),
    )
    await bot.handle_cfg_video(callback, state)

    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True
    assert "no disponible" in callback.answer.await_args.args[0].lower()
    assert sessions.get_video_config(uid)["aspect_ratio"] == "16:9"


@pytest.mark.parametrize("duration", [3, 31])
def test_kie_map_duration_extremes(duration):
    assert bot._kie_map_duration(duration) == 6 if duration < 6 else 30


@pytest.mark.asyncio
async def test_process_video_result_oversized_includes_sensitive_warning():
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    message = MagicMock()
    huge = b"x" * (bot.TELEGRAM_MAX_VIDEO_BYTES + 1)
    with patch.object(bot, "download_url", new_callable=AsyncMock, return_value=(huge, None)):
        await bot.process_video_result(
            VIDEO_RESULT_URL,
            "prompt",
            status_msg,
            message,
            "Prompt",
            download_allowlist="kie",
        )
    text = status_msg.edit_text.await_args.args[0]
    assert "no lo compartas" in text
    assert VIDEO_RESULT_URL in text


def test_sanitize_kie_fail_log_truncates():
    long_msg = "x" * 200
    sanitized = bot._sanitize_kie_fail_log(long_msg)
    assert len(sanitized) <= 81
    assert sanitized.endswith("…")


def test_default_grok_imagine_provider_is_kie():
    assert sessions.DEFAULT_GROK_IMAGINE_PROVIDER == "kie"
    assert bot.DEFAULT_GROK_IMAGINE_PROVIDER == "kie"


def test_config_provider_keyboard_kie_first(sessions_file):
    uid = 7042
    sessions.set_grok_imagine_config(uid, "xai", "quality")
    kb = bot.config_provider_keyboard(uid)
    first_row = kb.inline_keyboard[0][0]
    assert "Kie.ai" in first_row.text


def test_get_video_config_includes_mode_default(sessions_file):
    cfg = sessions.get_video_config(8001)
    assert cfg["mode"] == sessions.DEFAULT_VIDEO_MODE


def test_save_and_get_generation_ref(generation_refs_file):
    sessions.save_generation_ref(100, 42, kie_task_id="task-xyz", kie_index=2, prompt="cat")
    ref = sessions.get_generation_ref(100, 42)
    assert ref["kie_task_id"] == "task-xyz"
    assert ref["kie_index"] == 2
    assert ref["provider"] == "kie"


@pytest.mark.asyncio
async def test_kie_i2v_task_id_uses_spicy_mode(no_sleep, sessions_file):
    sessions.set_video_config(8010, mode="spicy")
    model = {"key": "grok_video", "provider": "kie"}
    captured: dict = {}

    def capture_post(url, **kwargs):
        captured["body"] = kwargs["json"]

    with aioresponses() as mocked:
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}}, callback=capture_post)
        mocked.get(
            "https://api.kie.ai/api/v1/jobs/recordInfo?taskId=task-abc",
            payload=_kie_success_poll_payload(VIDEO_RESULT_URL),
        )
        url, err = await bot._generate_kie_video(
            model,
            "dance slowly",
            kie_source_ref={"task_id": "prior-task", "index": 1},
            user_id=8010,
        )

    assert err is None
    assert url == VIDEO_RESULT_URL
    assert captured["body"]["model"] == bot.KIE_VIDEO_I2V
    assert captured["body"]["input"]["task_id"] == "prior-task"
    assert captured["body"]["input"]["index"] == 1
    assert captured["body"]["input"]["mode"] == "spicy"
    assert "image_urls" not in captured["body"]["input"]


@pytest.mark.asyncio
async def test_kie_i2v_external_image_forces_normal_when_spicy(no_sleep, sessions_file):
    sessions.set_video_config(8011, mode="spicy")
    model = {"key": "grok_video", "provider": "kie"}
    captured: dict = {}

    def capture_post(url, **kwargs):
        captured["body"] = kwargs["json"]

    with aioresponses() as mocked:
        mocked.post(
            KIE_UPLOAD_URL,
            payload={"code": 200, "data": {"fileUrl": "https://kieai.redpandaai.co/frame.jpg"}},
        )
        mocked.post(KIE_CREATE_URL, payload={"code": 200, "data": {"taskId": "task-abc"}}, callback=capture_post)
        mocked.get(
            "https://api.kie.ai/api/v1/jobs/recordInfo?taskId=task-abc",
            payload=_kie_success_poll_payload(VIDEO_RESULT_URL),
        )
        url, err = await bot._generate_kie_video(
            model,
            "wave",
            image_data=BytesIO(b"\xff\xd8\xffjpeg"),
            user_id=8011,
        )

    assert err is None
    assert captured["body"]["input"]["mode"] == "normal"
    assert "image_urls" in captured["body"]["input"]


@pytest.mark.asyncio
async def test_kie_i2i_from_task_id_resolves_image_url(no_sleep):
    model = {
        "key": "grok",
        "provider": "kie",
        "id": "grok-imagine/text-to-image",
        "imagine_variant": "standard",
    }
    ref_url = "https://kieai.redpandaai.co/static/ref.png"
    captured: dict = {}

    def capture_create(url, **kwargs):
        captured["create"] = kwargs["json"]

    with aioresponses() as mocked:
        mocked.get(
            "https://api.kie.ai/api/v1/jobs/recordInfo?taskId=prior-task",
            payload=_kie_success_poll_payload(ref_url),
        )
        mocked.post(
            KIE_CREATE_URL,
            payload={"code": 200, "data": {"taskId": "task-abc"}},
            callback=capture_create,
        )
        mocked.get(KIE_POLL_URL, payload=_kie_success_poll_payload())
        output, err, meta = await bot._generate_kie(
            model,
            "add hat",
            kie_source_ref={"task_id": "prior-task", "index": 0},
        )

    assert err is None
    assert output == [RESULT_URL]
    assert captured["create"]["input"]["image_urls"] == [ref_url]
    assert captured["create"]["model"] == bot.KIE_IMAGE_I2I


@pytest.mark.asyncio
async def test_process_image_result_saves_generation_ref(generation_refs_file):
    status_msg = MagicMock()
    status_msg.delete = AsyncMock()
    message = MagicMock()
    message.chat.id = 200
    sent = MagicMock()
    sent.message_id = 99
    message.answer_photo = AsyncMock(return_value=sent)

    with patch.object(bot, "download_url", new_callable=AsyncMock, return_value=(b"png", None)):
        await bot.process_image_result(
            [RESULT_URL],
            "neon cat",
            status_msg,
            message,
            "Prompt",
            download_allowlist="kie",
            kie_meta={"task_id": "task-save", "index": 0, "provider": "kie"},
        )

    ref = sessions.get_generation_ref(200, 99)
    assert ref["kie_task_id"] == "task-save"


@pytest.mark.asyncio
async def test_handle_reply_edit_uses_kie_task_id_for_video(no_sleep, sessions_file, generation_refs_file):
    uid = 8012
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    bot.get_user_state(uid)["model"] = "grok_video"

    reply_msg = MagicMock()
    reply_msg.photo = [MagicMock(file_id="fid")]
    reply_msg.chat.id = 300
    reply_msg.message_id = 50

    message = MagicMock()
    message.from_user.id = uid
    message.text = "gentle zoom out"
    message.reply_to_message = reply_msg
    message.answer = AsyncMock()

    sessions.save_generation_ref(300, 50, kie_task_id="stored-task", kie_index=3)

    with patch.object(bot, "_download_telegram_photo", new_callable=AsyncMock) as mock_dl:
        with patch.object(bot, "_do_generate_video", new_callable=AsyncMock) as mock_video:
            await bot.handle_reply_edit(message)

    mock_dl.assert_not_awaited()
    mock_video.assert_awaited_once()
    assert mock_video.await_args.kwargs["kie_source_ref"] == {
        "task_id": "stored-task",
        "index": 3,
    }
    assert mock_video.await_args.args[3] is None