# mypy Strictness Ratchet

This document describes the typing strictness plan for the Stori Maestro codebase.

## Current State

Both `maestro` and `orpheus` containers run mypy with `strict = true`, which enables:

| Flag | Status | What it does |
|------|--------|-------------|
| `disallow_any_generics` | ✅ Enabled (via strict) | Bare `dict`, `list` without params → error |
| `disallow_subclassing_any` | ✅ Enabled (via strict) | Can't subclass `Any`-typed bases |
| `disallow_untyped_calls` | ✅ Enabled (via strict) | Calling untyped functions → error |
| `disallow_untyped_defs` | ✅ Enabled (via strict) | Missing annotations → error |
| `disallow_incomplete_defs` | ✅ Enabled (via strict) | Partial annotations → error |
| `check_untyped_defs` | ✅ Enabled (via strict) | Type-check bodies even if untyped |
| `warn_return_any` | ✅ Enabled (via strict) | Returning `Any` → warning |
| `warn_unused_ignores` | ✅ Enabled (via strict) | Unused `# type: ignore` → warning |
| `warn_redundant_casts` | ✅ Enabled (via strict) | Redundant `cast()` → warning |
| `no_implicit_optional` | ✅ Enabled (via strict) | `None` default doesn't imply `Optional` |
| `warn_unreachable` | ✅ Enabled (explicit) | Unreachable code → warning |
| `show_error_codes` | ✅ Enabled (explicit) | Error codes in output |

### What strict does NOT catch

`strict = true` still allows explicit `Any` in type annotations. Code like
`dict[str, Any]`, `: Any`, and `-> Any` passes without complaint. This is by
design — `Any` is a valid type — but it weakens the safety net.

## Ratchet Plan

### Phase 1 — Baseline (done)

`strict = true` globally. Override for tests (`disallow_untyped_decorators = false`).

### Phase 2 — Kill explicit `Any` in app code

Replace `dict[str, Any]` with `TypedDict`, `JSONValue`, or `JSONObject` from
`app/contracts/json_types.py`. Per-module overrides not needed — the fix is in
the code, not the config.

### Phase 3 — Enable `disallow_any_explicit` per-module

Once modules are clean, add per-module overrides:

```toml
[[tool.mypy.overrides]]
module = ["app.contracts.*", "app.protocol.*", "app.models.*"]
disallow_any_explicit = true
```

Ratchet outward as more modules are cleaned.

### Phase 4 — Global `disallow_any_explicit`

Once all `app/` modules pass, enable globally:

```toml
[tool.mypy]
disallow_any_explicit = true
```

Tests get an override to keep `Any` for mocks/fixtures where it's practical.

## Boundary Quarantine

Some boundaries will always need `Any` (untyped third-party libs, raw HTTP payloads,
Gradio returns). These are quarantined:

- **Allowed**: boundary adapter modules that parse raw → typed
- **Required**: immediate conversion to typed forms before returning

See `app/contracts/json_types.py` for the canonical type definitions.

## CI Guardrail

The `tools/typing_audit.py` script tracks `Any` usage over time.
`artifacts/typing_audit.json` is the committed baseline. CI compares current
counts against the baseline and fails if `Any` usage increases.

## Tracking Progress

Run `python tools/typing_audit.py` to see current counts. The baseline was:

| Pattern | Count |
|---------|-------|
| `dict[str, Any]` | 1108 |
| `: Any` params | 753 |
| `-> Any` returns | 72 |
| `# type: ignore` | 39 |
| `tuple[..., Any]` | 30 |
| `list[Any]` | 12 |
| `cast(Any)` | 1 |
| **Total** | **2015** |
