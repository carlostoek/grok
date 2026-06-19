# grok

Telegram bot for image and video generation via xAI Grok Imagine and Replicate.

## Models

- **Grok Imagine** — image generation/editing (xAI or Replicate)
- **Grok Imagine Video** — text-to-video and image-to-video (xAI)
- **Seedream 5.0** — image generation (Replicate)
- **Face Swap** — face swap (Replicate)

## Video generation

Select **Grok Imagine Video** via `/model`, then:

- Send a text prompt to generate a video (with confirmation)
- Send a photo with caption to animate it (image-to-video)
- Reply to a photo with text to animate it

Defaults: 5s duration, 16:9 aspect ratio, 720p resolution (persisted per user in `sessions.json`).

Use `/video` to change duration (5/10/15s), aspect ratio, and resolution (480p/720p).

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram bot token |
| `REPLICATE_API_TOKEN` | Yes | Replicate API token |
| `XAI_API_KEY` | Yes | xAI API key |
| `ALLOWED_TELEGRAM_IDS` | No | Comma-separated user IDs; if set, only these users can use the bot (enforced on all messages and callbacks) |
| `VIDEO_MAX_GLOBAL_CONCURRENT` | No | Max simultaneous video jobs fleet-wide (default: 5) |
| `VIDEO_MAX_GLOBAL_HOURLY` | No | Max video API jobs per hour fleet-wide (default: 50) |

## Deployment

**Production allowlist:** Set `ALLOWED_TELEGRAM_IDS` to a comma-separated list of Telegram user IDs before going live. Without it, anyone who discovers the bot token can use the bot. Example: `ALLOWED_TELEGRAM_IDS=123456789,987654321`.

**Single instance:** Run one bot process per deployment. Global video concurrency (`VIDEO_MAX_GLOBAL_CONCURRENT`) and in-flight hourly quota reservations are kept in process memory only—they are not shared across replicas or surviving restarts.

**In-memory concurrency:** Per-user and fleet-wide concurrent video job limits reset when the process restarts. A brief window after restart may allow more than the configured cap until counters are rebuilt from active jobs.

**Hourly quota on failed POST:** A slot is reserved when a video job starts. The hourly counter is persisted only after xAI accepts the POST (returns `request_id`). Failed POSTs release the reservation and do not increment the persisted hourly count. Poll failures after a successful POST still count toward the limit (the API job was already accepted).

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
