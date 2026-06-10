#!/usr/bin/env python3
"""User session state management with JSON persistence."""

import json
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
    return changed


def _get_or_create_full(user_id: int) -> dict:
    """Internal: load/create a full record with all persisted fields."""
    uid = str(user_id)
    sessions = _load()
    if uid not in sessions:
        rec = {
            "source_path": None,
            "state": FsState.IDLE,
            "model": DEFAULT_MODEL,
            "grok_imagine_provider": DEFAULT_GROK_IMAGINE_PROVIDER,
            "grok_imagine_variant": DEFAULT_GROK_IMAGINE_VARIANT,
        }
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
        sessions[uid] = {
            "source_path": None,
            "state": state,
            "model": DEFAULT_MODEL,
            "grok_imagine_provider": DEFAULT_GROK_IMAGINE_PROVIDER,
            "grok_imagine_variant": DEFAULT_GROK_IMAGINE_VARIANT,
        }
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
    }
    _save(sessions)


# --- New API for top-level model and the independent persistent Grok Imagine config ---

def set_model(user_id: int, model_key: str) -> None:
    """Persist the top-level model choice ("grok", "seedream", "faceswap")."""
    uid = str(user_id)
    sessions = _load()
    if uid not in sessions:
        sessions[uid] = {
            "source_path": None,
            "state": FsState.IDLE,
            "model": model_key,
            "grok_imagine_provider": DEFAULT_GROK_IMAGINE_PROVIDER,
            "grok_imagine_variant": DEFAULT_GROK_IMAGINE_VARIANT,
        }
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


def set_grok_imagine_config(user_id: int, provider: str, variant: str) -> None:
    """Persist a change to the independent Grok Imagine detailed settings.
    This is the main entry point for the separate /imagine configuration flow.
    """
    uid = str(user_id)
    sessions = _load()
    if uid not in sessions:
        sessions[uid] = {
            "source_path": None,
            "state": FsState.IDLE,
            "model": DEFAULT_MODEL,
            "grok_imagine_provider": provider,
            "grok_imagine_variant": variant,
        }
    else:
        rec = sessions[uid]
        _ensure_full(rec)
        rec["grok_imagine_provider"] = provider
        rec["grok_imagine_variant"] = variant
    _save(sessions)
