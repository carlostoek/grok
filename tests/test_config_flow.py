"""Tests for unified /config FSM flow and cfg:* callbacks."""

from __future__ import annotations

from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

import bot
import config_flow
import sessions
from conftest import make_fsm_context


def _state_key(st: config_flow.ConfigStates) -> str:
    return config_flow._state_key(st)


def test_state_key_matches_aiogram_fsm_format():
    assert _state_key(config_flow.ConfigStates.select_model) == "ConfigStates:select_model"
    assert _state_key(config_flow.ConfigStates.select_provider) == "ConfigStates:select_provider"
    assert _state_key(config_flow.ConfigStates.configure) == "ConfigStates:configure"


@pytest.mark.asyncio
async def test_reject_stale_callback_accepts_matching_fsm_state():
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    storage = MemoryStorage()
    ctx = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=1, chat_id=9001, user_id=9001),
    )
    await ctx.set_state(config_flow.ConfigStates.select_model)
    await ctx.update_data(config_message_id=100)

    callback = MagicMock()
    callback.message = MagicMock()
    callback.message.message_id = 100
    callback.answer = AsyncMock()

    rejected = await config_flow._reject_stale_callback(
        callback,
        ctx,
        allowed_states=(config_flow.ConfigStates.select_model,),
    )
    assert rejected is False
    callback.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_cmd_config_shows_model_keyboard(sessions_file):
    msg = MagicMock()
    msg.from_user.id = 7001
    msg.answer = AsyncMock()
    state = make_fsm_context()

    await bot.cmd_config(msg, state)

    msg.answer.assert_awaited_once()
    kwargs = msg.answer.await_args.kwargs
    assert kwargs.get("reply_markup") is not None
    assert "Selecciona el modelo" in msg.answer.await_args.args[0]
    state.set_state.assert_awaited_with(config_flow.ConfigStates.select_model)


@pytest.mark.asyncio
async def test_cmd_model_alias_opens_level_one(sessions_file):
    msg = MagicMock()
    msg.from_user.id = 7008
    msg.answer = AsyncMock()
    state = make_fsm_context()

    await bot.cmd_model(msg, state)

    msg.answer.assert_awaited_once()
    assert "Selecciona el modelo" in msg.answer.await_args.args[0]
    state.set_state.assert_awaited_with(config_flow.ConfigStates.select_model)


@pytest.mark.asyncio
async def test_cmd_imagine_aliases_open_variant_when_provider_set(sessions_file):
    msg = MagicMock()
    msg.from_user.id = 7010
    msg.answer = AsyncMock(return_value=MagicMock(message_id=42))
    state = make_fsm_context()
    sessions.set_grok_imagine_config(7010, "kie", "quality")

    await bot.cmd_imaginess(msg, state)

    msg.answer.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    assert "nivel de calidad" in text.lower()
    assert "proveedor" not in text.lower()
    state.set_state.assert_awaited_with(config_flow.ConfigStates.configure)
    state.update_data.assert_any_await(config_model="grok")
    state.update_data.assert_any_await(config_message_id=ANY)


@pytest.mark.asyncio
async def test_cmd_video_shows_keyboard(sessions_file):
    msg = MagicMock()
    msg.from_user.id = 7001
    msg.answer = AsyncMock(return_value=MagicMock(message_id=43))
    state = make_fsm_context()

    await bot.cmd_video(msg, state)

    msg.answer.assert_awaited_once()
    kwargs = msg.answer.await_args.kwargs
    assert kwargs.get("reply_markup") is not None
    assert "720p" in msg.answer.await_args.args[0]
    state.set_state.assert_awaited_with(config_flow.ConfigStates.configure)
    state.update_data.assert_any_await(config_model="grok_video")
    state.update_data.assert_any_await(config_message_id=ANY)


@pytest.mark.asyncio
async def test_cfg_video_duration_persists(sessions_file, mock_config_safe_edit):
    uid = 7002
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:video:duration:10"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    await bot.handle_cfg_video(callback, state)

    cfg = sessions.get_video_config(uid)
    assert cfg["duration"] == 10
    state.set_state.assert_awaited_with(config_flow.ConfigStates.configure)


@pytest.mark.asyncio
async def test_cfg_video_duration_invalid_rejected(sessions_file, mock_config_safe_edit):
    uid = 7012
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:video:duration:99"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    mock_set = MagicMock()
    with patch.dict(bot._CONFIG_DEPS, {"set_video_config": mock_set}):
        await bot.handle_cfg_video(callback, state)

    mock_set.assert_not_called()
    callback.answer.assert_awaited_once_with("Duración no disponible.", show_alert=True)


@pytest.mark.asyncio
async def test_cfg_video_guard_same_duration(sessions_file, mock_config_safe_edit):
    uid = 7003
    sessions.set_video_config(uid, duration=15)
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:video:duration:15"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    await bot.handle_cfg_video(callback, state)

    callback.answer.assert_awaited_once()
    assert "Ya está activa" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cfg_video_aspect_persists(sessions_file, mock_config_safe_edit):
    uid = 7004
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:video:aspect:9:16"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    await bot.handle_cfg_video(callback, state)

    cfg = sessions.get_video_config(uid)
    assert cfg["aspect_ratio"] == "9:16"


@pytest.mark.asyncio
async def test_cfg_video_guard_same_aspect(sessions_file, mock_config_safe_edit):
    uid = 7005
    sessions.set_video_config(uid, aspect_ratio="16:9")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:video:aspect:16:9"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    await bot.handle_cfg_video(callback, state)

    callback.answer.assert_awaited_once()
    assert "Ya está activa" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cfg_video_resolution_persists(sessions_file, mock_config_safe_edit):
    uid = 7006
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:video:resolution:480p"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    await bot.handle_cfg_video(callback, state)

    cfg = sessions.get_video_config(uid)
    assert cfg["resolution"] == "480p"


@pytest.mark.asyncio
async def test_cfg_video_model_persists(sessions_file, mock_config_safe_edit):
    uid = 9001
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:video:model:grok-imagine-video-1.5"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    await bot.handle_cfg_video(callback, state)

    cfg = sessions.get_video_config(uid)
    assert cfg["model"] == "grok-imagine-video-1.5"


@pytest.mark.asyncio
async def test_cfg_video_guard_same_model(sessions_file, mock_config_safe_edit):
    uid = 9002
    sessions.set_video_config(uid, model="grok-imagine-video")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:video:model:grok-imagine-video"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    await bot.handle_cfg_video(callback, state)

    callback.answer.assert_awaited_once()
    assert "activo" in callback.answer.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_cfg_video_guard_same_resolution(sessions_file, mock_config_safe_edit):
    uid = 7007
    sessions.set_video_config(uid, resolution="720p")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:video:resolution:720p"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    await bot.handle_cfg_video(callback, state)

    callback.answer.assert_awaited_once()
    assert "Ya está activa" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cfg_video_mode_persists_for_kie(sessions_file, mock_config_safe_edit):
    uid = 9010
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:video:mode:spicy"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    await bot.handle_cfg_video(callback, state)

    cfg = sessions.get_video_config(uid)
    assert cfg["mode"] == "spicy"
    assert "Spicy" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cfg_video_mode_guard_same_mode(sessions_file, mock_config_safe_edit):
    uid = 9011
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    sessions.set_video_config(uid, mode="normal")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:video:mode:normal"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    await bot.handle_cfg_video(callback, state)

    callback.answer.assert_awaited_once()
    assert "activo" in callback.answer.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_cfg_video_mode_rejected_for_non_kie(sessions_file, mock_config_safe_edit):
    uid = 9012
    sessions.set_grok_imagine_config(uid, "xai", "standard")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:video:mode:fun"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    mock_set = MagicMock()
    with patch.dict(bot._CONFIG_DEPS, {"set_video_config": mock_set}):
        await bot.handle_cfg_video(callback, state)

    mock_set.assert_not_called()
    callback.answer.assert_awaited_once_with(
        "Modo de video solo disponible con Kie.ai.",
        show_alert=True,
    )


@pytest.mark.asyncio
async def test_cfg_model_grok_video_navigates_to_provider(sessions_file, mock_config_safe_edit):
    uid = 5101
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:model:grok_video"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(fsm_state=_state_key(config_flow.ConfigStates.select_model))
    bot.get_user_state(uid)["model"] = "grok"

    await bot.handle_cfg_model(callback, state)

    text = mock_config_safe_edit.await_args.args[1]
    assert "proveedor" in text.lower()
    assert bot.get_user_state(uid)["model"] == "grok_video"
    state.set_state.assert_awaited_with(config_flow.ConfigStates.select_provider)
    state.update_data.assert_any_await(config_model="grok_video", config_message_id=ANY)


@pytest.mark.asyncio
async def test_cfg_model_seedream_skips_provider(sessions_file, mock_config_safe_edit):
    uid = 5102
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:model:seedream"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(fsm_state=_state_key(config_flow.ConfigStates.select_model))
    bot.get_user_state(uid)["model"] = "grok"

    await bot.handle_cfg_model(callback, state)

    text = mock_config_safe_edit.await_args.args[1]
    assert "proveedor" not in text.lower()
    assert "Seedream" in text
    state.set_state.assert_awaited_with(config_flow.ConfigStates.configure)


@pytest.mark.asyncio
async def test_cfg_model_faceswap_skips_provider(sessions_file, mock_config_safe_edit):
    uid = 5103
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:model:faceswap"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(fsm_state=_state_key(config_flow.ConfigStates.select_model))
    bot.get_user_state(uid)["model"] = "grok"

    await bot.handle_cfg_model(callback, state)

    text = mock_config_safe_edit.await_args.args[1]
    assert "proveedor" not in text.lower()
    assert "Face Swap" in text
    state.set_state.assert_awaited_with(config_flow.ConfigStates.configure)


@pytest.mark.asyncio
async def test_cfg_model_same_seedream_shows_configure(sessions_file, mock_config_safe_edit):
    uid = 5104
    bot.get_user_state(uid)["model"] = "seedream"
    sessions.set_model(uid, "seedream")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:model:seedream"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(fsm_state=_state_key(config_flow.ConfigStates.select_model))

    await bot.handle_cfg_model(callback, state)

    text = mock_config_safe_edit.await_args.args[1]
    assert "Seedream" in text
    callback.answer.assert_awaited_once_with("Ya estás usando ese modelo.")


@pytest.mark.asyncio
async def test_cfg_provider_persists_and_navigates(sessions_file, mock_config_safe_edit):
    uid = 7020
    sessions.set_grok_imagine_config(uid, "xai", "quality")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:provider:kie"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok",
        fsm_state=_state_key(config_flow.ConfigStates.select_provider),
    )

    await bot.handle_cfg_provider(callback, state)

    cfg = sessions.get_grok_imagine_config(uid)
    assert cfg["provider"] == "kie"
    text = mock_config_safe_edit.await_args.args[1]
    assert "nivel de calidad" in text.lower()
    state.set_state.assert_awaited_with(config_flow.ConfigStates.configure)


@pytest.mark.asyncio
async def test_cfg_provider_same_provider_advances_to_configure(sessions_file, mock_config_safe_edit):
    uid = 7025
    sessions.set_grok_imagine_config(uid, "kie", "quality")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:provider:kie"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok",
        fsm_state=_state_key(config_flow.ConfigStates.select_provider),
    )

    mock_set = MagicMock()
    with patch.dict(bot._CONFIG_DEPS, {"set_grok_imagine_config": mock_set}):
        await bot.handle_cfg_provider(callback, state)

    mock_set.assert_not_called()
    text = mock_config_safe_edit.await_args.args[1]
    assert "nivel de calidad" in text.lower()
    state.set_state.assert_awaited_with(config_flow.ConfigStates.configure)
    callback.answer.assert_awaited_once_with("Ya está activo ese proveedor.")


@pytest.mark.asyncio
async def test_cfg_provider_guard_same_provider_no_mutation(sessions_file, mock_config_safe_edit):
    uid = 7026
    sessions.set_grok_imagine_config(uid, "kie", "quality")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:provider:kie"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.select_provider),
    )

    mock_set = MagicMock()
    with patch.dict(bot._CONFIG_DEPS, {"set_grok_imagine_config": mock_set}):
        await bot.handle_cfg_provider(callback, state)

    mock_set.assert_not_called()
    text = mock_config_safe_edit.await_args.args[1]
    assert "video" in text.lower()


@pytest.mark.asyncio
async def test_cfg_provider_grok_activates_model(sessions_file, mock_config_safe_edit):
    uid = 7027
    bot.get_user_state(uid)["model"] = "seedream"
    sessions.set_model(uid, "seedream")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:provider:xai"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok",
        fsm_state=_state_key(config_flow.ConfigStates.select_provider),
    )

    await bot.handle_cfg_provider(callback, state)

    assert bot.get_user_state(uid)["model"] == "grok"
    assert sessions.get_session(uid)["model"] == "grok"


@pytest.mark.asyncio
async def test_cfg_variant_guard_before_mutation(sessions_file, mock_config_safe_edit):
    uid = 3003
    sessions.set_grok_imagine_config(uid, "xai", "quality")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:variant:quality"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    mock_set = MagicMock()
    with patch.dict(bot._CONFIG_DEPS, {"set_grok_imagine_config": mock_set}):
        await bot.handle_cfg_variant(callback, state)

    mock_set.assert_not_called()
    callback.answer.assert_awaited_once()
    assert "Ya está activa" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cfg_back_model_navigation(sessions_file, mock_config_safe_edit):
    uid = 8012
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:back:model"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    await bot.handle_cfg_back_model(callback, state)

    text = mock_config_safe_edit.await_args.args[1]
    assert "Selecciona el modelo" in text
    state.set_state.assert_awaited_with(config_flow.ConfigStates.select_model)
    state.update_data.assert_any_await(config_model=None, config_message_id=ANY)
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cfg_back_provider_returns_to_provider_screen(sessions_file, mock_config_safe_edit):
    uid = 8010
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:back:provider"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    await bot.handle_cfg_back_provider(callback, state)

    text = mock_config_safe_edit.await_args.args[1]
    assert "proveedor" in text.lower()
    state.set_state.assert_awaited_with(config_flow.ConfigStates.select_provider)
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cfg_close_clears_fsm(sessions_file, mock_config_safe_edit):
    callback = MagicMock()
    callback.from_user.id = 8011
    callback.data = "cfg:close"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(config_model="grok")

    await bot.handle_cfg_close(callback, state)

    state.clear.assert_awaited_once()
    mock_config_safe_edit.assert_awaited_once_with(
        callback.message,
        "Configuración cerrada.",
        reply_markup=None,
    )
    callback.answer.assert_awaited_once()


@pytest.mark.parametrize(
    "handler_name,callback_data,fsm_state,config_model,patch_key,expected_msg",
    [
        ("handle_cfg_model", "cfg:model:invalid", "select_model", None, "set_model", "Modelo no disponible."),
        ("handle_cfg_provider", "cfg:provider:evil", "select_provider", "grok", "set_grok_imagine_config", "Proveedor no disponible."),
        ("handle_cfg_variant", "cfg:variant:ultra", "configure", "grok", "set_grok_imagine_config", "Opción inválida."),
        ("handle_cfg_video", "cfg:video:model:evil-model", "configure", "grok_video", "set_video_config", "Modelo no disponible."),
    ],
)
@pytest.mark.asyncio
async def test_cfg_invalid_callbacks_rejected(
    sessions_file,
    mock_config_safe_edit,
    handler_name,
    callback_data,
    fsm_state,
    config_model,
    patch_key,
    expected_msg,
):
    callback = MagicMock()
    callback.from_user.id = 8100
    callback.data = callback_data
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state_kwargs = {}
    if config_model is not None:
        state_kwargs["config_model"] = config_model
    state = make_fsm_context(
        fsm_state=_state_key(getattr(config_flow.ConfigStates, fsm_state)),
        **state_kwargs,
    )

    handler = getattr(bot, handler_name)
    mock_set = MagicMock()
    with patch.dict(bot._CONFIG_DEPS, {patch_key: mock_set}):
        await handler(callback, state)

    mock_set.assert_not_called()
    callback.answer.assert_awaited_once_with(expected_msg, show_alert=True)


@pytest.mark.asyncio
async def test_cfg_stale_fsm_rejected(sessions_file, mock_config_safe_edit):
    callback = MagicMock()
    callback.from_user.id = 8101
    callback.data = "cfg:provider:kie"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok",
        fsm_state=_state_key(config_flow.ConfigStates.select_model),
    )

    await bot.handle_cfg_provider(callback, state)

    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True


def _find_imaginess_handler():
    for handler in bot.dp.message.handlers:
        if getattr(handler.callback, "__name__", None) == "cmd_imaginess":
            return handler
    raise AssertionError("cmd_imaginess handler not registered")


@pytest.mark.parametrize("command", ["imagine", "imaginess"])
def test_cmd_imagine_aliases_registered(command):
    handler = _find_imaginess_handler()
    command_filters = [
        f.callback for f in handler.filters if hasattr(f.callback, "commands")
    ]
    assert len(command_filters) == 1
    assert set(command_filters[0].commands) == {"imagine", "imaginess"}


@pytest.mark.asyncio
async def test_cmd_imaginess_activates_grok(sessions_file):
    uid = 7011
    bot.get_user_state(uid)["model"] = "seedream"
    sessions.set_model(uid, "seedream")
    msg = MagicMock()
    msg.from_user.id = uid
    msg.answer = AsyncMock(return_value=MagicMock(message_id=42))
    state = make_fsm_context()

    await bot.cmd_imaginess(msg, state)

    assert bot.get_user_state(uid)["model"] == "grok"
    assert sessions.get_session(uid)["model"] == "grok"
    state.update_data.assert_any_await(config_message_id=42)


@pytest.mark.asyncio
async def test_cmd_video_activates_grok_video(sessions_file):
    uid = 7012
    bot.get_user_state(uid)["model"] = "grok"
    sessions.set_model(uid, "grok")
    msg = MagicMock()
    msg.from_user.id = uid
    msg.answer = AsyncMock(return_value=MagicMock(message_id=43))
    state = make_fsm_context()

    await bot.cmd_video(msg, state)

    assert bot.get_user_state(uid)["model"] == "grok_video"
    assert sessions.get_session(uid)["model"] == "grok_video"
    state.update_data.assert_any_await(config_message_id=43)


@pytest.mark.asyncio
async def test_cfg_model_same_grok_navigates_without_set_model(sessions_file, mock_config_safe_edit):
    uid = 5105
    bot.get_user_state(uid)["model"] = "grok"
    sessions.set_model(uid, "grok")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:model:grok"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(fsm_state=_state_key(config_flow.ConfigStates.select_model))

    mock_set = MagicMock()
    with patch.dict(bot._CONFIG_DEPS, {"set_model": mock_set}):
        await bot.handle_cfg_model(callback, state)

    mock_set.assert_not_called()
    text = mock_config_safe_edit.await_args.args[1]
    assert "proveedor" in text.lower()
    state.set_state.assert_awaited_with(config_flow.ConfigStates.select_provider)


@pytest.mark.asyncio
async def test_cfg_model_same_grok_video_navigates_without_set_model(sessions_file, mock_config_safe_edit):
    uid = 5106
    bot.get_user_state(uid)["model"] = "grok_video"
    sessions.set_model(uid, "grok_video")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:model:grok_video"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(fsm_state=_state_key(config_flow.ConfigStates.select_model))

    mock_set = MagicMock()
    with patch.dict(bot._CONFIG_DEPS, {"set_model": mock_set}):
        await bot.handle_cfg_model(callback, state)

    mock_set.assert_not_called()
    text = mock_config_safe_edit.await_args.args[1]
    assert "proveedor" in text.lower()
    state.set_state.assert_awaited_with(config_flow.ConfigStates.select_provider)


@pytest.mark.asyncio
async def test_cfg_stale_config_model_rejected(sessions_file, mock_config_safe_edit):
    callback = MagicMock()
    callback.from_user.id = 8102
    callback.data = "cfg:variant:quality"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    mock_set = MagicMock()
    with patch.dict(bot._CONFIG_DEPS, {"set_grok_imagine_config": mock_set}):
        await bot.handle_cfg_variant(callback, state)

    mock_set.assert_not_called()
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True
    assert "desactualizada" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cfg_back_provider_fallback_from_user_state(sessions_file, mock_config_safe_edit):
    uid = 8013
    bot.get_user_state(uid)["model"] = "grok_video"
    sessions.set_model(uid, "grok_video")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:back:provider"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    await bot.handle_cfg_back_provider(callback, state)

    text = mock_config_safe_edit.await_args.args[1]
    assert "proveedor" in text.lower()
    assert "Grok Imagine Video" in text
    state.set_state.assert_awaited_with(config_flow.ConfigStates.select_provider)
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cfg_back_provider_rejects_invalid_model(sessions_file, mock_config_safe_edit):
    uid = 8014
    bot.get_user_state(uid)["model"] = "seedream"
    sessions.set_model(uid, "seedream")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:back:provider"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="seedream",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )

    await bot.handle_cfg_back_provider(callback, state)

    mock_config_safe_edit.assert_not_awaited()
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True
    assert "desactualizada" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cfg_provider_rejects_missing_config_model(sessions_file, mock_config_safe_edit):
    uid = 8103
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:provider:kie"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        fsm_state=_state_key(config_flow.ConfigStates.select_provider),
    )

    mock_set = MagicMock()
    with patch.dict(bot._CONFIG_DEPS, {"set_grok_imagine_config": mock_set}):
        await bot.handle_cfg_provider(callback, state)

    mock_set.assert_not_called()
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_cfg_close_ignores_stale_message_id(sessions_file, mock_config_safe_edit):
    callback = MagicMock()
    callback.from_user.id = 8015
    callback.data = "cfg:close"
    callback.message = MagicMock()
    callback.message.message_id = 99
    callback.answer = AsyncMock()
    state = make_fsm_context(config_model="grok", config_message_id=42)

    await bot.handle_cfg_close(callback, state)

    state.clear.assert_not_awaited()
    mock_config_safe_edit.assert_not_awaited()
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.parametrize(
    "callback_data,expected_msg",
    [
        ("cfg:video:bad", "Opción inválida."),
        ("cfg:video:duration:abc", "Duración inválida."),
        ("cfg:video:resolution:4k", "Resolución no disponible."),
        ("cfg:video:mode:evil", "Modo no disponible."),
        ("cfg:video:field:evil", "Opción inválida."),
    ],
)
@pytest.mark.asyncio
async def test_cfg_video_error_branches_no_mutation(
    sessions_file,
    mock_config_safe_edit,
    callback_data,
    expected_msg,
):
    uid = 8110
    sessions.set_grok_imagine_config(uid, "kie", "standard")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = callback_data
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.configure),
    )
    before = sessions.get_video_config(uid)

    mock_set = MagicMock()
    with patch.dict(bot._CONFIG_DEPS, {"set_video_config": mock_set}):
        await bot.handle_cfg_video(callback, state)

    mock_set.assert_not_called()
    assert sessions.get_video_config(uid) == before
    callback.answer.assert_awaited_once_with(expected_msg, show_alert=True)


@pytest.mark.asyncio
async def test_cfg_provider_grok_video_activates_model(sessions_file, mock_config_safe_edit):
    uid = 7028
    bot.get_user_state(uid)["model"] = "seedream"
    sessions.set_model(uid, "seedream")
    callback = MagicMock()
    callback.from_user.id = uid
    callback.data = "cfg:provider:xai"
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok_video",
        fsm_state=_state_key(config_flow.ConfigStates.select_provider),
    )

    await bot.handle_cfg_provider(callback, state)

    assert bot.get_user_state(uid)["model"] == "grok_video"
    assert sessions.get_session(uid)["model"] == "grok_video"


@pytest.mark.asyncio
async def test_cfg_rejects_non_private_group(sessions_file):
    msg = MagicMock()
    msg.from_user.id = 8200
    msg.chat.type = "group"
    msg.answer = AsyncMock()
    state = make_fsm_context()

    await bot.cmd_config(msg, state)

    msg.answer.assert_awaited_once()
    assert "privados" in msg.answer.await_args.args[0].lower()
    state.set_state.assert_not_awaited()


@pytest.mark.parametrize(
    "handler_name,callback_data",
    [
        ("handle_cfg_close", "cfg:close"),
        ("handle_cfg_model", "cfg:model:grok"),
    ],
)
@pytest.mark.asyncio
async def test_cfg_rejects_non_private_callback(
    sessions_file,
    mock_config_safe_edit,
    handler_name,
    callback_data,
):
    callback = MagicMock()
    callback.from_user.id = 8201
    callback.data = callback_data
    callback.message = MagicMock()
    callback.message.chat.type = "group"
    callback.answer = AsyncMock()
    state = make_fsm_context(
        config_model="grok",
        fsm_state=_state_key(config_flow.ConfigStates.select_model),
    )

    handler = getattr(bot, handler_name)
    await handler(callback, state)

    callback.answer.assert_awaited_once_with(
        "La configuración solo está disponible en chats privados.",
        show_alert=True,
    )
    mock_config_safe_edit.assert_not_awaited()
    state.clear.assert_not_awaited()