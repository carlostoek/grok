# Arch-Enforcer Audit: Long Prompt Collection

**Feature ID:** `long-prompt-collection`  
**Date:** 2026-06-24  
**Auditor:** arch-enforcer  
**Plan:** `.planning/quick/long-prompt-collection/PLAN.md`  
**Test run:** `./venv/bin/python -m pytest tests/test_long_prompt_collection.py -q` → **15 passed**  
**Suite:** `./venv/bin/python -m pytest tests/ -q` → **255 passed, 2 skipped**

---

## Verdict

**PASS WITH NOTES**

The long-prompt-collection feature is architecturally aligned with the approved plan: correct `handle_text` precedence, exact state key names, in-memory-only collection state, post-`/s` threshold evaluation, shared generation helpers, outbound caption truncation with full prompt in `generation_refs`, and model-switch cleanup. No critical violations block merge for the collection feature itself.

**Collateral note:** `sessions.py` was modified in the same working tree for `integrate_ref_path` / `set_integrate_ref` (unrelated to collection persistence). This conflicts with plan §No hacer (“No modificar `sessions.py` schema”) but does **not** persist collection keys.

---

## Mandatory Checks

| Check | Result | Evidence |
|-------|--------|----------|
| **`handle_text` ordering** | ✅ PASS | `bot.py` L1017–1058: `awaiting_long_prompt_text` → `_complete_long_prompt_collection` → `return`; then faceswap guard; then `_validate_prompt`; then grok/grok_video confirm; else `_do_generate_text`. Matches plan §Patrones obligatorios #3. |
| **State key names match plan** | ✅ PASS | Exact keys: `awaiting_long_prompt_text`, `pending_edit_file_ids`, `pending_edit_integrate_mode`, `pending_edit_is_video`. Defaults in `get_user_state` (L540–543); documented in `user_state` comment (L157–158). No `pending_caption_collect` or aliases. |
| **No scope creep (collection)** | ✅ PASS | Collection limited to caption > 1020 on photo/album inbound; no faceswap collection; no foto-sin-caption path; no provider branching. |
| **No `sessions.py` changes (collection)** | ✅ PASS | Collection state not persisted; grep shows no long-prompt keys in `sessions.py`. |
| **No `sessions.py` changes (diff)** | ⚠️ NOTE | `sessions.py` diff adds `integrate_ref_path` + `set_integrate_ref` — integrate-ref schema, not collection. Plan forbids any `sessions.py` schema edits. |
| **Regen keeps full prompt** | ✅ PASS | `process_image_result` saves `prompt=prompt` (full) at L2314 while caption uses `_format_result_caption` (truncated) at L2298. `test_process_image_result_truncates_caption_keeps_full_ref` asserts `ref["regen"]["prompt"] == long_prompt`. `handle_regenerate_image` uses `_validate_prompt` default `TELEGRAM_MAX_TEXT_LEN` (4096). |

---

## Phase-by-Phase Compliance

### Fase 1 — Constants, validation, outbound truncation

| Requirement | Status |
|-------------|--------|
| `MAX_PROMPT_LEN` removed | ✅ L52–54 |
| `TELEGRAM_MAX_CAPTION_LEN`, `TELEGRAM_CAPTION_COLLECT_THRESHOLD`, `TELEGRAM_MAX_TEXT_LEN` | ✅ |
| `_validate_prompt(..., max_len=TELEGRAM_MAX_TEXT_LEN)` | ✅ L246–251 |
| `_format_result_caption` truncates escaped prompt ≤ 1024 | ✅ L254–264 |
| `process_image_result` / `process_video_result` use `_format_result_caption` | ✅ L2298, L2660 |
| `save_generation_ref` stores full prompt | ✅ L2314 |

### Fase 2 — State and helpers

| Helper | Status |
|--------|--------|
| `_prompt_needs_long_text_collection` | ✅ L276–277 |
| `_set_long_prompt_collection` | ✅ L280–290 |
| `_clear_long_prompt_collection` | ✅ L293–297 |
| `_is_awaiting_long_prompt_text` | ✅ L300–301 |
| `_long_prompt_collection_reply` | ✅ L304–323 (Spanish UX, i2v/album differentiation) |

### Fase 3 — Inbound triggers

| Path | Status |
|------|--------|
| `handle_photo_caption` — post `_parse_integrate_caption`, before `_validate_prompt` | ✅ L1379–1390 |
| `_process_grok_album_after_delay` — same pattern | ✅ L1611–1626 |
| Skips `generate_image` / `_do_generate_video` on collection | ✅ Tests confirm |
| `/s` → `pending_edit_integrate_mode` | ✅ `test_integrate_long_caption_saves_integrate_mode` |
| `grok_video` → `pending_edit_is_video` | ✅ `test_grok_video_long_caption_sets_is_video` |
| Overwrite pending on new photo/album | ✅ `_set_long_prompt_collection` replaces all 4 keys (no accumulate); **no dedicated test** |

### Fase 4 — Consumption and refactors

| Item | Status |
|------|--------|
| `_process_single_photo_edit` | ✅ L1161–1230 |
| `_process_album_edit_from_file_ids` | ✅ L1233–1315 |
| `_complete_long_prompt_collection` | ✅ L1318–1356 |
| Invalid follow-up keeps state | ✅ L1323–1326, test `test_handle_text_invalid_prompt_keeps_state` |
| Collection skips confirm keyboard | ✅ `test_handle_text_collection_skips_confirm_flow` |
| `regen_context` with `source_file_id`, `integrate_mode` | ✅ L1211–1218, L1294–1301 |

### Fase 5 — Model switch cleanup

| Item | Status |
|------|--------|
| `config_flow._activate_model` clears 4 keys + `pending_prompt` | ✅ `config_flow.py` L401–405 |
| Tests | ✅ `test_model_switch_clears_long_prompt_state`, `test_model_switch_clears_long_prompt_collection` |

### Fase 6 — Tests

| Item | Status |
|------|--------|
| `tests/test_long_prompt_collection.py` | ✅ 15 tests (plan ≥ 14) |
| Legacy updates | ✅ `test_security`, `test_video_extra`, `test_album_batch`, `test_video_handlers`, `test_config_flow`, `test_process_video_result`, `test_kie_provider`, `test_integrate_ref` |
| Full suite green | ✅ 255 passed |

---

## Handler Ordering (verified)

```
handle_text:
  1. bot command guard (pre-existing)
  2. awaiting_long_prompt_text → _complete_long_prompt_collection → return   ← plan R1
  3. faceswap guard → return
  4. _validate_prompt (max 4096)
  5. grok/grok_video → pending_prompt + confirm keyboard
  6. seedream → _do_generate_text

handle_photo_caption (grok path):
  integrate_ref_awaiting → faceswap → _parse_integrate_caption
  → _prompt_needs_long_text_collection → _set_long_prompt_collection → return
  → _validate_prompt → _process_single_photo_edit
```

Threshold always evaluated **after** `_parse_integrate_caption`, not on raw caption. ✅

---

## Risk Mitigations (plan table)

| Risk | Mitigation present? |
|------|---------------------|
| R1 `handle_text` precedence | ✅ First branch + test |
| R2 HTML entities in outbound caption | ✅ Truncate on `_escape_prompt` output |
| R3 Stale state | ✅ Model switch clear; new photo overwrites via `_set_long_prompt_collection` |
| R4 `pending_prompt` + collection | ✅ Collection consumes first; model switch clears both |
| R5 Album `/s` + long caption | ✅ `pending_edit_integrate_mode` + tests |
| R10 Duplicated foto/album logic | ✅ `_process_single_photo_edit` + `_process_album_edit_from_file_ids` |

---

## Scope Creep Assessment

### In-plan (not creep)

- `integrate_mode` / `/s` support in collection flow — explicitly in plan Fase 3–4 and tests.
- `_parse_integrate_caption` placement near collection helpers — per plan Fase 2.

### Out-of-plan collateral in same diff

| Change | File | Relation to collection |
|--------|------|------------------------|
| `integrate_ref_path` persistence | `sessions.py` | Prerequisite for `/s` integrate edits; **not** collection state |
| `set_integrate_ref`, `_integrate_ref_path`, `_load_integrate_ref_bytes` | `bot.py` | Integrate-ref feature infrastructure |
| `INTEGRATE_REFS_DIR`, `/cambiar_referencia` UX | `bot.py` | Separate integrate-ref surface |

Collection feature does **not** depend on persisting collection keys to `sessions.py`. The `sessions.py` edit is integrate-ref collateral and should be tracked separately from collection architecture.

### Correctly excluded (per plan)

- ❌ No collection for faceswap, `handle_reply_edit`, photo-without-caption
- ❌ No changes to `_generate_kie`, `_generate_xai`, `_generate_replicate`
- ❌ No truncation of prompt in `regen_context` / `generation_refs`

---

## Critical Violations

**None** for the long-prompt-collection architecture.

---

## Non-Blocking Notes

1. **`sessions.py` schema edit** — `integrate_ref_path` added despite plan §No hacer. Collection keys correctly remain in-memory only; flag for release hygiene if this PR is collection-only.
2. **Overwrite-while-awaiting untested** — Plan Fase 3 DoD item 3 (new photo/album replaces pending `file_id`s) is implemented by `_set_long_prompt_collection` overwrite semantics but lacks an explicit test.
3. **Video regen** — `process_video_result` does not call `save_generation_ref` (pre-existing); only image path tested for full-prompt-in-ref. Acceptable per plan scope (image caption truncation + ref).
4. **`_complete_long_prompt_collection` return type** — Returns `bool` per plan; callers in `handle_text` ignore return value (harmless).

---

## Summary for Merge

| Area | Assessment |
|------|------------|
| Collection state design | Compliant |
| Handler precedence | Compliant |
| Generation path reuse | Compliant |
| Outbound truncation / inbound full prompt | Compliant |
| Model switch cleanup | Compliant |
| Test coverage | Adequate (15/14 required) |
| `sessions.py` diff | Collateral integrate-ref; not collection-breaking |

**Recommendation:** Approve long-prompt-collection architecture. Address `sessions.py` integrate-ref bundling in PR description or split if enforcing strict single-feature diffs.