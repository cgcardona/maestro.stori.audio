"""Structured prompt context builders for parsed MAESTRO PROMPTs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maestro.prompts import MaestroPrompt


def _structured_routing_lines(parsed: "MaestroPrompt") -> list[str]:
    """Build the routing-field lines shared by both context helpers."""
    lines: list[str] = ["", "═══ STORI STRUCTURED INPUT ═══"]
    lines.append(f"Mode: {parsed.mode}")

    if parsed.section:
        lines.append(f"Section: {parsed.section}")
    if parsed.target:
        target_str: str = parsed.target.kind
        if parsed.target.name:
            target_str += f":{parsed.target.name}"
        lines.append(f"Target: {target_str}")
    if parsed.style:
        lines.append(f"Style: {parsed.style}")
    if parsed.key:
        lines.append(f"Key: {parsed.key}")
    if parsed.tempo:
        lines.append(f"Tempo: {parsed.tempo} BPM")
    if parsed.roles:
        lines.append(f"Roles: {', '.join(parsed.roles)}")
    if parsed.constraints:
        lines.append(f"Constraints: {', '.join(f'{k}={v}' for k, v in parsed.constraints.items())}")
    if parsed.vibes:
        vibe_parts = [
            f"{vw.vibe} (weight {vw.weight})" if vw.weight != 1 else vw.vibe
            for vw in parsed.vibes
        ]
        lines.append(f"Vibes: {', '.join(vibe_parts)}")

    lines.append("─────────────────────────────────────")
    lines.append("Use the above values directly. Do not re-infer from the Request text.")
    return lines


def structured_prompt_routing_context(parsed: "MaestroPrompt") -> str:
    """Routing fields only — no Maestro extension dimensions.

    Used by the planner, which only needs to decide *what tools to call*
    (tracks, roles, bars, style), not interpret musical content.
    """
    lines = _structured_routing_lines(parsed)
    lines.append("═════════════════════════════════════")
    lines.append("")
    return "\n".join(lines)


def structured_prompt_context(parsed: "MaestroPrompt") -> str:
    """Full structured context: routing fields + Maestro extension dimensions.

    Used by the EDITING LLM, which needs the creative brief to generate
    correct note data, voicings, dynamics, etc.
    """
    import yaml as _yaml  # local to avoid circular at module import time

    lines = _structured_routing_lines(parsed)

    if parsed.extensions:
        lines.append("")
        lines.append("MAESTRO DIMENSIONS — TRANSLATE ALL BLOCKS INTO TOOL CALLS:")
        try:
            ext_yaml = _yaml.dump(
                parsed.extensions,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            ).rstrip()
            lines.append(ext_yaml)
        except Exception:
            for k, v in parsed.extensions.items():
                lines.append(f"  {k}: {v}")

        ext_keys = {k.lower() for k in parsed.extensions}
        translation_lines: list[str] = []

        if "effects" in ext_keys:
            translation_lines.append(
                "EXECUTE Effects block: call stori_add_insert_effect for every effect listed. "
                "drums.compression→compressor, drums.room/reverb→reverb, bass.saturation→overdrive, "
                "bass.eq→eq, chords.tremolo→tremolo, chords.reverb→reverb, lead.overdrive→overdrive, "
                "lead.distortion→distortion, lead.delay→delay, lead.chorus→chorus. "
                "Call AFTER the track is created, BEFORE adding notes."
            )
        if "midiexpressiveness" in ext_keys:
            translation_lines.append(
                "EXECUTE MidiExpressiveness block: "
                "(1) cc_curves → call stori_add_midi_cc(regionId, cc=N, events=[{beat,value},...]) "
                "for EACH cc_curves entry. CC91=reverb send, CC1=modulation, CC11=expression, "
                "CC64=sustain pedal, CC74=filter cutoff, CC93=chorus send. "
                "(2) pitch_bend → call stori_add_pitch_bend(regionId, events=[{beat,value},...]) "
                "on the target region. Values: 0=center, ±8192=±2 semitones, ±4096=±1 semitone. "
                "(3) sustain_pedal → call stori_add_midi_cc(cc=64) with 127=down / 0=up pairs "
                "at the specified changes_per_bar rate. "
                "Call ALL of these AFTER stori_add_notes on the relevant region."
            )
        if "automation" in ext_keys:
            translation_lines.append(
                "EXECUTE Automation block: call stori_add_automation(trackId=TRACK_ID, "
                "parameter='Volume', points=[{beat,value,curve},...]) "
                "for EACH automation lane. Use trackId (NOT 'target') from stori_add_midi_track. "
                "parameter must be a canonical string: 'Volume', 'Pan', 'EQ Low', 'EQ Mid', "
                "'EQ High', 'Synth Cutoff', 'Synth Resonance', 'Pitch Bend', "
                "'Mod Wheel (CC1)', 'Expression (CC11)', 'Filter Cutoff (CC74)', etc. "
                "Call AFTER all notes are added."
            )

        if translation_lines:
            lines.append("")
            lines.append("▶ EXECUTION REQUIREMENTS (these are tool calls, not suggestions):")
            for tl in translation_lines:
                lines.append(f"  • {tl}")

    lines.append("═════════════════════════════════════")
    lines.append("")
    return "\n".join(lines)
