"""Unified /config FSM flow for model, provider, and model-specific settings."""

from __future__ import annotations

from typing import Any

from aiogram import Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import sessions


class ConfigStates(StatesGroup):
    select_model = State()
    select_provider = State()
    configure = State()


_NON_PRIVATE_CHAT_TYPES = frozenset({"group", "supergroup", "channel"})


def _chat_is_private(chat: types.Chat) -> bool:
    chat_type = getattr(chat, "type", None)
    if chat_type is None:
        return True
    if hasattr(chat_type, "value"):
        chat_type = chat_type.value
    return str(chat_type) not in _NON_PRIVATE_CHAT_TYPES


async def _reject_non_private_message(message: types.Message) -> bool:
    """Return True when the command was rejected because chat is not private."""
    if _chat_is_private(message.chat):
        return False
    await message.answer("La configuración solo está disponible en chats privados.")
    return True


async def _reject_non_private_callback(callback: types.CallbackQuery) -> bool:
    """Return True when the callback was rejected because chat is not private."""
    if _chat_is_private(callback.message.chat):
        return False
    await callback.answer(
        "La configuración solo está disponible en chats privados.",
        show_alert=True,
    )
    return True


def _kie_footer(deps: dict[str, Any], cfg: dict) -> str:
    kie_configured = deps["kie_configured"]
    _KIE_NOT_CONFIGURED_MSG = deps["_KIE_NOT_CONFIGURED_MSG"]
    _KIE_QUALITY_NOTE = deps["_KIE_QUALITY_NOTE"]
    _KIE_PRIVACY_NOTICE = deps["_KIE_PRIVACY_NOTICE"]
    text = ""
    if cfg["provider"] == "kie" and not kie_configured:
        text += f"\n\n⚠️ {_KIE_NOT_CONFIGURED_MSG}"
    if cfg["provider"] == "kie":
        text += f"\n\n<i>{_KIE_QUALITY_NOTE}</i>\n<i>{_KIE_PRIVACY_NOTICE}</i>"
    return text


def config_model_keyboard(deps: dict[str, Any], user_id: int) -> InlineKeyboardMarkup:
    get_user_state = deps["get_user_state"]
    get_grok_imagine_config = deps["get_grok_imagine_config"]
    MODELS = deps["MODELS"]
    state = get_user_state(user_id)
    current_key = state["model"]
    buttons = []
    for key, m in MODELS.items():
        if key == "grok":
            cfg = get_grok_imagine_config(user_id)
            suffix = f"{cfg['prov_label']} • {cfg['label']}"
            label = f"{'✅ ' if key == current_key else ''}Grok Imagine ({suffix})"
        else:
            label = f"{'✅ ' if key == current_key else ''}{m['name']}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"cfg:model:{key}")])
    buttons.append([InlineKeyboardButton(text="Cerrar", callback_data="cfg:close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def config_provider_keyboard(deps: dict[str, Any], user_id: int) -> InlineKeyboardMarkup:
    get_grok_imagine_config = deps["get_grok_imagine_config"]
    _prov_label = deps["_prov_label"]
    cfg = get_grok_imagine_config(user_id)
    current = cfg["provider"]
    buttons = []
    for prov in ("kie", "xai", "replicate"):
        prefix = "✅ " if prov == current else ""
        buttons.append([
            InlineKeyboardButton(
                text=f"{prefix}{_prov_label(prov)}",
                callback_data=f"cfg:provider:{prov}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="← Modelo", callback_data="cfg:back:model")])
    buttons.append([InlineKeyboardButton(text="Cerrar", callback_data="cfg:close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def config_variant_keyboard(deps: dict[str, Any], user_id: int) -> InlineKeyboardMarkup:
    get_grok_imagine_config = deps["get_grok_imagine_config"]
    GROK_IMAGINE_VARIANTS = deps["GROK_IMAGINE_VARIANTS"]
    cfg = get_grok_imagine_config(user_id)
    buttons = []
    for var, spec in GROK_IMAGINE_VARIANTS.items():
        prefix = "✅ " if var == cfg["variant"] else ""
        buttons.append([
            InlineKeyboardButton(
                text=f"{prefix}{spec['label']}",
                callback_data=f"cfg:variant:{var}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="← Proveedor", callback_data="cfg:back:provider")])
    buttons.append([InlineKeyboardButton(text="Cerrar", callback_data="cfg:close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def config_video_keyboard(deps: dict[str, Any], current: dict, user_id: int) -> InlineKeyboardMarkup:
    get_video_provider_for_user = deps["get_video_provider_for_user"]
    _kie_aspect_ratios_for_model = deps["_kie_aspect_ratios_for_model"]
    VIDEO_MODEL_LABELS = deps["VIDEO_MODEL_LABELS"]
    VIDEO_MODE_LABELS = deps["VIDEO_MODE_LABELS"]

    def model_btn(model_id: str) -> InlineKeyboardButton:
        prefix = "✅ " if model_id == current["model"] else ""
        label = VIDEO_MODEL_LABELS.get(model_id, model_id)
        return InlineKeyboardButton(
            text=f"{prefix}{label}",
            callback_data=f"cfg:video:model:{model_id}",
        )

    def dur_btn(value: int) -> InlineKeyboardButton:
        prefix = "✅ " if value == current["duration"] else ""
        return InlineKeyboardButton(
            text=f"{prefix}{value}s",
            callback_data=f"cfg:video:duration:{value}",
        )

    def aspect_btn(value: str) -> InlineKeyboardButton:
        prefix = "✅ " if value == current["aspect_ratio"] else ""
        return InlineKeyboardButton(
            text=f"{prefix}{value}",
            callback_data=f"cfg:video:aspect:{value}",
        )

    def res_btn(value: str) -> InlineKeyboardButton:
        prefix = "✅ " if value == current["resolution"] else ""
        return InlineKeyboardButton(
            text=f"{prefix}{value}",
            callback_data=f"cfg:video:resolution:{value}",
        )

    def mode_btn(value: str) -> InlineKeyboardButton:
        prefix = "✅ " if value == current["mode"] else ""
        label = VIDEO_MODE_LABELS.get(value, value)
        return InlineKeyboardButton(
            text=f"{prefix}{label}",
            callback_data=f"cfg:video:mode:{value}",
        )

    prov = get_video_provider_for_user(user_id)
    aspects = list(
        _kie_aspect_ratios_for_model(current["model"])
        if prov == "kie"
        else sessions.VALID_VIDEO_ASPECT_RATIOS
    )
    aspect_rows = [aspects[i : i + 4] for i in range(0, len(aspects), 4)]

    buttons = [
        [model_btn("grok-imagine-video"), model_btn("grok-imagine-video-1.5")],
        [dur_btn(v) for v in sessions.VALID_VIDEO_DURATIONS],
    ]
    for row in aspect_rows:
        buttons.append([aspect_btn(v) for v in row])
    buttons.append([res_btn("480p"), res_btn("720p")])
    if prov == "kie":
        buttons.append([mode_btn(v) for v in sessions.VALID_VIDEO_MODES])
    buttons.append([InlineKeyboardButton(text="← Proveedor", callback_data="cfg:back:provider")])
    buttons.append([InlineKeyboardButton(text="Cerrar", callback_data="cfg:close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _provider_screen_text(deps: dict[str, Any], user_id: int, model_key: str) -> str:
    get_grok_imagine_config = deps["get_grok_imagine_config"]
    cfg = get_grok_imagine_config(user_id)
    model_label = "Grok Imagine" if model_key == "grok" else "Grok Imagine Video"
    text = (
        f"Configuración de <b>{model_label}</b> — proveedor.\n\n"
        f"Actual: <b>{cfg['prov_label']}</b>\n\n"
        "Elige el proveedor de API. El cambio se guarda inmediatamente.\n\n"
        "<i>Si el proveedor activo ya está seleccionado, tócalo de nuevo para continuar.</i>"
    )
    text += _kie_footer(deps, cfg)
    return text


def _variant_screen_text(deps: dict[str, Any], user_id: int, *, updated: bool = False) -> str:
    get_grok_imagine_config = deps["get_grok_imagine_config"]
    cfg = get_grok_imagine_config(user_id)
    prefix = "✅ Configuración de Grok Imagine actualizada y guardada.\n\n" if updated else ""
    text = (
        f"{prefix}"
        "Configuración de <b>Grok Imagine</b> — nivel de calidad.\n\n"
        f"Actual: <b>{cfg['prov_label']} • {cfg['label']}</b>\n"
        f"<i>{cfg['desc']}</i>\n\n"
        "Elige nivel de calidad. El cambio se guarda inmediatamente."
    )
    text += _kie_footer(deps, cfg)
    return text


def _video_screen_text(
    deps: dict[str, Any],
    user_id: int,
    *,
    updated: bool = False,
    aspect_reset_msg: str | None = None,
) -> str:
    get_video_provider_for_user = deps["get_video_provider_for_user"]
    _video_config_summary = deps["_video_config_summary"]
    prov = get_video_provider_for_user(user_id)
    summary = _video_config_summary(user_id)
    prefix = "✅ Configuración de video actualizada y guardada.\n\n" if updated else ""
    text = (
        f"{prefix}"
        "Configuración de <b>Grok Imagine Video</b> (persistente).\n\n"
        f"Actual: {summary}\n\n"
        "Elige modelo, duración, relación de aspecto y resolución. El cambio se guarda inmediatamente."
    )
    if prov == "kie":
        text += (
            "\n\n<i>Kie.ai: duración mínima 6s (3/5s se ajustan a 6s). "
            "Modelo 1.5 solo soporta imagen a video en Kie.ai. "
            "Modo Spicy solo funciona al animar imágenes generadas por el bot (reply a imagen del bot).</i>"
        )
    if aspect_reset_msg:
        text += f"\n\n<i>{aspect_reset_msg}</i>"
    return text


def _simple_model_text(deps: dict[str, Any], model_key: str) -> str:
    MODELS = deps["MODELS"]
    model = MODELS[model_key]
    lines = [
        f"Modelo cambiado a <b>{model['name']}</b>.\n",
        f"<i>{model['desc']}</i>\n",
    ]
    if model_key == "faceswap":
        lines.append("Usa /cambiar_source para configurar tu cara fuente.\n")
        lines.append("Luego Envía fotos (incluso albumes) para hacer face swap.")
    elif model_key == "seedream":
        lines.append("Enviame un prompt para generar una imagen.")
        lines.append("O Envía una foto con caption para editarla.")
    return "\n".join(lines)


def _simple_close_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="← Modelo", callback_data="cfg:back:model")],
            [InlineKeyboardButton(text="Cerrar", callback_data="cfg:close")],
        ]
    )


def _state_key(st: State) -> str:
    """Return the FSM state id as stored by aiogram (e.g. ConfigStates:select_model)."""
    return st.state


async def _reject_stale_callback(
    callback: types.CallbackQuery,
    state: FSMContext,
    *,
    allowed_states: tuple[State, ...],
    required_config_models: tuple[str, ...] | None = None,
) -> bool:
    """Return True when the callback is stale and was rejected."""
    data = await state.get_data()
    stored_id = data.get("config_message_id")
    if stored_id is not None and callback.message.message_id != stored_id:
        await callback.answer(
            "Esta pantalla ya no está activa. Usa /config para empezar de nuevo.",
            show_alert=True,
        )
        return True
    current = await state.get_state()
    allowed = {_state_key(s) for s in allowed_states}
    if current not in allowed:
        await callback.answer(
            "Esta pantalla ya no está activa. Usa /config para empezar de nuevo.",
            show_alert=True,
        )
        return True
    if required_config_models is not None:
        if data.get("config_model") not in required_config_models:
            await callback.answer(
                "Sesión de configuración desactualizada. Usa /config para empezar de nuevo.",
                show_alert=True,
            )
            return True
    return False


async def _show_model_screen(
    target: types.Message,
    state: FSMContext,
    deps: dict[str, Any],
    user_id: int,
) -> None:
    safe_edit_text = deps["safe_edit_text"]
    await state.set_state(ConfigStates.select_model)
    await state.update_data(config_model=None, config_message_id=target.message_id)
    await safe_edit_text(
        target,
        "Selecciona el modelo:",
        reply_markup=config_model_keyboard(deps, user_id),
    )


async def _show_provider_screen(
    target: types.Message,
    state: FSMContext,
    deps: dict[str, Any],
    user_id: int,
    model_key: str,
) -> None:
    safe_edit_text = deps["safe_edit_text"]
    await state.set_state(ConfigStates.select_provider)
    await state.update_data(config_model=model_key, config_message_id=target.message_id)
    await safe_edit_text(
        target,
        _provider_screen_text(deps, user_id, model_key),
        parse_mode="HTML",
        reply_markup=config_provider_keyboard(deps, user_id),
    )


async def _show_configure_screen(
    target: types.Message,
    state: FSMContext,
    deps: dict[str, Any],
    user_id: int,
    model_key: str,
    *,
    updated: bool = False,
    aspect_reset_msg: str | None = None,
) -> None:
    safe_edit_text = deps["safe_edit_text"]
    await state.set_state(ConfigStates.configure)
    await state.update_data(config_model=model_key, config_message_id=target.message_id)

    if model_key == "grok":
        await safe_edit_text(
            target,
            _variant_screen_text(deps, user_id, updated=updated),
            parse_mode="HTML",
            reply_markup=config_variant_keyboard(deps, user_id),
        )
        return

    if model_key == "grok_video":
        get_video_config = deps["get_video_config"]
        cfg = get_video_config(user_id)
        await safe_edit_text(
            target,
            _video_screen_text(
                deps,
                user_id,
                updated=updated,
                aspect_reset_msg=aspect_reset_msg,
            ),
            parse_mode="HTML",
            reply_markup=config_video_keyboard(deps, cfg, user_id),
        )
        return

    await safe_edit_text(
        target,
        _simple_model_text(deps, model_key),
        parse_mode="HTML",
        reply_markup=_simple_close_keyboard(),
    )


_CONFIG_DEPS: dict[str, Any] | None = None


def _deps() -> dict[str, Any]:
    if _CONFIG_DEPS is None:
        raise RuntimeError("register_config_handlers() must be called first")
    return _CONFIG_DEPS


def _activate_model(deps: dict[str, Any], user_id: int, model_key: str) -> None:
    state = deps["get_user_state"](user_id)
    state["pending_prompt"] = None
    clear_pending_faceswap = deps.get("clear_pending_faceswap")
    if clear_pending_faceswap:
        clear_pending_faceswap(state)
    state["awaiting_long_prompt_text"] = False
    state["pending_edit_file_ids"] = None
    state["pending_edit_integrate_mode"] = False
    state["pending_edit_is_video"] = False
    state["model"] = model_key
    deps["set_model"](user_id, model_key)


async def cmd_config(message: types.Message, state: FSMContext):
    if await _reject_non_private_message(message):
        return
    deps = _deps()
    await state.set_state(ConfigStates.select_model)
    await state.update_data(config_model=None)
    sent = await message.answer(
        "Selecciona el modelo:",
        reply_markup=config_model_keyboard(deps, message.from_user.id),
    )
    await state.update_data(config_message_id=sent.message_id)


async def cmd_model(message: types.Message, state: FSMContext):
    await cmd_config(message, state)


def _grok_imagine_provider_is_set(deps: dict[str, Any], user_id: int) -> bool:
    """Return True when the user already has a persisted Grok Imagine provider."""
    get_grok_imagine_config = deps["get_grok_imagine_config"]
    cfg = get_grok_imagine_config(user_id)
    return cfg["provider"] in ("xai", "replicate", "kie")


async def cmd_imaginess(message: types.Message, state: FSMContext):
    if await _reject_non_private_message(message):
        return
    deps = _deps()
    uid = message.from_user.id
    _activate_model(deps, uid, "grok")
    await state.update_data(config_model="grok")
    if _grok_imagine_provider_is_set(deps, uid):
        await state.set_state(ConfigStates.configure)
        text = _variant_screen_text(deps, uid)
        sent = await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=config_variant_keyboard(deps, uid),
        )
    else:
        await state.set_state(ConfigStates.select_provider)
        text = _provider_screen_text(deps, uid, "grok")
        sent = await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=config_provider_keyboard(deps, uid),
        )
    await state.update_data(config_message_id=sent.message_id)


async def cmd_video(message: types.Message, state: FSMContext):
    if await _reject_non_private_message(message):
        return
    deps = _deps()
    uid = message.from_user.id
    _activate_model(deps, uid, "grok_video")
    get_video_config = deps["get_video_config"]
    cfg = get_video_config(uid)
    await state.set_state(ConfigStates.configure)
    await state.update_data(config_model="grok_video")
    sent = await message.answer(
        _video_screen_text(deps, uid),
        parse_mode="HTML",
        reply_markup=config_video_keyboard(deps, cfg, uid),
    )
    await state.update_data(config_message_id=sent.message_id)


async def handle_cfg_model(callback: types.CallbackQuery, state: FSMContext):
    if await _reject_non_private_callback(callback):
        return
    deps = _deps()
    models = deps["MODELS"]
    get_user_state = deps["get_user_state"]

    if await _reject_stale_callback(
        callback,
        state,
        allowed_states=(ConfigStates.select_model,),
    ):
        return

    model_key = callback.data.split(":", 2)[2]
    if model_key not in models:
        await callback.answer("Modelo no disponible.", show_alert=True)
        return

    uid = callback.from_user.id
    user_state = get_user_state(uid)
    same_model = model_key == user_state.get("model")

    if same_model and model_key not in ("grok", "grok_video"):
        await _show_configure_screen(callback.message, state, deps, uid, model_key)
        await callback.answer("Ya estás usando ese modelo.")
        return

    if not same_model:
        _activate_model(deps, uid, model_key)

    if model_key in ("grok", "grok_video"):
        await _show_provider_screen(callback.message, state, deps, uid, model_key)
        await callback.answer(f"Modelo: {models[model_key]['name']}")
        return

    await _show_configure_screen(callback.message, state, deps, uid, model_key)
    await callback.answer(f"Modelo: {models[model_key]['name']}")


async def handle_cfg_provider(callback: types.CallbackQuery, state: FSMContext):
    if await _reject_non_private_callback(callback):
        return
    deps = _deps()
    get_user_state = deps["get_user_state"]
    get_grok_imagine_config = deps["get_grok_imagine_config"]
    set_grok_imagine_config = deps["set_grok_imagine_config"]
    _maybe_reset_kie_aspect_ratio = deps["_maybe_reset_kie_aspect_ratio"]

    if await _reject_stale_callback(
        callback,
        state,
        allowed_states=(ConfigStates.select_provider,),
        required_config_models=("grok", "grok_video"),
    ):
        return

    prov = callback.data.split(":", 2)[2]
    if prov not in ("xai", "replicate", "kie"):
        await callback.answer("Proveedor no disponible.", show_alert=True)
        return

    uid = callback.from_user.id
    data = await state.get_data()
    model_key = data["config_model"]

    prior = get_grok_imagine_config(uid)
    if prov == prior["provider"]:
        await _show_configure_screen(
            callback.message,
            state,
            deps,
            uid,
            model_key,
            updated=False,
        )
        await callback.answer("Ya está activo ese proveedor.")
        return

    user_state = get_user_state(uid)
    user_state["grok_imagine_provider"] = prov
    set_grok_imagine_config(uid, prov, prior["variant"])
    if model_key == "grok":
        _activate_model(deps, uid, "grok")
    elif model_key == "grok_video":
        _activate_model(deps, uid, "grok_video")

    aspect_reset_msg = None
    reset_aspect = _maybe_reset_kie_aspect_ratio(uid)
    if reset_aspect:
        aspect_reset_msg = f"Relación de aspecto de video ajustada a {reset_aspect} (compatible con Kie.ai)."

    await _show_configure_screen(
        callback.message,
        state,
        deps,
        uid,
        model_key,
        aspect_reset_msg=aspect_reset_msg,
    )
    cfg = get_grok_imagine_config(uid)
    await callback.answer(f"Proveedor: {cfg['prov_label']}")


async def handle_cfg_variant(callback: types.CallbackQuery, state: FSMContext):
    if await _reject_non_private_callback(callback):
        return
    deps = _deps()
    get_user_state = deps["get_user_state"]
    get_grok_imagine_config = deps["get_grok_imagine_config"]
    set_grok_imagine_config = deps["set_grok_imagine_config"]
    grok_imagine_variants = deps["GROK_IMAGINE_VARIANTS"]

    if await _reject_stale_callback(
        callback,
        state,
        allowed_states=(ConfigStates.configure,),
        required_config_models=("grok",),
    ):
        return

    var = callback.data.split(":", 2)[2]
    if var not in grok_imagine_variants:
        await callback.answer("Opción inválida.", show_alert=True)
        return

    uid = callback.from_user.id
    prior = get_grok_imagine_config(uid)
    if var == prior["variant"]:
        await callback.answer("Ya está activa esa configuración.")
        return

    user_state = get_user_state(uid)
    user_state["grok_imagine_variant"] = var
    set_grok_imagine_config(uid, prior["provider"], var)
    _activate_model(deps, uid, "grok")

    await _show_configure_screen(
        callback.message,
        state,
        deps,
        uid,
        "grok",
        updated=True,
    )
    cfg = get_grok_imagine_config(uid)
    await callback.answer(f"Grok Imagine: {cfg['prov_label']} • {cfg['label']}")


async def handle_cfg_video(callback: types.CallbackQuery, state: FSMContext):
    if await _reject_non_private_callback(callback):
        return
    deps = _deps()
    get_video_provider_for_user = deps["get_video_provider_for_user"]
    get_video_config = deps["get_video_config"]
    set_video_config = deps["set_video_config"]
    _maybe_reset_kie_aspect_ratio = deps["_maybe_reset_kie_aspect_ratio"]
    _kie_aspect_ratios_for_model = deps["_kie_aspect_ratios_for_model"]
    _video_duration_display = deps["_video_duration_display"]
    video_model_labels = deps["VIDEO_MODEL_LABELS"]
    video_mode_labels = deps["VIDEO_MODE_LABELS"]

    if await _reject_stale_callback(
        callback,
        state,
        allowed_states=(ConfigStates.configure,),
        required_config_models=("grok_video",),
    ):
        return

    parts = callback.data.split(":")
    if len(parts) < 4 or parts[0] != "cfg" or parts[1] != "video":
        await callback.answer("Opción inválida.", show_alert=True)
        return
    field = parts[2]
    value = ":".join(parts[3:])

    uid = callback.from_user.id
    prior = get_video_config(uid)
    aspect_reset_msg: str | None = None

    if field == "model":
        if value not in sessions.VALID_VIDEO_MODELS:
            await callback.answer("Modelo no disponible.", show_alert=True)
            return
        if value == prior["model"]:
            await callback.answer("Ya está activo ese modelo.")
            return
        set_video_config(uid, model=value)
        reset_aspect = _maybe_reset_kie_aspect_ratio(uid, video_model=value)
        if reset_aspect:
            aspect_reset_msg = f"Relación de aspecto ajustada a {reset_aspect} (compatible con Kie.ai)."
    elif field == "duration":
        try:
            duration = int(value)
        except ValueError:
            await callback.answer("Duración inválida.", show_alert=True)
            return
        if duration not in sessions.VALID_VIDEO_DURATIONS:
            await callback.answer("Duración no disponible.", show_alert=True)
            return
        if duration == prior["duration"]:
            await callback.answer("Ya está activa esa duración.")
            return
        set_video_config(uid, duration=duration)
    elif field == "aspect":
        prov = get_video_provider_for_user(uid)
        allowed_aspects = (
            _kie_aspect_ratios_for_model(prior["model"])
            if prov == "kie"
            else sessions.VALID_VIDEO_ASPECT_RATIOS
        )
        if value not in allowed_aspects:
            await callback.answer("Relación de aspecto no disponible.", show_alert=True)
            return
        if value == prior["aspect_ratio"]:
            await callback.answer("Ya está activa esa relación de aspecto.")
            return
        set_video_config(uid, aspect_ratio=value)
    elif field == "resolution":
        if value not in sessions.VALID_VIDEO_RESOLUTIONS:
            await callback.answer("Resolución no disponible.", show_alert=True)
            return
        if value == prior["resolution"]:
            await callback.answer("Ya está activa esa resolución.")
            return
        set_video_config(uid, resolution=value)
    elif field == "mode":
        if get_video_provider_for_user(uid) != "kie":
            await callback.answer("Modo de video solo disponible con Kie.ai.", show_alert=True)
            return
        if value not in sessions.VALID_VIDEO_MODES:
            await callback.answer("Modo no disponible.", show_alert=True)
            return
        if value == prior["mode"]:
            await callback.answer("Ya está activo ese modo.")
            return
        set_video_config(uid, mode=value)
    else:
        await callback.answer("Opción inválida.", show_alert=True)
        return

    cfg = get_video_config(uid)
    prov = get_video_provider_for_user(uid)
    await _show_configure_screen(
        callback.message,
        state,
        deps,
        uid,
        "grok_video",
        updated=True,
        aspect_reset_msg=aspect_reset_msg,
    )
    dur_label = _video_duration_display(cfg["duration"], prov)
    model_label = video_model_labels.get(cfg["model"], cfg["model"])
    answer = (
        f"Video: {model_label} • {dur_label} • {cfg['aspect_ratio']} • {cfg['resolution']}"
    )
    if prov == "kie":
        mode_label = video_mode_labels.get(cfg["mode"], cfg["mode"])
        answer += f" • {mode_label}"
    await callback.answer(answer)


async def handle_cfg_back_model(callback: types.CallbackQuery, state: FSMContext):
    if await _reject_non_private_callback(callback):
        return
    deps = _deps()
    if await _reject_stale_callback(
        callback,
        state,
        allowed_states=(ConfigStates.select_provider, ConfigStates.configure),
    ):
        return
    await _show_model_screen(callback.message, state, deps, callback.from_user.id)
    await callback.answer()


async def handle_cfg_back_provider(callback: types.CallbackQuery, state: FSMContext):
    if await _reject_non_private_callback(callback):
        return
    deps = _deps()
    get_user_state = deps["get_user_state"]
    if await _reject_stale_callback(
        callback,
        state,
        allowed_states=(ConfigStates.configure,),
    ):
        return
    data = await state.get_data()
    uid = callback.from_user.id
    model_key = data.get("config_model")
    if model_key not in ("grok", "grok_video"):
        model_key = get_user_state(uid)["model"]
    if model_key not in ("grok", "grok_video"):
        await callback.answer(
            "Sesión de configuración desactualizada. Usa /config para empezar de nuevo.",
            show_alert=True,
        )
        return
    await _show_provider_screen(
        callback.message,
        state,
        deps,
        callback.from_user.id,
        model_key,
    )
    await callback.answer()


async def handle_cfg_close(callback: types.CallbackQuery, state: FSMContext):
    if await _reject_non_private_callback(callback):
        return
    if await _reject_stale_callback(
        callback,
        state,
        allowed_states=(
            ConfigStates.select_model,
            ConfigStates.select_provider,
            ConfigStates.configure,
        ),
    ):
        return
    deps = _deps()
    safe_edit_text = deps["safe_edit_text"]
    await state.clear()
    await safe_edit_text(callback.message, "Configuración cerrada.", reply_markup=None)
    await callback.answer()


def register_config_handlers(dp: Dispatcher, deps: dict[str, Any]) -> None:
    """Register unified /config command and cfg:* callback handlers."""
    global _CONFIG_DEPS
    _CONFIG_DEPS = deps

    dp.message.register(cmd_config, Command("config"))
    dp.message.register(cmd_model, Command("model"))
    dp.message.register(cmd_imaginess, Command("imagine", "imaginess"))
    dp.message.register(cmd_video, Command("video"))
    dp.callback_query.register(handle_cfg_back_model, lambda c: c.data == "cfg:back:model")
    dp.callback_query.register(handle_cfg_back_provider, lambda c: c.data == "cfg:back:provider")
    dp.callback_query.register(handle_cfg_close, lambda c: c.data == "cfg:close")
    dp.callback_query.register(handle_cfg_model, lambda c: c.data and c.data.startswith("cfg:model:"))
    dp.callback_query.register(handle_cfg_provider, lambda c: c.data and c.data.startswith("cfg:provider:"))
    dp.callback_query.register(handle_cfg_variant, lambda c: c.data and c.data.startswith("cfg:variant:"))
    dp.callback_query.register(handle_cfg_video, lambda c: c.data and c.data.startswith("cfg:video:"))