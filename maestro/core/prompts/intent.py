"""Intent classification prompt and system constant."""

from __future__ import annotations


def intent_classification_prompt(user_prompt: str) -> str:
    """Prompt for LLM intent classification when pattern matching returns UNKNOWN."""
    return (
        "Classify the user's intent for a DAW (Digital Audio Workstation) called Stori.\n\n"
        "Categories:\n"
        "- transport: Play, stop, pause, seek playback\n"
        "- track_edit: Add, rename, mute, solo, delete tracks; set volume, pan, color, icon\n"
        "- region_edit: Add, modify, delete regions; add/edit MIDI notes; quantize, swing\n"
        "- effects: Add effects (reverb, delay, compressor, EQ), create buses, add sends\n"
        "- mix_vibe: Producer language about the feel/vibe (darker, punchier, wider, more energy)\n"
        "- generation: Create/generate new music, beats, drums, bass, chords, melody\n"
        "- question: Asking for help, how-to, or information about Stori\n"
        "- clarify: Request is too vague to understand\n"
        "- other: None of the above\n\n"
        "Respond with ONLY the category name, nothing else.\n\n"
        f"User request: {user_prompt}\n"
        "Category:"
    )


INTENT_CLASSIFICATION_SYSTEM = "You are an intent classifier for a DAW. Respond with only the category name."
