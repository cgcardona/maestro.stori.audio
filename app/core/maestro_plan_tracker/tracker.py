"""_PlanTracker — manages plan lifecycle for an EDITING session."""

from __future__ import annotations

import uuid as _uuid_mod
from typing import Any, Optional

from app.core.maestro_helpers import _human_label_for_tool, _humanize_style
from app.core.maestro_plan_tracker.constants import (
    _AGENT_TEAM_PHASE3_TOOLS,
    _CONTENT_TOOL_NAMES,
    _EFFECT_TOOL_NAMES,
    _EXPRESSIVE_TOOL_NAMES,
    _GENERATOR_TOOL_NAMES,
    _MIXING_TOOL_NAMES,
    _PROJECT_SETUP_TOOL_NAMES,
    _TRACK_CREATION_NAMES,
)
from app.core.maestro_plan_tracker.models import _PlanStep


class _PlanTracker:
    """Manages the structured plan lifecycle for an EDITING session.

    Builds a plan from the first batch of tool calls, emits plan / planStepUpdate
    SSE events, and tracks step progress across composition iterations.
    """

    def __init__(self) -> None:
        self.plan_id: str = str(_uuid_mod.uuid4())
        self.title: str = ""
        self.steps: list[_PlanStep] = []
        self._active_step_id: Optional[str] = None
        self._active_step_ids: set[str] = set()
        self._next_id: int = 1

    # -- Build ----------------------------------------------------------------

    def build(
        self,
        tool_calls: list[Any],
        prompt: str,
        project_context: dict[str, Any],
        is_composition: bool,
        store: Any,
    ) -> None:
        self.title = self._derive_title(prompt, tool_calls, project_context)
        self.steps = self._group_into_steps(tool_calls)
        if is_composition:
            self._add_anticipatory_steps(store)

    def _derive_title(
        self,
        prompt: str,
        tool_calls: list[Any],
        project_context: dict[str, Any],
    ) -> str:
        """Build a musically descriptive plan title.

        Target patterns (from ExecutionTimelineView spec):
          Editing:    "Building Funk Groove"
          Composing:  "Composing Lo-Fi Hip Hop"
          Multi-track: "Setting Up 6-Track Jazz"
        """
        track_count = sum(
            1 for tc in tool_calls if tc.name in _TRACK_CREATION_NAMES
        )

        style: Optional[str] = None
        section: Optional[str] = None

        if prompt.startswith("STORI PROMPT"):
            for line in prompt.splitlines():
                stripped = line.strip()
                if stripped.lower().startswith("section:"):
                    section = stripped.split(":", 1)[1].strip()
                elif stripped.lower().startswith("style:"):
                    style = stripped.split(":", 1)[1].strip()
        else:
            for tc in tool_calls:
                if tc.name in _GENERATOR_TOOL_NAMES:
                    s = tc.params.get("style", "")
                    if s:
                        style = _humanize_style(s)
                        break

        style_title = _humanize_style(style) if style else None

        if track_count >= 3 and style_title:
            return f"Setting Up {track_count}-Track {style_title}"
        if section and style_title:
            return f"Building {style_title} {section.title()}"
        if style_title:
            return f"Composing {style_title}"
        if section:
            return f"Building {section.title()}"
        if track_count >= 2:
            return f"Setting Up {track_count}-Track Arrangement"

        short = prompt[:80].rstrip()
        if len(prompt) > 80:
            short = short.rsplit(" ", 1)[0] or short
        if short.startswith("STORI PROMPT"):
            return "Composing"
        return f"Building {short}" if len(short) < 40 else short

    @staticmethod
    def _track_name_for_call(tc: Any) -> Optional[str]:
        """Extract the track name a tool call targets (None for project-level)."""
        name = tc.name
        params = tc.params
        if name in _TRACK_CREATION_NAMES:
            return params.get("name")
        if name in _GENERATOR_TOOL_NAMES:
            return params.get("trackName") or params.get("role", "").capitalize() or None
        return params.get("trackName") or params.get("name") or None

    def _group_into_steps(self, tool_calls: list[Any]) -> list[_PlanStep]:
        """Group tool calls into plan steps using canonical label patterns.

        Canonical patterns recognised by ExecutionTimelineView:
          "Create <TrackName> track"     — track creation
          "Add content to <TrackName>"   — region + note generation
          "Add notes to <TrackName>"     — note-only addition
          "Add region to <TrackName>"    — region-only
          "Add effects to <TrackName>"   — insert effects for a track
          "Add MIDI CC to <TrackName>"   — CC curves
          "Add pitch bend to <TrackName>"— pitch bend events
          "Write automation for <TrackName>" — automation lanes
          "Set up shared Reverb bus"     — project-level bus setup

        Project-level steps (tempo, key, bus) must NOT contain a
        preposition pattern — they fall into "Project Setup".
        """
        steps: list[_PlanStep] = []
        i, n = 0, len(tool_calls)

        # Leading setup tools — one step per call (project-level)
        while i < n and tool_calls[i].name in _PROJECT_SETUP_TOOL_NAMES:
            tc = tool_calls[i]
            if tc.name == "stori_set_tempo":
                label = f"Set tempo to {tc.params.get('tempo', '?')} BPM"
            elif tc.name == "stori_set_key":
                key_val = tc.params.get("key", "?")
                label = f"Set key signature to {key_val}"
            else:
                label = _human_label_for_tool(tc.name, tc.params)
            steps.append(_PlanStep(
                step_id=str(self._next_id),
                label=label,
                tool_name=tc.name,
                tool_indices=[i],
            ))
            self._next_id += 1
            i += 1

        while i < n:
            tc = tool_calls[i]

            # ----- Track creation: "Create <TrackName> track" -----
            if tc.name in _TRACK_CREATION_NAMES:
                track_name = tc.params.get("name", "Track")
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=f"Create {track_name} track",
                    track_name=track_name,
                    tool_name="stori_add_midi_track",
                    tool_indices=[i],
                    parallel_group="instruments",
                ))
                self._next_id += 1
                i += 1

                # Consume contiguous content/generator tools for the same track
                content_indices: list[int] = []
                content_detail_parts: list[str] = []
                while i < n and tool_calls[i].name in (
                    _CONTENT_TOOL_NAMES | _GENERATOR_TOOL_NAMES | _MIXING_TOOL_NAMES
                ):
                    next_tc = tool_calls[i]
                    if next_tc.name in {"stori_set_track_color", "stori_set_track_icon"}:
                        content_indices.append(i)
                        i += 1
                        continue
                    next_track = self._track_name_for_call(next_tc)
                    if next_track and next_track.lower() != track_name.lower():
                        break
                    content_indices.append(i)
                    if next_tc.name in _GENERATOR_TOOL_NAMES:
                        style = next_tc.params.get("style", "")
                        bars = next_tc.params.get("bars", "")
                        role = next_tc.params.get("role", "")
                        parts = []
                        if bars:
                            parts.append(f"{bars} bars")
                        if style:
                            parts.append(_humanize_style(style))
                        if role:
                            parts.append(role)
                        if parts:
                            content_detail_parts.append(", ".join(parts))
                    i += 1
                if content_indices:
                    steps.append(_PlanStep(
                        step_id=str(self._next_id),
                        label=f"Add content to {track_name}",
                        detail="; ".join(content_detail_parts) if content_detail_parts else None,
                        track_name=track_name,
                        tool_name="stori_add_notes",
                        tool_indices=content_indices,
                        parallel_group="instruments",
                    ))
                    self._next_id += 1

                # Consume contiguous effects for the same track
                effect_indices: list[int] = []
                effect_detail_parts: list[str] = []
                while i < n and tool_calls[i].name in _EFFECT_TOOL_NAMES:
                    etc = tool_calls[i]
                    etc_track = etc.params.get("trackName") or etc.params.get("name", "")
                    if etc_track and etc_track.lower() != track_name.lower():
                        break
                    effect_indices.append(i)
                    if etc.name == "stori_add_insert_effect":
                        etype = etc.params.get("type", "")
                        if etype:
                            effect_detail_parts.append(etype.title())
                    i += 1
                if effect_indices:
                    steps.append(_PlanStep(
                        step_id=str(self._next_id),
                        label=f"Add effects to {track_name}",
                        detail=", ".join(effect_detail_parts) if effect_detail_parts else None,
                        track_name=track_name,
                        tool_name="stori_add_insert_effect",
                        tool_indices=effect_indices,
                        parallel_group="instruments",
                    ))
                    self._next_id += 1

                # Consume contiguous expressive tools for the same track
                while i < n and tool_calls[i].name in _EXPRESSIVE_TOOL_NAMES:
                    etc = tool_calls[i]
                    if etc.name == "stori_add_midi_cc":
                        steps.append(_PlanStep(
                            step_id=str(self._next_id),
                            label=f"Add MIDI CC to {track_name}",
                            track_name=track_name,
                            tool_name="stori_add_midi_cc",
                            tool_indices=[i],
                            parallel_group="instruments",
                        ))
                    elif etc.name == "stori_add_pitch_bend":
                        steps.append(_PlanStep(
                            step_id=str(self._next_id),
                            label=f"Add pitch bend to {track_name}",
                            track_name=track_name,
                            tool_name="stori_add_pitch_bend",
                            tool_indices=[i],
                            parallel_group="instruments",
                        ))
                    elif etc.name == "stori_add_automation":
                        steps.append(_PlanStep(
                            step_id=str(self._next_id),
                            label=f"Write automation for {track_name}",
                            track_name=track_name,
                            tool_name="stori_add_automation",
                            tool_indices=[i],
                            parallel_group="instruments",
                        ))
                    self._next_id += 1
                    i += 1

            # ----- Orphaned content tools (no preceding track creation) -----
            elif tc.name in (_CONTENT_TOOL_NAMES | _GENERATOR_TOOL_NAMES):
                track_name = self._track_name_for_call(tc) or "Track"
                indices = [i]
                i += 1
                while i < n and tool_calls[i].name in (
                    _CONTENT_TOOL_NAMES | _GENERATOR_TOOL_NAMES
                ):
                    next_track = self._track_name_for_call(tool_calls[i])
                    if next_track and next_track.lower() != track_name.lower():
                        break
                    indices.append(i)
                    i += 1
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=f"Add content to {track_name}",
                    track_name=track_name,
                    tool_name="stori_add_notes",
                    tool_indices=indices,
                    parallel_group="instruments",
                ))
                self._next_id += 1

            # ----- Bus/routing (ensure bus + sends) -----
            elif tc.name == "stori_ensure_bus":
                bus_name = tc.params.get("name", "Bus")
                bus_indices = [i]
                i += 1
                while i < n and tool_calls[i].name == "stori_add_send":
                    bus_indices.append(i)
                    i += 1
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=f"Set up shared {bus_name} bus",
                    tool_name="stori_ensure_bus",
                    tool_indices=bus_indices,
                ))
                self._next_id += 1

            # ----- Effects (track-targeted insert effects) -----
            elif tc.name in _EFFECT_TOOL_NAMES:
                track_name = tc.params.get("trackName") or "Track"
                indices = [i]
                detail_parts: list[str] = []
                if tc.name == "stori_add_insert_effect":
                    etype = tc.params.get("type", "")
                    if etype:
                        detail_parts.append(etype.title())
                i += 1
                while i < n and tool_calls[i].name in _EFFECT_TOOL_NAMES:
                    etc = tool_calls[i]
                    etc_track = etc.params.get("trackName", "")
                    if etc_track and etc_track.lower() != track_name.lower():
                        break
                    indices.append(i)
                    if etc.name == "stori_add_insert_effect":
                        etype = etc.params.get("type", "")
                        if etype:
                            detail_parts.append(etype.title())
                    i += 1
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=f"Add effects to {track_name}",
                    detail=", ".join(detail_parts) if detail_parts else None,
                    track_name=track_name,
                    tool_name="stori_add_insert_effect",
                    tool_indices=indices,
                    parallel_group="instruments",
                ))
                self._next_id += 1

            # ----- Expressive tools (standalone) -----
            elif tc.name in _EXPRESSIVE_TOOL_NAMES:
                track_name = self._track_name_for_call(tc) or "Track"
                if tc.name == "stori_add_midi_cc":
                    label = f"Add MIDI CC to {track_name}"
                elif tc.name == "stori_add_pitch_bend":
                    label = f"Add pitch bend to {track_name}"
                else:
                    label = f"Write automation for {track_name}"
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=label,
                    track_name=track_name,
                    tool_name=tc.name,
                    tool_indices=[i],
                    parallel_group="instruments",
                ))
                self._next_id += 1
                i += 1

            # ----- Mixing tools -----
            elif tc.name in _MIXING_TOOL_NAMES:
                indices = []
                while i < n and tool_calls[i].name in _MIXING_TOOL_NAMES:
                    indices.append(i)
                    i += 1
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label="Adjust mix",
                    tool_name="stori_set_track_volume",
                    tool_indices=indices,
                ))
                self._next_id += 1

            # ----- Fallback -----
            else:
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=_human_label_for_tool(tc.name, tc.params),
                    tool_name=tc.name,
                    tool_indices=[i],
                ))
                self._next_id += 1
                i += 1

        from app.core.maestro_editing.tool_execution import phase_for_tool
        for step in steps:
            if step.tool_name:
                step.phase = phase_for_tool(step.tool_name)

        return steps

    def build_from_prompt(
        self,
        parsed: Any,  # ParsedPrompt — avoid circular import
        prompt: str,
        project_context: dict[str, Any],
    ) -> None:
        """Build a skeleton plan from a parsed STORI PROMPT before any LLM call.

        Creates one pending step per expected action derived from the prompt's
        routing fields (Tempo, Key, Role, Style, Section) so the TODO list
        appears immediately when the user submits, not after the first LLM
        response arrives.

        Labels use canonical patterns for ExecutionTimelineView grouping.
        Steps are ordered per-track (contiguous) so the timeline renders
        coherent instrument sections.
        """
        self.title = self._derive_title(prompt, [], project_context)

        current_tempo = project_context.get("tempo")
        current_key = (project_context.get("key") or "").strip().lower()
        if parsed.tempo and parsed.tempo != current_tempo:
            self.steps.append(_PlanStep(
                step_id=str(self._next_id),
                label=f"Set tempo to {parsed.tempo} BPM",
                tool_name="stori_set_tempo",
            ))
            self._next_id += 1
        if parsed.key and parsed.key.strip().lower() != current_key:
            self.steps.append(_PlanStep(
                step_id=str(self._next_id),
                label=f"Set key signature to {parsed.key}",
                tool_name="stori_set_key",
            ))
            self._next_id += 1

        existing_track_names = {
            t.get("name", "").lower()
            for t in project_context.get("tracks", [])
            if t.get("name")
        }

        ext = getattr(parsed, "extensions", {}) or {}
        ext_keys = {k.lower() for k in ext}
        effects_data = ext.get("effects") or ext.get("Effects") or {}

        _ROLE_LABELS: dict[str, str] = {
            "drums": "Drums", "drum": "Drums",
            "bass": "Bass",
            "chords": "Chords", "chord": "Chords",
            "melody": "Melody",
            "lead": "Lead",
            "arp": "Arp",
            "pads": "Pads", "pad": "Pads",
            "fx": "FX",
        }
        for role in parsed.roles:
            track_label = _ROLE_LABELS.get(role.lower(), role.title())
            track_exists = track_label.lower() in existing_track_names

            if track_exists:
                self.steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=f"Add content to {track_label}",
                    track_name=track_label,
                    tool_name="stori_add_notes",
                    parallel_group="instruments",
                ))
            else:
                self.steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=f"Create {track_label} track",
                    track_name=track_label,
                    tool_name="stori_add_midi_track",
                    parallel_group="instruments",
                ))
            self._next_id += 1

            if not track_exists:
                self.steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=f"Add content to {track_label}",
                    track_name=track_label,
                    tool_name="stori_add_notes",
                    parallel_group="instruments",
                ))
                self._next_id += 1

            track_key_lower = track_label.lower()
            if "effects" in ext_keys and isinstance(effects_data, dict):
                matched_key = None
                for ek in effects_data:
                    if ek.replace("_", " ").lower() == track_key_lower:
                        matched_key = ek
                        break
                if matched_key:
                    self.steps.append(_PlanStep(
                        step_id=str(self._next_id),
                        label=f"Add effects to {track_label}",
                        track_name=track_label,
                        tool_name="stori_add_insert_effect",
                        parallel_group="instruments",
                    ))
                    self._next_id += 1

        if "effects" in ext_keys and isinstance(effects_data, dict):
            role_labels_lower = {
                _ROLE_LABELS.get(r.lower(), r.title()).lower()
                for r in parsed.roles
            }
            for track_key in effects_data:
                label = track_key.replace("_", " ").title()
                if label.lower() not in role_labels_lower:
                    self.steps.append(_PlanStep(
                        step_id=str(self._next_id),
                        label=f"Add effects to {label}",
                        track_name=label,
                        tool_name="stori_add_insert_effect",
                        parallel_group="instruments",
                    ))
                    self._next_id += 1

        if "effects" not in ext_keys and parsed.roles:
            self.steps.append(_PlanStep(
                step_id=str(self._next_id),
                label="Add effects and routing",
                tool_name="stori_add_insert_effect",
                parallel_group="instruments",
            ))
            self._next_id += 1

        if not parsed.roles:
            self.steps.append(_PlanStep(
                step_id=str(self._next_id),
                label="Generate music",
                tool_name="stori_add_midi_track",
            ))
            self._next_id += 1

        if "midiexpressiveness" in ext_keys:
            midi_exp = ext.get("midiexpressiveness") or ext.get("MidiExpressiveness") or {}
            if isinstance(midi_exp, dict):
                target_track = None
                if parsed.roles:
                    for r in parsed.roles:
                        if r.lower() not in ("drums",):
                            target_track = _ROLE_LABELS.get(r.lower(), r.title())
                            break
                    if not target_track:
                        target_track = _ROLE_LABELS.get(
                            parsed.roles[0].lower(), parsed.roles[0].title()
                        )

                if "cc_curves" in midi_exp:
                    label = f"Add MIDI CC to {target_track}" if target_track else "Add MIDI CC curves"
                    self.steps.append(_PlanStep(
                        step_id=str(self._next_id),
                        label=label,
                        track_name=target_track,
                        tool_name="stori_add_midi_cc",
                        parallel_group="instruments",
                    ))
                    self._next_id += 1
                if "pitch_bend" in midi_exp:
                    label = f"Add pitch bend to {target_track}" if target_track else "Add pitch bend"
                    self.steps.append(_PlanStep(
                        step_id=str(self._next_id),
                        label=label,
                        track_name=target_track,
                        tool_name="stori_add_pitch_bend",
                        parallel_group="instruments",
                    ))
                    self._next_id += 1
                if "sustain_pedal" in midi_exp:
                    label = f"Add MIDI CC to {target_track}" if target_track else "Add sustain pedal (CC 64)"
                    self.steps.append(_PlanStep(
                        step_id=str(self._next_id),
                        label=label,
                        track_name=target_track,
                        tool_name="stori_add_midi_cc",
                        parallel_group="instruments",
                    ))
                    self._next_id += 1

        if "automation" in ext_keys:
            automation_data = ext.get("automation") or ext.get("Automation") or []
            count = len(automation_data) if isinstance(automation_data, list) else 1
            if isinstance(automation_data, list) and automation_data:
                first_track = automation_data[0].get("track")
                if first_track:
                    label = f"Write automation for {first_track.replace('_', ' ').title()}"
                else:
                    label = f"Write automation ({count} lane{'s' if count != 1 else ''})"
            else:
                label = f"Write automation ({count} lane{'s' if count != 1 else ''})"
            self.steps.append(_PlanStep(
                step_id=str(self._next_id),
                label=label,
                tool_name="stori_add_automation",
            ))
            self._next_id += 1

        if "effects" in ext_keys and isinstance(effects_data, dict):
            tracks_needing_reverb = [
                k for k, v in effects_data.items()
                if isinstance(v, dict) and "reverb" in v
            ]
            if len(tracks_needing_reverb) >= 2:
                self.steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label="Set up shared Reverb bus",
                    tool_name="stori_ensure_bus",
                ))
                self._next_id += 1

        from app.core.maestro_editing.tool_execution import phase_for_tool
        for step in self.steps:
            if step.tool_name:
                step.phase = phase_for_tool(step.tool_name)

    def _add_anticipatory_steps(self, store: Any) -> None:
        """For composition mode, add pending steps for tracks still needing content."""
        names_with_steps = {
            s.track_name.lower() for s in self.steps if s.track_name
        }
        for track in store.registry.list_tracks():
            if track.name.lower() in names_with_steps:
                continue
            regions = store.registry.get_track_regions(track.id)
            has_notes = any(
                bool(store.get_region_notes(r.id)) for r in regions
            ) if regions else False
            if not has_notes:
                self.steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=f"Add content to {track.name}",
                    track_name=track.name,
                    tool_name="stori_add_notes",
                ))
                self._next_id += 1

    # -- SSE events -----------------------------------------------------------

    def to_plan_event(self) -> dict[str, Any]:
        return {
            "type": "plan",
            "planId": self.plan_id,
            "title": self.title,
            "steps": [
                {
                    "stepId": s.step_id,
                    "label": s.label,
                    "status": "pending",
                    "phase": s.phase,
                    **({"toolName": s.tool_name} if s.tool_name else {}),
                    **({"detail": s.detail} if s.detail else {}),
                    **({"parallelGroup": s.parallel_group} if s.parallel_group else {}),
                }
                for s in self.steps
            ],
        }

    def step_for_tool_index(self, index: int) -> Optional[_PlanStep]:
        """Find the step a tool-call index belongs to (first iteration only)."""
        for step in self.steps:
            if index in step.tool_indices:
                return step
        return None

    def find_step_for_tool(
        self,
        tc_name: str,
        tc_params: dict[str, Any],
        store: Any,
    ) -> Optional[_PlanStep]:
        """Map a tool call to a plan step by name/context.

        Used for both subsequent iterations (reactive plan) and upfront-built
        plans (where tool_indices are empty and steps are matched by label/name).
        """
        if tc_name == "stori_set_tempo":
            tempo = tc_params.get("tempo")
            for step in self.steps:
                if "tempo" in step.label.lower() and step.status != "completed":
                    return step
            _ = tempo  # silence linter
        if tc_name == "stori_set_key":
            for step in self.steps:
                if "key" in step.label.lower() and step.status != "completed":
                    return step

        if tc_name in _TRACK_CREATION_NAMES:
            track_name = tc_params.get("name", "").lower()
            for step in self.steps:
                if (
                    step.track_name
                    and step.track_name.lower() == track_name
                    and step.status != "completed"
                ):
                    return step

        if tc_name in _CONTENT_TOOL_NAMES:
            track_id = tc_params.get("trackId", "")
            if track_id:
                for track in store.registry.list_tracks():
                    if track.id == track_id:
                        for step in self.steps:
                            if (
                                step.track_name
                                and step.track_name.lower() == track.name.lower()
                                and step.status != "completed"
                            ):
                                return step
                        break

        if tc_name in _GENERATOR_TOOL_NAMES:
            gen_track = (tc_params.get("trackName") or "").lower()
            if gen_track:
                for step in self.steps:
                    if (
                        step.track_name
                        and step.track_name.lower() == gen_track
                        and "content" in step.label.lower()
                        and step.status != "completed"
                    ):
                        return step
                for step in self.steps:
                    if (
                        step.track_name
                        and step.track_name.lower() == gen_track
                        and step.status != "completed"
                    ):
                        return step

        if tc_name in _EFFECT_TOOL_NAMES:
            tc_track = tc_params.get("trackName", "").lower()
            if tc_track:
                for step in self.steps:
                    if (
                        "effect" in step.label.lower()
                        and step.track_name
                        and step.track_name.lower() == tc_track
                        and step.status != "completed"
                    ):
                        return step
            if tc_name == "stori_ensure_bus":
                for step in self.steps:
                    if "bus" in step.label.lower() and step.status != "completed":
                        return step
            for step in self.steps:
                if "effect" in step.label.lower() and step.status != "completed":
                    return step

        if tc_name in _EXPRESSIVE_TOOL_NAMES:
            tc_track = tc_params.get("trackName", "").lower()
            if tc_name == "stori_add_midi_cc":
                for step in self.steps:
                    if "MIDI CC" in step.label and step.status != "completed":
                        if not tc_track or not step.track_name or step.track_name.lower() == tc_track:
                            return step
                for step in self.steps:
                    if "sustain" in step.label.lower() and step.status != "completed":
                        return step
            elif tc_name == "stori_add_pitch_bend":
                for step in self.steps:
                    if "pitch bend" in step.label.lower() and step.status != "completed":
                        return step
            elif tc_name == "stori_add_automation":
                for step in self.steps:
                    if "automation" in step.label.lower() and step.status != "completed":
                        return step

        if tc_name in _MIXING_TOOL_NAMES:
            for step in self.steps:
                if "mix" in step.label.lower() and step.status != "completed":
                    return step
        return None

    def get_step(self, step_id: str) -> Optional[_PlanStep]:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def activate_step(self, step_id: str) -> dict[str, Any]:
        step = self.get_step(step_id)
        if step:
            step.status = "active"
        self._active_step_id = step_id
        self._active_step_ids.add(step_id)
        d: dict[str, Any] = {
            "type": "planStepUpdate",
            "stepId": step_id,
            "status": "active",
            "phase": step.phase if step else "composition",
        }
        return d

    def complete_active_step(self) -> Optional[dict[str, Any]]:
        """Complete the currently-active step; returns event dict or None."""
        if not self._active_step_id:
            return None
        step = self.get_step(self._active_step_id)
        if not step:
            return None
        step.status = "completed"
        self._active_step_ids.discard(self._active_step_id)
        self._active_step_id = None
        d: dict[str, Any] = {
            "type": "planStepUpdate",
            "stepId": step.step_id,
            "status": "completed",
            "phase": step.phase,
        }
        if step.result:
            d["result"] = step.result
        return d

    def complete_step_by_id(
        self, step_id: str, result: Optional[str] = None,
    ) -> dict[str, Any]:
        step = self.get_step(step_id)
        if step:
            step.status = "completed"
            if result:
                step.result = result
        if self._active_step_id == step_id:
            self._active_step_id = None
        self._active_step_ids.discard(step_id)
        d: dict[str, Any] = {
            "type": "planStepUpdate",
            "stepId": step_id,
            "status": "completed",
            "phase": step.phase if step else "composition",
        }
        if result:
            d["result"] = result
        return d

    def complete_all_active_steps(self) -> list[dict[str, Any]]:
        """Complete every currently-active step. Returns list of event dicts."""
        events: list[dict[str, Any]] = []
        for step_id in list(self._active_step_ids):
            step = self.get_step(step_id)
            if step and step.status == "active":
                step.status = "completed"
                d: dict[str, Any] = {
                    "type": "planStepUpdate",
                    "stepId": step.step_id,
                    "status": "completed",
                    "phase": step.phase,
                }
                if step.result:
                    d["result"] = step.result
                events.append(d)
        self._active_step_ids.clear()
        self._active_step_id = None
        return events

    def find_active_step_for_track(self, track_name: str) -> Optional[_PlanStep]:
        """Find the active step bound to a specific instrument track."""
        track_lower = track_name.lower()
        for step in self.steps:
            if (
                step.status == "active"
                and step.track_name
                and step.track_name.lower() == track_lower
            ):
                return step
        return None

    def finalize_pending_as_skipped(self) -> list[dict[str, Any]]:
        """Mark all remaining pending steps as skipped and return events.

        The Execution Timeline spec requires that no step is left in "pending"
        at plan completion — steps that were never activated must be emitted
        as "skipped" so the frontend can render them correctly.
        """
        events: list[dict[str, Any]] = []
        for step in self.steps:
            if step.status == "pending":
                step.status = "skipped"
                events.append({
                    "type": "planStepUpdate",
                    "stepId": step.step_id,
                    "status": "skipped",
                    "phase": step.phase,
                })
        return events

    def progress_context(self) -> str:
        """Format plan progress for injection into the system prompt."""
        icons = {
            "completed": "✅",
            "active": "🔄",
            "pending": "⬜",
            "failed": "❌",
            "skipped": "⏭",
        }
        lines = ["Current plan progress:"]
        for s in self.steps:
            icon = icons.get(s.status, "⬜")
            line = f"{icon} Step {s.step_id}: {s.label}"
            if s.status == "completed" and s.result:
                line += f" — done ({s.result})"
            elif s.status == "active":
                line += " — active"
            else:
                line += " — pending"
            lines.append(line)
        return "\n".join(lines)
