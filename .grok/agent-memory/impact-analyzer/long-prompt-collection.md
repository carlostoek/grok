# Impact Analysis: Long Prompt Collection & Telegram Length Limits

**Feature ID:** `long-prompt-collection`  
**Date:** 2026-06-24  
**Scope:** Replace artificial `MAX_PROMPT_LEN=1000` with Telegram-native limits; when photo/album captions exceed 1020 chars, defer prompt collection via follow-up text message; truncate outbound photo/video captions to 1024; clear new pending state on model switch.

---

## 1. Executive Summary

Today `_validate_prompt` rejects any prompt longer than **1000** characters (`MAX_PROMPT_LEN`), even though Telegram allows **4096** chars in text messages and **1024** in media captions. Long prompts on photo/album captions therefore fail validation instead of being handled gracefully.

The change removes the 1000-char cap, introduces Telegram-aligned constants, adds a **two-step collection flow** for captions > 1020 chars (save `file_id`s → ask user for text prompt → generate), truncates **output** captions in `process_image_result` / `process_video_result`, and extends `config_flow._activate_model` to clear the new pending collection state.

**Primary touch surface:** `bot.py` handlers and result delivery. **No Kie/xAI/Replicate API changes** — backends receive the full prompt. **No `sessions.py` persistence** for collection state (in-memory `user_state` only, same pattern as `pending_prompt`).

---

## 2. Current State (Verified)

### 2.1 Constants & validation (`bot.py`)

| Symbol | Location | Today |
|--------|----------|-------|
| `MAX_PROMPT_LEN` | L52 | `1000` — arbitrary app limit |
| `_validate_prompt` | L242–247 | min 3, max `MAX_PROMPT_LEN` |
| `TELEGRAM_MAX_VIDEO_BYTES` | L134 | 50 MB (unrelated) |

**Call sites of `_validate_prompt`:**

| Function | Line | Input source | Max today |
|----------|------|--------------|-----------|
| `handle_regenerate_image` | L778 | `regen["prompt"]` from `generation_refs` | 1000 |
| `handle_text` | L962 | `message.text` | 1000 |
| `handle_photo_caption` | L1107 | parsed caption (`/s` stripped) | 1000 |
| `handle_reply_edit` | L1223 | `message.text` (reply) | 1000 |
| `_process_grok_album_after_delay` | L1381 | album caption | 1000 |

### 2.2 Inbound handler routing

| Handler | Filter | Prompt source | Confirm step? |
|---------|--------|---------------|---------------|
| `handle_text` | `_is_generation_prompt_message` | plain text | **Yes** for `grok` / `grok_video` (`pending_prompt` + inline keyboard) |
| `handle_photo_caption` | `photo + caption`, no `media_group_id` | caption | **No** — direct `_do_generate_video` or `generate_image` |
| `handle_reply_edit` | `text + reply_to_message.photo` | reply text | **No** |
| `_process_grok_album_after_delay` | via `handle_album` | first album caption | **No** — sequential batch |

`handle_photo_caption` explicitly ignores `media_group_id` (L1091–1092); albums go through `handle_album` → `_process_grok_album_after_delay`.

### 2.3 Outbound captions (`bot.py`)

```2117:2121:bot.py
    sent_msg = await message.answer_photo(
        photo,
        caption=f"<b>{prefix}:</b> {_escape_prompt(prompt)}",
        parse_mode="HTML",
```

```2480:2483:bot.py
        await message.answer_video(
            video,
            caption=f"<b>{prefix}:</b> {safe_prompt}",
            parse_mode="HTML",
```

- Full `prompt` is echoed in caption with HTML wrapper.
- **No truncation today** — prompts > ~1010 chars can cause `TelegramBadRequest` on `answer_photo` / `answer_video` (1024 caption limit).
- Full prompt is also stored in `sessions.save_generation_ref` via `regen_context` — must **not** be truncated there.

### 2.4 `user_state` (`bot.py` L152–157, L460–474)

In-memory only (hydrated from `sessions.py` on first access):

| Key | Purpose |
|-----|---------|
| `pending_prompt` | Text-only grok/grok_video confirm flow |
| `integrate_ref_awaiting` | `/cambiar_referencia` photo capture |
| `model`, `fs_state`, paths | Model / faceswap / integrate |

`pending_prompt` is cleared on confirm cancel (L722), confirm yes (L728), and model switch (`config_flow._activate_model` L401).

**No caption-collection state exists today.**

### 2.5 `config_flow.py`

`_activate_model` (L399–403) clears `pending_prompt` and sets `model`. Invoked from all `cfg:model:*` paths and grok-video shortcut flows.

### 2.6 Generation stack (unchanged by this feature)

| Function | Providers | Receives prompt |
|----------|-----------|-----------------|
| `generate_image` (L1669) | xAI, Kie, Replicate | full string |
| `generate_video` (L2198) | xAI, Kie | full string |
| `_generate_kie` | Kie upload / `kie_source_ref` | full string |
| `_do_generate_video` (L1023) | all video paths | full string |

Provider APIs are not the bottleneck; Telegram I/O limits are.

### 2.7 Tests referencing `MAX_PROMPT_LEN`

| File | Test | Impact |
|------|------|--------|
| `tests/test_security.py` | `test_validate_prompt_max_length` | Must use `TELEGRAM_MAX_TEXT_LEN` |
| `tests/test_video_extra.py` | `test_text_prompt_too_long_rejected` | Must use 4096 threshold |
| `tests/test_video_handlers.py` | `test_model_switch_clears_pending_prompt` | Extend for new pending state |
| `tests/test_cmd_handlers.py` | confirm cancel / pending_prompt | Unaffected unless collection state added |
| `tests/test_album_batch.py` | caption validation via `_validate_prompt` | Long-caption → collection, not error |
| `tests/test_kie_provider.py` | `test_process_image_result_saves_generation_ref` | Add truncation assertion |
| `tests/test_process_video_result.py` | success path caption | Add truncation test |

---

## 3. Proposed Behavior

### 3.1 New constants (`bot.py`)

| Constant | Value | Role |
|----------|-------|------|
| `TELEGRAM_MAX_CAPTION_LEN` | 1024 | Hard Telegram caption limit (output + reference) |
| `TELEGRAM_CAPTION_COLLECT_THRESHOLD` | 1020 | Inbound caption length triggering collection flow |
| `TELEGRAM_MAX_TEXT_LEN` | 4096 | Max prompt from text messages / collection follow-up |

Remove `MAX_PROMPT_LEN`.

### 3.2 Updated `_validate_prompt`

Recommended signature behavior:

- **Min length:** 3 (unchanged).
- **Max length:** parameterised or context-aware:
  - **Caption path** (photo/album): if `len(prompt) > TELEGRAM_CAPTION_COLLECT_THRESHOLD` → do **not** call `_validate_prompt` max check; trigger collection instead.
  - **Text path** (`handle_text`, `handle_reply_edit`, regen): max `TELEGRAM_MAX_TEXT_LEN` (4096).

### 3.3 Collection flow (inbound captions > 1020)

Applies to:

1. **`handle_photo_caption`** — single photo + caption (grok, grok_video, seedream; not faceswap).
2. **`_process_grok_album_after_delay`** — album batch (grok only).

**Steps:**

| Step | Action |
|------|--------|
| 1 | Parse caption (`_parse_integrate_caption` for `/s`). |
| 2 | If `len(prompt) > TELEGRAM_CAPTION_COLLECT_THRESHOLD` (1020): |
| 3 | Save pending state: `file_id`(s), `integrate_mode`, `kind` (`photo` \| `album`), optional `model_key` guard. |
| 4 | Reply: ask user to send prompt as a **plain text message** (Spanish UX, mirror existing help tone). |
| 5 | Return without calling `generate_image` / `_do_generate_video`. |
| 6 | On next `handle_text` (no reply): if pending collection state exists, validate text (min 3, max 4096), download saved `file_id`s, run same generation path as direct caption (i2v / single edit / album loop), clear state. |

**Provider coverage:** all providers on existing code paths (xAI, Kie, Replicate for image; xAI, Kie for video) — no provider branching in collection itself.

**Not in scope (per approved plan):**

- Photo **without** caption + separate text (user must use caption > 1020 or text-only flows).
- `faceswap` (caption ignored).
- Persisting collection state across bot restarts.

### 3.4 Output caption truncation

In `process_image_result` and `process_video_result`:

- Build caption as today: `<b>{prefix}:</b> {escaped_prompt}`.
- Truncate so final string ≤ `TELEGRAM_MAX_CAPTION_LEN` (1024), accounting for HTML prefix length and HTML entity expansion from `_escape_prompt`.
- Store **full** `prompt` in `save_generation_ref` / `regen_context` (unchanged).

Recommend helper: `_format_result_caption(prefix: str, prompt: str) -> str`.

### 3.5 Model switch cleanup (`config_flow.py`)

Extend `_activate_model` to clear new collection state (e.g. `pending_caption_collect = None`) alongside `pending_prompt`.

Also consider clearing on `handle_confirm_generation` cancel — optional; not in plan but reduces stale-state risk.

---

## 4. Files & Functions to Touch

### 4.1 `bot.py` — primary

| Area | Functions / symbols | Change |
|------|---------------------|--------|
| Constants | L52 | Remove `MAX_PROMPT_LEN`; add 3 Telegram constants |
| Validation | `_validate_prompt` | Max → `TELEGRAM_MAX_TEXT_LEN`; optional `max_len` param |
| New helpers | `_caption_needs_collection`, `_format_result_caption`, `_clear_pending_caption_collect`, `_set_pending_caption_collect`, `_consume_pending_caption_collect` (names TBD) | Collection + truncation |
| State init | `get_user_state` | Add `pending_caption_collect: None` |
| Text handler | `handle_text` (L940) | **First:** check pending collection → generate; else existing flow |
| Photo caption | `handle_photo_caption` (L1090) | Branch: collection vs validate vs generate |
| Album batch | `_process_grok_album_after_delay` (L1354) | Collection branch before `_validate_prompt` |
| Results | `process_image_result` (L2092), `process_video_result` (L2443) | Truncate outbound caption |
| Docs comment | `user_state` key list (L154) | Document new key |

**Likely refactor (recommended):** extract shared single-photo edit logic from `handle_photo_caption` into `_process_single_photo_edit(message, prompt, file_id, integrate_mode)` so collection completion and direct caption paths share one implementation.

**Unchanged handlers (but validation limit changes):**

- `handle_reply_edit` — text max 4096 (was 1000).
- `handle_regenerate_image` — regen prompts up to 4096 if stored.

### 4.2 `config_flow.py`

| Function | Change |
|----------|--------|
| `_activate_model` (L399) | Clear `pending_caption_collect` (and keep `pending_prompt` clear) |

### 4.3 `sessions.py`

**No changes expected** — collection state is ephemeral in `user_state`.

### 4.4 Tests (new + updates)

| File | Action |
|------|--------|
| `tests/test_long_prompt_collection.py` (**new**) | Collection trigger, completion, album, i2v, integrate `/s`, model-switch clear |
| `tests/test_security.py` | Update max-length test for 4096 |
| `tests/test_video_extra.py` | Update `test_text_prompt_too_long_rejected` |
| `tests/test_album_batch.py` | Long album caption → collection, not `_validate_prompt` error |
| `tests/test_video_handlers.py` | i2v long caption collection; model switch clears collection |
| `tests/test_process_video_result.py` | Caption truncation on long prompt |
| `tests/test_kie_provider.py` | `process_image_result` caption truncation + full prompt in ref |
| `tests/test_config_flow.py` | Model switch clears collection state |
| `tests/test_integrate_ref.py` | Long `/s` album/photo caption collection |

---

## 5. Consumers & Downstream Effects

| Consumer | Effect |
|----------|--------|
| **Telegram `answer_photo` / `answer_video`** | Fewer `TelegramBadRequest` from oversized captions |
| **`sessions.save_generation_ref`** | Still stores full prompt; regen works with long prompts |
| **`handle_regenerate_image`** | Can regen 1000–4096 char prompts if previously generated |
| **Kie `_generate_kie`** | Receives longer prompts; no code change |
| **xAI `_generate_xai`** | Same |
| **Replicate `_generate_replicate`** | Same |
| **Status messages** (`_video_start_message`, edit status) | Can display up to 4096 in `edit_text` (within Telegram message limit) — no change required |
| **`pending_prompt` confirm flow** | Must not fire when completing collection; `handle_text` ordering critical |
| **Album `_album_cache`** | Orthogonal to caption collection; album still collects photos first, then evaluates caption length |

---

## 6. Risks & Mitigations

### 6.1 Handler precedence / stale state

| Risk | Severity | Mitigation |
|------|----------|------------|
| User enters collection flow, sends unrelated text | Medium | Pending state consumed only on valid text; define overwrite/cancel rules (new photo replaces pending?) |
| `pending_caption_collect` + `pending_prompt` both set | Low | Collection check first in `handle_text`; clear both on model switch |
| Model switch mid-collection | Medium | Clear in `_activate_model`; test in `test_config_flow` / `test_video_handlers` |
| Bot restart during collection | Low | State lost (acceptable; same as `pending_prompt`) |
| Telegram `file_id` expiry | Low | Complete promptly; file_ids typically valid hours/days |

### 6.2 Caption truncation edge cases

| Risk | Severity | Mitigation |
|------|----------|------------|
| HTML entities (`&`, `<`) expand length after escape | Medium | Truncate on **escaped** string or reserve margin |
| Prefix `"Edit:"` / `"Prompt:"` consumes budget | Low | Truncate prompt portion in `_format_result_caption` |
| User sees truncated caption but regen uses full prompt | Low | Expected; document in UX if needed |

### 6.3 Collection threshold vs Telegram limit

| Risk | Severity | Mitigation |
|------|----------|------------|
| Captions 1021–1024 valid on Telegram but trigger collection | Low | By design — avoids operating at hard limit |
| Prompts > 1024 cannot be sent as caption at all | Info | User must use text-only or collection from shorter caption; plan covers > 1020 in caption |

### 6.4 Album + collection interaction

| Risk | Severity | Mitigation |
|------|----------|------------|
| Long caption on album saves N file_ids; user sends short text | Low | Validate min 3 on follow-up text |
| Album integrate mode (`/s`) with long caption | Medium | Store `integrate_mode` in pending state; test in `test_integrate_ref` |

### 6.5 Regression: removing 1000-char cap

| Risk | Severity | Mitigation |
|------|----------|------------|
| Provider rejects very long prompts | Low | Pre-existing; backend errors already surfaced |
| Text spam / abuse | Low | Existing allowlist middleware unchanged |

### 6.6 Sensitive systems

| System | Sensitivity | Notes |
|--------|-------------|-------|
| **Telegram handlers** | High | Registration order unchanged; `handle_text` gains early branch |
| **`user_state`** | Medium | New ephemeral key; must clear on model switch |
| **Kie provider** | Medium | No internal changes; i2i/i2v upload paths reused with stored `file_id`s |
| **`_album_cache` / `_album_lock`** | Medium | Do not conflate with caption pending state |
| **`generation_refs.json`** | Low | Full prompt preserved |

---

## 7. Suggested Implementation Order

1. Add constants + `_format_result_caption` + truncation in `process_image_result` / `process_video_result`.
2. Update `_validate_prompt` to use `TELEGRAM_MAX_TEXT_LEN`.
3. Add `pending_caption_collect` to `get_user_state` + helpers.
4. Wire collection in `handle_photo_caption` and `_process_grok_album_after_delay`.
5. Wire consumption in `handle_text` (before confirm flow).
6. Clear state in `config_flow._activate_model`.
7. Update/add tests.

---

## 8. Test Commands

Run from repo root `/home/ubuntu/repos/grok`:

```bash
# Full suite
pytest

# Focused — validation & security
pytest tests/test_security.py tests/test_video_extra.py -v

# Focused — collection flow (after new file exists)
pytest tests/test_long_prompt_collection.py -v

# Focused — handlers
pytest tests/test_video_handlers.py tests/test_album_batch.py tests/test_integrate_ref.py -v

# Focused — output truncation
pytest tests/test_process_video_result.py tests/test_kie_provider.py::test_process_image_result_saves_generation_ref -v

# Focused — config / state cleanup
pytest tests/test_config_flow.py tests/test_video_handlers.py::test_model_switch_clears_pending_prompt -v

# Regression — album batch + Kie (unchanged paths)
pytest tests/test_album_batch.py tests/test_kie_provider.py -v
```

**Pre-implementation baseline (current tree):**

```bash
pytest tests/test_security.py::test_validate_prompt_max_length \
       tests/test_video_extra.py::test_text_prompt_too_long_rejected -v
```

These two tests **will fail** after `MAX_PROMPT_LEN` removal until updated.

---

## 9. Acceptance Checklist

- [ ] `MAX_PROMPT_LEN` removed; three Telegram constants present
- [ ] Caption > 1020 on photo → asks for text; follow-up text triggers edit/i2v (all models/providers)
- [ ] Caption > 1020 on grok album → saves all `file_id`s; follow-up text triggers batch edit
- [ ] Text prompts accept up to 4096 chars (text, reply-edit, regen)
- [ ] Output photo/video captions ≤ 1024 chars
- [ ] `generation_refs` retains full prompt for regen
- [ ] Model switch clears `pending_caption_collect` and `pending_prompt`
- [ ] All updated/new tests pass under `pytest`

---

## 10. Impact Map (Summary Diagram)

```
Inbound captions (photo/album)
  ├─ len ≤ 1020 → _validate_prompt (min 3) → generate_image / _do_generate_video
  └─ len > 1020 → save file_ids in user_state.pending_caption_collect
                    → prompt user for text
                         └─ handle_text → validate (max 4096) → same generate paths

Inbound text (no pending collection)
  ├─ grok / grok_video → confirm flow (pending_prompt) → generate
  ├─ seedream → direct generate
  └─ reply-edit → direct generate (max 4096)

Outbound results
  process_image_result / process_video_result
    └─ caption = truncate(_format_result_caption(prefix, full_prompt), 1024)
    └─ save_generation_ref(prompt=full_prompt)  # untruncated

Model switch (config_flow._activate_model)
  └─ clear pending_prompt + pending_caption_collect
```