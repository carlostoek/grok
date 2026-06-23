# Learning: Album Batch Kie Imagine (2026-06-23)

## Pattern
Telegram albums require `not media_group_id` on single-photo handlers — first captioned item otherwise wins registration order.

## Reuse
Extend existing `_album_cache` + `ALBUM_COLLECT_DELAY` pattern from faceswap; dispatch by `model` at flush time.

## Kie batch
Always `image_data` upload per image; never `kie_source_ref` for inputs. Outputs still get per-image `generation_refs` for reply-edit chain.

## UX
`process_image_result(delete_status=False)` keeps batch progress message alive across N results.

## Tests
`tests/test_album_batch.py` — 11 tests; patch `asyncio.create_task` + `asyncio.sleep` for album delay collection.