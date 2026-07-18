# SUMMARY: Long Prompt Collection

**Date:** 2026-06-24  
**Pool:** long-prompt-collection (1 item)  
**Status:** Closed — tests passing

## Outcome

Removed the artificial `MAX_PROMPT_LEN=1000` cap and aligned the bot with Telegram-native length limits. Photo and grok-album captions longer than **1020** chars (after `/s` parsing) trigger a two-step flow: save `file_id`s → ask for follow-up text → generate. Outbound photo/video captions truncate to **1024**; `generation_refs` keep the full prompt.

## Changes

| File | What |
|------|------|
| `bot.py` | `TELEGRAM_MAX_CAPTION_LEN` (1024), `TELEGRAM_CAPTION_COLLECT_THRESHOLD` (1020), `TELEGRAM_MAX_TEXT_LEN` (4096); `_format_result_caption`; collection state + helpers; triggers in `handle_photo_caption` / `_process_grok_album_after_delay`; `_complete_long_prompt_collection`; refactored `_process_single_photo_edit` / `_process_album_edit_from_file_ids` |
| `config_flow.py` | Model switch clears 4 collection keys + `pending_prompt` |
| `tests/test_long_prompt_collection.py` | 24 new tests |
| `tests/test_security.py` | Max-length → 4096 |
| `tests/test_video_extra.py` | Too-long text → 4096+1 |
| `tests/test_album_batch.py` | Long album caption → collection |
| `tests/test_video_handlers.py` | i2v long caption + model switch cleanup |
| `tests/test_integrate_ref.py` | `/s` long caption → integrate mode flag |
| `tests/test_config_flow.py` | Model switch clears collection |
| `tests/test_process_video_result.py` | Outbound caption truncation |
| `tests/test_kie_provider.py` | Truncated caption + full ref prompt |

## Key Decisions

- **Threshold 1020** — evaluated post-`_parse_integrate_caption`, not on raw caption
- **State keys** — `awaiting_long_prompt_text`, `pending_edit_file_ids`, `pending_edit_integrate_mode`, `pending_edit_is_video` (in-memory only)
- **Clear on success only** — collection state cleared after successful generation; invalid text or failures preserve state for retry
- **`handle_text` precedence** — collection branch before confirm keyboard

## Verification

```bash
./venv/bin/python -m pytest tests/test_long_prompt_collection.py -q
# 24 passed

./venv/bin/python -m pytest tests/ -q
# 264 passed, 2 skipped
```

## Gates

| Step | Agent | Verdict |
|------|-------|---------|
| Impact | impact-analyzer | Done |
| Plan | gsd-planner | PLAN.md |
| Execute | gsd-executor | Self-check PASSED |
| Arch | arch-enforcer | PASS WITH NOTES (0 critical) |
| Tests | test-guardian | ADEQUATE (24/24) |
| Review | code review | Effort **3**, **3 rounds**, issues fixed |
| Run | shell | **264 passed** |

## Review Issues Fixed (3 rounds)

- Follow-up >4096 keeps collection state
- New long caption replaces pending `file_id`s
- Integrate `/s` completion after follow-up text
- HTML entity truncation in outbound captions
- Clear collection state on success only (not on generation failure)
- Stale-state UX (short caption / photo without caption while awaiting)
- Reply-to-message text completion paths