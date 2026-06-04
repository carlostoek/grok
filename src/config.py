#!/usr/bin/env python3
"""Configuration management for face swap system."""

import json
import os
from pathlib import Path
from typing import Optional

CONFIG_FILE = Path(__file__).parent / ".config" / "settings.json"


def load_config() -> dict:
    """Load configuration from file."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {
        "source": "",
        "input_dir": "input",
        "output_dir": "output",
        "api_token": "",
        "model": "ddvinh1/inswapper:25bdae46f2713138640b6e8c04dc4ca18625ce95b1863936b053eee42d9ba6db"
    }


def save_config(config: dict) -> None:
    """Save configuration to file."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def get_input(prompt: str, default: str, config: dict, key: str) -> str:
    """Get input with optional default value."""
    current = config.get(key, "")
    display = current if current else "<not set>"

    user_input = input(f"{prompt} [{display}]: ").strip()

    if not user_input:
        return current
    else:
        config[key] = user_input
        return user_input


def interactive_config() -> dict:
    """Run interactive configuration."""
    config = load_config()

    print("\n=== Face Swap Configuration ===")
    print("Press Enter to keep current value\n")

    get_input("Source image path", config.get("source", ""), config, "source")
    get_input("Input directory", config.get("input_dir", ""), config, "input_dir")
    get_input("Output directory", config.get("output_dir", ""), config, "output_dir")
    get_input("Replicate Model", config.get("model", ""), config, "model")

    save_config(config)

    return config