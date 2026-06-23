# Test Guardian Audit: Album Batch Kie Imagine

**Feature ID:** `album-batch-kie-imagine`  
**Date:** 2026-06-23  
**Auditor:** test-guardian  
**Plan:** `.planning/quick/20260623-album-batch-kie-imagine/PLAN.md`  
**Test file:** `tests/test_album_batch.py`  
**Run:** `./venv/bin/python -m pytest tests/test_album_batch.py -q` → **11 passed** (0.35s)

---

## Verdict

**ADEQUATE for v1** — all 11 planned tests exist, pass, and assert meaningful behavior on the core album-batch orchestration path. The suite protects routing, collection, caption extraction, Kie upload isolation, sequential processing, error-stop policy, faceswap regression, xAI routing, and seedream silence.

**Caveat:** Several plan DoD items and downstream flows (regen/reply-edit, `delete_status`, success UX) are only partially or indirectly covered. Gaps are **non-blocking for merge** but should be tracked for a follow-up test pass.

---

## Planned Test Inventory (11/11)

| # | Test | Present | Asserts meaningful behavior? | Notes |
|---|------|---------|------------------------------|-------|
| 1 | `test_handle_photo_caption_ignores_media_group` | ✅ | ✅ | `media_group_id` set → `generate_image` never called (routing fix R1) |
| 2 | `test_grok_album_collects_messages` | ✅ | ✅ | 3× `handle_album` → 3× `generate_image` after delay drain |
| 3 | `test_grok_album_extracts_caption_from_first_message` | ✅ | ✅ | Caption on msg[0] → same prompt on every `generate_image` call |
| 4 | `test_grok_album_requires_caption` | ✅ | ✅ | No caption → help text with "caption", no `generate_image` |
| 5 | `test_grok_album_sequential_kie_calls` | ✅ | ✅ | Same prompt, distinct `image_data` bytes (`img-a` / `img-b`) |
| 6 | `test_grok_album_kie_uses_upload_not_ref` | ✅ | ✅ | `kie_source_ref` absent from all `generate_image` kwargs |
| 7 | `test_grok_album_saves_generation_ref_per_output` | ✅ | ⚠️ Partial | 2 refs saved via real `process_image_result`; only `is not None` — no `regen` shape |
| 8 | `test_grok_album_stops_on_error` | ✅ | ✅ | 2nd image fails → 2 calls, status `1/3 completadas; error en imagen 2` |
| 9 | `test_faceswap_album_unchanged` | ✅ | ✅ | `_process_batch_replicate_sync` called once; `generate_image` not called |
| 10 | `test_grok_album_xai_provider` | ✅ | ⚠️ Partial | 2× `generate_image`; `model["provider"] == "xai"` only |
| 11 | `test_handle_album_ignored_for_seedream` | ✅ | ✅ | No cache, no grok processor, no `generate_image` |

---

## Coverage by Risk Area

### Faceswap regression (R9) — **Covered**
`test_faceswap_album_unchanged` patches `_process_batch_replicate_sync` and `download.download_telegram_photo`, confirms faceswap path unchanged and grok `generate_image` never invoked.

### `kie_source_ref` isolation — **Covered at call site**
`test_grok_album_kie_uses_upload_not_ref` inspects every `generate_image` kwargs — no `kie_source_ref`. Implementation (`_process_grok_album_after_delay` L1273) passes only `(model, prompt, image_data)`.

**Gap:** Saved `regen` blobs are not asserted to exclude `kie_source_ref` or include per-image `source_file_id` (`p60`, `p61`). Downstream reply-edit/regenerate paths are untested here (covered elsewhere in `test_kie_provider.py` for single-image flows only).

### xAI provider — **Covered (minimal)**
`test_grok_album_xai_provider` confirms routing reaches `generate_image` twice with xAI-configured model. Does not assert xAI-specific API behavior (delegated to existing provider tests).

### Replicate provider — **Not covered in album suite**
Plan DoD: "xAI y Replicate funcionan vía routing existente de `generate_image`". No album-batch test for `provider == "replicate"`. Low risk if `generate_image` routing is already tested elsewhere.

---

## Gaps (ordered by severity)

### Medium — worth adding post-v1

| Gap | Plan reference | Current state |
|-----|----------------|---------------|
| **`regen_context` / `source_file_id` per output** | DoD: "Cada output guarda `generation_refs` para reply-edit posterior"; regen uses Telegram `source_file_id`, not `kie_source_ref` | `test_grok_album_saves_generation_ref_per_output` only checks ref exists. Should assert `ref["regen"]["mode"] == "edit"`, `ref["regen"]["source_file_id"]` in `{p60, p61}`, and `"kie_source_ref" not in ref["regen"]`. |
| **`delete_status=False`** | R5: batch status must not be deleted mid-loop | Most tests mock `process_image_result`; no test asserts `delete_status=False` or that batch `status_msg.delete` is not called until completion. |
| **Success completion status** | DoD: `Completadas {N}/{N} imágenes.` | No test asserts final success `edit_text` on happy path. |
| **Regenerate after album output** | Manual verify: regen keyboard on each result | `handle_regenerate_image` with album-saved `source_file_id` regen not tested. Existing regen tests use hand-built regen, not album pipeline. |
| **Reply-edit chain after batch** | Manual verify: reply-edit on result #2 | Out of plan unit scope; no integration test that album output ref enables `handle_reply_edit` → `kie_source_ref` path. |

### Low — optional hardening

| Gap | Notes |
|-----|-------|
| **Caption on non-first message only** | `_album_prompt` sorts by `message_id`; only "caption on first message" tested. |
| **`_validate_prompt` failure** | Invalid prompt → `first_msg.answer(prompt_err)` untested. |
| **`grok_video` silent ignore** | Plan: silent return for `grok_video`; only `seedream` tested. |
| **Progress status `Editando i/N…`** | Intermediate `status_msg.edit_text` not asserted on success path (only error path checked). |
| **`process_image_result` kwargs** | `kie_meta`, `download_allowlist`, `regen_context` not asserted when mocked (8/11 tests mock it away). |
| **Exception paths** | `ReplicateError` / generic `Exception` handlers in `_process_grok_album_after_delay` untested. |
| **Single-image caption regression** | Plan Fase 1 DoD; covered in `test_video_handlers.py` / `test_video_extra.py`, not in album file. |

---

## Implementation ↔ Test Alignment

Key behaviors in `bot.py` and test coverage:

```
handle_photo_caption filter (L1002): not m.media_group_id     → test #1 ✅
handle_album dispatch grok vs faceswap vs silent              → tests #2,9,11 ✅
_album_prompt + no-caption help                               → tests #3,4 ✅
sequential generate_image, no kie_source_ref (L1273)          → tests #5,6 ✅
stop-on-error status (L1274-1278)                             → test #8 ✅
process_image_result delete_status=False (L1285)              → gap ⚠️
regen_context source_file_id per msg (L1288-1294)             → gap ⚠️
save_generation_ref (via process_image_result)                → test #7 partial ⚠️
Completadas N/N (L1297)                                       → gap ⚠️
```

---

## Recommendations

1. **Ship with current suite** — core feature contract is protected; 11/11 green.
2. **Next test PR (3–4 tests):**
   - `test_grok_album_regen_context_per_image` — assert saved refs include correct `source_file_id`, no `kie_source_ref`.
   - `test_grok_album_preserves_batch_status` — real `process_image_result`, assert `status_msg.delete` not called until final success/error.
   - `test_grok_album_success_status` — assert final `Completadas N/N imágenes.`
   - Optional: `test_grok_album_regenerate_uses_source_file_id` — end-to-end regen from album-saved ref.
3. **Keep manual smoke** from plan: Kie 3-photo album + reply-edit on result #2; faceswap media group unchanged.

---

## Summary

| Metric | Result |
|--------|--------|
| Planned tests | 11/11 present |
| Test run | 11 passed |
| Core orchestration | Well protected |
| Faceswap regression | Protected |
| kie_source_ref isolation (inputs) | Protected |
| xAI provider | Minimally protected |
| Regen / reply-edit / status UX | Gaps remain |

**Final verdict: ADEQUATE** — suite meets plan Phase 3 requirements and guards the highest-severity risks (routing bug, upload-not-ref, sequential batch, error policy, faceswap). Documented gaps are follow-up hardening, not blockers.