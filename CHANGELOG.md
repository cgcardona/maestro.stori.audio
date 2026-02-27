# Changelog

## Unreleased

### Breaking

- **STORI PROMPT renamed to MAESTRO PROMPT.** The structured prompt header
  is now `MAESTRO PROMPT` (exact, case-sensitive). Requests beginning with
  `STORI PROMPT` are rejected with HTTP 400. No backward compatibility.

### Added

- `app/prompts/` package — dedicated namespace for the prompt DSL:
  - `StructuredPrompt` base class and `MaestroPrompt` canonical subclass.
  - `parse_prompt()` with typed error hierarchy (`UnsupportedPromptHeader`,
    `InvalidMaestroPrompt`).
- Repo-wide guard test (`test_maestro_prompt_cutover.py`) that fails if
  legacy `STORI PROMPT` patterns are reintroduced.

### Changed

- `ParsedPrompt` removed — use `MaestroPrompt` everywhere.
- `emotion_vector_from_stori_prompt()` renamed to `emotion_vector_from_maestro_prompt()`.
- `build_edit_stori_prompt()` renamed to `build_edit_maestro_prompt()`.
- Prompt inspiration pool updated: all `fullPrompt` values use `MAESTRO PROMPT`.
- Spec doc renamed: `stori_prompt_spec.md` → `maestro_prompt_spec.md`.

### Removed

- `app/core/prompt_parser.py` — replaced by `app/prompts/`.
