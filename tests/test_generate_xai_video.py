"""Tests for xAI video generation polling and error handling."""

from __future__ import annotations

import base64
import json
from io import BytesIO
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from aioresponses import aioresponses

import bot
import sessions

MODEL = bot.MODELS["grok_video"]
GEN_URL = "https://api.x.ai/v1/videos/generations"
POLL_URL = "https://api.x.ai/v1/videos/req-1"
VIDEO_URL = "https://cdn.x.ai/generated.mp4"


def _done_payload(*, respect_moderation=True, url=VIDEO_URL):
    return {
        "status": "done",
        "video": {
            "url": url,
            "respect_moderation": respect_moderation,
        },
    }


@pytest.fixture
def no_sleep():
    async def _noop(_seconds):
        return None

    with patch("bot.asyncio.sleep", new=_noop):
        yield


@pytest.mark.asyncio
async def test_poll_done_returns_url(no_sleep, sessions_file):
    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-1"})
        mocked.get(POLL_URL, payload=_done_payload())
        url, err = await bot._generate_xai_video(MODEL, "a cat lounging", user_id=42)

    assert err is None
    assert url == VIDEO_URL


@pytest.mark.asyncio
async def test_poll_failed_sanitized_for_user(no_sleep):
    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-1"})
        mocked.get(
            POLL_URL,
            payload={"status": "failed", "error": {"message": "content policy violation"}},
        )
        url, err = await bot._generate_xai_video(MODEL, "bad prompt")

    assert url is None
    assert err == bot._xai_user_error("generación de video (failed)")
    assert "content policy violation" not in err


@pytest.mark.asyncio
async def test_poll_expired_sanitized_for_user(no_sleep):
    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-1"})
        mocked.get(POLL_URL, payload={"status": "expired", "message": "request timed out"})
        url, err = await bot._generate_xai_video(MODEL, "prompt")

    assert url is None
    assert err == bot._xai_user_error("generación de video (expired)")
    assert "request timed out" not in err


@pytest.mark.asyncio
async def test_poll_timeout(no_sleep):
    start = 1000.0

    def fake_monotonic():
        fake_monotonic.counter += 700
        return fake_monotonic.counter

    fake_monotonic.counter = start

    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-1"})
        mocked.get(POLL_URL, payload={"status": "processing"}, repeat=True)
        with patch("bot.time.monotonic", side_effect=fake_monotonic):
            url, err = await bot._generate_xai_video(MODEL, "slow prompt")

    assert url is None
    assert "Tiempo de espera agotado" in err


@pytest.mark.asyncio
async def test_done_missing_video_url(no_sleep):
    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-1"})
        mocked.get(
            POLL_URL,
            payload={"status": "done", "video": {"respect_moderation": True}},
        )
        url, err = await bot._generate_xai_video(MODEL, "prompt")

    assert url is None
    assert err == "No se recibió URL de video. Intenta de nuevo."


@pytest.mark.asyncio
async def test_respect_moderation_false_nested_in_video(no_sleep):
    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-1"})
        mocked.get(
            POLL_URL,
            payload={
                "status": "done",
                "video": {"url": VIDEO_URL, "respect_moderation": False},
            },
        )
        url, err = await bot._generate_xai_video(MODEL, "prompt")

    assert url is None
    assert err == "El contenido no cumple las políticas de moderación."


@pytest.mark.asyncio
async def test_post_http_error_sanitized(no_sleep):
    with aioresponses() as mocked:
        mocked.post(GEN_URL, status=500, body="internal explosion")
        url, err = await bot._generate_xai_video(MODEL, "prompt")

    assert url is None
    assert "internal explosion" not in err
    assert err == bot._xai_user_error("generación de video")


@pytest.mark.asyncio
async def test_poll_http_error_sanitized(no_sleep):
    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-1"})
        mocked.get(POLL_URL, status=404, body="not found", repeat=True)
        url, err = await bot._generate_xai_video(MODEL, "prompt")

    assert url is None
    assert "not found" not in err


@pytest.mark.asyncio
async def test_poll_retries_on_5xx(no_sleep):
    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-1"})
        mocked.get(POLL_URL, status=503, body="busy")
        mocked.get(POLL_URL, payload=_done_payload())
        url, err = await bot._generate_xai_video(MODEL, "prompt")

    assert err is None
    assert url == VIDEO_URL


@pytest.mark.asyncio
async def test_missing_request_id(no_sleep):
    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={})
        url, err = await bot._generate_xai_video(MODEL, "prompt")

    assert url is None
    assert err == "No se pudo iniciar la generación de video. Intenta de nuevo."


@pytest.mark.asyncio
async def test_i2v_rejects_oversized_image(no_sleep):
    big = BytesIO(b"x" * (bot.I2V_MAX_IMAGE_BYTES + 1))
    url, err = await bot._generate_xai_video(MODEL, "animate", image_data=big)

    assert url is None
    assert "demasiado grande" in err


@pytest.mark.asyncio
async def test_status_message_updates_use_safe_edit(no_sleep):
    status_msg = AsyncMock()
    status_msg.edit_text = AsyncMock()

    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-1"})
        mocked.get(POLL_URL, payload={"status": "pending"})
        mocked.get(POLL_URL, payload=_done_payload())
        with patch("bot.safe_edit_text", new_callable=AsyncMock) as safe_edit:
            await bot._generate_xai_video(
                MODEL,
                "prompt <b>tag</b>",
                status_msg=status_msg,
            )
            assert safe_edit.await_count >= 1
            call_text = safe_edit.await_args_list[0].args[1]
            assert "<b>tag</b>" not in call_text
            assert "&lt;b&gt;tag&lt;/b&gt;" in call_text


@pytest.mark.asyncio
async def test_post_body_t2v_uses_session_config(no_sleep, sessions_file):
    sessions_file.write_text(
        json.dumps(
            {
                "77": {
                    "video_duration": 10,
                    "video_aspect_ratio": "9:16",
                    "video_resolution": "480p",
                }
            }
        )
    )
    captured: dict = {}

    def capture_post(url, **kwargs):
        captured["body"] = kwargs["json"]

    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-1"}, callback=capture_post, repeat=True)
        mocked.get(POLL_URL, payload=_done_payload())
        await bot._generate_xai_video(MODEL, "sunset over mountains", user_id=77)

    assert captured["body"]["model"] == "grok-imagine-video"
    assert captured["body"]["duration"] == 10
    assert captured["body"]["aspect_ratio"] == "9:16"
    assert captured["body"]["resolution"] == "480p"
    assert "image" not in captured["body"]


@pytest.mark.asyncio
async def test_post_body_i2v_uses_persisted_model_and_payload(no_sleep, sessions_file):
    sessions.set_video_config(88, model="grok-imagine-video-1.5")
    captured: dict = {}
    image_bytes = b"fake-jpeg-data"

    def capture_post(url, **kwargs):
        captured["body"] = kwargs["json"]

    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-1"}, callback=capture_post, repeat=True)
        mocked.get(POLL_URL, payload=_done_payload())
        await bot._generate_xai_video(
            MODEL,
            "animate waves",
            image_data=BytesIO(image_bytes),
            user_id=88,
        )

    assert captured["body"]["model"] == "grok-imagine-video-1.5"
    expected_b64 = base64.b64encode(image_bytes).decode()
    assert captured["body"]["image"]["url"] == f"data:image/jpeg;base64,{expected_b64}"


@pytest.mark.asyncio
async def test_successful_post_records_hourly_usage(no_sleep, sessions_file):
    user_id = 9090
    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-1"})
        mocked.get(POLL_URL, payload={"status": "processing"})
        await bot._generate_xai_video(MODEL, "prompt", user_id=user_id)

    assert sessions.count_video_hourly_usage(user_id) == 1


@pytest.mark.asyncio
async def test_failed_poll_still_records_hourly_after_post(no_sleep, sessions_file):
    user_id = 9091
    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-1"})
        mocked.get(POLL_URL, payload={"status": "failed", "error": {"message": "nope"}})
        await bot._generate_xai_video(MODEL, "prompt", user_id=user_id)

    assert sessions.count_video_hourly_usage(user_id) == 1


@pytest.mark.asyncio
async def test_done_does_not_double_count_hourly(no_sleep, sessions_file):
    user_id = 9092
    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-1"})
        mocked.get(POLL_URL, payload=_done_payload())
        await bot._generate_xai_video(MODEL, "prompt", user_id=user_id)

    assert sessions.count_video_hourly_usage(user_id) == 1


@pytest.mark.asyncio
async def test_generate_video_unsupported_provider():
    model = {"key": "other", "provider": "replicate", "id": "x"}
    url, err = await bot.generate_video(model, "prompt")
    assert url is None
    assert "no soportado" in err.lower()


@pytest.mark.asyncio
async def test_unknown_status_updates_elapsed_message(no_sleep):
    status_msg = AsyncMock()
    start = 1000.0

    def fake_monotonic():
        fake_monotonic.counter += 35
        return fake_monotonic.counter

    fake_monotonic.counter = start

    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-1"})
        mocked.get(POLL_URL, payload={"status": "weird"})
        mocked.get(POLL_URL, payload=_done_payload())
        with patch("bot.time.monotonic", side_effect=fake_monotonic):
            with patch("bot.safe_edit_text", new_callable=AsyncMock) as safe_edit:
                await bot._generate_xai_video(MODEL, "prompt", status_msg=status_msg)
                assert any("transcurridos" in call.args[1] for call in safe_edit.await_args_list)


@pytest.mark.asyncio
async def test_poll_network_error_retries(no_sleep):
    with aioresponses() as mocked:
        mocked.post(GEN_URL, payload={"request_id": "req-1"})
        mocked.get(POLL_URL, exception=aiohttp.ClientError("timeout"))
        mocked.get(POLL_URL, payload=_done_payload())
        url, err = await bot._generate_xai_video(MODEL, "prompt")

    assert err is None
    assert url == VIDEO_URL


@pytest.mark.asyncio
async def test_poll_http_202_accepted_continues_until_done(no_sleep, sessions_file):
    """xAI returns HTTP 202 on poll while the video is still processing."""
    with aioresponses() as mocked:
        mocked.post(GEN_URL, status=202, payload={"request_id": "req-1"})
        mocked.get(POLL_URL, status=202, payload={"status": "pending"})
        mocked.get(POLL_URL, status=202, payload={"status": "processing"})
        mocked.get(POLL_URL, status=200, payload=_done_payload())
        url, err = await bot._generate_xai_video(MODEL, "prompt", user_id=42)

    assert err is None
    assert url == VIDEO_URL