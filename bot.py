from __future__ import annotations

import asyncio
import base64
import html
import json
import os
import shutil
import tempfile
import time
import urllib.parse
from io import BytesIO
from pathlib import Path

from typing import Any, Awaitable, Callable

import aiohttp
import replicate
from aiogram import BaseMiddleware, Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
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
KIE_API_KEY = os.environ.get("KIE_API_KEY", "")
os.environ["REPLICATE_API_TOKEN"] = REPLICATE_TOKEN


def _parse_allowed_telegram_ids() -> set[int] | None:
    raw = os.environ.get("ALLOWED_TELEGRAM_IDS", "").strip()
    if not raw:
        return None
    return {int(item.strip()) for item in raw.split(",") if item.strip()}


ALLOWED_TELEGRAM_IDS = _parse_allowed_telegram_ids()
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
VIDEO_MODE_LABELS = {
    "fun": "Fun",
    "normal": "Normal",
    "spicy": "Spicy",
}

DEFAULT_MODEL = "grok"

# Granular Grok Imagine configuration (independent, persistent flow).
# Three providers (xAI direct / Replicate / Kie.ai) × two quality tiers.
# Research-backed identifiers (xAI API + Replicate xai/ mirrors):
#   - standard: fast, grok-imagine-image / xai/grok-imagine-image
#   - quality : higher fidelity, better text/detail/2K, grok-imagine-image-quality / xai/grok-imagine-image-quality
GROK_IMAGINE_VARIANTS = {
    "standard": {
        "id": "grok-imagine-image",
        "replicate_id": "xai/grok-imagine-image",
        "kie_id": "grok-imagine/text-to-image",
        "label": "Estándar",
        "desc": "Rápido, ideal para prototipado y previews",
    },
    "quality": {
        "id": "grok-imagine-image-quality",
        "replicate_id": "xai/grok-imagine-image-quality",
        "kie_id": "grok-imagine/text-to-image",
        "label": "Alta calidad",
        "desc": "Mayor detalle, texto nítido, hasta 2K (recomendado para finales)",
    },
}
DEFAULT_GROK_IMAGINE_PROVIDER = "kie"
DEFAULT_GROK_IMAGINE_VARIANT = "quality"

# xAI video generation polling
VIDEO_POLL_INTERVAL_SEC = 5
VIDEO_MAX_POLL_SEC = 600  # 10 minutes
I2V_MAX_IMAGE_BYTES = 5 * 1024 * 1024
DOWNLOAD_TIMEOUT_SEC = 120
DOWNLOAD_MAX_BYTES = 50 * 1024 * 1024
TELEGRAM_MAX_VIDEO_BYTES = 50 * 1024 * 1024
POLL_MAX_RETRIES = 3
POLL_RETRY_BACKOFF_SEC = (2, 4, 8)
# xAI serves generated assets from *.x.ai / *.xai.com only (no broad CDN suffixes).
ALLOWED_DOWNLOAD_HOST_SUFFIXES = (".x.ai", ".xai.com")
# Kie.ai result and upload CDN hosts (exact + subdomain suffixes from API probes).
KIE_DOWNLOAD_HOSTS = frozenset({
    "kieai.redpandaai.co",
    "static.aiquickdraw.com",
    "tempfile.redpandaai.co",
    "tempfile.aiquickdraw.com",
    "file.aiquickdraw.com",
})
KIE_DOWNLOAD_HOST_SUFFIXES = (".aiquickdraw.com", ".redpandaai.co")
KIE_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_SEC)

# (GROK_PROVIDERS removed — replaced by the granular GROK_IMAGINE_VARIANTS + unified /config FSM)

# Per-user in-memory cache (hydrated from sessions.py persistence on first access).
# Keys: model (top-level), grok_imagine_provider + grok_imagine_variant (granular Imagine config),
#       source_path, fs_state, pending_prompt.
# Model, provider, and model-specific settings are configured via the unified /config FSM
# (/config, /model, /imagine, /imaginess, /video).
user_state: dict[int, dict] = {}


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


def _kie_user_error(context: str = "generación") -> str:
    return f"Error en la {context}. Intenta de nuevo más tarde."


def _prov_label(prov: str) -> str:
    return {"xai": "xAI", "replicate": "Replicate", "kie": "Kie.ai"}.get(prov, prov)


_KIE_NOT_CONFIGURED_MSG = "Kie.ai no está disponible en este momento. Contacta al administrador del bot."
_KIE_PRIVACY_NOTICE = (
    "Los prompts e imágenes se envían a servidores de Kie.ai (tercero) para procesamiento."
)
_KIE_QUALITY_NOTE = (
    "Nota Kie.ai: Alta calidad aplica solo a imágenes. En video solo está disponible el modo estándar."
)
_SENSITIVE_DOWNLOAD_WARNING = (
    "\n\n⚠️ Enlace temporal con tu contenido generado; no lo compartas públicamente."
)


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


def _is_bot_command_message(message: types.Message) -> bool:
    """True when the message is a Telegram bot command (e.g. /config), not user prompt text."""
    if not message.text:
        return False
    if message.entities:
        for ent in message.entities:
            if ent.type == "bot_command" and ent.offset == 0:
                return True
    # Fallback for mocks/tests without entities
    stripped = message.text.lstrip()
    return stripped.startswith("/") and len(stripped) > 1 and stripped[1].isalnum()


def _is_generation_prompt_message(message: types.Message) -> bool:
    """Plain user text for image/video generation — excludes commands and replies."""
    return bool(
        message.text
        and not message.reply_to_message
        and not _is_bot_command_message(message)
    )


def _validate_prompt(prompt: str) -> str | None:
    if len(prompt) < 3:
        return "El prompt es muy corto. Dame algo mas descriptivo."
    if len(prompt) > MAX_PROMPT_LEN:
        return f"El prompt es demasiado largo (máximo {MAX_PROMPT_LEN} caracteres)."
    return None


def _detect_image_mime(image_data: BytesIO) -> tuple[str, str]:
    """Return (mime_type, file_extension) from image magic bytes."""
    image_data.seek(0)
    header = image_data.read(16)
    image_data.seek(0)
    if header[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "jpg"
    if header[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp", "webp"
    return "image/jpeg", "jpg"


def _image_to_data_uri(image_data: BytesIO, mime: str | None = None) -> str:
    if mime is None:
        mime, _ = _detect_image_mime(image_data)
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


def _is_allowed_kie_download_host(host: str) -> bool:
    host = (host or "").lower()
    if host in KIE_DOWNLOAD_HOSTS:
        return True
    return any(host.endswith(suffix) for suffix in KIE_DOWNLOAD_HOST_SUFFIXES)


def _is_allowed_kie_asset_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return False
    return _is_allowed_kie_download_host(parsed.hostname or "")


def _download_allowlist_for_provider(provider: str | None) -> str | None:
    """Return download allowlist key for a provider, or None for no host check."""
    if provider == "xai":
        return "xai"
    if provider == "kie":
        return "kie"
    return None


def _is_host_allowed_for_download(host: str, allowlist: str | None) -> bool:
    if allowlist == "xai":
        return _is_allowed_download_host(host)
    if allowlist == "kie":
        return _is_allowed_kie_download_host(host)
    return True


def get_video_provider_for_user(user_id: int) -> str:
    """Effective video backend. Replicate has no video API — falls back to xAI."""
    prov = get_grok_imagine_config(user_id)["provider"]
    if prov == "replicate":
        return "xai"
    return prov


async def _download_telegram_photo(photo: types.PhotoSize) -> BytesIO:
    return await _download_telegram_file_id(photo.file_id)


async def _download_telegram_file_id(file_id: str) -> BytesIO:
    file = await bot.get_file(file_id)
    file_bytes = await bot.download_file(file.file_path)
    file_bytes.seek(0)
    image_data = BytesIO(file_bytes.read())
    image_data.name = "image.jpg"
    return image_data


def _image_regenerate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Regenerar", callback_data="regen")],
        ]
    )


def _grok_model_for_config(user_id: int, provider: str, variant: str) -> dict:
    m = dict(MODELS["grok"])
    spec = GROK_IMAGINE_VARIANTS.get(variant, GROK_IMAGINE_VARIANTS[sessions.DEFAULT_GROK_IMAGINE_VARIANT])
    if provider == "replicate":
        model_id = spec["replicate_id"]
    elif provider == "kie":
        model_id = spec["kie_id"]
    else:
        model_id = spec["id"]
    prov_label = _prov_label(provider)
    m["provider"] = provider
    m["id"] = model_id
    m["name"] = f"Grok Imagine ({prov_label} • {spec['label']})"
    m["desc"] = f"xAI Grok Imagine — {prov_label} • {spec['label']}: {spec['desc']}"
    m["imagine_provider"] = provider
    m["imagine_variant"] = variant
    return m


def _model_from_regen(regen: dict) -> dict:
    key = regen.get("model_key", DEFAULT_MODEL)
    if key == "grok":
        prov = regen.get("imagine_provider", sessions.DEFAULT_GROK_IMAGINE_PROVIDER)
        var = regen.get("imagine_variant", sessions.DEFAULT_GROK_IMAGINE_VARIANT)
        return _grok_model_for_config(regen["user_id"], prov, var)
    return MODELS.get(key, MODELS[DEFAULT_MODEL])


def _build_image_regen_context(
    *,
    model: dict,
    user_id: int,
    prompt: str,
    mode: str,
    source_file_id: str | None = None,
    kie_source_ref: dict | None = None,
) -> dict:
    ctx: dict = {
        "mode": mode,
        "model_key": model["key"],
        "user_id": user_id,
        "prompt": prompt,
        "provider": model.get("provider", "?"),
    }
    if model["key"] == "grok":
        cfg = get_grok_imagine_config(user_id)
        ctx["imagine_provider"] = model.get("imagine_provider", cfg["provider"])
        ctx["imagine_variant"] = model.get("imagine_variant", cfg["variant"])
    if source_file_id:
        ctx["source_file_id"] = source_file_id
    if kie_source_ref:
        ctx["kie_source_ref"] = {
            "task_id": kie_source_ref["task_id"],
            "index": kie_source_ref.get("index", 0),
        }
    return ctx


def _resolve_reply_kie_ref(reply_to_message: types.Message | None) -> dict | None:
    """Return Kie task_id ref when replying to a bot-generated Kie image."""
    if reply_to_message is None or not reply_to_message.photo:
        return None
    ref = sessions.get_generation_ref(reply_to_message.chat.id, reply_to_message.message_id)
    if not ref or ref.get("provider") != "kie" or not ref.get("kie_task_id"):
        return None
    return {
        "task_id": ref["kie_task_id"],
        "index": ref.get("kie_index", 0),
    }


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
    if prov not in ("xai", "replicate", "kie"):
        prov = sessions.DEFAULT_GROK_IMAGINE_PROVIDER
    if var not in GROK_IMAGINE_VARIANTS:
        var = sessions.DEFAULT_GROK_IMAGINE_VARIANT
    spec = GROK_IMAGINE_VARIANTS[var]
    if prov == "replicate":
        model_id = spec["replicate_id"]
    elif prov == "kie":
        model_id = spec["kie_id"]
    else:
        model_id = spec["id"]
    return {
        "provider": prov,
        "variant": var,
        "id": model_id,
        "label": spec["label"],
        "desc": spec["desc"],
        "prov_label": _prov_label(prov),
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
    if key == "grok_video":
        m = dict(base)
        cfg = get_grok_imagine_config(user_id)
        video_prov = get_video_provider_for_user(user_id)
        m["provider"] = video_prov
        prov_label = _prov_label(video_prov)
        if cfg["provider"] == "replicate":
            m["name"] = f"Grok Imagine Video ({prov_label}; imágenes: Replicate)"
            m["desc"] = "Generación de video con xAI; imágenes vía Replicate"
        else:
            m["name"] = f"Grok Imagine Video ({prov_label})"
            m["desc"] = f"Generación de video con Grok Imagine — {prov_label}"
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
dp = Dispatcher(storage=MemoryStorage())


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
        if ALLOWED_TELEGRAM_IDS is not None and (user_id is None or not _is_user_allowed(user_id)):
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
    lines.append("Usa /config para cambiar de modelo o ajustar opciones.")

    await message.answer("".join(lines), parse_mode="HTML")


# ---------------------------------------------------------------------------
# Video config helpers (used by config_flow and generation)
# ---------------------------------------------------------------------------
KIE_BASE_VIDEO_ASPECT_RATIOS = ("16:9", "9:16", "1:1", "3:2", "2:3")
KIE_15_VIDEO_ASPECT_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3")


def _kie_aspect_ratios_for_model(video_model: str) -> tuple[str, ...]:
    if video_model == "grok-imagine-video-1.5":
        return KIE_15_VIDEO_ASPECT_RATIOS
    return KIE_BASE_VIDEO_ASPECT_RATIOS


def _maybe_reset_kie_aspect_ratio(
    user_id: int,
    *,
    video_model: str | None = None,
) -> str | None:
    """Reset persisted aspect ratio when invalid for Kie provider/model. Returns new ratio or None."""
    if get_video_provider_for_user(user_id) != "kie":
        return None
    cfg = sessions.get_video_config(user_id)
    model = video_model or cfg["model"]
    allowed = _kie_aspect_ratios_for_model(model)
    if cfg["aspect_ratio"] in allowed:
        return None
    fallback = (
        sessions.DEFAULT_VIDEO_ASPECT_RATIO
        if sessions.DEFAULT_VIDEO_ASPECT_RATIO in allowed
        else allowed[0]
    )
    sessions.set_video_config(user_id, aspect_ratio=fallback)
    return fallback


def _kie_video_status_label(video_model: str, *, image_to_video: bool) -> str:
    """Human-readable model id for status messages, including Kie fallbacks."""
    if video_model == "grok-imagine-video-1.5" and not image_to_video:
        return f"{video_model} (Kie.ai usa modelo base para texto→video)"
    return video_model


def _sanitize_kie_fail_log(fail_msg: str | None, limit: int = 80) -> str:
    if not fail_msg:
        return ""
    text = str(fail_msg).replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _kie_map_duration(duration: int) -> int:
    """kie.ai accepts 6–30 seconds for base text-to-video."""
    return max(6, min(duration, 30))


def _video_duration_display(configured: int, provider: str) -> str:
    if provider == "kie":
        effective = _kie_map_duration(configured)
        if effective != configured:
            return f"{configured}s → {effective}s (Kie.ai)"
        return f"{configured}s"
    return f"{configured}s"


def _video_config_summary(user_id: int) -> str:
    cfg = sessions.get_video_config(user_id)
    prov = get_video_provider_for_user(user_id)
    model_label = VIDEO_MODEL_LABELS.get(cfg["model"], cfg["model"])
    dur = _video_duration_display(cfg["duration"], prov)
    summary = f"<b>{model_label}</b> • {dur} • {cfg['aspect_ratio']} • {cfg['resolution']}"
    if prov == "kie":
        mode_label = VIDEO_MODE_LABELS.get(cfg["mode"], cfg["mode"])
        summary += f" • {mode_label}"
    return summary


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
    await _do_generate_text(callback.message, model, prompt, user_id=callback.from_user.id)


@dp.callback_query(lambda c: c.data == "regen")
async def handle_regenerate_image(callback: types.CallbackQuery):
    if callback.message is None or not callback.message.photo:
        await callback.answer("Mensaje no valido.", show_alert=True)
        return

    ref = sessions.get_generation_ref(callback.message.chat.id, callback.message.message_id)
    regen = ref.get("regen") if ref else None
    if not regen:
        await callback.answer("No se puede regenerar (contexto expirado).", show_alert=True)
        return

    prompt = regen.get("prompt", "").strip()
    prompt_err = _validate_prompt(prompt)
    if prompt_err:
        await callback.answer(prompt_err, show_alert=True)
        return

    model = _model_from_regen(regen)
    mode = regen.get("mode", "text")
    await callback.answer("Regenerando...")

    status_msg = await callback.message.answer(f"Regenerando imagen con {model['name']}...")
    image_data = None
    kie_source_ref = regen.get("kie_source_ref")
    source_file_id = regen.get("source_file_id")

    try:
        if mode == "edit" and not kie_source_ref:
            if source_file_id:
                image_data = await _download_telegram_file_id(source_file_id)
            else:
                await status_msg.edit_text("No se pudo recuperar la imagen original para regenerar.")
                return

        output, err, kie_meta = await generate_image(
            model,
            prompt,
            image_data,
            kie_source_ref=kie_source_ref,
        )
        if err:
            await status_msg.edit_text(err)
            return

        prefix = "Edit" if mode == "edit" else "Prompt"
        await process_image_result(
            output,
            prompt,
            status_msg,
            callback.message,
            prefix,
            download_allowlist=_download_allowlist_for_provider(model.get("provider")),
            kie_meta=kie_meta,
            regen_context=regen,
        )
    except replicate.exceptions.ReplicateError as e:
        backend = _prov_label(model.get("provider", "?"))
        await status_msg.edit_text(f"Error de {backend}: {e}")
    except Exception as e:
        await status_msg.edit_text(f"Error inesperado: {e}")


# ---------------------------------------------------------------------------
# /cambiar_source  (solo faceswap)
# ---------------------------------------------------------------------------
@dp.message(Command("cambiar_source"))
async def cmd_cambiar_source(message: types.Message):
    state = get_user_state(message.from_user.id)
    if state["model"] != "faceswap":
        await message.answer(
            "Este comando solo esta disponible en modo <b>Face Swap</b>.\n"
            "Usa /config para cambiar al modo Face Swap.",
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
            prov_labels = {"xai": "xAI (oficial)", "replicate": "Replicate", "kie": "Kie.ai"}
            prov_label = prov_labels.get(prov, prov)
            lines.append(f"API / Backend: {prov_label} • {var_label}\n")
            lines.append("Listo para generar/editar imagenes.")
        elif model.get("key") == "grok_video":
            uid = message.from_user.id
            video_cfg = sessions.get_video_config(uid)
            video_prov = get_video_provider_for_user(uid)
            prov_labels = {"xai": "xAI (oficial)", "replicate": "Replicate", "kie": "Kie.ai"}
            lines.append(f"API / Backend: {prov_labels.get(video_prov, video_prov)}\n")
            if model.get("imagine_provider") == "replicate":
                lines.append("(Imágenes: Replicate; video vía xAI)\n")
            model_label = VIDEO_MODEL_LABELS.get(video_cfg["model"], video_cfg["model"])
            dur_label = _video_duration_display(video_cfg["duration"], video_prov)
            lines.append(
                f"Video: {model_label}, {dur_label}, "
                f"{video_cfg['aspect_ratio']}, {video_cfg['resolution']}\n"
            )
            lines.append("Listo para generar videos (texto o imagen a video).")
            lines.append("Usa /config (o /video) para configurar modelo, duración, aspecto y resolución.")
        else:
            lines.append("Listo para generar/editar imagenes.")

    await message.answer("\n".join(lines))


# ---------------------------------------------------------------------------
# TEXT messages — route by model
# ---------------------------------------------------------------------------
@dp.message(_is_generation_prompt_message)
async def handle_text(message: types.Message):
    if _is_bot_command_message(message):
        return

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


async def _do_generate_text(
    message: types.Message,
    model: dict,
    prompt: str,
    *,
    user_id: int | None = None,
):
    uid = user_id if user_id is not None else message.from_user.id
    status_msg = await message.answer(f"Generando imagen con {model['name']}...")

    backend = _prov_label(model.get("provider", "?"))
    try:
        output, err, kie_meta = await generate_image(model, prompt)
        if err:
            await status_msg.edit_text(err)
            return
        await process_image_result(
            output,
            prompt,
            status_msg,
            message,
            "Prompt",
            download_allowlist=_download_allowlist_for_provider(model.get("provider")),
            kie_meta=kie_meta,
            regen_context=_build_image_regen_context(
                model=model,
                user_id=uid,
                prompt=prompt,
                mode="text",
            ),
        )
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
    kie_source_ref: dict | None = None,
):
    uid = user_id if user_id is not None else message.from_user.id
    reply_msg = reply_message or message

    try:
        if image_data and kie_source_ref is None:
            size_err = _validate_image_for_i2v(image_data)
            if size_err:
                if status_msg:
                    await status_msg.edit_text(size_err)
                else:
                    await reply_msg.answer(size_err)
                return

        if status_msg is None:
            video_model = sessions.get_video_config(uid)["model"]
            if image_data or kie_source_ref:
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
            kie_source_ref=kie_source_ref,
            status_msg=status_msg,
            user_id=uid,
        )
        if err:
            await status_msg.edit_text(err)
            return
        prefix = "Edit" if image_data or kie_source_ref else "Prompt"
        await process_video_result(
            output,
            prompt,
            status_msg,
            reply_msg,
            prefix,
            download_allowlist=_download_allowlist_for_provider(model.get("provider")),
        )
    except Exception as e:
        print(f"[video] unexpected error user={uid}: {e}")
        if status_msg:
            await status_msg.edit_text("Error inesperado. Intenta de nuevo.")
        else:
            await reply_msg.answer("Error inesperado. Intenta de nuevo.")


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
        backend = _prov_label(model.get("provider", "?"))
        output, err, kie_meta = await generate_image(model, prompt, image_data)
        if err:
            await status_msg.edit_text(err)
            return
        await process_image_result(
            output,
            prompt,
            status_msg,
            message,
            "Edit",
            download_allowlist=_download_allowlist_for_provider(model.get("provider")),
            kie_meta=kie_meta,
            regen_context=_build_image_regen_context(
                model=model,
                user_id=message.from_user.id,
                prompt=prompt,
                mode="edit",
                source_file_id=message.photo[-1].file_id,
            ),
        )
    except replicate.exceptions.ReplicateError as e:
        backend = _prov_label(model.get("provider", "?"))
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
        kie_source_ref = None
        image_data = None
        if model.get("provider") == "kie":
            kie_source_ref = _resolve_reply_kie_ref(message.reply_to_message)
        if kie_source_ref is None:
            image_data = await _download_telegram_photo(message.reply_to_message.photo[-1])

        if model["key"] == "grok_video":
            await _do_generate_video(
                message,
                model,
                prompt,
                image_data,
                user_id=message.from_user.id,
                kie_source_ref=kie_source_ref,
            )
            return

        status_msg = await message.answer(f"Editando imagen con {model['name']}...")
        backend = _prov_label(model.get("provider", "?"))
        output, err, kie_meta = await generate_image(
            model,
            prompt,
            image_data,
            kie_source_ref=kie_source_ref,
        )
        if err:
            await status_msg.edit_text(err)
            return
        source_file_id = None
        if kie_source_ref is None and message.reply_to_message.photo:
            source_file_id = message.reply_to_message.photo[-1].file_id
        await process_image_result(
            output,
            prompt,
            status_msg,
            message,
            "Edit",
            download_allowlist=_download_allowlist_for_provider(model.get("provider")),
            kie_meta=kie_meta,
            regen_context=_build_image_regen_context(
                model=model,
                user_id=message.from_user.id,
                prompt=prompt,
                mode="edit",
                source_file_id=source_file_id,
                kie_source_ref=kie_source_ref,
            ),
        )
    except replicate.exceptions.ReplicateError as e:
        backend = _prov_label(model.get("provider", "?"))
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
async def generate_image(
    model: dict,
    prompt: str,
    image_data: BytesIO | None = None,
    *,
    kie_source_ref: dict | None = None,
) -> tuple[object | None, str | None, dict | None]:
    prov = model.get("provider", "?")
    model_id = model.get("id")
    print(
        f"[generate] key={model.get('key')} provider={prov} id={model_id} "
        f"has_image={image_data is not None} kie_ref={kie_source_ref is not None}"
    )
    if prov == "xai":
        output, err = await _generate_xai(model, prompt, image_data)
        return output, err, None
    if prov == "kie":
        return await _generate_kie(model, prompt, image_data, kie_source_ref=kie_source_ref)
    output, err = await _generate_replicate(model, prompt, image_data)
    return output, err, None


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


KIE_BASE = "https://api.kie.ai"
KIE_UPLOAD_BASE = "https://kieai.redpandaai.co"
KIE_IMAGE_I2I = "grok-imagine/image-to-image"
KIE_VIDEO_T2V = "grok-imagine/text-to-video"
KIE_VIDEO_I2V = "grok-imagine/image-to-video"
# grok-imagine-video-1.5 on Kie.ai is i2v-only (slug verified via API probe).
KIE_VIDEO_15_I2V = "grok-imagine-video-1-5-preview"

_KIE_STATUS_LABELS = {
    "waiting": "en cola",
    "queuing": "en cola",
    "generating": "procesando",
}


def _kie_video_slug(video_model: str, *, image_to_video: bool) -> str:
    """Map bot video model selection to Kie.ai model slug."""
    if video_model == "grok-imagine-video-1.5":
        if image_to_video:
            return KIE_VIDEO_15_I2V
        # Kie 1.5 has no t2v slug; fall back to base text-to-video.
        return KIE_VIDEO_T2V
    return KIE_VIDEO_I2V if image_to_video else KIE_VIDEO_T2V


def _kie_enable_pro(model: dict) -> bool:
    variant = model.get("imagine_variant", DEFAULT_GROK_IMAGINE_VARIANT)
    return variant == "quality"


def _kie_headers() -> dict:
    return {
        "Authorization": f"Bearer {KIE_API_KEY}",
        "Content-Type": "application/json",
    }


def _log_kie_error(status: int, task_id: str | None = None) -> None:
    suffix = f" task_id={task_id}" if task_id else ""
    print(f"[kie error] status={status}{suffix}")


async def _kie_upload_image(session: aiohttp.ClientSession, image_data: BytesIO) -> tuple[str | None, str | None]:
    """Upload image to kie.ai and return a public URL for image_urls fields."""
    mime, ext = _detect_image_mime(image_data)
    data_uri = _image_to_data_uri(image_data, mime=mime)
    body = {
        "base64Data": data_uri,
        "uploadPath": "grok-bot",
        "fileName": f"upload-{int(time.time())}.{ext}",
    }
    async with session.post(
        f"{KIE_UPLOAD_BASE}/api/file-base64-upload",
        headers=_kie_headers(),
        json=body,
    ) as resp:
        if resp.status != 200:
            await resp.text()
            _log_kie_error(resp.status)
            return None, _kie_user_error("subida de imagen")
        data = await resp.json()
    if data.get("success") is False or data.get("code") not in (None, 200):
        _log_kie_error(data.get("code", 0))
        return None, _kie_user_error("subida de imagen")
    payload = data.get("data") or {}
    file_url = payload.get("fileUrl") or payload.get("downloadUrl")
    if not file_url:
        return None, "No se pudo subir la imagen. Intenta de nuevo."
    if not _is_allowed_kie_asset_url(file_url):
        print(f"[kie upload] blocked fileUrl host: {urllib.parse.urlparse(file_url).hostname}")
        return None, _kie_user_error("subida de imagen")
    return file_url, None


async def _kie_create_task(
    session: aiohttp.ClientSession,
    model_slug: str,
    input_data: dict,
) -> tuple[str | None, str | None]:
    body = {"model": model_slug, "input": input_data}
    async with session.post(
        f"{KIE_BASE}/api/v1/jobs/createTask",
        headers=_kie_headers(),
        json=body,
    ) as resp:
        if resp.status != 200:
            await resp.text()
            _log_kie_error(resp.status)
            return None, _kie_user_error("inicio de tarea")
        data = await resp.json()
    if data.get("code") != 200:
        _log_kie_error(data.get("code", 0))
        return None, _kie_user_error("inicio de tarea")
    task_id = (data.get("data") or {}).get("taskId")
    if not task_id:
        return None, "No se pudo iniciar la generación. Intenta de nuevo."
    return task_id, None


async def _kie_poll_task(
    session: aiohttp.ClientSession,
    task_id: str,
    *,
    status_msg: types.Message | None = None,
    status_label: str = "",
    prompt: str = "",
) -> tuple[str | None, str | None]:
    """Poll kie.ai task until success/fail or timeout. Returns first result URL."""
    started = time.monotonic()
    last_status = None
    last_elapsed_shown = -1

    while time.monotonic() - started < VIDEO_MAX_POLL_SEC:
        poll_data, poll_err, transient = await _kie_poll_once(session, task_id)
        if poll_err:
            if transient:
                print(f"[kie poll] transient error, retrying task_id={task_id}")
                await asyncio.sleep(VIDEO_POLL_INTERVAL_SEC)
                continue
            return None, poll_err

        state = (poll_data.get("data") or {}).get("state", "unknown")

        if state == "success":
            result_json_raw = (poll_data.get("data") or {}).get("resultJson")
            if not result_json_raw:
                return None, "No se recibió resultado. Intenta de nuevo."
            try:
                result_json = json.loads(result_json_raw) if isinstance(result_json_raw, str) else result_json_raw
            except (json.JSONDecodeError, TypeError):
                return None, "No se pudo interpretar el resultado. Intenta de nuevo."
            urls = result_json.get("resultUrls") or []
            if not urls:
                return None, "No se recibió URL de resultado. Intenta de nuevo."
            result_url = urls[0]
            if not _is_allowed_kie_asset_url(result_url):
                print(f"[kie poll] blocked result host: {urllib.parse.urlparse(result_url).hostname}")
                return None, _kie_user_error("descarga de resultado")
            return result_url, None

        if state == "fail":
            fail_data = poll_data.get("data") or {}
            fail_code = fail_data.get("failCode")
            fail_msg = _sanitize_kie_fail_log(fail_data.get("failMsg"))
            print(
                f"[kie poll] state=fail task_id={task_id} "
                f"failCode={fail_code} failMsg={fail_msg}"
            )
            return None, _kie_user_error("generación")

        if status_msg and status_label:
            elapsed = int(time.monotonic() - started)
            if state in _KIE_STATUS_LABELS and state != last_status:
                label = _KIE_STATUS_LABELS[state]
                await safe_edit_text(
                    status_msg,
                    _video_status_message(status_label, label, prompt),
                    parse_mode="HTML",
                )
                last_status = state
                last_elapsed_shown = elapsed
            elif state not in _KIE_STATUS_LABELS:
                print(f"[kie poll] unknown state: {state} task_id={task_id}")
                if elapsed - last_elapsed_shown >= 30:
                    await safe_edit_text(
                        status_msg,
                        _video_status_message(status_label, f"({elapsed}s transcurridos)", prompt),
                        parse_mode="HTML",
                    )
                    last_elapsed_shown = elapsed

        await asyncio.sleep(VIDEO_POLL_INTERVAL_SEC)

    return None, "Tiempo de espera agotado (10 min). Intenta de nuevo."


def _kie_poll_error_is_transient(http_status: int, api_code: int | None = None) -> bool:
    if http_status in (404, 422, 429):
        return True
    if api_code in (422, 429):
        return True
    return http_status >= 500


async def _kie_poll_once(
    session: aiohttp.ClientSession,
    task_id: str,
) -> tuple[dict | None, str | None, bool]:
    """Poll once. Third value is True when the outer loop should retry until timeout."""
    url = f"{KIE_BASE}/api/v1/jobs/recordInfo?taskId={urllib.parse.quote(task_id)}"
    for attempt in range(POLL_MAX_RETRIES + 1):
        try:
            async with session.get(url, headers=_kie_headers()) as poll_resp:
                if poll_resp.status == 429:
                    if attempt < POLL_MAX_RETRIES:
                        await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                        continue
                    return None, _kie_user_error("consulta de tarea"), True
                if poll_resp.status >= 500:
                    if attempt < POLL_MAX_RETRIES:
                        await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                        continue
                    await poll_resp.text()
                    _log_kie_error(poll_resp.status, task_id)
                    return None, _kie_user_error("consulta de tarea"), True
                if poll_resp.status in (404, 422):
                    await poll_resp.text()
                    _log_kie_error(poll_resp.status, task_id)
                    return None, _kie_user_error("consulta de tarea"), True
                if poll_resp.status != 200:
                    await poll_resp.text()
                    _log_kie_error(poll_resp.status, task_id)
                    return None, _kie_user_error("consulta de tarea"), False
                data = await poll_resp.json()
                api_code = data.get("code")
                if api_code != 200:
                    _log_kie_error(api_code or 0, task_id)
                    transient = _kie_poll_error_is_transient(200, api_code)
                    return None, _kie_user_error("consulta de tarea"), transient
                return data, None, False
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            print(f"[kie poll] transient error: {exc}")
            if attempt < POLL_MAX_RETRIES:
                await asyncio.sleep(POLL_RETRY_BACKOFF_SEC[attempt])
                continue
            return None, _kie_user_error("consulta de tarea"), True
    return None, _kie_user_error("consulta de tarea"), True


async def _kie_get_result_url_at_index(
    session: aiohttp.ClientSession,
    task_id: str,
    index: int = 0,
) -> tuple[str | None, str | None]:
    """Fetch a result URL from a completed Kie task (for i2i from bot-generated images)."""
    poll_data, poll_err, _ = await _kie_poll_once(session, task_id)
    if poll_err:
        return None, poll_err
    state = (poll_data.get("data") or {}).get("state")
    if state != "success":
        return None, "La imagen de referencia no está disponible. Intenta de nuevo."
    result_json_raw = (poll_data.get("data") or {}).get("resultJson")
    if not result_json_raw:
        return None, "No se recibió resultado de la imagen de referencia."
    try:
        result_json = json.loads(result_json_raw) if isinstance(result_json_raw, str) else result_json_raw
    except (json.JSONDecodeError, TypeError):
        return None, "No se pudo interpretar la imagen de referencia."
    urls = result_json.get("resultUrls") or []
    if not urls:
        return None, "No se encontró URL de la imagen de referencia."
    idx = max(0, min(int(index), len(urls) - 1))
    result_url = urls[idx]
    if not _is_allowed_kie_asset_url(result_url):
        print(f"[kie ref] blocked result host: {urllib.parse.urlparse(result_url).hostname}")
        return None, _kie_user_error("descarga de resultado")
    return result_url, None


async def _generate_kie(
    model: dict,
    prompt: str,
    image_data: BytesIO | None = None,
    *,
    kie_source_ref: dict | None = None,
) -> tuple[object | None, str | None, dict | None]:
    if not KIE_API_KEY:
        return None, _KIE_NOT_CONFIGURED_MSG, None

    enable_pro = _kie_enable_pro(model)
    source_index = 0

    async with aiohttp.ClientSession(timeout=KIE_REQUEST_TIMEOUT) as session:
        if kie_source_ref:
            source_index = kie_source_ref.get("index", 0)
            image_url, ref_err = await _kie_get_result_url_at_index(
                session,
                kie_source_ref["task_id"],
                source_index,
            )
            if ref_err:
                return None, ref_err, None
            input_data: dict = {
                "image_urls": [image_url],
                "prompt": prompt,
            }
            model_slug = KIE_IMAGE_I2I
        elif image_data:
            size_err = _validate_image_for_i2v(image_data)
            if size_err:
                return None, size_err, None
            image_url, upload_err = await _kie_upload_image(session, image_data)
            if upload_err:
                return None, upload_err, None
            input_data = {
                "image_urls": [image_url],
                "prompt": prompt,
            }
            model_slug = KIE_IMAGE_I2I
        else:
            input_data = {
                "prompt": prompt,
                "aspect_ratio": sessions.DEFAULT_VIDEO_ASPECT_RATIO,
                "enable_pro": enable_pro,
            }
            model_slug = model["id"]

        task_id, create_err = await _kie_create_task(session, model_slug, input_data)
        if create_err:
            return None, create_err, None

        result_url, poll_err = await _kie_poll_task(session, task_id)
        if poll_err:
            return None, poll_err, None
        kie_meta = {"task_id": task_id, "index": 0, "provider": "kie"}
        return [result_url], None, kie_meta


async def process_image_result(
    output,
    prompt: str,
    status_msg: types.Message,
    message: types.Message,
    prefix: str,
    *,
    download_allowlist: str | None = None,
    kie_meta: dict | None = None,
    regen_context: dict | None = None,
):
    if output is None:
        await status_msg.edit_text("Error: el modelo no devolvio nada. Intenta con otro prompt.")
        return

    image_url = output[0] if isinstance(output, list) else output
    if hasattr(image_url, "url"):
        image_url = image_url.url

    image_bytes, dl_err = await download_url(str(image_url), download_allowlist=download_allowlist)
    if dl_err:
        await status_msg.edit_text(dl_err)
        return
    photo = BufferedInputFile(image_bytes, filename="generated.png")
    sent_msg = await message.answer_photo(
        photo,
        caption=f"<b>{prefix}:</b> {_escape_prompt(prompt)}",
        parse_mode="HTML",
        reply_markup=_image_regenerate_keyboard(),
    )
    provider = (
        kie_meta.get("provider")
        if kie_meta and kie_meta.get("task_id")
        else (regen_context or {}).get("provider", "unknown")
    )
    sessions.save_generation_ref(
        message.chat.id,
        sent_msg.message_id,
        kie_task_id=kie_meta.get("task_id") if kie_meta else None,
        kie_index=kie_meta.get("index", 0) if kie_meta else 0,
        provider=provider,
        kind="image",
        prompt=prompt,
        regen=regen_context,
    )
    await status_msg.delete()


async def download_url(
    url: str,
    *,
    max_bytes: int = DOWNLOAD_MAX_BYTES,
    enforce_host_allowlist: bool = False,
    download_allowlist: str | None = None,
) -> tuple[bytes | None, str | None]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return None, "No se pudo descargar el archivo (URL no permitida)."

    allowlist = download_allowlist
    if allowlist is None and enforce_host_allowlist:
        allowlist = "xai"

    host = (parsed.hostname or "").lower()
    if allowlist and not _is_host_allowed_for_download(host, allowlist):
        print(f"[download_url] blocked host: {host}")
        return None, "No se pudo descargar el archivo (origen no permitido)."

    timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_SEC)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    return None, "No se pudo descargar el archivo. Intenta de nuevo."
                if allowlist:
                    final_host = (urllib.parse.urlparse(str(resp.url)).hostname or "").lower()
                    if not _is_host_allowed_for_download(final_host, allowlist):
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
    kie_source_ref: dict | None = None,
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
        f"has_image={image_data is not None} kie_ref={kie_source_ref is not None}"
    )
    if prov == "xai":
        return await _generate_xai_video(
            model,
            prompt,
            image_data,
            status_msg=status_msg,
            user_id=user_id,
        )
    if prov == "kie":
        return await _generate_kie_video(
            model,
            prompt,
            image_data,
            kie_source_ref=kie_source_ref,
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


async def _generate_kie_video(
    model: dict,
    prompt: str,
    image_data: BytesIO | None = None,
    *,
    kie_source_ref: dict | None = None,
    status_msg: types.Message | None = None,
    user_id: int | None = None,
) -> tuple[str | None, str | None]:
    if not KIE_API_KEY:
        return None, _KIE_NOT_CONFIGURED_MSG

    video_cfg = sessions.get_video_config(user_id) if user_id is not None else {
        "duration": sessions.DEFAULT_VIDEO_DURATION,
        "aspect_ratio": sessions.DEFAULT_VIDEO_ASPECT_RATIO,
        "resolution": sessions.DEFAULT_VIDEO_RESOLUTION,
        "model": sessions.DEFAULT_VIDEO_MODEL,
        "mode": sessions.DEFAULT_VIDEO_MODE,
    }

    allowed_aspects = _kie_aspect_ratios_for_model(video_cfg["model"])
    if video_cfg["aspect_ratio"] not in allowed_aspects:
        supported = ", ".join(allowed_aspects)
        return None, f"Relación de aspecto no compatible con Kie.ai. Usa: {supported}"

    has_image = image_data is not None or kie_source_ref is not None
    status_label = _kie_video_status_label(video_cfg["model"], image_to_video=has_image)
    kie_duration = _kie_map_duration(video_cfg["duration"])
    configured_mode = video_cfg.get("mode", sessions.DEFAULT_VIDEO_MODE)

    input_data: dict = {
        "prompt": prompt,
        "aspect_ratio": video_cfg["aspect_ratio"],
        "duration": kie_duration,
        "resolution": video_cfg["resolution"],
    }

    async with aiohttp.ClientSession(timeout=KIE_REQUEST_TIMEOUT) as session:
        if kie_source_ref:
            input_data["task_id"] = kie_source_ref["task_id"]
            input_data["index"] = kie_source_ref.get("index", 0)
        elif has_image:
            size_err = _validate_image_for_i2v(image_data)
            if size_err:
                return None, size_err
            image_url, upload_err = await _kie_upload_image(session, image_data)
            if upload_err:
                return None, upload_err
            input_data["image_urls"] = [image_url]

        model_slug = _kie_video_slug(video_cfg["model"], image_to_video=has_image)
        if model_slug != KIE_VIDEO_15_I2V:
            if kie_source_ref:
                input_data["mode"] = configured_mode
            else:
                # Spicy only works with Kie-generated images (task_id path).
                input_data["mode"] = "normal" if configured_mode == "spicy" else configured_mode

        task_id, create_err = await _kie_create_task(session, model_slug, input_data)
        if create_err:
            return None, create_err

        return await _kie_poll_task(
            session,
            task_id,
            status_msg=status_msg,
            status_label=status_label,
            prompt=prompt,
        )


async def process_video_result(
    video_url: str,
    prompt: str,
    status_msg: types.Message,
    message: types.Message,
    prefix: str,
    *,
    enforce_host_allowlist: bool = True,
    download_allowlist: str | None = None,
):
    if not video_url:
        await status_msg.edit_text("Error: el modelo no devolvió URL de video. Intenta con otro prompt.")
        return

    allowlist = download_allowlist
    if allowlist is None and enforce_host_allowlist:
        allowlist = "xai"

    video_bytes, dl_err = await download_url(
        str(video_url),
        max_bytes=DOWNLOAD_MAX_BYTES,
        download_allowlist=allowlist,
    )
    if dl_err:
        await status_msg.edit_text(dl_err)
        return

    safe_prompt = _escape_prompt(prompt)
    if len(video_bytes) > TELEGRAM_MAX_VIDEO_BYTES:
        await status_msg.edit_text(
            f"El video es demasiado grande para Telegram ({len(video_bytes) // 1024 // 1024} MB).\n"
            f"Descárgalo aquí:\n{video_url}{_SENSITIVE_DOWNLOAD_WARNING}"
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
            f"Descárgalo aquí:\n{video_url}{_SENSITIVE_DOWNLOAD_WARNING}"
        )


# ---------------------------------------------------------------------------
# Unified /config FSM flow
# ---------------------------------------------------------------------------
import config_flow

_CONFIG_DEPS = {
        "MODELS": MODELS,
        "get_user_state": get_user_state,
        "get_grok_imagine_config": get_grok_imagine_config,
        "set_model": sessions.set_model,
        "set_grok_imagine_config": sessions.set_grok_imagine_config,
        "get_video_config": sessions.get_video_config,
        "set_video_config": sessions.set_video_config,
        "safe_edit_text": safe_edit_text,
        "GROK_IMAGINE_VARIANTS": GROK_IMAGINE_VARIANTS,
        "get_video_provider_for_user": get_video_provider_for_user,
        "_maybe_reset_kie_aspect_ratio": _maybe_reset_kie_aspect_ratio,
        "_kie_aspect_ratios_for_model": _kie_aspect_ratios_for_model,
        "_video_config_summary": _video_config_summary,
        "_video_duration_display": _video_duration_display,
        "VIDEO_MODEL_LABELS": VIDEO_MODEL_LABELS,
        "VIDEO_MODE_LABELS": VIDEO_MODE_LABELS,
        "_prov_label": _prov_label,
        "kie_configured": bool(KIE_API_KEY),
        "_KIE_NOT_CONFIGURED_MSG": _KIE_NOT_CONFIGURED_MSG,
        "_KIE_QUALITY_NOTE": _KIE_QUALITY_NOTE,
        "_KIE_PRIVACY_NOTICE": _KIE_PRIVACY_NOTICE,
}

config_flow.register_config_handlers(dp, _CONFIG_DEPS)

# Re-exports for tests (unified /config flow)
cmd_config = config_flow.cmd_config
cmd_model = config_flow.cmd_model
cmd_imaginess = config_flow.cmd_imaginess
cmd_video = config_flow.cmd_video
handle_cfg_model = config_flow.handle_cfg_model
handle_cfg_provider = config_flow.handle_cfg_provider
handle_cfg_variant = config_flow.handle_cfg_variant
handle_cfg_video = config_flow.handle_cfg_video
handle_cfg_back_model = config_flow.handle_cfg_back_model
handle_cfg_back_provider = config_flow.handle_cfg_back_provider
handle_cfg_close = config_flow.handle_cfg_close
def config_model_keyboard(user_id: int):
    return config_flow.config_model_keyboard(_CONFIG_DEPS, user_id)


def config_provider_keyboard(user_id: int):
    return config_flow.config_provider_keyboard(_CONFIG_DEPS, user_id)


def config_variant_keyboard(user_id: int):
    return config_flow.config_variant_keyboard(_CONFIG_DEPS, user_id)


def config_video_keyboard(current: dict, user_id: int):
    return config_flow.config_video_keyboard(_CONFIG_DEPS, current, user_id)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
