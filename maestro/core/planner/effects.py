"""Style-to-effects inference for deterministic plans."""

from __future__ import annotations

from maestro.core.plan_schemas import MixStep

# Per-role effects always applied regardless of style
_ROLE_ALWAYS_EFFECTS: dict[str, list[str]] = {
    "drums": ["compressor"],
    "bass":  ["compressor"],
    "pads":  ["reverb"],
    "lead":  ["reverb"],
    "chords": [],
    "melody": [],
    "arp":    ["reverb"],
    "fx":     ["reverb", "delay"],
}

# Style-keyword → additional per-role overrides
_STYLE_ROLE_EFFECTS: list[tuple[str, dict[str, list[str]]]] = [
    ("rock",       {"lead": ["distortion", "reverb"], "drums": ["compressor"], "bass": ["overdrive"]}),
    ("metal",      {"lead": ["distortion"], "drums": ["compressor"], "bass": ["distortion"]}),
    ("prog",       {"pads": ["reverb", "chorus"], "lead": ["reverb", "distortion"]}),
    ("psychedel",  {"pads": ["reverb", "flanger"], "lead": ["reverb", "phaser"]}),
    ("house",      {"drums": ["compressor"], "bass": ["compressor", "filter"], "pads": ["reverb", "chorus"]}),
    ("techno",     {"drums": ["compressor"], "bass": ["distortion", "filter"]}),
    ("trap",       {"drums": ["compressor"], "bass": ["filter"]}),
    ("dubstep",    {"bass": ["distortion", "filter"], "drums": ["compressor"]}),
    ("edm",        {"drums": ["compressor"], "pads": ["reverb", "chorus"], "lead": ["reverb", "delay"]}),
    ("ambient",    {"pads": ["reverb", "chorus"], "melody": ["reverb", "delay"], "lead": ["reverb", "delay"]}),
    ("cinematic",  {"pads": ["reverb"], "melody": ["reverb", "delay"]}),
    ("post rock",  {"pads": ["reverb", "delay"], "lead": ["reverb", "delay"]}),
    ("shoegaze",   {"lead": ["reverb", "chorus", "distortion"], "pads": ["reverb", "flanger"]}),
    ("jazz",       {"chords": ["reverb"], "melody": ["reverb"], "bass": [], "drums": ["reverb"]}),
    ("blues",      {"lead": ["overdrive", "reverb"], "chords": ["reverb"]}),
    ("funk",       {"bass": ["compressor"], "drums": ["compressor"], "chords": ["chorus"]}),
    ("lofi",       {"drums": ["filter", "compressor"], "chords": ["reverb", "chorus"], "bass": []}),
    ("lo-fi",      {"drums": ["filter", "compressor"], "chords": ["reverb", "chorus"]}),
    ("vintage",    {"chords": ["chorus", "reverb"], "melody": ["reverb"]}),
    ("tape",       {"drums": ["compressor"], "pads": ["reverb"]}),
    ("soul",       {"chords": ["reverb", "chorus"], "melody": ["reverb"]}),
    ("r&b",        {"chords": ["reverb"], "bass": ["compressor"]}),
    ("neosoul",    {"chords": ["reverb", "chorus"], "pads": ["reverb"], "drums": ["compressor"]}),
    ("classical",  {"pads": ["reverb"], "melody": ["reverb"], "chords": ["reverb"]}),
    ("orchestral", {"pads": ["reverb"], "melody": ["reverb"], "chords": ["reverb"]}),
    ("pop",        {"drums": ["compressor"], "chords": ["reverb"], "melody": ["reverb"]}),
    ("synth",      {"pads": ["chorus", "reverb"], "lead": ["reverb", "delay"]}),
]


def _infer_mix_steps(style: str, roles: list[str]) -> list[MixStep]:
    """Infer MixStep effects for a style + role combination.

    Returns a flat list of MixStep objects. Shared Reverb bus is created for
    tracks that get reverb; direct inserts (compressor, EQ, distortion) go on
    individual tracks.
    """
    style_lower = style.lower()

    role_effects: dict[str, set[str]] = {}
    for role in roles:
        effects = set(_ROLE_ALWAYS_EFFECTS.get(role, []))
        for keyword, overrides in _STYLE_ROLE_EFFECTS:
            if keyword in style_lower:
                effects.update(overrides.get(role, []))
        if effects:
            role_effects[role] = effects

    if not role_effects:
        return []

    needs_reverb_bus = any("reverb" in efx for efx in role_effects.values())
    steps: list[MixStep] = []

    for role in roles:
        track_name = role.capitalize()
        effects = role_effects.get(role, set())

        for efx in sorted(effects - {"reverb"}):
            try:
                steps.append(MixStep(action="add_insert", track=track_name, type=efx))
            except Exception:
                pass

        if "reverb" in effects and needs_reverb_bus:
            try:
                steps.append(MixStep(action="add_send", track=track_name, bus="Reverb"))
            except Exception:
                pass

    return steps
