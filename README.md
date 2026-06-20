# grok

Telegram bot for image and video generation via xAI Grok Imagine and Replicate.

## Models

- **Grok Imagine** — image generation/editing (xAI, Replicate, or Kie.ai)
- **Grok Imagine Video** — text-to-video and image-to-video (xAI or Kie.ai)
- **Seedream 5.0** — image generation (Replicate)
- **Face Swap** — face swap (Replicate)

## Video generation

Use `/config` to select models and adjust settings. Legacy aliases: `/model`, `/imagine`, `/imaginess`, `/video`.

Select **Grok Imagine Video** via `/config` (or `/model`), then:

- Send a text prompt to generate a video (with confirmation)
- Send a photo with caption to animate it (image-to-video)
- Reply to a photo with text to animate it

Defaults: 5s duration, 16:9 aspect ratio, 720p resolution (persisted per user in `sessions.json`).

Use `/config` (or `/video`) to change the model (`grok-imagine-video` base or `grok-imagine-video-1.5` recent), duration (3/5/10/15s), aspect ratio, and resolution (480p/720p).

**Kie.ai video constraints** (when Kie.ai is selected via `/config` or `/imaginess`): duration is clamped to 6–30s (3/5s become 6s); base model supports aspect ratios 16:9, 9:16, 1:1, 3:2, 2:3; model 1.5 adds 4:3 and 3:4 and is image-to-video only on Kie.ai. Replicate image provider still uses xAI for video.

**Data privacy:** When Kie.ai is the active provider, prompts and uploaded images are sent to Kie.ai (third-party) for processing. Operators should inform users accordingly.

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram bot token |
| `REPLICATE_API_TOKEN` | Yes | Replicate API token |
| `XAI_API_KEY` | Yes | xAI API key |
| `KIE_API_KEY` | No | Kie.ai API key (required when using Kie.ai provider via `/config` or `/imaginess`) |
| `ALLOWED_TELEGRAM_IDS` | Yes (recommended) | Comma-separated user IDs; only these users can use the bot (enforced on all messages and callbacks) |

## Deployment

**Allowlist:** Set `ALLOWED_TELEGRAM_IDS` to your Telegram user ID(s) before going live. Without it, anyone who discovers the bot can use it and consume your API tokens. Example: `ALLOWED_TELEGRAM_IDS=123456789,987654321`.

**FSM storage:** The bot uses aiogram `MemoryStorage` for the `/config` flow state. FSM data is lost on restart and is not shared across multiple bot processes. For production with restarts or horizontal scaling, switch to Redis-backed storage.

## Tests

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
pytest tests/
```

Or without activating the venv:

```bash
./venv/bin/pip install -r requirements-dev.txt
./venv/bin/python -m pytest tests/ -q
```
