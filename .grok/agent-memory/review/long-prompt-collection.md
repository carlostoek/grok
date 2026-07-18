# Review — long-prompt-collection

- Effort: 3 (1 general + tests + plan specialists)
- Rounds: 3
- Final: 0 open issues
- Tests: 264 passed, 2 skipped

## Issues fixed across rounds

1. Collection state cleared only on success (retry on failure)
2. Stale state cleared on short-caption direct path
3. HTML entity-safe caption truncation
4. Reply-to-bot-message completes collection
5. Photo without caption reminds pending collection
6. 24 tests in test_long_prompt_collection.py