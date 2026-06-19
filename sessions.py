#!/usr/bin/env python3
"""User session state management with JSON persistence."""

import json
import time
from pathlib import Path

SESSIONS_FILE = Path(__file__).parent / "sessions.json"


class FsState:
    IDLE = "IDLE"
    AWAITING_SOURCE = "AWAITING_SOURCE"


# Defaults for top-level model selection and the granular Grok Imagine config.
# These power the independent persistent Imagine settings (provider + variant).
DEFAULT_MODEL = "grok"
DEFAULT_GROK_IMAGINE_PROVIDER = "xai"
DEFAULT_GROK_IMAGINE_VARIANT = "quality"
DEFAULT_VIDEO_DURATION = 5
DEFAULT_VIDEO_ASPECT_RATIO = "16:9"
DEFAULT_VIDEO_RESOLUTION = "720p"
DEFAULT_VIDEO_MODEL = "grok-imagine-video"
VALID_VIDEO_DURATIONS = (5, 10, 15)
VALID_VIDEO_ASPECT_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3")
VALID_VIDEO_RESOLUTIONS = ("480p", "720p")
VALID_VIDEO_MODELS = ("grok-imagine-video", "grok-imagine-video-1.5")
VIDEO_HOURLY_WINDOW_SEC = 3600


def _default_session_record(**overrides) -> dict:
    """Full session record with all persisted fields and sensible defaults."""
    rec = {
        "source_path": None,
        "state": FsState.IDLE,
        "model": DEFAULT_MODEL,
        "grok_imagine_provider": DEFAULT_GROK_IMAGINE_PROVIDER,
        "grok_imagine_variant": DEFAULT_GROK_IMAGINE_VARIANT,
        "video_duration": DEFAULT_VIDEO_DURATION,
        "video_aspect_ratio": DEFAULT_VIDEO_ASPECT_RATIO,
        "video_resolution": DEFAULT_VIDEO_RESOLUTION,
        "video_model": DEFAULT_VIDEO_MODEL,
        "video_hourly_timestamps": [],
    }
    rec.update(overrides)
    return rec


def _load() -> dict:
    if SESSIONS_FILE.exists():
        with open(SESSIONS_FILE, "r") as f:
            return json.load(f)
    return {}


def _save(data: dict) -> None:
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SESSIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _ensure_full(rec: dict) -> bool:
    """Ensure record has all keys (top model + grok imagine granular config).
    Returns True if migration or defaults were applied (caller may save).
    Handles legacy 'grok_provider' -> 'grok_imagine_provider'.
    """
    changed = False
    if "model" not in rec:
        rec["model"] = DEFAULT_MODEL
        changed = True
    if "grok_imagine_provider" not in rec:
        rec["grok_imagine_provider"] = DEFAULT_GROK_IMAGINE_PROVIDER
        changed = True
    if "grok_imagine_variant" not in rec:
        rec["grok_imagine_variant"] = DEFAULT_GROK_IMAGINE_VARIANT
        changed = True
    if "grok_provider" in rec:
        # legacy migration from previous single-provider flag
        rec["grok_imagine_provider"] = rec.pop("grok_provider")
        if "grok_imagine_variant" not in rec:
            rec["grok_imagine_variant"] = DEFAULT_GROK_IMAGINE_VARIANT
        changed = True
    if "video_duration" not in rec:
        rec["video_duration"] = DEFAULT_VIDEO_DURATION
        changed = True
    if "video_aspect_ratio" not in rec:
        rec["video_aspect_ratio"] = DEFAULT_VIDEO_ASPECT_RATIO
        changed = True
    if "video_resolution" not in rec:
        rec["video_resolution"] = DEFAULT_VIDEO_RESOLUTION
        changed = True
    if "video_hourly_timestamps" not in rec:
        rec["video_hourly_timestamps"] = []
        changed = True
    if "video_model" not in rec:
        rec["video_model"] = DEFAULT_VIDEO_MODEL
        changed = True
    return changed


def _get_or_create_full(user_id: int) -> dict:
    """Internal: load/create a full record with all persisted fields."""
    uid = str(user_id)
    sessions = _load()
    if uid not in sessions:
        rec = _default_session_record()
        sessions[uid] = rec
        _save(sessions)
        return rec
    rec = sessions[uid]
    if _ensure_full(rec):
        _save(sessions)
    return rec


def get_session(user_id: int) -> dict:
    """Return the (full) session record for the user.
    Existing FaceSwap callers continue to work (they only read source_path/state).
    New fields (model, grok_imagine_*) are always present thanks to _ensure_full.
    """
    return _get_or_create_full(user_id)


def set_state(user_id: int, state: str) -> None:
    """Update only the fs state, preserving all other persisted fields (incl. model + imagine config)."""
    uid = str(user_id)
    sessions = _load()
    if uid not in sessions:
        sessions[uid] = _default_session_record(state=state)
    else:
        rec = sessions[uid]
        _ensure_full(rec)
        rec["state"] = state
    _save(sessions)


def set_source(user_id: int, source_path: str) -> None:
    """Set source (and reset fs state). Preserves model and grok imagine granular config."""
    uid = str(user_id)
    sessions = _load()
    current = sessions.get(uid, {})
    # Preserve existing (or default) model + imagine config across the overwrite
    model = current.get("model", DEFAULT_MODEL)
    prov = current.get("grok_imagine_provider", DEFAULT_GROK_IMAGINE_PROVIDER)
    var = current.get("grok_imagine_variant", DEFAULT_GROK_IMAGINE_VARIANT)
    if "grok_provider" in current and "grok_imagine_provider" not in current:
        prov = current["grok_provider"]
    sessions[uid] = {
        "source_path": source_path,
        "state": FsState.IDLE,
        "model": model,
        "grok_imagine_provider": prov,
        "grok_imagine_variant": var,
        "video_duration": current.get("video_duration", DEFAULT_VIDEO_DURATION),
        "video_aspect_ratio": current.get("video_aspect_ratio", DEFAULT_VIDEO_ASPECT_RATIO),
        "video_resolution": current.get("video_resolution", DEFAULT_VIDEO_RESOLUTION),
        "video_model": current.get("video_model", DEFAULT_VIDEO_MODEL),
        "video_hourly_timestamps": current.get("video_hourly_timestamps", []),
    }
    _save(sessions)


# --- New API for top-level model and the independent persistent Grok Imagine config ---

def set_model(user_id: int, model_key: str) -> None:
    """Persist the top-level model choice ("grok", "seedream", "faceswap", "grok_video")."""
    uid = str(user_id)
    sessions = _load()
    if uid not in sessions:
        sessions[uid] = _default_session_record(model=model_key)
    else:
        rec = sessions[uid]
        _ensure_full(rec)
        rec["model"] = model_key
    _save(sessions)


def get_grok_imagine_config(user_id: int) -> dict:
    """Return the current persistent granular config for Grok Imagine.
    Always returns a dict with 'provider' and 'variant' (with safe defaults).
    """
    rec = _get_or_create_full(user_id)
    return {
        "provider": rec.get("grok_imagine_provider", DEFAULT_GROK_IMAGINE_PROVIDER),
        "variant": rec.get("grok_imagine_variant", DEFAULT_GROK_IMAGINE_VARIANT),
    }


def _prune_hourly_timestamps(timestamps: list, now: float | None = None) -> list[float]:
    now = now if now is not None else time.time()
    return [float(t) for t in timestamps if now - float(t) < VIDEO_HOURLY_WINDOW_SEC]


def get_video_config(user_id: int) -> dict:
    """Return persisted video generation defaults, validated against xAI constraints."""
    rec = _get_or_create_full(user_id)
    try:
        duration = int(rec.get("video_duration", DEFAULT_VIDEO_DURATION))
    except (TypeError, ValueError):
        duration = DEFAULT_VIDEO_DURATION
    if duration not in VALID_VIDEO_DURATIONS:
        duration = DEFAULT_VIDEO_DURATION

    aspect_ratio = rec.get("video_aspect_ratio", DEFAULT_VIDEO_ASPECT_RATIO)
    if aspect_ratio not in VALID_VIDEO_ASPECT_RATIOS:
        aspect_ratio = DEFAULT_VIDEO_ASPECT_RATIO

    resolution = rec.get("video_resolution", DEFAULT_VIDEO_RESOLUTION)
    if resolution not in VALID_VIDEO_RESOLUTIONS:
        resolution = DEFAULT_VIDEO_RESOLUTION

    video_model = rec.get("video_model", DEFAULT_VIDEO_MODEL)
    if video_model not in VALID_VIDEO_MODELS:
        video_model = DEFAULT_VIDEO_MODEL

    return {
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "model": video_model,
    }


def count_video_hourly_usage(user_id: int, *, now: float | None = None) -> int:
    """Return recent hourly video usage count, pruning expired timestamps."""
    now = now if now is not None else time.time()
    uid = str(user_id)
    sessions_data = _load()
    rec = sessions_data.get(uid)
    if rec is None:
        rec = _get_or_create_full(user_id)
        return 0
    _ensure_full(rec)
    pruned = _prune_hourly_timestamps(rec.get("video_hourly_timestamps", []), now)
    if pruned != rec.get("video_hourly_timestamps", []):
        rec["video_hourly_timestamps"] = pruned
        sessions_data[uid] = rec
        _save(sessions_data)
    return len(pruned)


def count_global_video_hourly_usage(*, now: float | None = None) -> int:
    """Return total recent hourly video usage across all users."""
    now = now if now is not None else time.time()
    sessions_data = _load()
    total = 0
    for rec in sessions_data.values():
        if not isinstance(rec, dict):
            continue
        total += len(_prune_hourly_timestamps(rec.get("video_hourly_timestamps", []), now))
    return total


def record_video_hourly_usage(user_id: int, *, now: float | None = None) -> None:
    """Persist video API acceptance (POST request_id) in the user's hourly quota."""
    now = now if now is not None else time.time()
    uid = str(user_id)
    sessions_data = _load()
    if uid not in sessions_data:
        sessions_data[uid] = _default_session_record()
    rec = sessions_data[uid]
    _ensure_full(rec)
    pruned = _prune_hourly_timestamps(rec.get("video_hourly_timestamps", []), now)
    pruned.append(now)
    rec["video_hourly_timestamps"] = pruned
    sessions_data[uid] = rec
    _save(sessions_data)


def set_video_config(
    user_id: int,
    *,
    duration: int | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    model: str | None = None,
) -> None:
    """Persist video generation settings (duration, aspect ratio, resolution, model)."""
    uid = str(user_id)
    sessions_data = _load()
    if uid not in sessions_data:
        sessions_data[uid] = _default_session_record()
    rec = sessions_data[uid]
    _ensure_full(rec)
    if duration is not None:
        if duration in VALID_VIDEO_DURATIONS:
            rec["video_duration"] = duration
    if aspect_ratio is not None:
        if aspect_ratio in VALID_VIDEO_ASPECT_RATIOS:
            rec["video_aspect_ratio"] = aspect_ratio
    if resolution is not None:
        if resolution in VALID_VIDEO_RESOLUTIONS:
            rec["video_resolution"] = resolution
    if model is not None:
        if model in VALID_VIDEO_MODELS:
            rec["video_model"] = model
    sessions_data[uid] = rec
    _save(sessions_data)


def set_grok_imagine_config(user_id: int, provider: str, variant: str) -> None:
    """Persist a change to the independent Grok Imagine detailed settings.
    This is the main entry point for the separate /imagine configuration flow.
    """
    uid = str(user_id)
    sessions = _load()
    if uid not in sessions:
        sessions[uid] = _default_session_record(
            grok_imagine_provider=provider,
            grok_imagine_variant=variant,
        )
    else:
        rec = sessions[uid]
        _ensure_full(rec)
        rec["grok_imagine_provider"] = provider
        rec["grok_imagine_variant"] = variant
    _save(sessions)
