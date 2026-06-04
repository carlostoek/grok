#!/usr/bin/env python3
"""Download photos from Telegram using aiogram."""

import uuid
from pathlib import Path

from aiogram import Bot


async def download_telegram_photo(bot: Bot, file_id: str, temp_dir: Path) -> Path:
    temp_dir.mkdir(parents=True, exist_ok=True)
    file = await bot.get_file(file_id)
    file_bytes = await bot.download_file(file.file_path)
    file_path = temp_dir / f"{uuid.uuid4().hex}.jpg"
    file_path.write_bytes(file_bytes.read())
    return file_path


def cleanup_temp_files(paths: list) -> None:
    for p in paths:
        if p and p.exists():
            p.unlink()
