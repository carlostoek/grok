# Test Guardian Audit: Long Prompt Collection

**Feature ID:** `long-prompt-collection`  
**Date:** 2026-06-24  
**Auditor:** test-guardian  
**Plan:** `.planning/quick/long-prompt-collection/PLAN.md` (Fase 6)  
**Primary test file:** `tests/test_long_prompt_collection.py`  
**Runs:**
- `./venv/bin/python -m pytest tests/test_long_prompt_collection.py -v` → **15 passed**
- Phase 6 legacy slice (24 tests) → **24 passed**
- `./venv/bin/python -m pytest tests/ -q` → **255 passed, 2 skipped**

---

## Verdict

**ADEQUATE** — Phase 6 requirements are met: dedicated suite has 15 tests (≥14 required), all eight legacy files were updated, no `MAX_PROMPT_LEN` references remain, and the full suite is green.

The suite protects the core contract: caption >1020 triggers two-step collection, follow-up text completes single edit / i2v / album batch, invalid short follow-up preserves state, confirm flow is bypassed during collection, outbound captions truncate while refs keep full prompt, and model switch clears all four collection keys plus `pending_prompt`.

**Caveat:** A few plan DoD items and risk mitigations are only partially covered (overwrite-on-resend, integrate completion, >4096 follow-up rejection, HTML-escape truncation). Gaps are **non-blocking for merge** but should be tracked for a follow-up test pass.

---

## Phase 6 Planned Test Inventory (15/15)

| # | Test | Present | Asserts meaningful behavior? | Notes |
|---|------|---------|------------------------------|-------|
| 1 | `test_long_prompt_state_helpers` | ✅ | ✅ | set/clear/is round-trip on all 4 keys |
| 2 | `test_prompt_needs_collection_at_1021` | ✅ | ✅ | 1020 → False; 1021 → True |
| 3 | `test_photo_caption_over_1020_triggers_collection` | ✅ | ✅ | state set; `generate_image` not called; reply asks for text |
| 4 | `test_photo_caption_under_1020_direct_generate` | ✅ | ✅ | no collection state; `generate_image` awaited |
| 5 | `test_grok_video_long_caption_sets_is_video` | ✅ | ✅ | `pending_edit_is_video=True`; i2v wording in reply |
| 6 | `test_album_long_caption_saves_all_file_ids` | ✅ | ✅ | N file_ids saved; album note in reply |
| 7 | `test_integrate_long_caption_saves_integrate_mode` | ✅ | ✅ | `/s` + 1021 → `pending_edit_integrate_mode=True` |
| 8 | `test_handle_text_completes_single_edit` | ✅ | ✅ | follow-up → `generate_image` with text prompt; state cleared |
| 9 | `test_handle_text_completes_i2v` | ✅ | ✅ | `pending_edit_is_video` → `_do_generate_video` |
| 10 | `test_handle_text_completes_album_batch` | ✅ | ✅ | 2 file_ids → 2× `generate_image` |
| 11 | `test_handle_text_invalid_prompt_keeps_state` | ✅ | ⚠️ Partial | Only short prompt (`"no"`); not >4096 |
| 12 | `test_handle_text_collection_skips_confirm_flow` | ✅ | ✅ | `reply_markup` is None (R1 precedence) |
| 13 | `test_model_switch_clears_long_prompt_state` | ✅ | ✅ | All 4 keys + `pending_prompt` reset |
| 14 | `test_format_result_caption_truncates_long_prompt` | ✅ | ✅ | len ≤ 1024; ellipsis suffix |
| 15 | `test_text_prompt_accepts_4096_chars` | ✅ | ✅ | `_validate_prompt` OK at 4096; error at 4097 |

---

## Legacy File Updates (8/8)

| File | Required change | Status | Evidence |
|------|-----------------|--------|----------|
| `tests/test_security.py` | `TELEGRAM_MAX_TEXT_LEN` in max-length test | ✅ | `test_validate_prompt_max_length` |
| `tests/test_video_extra.py` | `test_text_prompt_too_long_rejected` → 4096+1 | ✅ | uses `TELEGRAM_MAX_TEXT_LEN + 5` |
| `tests/test_album_batch.py` | Caption 1021+ → collection, not validation error | ✅ | `test_grok_album_long_caption_triggers_collection` |
| `tests/test_video_handlers.py` | i2v long caption → collection; model switch cleanup | ✅ | `test_photo_caption_i2v_long_caption_triggers_collection`; `test_model_switch_clears_pending_prompt` (partial on 4 keys) |
| `tests/test_integrate_ref.py` | `/s` + long caption → `pending_edit_integrate_mode` | ✅ | `test_integrate_long_caption_saves_integrate_mode` |
| `tests/test_config_flow.py` | Model switch clears collection | ✅ | `test_model_switch_clears_long_prompt_collection` |
| `tests/test_process_video_result.py` | Truncated long outbound caption | ✅ | `test_success_truncates_long_caption` |
| `tests/test_kie_provider.py` | Caption truncated + full prompt in ref | ✅ | `test_process_image_result_truncates_caption_keeps_full_ref` |

**Legacy hygiene:** `grep MAX_PROMPT_LEN` across repo → **0 matches** ✅

---

## Coverage by Plan Phase / Risk

| Area | Coverage | Tests |
|------|----------|-------|
| Fase 1 — constants, `_validate_prompt` 4096, outbound truncation | **Strong** | `test_text_prompt_accepts_4096_chars`, `test_format_result_caption_truncates_long_prompt`, `test_success_truncates_long_caption`, `test_process_image_result_truncates_caption_keeps_full_ref`, `test_validate_prompt_max_length` |
| Fase 2 — state helpers + defaults | **Strong** | `test_long_prompt_state_helpers`; defaults exercised via handlers |
| Fase 3 — triggers (photo + album, integrate, i2v) | **Strong** | photo/album/i2v/integrate trigger tests across 4 files |
| Fase 4 — `handle_text` consumption + confirm skip | **Strong** | completion tests (single, i2v, album), invalid short, skip confirm |
| Fase 5 — model switch cleanup | **Strong** | 3 tests in `test_long_prompt_collection`, `test_config_flow`, `test_video_handlers` |
| R1 — collection before confirm | **Covered** | `test_handle_text_collection_skips_confirm_flow` |
| R2 — HTML entities expand caption | **Gap** | No prompt with `&`, `<`, `>` in `_format_result_caption` test |
| R3 — stale state / abandon flow | **Partial** | Model switch clears; overwrite-on-resend untested |
| R5 — album integrate `/s` + long caption | **Partial** | Trigger tested; completion with integrate untested |
| R7 — album short-caption regression | **Covered elsewhere** | Existing `test_album_batch.py` short-caption tests unchanged |

---

## Gaps (ordered by severity)

### Medium — worth adding post-v1

| Gap | Plan / risk reference | Current state | Suggested test |
|-----|----------------------|---------------|----------------|
| **Follow-up >4096 keeps state** | Fase 4 DoD: invalid `< 3` **or** `> 4096` | `test_handle_text_invalid_prompt_keeps_state` only uses `"no"` (<3) | `test_handle_text_over_4096_keeps_state` — send `x * 4097` while awaiting; assert error answer, state intact |
| **Overwrite pending on new long caption** | Fase 3 DoD: new photo/album replaces `pending_edit_file_ids` | Not tested | `test_new_long_caption_overwrites_pending_file_ids` — set awaiting + old ids; send new photo caption 1021; assert new `file_id` only |
| **Integrate completion after follow-up** | R5; manual verify `/s` + long → follow-up → integrate edit | Trigger in 2 files; no completion test | `test_handle_text_completes_integrate_edit` — `pending_edit_integrate_mode=True`, mock `_validate_integrate_prerequisites` + ref load, assert `generate_image` with integrate path |

### Low — optional hardening

| Gap | Notes | Suggested test |
|-----|-------|----------------|
| **HTML-escape truncation budget** | R2: entities may expand escaped length | `test_format_result_caption_truncates_escaped_html` — prompt with `&` / `<`; assert final len ≤ 1024 |
| **Handler-level 4096 for reply-edit / regen** | Fase 4 DoD mentions `handle_reply_edit` / `handle_regenerate_image` | Only `_validate_prompt` unit-tested; handlers use same helper | Optional: 4096-char prompt through `handle_reply_edit` / `handle_regenerate_image` |
| **`test_model_switch_clears_pending_prompt` partial** | `test_video_handlers.py` asserts 2/4 collection keys | Full 4-key assert exists in `test_config_flow` + `test_long_prompt_collection` | Extend video_handlers test for parity (cosmetic) |
| **Duplicate integrate trigger tests** | Same test name in `test_long_prompt_collection`, `test_integrate_ref` | Redundant but harmless | Consider deduping later |

---

## Implementation ↔ Test Alignment

```
TELEGRAM_CAPTION_COLLECT_THRESHOLD (1020) post-_parse_integrate_caption
  → test_prompt_needs_collection_at_1021 ✅
  → photo/album/i2v/integrate trigger tests ✅

handle_photo_caption / _process_grok_album_after_delay: collection branch
  → no generate_image / _do_generate_video ✅

handle_text: awaiting_long_prompt_text first branch
  → completion + skip confirm ✅
  → invalid short keeps state ⚠️ (not >4096)

_complete_long_prompt_collection → _process_single_photo_edit / _process_album_edit_from_file_ids
  → single, i2v, album batch ✅
  → integrate completion gap ⚠️

_format_result_caption + process_image_result / process_video_result
  → truncation ≤ 1024; ref keeps full prompt ✅
  → HTML escape edge case gap ⚠️

config_flow._activate_model clears 4 keys + pending_prompt
  → 3 model-switch tests ✅

Overwrite pending while awaiting (implementation: _set_long_prompt_collection replaces)
  → gap ⚠️
```

---

## Recommendations

1. **Ship with current suite** — Phase 6 DoD satisfied; 255/255 runnable tests green.
2. **Next test PR (3 tests minimum):**
   - `test_handle_text_over_4096_keeps_state`
   - `test_new_long_caption_overwrites_pending_file_ids`
   - `test_handle_text_completes_integrate_edit`
3. **Optional fourth:** `test_format_result_caption_truncates_escaped_html` for R2.
4. **Keep manual smoke** from plan: 1021-char caption → 2000-char follow-up; album 3-photo batch; model switch during wait.

---

## Summary

| Metric | Result |
|--------|--------|
| `test_long_prompt_collection.py` tests | 15/15 (≥14 required) |
| Legacy files updated | 8/8 |
| `MAX_PROMPT_LEN` references | 0 |
| Full suite | 255 passed, 2 skipped |
| Core two-step flow | Well protected |
| Outbound truncation + full refs | Protected |
| Model switch cleanup | Protected (3 tests) |
| Confirm-flow precedence (R1) | Protected |
| Known gaps | 3 medium, 4 low |

**Final verdict: ADEQUATE** — suite protects the feature adequately for merge; add 3 medium-priority tests in a follow-up to close documented DoD gaps.