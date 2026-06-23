---
phase: quick
plan: album-batch-kie-imagine
type: auto
feature_id: album-batch-kie-imagine
date: 2026-06-23
impact_ref: .grok/agent-memory/impact-analyzer/album-batch-kie-imagine.md
test_command: ./venv/bin/python -m pytest tests/ -q
---

# Plan: Album Batch Image Editing (Grok Imagine / Kie primary)

## Objetivo

Permitir edición batch i2i cuando el usuario envía un **álbum de Telegram** (media group) con **caption**, con `model == "grok"` activo. Flujo: recolectar fotos → extraer caption compartido → loop secuencial `generate_image` → `process_image_result` por imagen. Corregir el bug de routing donde `handle_photo_caption` captura el primer ítem del álbum.

**Fuera de alcance:** `grok_video` album i2v, `seedream` album batch, tareas Kie en paralelo, `kie_source_ref` como input de batch, enviar resultados como `reply_media_group`.

## Decisiones del planner (open questions resueltas)

| Pregunta | Decisión |
|----------|----------|
| Error policy | **Parar en primer fallo**; status final indica índice y error (`3/5 completadas; error en imagen 4: …`) |
| grok_video / seedream | **Return silencioso** en `handle_album` (sin mensaje extra) |
| Status UX | **Un solo `status_msg` de batch** que evoluciona `Editando i/N…`; no pasarlo a `process_image_result` |
| Concurrencia | **Sin batch-guard** en v1; no tocar `sessions.py` |
| `ALBUM_COLLECT_DELAY` | Mantener **1.0s** (mismo riesgo que faceswap; bump opcional post-v1) |
| Confirmación de costo | **No** — `handle_photo_caption` tampoco confirma para grok single-image |

---

## Fase 1 — Fix routing `handle_photo_caption` (prerequisito)

**Archivos:** `bot.py`

### Cambios exactos

1. **L1001** — Actualizar filtro del handler:
   ```python
   @dp.message(lambda m: m.photo and m.caption and not m.media_group_id)
   ```

### DoD

- [ ] Ítems de álbum con caption **no** disparan `handle_photo_caption`
- [ ] Foto única con caption sigue funcionando (grok, grok_video, faceswap)

### Tests (añadir en Fase 3, verificar aquí)

- `test_handle_photo_caption_ignores_media_group`

### Comando

```bash
./venv/bin/python -m pytest tests/test_album_batch.py::test_handle_photo_caption_ignores_media_group -q
```

---

## Fase 2 — Orquestación Grok album en `handle_album`

**Archivos:** `bot.py`

### Cambios exactos

1. **Refactorizar `handle_album` (L1184–1211)** — Despachar por modelo al inicio:
   - `faceswap` → lógica existente (sin cambios funcionales)
   - `grok` → append a `_album_cache` + `asyncio.create_task(_process_grok_album_after_delay(...))`
   - otros modelos → `return` silencioso

2. **Nueva función `_album_prompt(messages)`** (cerca de L1179):
   ```python
   def _album_prompt(messages: list[types.Message]) -> str | None:
       for msg in sorted(messages, key=lambda m: m.message_id):
           if msg.caption and msg.caption.strip():
               return msg.caption.strip()
       return None
   ```

3. **Nueva función `_process_grok_album_after_delay(cache_key, first_msg)`**:
   - `await asyncio.sleep(ALBUM_COLLECT_DELAY)`
   - Pop mensajes bajo `_album_lock`; sort por `message_id`
   - `prompt = _album_prompt(messages)`; si vacío → responder con texto de `handle_photo_no_caption` (L1082–1086)
   - `_validate_prompt(prompt)`; si error → `first_msg.answer(prompt_err)`
   - `model = get_model(first_msg.from_user.id)`
   - `status_msg = await first_msg.reply(f"Editando 0/{N} imágenes con {model['name']} ({_prov_label(...)} )...")`
   - Loop `for i, msg in enumerate(messages, 1)`:
     - `await status_msg.edit_text(f"Editando {i}/{N} imágenes con …")`
     - `image_data = await _download_telegram_photo(msg.photo[-1])`
     - `output, err, kie_meta = await generate_image(model, prompt, image_data)` — **sin** `kie_source_ref`
     - Si `err`: `status_msg.edit_text(f"{i-1}/{N} completadas; error en imagen {i}: {err}")`; `break`
     - `await process_image_result(output, prompt, status_msg, first_msg, "Edit", delete_status=False, download_allowlist=..., kie_meta=..., regen_context=_build_image_regen_context(..., source_file_id=msg.photo[-1].file_id))`
   - Al terminar OK: `status_msg.edit_text(f"Completadas {N}/{N} imágenes.")`
   - Manejar `ReplicateError` y `Exception` como en `handle_photo_caption`

4. **`process_image_result` (L1798)** — Añadir parámetro opcional:
   ```python
   async def process_image_result(..., *, delete_status: bool = True, ...):
       ...
       if delete_status:
           await status_msg.delete()
   ```
   Para batch: pasar `delete_status=False` y un `status_msg` dummy o reutilizar el de batch (no se borra hasta el final).

5. **`cmd_start` (L578–584)** — Añadir línea para grok (rama `else`):
   ```
   También puedes enviar un <b>álbum de fotos con caption</b> para editarlas todas con el mismo prompt.
   ```

### Patrones a seguir

- Reutilizar `_album_cache`, `_album_lock`, `ALBUM_COLLECT_DELAY` (mismo key `(chat_id, media_group_id)`)
- Orden estable: `sorted(messages, key=lambda m: m.message_id)`
- `first_msg` como anchor para `reply`/`answer_photo` (como faceswap usa `first_msg.reply_media_group`)
- `regen_context` con `source_file_id` de cada foto del álbum (Telegram), no `kie_source_ref`

### DoD

- [ ] `handle_album` procesa álbumes grok con caption
- [ ] Loop secuencial: download → `generate_image` → `process_image_result` por imagen
- [ ] Mismo prompt para todas las imágenes
- [ ] Kie usa `image_data` upload (nunca `kie_source_ref` en inputs)
- [ ] xAI y Replicate funcionan vía routing existente de `generate_image`
- [ ] Faceswap album **sin regresión**
- [ ] Álbum sin caption → mensaje de ayuda (no ignore silencioso)
- [ ] Status batch no se borra hasta completar/fallar
- [ ] Cada output guarda `generation_refs` para reply-edit posterior

### Comando

```bash
./venv/bin/python -m pytest tests/test_album_batch.py -q
```

---

## Fase 3 — Tests unitarios

**Archivos:** `tests/test_album_batch.py` (nuevo)

### Estructura del archivo

```python
"""Album batch editing for Grok Imagine (media group + caption)."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import bot
import sessions
```

### Fixtures / helpers

- Reutilizar `sessions_file`, `generation_refs_file` de `conftest.py`
- Reutilizar patrón `no_sleep` de `tests/test_kie_provider.py` (patch `bot.asyncio.sleep`)
- Helper `_make_album_message(**kwargs)` — extender patrón de `test_video_handlers._make_user_message`:
  - `media_group_id`, `photo`, `caption`, `message_id`, `chat.id`, `from_user.id`
  - `answer`, `reply`, `edit_text` como `AsyncMock`

### Tests requeridos

| Test | Asserts |
|------|---------|
| `test_handle_photo_caption_ignores_media_group` | `media_group_id` set → `generate_image` **no** llamado |
| `test_grok_album_collects_messages` | N llamadas a `handle_album` → tras delay, N procesadas |
| `test_grok_album_extracts_caption_from_first_message` | Caption solo en msg[0] → prompt correcto |
| `test_grok_album_requires_caption` | Sin caption → help text, sin `generate_image` |
| `test_grok_album_sequential_kie_calls` | `generate_image` N veces, mismo prompt, distinto `image_data` |
| `test_grok_album_kie_uses_upload_not_ref` | `kie_source_ref` nunca en kwargs de `generate_image` |
| `test_grok_album_saves_generation_ref_per_output` | N × `process_image_result` → N refs en `generation_refs.json` |
| `test_grok_album_stops_on_error` | 2ª llamada falla → loop para, status parcial |
| `test_faceswap_album_unchanged` | `model == faceswap"` → `_process_batch_replicate_sync` (no `generate_image`) |
| `test_grok_album_xai_provider` | `provider == "xai"` → `generate_image` por imagen |
| `test_handle_album_ignored_for_seedream` | `model == seedream"` → sin batch processing |

### Mocks típicos

```python
with patch("bot.asyncio.sleep", new=AsyncMock()):
    with patch.object(bot, "generate_image", new_callable=AsyncMock, return_value=(["url"], None, {"task_id": "t1", "index": 0, "provider": "kie"})):
        with patch.object(bot, "_download_telegram_photo", new_callable=AsyncMock, return_value=b"bytes"):
            with patch.object(bot, "process_image_result", new_callable=AsyncMock):
                ...
```

Para faceswap: patch `_process_batch_replicate_sync` y `download.download_telegram_photo`.

### DoD

- [ ] 11 tests listados arriba pasan
- [ ] Suite completa verde

### Comando final

```bash
./venv/bin/python -m pytest tests/ -q
```

---

## Riesgos y mitigaciones

| ID | Riesgo | Severidad | Mitigación en plan |
|----|--------|-----------|-------------------|
| R1 | `handle_photo_caption` roba primer ítem del álbum | **Critical** | Fase 1: filtro `not media_group_id` |
| R2 | Batch largo bloquea sesión (N × poll Kie) | **High** | Status `Editando i/N`; documentar en `/start`; máx 10 fotos (límite Telegram) |
| R3 | Costo / rate limits Kie en N tareas | **Medium** | Secuencial por diseño; sin paralelismo |
| R4 | Fallo parcial UX inconsistente | **Medium** | Parar en error; status `X/N completadas; error en imagen Y` |
| R5 | `process_image_result` borra status de batch | **Medium** | `delete_status=False` en Fase 2 |
| R6 | Race en `_album_cache` (dos álbumes) | **Low** | Key incluye `media_group_id` — OK |
| R7 | Álbum sin caption → ignore silencioso | **Medium** | Mensaje de ayuda de `handle_photo_no_caption` |
| R8 | Validación tamaño falla en un ítem | **Low** | Error por imagen vía `_generate_kie` / `_generate_xai` |
| R9 | Regresión faceswap | **Medium** | `test_faceswap_album_unchanged` + no tocar rama faceswap |
| R10 | Orden no determinista | **Low** | Sort por `message_id` |

---

## Instrucciones para gsd-executor

### Contexto del repo

- **Framework:** aiogram 3.x; handlers registrados en orden — **primer match gana** (no hay `SkipHandler`)
- **Estado:** `get_user_state(user_id)` in-memory; persistencia en `sessions.py` solo para config/refs — **no modificar schema** salvo necesidad explícita
- **Modelo grok:** `state["model"] == "grok"`; config provider vía `get_model()` → `kie` / `xai` / `replicate`

### Patrones obligatorios

1. **Handlers** — Decoradores `@dp.message(lambda m: ...)` en `bot.py`; seguir estilo de `handle_photo_caption` / `handle_album`
2. **Album cache** — Mismo patrón faceswap: lock → append → `create_task` con delay → pop y procesar
3. **Generación** — Siempre `generate_image(model, prompt, image_data)` para batch; **nunca** `kie_source_ref` en inputs de álbum
4. **Resultados** — `process_image_result` + `sessions.save_generation_ref` (automático dentro de la función)
5. **Tests** — `pytest.mark.asyncio`; fixtures `sessions_file`, `generation_refs_file`; patch `bot.asyncio.sleep` para album delay; importar `bot` después de env vars (ver `conftest.py`)

### Orden de implementación

1. Fase 1 (filtro) — commit lógico pequeño, test de regresión
2. Fase 2 (`delete_status` → `_album_prompt` → `_process_grok_album_after_delay` → refactor `handle_album`)
3. Fase 3 (archivo `tests/test_album_batch.py` completo)
4. `./venv/bin/python -m pytest tests/ -q` — debe quedar verde

### No hacer

- No cambiar `_generate_kie`, `_generate_xai`, `_resolve_reply_kie_ref`
- No añadir album batch para `grok_video` o `seedream`
- No enviar resultados como `reply_media_group` (cada imagen individual)
- No paralelizar tareas Kie
- No tocar `sessions.py` salvo bug blocker

### Verificación manual (opcional post-merge)

- Kie: álbum 3 fotos + caption → 3 edits; reply-edit en resultado #2 funciona
- Faceswap: álbum sigue devolviendo media group
- xAI: álbum 2 fotos → 2 edits

---

## Checklist global (Definition of Done)

- [ ] Fase 1: filtro `handle_photo_caption` corregido
- [ ] Fase 2: `handle_album` + `_process_grok_album_after_delay` + `delete_status`
- [ ] Fase 3: `tests/test_album_batch.py` con 11 tests
- [ ] `/start` menciona álbum + caption
- [ ] `./venv/bin/python -m pytest tests/ -q` verde
- [ ] Sin cambios en `sessions.py`