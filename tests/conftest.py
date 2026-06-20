"""Shared pytest fixtures. Sets dummy env vars before importing bot."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "000000000:TEST-TOKEN-FOR-UNIT-TESTS")
os.environ.setdefault("REPLICATE_API_TOKEN", "r8_test")
os.environ.setdefault("XAI_API_KEY", "xai-test-key")
os.environ.setdefault("KIE_API_KEY", "kie-test-key")

import pytest  # noqa: E402

import bot  # noqa: E402
import config_flow  # noqa: E402
import sessions  # noqa: E402


def make_fsm_context(*, fsm_state: str | None = None, **data):
    """Build an FSMContext mock with async get_state for /config handler tests."""
    state = MagicMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    state.get_data = AsyncMock(return_value=data)
    state.clear = AsyncMock()
    if fsm_state is None:
        config_model = data.get("config_model")
        if config_model is None:
            fsm_state = config_flow._state_key(config_flow.ConfigStates.select_model)
        elif config_model == "grok_video":
            fsm_state = config_flow._state_key(config_flow.ConfigStates.configure)
        else:
            fsm_state = config_flow._state_key(config_flow.ConfigStates.select_provider)
    state.get_state = AsyncMock(return_value=fsm_state)
    return state


@pytest.fixture(autouse=True)
def reset_runtime_state():
    bot.user_state.clear()
    deps_snapshot = dict(bot._CONFIG_DEPS)
    yield
    bot._CONFIG_DEPS.clear()
    bot._CONFIG_DEPS.update(deps_snapshot)


@pytest.fixture
def sessions_file(tmp_path, monkeypatch):
    path = tmp_path / "sessions.json"
    monkeypatch.setattr(sessions, "SESSIONS_FILE", path)
    return path


@pytest.fixture
def generation_refs_file(tmp_path, monkeypatch):
    path = tmp_path / "generation_refs.json"
    monkeypatch.setattr(sessions, "GENERATION_REFS_FILE", path)
    return path


@pytest.fixture
def mock_config_safe_edit(monkeypatch):
    """Patch safe_edit_text inside the unified /config deps dict."""
    from unittest.mock import AsyncMock

    mock = AsyncMock()
    monkeypatch.setitem(bot._CONFIG_DEPS, "safe_edit_text", mock)
    return mock