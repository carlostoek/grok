# Learning: Long Prompt Collection (2026-06-24)

**Feature ID:** `long-prompt-collection`  
**Pool:** long-prompt-collection (closed)  
**Plan:** `.planning/quick/long-prompt-collection/PLAN.md`

---

## Feature Summary

Replaced the artificial `MAX_PROMPT_LEN=1000` cap with Telegram-native limits and a two-step collection flow for oversized photo/album captions.

| Area | Before | After |
|------|--------|-------|
| Prompt validation | Max 1000 chars everywhere | Max **4096** for text paths (`TELEGRAM_MAX_TEXT_LEN`) |
| Inbound captions | Failed validation if >1000 | If `len(prompt) > 1020` **after** `_parse_integrate_caption` → save `file_id`s, ask for follow-up text |
| Outbound captions | Full prompt echoed (could exceed Telegram limit) | `_format_result_caption` truncates to ≤ **1024** chars |
| `generation_refs` | Full prompt stored | **Unchanged** — full prompt preserved for regen |

### Collection flow (>1020)

1. User sends photo or grok album with caption > 1020 chars (post-`/s` strip).
2. Bot stores `pending_edit_file_ids` + flags, replies asking for prompt as plain text.
3. User sends text message → `handle_text` consumes via `_complete_long_prompt_collection`.
4. Same generation paths as direct caption: single edit, i2v (`grok_video`), album batch, integrate `/s`.

### Truncate output (1024)

`process_image_result` and `process_video_result` use `_format_result_caption(prefix, prompt)` so Telegram captions never exceed `TELEGRAM_MAX_CAPTION_LEN` (1024). Truncation runs on **escaped** prompt text to account for HTML entities.

---

## Files Changed

| File | Change |
|------|--------|
| `bot.py` | Removed `MAX_PROMPT_LEN`; added Telegram constants; `_format_result_caption`; collection helpers; triggers in `handle_photo_caption` / `_process_grok_album_after_delay`; `_process_single_photo_edit`, `_process_album_edit_from_file_ids`, `_complete_long_prompt_collection`; `handle_text` first-branch consumption |
| `config_flow.py` | `_activate_model` clears all 4 collection keys + `pending_prompt` |
| `tests/test_long_prompt_collection.py` | **New** — 24 tests for collection flow |
| `tests/test_security.py` | Max-length test → `TELEGRAM_MAX_TEXT_LEN` (4096) |
| `tests/test_video_extra.py` | Too-long text test → 4096+1 |
| `tests/test_album_batch.py` | Long album caption → collection, not validation error |
| `tests/test_video_handlers.py` | i2v long caption → collection; model switch cleanup |
| `tests/test_integrate_ref.py` | `/s` + long caption → `pending_edit_integrate_mode` |
| `tests/test_config_flow.py` | Model switch clears collection state |
| `tests/test_process_video_result.py` | Outbound caption truncation |
| `tests/test_kie_provider.py` | Caption truncated + full prompt in ref |

**Out of scope (correctly):** no collection state in `sessions.py`; no provider API changes; faceswap / photo-without-caption paths unchanged.

---

## Test Stats

```bash
./venv/bin/python -m pytest tests/test_long_prompt_collection.py -q
# 24 passed

./venv/bin/python -m pytest tests/ -q
# 264 passed, 2 skipped
```

| Metric | Result |
|--------|--------|
| Dedicated suite | 24 tests (`test_long_prompt_collection.py`) |
| Full suite | **264 passed**, 2 skipped |
| Legacy updates | 8 files; `MAX_PROMPT_LEN` references → 0 |

---

## Review

| Field | Value |
|-------|-------|
| **Effort** | 3 |
| **Rounds** | 3 |
| **Verdict** | PASS WITH NOTES (arch-enforcer); ADEQUATE (test-guardian) |

### Issues fixed across review rounds

1. **Follow-up >4096 keeps state** — `test_handle_text_over_4096_keeps_state`
2. **Overwrite pending on new long caption** — `test_new_long_caption_replaces_pending_file_ids`
3. **Integrate completion after follow-up** — `test_handle_text_completes_integrate_edit`
4. **HTML entity truncation (R2)** — `test_format_result_caption_html_entities`
5. **Clear on success only** — `test_generation_failure_keeps_collection_state`; generation helpers return `bool`
6. **Stale collection UX** — `test_short_caption_clears_stale_collection_state`, `test_photo_no_caption_reminds_pending_collection`
7. **Reply-based completion** — `test_reply_to_bot_message_completes_collection`, `test_reply_text_completes_collection`

---

## Key Decisions

### Threshold 1020

Evaluate `len(prompt) > TELEGRAM_CAPTION_COLLECT_THRESHOLD` (1020) **after** `_parse_integrate_caption`, not on raw caption. Leaves margin below Telegram's 1024 caption hard limit; captions 1021–1024 still trigger collection by design.

### State keys (in-memory only)

| Key | Role |
|-----|------|
| `awaiting_long_prompt_text` | User must send prompt as text |
| `pending_edit_file_ids` | Saved `file_id`s (1 photo or N album) |
| `pending_edit_integrate_mode` | `/s` detected in original caption |
| `pending_edit_is_video` | Caption was in `grok_video` mode |

Not persisted in `sessions.py` (same pattern as `pending_prompt`). Cleared on model switch via `config_flow._activate_model`. New long caption **replaces** pending state (no accumulate). `_set_long_prompt_collection` also clears `pending_prompt` to avoid dual pending flows.

### Clear on success only

`_complete_long_prompt_collection` calls `_clear_long_prompt_collection` **only when** `_process_single_photo_edit` / `_process_album_edit_from_file_ids` return `True`. Invalid follow-up text or generation failure keeps collection state so the user can retry without re-sending photos.

### `handle_text` ordering

```
awaiting_long_prompt_text → _complete_long_prompt_collection → return
faceswap guard → return
_validate_prompt (max 4096)
grok/grok_video → pending_prompt confirm
seedream → _do_generate_text
```

Collection branch runs **before** confirm keyboard to prevent R1 precedence bugs.

---

## Patterns to Reuse

- **Shared generation helpers:** `_process_single_photo_edit` and `_process_album_edit_from_file_ids` serve both direct-caption and collection-completion paths — avoids duplicated edit/i2v/album logic.
- **Outbound vs ref split:** truncate caption for Telegram I/O; always pass full `prompt` to `save_generation_ref` / `regen_context`.
- **Album tests:** patch `asyncio.create_task` + `asyncio.sleep` (same as `test_album_batch.py`) for delayed album flush.