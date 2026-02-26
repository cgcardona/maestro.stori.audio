"""
Music Generation Service for Maestro.

Primary backend: Storpheus (required for composing). No pattern fallback;
other backends (IR, HuggingFace, LLM) can be used when explicitly requested
or configured in priority.

Unified generation: all instruments for a section are generated in a single
Storpheus call so the model produces coherent, musically-related parts.  A
per-section cache ensures that concurrent instrument agents sharing the same
section only trigger ONE GPU call; subsequent agents read from cache.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable
from dataclasses import dataclass, field

from app.contracts.generation_types import GenerationContext
from app.contracts.json_types import JSONValue, NoteDict
from app.services.backends.base import GeneratorBackend, GenerationResult, MusicGeneratorBackend
from app.services.backends.text2midi import Text2MidiGeneratorBackend
from app.services.backends.drum_ir import DrumSpecBackend
from app.services.backends.bass_ir import BassSpecBackend
from app.services.backends.harmonic_ir import HarmonicSpecBackend
from app.services.backends.melody_ir import MelodySpecBackend
from app.services.backends.storpheus import StorpheusBackend
from app.services.groove_engine import RhythmSpine, extract_kick_onsets
from app.services.expressiveness import apply_expressiveness
from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Role → channel family mapping for unified generation channel extraction.
#
# Storpheus labels channels by GM family (e.g. "piano", "bass", "drums",
# "guitar", "strings") while the LLM uses musical role names (e.g. "keys",
# "chords", "rhythm guitar", "pad").  This map bridges the two vocabularies.
# ---------------------------------------------------------------------------
_ROLE_TO_FAMILIES: dict[str, list[str]] = {
    # Piano / keys roles → piano family
    "keys": ["piano", "organ", "chromatic_perc"],
    "chords": ["piano", "organ", "guitar", "strings", "ensemble"],
    "piano": ["piano"],
    "rhodes": ["piano"],
    "electric piano": ["piano"],
    "keyboard": ["piano", "organ"],
    "organ": ["organ"],
    "synth": ["synth_lead", "synth_pad"],
    "synth lead": ["synth_lead"],
    "synth pad": ["synth_pad"],
    "pad": ["synth_pad", "ensemble", "strings"],
    # Bass roles → bass family
    "bass": ["bass"],
    "sub bass": ["bass"],
    "synth bass": ["bass"],
    # Drums roles → drums
    "drums": ["drums"],
    "percussion": ["drums", "percussive"],
    # Guitar roles
    "guitar": ["guitar"],
    "rhythm guitar": ["guitar"],
    "lead guitar": ["guitar"],
    "acoustic guitar": ["guitar"],
    # Strings / ensemble
    "strings": ["strings", "ensemble"],
    "violin": ["strings"],
    "cello": ["strings"],
    "orchestral": ["strings", "ensemble", "brass"],
    # Brass / reed / wind
    "brass": ["brass"],
    "horns": ["brass"],
    "trumpet": ["brass"],
    "saxophone": ["reed"],
    "sax": ["reed"],
    "woodwinds": ["reed", "pipe"],
    "flute": ["pipe"],
    # Melody (generic)
    "melody": ["piano", "synth_lead", "guitar", "reed", "pipe", "brass"],
    "lead": ["synth_lead", "piano", "guitar"],
}


def _extract_channel_for_role(
    role_key: str,
    channel_notes: dict[str, list[NoteDict]],
) -> list[NoteDict]:
    """Extract notes for a musical role from Storpheus channel labels.

    Tries, in order:
    1. Exact match (role_key == channel label)
    2. Substring match (either direction)
    3. Family alias match (role_key maps to GM families that match channel labels)
    4. Returns empty list if no match (caller decides fallback)
    """
    if not channel_notes:
        return []

    # 1. Exact match
    if role_key in channel_notes:
        logger.info(f"🔍 Channel match (exact): '{role_key}'")
        return channel_notes[role_key]

    # 2. Substring match (e.g. "bass" in "bass_0", or "piano" in "electric piano")
    for ch_label, ch_notes in channel_notes.items():
        if role_key in ch_label.lower() or ch_label.lower() in role_key:
            logger.info(
                f"🔍 Channel match (substring): '{role_key}' → '{ch_label}' "
                f"({len(ch_notes)} notes)"
            )
            return ch_notes

    # 3. Family alias match
    target_families = _ROLE_TO_FAMILIES.get(role_key, [])
    if target_families:
        for family in target_families:
            for ch_label, ch_notes in channel_notes.items():
                ch_lower = ch_label.lower()
                # "piano_1" starts with "piano", "bass_0" starts with "bass"
                if ch_lower.startswith(family) or ch_lower == family:
                    logger.info(
                        f"🔍 Channel match (family alias): '{role_key}' → "
                        f"family '{family}' → channel '{ch_label}' "
                        f"({len(ch_notes)} notes)"
                    )
                    return ch_notes

    logger.warning(
        f"🔍 No channel match for role '{role_key}' in {list(channel_notes.keys())} "
        f"(tried families: {target_families})"
    )
    return []


# -----------------------------------------------------------------------------
# Quality Preset Configuration
# -----------------------------------------------------------------------------

@dataclass
class QualityPresetConfig:
    """Configuration for quality presets."""
    num_candidates: int = 1
    use_critic: bool = False
    accept_threshold: float = 0.65
    early_stop_threshold: float = 0.85
    use_coupled_generation: bool = False


QUALITY_PRESETS = {
    "fast": QualityPresetConfig(
        num_candidates=1,
        use_critic=False,
        accept_threshold=0.5,
        early_stop_threshold=0.7,
        use_coupled_generation=False,
    ),
    "balanced": QualityPresetConfig(
        num_candidates=2,
        use_critic=True,
        accept_threshold=0.65,
        early_stop_threshold=0.80,
        use_coupled_generation=True,
    ),
    "quality": QualityPresetConfig(
        num_candidates=4,
        use_critic=True,
        accept_threshold=0.75,
        early_stop_threshold=0.88,
        use_coupled_generation=True,
    ),
}


# -----------------------------------------------------------------------------
# Cached Rhythm Spine for Coupled Generation
# -----------------------------------------------------------------------------

@dataclass
class CoupledGenState:
    """Mutable state shared across coupled generation calls (drum→bass spine)."""
    rhythm_spine: RhythmSpine | None = None
    drum_notes: list[NoteDict] | None = None
    style: str = "trap"
    tempo: int = 120
    bars: int = 16


@dataclass
class _SectionCacheEntry:
    """Cached unified generation result for one section.

    Concurrent instrument agents sharing the same section wait on the lock;
    the first to acquire it performs the Orpheus call and stores the result.
    """
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    result: GenerationResult | None = None


class MusicGenerator:
    """
    Music generation service for composing.

    Default backend: Storpheus (required for composing). No pattern fallback;
    if Storpheus is unavailable, generation fails with a clear error.
    """
    
    def __init__(self, backend_priority: list[GeneratorBackend] | None = None):
        """
        Initialize with custom backend priority.
        
        Args:
            backend_priority: Ordered list of backends to try
        """
        # Composing requires Storpheus; no pattern fallback (fail fast)
        if backend_priority is None:
            backend_priority = [
                GeneratorBackend.STORPHEUS,
            ]
        
        # Initialize backends
        self.backend_map = {
            GeneratorBackend.TEXT2MIDI: Text2MidiGeneratorBackend(),
            GeneratorBackend.DRUM_IR: DrumSpecBackend(),
            GeneratorBackend.BASS_IR: BassSpecBackend(),
            GeneratorBackend.HARMONIC_IR: HarmonicSpecBackend(),
            GeneratorBackend.MELODY_IR: MelodySpecBackend(),
            GeneratorBackend.STORPHEUS: StorpheusBackend(),
        }
        
        self.priority = backend_priority
        self._availability_cache: dict[str, bool] = {}
        
        # Cached context for coupled generation
        self._generation_context: CoupledGenState | None = None
        # Per-section unified generation cache
        self._section_cache: dict[str, _SectionCacheEntry] = {}
    
    def set_generation_context(self, context: CoupledGenState) -> None:
        """set context for coupled generation (e.g., rhythm spine from drums)."""
        self._generation_context = context
    
    def clear_generation_context(self) -> None:
        """Clear the generation context."""
        self._generation_context = None
    
    async def generate(
        self,
        instrument: str,
        style: str,
        tempo: int,
        bars: int,
        key: str | None = None,
        chords: list[str] | None = None,
        preferred_backend: GeneratorBackend | None = None,
        context: GenerationContext | None = None,
    ) -> GenerationResult:
        """Generate MIDI notes (default: Storpheus; no pattern fallback)."""
        ctx = context or {}
        quality_preset = ctx.get("quality_preset", "quality")
        num_candidates = ctx.get("num_candidates")
        
        # Get preset config
        preset_config = QUALITY_PRESETS.get(quality_preset, QUALITY_PRESETS["balanced"])
        n = num_candidates or preset_config.num_candidates

        # If specific backend requested
        if preferred_backend:
            backend = self.backend_map.get(preferred_backend)
            if backend:
                result = await self._generate_with_coupling(
                    backend, preferred_backend, instrument, style, tempo, bars, key, chords,
                    preset_config, n, context=context,
                )
                if result.success:
                    result = self._maybe_apply_expressiveness(result, instrument, style, bars)
                return result
            else:
                logger.warning(f"Requested backend not available: {preferred_backend}")
        
        # Try backends in priority order
        last_failure: GenerationResult | None = None
        for backend_type in self.priority:
            backend = self.backend_map.get(backend_type)
            if not backend:
                continue
            
            if not await self._is_backend_available(backend):
                continue
            
            result = await self._generate_with_coupling(
                backend, backend_type, instrument, style, tempo, bars, key, chords,
                preset_config, n, context=context,
            )
            
            if result.success:
                result = self._maybe_apply_expressiveness(result, instrument, style, bars)
                return result
            else:
                last_failure = result
                logger.warning(f"Backend {backend_type.value} failed: {result.error}")
        
        if last_failure and last_failure.error:
            error_msg = last_failure.error
            logger.error(error_msg)
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=last_failure.backend_used,
                metadata=last_failure.metadata or {},
                error=error_msg,
            )
        error_msg = (
            "Music generation (Storpheus) is currently unavailable. "
            "Ensure the Storpheus service is running (port 10002) and reachable. "
            "Please try again once the service is up."
        )
        logger.error(error_msg)
        return GenerationResult(
            success=False,
            notes=[],
            backend_used=GeneratorBackend.STORPHEUS,
            metadata={},
            error=error_msg,
        )
    
    async def generate_unified(
        self,
        instruments: list[str],
        style: str,
        tempo: int,
        bars: int,
        key: str | None = None,
        context: GenerationContext | None = None,
    ) -> GenerationResult:
        """Generate all instruments together in one Storpheus call.

        Produces coherent multi-instrument output where all parts are
        generated simultaneously, yielding better musical coherence than
        generating each instrument independently.
        """
        for backend_type in self.priority:
            backend = self.backend_map.get(backend_type)
            if not backend:
                continue
            if not await self._is_backend_available(backend):
                continue

            result = await backend.generate_unified(
                instruments=instruments,
                style=style,
                tempo=tempo,
                bars=bars,
                key=key,
                context=context,
            )
            if result.success:
                return result
            else:
                logger.warning(f"Unified generation via {backend_type.value} failed: {result.error}")

        return GenerationResult(
            success=False,
            notes=[],
            backend_used=GeneratorBackend.STORPHEUS,
            metadata={},
            error="No backend available for unified generation",
        )

    async def generate_for_section(
        self,
        section_key: str,
        instrument: str,
        all_instruments: list[str],
        style: str,
        tempo: int,
        bars: int,
        key: str | None = None,
        context: GenerationContext | None = None,
    ) -> GenerationResult:
        """Generate notes for one instrument within a section using unified caching.

        The first call for a section triggers a single Storpheus call with all
        instruments. Concurrent callers for the same section wait on the lock
        and read from cache, extracting only their channel's notes.
        """
        if section_key not in self._section_cache:
            self._section_cache[section_key] = _SectionCacheEntry()
        entry = self._section_cache[section_key]

        async with entry.lock:
            if entry.result is None:
                logger.info(
                    f"🎼 Section {section_key}: FIRST call ({instrument}), "
                    f"triggering unified Storpheus call for ALL {all_instruments}"
                )
                entry.result = await self.generate_unified(
                    instruments=all_instruments,
                    style=style,
                    tempo=tempo,
                    bars=bars,
                    key=key,
                    context=context,
                )
            else:
                logger.info(
                    f"🎼 Section {section_key}: CACHE HIT ({instrument}), "
                    f"extracting from unified result"
                )

        unified = entry.result
        if not unified.success:
            return unified

        channel_notes = unified.channel_notes or {}
        role_notes: list[NoteDict] = []
        role_key = instrument.lower().strip()

        logger.info(
            f"🔍 Section {section_key}: extracting '{instrument}' (key='{role_key}') "
            f"from channels={list(channel_notes.keys())} "
            f"flat_notes={len(unified.notes) if unified.notes else 0}"
        )

        role_notes = _extract_channel_for_role(role_key, channel_notes)

        if not role_notes and unified.notes:
            logger.warning(
                f"⚠️ No channel match for '{instrument}' in {list(channel_notes.keys())} — "
                f"falling back to ALL flat notes ({len(unified.notes)} notes)"
            )
            role_notes = unified.notes

        return GenerationResult(
            success=True,
            notes=role_notes,
            backend_used=unified.backend_used,
            metadata={
                **(unified.metadata or {}),
                "unified_section": section_key,
                "extracted_channel": instrument,
            },
            cc_events=unified.cc_events,
            pitch_bends=unified.pitch_bends,
            aftertouch=unified.aftertouch,
        )

    def clear_section_cache(self, section_key: str | None = None) -> None:
        """Clear the unified generation cache for a section or all sections."""
        if section_key:
            self._section_cache.pop(section_key, None)
        else:
            self._section_cache.clear()

    @staticmethod
    def _ensure_snake_keys(notes: list[NoteDict]) -> list[NoteDict]:
        """Return a shallow copy of notes with snake_case beat keys for critics."""
        _MAP = {"startBeat": "start_beat", "durationBeats": "duration_beats"}
        out: list[NoteDict] = []
        for n in notes:
            converted: NoteDict = {_MAP.get(k, k): v for k, v in n.items()}  # type: ignore[assignment]  # dynamic key remap; mypy can't narrow computed keys
            out.append(converted)
        return out

    def _scorer_for_instrument(
        self,
        instrument: str,
        backend_type: GeneratorBackend,
        bars: int,
        style: str,
    ) -> Callable[[list[NoteDict]], tuple[float, list[str]]] | None:
        """
        Return a scorer function for the given instrument role.

        Scoring is now instrument-role-based rather than backend-type-based so
        that Storpheus (and any other backend) benefits from rejection sampling,
        not just the IR backends.
        """
        from app.services.critic import (
            score_drum_notes,
            score_bass_notes,
            score_melody_notes,
            score_chord_notes,
        )
        _snake = self._ensure_snake_keys
        role = instrument.lower()
        _PERCUSSION_ROLES = {
            "drums", "percussion", "congas", "bongos", "timbales",
            "guacharaca", "tumbadora", "djembe", "cajon", "shaker",
            "tambourine", "cowbell", "claves", "maracas", "cabasa",
        }
        if role in _PERCUSSION_ROLES or backend_type == GeneratorBackend.DRUM_IR:
            fill_bars = [b for b in range(3, bars, 4)]
            return lambda notes: score_drum_notes(_snake(notes), fill_bars=fill_bars, bars=bars, style=style)
        if role == "bass" or backend_type == GeneratorBackend.BASS_IR:
            kick_beats = None
            if self._generation_context and self._generation_context.rhythm_spine:
                kick_beats = self._generation_context.rhythm_spine.kick_onsets
            return lambda notes: score_bass_notes(_snake(notes), kick_beats=kick_beats)
        if role in ("lead", "melody", "synth", "vocal") or backend_type == GeneratorBackend.MELODY_IR:
            return lambda notes: score_melody_notes(_snake(notes))
        _HARMONIC_ROLES = {
            "piano", "chords", "harmony", "keys", "organ", "guitar",
            "horns", "brass", "strings", "accordion", "gaita",
            "bandoneón", "charango", "cuatro", "tres", "marimba",
        }
        if role in _HARMONIC_ROLES or backend_type == GeneratorBackend.HARMONIC_IR:
            return lambda notes: score_chord_notes(_snake(notes))
        return None  # unknown role — no scoring

    def _candidates_for_role(
        self,
        instrument: str,
        preset_config: QualityPresetConfig,
        backend_type: GeneratorBackend,
    ) -> int:
        """
        Reduce candidate count for low-variance melodic instruments.

        Drums and bass have high output variance and benefit from many Storpheus
        candidates; melodic instruments (organ, guitar, strings, etc.) are more
        consistent across samples so two candidates capture most of the gain.
        Only applies when the backend is Storpheus — IR backends already handle
        this at the scorer level.
        """
        if backend_type != GeneratorBackend.STORPHEUS:
            return preset_config.num_candidates
        role = instrument.lower()
        _HIGH_VARIANCE_ROLES = {
            "drums", "bass", "percussion", "congas", "bongos", "timbales",
            "guacharaca", "tumbadora", "djembe", "cajon", "shaker",
        }
        if role in _HIGH_VARIANCE_ROLES:
            return preset_config.num_candidates
        # Melodic / harmonic tracks: cap at 2 for quality, keep as-is otherwise
        if preset_config.num_candidates > 2:
            return 2
        return preset_config.num_candidates

    async def _generate_with_coupling(
        self,
        backend: MusicGeneratorBackend,
        backend_type: GeneratorBackend,
        instrument: str,
        style: str,
        tempo: int,
        bars: int,
        key: str | None,
        chords: list[str] | None,
        preset_config: QualityPresetConfig,
        num_candidates: int,
        context: GenerationContext | None = None,
    ) -> GenerationResult:
        """Generate with coupled generation and parallel rejection sampling.

        For drums: captures rhythm spine for bass coupling.
        For bass: uses rhythm spine if available.
        Candidates are dispatched concurrently via asyncio.gather so N Storpheus
        calls run in parallel rather than sequentially.
        """
        gen_ctx: GenerationContext = {**(context or {})}

        if instrument == "bass" and preset_config.use_coupled_generation:
            if self._generation_context and self._generation_context.rhythm_spine:
                gen_ctx["rhythm_spine"] = self._generation_context.rhythm_spine
                gen_ctx["drum_kick_beats"] = self._generation_context.rhythm_spine.kick_onsets

        # Smarter candidate count — melodic instruments cap at 2 for Storpheus
        effective_candidates = self._candidates_for_role(instrument, preset_config, backend_type)

        use_rejection = preset_config.use_critic and effective_candidates > 1
        scorer = self._scorer_for_instrument(instrument, backend_type, bars, style) if use_rejection else None

        if scorer is not None:
            # Dispatch all candidates concurrently — the bottleneck is GPU inference
            # time on Storpheus, so parallel dispatch cuts wall-clock time from
            # N × T_inference to ≈ T_inference + network overhead.
            tasks = [
                backend.generate(instrument, style, tempo, bars, key, chords, context=gen_ctx)
                for _ in range(effective_candidates)
            ]
            candidates = await asyncio.gather(*tasks, return_exceptions=True)

            best_result: GenerationResult | None = None
            best_score = -1.0
            all_scores: list[float] = []

            for res in candidates:
                if isinstance(res, BaseException) or not res.success:
                    continue
                score, _ = scorer(res.notes)
                all_scores.append(score)
                if score > best_score:
                    best_score = score
                    best_result = res

            if best_result is not None:
                if instrument == "drums" and preset_config.use_coupled_generation:
                    self._capture_drum_context(best_result.notes, style, tempo, bars)
                scores_val: list[JSONValue] = list(all_scores)
                best_result.metadata.update({
                    "critic_score": best_score,
                    "rejection_attempts": len(all_scores),
                    "all_scores": scores_val,
                    "parallel_candidates": effective_candidates,
                })
                return best_result

        # Single generation (no rejection sampling, or all candidates failed)
        result = await backend.generate(
            instrument, style, tempo, bars, key, chords, context=gen_ctx
        )

        if result.success and instrument == "drums" and preset_config.use_coupled_generation:
            self._capture_drum_context(result.notes, style, tempo, bars)

        return result
    
    @staticmethod
    def _maybe_apply_expressiveness(
        result: GenerationResult,
        instrument: str,
        style: str,
        bars: int,
    ) -> GenerationResult:
        """Enrich a successful generation with velocity curves, CC, and humanization.

        Gated by SKIP_EXPRESSIVENESS for debugging raw model output.
        """
        from app.config import settings
        if settings.skip_expressiveness:
            logger.info("⏭️ Expressiveness post-processing skipped (SKIP_EXPRESSIVENESS=true)")
            return result

        expr = apply_expressiveness(
            notes=result.notes,
            style=style,
            bars=bars,
            instrument_role=instrument.lower(),
        )
        result.cc_events = (result.cc_events or []) + expr.get("cc_events", [])
        result.pitch_bends = (result.pitch_bends or []) + expr.get("pitch_bends", [])
        return result

    def _capture_drum_context(self, notes: list[NoteDict], style: str, tempo: int, bars: int) -> None:
        """Capture rhythm spine from drum generation for bass coupling."""
        snake_notes = self._ensure_snake_keys(notes)
        rhythm_spine = RhythmSpine.from_drum_notes(snake_notes, tempo=tempo, bars=bars, style=style)
        self._generation_context = CoupledGenState(
            rhythm_spine=rhythm_spine,
            drum_notes=notes,
            style=style,
            tempo=tempo,
            bars=bars,
        )
    
    async def _is_backend_available(self, backend: MusicGeneratorBackend) -> bool:
        """Check backend availability with caching.
        Only cache positive results so we re-check after Storpheus restarts or brief outages.
        """
        backend_type = backend.backend_type
        cache_key = backend_type.value

        if cache_key in self._availability_cache and self._availability_cache[cache_key]:
            return True

        available = await backend.is_available()
        if available:
            self._availability_cache[cache_key] = True
        return available
    
    def clear_cache(self) -> None:
        """Clear availability cache to force re-check."""
        self._availability_cache.clear()
    
    async def get_available_backends(self) -> list[GeneratorBackend]:
        """Get list of currently available backends."""
        available = []
        for backend_type in self.priority:
            backend = self.backend_map.get(backend_type)
            if backend and await self._is_backend_available(backend):
                available.append(backend_type)
        return available


# Singleton instance
_generator: MusicGenerator | None = None


def get_music_generator(backend_priority: list[GeneratorBackend] | None = None) -> MusicGenerator:
    """Get the singleton music generator instance."""
    global _generator
    if _generator is None:
        _generator = MusicGenerator(backend_priority)
    return _generator


def reset_music_generator() -> None:
    """Reset the singleton (useful for testing)."""
    global _generator
    _generator = None
