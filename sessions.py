#!/usr/bin/env python3
"""User session state management with JSON persistence."""

import json
from pathlib import Path

SESSIONS_FILE = Path(__file__).parent / "sessions.json"


class FsState:
    IDLE = "IDLE"
    AWAITING_SOURCE = "AWAITING_SOURCE"


def _load() -> dict:
    if SESSIONS_FILE.exists():
        with open(SESSIONS_FILE, "r") as f:
            return json.load(f)
    return {}


def _save(data: dict) -> None:
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SESSIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_session(user_id: int) -> dict:
    uid = str(user_id)
    sessions = _load()
    if uid not in sessions:
        sessions[uid] = {"source_path": None, "state": FsState.IDLE}
        _save(sessions)
    return sessions[uid]


def set_state(user_id: int, state: str) -> None:
    uid = str(user_id)
    sessions = _load()
    if uid not in sessions:
        sessions[uid] = {"source_path": None, "state": FsState.IDLE}
    else:
        sessions[uid]["state"] = state
    _save(sessions)


def set_source(user_id: int, source_path: str) -> None:
    uid = str(user_id)
    sessions = _load()
    sessions[uid] = {"source_path": source_path, "state": FsState.IDLE}
    _save(sessions)
