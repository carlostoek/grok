---
phase: quick
plan: long-prompt-collection
type: auto
feature_id: long-prompt-collection
date: 2026-06-24
impact_ref: .grok/agent-memory/impact-analyzer/long-prompt-collection.md
test_command: ./venv/bin/python -m pytest tests/ -q
---

# Plan: Long Prompt Collection & Telegram Length Limits

## Objetivo

Eliminar el límite artificial `MAX_PROMPT_LEN=1000` y alinear el bot con límites nativos de Telegram:

- **Texto:** hasta 4096 chars (`TELEGRAM_MAX_TEXT_LEN`)
- **Captions inbound > 1020** (tras `_parse_integrate_caption`): flujo de dos pasos — guardar `file_id`s → pedir prompt por mensaje de texto → generar
- **Captions outbound:** truncar a ≤ 1024 en `process_image_result` / `process_video_result`; **full prompt** en `generation_refs`

**Fuera de alcance:** foto sin caption + texto separado; `faceswap`; persistir estado de colección en `sessions.py`; cambios en APIs Kie/xAI/Replicate.

## Claves de estado (plan aprobado)

| Key | Tipo | Default | Rol |
|-----|------|---------|-----|
| `awaiting_long_prompt_text` | `bool` | `False` | Usuario debe enviar prompt como texto |
| `pending_edit_file_ids` | `list[str] \| None` | `None` | `file_id`s guardados (1 foto o álbum) |
| `pending_edit_integrate_mode` | `bool` | `False` | `/s` detectado en caption original |
| `pending_edit_is_video` | `bool` | `False` | `True` si caption era en modo `grok_video` |

**Umbral de colección:** `len(prompt) > TELEGRAM_CAPTION_COLLECT_THRESHOLD` (1020) **después** de `_parse_integrate_caption(caption)`.

---

## Fase 1 — Constantes, validación y truncado outbound

**Archivos:** `bot.py`

### Cambios exactos

1. **L52** — Reemplazar `MAX_PROMPT_LEN = 1000` por:
   ```python
   TELEGRAM_MAX_CAPTION_LEN = 1024
   TELEGRAM_CAPTION_COLLECT_THRESHOLD = 1020
   TELEGRAM_MAX_TEXT_LEN = 4096
   ```

2. **`_validate_prompt` (L242–247)** — Modificar:
   ```python
   def _validate_prompt(prompt: str, *, max_len: int = TELEGRAM_MAX_TEXT_LEN) -> str | None:
   ```
   - Min 3 (sin cambio)
   - Max `max_len`; mensaje: `máximo {max_len} caracteres`

3. **Nueva `_format_result_caption(prefix: str, prompt: str) -> str`**
   - Construir `f"<b>{prefix}:</b> {_escape_prompt(prompt)}"`
   - Truncar el **prompt escapado** para que la cadena final ≤ `TELEGRAM_MAX_CAPTION_LEN`
   - Truncar sobre texto ya escapado (HTML entities no exceden límite)
   - Sufijo opcional `…` si se truncó

4. **`process_image_result` (L2117–2120)** — Usar `_format_result_caption(prefix, prompt)` en `answer_photo` caption

5. **`process_video_result` (L2480–2483)** — Usar `_format_result_caption(prefix, prompt)` en `answer_video` caption

6. **Comentario `user_state` (L152–154)** — Documentar las 4 claves nuevas

### DoD

- [ ] `MAX_PROMPT_LEN` eliminado; tres constantes Telegram presentes
- [ ] `_validate_prompt` acepta hasta 4096 por defecto
- [ ] Captions de resultado ≤ 1024 chars (incl. prefix HTML)
- [ ] `save_generation_ref(..., prompt=prompt)` sigue guardando prompt **completo** (sin truncar)

### Tests (actualizar en Fase 6)

- `tests/test_security.py::test_validate_prompt_max_length` → umbral 4096
- `tests/test_process_video_result.py` — nuevo test truncado caption
- `tests/test_kie_provider.py::test_process_image_result_saves_generation_ref` — truncado caption + ref completo

### Comando

```bash
./venv/bin/python -m pytest tests/test_security.py::test_validate_prompt_max_length \
  tests/test_process_video_result.py tests/test_kie_provider.py::test_process_image_result_saves_generation_ref -v
```

---

## Fase 2 — Estado y helpers de colección

**Archivos:** `bot.py`

### Cambios exactos

1. **`get_user_state` (L464–473)** — Añadir defaults:
   ```python
   "awaiting_long_prompt_text": False,
   "pending_edit_file_ids": None,
   "pending_edit_integrate_mode": False,
   "pending_edit_is_video": False,
   ```

2. **Nuevas funciones** (cerca de `_parse_integrate_caption`):

   | Función | Firma / comportamiento |
   |---------|------------------------|
   | `_prompt_needs_long_text_collection` | `(prompt: str) -> bool` → `len(prompt) > TELEGRAM_CAPTION_COLLECT_THRESHOLD` |
   | `_set_long_prompt_collection` | `(state, *, file_ids: list[str], integrate_mode: bool, is_video: bool)` → set las 4 claves |
   | `_clear_long_prompt_collection` | `(state)` → reset a defaults |
   | `_is_awaiting_long_prompt_text` | `(state) -> bool` → `state.get("awaiting_long_prompt_text")` |

3. **Nueva `_long_prompt_collection_reply(message, *, is_video: bool, n_photos: int)`**
   - Mensaje en español (tono existente): caption demasiado largo; envía el prompt como **mensaje de texto**
   - Diferenciar i2v vs edit; mencionar álbum si `n_photos > 1`

### DoD

- [ ] Estado inicializado en primer `get_user_state`
- [ ] Helpers set/clear/is funcionan de forma simétrica
- [ ] Sin persistencia en `sessions.py`

### Comando

```bash
./venv/bin/python -m pytest tests/test_long_prompt_collection.py::test_long_prompt_state_helpers -v
```

---

## Fase 3 — Disparar colección en caption inbound (foto + álbum)

**Archivos:** `bot.py`

### Cambios exactos

1. **`handle_photo_caption` (L1105–1110)** — Reordenar tras `_parse_integrate_caption`:
   ```python
   integrate_mode, prompt = _parse_integrate_caption(message.caption)
   if _prompt_needs_long_text_collection(prompt):
       _set_long_prompt_collection(
           state,
           file_ids=[message.photo[-1].file_id],
           integrate_mode=integrate_mode,
           is_video=(get_model(message.from_user.id)["key"] == "grok_video"),
       )
       await _long_prompt_collection_reply(message, is_video=..., n_photos=1)
       return
   prompt_err = _validate_prompt(prompt)
   ```
   - **No** llamar `_validate_prompt` max cuando se dispara colección (caption puede ser > 4096 en teoría; Telegram limita caption a 1024, pero el umbral es 1020)
   - Faceswap / `integrate_ref_awaiting` sin cambios (rutas anteriores)

2. **`_process_grok_album_after_delay` (L1380–1384)** — Mismo patrón tras `_parse_integrate_caption`:
   ```python
   integrate_mode, prompt = _parse_integrate_caption(raw_caption)
   if _prompt_needs_long_text_collection(prompt):
       file_ids = [msg.photo[-1].file_id for msg in messages if msg.photo]
       state = get_user_state(first_msg.from_user.id)
       _set_long_prompt_collection(state, file_ids=file_ids, integrate_mode=integrate_mode, is_video=False)
       await _long_prompt_collection_reply(first_msg, is_video=False, n_photos=len(file_ids))
       return
   prompt_err = _validate_prompt(prompt)
   ```

3. **Sobrescritura:** si usuario envía nueva foto/álbum con caption largo mientras `awaiting_long_prompt_text`, **reemplazar** `pending_edit_file_ids` y flags (no acumular)

### DoD

- [ ] Caption > 1020 en foto única → pide texto; no llama `generate_image` / `_do_generate_video`
- [ ] Caption > 1020 en álbum grok → guarda todos los `file_id`s; pide texto
- [ ] `/s` largo guarda `pending_edit_integrate_mode=True`
- [ ] `grok_video` guarda `pending_edit_is_video=True`
- [ ] Caption ≤ 1020 sigue flujo directo (sin regresión)

### Comando

```bash
./venv/bin/python -m pytest tests/test_long_prompt_collection.py -k "trigger or album" -v
```

---

## Fase 4 — Consumir colección en `handle_text` + helpers de generación compartidos

**Archivos:** `bot.py`

### Cambios exactos

1. **Nueva `_process_single_photo_edit`** — Extraer lógica de `handle_photo_caption` (L1112–1173):
   ```python
   async def _process_single_photo_edit(
       message: types.Message,
       prompt: str,
       file_id: str,
       *,
       integrate_mode: bool = False,
       is_video: bool = False,
       user_id: int | None = None,
   ) -> None:
   ```
   - Descargar foto por `file_id` (`_download_telegram_photo` con photo mock o helper `_download_photo_by_file_id`)
   - Si `is_video`: `_do_generate_video(...)`
   - Si no: `integrate_mode` → `_validate_integrate_prerequisites` + `generate_image` + `process_image_result`
   - Mismo `regen_context` que hoy (`source_file_id=file_id`, `integrate_mode`)

2. **Nueva `_process_album_edit_from_file_ids`** — Extraer loop de `_process_grok_album_after_delay` (L1399–1449):
   ```python
   async def _process_album_edit_from_file_ids(
       anchor_message: types.Message,
       prompt: str,
       file_ids: list[str],
       *,
       integrate_mode: bool = False,
       user_id: int | None = None,
   ) -> None:
   ```

3. **Nueva `_complete_long_prompt_collection(message, prompt: str)`**
   - Leer state; si no `awaiting_long_prompt_text` → return False
   - `_validate_prompt(prompt)`; si error → answer y return True (mantener state)
   - Si `pending_edit_is_video` y len(file_ids)==1 → `_process_single_photo_edit(..., is_video=True)`
   - Elif len(file_ids)==1 → `_process_single_photo_edit(...)`
   - Else → `_process_album_edit_from_file_ids(...)`
   - `_clear_long_prompt_collection(state)`; return True

4. **`handle_text` (L940)** — **Primera** rama (antes de faceswap / confirm):
   ```python
   if _is_awaiting_long_prompt_text(state):
       await _complete_long_prompt_collection(message, message.text.strip())
       return
   ```
   - **No** entrar a `pending_prompt` confirm cuando se completa colección
   - Texto normal grok/grok_video sigue con confirm flow

5. **Refactor `handle_photo_caption`** — Delegar a `_process_single_photo_edit` en rama directa (caption ≤ 1020)

6. **Refactor `_process_grok_album_after_delay`** — Delegar loop a `_process_album_edit_from_file_ids`

### DoD

- [ ] Follow-up texto válido completa generación (single edit, i2v, álbum batch)
- [ ] Follow-up inválido (< 3 o > 4096) responde error; state se mantiene
- [ ] `handle_text` con colección pendiente **no** muestra keyboard Confirmar/Cancelar
- [ ] Todos los providers (xAI, Kie, Replicate) en rutas existentes — sin branching nuevo por provider
- [ ] `handle_reply_edit` / `handle_regenerate_image` aceptan hasta 4096 (solo cambio de `_validate_prompt`)

### Comando

```bash
./venv/bin/python -m pytest tests/test_long_prompt_collection.py -v
```

---

## Fase 5 — Limpieza en model switch

**Archivos:** `config_flow.py`, `bot.py` (opcional)

### Cambios exactos

1. **`config_flow._activate_model` (L399–403)** — Tras `pending_prompt = None`:
   ```python
   state["awaiting_long_prompt_text"] = False
   state["pending_edit_file_ids"] = None
   state["pending_edit_integrate_mode"] = False
   state["pending_edit_is_video"] = False
   ```
   Alternativa: llamar `deps` helper si se expone `_clear_long_prompt_collection` vía `_CONFIG_DEPS` — preferir inline para no ampliar deps.

2. **Opcional `handle_confirm_generation` cancel (`confirm:no`)** — No requerido por plan; model switch es suficiente.

### DoD

- [ ] Cambio de modelo limpia las 4 claves + `pending_prompt`
- [ ] Tests de `test_video_handlers` y `test_config_flow` actualizados

### Comando

```bash
./venv/bin/python -m pytest tests/test_config_flow.py tests/test_video_handlers.py::test_model_switch_clears_pending_prompt -v
```

---

## Fase 6 — Tests (nuevo + actualizaciones)

**Archivos nuevos:** `tests/test_long_prompt_collection.py`

**Archivos a actualizar:**

| Archivo | Cambio |
|---------|--------|
| `tests/test_security.py` | `TELEGRAM_MAX_TEXT_LEN` en max-length test |
| `tests/test_video_extra.py` | `test_text_prompt_too_long_rejected` → 4096+1 |
| `tests/test_album_batch.py` | Caption 1021+ → colección, no error de validación |
| `tests/test_video_handlers.py` | i2v long caption → colección; model switch limpia state |
| `tests/test_integrate_ref.py` | `/s` + caption largo → `pending_edit_integrate_mode` |
| `tests/test_config_flow.py` | Model switch limpia colección |
| `tests/test_process_video_result.py` | Truncado caption largo |
| `tests/test_kie_provider.py` | Caption truncado + prompt completo en ref |

### Estructura `tests/test_long_prompt_collection.py`

```python
"""Long prompt collection: caption > 1020 → text follow-up."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import bot
import sessions
```

### Fixtures / helpers

- Reutilizar `sessions_file`, `generation_refs_file` de `conftest.py`
- `_make_user_message(**kwargs)` — patrón `test_video_handlers`
- `_make_photo_caption_message(caption, file_id="p1", model="grok")`
- `_make_album_messages(n, caption)` + `album_tasks` / `no_sleep` de `test_album_batch.py`

### Tests requeridos

| Test | Asserts |
|------|---------|
| `test_long_prompt_state_helpers` | set/clear/is round-trip |
| `test_prompt_needs_collection_at_1021` | 1020 OK; 1021 True |
| `test_photo_caption_over_1020_triggers_collection` | state set; `generate_image` no llamado |
| `test_photo_caption_under_1020_direct_generate` | sin state; genera |
| `test_grok_video_long_caption_sets_is_video` | `pending_edit_is_video=True` |
| `test_album_long_caption_saves_all_file_ids` | N file_ids; reply pide texto |
| `test_integrate_long_caption_saves_integrate_mode` | `/s` + 1021 chars → `pending_edit_integrate_mode=True` |
| `test_handle_text_completes_single_edit` | follow-up → `generate_image` con prompt texto |
| `test_handle_text_completes_i2v` | `pending_edit_is_video` → `_do_generate_video` |
| `test_handle_text_completes_album_batch` | N file_ids → N × `generate_image` |
| `test_handle_text_invalid_prompt_keeps_state` | prompt corto; state intacto |
| `test_handle_text_collection_skips_confirm_flow` | grok + pending → sin inline keyboard |
| `test_model_switch_clears_long_prompt_state` | 4 claves reset |
| `test_format_result_caption_truncates_long_prompt` | len ≤ 1024 |
| `test_text_prompt_accepts_4096_chars` | `_validate_prompt` OK en 4096 |

### DoD

- [ ] `tests/test_long_prompt_collection.py` con ≥ 14 tests
- [ ] Tests legacy actualizados (no referencias a `MAX_PROMPT_LEN`)
- [ ] Suite completa verde

### Comandos

```bash
# Foco feature
./venv/bin/python -m pytest tests/test_long_prompt_collection.py -v

# Suite completa
./venv/bin/python -m pytest tests/ -q
```

---

## Riesgos y mitigaciones

| ID | Riesgo | Sev. | Mitigación |
|----|--------|------|------------|
| R1 | `handle_text` precedence: confirm vs colección | **High** | Colección **primera** rama; test `test_handle_text_collection_skips_confirm_flow` |
| R2 | HTML entities expanden caption outbound | **Medium** | Truncar sobre `_escape_prompt` output en `_format_result_caption` |
| R3 | Usuario abandona flujo (state stale) | **Medium** | Clear en `_activate_model`; nueva foto reemplaza pending |
| R4 | `pending_prompt` + colección simultáneos | **Low** | Model switch limpia ambos; colección consume antes de confirm |
| R5 | Álbum integrate `/s` + caption largo | **Medium** | `pending_edit_integrate_mode`; test en `test_integrate_ref` / collection |
| R6 | `file_id` expiry antes de follow-up | **Low** | UX: reenviar foto; mismo riesgo que regen con `source_file_id` |
| R7 | Regresión álbum batch caption corto | **Medium** | `test_album_batch` sin cambio de comportamiento ≤ 1020 |
| R8 | Quitar cap 1000 expone errores provider | **Low** | Pre-existente; errores ya surfaced por backends |
| R9 | Bot restart pierde colección | **Low** | Aceptable (igual que `pending_prompt`) |
| R10 | Duplicar lógica foto vs colección | **Medium** | Refactor `_process_single_photo_edit` + `_process_album_edit_from_file_ids` |

---

## Instrucciones para gsd-executor

### Contexto del repo

- **Framework:** aiogram 3.x; handlers `@dp.message(lambda m: ...)` en `bot.py`; **primer match gana**
- **Estado:** `get_user_state(user_id)` in-memory; hidratado desde `sessions.py` en primer acceso — **no** persistir claves de colección
- **Integrate `/s`:** `_parse_integrate_caption` → `(integrate_mode, prompt)`; prereqs vía `_validate_integrate_prerequisites` + `_load_integrate_ref_bytes`
- **Álbum grok:** `_album_cache` + `_album_lock` + `ALBUM_COLLECT_DELAY`; `_process_grok_album_after_delay` — **ortogonal** a `awaiting_long_prompt_text` (evaluar longitud **después** de recolectar fotos)

### Patrones obligatorios

1. **Umbral** — Siempre evaluar `len(prompt) > 1020` **después** de `_parse_integrate_caption`, no sobre caption crudo
2. **State keys** — Usar exactamente: `awaiting_long_prompt_text`, `pending_edit_file_ids`, `pending_edit_integrate_mode`, `pending_edit_is_video`
3. **`handle_text` ordering:**
   ```
   awaiting_long_prompt_text → _complete_long_prompt_collection → return
   faceswap guard → return
   _validate_prompt (max 4096)
   grok/grok_video → pending_prompt confirm
   seedream → _do_generate_text
   ```
4. **Generación** — Reutilizar `generate_image`, `_do_generate_video`, `process_image_result`, `_build_image_regen_context` — backends reciben prompt **completo**
5. **Outbound** — Solo caption truncado; `sessions.save_generation_ref(..., prompt=full)`
6. **Tests** — `pytest.mark.asyncio`; patch `bot.asyncio.sleep` para álbum; mocks `AsyncMock` en `message.answer` / `generate_image`

### Orden de implementación

1. Fase 1 (constantes + `_format_result_caption` + result handlers)
2. Fase 2 (state + helpers)
3. Fase 3 (triggers en `handle_photo_caption` + `_process_grok_album_after_delay`)
4. Fase 4 (refactor + `_complete_long_prompt_collection` + `handle_text`)
5. Fase 5 (`config_flow._activate_model`)
6. Fase 6 (tests nuevos + actualizar legacy)
7. `./venv/bin/python -m pytest tests/test_long_prompt_collection.py -v`
8. `./venv/bin/python -m pytest tests/ -q`

### No hacer

- No modificar `sessions.py` schema
- No cambiar `_generate_kie`, `_generate_xai`, `_generate_replicate`
- No añadir colección para `faceswap`, `handle_reply_edit`, o foto sin caption
- No truncar prompt en `generation_refs` / `regen_context`
- No usar `pending_caption_collect` ni otras claves distintas al plan aprobado

### Verificación manual (opcional)

- Foto grok + caption 1021 chars → bot pide texto → texto 2000 chars → edit OK; caption resultado truncado; regen usa prompt completo
- Álbum 3 fotos + caption largo → follow-up → 3 edits
- `grok_video` caption largo → follow-up → i2v
- `/s` + caption largo con referencia configurada → integrate edit tras follow-up
- `/config` cambio modelo durante espera → state limpio

---

## Checklist global (Definition of Done)

- [ ] Fase 1: constantes Telegram + truncado outbound
- [ ] Fase 2: 4 state keys + helpers
- [ ] Fase 3: triggers foto + álbum (> 1020 post-`/s`)
- [ ] Fase 4: consumo en `handle_text` + refactors compartidos
- [ ] Fase 5: model switch cleanup
- [ ] Fase 6: `tests/test_long_prompt_collection.py` + legacy updates
- [ ] `pytest tests/test_long_prompt_collection.py -v` verde
- [ ] `pytest` suite completa verde