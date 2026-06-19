"""Optional live smoke tests (skipped unless LIVE_SMOKE=1).

Run against the real xAI API:

    LIVE_SMOKE=1 XAI_API_KEY=xai-... ./venv/bin/python -m pytest tests/test_live_smoke.py -q

Without ``LIVE_SMOKE=1`` the module is skipped by default so CI and local
``pytest tests/`` runs stay fast and offline. When enabled, tests that need a
real credential skip if ``XAI_API_KEY`` is unset or still the unit-test dummy
from ``conftest.py``.
"""

from __future__ import annotations

import asyncio
import os

import pytest

import bot

MODEL = bot.MODELS["grok_video"]
_LIVE_SMOKE_ENABLED = os.environ.get("LIVE_SMOKE") == "1"
_SKIP_LIVE = pytest.mark.skipif(
    not _LIVE_SMOKE_ENABLED,
    reason="Set LIVE_SMOKE=1 to run live smoke tests",
)


def _real_xai_api_key() -> str | None:
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key or key == "xai-test-key":
        return None
    return key


@_SKIP_LIVE
@pytest.mark.asyncio
async def test_live_smoke_imports_and_registry():
    assert bot.MODELS["grok_video"]["provider"] == "xai"
    assert bot.XAI_BASE.startswith("https://")


@_SKIP_LIVE
@pytest.mark.asyncio
async def test_live_smoke_xai_video_post():
    if _real_xai_api_key() is None:
        pytest.skip("Set a real XAI_API_KEY to exercise the live xAI video API")

    url, err = await asyncio.wait_for(
        bot._generate_xai_video(
            MODEL,
            "a single red dot on white background",
            user_id=None,
        ),
        timeout=300,
    )
    assert err is None, err
    assert url
    assert url.startswith("http")