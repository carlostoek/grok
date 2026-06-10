from __future__ import annotations

import asyncio
import base64
import os
import shutil
import tempfile
from io import BytesIO
from pathlib import Path

import aiohttp
import replicate
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

import sessions
import download

load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
REPLICATE_TOKEN = os.environ["REPLICATE_API_TOKEN"]
XAI_API_KEY = os.environ["XAI_API_KEY"]
os.environ["REPLICATE_API_TOKEN"] = REPLICATE_TOKEN

SOURCES_DIR = Path(__file__).parent / "sources"

# --- Model Registry ---
MODELS = {
    "grok": {
        "key": "grok",
        # Base identifiers (variant-specific id/replicate_id resolved at runtime via get_grok_imagine_config + get_model)
        "id": "grok-imagine-image-quality",           # default/fallback (xAI direct)
        "replicate_id": "xai/grok-imagine-image-quality",
        "name": "Grok Imagine",
        "desc": "xAI Grok Imagine",
        "provider": "xai",
    },
    "seedream": {
        "key": "seedream",
        "id": "bytedance/seedream-5-lite",
        "name": "Seedream 5.0",
        "desc": "ByteDance Seedream 5.0 Lite",
        "provider": "replicate",
    },
    "faceswap": {
        "key": "faceswap",
        "id": "ddvinh1/inswapper:25bdae46f2713138640b6e8c04dc4ca18625ce95b1863936b053eee42d9ba6db",
        "name": "Face Swap",
        "desc": "Intercambio de caras con inswapper",
        "provider": "replicate",
    },
}

DEFAULT_MODEL = "grok"

# Granular Grok Imagine configuration (independent, persistent flow).
# Two providers (xAI direct / Replicate) × two quality tiers.
# Research-backed identifiers (xAI API + Replicate xai/ mirrors):
#   - standard: fast, grok-imagine-image / xai/grok-imagine-image
#   - quality : higher fidelity, better text/detail/2K, grok-imagine-image-quality / xai/grok-imagine-image-quality
GROK_IMAGINE_VARIANTS = {
    "standard": {
        "id": "grok-imagine-image",
        "replicate_id": "xai/grok-imagine-image",
        "label": "Estándar",
        "desc": "Rápido, ideal para prototipado y previews",
    },
    "quality": {
        "id": "grok-imagine-image-quality",
        "replicate_id": "xai/grok-imagine-image-quality",
        "label": "Alta calidad",
        "desc": "Mayor detalle, texto nítido, hasta 2K (recomendado para finales)",
    },
}
DEFAULT_GROK_IMAGINE_PROVIDER = "xai"
DEFAULT_GROK_IMAGINE_VARIANT = "quality"

# (GROK_PROVIDERS removed — replaced by the granular GROK_IMAGINE_VARIANTS + independent /imagine flow)

# Per-user in-memory cache (hydrated from sessions.py persistence on first access).
# Keys: model (top-level), grok_imagine_provider + grok_imagine_variant (granular Imagine config),
#       source_path, fs_state, pending_prompt.
# The Grok Imagine detailed config (provider + variant) lives in its own independent
# persistent flow (/imagine) and is completely decoupled from the /model top-level selector.
user_state: dict[int, dict] = {}


def get_user_state(user_id: int) -> dict:
    if user_id not in user_state:
        # Hydrate from disk persistence (sessions.py now stores model + imagine granular config)
        persisted = sessions.get_session(user_id)
        user_state[user_id] = {
            "model": persisted.get("model", sessions.DEFAULT_MODEL),
            "grok_imagine_provider": persisted.get("grok_imagine_provider", sessions.DEFAULT_GROK_IMAGINE_PROVIDER),
            "grok_imagine_variant": persisted.get("grok_imagine_variant", sessions.DEFAULT_GROK_IMAGINE_VARIANT),
            "source_path": persisted.get("source_path"),
            "fs_state": persisted.get("state", sessions.FsState.IDLE),
            "pending_prompt": None,
        }
    return user_state[user_id]


def get_grok_imagine_config(user_id: int) -> dict:
    """Return the current granular (provider + variant) config for Grok Imagine.
    This is the source of truth for the independent persistent Imagine settings.
    Falls back to module defaults (which match the ones in sessions).
    """
    state = get_user_state(user_id)
    prov = state.get("grok_imagine_provider", sessions.DEFAULT_GROK_IMAGINE_PROVIDER)
    var = state.get("grok_imagine_variant", sessions.DEFAULT_GROK_IMAGINE_VARIANT)
    if prov not in ("xai", "replicate"):
        prov = sessions.DEFAULT_GROK_IMAGINE_PROVIDER
    if var not in GROK_IMAGINE_VARIANTS:
        var = sessions.DEFAULT_GROK_IMAGINE_VARIANT
    spec = GROK_IMAGINE_VARIANTS[var]
    return {
        "provider": prov,
        "variant": var,
        "id": spec["replicate_id"] if prov == "replicate" else spec["id"],
        "label": spec["label"],
        "desc": spec["desc"],
        "prov_label": "xAI" if prov == "xai" else "Replicate",
    }


def get_model(user_id: int) -> dict:
    """Return a concrete model dict for generation (and for display in UI).
    For the 'grok' (Grok Imagine) key the dict is dynamically built from the
    independent granular config (provider + standard/quality variant).
    Non-grok models are returned as-is from the static registry.
    """
    key = get_user_state(user_id)["model"]
    base = MODELS.get(key, MODELS[DEFAULT_MODEL])
    if key == "grok":
        m = dict(base)
        cfg = get_grok_imagine_config(user_id)
        m["provider"] = cfg["provider"]
        m["id"] = cfg["id"]  # already resolved to short (xAI) or full (Replicate)
        m["name"] = f"Grok Imagine ({cfg['prov_label']} • {cfg['label']})"
        m["desc"] = f"xAI Grok Imagine — {cfg['prov_label']} • {cfg['label']}: {cfg['desc']}"
        # keep a couple of extra fields for convenience in status messages
        m["imagine_provider"] = cfg["provider"]
        m["imagine_variant"] = cfg["variant"]
        return m
    return base


bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()


# --- Model selection keyboard (top-level only: Grok / Seedream / FaceSwap) ---
# The detailed Grok Imagine settings (provider + variant) are shown in the label
# but changed via the independent /imagine flow (not here).
def model_keyboard(user_id: int) -> InlineKeyboardMarkup:
    state = get_user_state(user_id)
    current_key = state["model"]
    buttons = []
    for key, m in MODELS.items():
        if key == "grok":
            # Use the separate persistent imagine config for the rich label
            cfg = get_grok_imagine_config(user_id)
            suffix = f"{cfg['prov_label']} • {cfg['label']}"
            label = f"{'✅ ' if key == current_key else ''}Grok Imagine ({suffix})"
        else:
            label = f"{'✅ ' if key == current_key else ''}{m['name']}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"model:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# (Old single-provider keyboard + handler fully removed. Granular Imagine config is exclusively in the /imagine dedicated flow.)


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    state = get_user_state(message.from_user.id)
    model = get_model(message.from_user.id)

    lines = [
        "Envame un prompt y te genero la imagen.\n",
        "Ejemplo: <i>a cat wearing a wizard hat in a neon-lit cyberpunk alley</i>\n",
        "Tambien puedes enviar una <b>foto con caption</b> para editarla:\n",
        "la IA tomara tu imagen y aplicara los cambios que describas en el caption.\n",
    ]

    if state["model"] == "faceswap":
        lines = [
            "Modo <b>Face Swap</b> activo.\n",
            "Usa /cambiar_source para configurar la cara fuente.\n",
            "Luego envia fotos para intercambiar las caras.\n",
            "Tambien puedes enviar albumes de fotos.\n",
        ]
        if state["source_path"]:
            lines.insert(2, "Source ya configurado. Envia tus fotos.\n")

    lines.append(f"Modelo actual: <b>{model['name']}</b>\n")
    lines.append("Usa /model para cambiar de modelo.")

    await message.answer("".join(lines), parse_mode="HTML")


# ---------------------------------------------------------------------------
# /model
# ---------------------------------------------------------------------------
@dp.message(Command("model"))
async def cmd_model(message: types.Message):
    await message.answer(
        "Selecciona el modelo:",
        reply_markup=model_keyboard(message.from_user.id),
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("model:"))
async def handle_model_selection(callback: types.CallbackQuery):
    model_key = callback.data.split(":", 1)[1]
    if model_key not in MODELS:
        await callback.answer("Modelo no disponible.", show_alert=True)
        return

    state = get_user_state(callback.from_user.id)

    if model_key == "grok":
        # Grok Imagine selected. We do NOT prompt for provider/variant here anymore.
        # The detailed settings live in a completely independent persistent flow (/imagine).
        # We just activate the top-level mode; display will use the saved granular config.
        state["model"] = "grok"
        # Also persist the top-level choice
        sessions.set_model(callback.from_user.id, "grok")

        model = get_model(callback.from_user.id)
        lines = [
            f"Modo <b>{model['name']}</b> activado.\n",
            f"<i>{model['desc']}</i>\n",
            "Usa /imagine para configurar proveedor (xAI/Replicate) y nivel de calidad (Estándar/Alta calidad).",
            "Esa configuración es persistente e independiente del selector de modelos.",
            "",
            "Enviame un prompt (o foto + caption para editar).",
        ]
        await callback.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=model_keyboard(callback.from_user.id),
        )
        await callback.answer(f"Modelo: {model['name']}")
        return

    # Non-grok models: direct switch (unchanged behavior)
    state["model"] = model_key
    sessions.set_model(callback.from_user.id, model_key)  # persist top-level choice
    model = MODELS[model_key]

    lines = [
        f"Modelo cambiado a <b>{model['name']}</b>.\n",
        f"<i>{model['desc']}</i>\n",
    ]

    if model_key == "faceswap":
        lines.append("Usa /cambiar_source para configurar tu cara fuente.\n")
        lines.append("Luego envia fotos (incluso albumes) para hacer face swap.")
    else:
        lines.append("Enviame un prompt para generar una imagen.")
        lines.append("O envia una foto con caption para editarla.")

    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=model_keyboard(callback.from_user.id),
    )
    await callback.answer(f"Modelo: {model['name']}")


# Old grokprov: handler removed.
# Provider + variant selection for Grok Imagine is now exclusively in the
# independent /imagine command (grokcfg: callbacks below).

@dp.callback_query(lambda c: c.data == "model:back")
async def handle_model_back(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "Selecciona el modelo:",
        reply_markup=model_keyboard(callback.from_user.id),
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# /imagine  — independent, persistent granular config for Grok Imagine only
# (provider x variant). Completely separate from the /model top-level selector.
# ---------------------------------------------------------------------------
def imagine_config_keyboard(current_prov: str, current_var: str) -> InlineKeyboardMarkup:
    """4-option keyboard for the dedicated Grok Imagine settings.
    Shows checkmarks for the active combination. Selection is immediate + persistent.
    """
    def mk(prov: str, var: str, text: str) -> InlineKeyboardButton:
        is_current = (prov == current_prov and var == current_var)
        prefix = "✅ " if is_current else ""
        return InlineKeyboardButton(
            text=f"{prefix}{text}",
            callback_data=f"grokcfg:{prov}:{var}",
        )

    buttons = [
        [mk("xai", "quality", "xAI (oficial) • Alta calidad")],
        [mk("xai", "standard", "xAI (oficial) • Estándar")],
        [mk("replicate", "quality", "Replicate • Alta calidad")],
        [mk("replicate", "standard", "Replicate • Estándar")],
        [InlineKeyboardButton(text="← Cerrar", callback_data="imagine:close")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(Command("imagine"))
async def cmd_imagine(message: types.Message):
    """Entry point to the separate persistent configuration for Grok Imagine.
    This flow manages provider (xAI vs Replicate) + quality tier and is
    remembered until the user makes an explicit change.
    """
    uid = message.from_user.id
    cfg = get_grok_imagine_config(uid)
    state = get_user_state(uid)
    # Ensure top-level is at least aware (optional nicety)
    if state.get("model") != "grok":
        # We don't force-switch the top model; user can do /model if they want.
        pass

    current_text = f"<b>{cfg['prov_label']} • {cfg['label']}</b>"

    text = (
        "Configuración de <b>Grok Imagine</b> (persistente e independiente).\n\n"
        f"Actual: {current_text}\n"
        f"<i>{cfg['desc']}</i>\n\n"
        "Elige combinación de proveedor y nivel de calidad. El cambio se guarda inmediatamente "
        "y se usará la próxima vez que actives Grok Imagine (vía /model o generación)."
    )
    await message.answer(text, parse_mode="HTML", reply_markup=imagine_config_keyboard(cfg["provider"], cfg["variant"]))


@dp.callback_query(lambda c: c.data and c.data.startswith("grokcfg:"))
async def handle_grok_imagine_config(callback: types.CallbackQuery):
    """Handle selection from the independent /imagine config keyboard.
    Persists via sessions.set_grok_imagine_config and updates runtime state + UI.
    """
    try:
        _, prov, var = callback.data.split(":", 2)
    except ValueError:
        await callback.answer("Opción inválida.", show_alert=True)
        return

    if prov not in ("xai", "replicate") or var not in GROK_IMAGINE_VARIANTS:
        await callback.answer("Configuración no disponible.", show_alert=True)
        return

    uid = callback.from_user.id
    state = get_user_state(uid)

    # Update runtime cache
    state["grok_imagine_provider"] = prov
    state["grok_imagine_variant"] = var

    # Persist (the key part of the requirement)
    sessions.set_grok_imagine_config(uid, prov, var)

    # Also make sure top-level model points to grok (convenience, not required)
    state["model"] = "grok"
    sessions.set_model(uid, "grok")

    cfg = get_grok_imagine_config(uid)
    model = get_model(uid)

    text = (
        "✅ Configuración de Grok Imagine actualizada y guardada.\n\n"
        f"Actual: <b>{cfg['prov_label']} • {cfg['label']}</b>\n"
        f"<i>{cfg['desc']}</i>\n\n"
        "Esta configuración es persistente. Se usará siempre que el modo Grok Imagine esté activo."
    )
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=imagine_config_keyboard(prov, var),
    )
    await callback.answer(f"Grok Imagine: {cfg['prov_label']} • {cfg['label']}")


@dp.callback_query(lambda c: c.data == "imagine:close")
async def handle_imagine_close(callback: types.CallbackQuery):
    await callback.message.edit_text("Configuración de Grok Imagine cerrada.")
    await callback.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("confirm:"))
async def handle_confirm_generation(callback: types.CallbackQuery):
    action = callback.data.split(":", 1)[1]
    state = get_user_state(callback.from_user.id)

    if action == "no":
        state["pending_prompt"] = None
        await callback.message.edit_text("Generacion cancelada.")
        await callback.answer()
        return

    prompt = state.get("pending_prompt")
    state["pending_prompt"] = None

    if not prompt:
        await callback.message.edit_text("El prompt ya no esta disponible. Envia uno nuevo.")
        await callback.answer()
        return

    model = get_model(callback.from_user.id)
    await callback.message.edit_text(
        f"Generando imagen con {model['name']}...\n\n<i>{prompt}</i>",
        parse_mode="HTML",
    )
    await callback.answer()

    state["pending_prompt"] = None
    await _do_generate_text(callback.message, model, prompt)


# ---------------------------------------------------------------------------
# /cambiar_source  (solo faceswap)
# ---------------------------------------------------------------------------
@dp.message(Command("cambiar_source"))
async def cmd_cambiar_source(message: types.Message):
    state = get_user_state(message.from_user.id)
    if state["model"] != "faceswap":
        await message.answer(
            "Este comando solo esta disponible en modo <b>Face Swap</b>.\n"
            "Usa /model para cambiar al modo Face Swap.",
            parse_mode="HTML",
        )
        return

    state["fs_state"] = sessions.FsState.AWAITING_SOURCE
    sessions.set_state(message.from_user.id, sessions.FsState.AWAITING_SOURCE)
    await message.answer("Envia tu foto source (la cara que quieres usar para el swap).")


# ---------------------------------------------------------------------------
# /estado
# ---------------------------------------------------------------------------
@dp.message(Command("estado"))
async def cmd_estado(message: types.Message):
    state = get_user_state(message.from_user.id)
    model = get_model(message.from_user.id)

    lines = [
        "Estado\n",
        f"Modelo: {model['name']}\n",
    ]

    if state["model"] == "faceswap":
        has_source = bool(state["source_path"])
        lines.append(f"Source: {'Configurado' if has_source else 'No configurado'}\n")
        lines.append(f"Estado: {state['fs_state']}")
    else:
        if model.get("key") == "grok":
            prov = model.get("imagine_provider") or model.get("provider", "?")
            var = model.get("imagine_variant", "?")
            var_label = GROK_IMAGINE_VARIANTS.get(var, {}).get("label", var)
            prov_label = "xAI (oficial)" if prov == "xai" else "Replicate"
            lines.append(f"API / Backend: {prov_label} • {var_label}\n")
        lines.append("Listo para generar/editar imagenes.")

    await message.answer("\n".join(lines))


# ---------------------------------------------------------------------------
# TEXT messages — route by model
# ---------------------------------------------------------------------------
@dp.message(lambda m: m.text and not m.reply_to_message)
async def handle_text(message: types.Message):
    state = get_user_state(message.from_user.id)

    if state["model"] == "faceswap":
        if state["source_path"]:
            await message.answer(
                "Envia una <b>foto</b> para hacer el face swap.\n"
                "Usa /cambiar_source si quieres cambiar la cara fuente.",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                "Primero configura tu cara fuente con /cambiar_source.\n"
                "Luego enviame fotos para intercambiar las caras.",
            )
        return

    # --- grok / seedream: text → generate image ---
    prompt = message.text.strip()
    if len(prompt) < 3:
        await message.answer("El prompt es muy corto. Dame algo mas descriptivo.")
        return

    model = get_model(message.from_user.id)

    if model["key"] == "grok":
        state["pending_prompt"] = prompt
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Confirmar", callback_data="confirm:yes"),
             InlineKeyboardButton(text="Cancelar", callback_data="confirm:no")],
        ])
        await message.answer(
            f"¿Confirmas generar esta imagen?\n\n<i>{prompt}</i>",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    await _do_generate_text(message, model, prompt)


async def _do_generate_text(message: types.Message, model: dict, prompt: str):
    status_msg = await message.answer(f"Generando imagen con {model['name']}...")

    backend = "xAI" if model.get("provider") == "xai" else "Replicate"
    try:
        output, err = await generate_image(model, prompt)
        if err:
            await status_msg.edit_text(err)
            return
        await process_image_result(output, prompt, status_msg, message, "Prompt")
    except replicate.exceptions.ReplicateError as e:
        await status_msg.edit_text(f"Error de {backend}: {e}")
    except Exception as e:
        await status_msg.edit_text(f"Error inesperado: {e}")


# ---------------------------------------------------------------------------
# PHOTO + CAPTION  — route by model
# ---------------------------------------------------------------------------
@dp.message(lambda m: m.photo and m.caption)
async def handle_photo_caption(message: types.Message):
    state = get_user_state(message.from_user.id)

    # --- faceswap: photo + caption (caption ignored, just do swap) ---
    if state["model"] == "faceswap":
        await _handle_faceswap_photo(message)
        return

    # --- grok / seedream: photo + caption → edit ---
    prompt = message.caption.strip()
    model = get_model(message.from_user.id)
    status_msg = await message.answer(f"Editando imagen con {model['name']}...")

    backend = "xAI" if model.get("provider") == "xai" else "Replicate"
    try:
        file = await bot.get_file(message.photo[-1].file_id)
        file_bytes = await bot.download_file(file.file_path)
        file_bytes.seek(0)
        image_data = BytesIO(file_bytes.read())
        image_data.name = "image.jpg"

        output, err = await generate_image(model, prompt, image_data)
        if err:
            await status_msg.edit_text(err)
            return
        await process_image_result(output, prompt, status_msg, message, "Edit")
    except replicate.exceptions.ReplicateError as e:
        await status_msg.edit_text(f"Error de {backend}: {e}")
    except Exception as e:
        await status_msg.edit_text(f"Error inesperado: {e}")


# ---------------------------------------------------------------------------
# PHOTO WITHOUT CAPTION  — route by model
# ---------------------------------------------------------------------------
@dp.message(lambda m: m.photo and not m.caption and not m.media_group_id)
async def handle_photo_no_caption(message: types.Message):
    state = get_user_state(message.from_user.id)

    if state["model"] == "faceswap":
        await _handle_faceswap_photo(message)
        return

    # grok / seedream
    await message.answer(
        "Para editar una imagen, enviala con un <b>caption</b> describiendo los cambios que quieres.\n\n"
        "Ejemplo: envia tu foto con el texto <i>\"cambia el fondo a una playa al atardecer\"</i>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# REPLY to photo (text reply to an image) — route by model
# ---------------------------------------------------------------------------
@dp.message(lambda m: m.text and m.reply_to_message and m.reply_to_message.photo)
async def handle_reply_edit(message: types.Message):
    state = get_user_state(message.from_user.id)

    if state["model"] == "faceswap":
        await message.answer(
            "En modo Face Swap no se usa reply con texto.\n"
            "Simplemente envia la foto directamente para hacer el swap.",
        )
        return

    # grok / seedream
    prompt = message.text.strip()
    if len(prompt) < 3:
        await message.answer("El prompt es muy corto. Dame algo mas descriptivo.")
        return

    model = get_model(message.from_user.id)
    status_msg = await message.answer(f"Editando imagen con {model['name']}...")

    backend = "xAI" if model.get("provider") == "xai" else "Replicate"
    try:
        replied_photo = message.reply_to_message.photo[-1]
        file = await bot.get_file(replied_photo.file_id)
        file_bytes = await bot.download_file(file.file_path)
        file_bytes.seek(0)
        image_data = BytesIO(file_bytes.read())
        image_data.name = "image.jpg"

        output, err = await generate_image(model, prompt, image_data)
        if err:
            await status_msg.edit_text(err)
            return
        await process_image_result(output, prompt, status_msg, message, "Edit")
    except replicate.exceptions.ReplicateError as e:
        await status_msg.edit_text(f"Error de {backend}: {e}")
    except Exception as e:
        await status_msg.edit_text(f"Error inesperado: {e}")


# ---------------------------------------------------------------------------
# ALBUM (media group) — only for faceswap
# ---------------------------------------------------------------------------
_album_cache: dict[tuple, list] = {}
_album_lock = asyncio.Lock()
ALBUM_COLLECT_DELAY = 1.0


@dp.message(lambda m: m.photo and m.media_group_id)
async def handle_album(message: types.Message):
    state = get_user_state(message.from_user.id)

    if state["model"] != "faceswap":
        return  # silently ignore albums for image generation models

    if state["fs_state"] == sessions.FsState.AWAITING_SOURCE:
        await _handle_faceswap_source_photo(message)
        return

    if not state["source_path"]:
        await message.answer(
            "Primero configura tu cara fuente con /cambiar_source."
        )
        return

    media_group_id = message.media_group_id
    chat_id = message.chat.id
    cache_key = (chat_id, media_group_id)

    async with _album_lock:
        if cache_key not in _album_cache:
            _album_cache[cache_key] = []
            asyncio.create_task(
                _process_album_after_delay(cache_key, chat_id, message)
            )
        _album_cache[cache_key].append(message)


async def _process_album_after_delay(cache_key: tuple, chat_id: int, first_msg: types.Message):
    await asyncio.sleep(ALBUM_COLLECT_DELAY)

    async with _album_lock:
        messages = _album_cache.pop(cache_key, [])

    if not messages:
        return

    state = get_user_state(first_msg.from_user.id)
    source_path = Path(state["source_path"])
    if not source_path.exists():
        await first_msg.reply("Source no encontrado. Usa /cambiar_source.")
        return

    file_ids = [msg.photo[-1].file_id for msg in messages if msg.photo]
    count = len(file_ids)
    if count == 0:
        return

    status_msg = await first_msg.reply(
        f"Procesando {count} imagen{'es' if count > 1 else ''}..."
    )

    temp_input = Path(tempfile.mkdtemp(prefix="fs_album_"))
    temp_output = temp_input / "output"

    downloaded = []
    try:
        for fid in file_ids:
            path = await download.download_telegram_photo(bot, fid, temp_input)
            downloaded.append(path)

        stats = _process_batch_replicate_sync(
            source_path=str(source_path),
            input_dir=temp_input,
            output_dir=temp_output,
        )

        processed = sorted(temp_output.glob("*"))
        if processed:
            media = []
            for p in processed:
                media.append(types.InputMediaPhoto(
                    media=BufferedInputFile(p.read_bytes(), filename=p.name)
                ))
            await first_msg.reply_media_group(media)

        await status_msg.edit_text(
            f"Procesadas {stats['processed']}/{count} imagenes"
        )
    except Exception as e:
        await status_msg.edit_text(f"Error: {e}")
    finally:
        download.cleanup_temp_files(downloaded)
        shutil.rmtree(temp_input, ignore_errors=True)


# ---------------------------------------------------------------------------
# Face swap photo processing (single photo, no album)
# ---------------------------------------------------------------------------
async def _handle_faceswap_photo(message: types.Message):
    state = get_user_state(message.from_user.id)

    # If awaiting source, save as source
    if state["fs_state"] == sessions.FsState.AWAITING_SOURCE:
        await _handle_faceswap_source_photo(message)
        return

    # Need a source to swap
    if not state["source_path"]:
        await message.answer(
            "Primero configura tu cara fuente con /cambiar_source."
        )
        return

    source_path = Path(state["source_path"])
    if not source_path.exists():
        await message.answer(
            "Source no encontrado. Usa /cambiar_source para configurar de nuevo."
        )
        state["source_path"] = None
        return

    status_msg = await message.answer("Procesando face swap...")

    temp_input = Path(tempfile.mkdtemp(prefix="fs_single_"))
    temp_output = temp_input / "output"
    target_path = None

    try:
        file_id = message.photo[-1].file_id
        target_path = await download.download_telegram_photo(bot, file_id, temp_input)

        stats = _process_batch_replicate_sync(
            source_path=str(source_path),
            input_dir=temp_input,
            output_dir=temp_output,
        )

        processed = list(temp_output.glob("*"))
        if processed:
            with open(processed[0], "rb") as f:
                photo = BufferedInputFile(f.read(), filename="swap.jpg")
                await message.reply_photo(photo)
        else:
            await message.answer("No se pudo procesar la imagen.")

        await status_msg.edit_text(f"Procesada {stats['processed']} imagen")
    except Exception as e:
        await status_msg.edit_text(f"Error: {e}")
    finally:
        if target_path:
            download.cleanup_temp_files([target_path])
        shutil.rmtree(temp_input, ignore_errors=True)


async def _handle_faceswap_source_photo(message: types.Message):
    file_id = message.photo[-1].file_id
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    source_path = SOURCES_DIR / f"{message.from_user.id}.jpg"

    file = await bot.get_file(file_id)
    file_bytes = await bot.download_file(file.file_path)
    source_path.write_bytes(file_bytes.read())

    state = get_user_state(message.from_user.id)
    state["source_path"] = str(source_path)
    state["fs_state"] = sessions.FsState.IDLE
    sessions.set_source(message.from_user.id, str(source_path))

    await message.answer("Source actualizado. Ahora envia tus fotos para hacer face swap.")


# ---------------------------------------------------------------------------
# Replicate face swap batch (sync wrapper around replicate.run)
# ---------------------------------------------------------------------------
def _process_batch_replicate_sync(source_path: str, input_dir: Path, output_dir: Path) -> dict:
    extensions = (".jpg", ".jpeg", ".png", ".webp")
    image_files = []
    for ext in extensions:
        image_files.extend(list(input_dir.glob(f"*{ext}")))
        image_files.extend(list(input_dir.glob(f"*{ext.upper()}")))
    image_files = sorted(set(image_files))

    output_dir.mkdir(parents=True, exist_ok=True)
    stats = {"total": len(image_files), "processed": 0, "failed": 0}

    # Encode source as base64 data URI once
    with open(source_path, "rb") as f:
        source_b64 = base64.b64encode(f.read()).decode()
    source_uri = f"data:image/jpeg;base64,{source_b64}"

    for target_path in image_files:
        try:
            with open(target_path, "rb") as f:
                target_b64 = base64.b64encode(f.read()).decode()
            target_uri = f"data:image/jpeg;base64,{target_b64}"

            output = replicate.run(
                MODELS["faceswap"]["id"],
                input={"source_img": source_uri, "target_img": target_uri},
            )

            result_path = output_dir / target_path.name
            if isinstance(output, str):
                import urllib.request
                result_path.write_bytes(urllib.request.urlopen(output).read())
            elif hasattr(output, "read"):
                result_path.write_bytes(output.read())
            elif isinstance(output, list) and len(output) > 0:
                url = output[0].url if hasattr(output[0], "url") else str(output[0])
                import urllib.request
                result_path.write_bytes(urllib.request.urlopen(url).read())
            stats["processed"] += 1
        except Exception as e:
            print(f"Error processing {target_path.name}: {e}")
            stats["failed"] += 1

    return stats


# ---------------------------------------------------------------------------
# Image generation / editing (grok / seedream)
# ---------------------------------------------------------------------------
async def generate_image(model: dict, prompt: str, image_data: BytesIO | None = None) -> tuple[object | None, str | None]:
    prov = model.get("provider", "?")
    model_id = model.get("id")
    print(f"[generate] key={model.get('key')} provider={prov} id={model_id} has_image={image_data is not None}")
    if prov == "xai":
        return await _generate_xai(model, prompt, image_data)
    return await _generate_replicate(model, prompt, image_data)


async def _generate_replicate(model: dict, prompt: str, image_data: BytesIO | None = None) -> tuple[object | None, str | None]:
    model_id = model["id"]
    input_data: dict = {"prompt": prompt}
    extra_kwargs: dict = {}

    if image_data:
        if model["key"] == "seedream":
            image_data.seek(0)
            b64 = base64.b64encode(image_data.read()).decode()
            input_data["image_input"] = [f"data:image/jpeg;base64,{b64}"]
            input_data["size"] = "2K"
        else:
            input_data["image"] = image_data
            extra_kwargs["file_encoding_strategy"] = "base64"

    output = await asyncio.to_thread(replicate.run, model_id, input=input_data, **extra_kwargs)
    return output, None


XAI_BASE = "https://api.x.ai/v1"


async def _generate_xai(model: dict, prompt: str, image_data: BytesIO | None = None) -> tuple[object | None, str | None]:
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }

    if image_data:
        image_data.seek(0)
        b64 = base64.b64encode(image_data.read()).decode()
        body = {
            "model": model["id"],
            "prompt": prompt,
            "image": {"url": f"data:image/jpeg;base64,{b64}", "type": "image_url"},
        }
        url = f"{XAI_BASE}/images/edits"
    else:
        body = {
            "model": model["id"],
            "prompt": prompt,
            "n": 1,
        }
        url = f"{XAI_BASE}/images/generations"

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as resp:
            if resp.status != 200:
                text = await resp.text()
                return None, f"Error de xAI ({resp.status}): {text[:200]}"
            data = await resp.json()

    result = data["data"][0]
    if "url" in result:
        return [result["url"]], None
    return None, "xAI no devolvio URL de imagen"


async def process_image_result(output, prompt: str, status_msg: types.Message, message: types.Message, prefix: str):
    if output is None:
        await status_msg.edit_text("Error: el modelo no devolvio nada. Intenta con otro prompt.")
        return

    image_url = output[0] if isinstance(output, list) else output
    if hasattr(image_url, "url"):
        image_url = image_url.url

    image_bytes = await download_image(str(image_url))
    photo = BufferedInputFile(image_bytes, filename="generated.png")
    await message.answer_photo(photo, caption=f"<b>{prefix}:</b> {prompt}", parse_mode="HTML")
    await status_msg.delete()


async def download_image(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.read()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
