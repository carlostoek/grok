from __future__ import annotations

import asyncio
import base64
import html
import os
import shutil
import tempfile
import threading
import time
import urllib.parse
from io import BytesIO
from pathlib import Path

from typing import Any, Awaitable, Callable

import aiohttp
import replicate
from aiogram import BaseMiddleware, Bot, Dispatcher, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    TelegramObject,
)
from dotenv import load_dotenv

import sessions
import download

load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
REPLICATE_TOKEN = os.environ["REPLICATE_API_TOKEN"]
XAI_API_KEY = os.environ["XAI_API_KEY"]
os.environ["REPLICATE_API_TOKEN"] = REPLICATE_TOKEN


def _parse_allowed_telegram_ids() -> set[int] | None:
    raw = os.environ.get("ALLOWED_TELEGRAM_IDS", "").strip()
    if not raw:
        return None
    return {int(item.strip()) for item in raw.split(",") if item.strip()}


ALLOWED_TELEGRAM_IDS = _parse_allowed_telegram_ids()
VIDEO_MAX_GLOBAL_CONCURRENT = int(os.environ.get("VIDEO_MAX_GLOBAL_CONCURRENT", "5"))
VIDEO_MAX_GLOBAL_HOURLY = int(os.environ.get("VIDEO_MAX_GLOBAL_HOURLY", "50"))
MAX_PROMPT_LEN = 1000

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
    "grok_video": {
        "key": "grok_video",
        "id": "grok-imagine-video",
        "name": "Grok Imagine Video",
        "desc": "Generación de video con xAI Grok Imagine",
        "provider": "xai",
    },
}

VIDEO_MODEL_LABELS = {
    "grok-imagine-video": "Base",
    "grok-imagine-video-1.5": "1.5 (reciente)",
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

# xAI video generation polling
VIDEO_POLL_INTERVAL_SEC = 5
VIDEO_MAX_POLL_SEC = 600  # 10 minutes
I2V_MAX_IMAGE_BYTES = 5 * 1024 * 1024
DOWNLOAD_TIMEOUT_SEC = 120
DOWNLOAD_MAX_BYTES = 50 * 1024 * 1024
TELEGRAM_MAX_VIDEO_BYTES = 50 * 1024 * 1024
VIDEO_MAX_CONCURRENT_PER_USER = 1
VIDEO_MAX_PER_HOUR = 10
POLL_MAX_RETRIES = 3
POLL_RETRY_BACKOFF_SEC = (2, 4, 8)
# xAI serves generated assets from *.x.ai / *.xai.com only (no broad CDN suffixes).
ALLOWED_DOWNLOAD_HOST_SUFFIXES = (".x.ai", ".xai.com")

# (GROK_PROVIDERS removed — replaced by the granular GROK_IMAGINE_VARIANTS + independent /imagine flow)

# Per-user in-memory cache (hydrated from sessions.py persistence on first access).
# Keys: model (top-level), grok_imagine_provider + grok_imagine_variant (granular Imagine config),
#       source_path, fs_state, pending_prompt.
# The Grok Imagine detailed config (provider + variant) lives in its own independent
# persistent flow (/imagine) and is completely decoupled from the /model top-level selector.
user_state: dict[int, dict] = {}
_video_active_jobs: set[int] = set()
_video_global_active_count = 0
_video_limit_lock = asyncio.Lock()
_video_hourly_lock = threading.Lock()
_video_hourly_pending: dict[int, int] = {}
_video_global_hourly_pending = 0


def _escape_prompt(prompt: str) -> str:
    return html.escape(prompt)


def _video_status_message(model_id: str, detail: str, prompt: str) -> str:
    """Video generation status line with model id in bold (HTML parse_mode)."""
    return (
        f"Generando video con <b>{html.escape(model_id)}</b>... {detail}\n\n"
        f"<i>{_escape_prompt(prompt)}</i>"
    )


def _video_start_message(model_id: str, prompt: str) -> str:
    """Initial video generation message before polling updates (HTML parse_mode)."""
    return (
        f"Generando video con <b>{html.escape(model_id)}</b>...\n\n"
        f"<i>{_escape_prompt(prompt)}</i>"
    )


def _xai_user_error(context: str = "generación") -> str:
    return f"Error en la {context}. Intenta de nuevo más tarde."


def _log_xai_error(status: int, request_id: str | None = None) -> None:
    suffix = f" request_id={request_id}" if request_id else ""
    print(f"[xAI error] status={status}{suffix}")


def _xai_http_ok(status: int) -> bool:
    """xAI video endpoints may return 200 or 202 (Accepted) while async work is in flight."""
    return status in (200, 202)


def _is_user_allowed(user_id: int) -> bool:
    if ALLOWED_TELEGRAM_IDS is None:
        return True
    return user_id in ALLOWED_TELEGRAM_IDS


def _validate_prompt(prompt: str) -> str | None:
    if len(prompt) < 3:
        return "El prompt es muy corto. Dame algo mas descriptivo."
    if len(prompt) > MAX_PROMPT_LEN:
        return f"El prompt es demasiado largo (máximo {MAX_PROMPT_LEN} caracteres)."
    return None


def _image_to_data_uri(image_data: BytesIO, mime: str = "image/jpeg") -> str:
    image_data.seek(0)
    b64 = base64.b64encode(image_data.read()).decode()
    return f"data:{mime};base64,{b64}"


def _validate_image_for_i2v(image_data: BytesIO) -> str | None:
    image_data.seek(0, os.SEEK_END)
    size = image_data.tell()
    image_data.seek(0)
    if size > I2V_MAX_IMAGE_BYTES:
        max_mb = I2V_MAX_IMAGE_BYTES // 1024 // 1024
        got_mb = max(1, size // 1024 // 1024)
        return f"La imagen es demasiado grande ({got_mb} MB). Máximo {max_mb} MB."
    return None


def _is_allowed_download_host(host: str) -> bool:
    host = (host or "").lower()
    if host in ("x.ai",):
        return True
    return any(host.endswith(suffix) for suffix in ALLOWED_DOWNLOAD_HOST_SUFFIXES)


async def _acquire_video_concurrency(user_id: int) -> str | None:
    global _video_global_active_count
    async with _video_limit_lock:
        if _video_global_active_count >= VIDEO_MAX_GLOBAL_CONCURRENT:
            return "El servidor está ocupado con otras generaciones de video. Intenta más tarde."
        if user_id in _video_active_jobs:
            return "Ya tienes una generación de video en curso. Espera a que termine."
        _video_active_jobs.add(user_id)
        _video_global_active_count += 1
    return None


def _effective_user_hourly_usage(user_id: int) -> int:
    return sessions.count_video_hourly_usage(user_id) + _video_hourly_pending.get(user_id, 0)


def _effective_global_hourly_usage() -> int:
    return sessions.count_global_video_hourly_usage() + _video_global_hourly_pending


def _release_video_hourly_pending(user_id: int) -> None:
    global _video_global_hourly_pending
    pending = _video_hourly_pending.get(user_id, 0)
    if pending <= 0:
        return
    _video_hourly_pending[user_id] = pending - 1
    if _video_hourly_pending[user_id] == 0:
        del _video_hourly_pending[user_id]
    _video_global_hourly_pending = max(0, _video_global_hourly_pending - 1)


async def _reserve_video_hourly_quota(user_id: int) -> str | None:
    """Atomically check hourly limits and reserve one in-flight slot (threading.Lock)."""
    def _reserve() -> str | None:
        global _video_global_hourly_pending
        with _video_hourly_lock:
            if _effective_user_hourly_usage(user_id) >= VIDEO_MAX_PER_HOUR:
                return "Has alcanzado el límite de videos por hora. Intenta más tarde."
            if _effective_global_hourly_usage() >= VIDEO_MAX_GLOBAL_HOURLY:
                return "El servidor alcanzó el límite global de videos por hora. Intenta más tarde."
            _video_hourly_pending[user_id] = _video_hourly_pending.get(user_id, 0) + 1
            _video_global_hourly_pending += 1
        return None

    return await asyncio.to_thread(_reserve)


def _commit_video_hourly_quota(user_id: int) -> None:
    """Persist hourly usage after successful POST and release the in-flight reservation."""
    with _video_hourly_lock:
        sessions.record_video_hourly_usage(user_id)
        _release_video_hourly_pending(user_id)


def _cancel_video_hourly_reservation(user_id: int) -> None:
    """Release a reserved hourly slot when the job never received a successful POST."""
    with _video_hourly_lock:
        _release_video_hourly_pending(user_id)


async def _release_video_concurrency(user_id: int) -> None:
    global _video_global_active_count
    async with _video_limit_lock:
        if user_id in _video_active_jobs:
            _video_active_jobs.discard(user_id)
            _video_global_active_count = max(0, _video_global_active_count - 1)


async def _download_telegram_photo(photo: types.PhotoSize) -> BytesIO:
    file = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file.file_path)
    file_bytes.seek(0)
    image_data = BytesIO(file_bytes.read())
    image_data.name = "image.jpg"
    return image_data


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


async def safe_edit_text(
    message: types.Message,
    text: str,
    **kwargs,
) -> bool:
    """Edit message text (and optional reply_markup etc.) safely.

    Ignores the benign 'message is not modified' error that Telegram returns
    when you attempt to edit a message to the exact same content + markup
    (very common when user re-taps the currently selected option in a keyboard
    that shows checkmarks).

    Returns:
        True if the edit went through, False if it was a no-op (same content).
    Raises any other TelegramBadRequest or unexpected errors.
    """
    try:
        await message.edit_text(text, **kwargs)
        return True
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return False
        raise


bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()


class AllowlistMiddleware(BaseMiddleware):
    """Block all message/callback handlers when ALLOWED_TELEGRAM_IDS is configured."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from_user = getattr(event, "from_user", None)
        user_id = getattr(from_user, "id", None) if from_user is not None else None
        if user_id is not None and not _is_user_allowed(user_id):
            answer = getattr(event, "answer", None)
            if answer is not None:
                if isinstance(event, types.CallbackQuery):
                    await event.answer("No tienes permiso para usar este bot.", show_alert=True)
                else:
                    await event.answer("No tienes permiso para usar este bot.")
            return None
        return await handler(event, data)


dp.message.middleware(AllowlistMiddleware())
dp.callback_query.middleware(AllowlistMiddleware())


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

    if state["model"] == "grok_video":
        lines = [
            "Envame un prompt y te genero un <b>video</b>.\n",
            "Ejemplo: <i>un gato descansando en un rayo de sol, moviendo la cola suavemente</i>\n",
            "Tambien puedes enviar una <b>foto con caption</b> para animarla (imagen a video):\n",
            "la IA tomara tu imagen y generara un video segun el caption.\n",
        ]
    elif state["model"] == "faceswap":
        lines = [
            "Modo <b>Face Swap</b> activo.\n",
            "Usa /cambiar_source para configurar la cara fuente.\n",
            "Luego envia fotos para intercambiar las caras.\n",
            "Tambien puedes enviar albumes de fotos.\n",
        ]
        if state["source_path"]:
            lines.insert(2, "Source ya configurado. Envia tus fotos.\n")
    else:
        lines = [
            "Envame un prompt y te genero la imagen (o video si eliges Grok Imagine Video).\n",
            "Ejemplo: <i>a cat wearing a wizard hat in a neon-lit cyberpunk alley</i>\n",
            "Tambien puedes enviar una <b>foto con caption</b> para editarla o animarla:\n",
            "la IA tomara tu imagen y aplicara los cambios que describas en el caption.\n",
        ]

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

    # Guard: if the user tapped the option that is already active, the resulting
    # text + keyboard (with ✅) would be identical. Telegram rejects that with
    # "message is not modified". Answer early for instant feedback and no API call.
    if model_key == state.get("model"):
        await callback.answer("Ya estás usando ese modelo.")
        return

    state["pending_prompt"] = None

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
        await safe_edit_text(
            callback.message,
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
    elif model_key == "grok_video":
        lines.append("Enviame un prompt para generar un video.")
        lines.append("O envia una foto con caption para animarla (imagen a video).")
    else:
        lines.append("Enviame un prompt para generar una imagen.")
        lines.append("O envia una foto con caption para editarla.")

    await safe_edit_text(
        callback.message,
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
    await safe_edit_text(
        callback.message,
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
    prior = sessions.get_grok_imagine_config(uid)

    # Guard against re-tapping the currently active (prov, var) combination.
    if prov == prior["provider"] and var == prior["variant"]:
        await callback.answer("Ya está activa esa configuración.")
        return

    # Update runtime cache
    state["grok_imagine_provider"] = prov
    state["grok_imagine_variant"] = var

    # Persist (the key part of the requirement)
    sessions.set_grok_imagine_config(uid, prov, var)

    # Also make sure top-level model points to grok (convenience, not required)
    state["model"] = "grok"
    sessions.set_model(uid, "grok")

    cfg = get_grok_imagine_config(uid)

    text = (
        "✅ Configuración de Grok Imagine actualizada y guardada.\n\n"
        f"Actual: <b>{cfg['prov_label']} • {cfg['label']}</b>\n"
        f"<i>{cfg['desc']}</i>\n\n"
        "Esta configuración es persistente. Se usará siempre que el modo Grok Imagine esté activo."
    )
    await safe_edit_text(
        callback.message,
        text,
        parse_mode="HTML",
        reply_markup=imagine_config_keyboard(prov, var),
    )
    await callback.answer(f"Grok Imagine: {cfg['prov_label']} • {cfg['label']}")


@dp.callback_query(lambda c: c.data == "imagine:close")
async def handle_imagine_close(callback: types.CallbackQuery):
    await callback.message.edit_text("Configuración de Grok Imagine cerrada.")
    await callback.answer()


# ---------------------------------------------------------------------------
# /video  — independent, persistent config for Grok Imagine Video
# ---------------------------------------------------------------------------
def video_config_keyboard(current: dict) -> InlineKeyboardMarkup:
    def model_btn(model_id: str) -> InlineKeyboardButton:
        prefix = "✅ " if model_id == current["model"] else ""
        label = VIDEO_MODEL_LABELS.get(model_id, model_id)
        return InlineKeyboardButton(
            text=f"{prefix}{label}",
            callback_data=f"videocfg:model:{model_id}",
        )

    def dur_btn(value: int) -> InlineKeyboardButton:
        prefix = "✅ " if value == current["duration"] else ""
        return InlineKeyboardButton(
            text=f"{prefix}{value}s",
            callback_data=f"videocfg:duration:{value}",
        )

    def aspect_btn(value: str) -> InlineKeyboardButton:
        prefix = "✅ " if value == current["aspect_ratio"] else ""
        return InlineKeyboardButton(
            text=f"{prefix}{value}",
            callback_data=f"videocfg:aspect:{value}",
        )

    def res_btn(value: str) -> InlineKeyboardButton:
        prefix = "✅ " if value == current["resolution"] else ""
        return InlineKeyboardButton(
            text=f"{prefix}{value}",
            callback_data=f"videocfg:resolution:{value}",
        )

    buttons = [
        [model_btn("grok-imagine-video"), model_btn("grok-imagine-video-1.5")],
        [dur_btn(5), dur_btn(10), dur_btn(15)],
        [aspect_btn("16:9"), aspect_btn("9:16"), aspect_btn("1:1")],
        [aspect_btn("4:3"), aspect_btn("3:4"), aspect_btn("3:2"), aspect_btn("2:3")],
        [res_btn("480p"), res_btn("720p")],
        [InlineKeyboardButton(text="← Cerrar", callback_data="video:close")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(Command("video"))
async def cmd_video(message: types.Message):
    cfg = sessions.get_video_config(message.from_user.id)
    model_label = VIDEO_MODEL_LABELS.get(cfg["model"], cfg["model"])
    text = (
        "Configuración de <b>Grok Imagine Video</b> (persistente).\n\n"
        f"Actual: <b>{model_label}</b> • {cfg['duration']}s • {cfg['aspect_ratio']} • {cfg['resolution']}\n\n"
        "Elige modelo, duración, relación de aspecto y resolución. El cambio se guarda inmediatamente."
    )
    await message.answer(text, parse_mode="HTML", reply_markup=video_config_keyboard(cfg))


@dp.callback_query(lambda c: c.data and c.data.startswith("videocfg:"))
async def handle_video_config(callback: types.CallbackQuery):
    try:
        _, field, value = callback.data.split(":", 2)
    except ValueError:
        await callback.answer("Opción inválida.", show_alert=True)
        return

    uid = callback.from_user.id
    prior = sessions.get_video_config(uid)

    if field == "model":
        if value not in sessions.VALID_VIDEO_MODELS:
            await callback.answer("Modelo no disponible.", show_alert=True)
            return
        if value == prior["model"]:
            await callback.answer("Ya está activo ese modelo.")
            return
        sessions.set_video_config(uid, model=value)
    elif field == "duration":
        try:
            duration = int(value)
        except ValueError:
            await callback.answer("Duración inválida.", show_alert=True)
            return
        if duration == prior["duration"]:
            await callback.answer("Ya está activa esa duración.")
            return
        sessions.set_video_config(uid, duration=duration)
    elif field == "aspect":
        if value not in sessions.VALID_VIDEO_ASPECT_RATIOS:
            await callback.answer("Relación de aspecto no disponible.", show_alert=True)
            return
        if value == prior["aspect_ratio"]:
            await callback.answer("Ya está activa esa relación de aspecto.")
            return
        sessions.set_video_config(uid, aspect_ratio=value)
    elif field == "resolution":
        if value not in sessions.VALID_VIDEO_RESOLUTIONS:
            await callback.answer("Resolución no disponible.", show_alert=True)
            return
        if value == prior["resolution"]:
            await callback.answer("Ya está activa esa resolución.")
            return
        sessions.set_video_config(uid, resolution=value)
    else:
        await callback.answer("Opción inválida.", show_alert=True)
        return

    cfg = sessions.get_video_config(uid)
    model_label = VIDEO_MODEL_LABELS.get(cfg["model"], cfg["model"])
    text = (
        "✅ Configuración de video actualizada y guardada.\n\n"
        f"Actual: <b>{model_label}</b> • {cfg['duration']}s • {cfg['aspect_ratio']} • {cfg['resolution']}\n\n"
        "Esta configuración es persistente para el modo Grok Imagine Video."
    )
    await safe_edit_text(
        callback.message,
        text,
        parse_mode="HTML",
        reply_markup=video_config_keyboard(cfg),
    )
    await callback.answer(
        f"Video: {model_label} • {cfg['duration']}s • {cfg['aspect_ratio']} • {cfg['resolution']}"
    )


@dp.callback_query(lambda c: c.data == "video:close")
async def handle_video_close(callback: types.CallbackQuery):
    await callback.message.edit_text("Configuración de video cerrada.")
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
    safe_prompt = _escape_prompt(prompt)
    if model["key"] == "grok_video":
        video_model = sessions.get_video_config(callback.from_user.id)["model"]
        await safe_edit_text(
            callback.message,
            _video_start_message(video_model, prompt),
            parse_mode="HTML",
            reply_markup=None,
        )
        await callback.answer()
        await _do_generate_video(
            callback.message,
            model,
            prompt,
            user_id=callback.from_user.id,
            status_msg=callback.message,
            reply_message=callback.message,
        )
        return

    await callback.message.edit_text(
        f"Generando imagen con {model['name']}...\n\n<i>{safe_prompt}</i>",
        parse_mode="HTML",
        reply_markup=None,
    )
    await callback.answer()
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
        elif model.get("key") == "grok_video":
            video_cfg = sessions.get_video_config(message.from_user.id)
            lines.append("API / Backend: xAI (oficial)\n")
            model_label = VIDEO_MODEL_LABELS.get(video_cfg["model"], video_cfg["model"])
            lines.append(
                f"Video: {model_label}, {video_cfg['duration']}s, "
                f"{video_cfg['aspect_ratio']}, {video_cfg['resolution']}\n"
            )
            lines.append("Listo para generar videos (texto o imagen a video).")
            lines.append("Usa /video para configurar modelo, duración, aspecto y resolución.")
        else:
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

    # --- grok / grok_video / seedream: text → generate image or video ---
    prompt = message.text.strip()
    prompt_err = _validate_prompt(prompt)
    if prompt_err:
        await message.answer(prompt_err)
        return

    model = get_model(message.from_user.id)

    if model["key"] in ("grok", "grok_video"):
        state["pending_prompt"] = prompt
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Confirmar", callback_data="confirm:yes"),
             InlineKeyboardButton(text="Cancelar", callback_data="confirm:no")],
        ])
        media_word = "video" if model["key"] == "grok_video" else "imagen"
        await message.answer(
            f"¿Confirmas generar este {media_word}?\n\n<i>{_escape_prompt(prompt)}</i>",
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


async def _do_generate_video(
    message: types.Message,
    model: dict,
    prompt: str,
    image_data: BytesIO | None = None,
    *,
    user_id: int | None = None,
    status_msg: types.Message | None = None,
    reply_message: types.Message | None = None,
):
    uid = user_id if user_id is not None else message.from_user.id
    reply_msg = reply_message or message
    quota_reserved = False

    hourly_err = await _reserve_video_hourly_quota(uid)
    if hourly_err:
        if status_msg:
            await status_msg.edit_text(hourly_err)
        else:
            await reply_msg.answer(hourly_err)
        return

    quota_reserved = True
    concurrency_err = await _acquire_video_concurrency(uid)
    if concurrency_err:
        _cancel_video_hourly_reservation(uid)
        quota_reserved = False
        if status_msg:
            await status_msg.edit_text(concurrency_err)
        else:
            await reply_msg.answer(concurrency_err)
        return

    try:
        if image_data:
            size_err = _validate_image_for_i2v(image_data)
            if size_err:
                _cancel_video_hourly_reservation(uid)
                quota_reserved = False
                if status_msg:
                    await status_msg.edit_text(size_err)
                else:
                    await reply_msg.answer(size_err)
                return

        if status_msg is None:
            video_model = sessions.get_video_config(uid)["model"]
            if image_data:
                status_text = (
                    f"Animando imagen con <b>{html.escape(video_model)}</b>...\n\n"
                    f"<i>{_escape_prompt(prompt)}</i>"
                )
            else:
                status_text = _video_start_message(video_model, prompt)
            status_msg = await reply_msg.answer(status_text, parse_mode="HTML")

        output, err = await generate_video(
            model,
            prompt,
            image_data,
            status_msg=status_msg,
            user_id=uid,
        )
        if err:
            await status_msg.edit_text(err)
            return
        prefix = "Edit" if image_data else "Prompt"
        await process_video_result(output, prompt, status_msg, reply_msg, prefix)
    except Exception as e:
        print(f"[video] unexpected error user={uid}: {e}")
        if status_msg:
            await status_msg.edit_text("Error inesperado. Intenta de nuevo.")
        else:
            await reply_msg.answer("Error inesperado. Intenta de nuevo.")
    finally:
        if quota_reserved:
            _cancel_video_hourly_reservation(uid)
        await _release_video_concurrency(uid)


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

    # --- grok / grok_video / seedream: photo + caption → edit or image-to-video ---
    prompt = message.caption.strip()
    prompt_err = _validate_prompt(prompt)
    if prompt_err:
        await message.answer(prompt_err)
        return

    model = get_model(message.from_user.id)
    status_msg = None

    try:
        image_data = await _download_telegram_photo(message.photo[-1])

        if model["key"] == "grok_video":
            await _do_generate_video(message, model, prompt, image_data, user_id=message.from_user.id)
            return

        status_msg = await message.answer(f"Editando imagen con {model['name']}...")
        backend = "xAI" if model.get("provider") == "xai" else "Replicate"
        output, err = await generate_image(model, prompt, image_data)
        if err:
            await status_msg.edit_text(err)
            return
        await process_image_result(output, prompt, status_msg, message, "Edit")
    except replicate.exceptions.ReplicateError as e:
        backend = "xAI" if model.get("provider") == "xai" else "Replicate"
        if status_msg:
            await status_msg.edit_text(f"Error de {backend}: {e}")
        else:
            await message.answer(f"Error de {backend}: {e}")
    except Exception as e:
        if status_msg:
            await status_msg.edit_text(f"Error inesperado: {e}")
        else:
            await message.answer(f"Error inesperado: {e}")


# ---------------------------------------------------------------------------
# PHOTO WITHOUT CAPTION  — route by model
# ---------------------------------------------------------------------------
@dp.message(lambda m: m.photo and not m.caption and not m.media_group_id)
async def handle_photo_no_caption(message: types.Message):
    state = get_user_state(message.from_user.id)

    if state["model"] == "faceswap":
        await _handle_faceswap_photo(message)
        return

    # grok / grok_video / seedream
    if get_model(message.from_user.id)["key"] == "grok_video":
        await message.answer(
            "Para animar una imagen (imagen a video), enviala con un <b>caption</b> describiendo el movimiento.\n\n"
            "Ejemplo: envia tu foto con el texto <i>\"haz que el agua caiga y aleja la camara lentamente\"</i>",
            parse_mode="HTML",
        )
        return

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

    # grok / grok_video / seedream
    prompt = message.text.strip()
    prompt_err = _validate_prompt(prompt)
    if prompt_err:
        await message.answer(prompt_err)
        return

    model = get_model(message.from_user.id)
    status_msg = None

    try:
        image_data = await _download_telegram_photo(message.reply_to_message.photo[-1])

        if model["key"] == "grok_video":
            await _do_generate_video(message, model, prompt, image_data, user_id=message.from_user.id)
            return

        status_msg = await message.answer(f"Editando imagen con {model['name']}...")
        backend = "xAI" if model.get("provider") == "xai" else "Replicate"
        output, err = await generate_image(model, prompt, image_data)
        if err:
            await status_msg.edit_text(err)
            return
        await process_image_result(output, prompt, status_msg, message, "Edit")
    except replicate.exceptions.ReplicateError as e:
        backend = "xAI" if model.get("provider") == "xai" else "Replicate"
        if status_msg:
            await status_msg.edit_text(f"Error de {backend}: {e}")
        else:
            await message.answer(f"Error de {backend}: {e}")
    except Exception as e:
        if status_msg:
            await status_msg.edit_text(f"Error inesperado: {e}")
        else:
            await message.answer(f"Error inesperado: {e}")


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
            input_data["image_input"] = [_image_to_data_uri(image_data)]
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
        size_err = _validate_image_for_i2v(image_data)
        if size_err:
            return None, size_err
        body = {
            "model": model["id"],
            "prompt": prompt,
            "image": {"url": _image_to_data_uri(image_data), "type": "image_url"},
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
                await resp.text()
                _log_xai_error(resp.status)
                return None, _xai_user_error("generación de imagen")
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

    image_bytes, dl_err = await download_url(str(image_url))
    if dl_err:
        await status_msg.edit_text(dl_err)
        return
    photo = BufferedInputFile(image_bytes, filename="generated.png")
    await message.answer_photo(
        photo,
        caption=f"<b>{prefix}:</b> {_escape_prompt(prompt)}",
        parse_mode="HTML",
    )
    await status_msg.delete()


async def download_url(
    url: str,
    *,
    max_bytes: int = DOWNLOAD_MAX_BYTES,
    enforce_host_allowlist: bool = False,
) -> tuple[bytes | None, str | None]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return None, "No se pudo descargar el archivo (URL no permitida)."

    host = (parsed.hostname or "").lower()
    if enforce_host_allowlist and not _is_allowed_download_host(host):
        print(f"[download_url] blocked host: {host}")
        return None, "No se pudo descargar el archivo (origen no permitido)."

    timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_SEC)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    return None, "No se pudo descargar el archivo. Intenta de nuevo."
                if enforce_host_allowlist:
                    final_host = (urllib.parse.urlparse(str(resp.url)).hostname or "").lower()
                    if not _is_allowed_download_host(final_host):
                        print(f"[download_url] blocked redirect host: {final_host}")
                        return None, "No se pudo descargar el archivo (origen no permitido)."
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.content.iter_chunked(65536):
                    total += len(chunk)
                    if total > max_bytes:
                        return None, "El archivo es demasiado grande para descargar."
                    chunks.append(chunk)
                data = b"".join(chunks)
                if not data:
                    return None, "El archivo descargado está vacío."
                return data, None
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        print(f"[download_url] error: {exc}")
        return None, "No se pudo descargar el archivo. Intenta de nuevo."


# ---------------------------------------------------------------------------
# Video generation (grok_video via xAI)
# ---------------------------------------------------------------------------
_VIDEO_STATUS_LABELS = {
    "pending": "en cola",
    "processing": "procesando",
}


async def generate_video(
    model: dict,
    prompt: str,
    image_data: BytesIO | None = None,
    *,
    status_msg: types.Message | None = None,
    user_id: int | None = None,
) -> tuple[str | None, str | None]:
    prov = model.get("provider", "?")
    if user_id is not None:
        model_id = sessions.get_video_config(user_id)["model"]
    else:
        model_id = sessions.DEFAULT_VIDEO_MODEL
    print(
        f"[generate_video] key={model.get('key')} provider={prov} id={model_id} "
        f"has_image={image_data is not None}"
    )
    if prov == "xai":
        return await _generate_xai_video(
            model,
            prompt,
            image_data,
            status_msg=status_msg,
            user_id=user_id,
        )
    return None, "Proveedor no soportado para generación de video."


async def _generate_xai_video(
    model: dict,
    prompt: str,
    image_data: BytesIO | None = None,
    *,
    status_msg: types.Message | None = None,
    user_id: int | None = None,
) -> tuple[str | None, str | None]:
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }

    video_cfg = sessions.get_video_config(user_id) if user_id is not None else {
        "duration": sessions.DEFAULT_VIDEO_DURATION,
        "aspect_ratio": sessions.DEFAULT_VIDEO_ASPECT_RATIO,
        "resolution": sessions.DEFAULT_VIDEO_RESOLUTION,
        "model": sessions.DEFAULT_VIDEO_MODEL,
    }

    model_id = video_cfg["model"]
    body: dict = {
        "model": model_id,
        "prompt": prompt,
        "duration": video_cfg["duration"],
        "aspect_ratio": video_cfg["aspect_ratio"],
        "resolution": video_cfg["resolution"],
    }

    if image_data:
        size_err = _validate_image_for_i2v(image_data)
        if size_err:
            return None, size_err
        body["image"] = {"url": _image_to_data_uri(image_data)}

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{XAI_BASE}/videos/generations",
            headers=headers,
            json=body,
        ) as resp:
            if not _xai_http_ok(resp.status):
                await resp.text()
                _log_xai_error(resp.status)
                return None, _xai_user_error("generación de video")
            data = await resp.json()

        request_id = data.get("request_id")
        if not request_id:
            return None, "No se pudo iniciar la generación de video. Intenta de nuevo."

        if user_id is not None:
            _commit_video_hourly_quota(user_id)

        started = time.monotonic()
        last_status = None
        last_elapsed_shown = -1

        while time.monotonic() - started < VIDEO_MAX_POLL_SEC:
            poll_data, poll_err = await _poll_video_once(session, request_id, headers)
            if poll_err:
                return None, poll_err

            status = poll_data.get("status", "unknown")

            if status == "done":
                video = poll_data.get("video") or {}
                respect_moderation = video.get(
                    "respect_moderation",
                    poll_data.get("respect_moderation"),
                )
                if respect_moderation is False:
                    return None, "El contenido no cumple las políticas de moderación."
                video_url = video.get("url")
                if not video_url:
                    return None, "No se recibió URL de video. Intenta de nuevo."
                return video_url, None

            if status in ("failed", "expired"):
                print(f"[video poll] status={status} request_id={request_id}")
                return None, _xai_user_error(f"generación de video ({status})")

            if status_msg:
                elapsed = int(time.monotonic() - started)
                if status in _VIDEO_STATUS_LABELS and status != last_status:
                    label = _VIDEO_STATUS_LABELS[status]
                    await safe_edit_text(
                        status_msg,
                        _video_status_message(model_id, label, prompt),
                        parse_mode="HTML",
                    )
                    last_status = status
                    last_elapsed_shown = elapsed
                elif status not in _VIDEO_STATUS_LABELS:
                    print(f"[video poll] unknown status: {status} request_id={request_id}")
                    if elapsed - last_elapsed_shown >= 30:
                        await safe_edit_text(
                            status_msg,
                            _video_status_message(model_id, f"({elapsed}s transcurridos)", prompt),
                            parse_mode="HTML",
                        )
                        last_elapsed_shown = elapsed

            await asyncio.sleep(VIDEO_POLL_INTERVAL_SEC)

    return None, "Tiempo de espera agotado (10 min). Intenta de nuevo."


async def _poll_video_once(
    session: aiohttp.ClientSession,
    request_id: str,
    headers: dict,
) -> tuple[dict | None, str | None]:
    """Poll video status once with retries on transient errors."""
    url = f"{XAI_BASE}/videos/{request_id}"
    for attempt in range(POLL_MAX_RETRIES + 1):
        try:
            async with session.get(url, headers=headers) as poll_resp:
                if poll_resp.status >= 500:
                    if attempt < POLL_MAX_RETRIES:
                        await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                        continue
                    await poll_resp.text()
                    _log_xai_error(poll_resp.status, request_id)
                    return None, _xai_user_error("consulta de video")
                if not _xai_http_ok(poll_resp.status):
                    await poll_resp.text()
                    _log_xai_error(poll_resp.status, request_id)
                    return None, _xai_user_error("consulta de video")
                return await poll_resp.json(), None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            print(f"[video poll] transient error: {exc}")
            if attempt < POLL_MAX_RETRIES:
                await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                continue
            return None, _xai_user_error("consulta de video")
    return None, _xai_user_error("consulta de video")


async def process_video_result(
    video_url: str,
    prompt: str,
    status_msg: types.Message,
    message: types.Message,
    prefix: str,
):
    if not video_url:
        await status_msg.edit_text("Error: el modelo no devolvió URL de video. Intenta con otro prompt.")
        return

    video_bytes, dl_err = await download_url(
        str(video_url),
        max_bytes=DOWNLOAD_MAX_BYTES,
        enforce_host_allowlist=True,
    )
    if dl_err:
        await status_msg.edit_text(dl_err)
        return

    safe_prompt = _escape_prompt(prompt)
    if len(video_bytes) > TELEGRAM_MAX_VIDEO_BYTES:
        await status_msg.edit_text(
            f"El video es demasiado grande para Telegram ({len(video_bytes) // 1024 // 1024} MB).\n"
            f"Descárgalo aquí:\n{video_url}"
        )
        return

    video = BufferedInputFile(video_bytes, filename="generated.mp4")
    try:
        await message.answer_video(
            video,
            caption=f"<b>{prefix}:</b> {safe_prompt}",
            parse_mode="HTML",
        )
        await status_msg.delete()
    except TelegramBadRequest as exc:
        print(f"[video] answer_video failed: {exc}")
        await status_msg.edit_text(
            "No se pudo enviar el video por Telegram.\n"
            f"Descárgalo aquí:\n{video_url}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
