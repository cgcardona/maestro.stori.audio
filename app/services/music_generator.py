"""
Music Generation Service for Maestro.

Primary backend: Orpheus (required for composing). No pattern fallback;
other backends (IR, HuggingFace, LLM) can be used when explicitly requested
or configured in priority. Coupled generation: drums first, then bass locked to kick map.

Performance notes:
- Candidate generation is parallelised with asyncio.gather so all N candidates
  are dispatched to Orpheus simultaneously instead of sequentially.
- Rejection-sampling scoring is now instrument-role-based (not backend-type-based),
  which means Orpheus finally benefits from the quality-preset candidate selection.
- Melodic/pad instruments get fewer candidates than drums/bass because their
  output variance is lower and the critic adds less marginal value.
"""
import asyncio
import logging
from typing import Optional, Any
from dataclasses import dataclass, field

from app.services.backends.base import GeneratorBackend, GenerationResult, MusicGeneratorBackend
from app.services.backends.text2midi import Text2MidiGeneratorBackend
from app.services.backends.drum_ir import DrumSpecBackend
from app.services.backends.bass_ir import BassSpecBackend
from app.services.backends.harmonic_ir import HarmonicSpecBackend
from app.services.backends.melody_ir import MelodySpecBackend
from app.services.backends.orpheus import OrpheusBackend
from app.services.backends.huggingface import HuggingFaceBackend
from app.services.backends.llm import LLMGeneratorBackend
from app.services.groove_engine import RhythmSpine, extract_kick_onsets
from app.services.expressiveness import apply_expressiveness
from app.config import settings

logger = logging.getLogger(__name__)


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
        num_candidates=6,
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
class GenerationContext:
    """Context shared across coupled generation calls."""
    rhythm_spine: Optional[RhythmSpine] = None
    drum_notes: Optional[list[dict]] = None
    style: str = "trap"
    tempo: int = 120
    bars: int = 16


class MusicGenerator:
    """
    Music generation service for composing.

    Default backend: Orpheus (required for composing). No pattern fallback;
    if Orpheus is unavailable, generation fails with a clear error.
    """
    
    def __init__(self, backend_priority: Optional[list[GeneratorBackend]] = None):
        """
        Initialize with custom backend priority.
        
        Args:
            backend_priority: Ordered list of backends to try
        """
        # Composing requires Orpheus; no pattern fallback (fail fast)
        if backend_priority is None:
            backend_priority = [
                GeneratorBackend.ORPHEUS,
            ]
        
        # Initialize backends
        self.backend_map = {
            GeneratorBackend.TEXT2MIDI: Text2MidiGeneratorBackend(),
            GeneratorBackend.DRUM_IR: DrumSpecBackend(),
            GeneratorBackend.BASS_IR: BassSpecBackend(),
            GeneratorBackend.HARMONIC_IR: HarmonicSpecBackend(),
            GeneratorBackend.MELODY_IR: MelodySpecBackend(),
            GeneratorBackend.ORPHEUS: OrpheusBackend(),
            GeneratorBackend.HUGGINGFACE: HuggingFaceBackend(),
            GeneratorBackend.LLM: LLMGeneratorBackend(),
        }
        
        self.priority = backend_priority
        self._availability_cache: dict[str, bool] = {}
        
        # Cached context for coupled generation
        self._generation_context: Optional[GenerationContext] = None
    
    def set_generation_context(self, context: GenerationContext):
        """Set context for coupled generation (e.g., rhythm spine from drums)."""
        self._generation_context = context
    
    def clear_generation_context(self):
        """Clear the generation context."""
        self._generation_context = None
    
    async def generate(
        self,
        instrument: str,
        style: str,
        tempo: int,
        bars: int,
        key: Optional[str] = None,
        chords: Optional[list[str]] = None,
        preferred_backend: Optional[GeneratorBackend] = None,
        **kwargs,
    ) -> GenerationResult:
        """
        Generate MIDI notes (default: Orpheus; no pattern fallback).
        
        Args:
            instrument: drums, bass, piano, lead, etc.
            style: boom_bap, jazz, house, trap, etc.
            tempo: BPM
            bars: Number of bars to generate
            key: Musical key (e.g., "Am", "C")
            chords: Chord progression
            preferred_backend: Force a specific backend
            **kwargs: Additional parameters for backends
        
        Returns:
            GenerationResult with notes and metadata
        """
        quality_preset = kwargs.pop("quality_preset", "quality")  # "fast" | "balanced" | "quality"
        num_candidates = kwargs.pop("num_candidates", None)
        
        # Get preset config
        preset_config = QUALITY_PRESETS.get(quality_preset, QUALITY_PRESETS["balanced"])
        n = num_candidates or preset_config.num_candidates

        # If specific backend requested
        if preferred_backend:
            backend = self.backend_map.get(preferred_backend)
            if backend:
                result = await self._generate_with_coupling(
                    backend, preferred_backend, instrument, style, tempo, bars, key, chords,
                    preset_config, n, **kwargs
                )
                if result.success:
                    result = self._apply_expressiveness(result, instrument, style, bars)
                return result
            else:
                logger.warning(f"Requested backend not available: {preferred_backend}")
        
        # Try backends in priority order
        last_failure: Optional[GenerationResult] = None
        for backend_type in self.priority:
            backend = self.backend_map.get(backend_type)
            if not backend:
                continue
            
            if not await self._is_backend_available(backend):
                continue
            
            result = await self._generate_with_coupling(
                backend, backend_type, instrument, style, tempo, bars, key, chords,
                preset_config, n, **kwargs
            )
            
            if result.success:
                result = self._apply_expressiveness(result, instrument, style, bars)
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
            "Music generation (Orpheus) is currently unavailable. "
            "Ensure the Orpheus service is running (port 10002) and reachable. "
            "Please try again once the service is up."
        )
        logger.error(error_msg)
        return GenerationResult(
            success=False,
            notes=[],
            backend_used=GeneratorBackend.ORPHEUS,
            metadata={},
            error=error_msg,
        )
    
    @staticmethod
    def _ensure_snake_keys(notes: list[dict]) -> list[dict]:
        """Return a shallow copy of notes with snake_case beat keys for critics."""
        _MAP = {"startBeat": "start_beat", "durationBeats": "duration_beats"}
        out: list[dict] = []
        for n in notes:
            converted = {_MAP.get(k, k): v for k, v in n.items()}
            out.append(converted)
        return out

    def _scorer_for_instrument(
        self,
        instrument: str,
        backend_type: GeneratorBackend,
        bars: int,
        style: str,
    ):
        """
        Return a scorer function for the given instrument role.

        Scoring is now instrument-role-based rather than backend-type-based so
        that Orpheus (and any other backend) benefits from rejection sampling,
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
        if role in ("drums", "percussion") or backend_type == GeneratorBackend.DRUM_IR:
            fill_bars = [b for b in range(3, bars, 4)]
            return lambda notes: score_drum_notes(_snake(notes), fill_bars=fill_bars, bars=bars, style=style)
        if role == "bass" or backend_type == GeneratorBackend.BASS_IR:
            kick_beats = None
            if self._generation_context and self._generation_context.rhythm_spine:
                kick_beats = self._generation_context.rhythm_spine.kick_onsets
            return lambda notes: score_bass_notes(_snake(notes), kick_beats=kick_beats)
        if role in ("lead", "melody", "synth", "vocal") or backend_type == GeneratorBackend.MELODY_IR:
            return lambda notes: score_melody_notes(_snake(notes))
        if role in ("piano", "chords", "harmony", "keys", "organ", "guitar", "horns", "brass", "strings") or backend_type == GeneratorBackend.HARMONIC_IR:
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

        Drums and bass have high output variance and benefit from many Orpheus
        candidates; melodic instruments (organ, guitar, strings, etc.) are more
        consistent across samples so two candidates capture most of the gain.
        Only applies when the backend is Orpheus — IR backends already handle
        this at the scorer level.
        """
        if backend_type != GeneratorBackend.ORPHEUS:
            return preset_config.num_candidates
        role = instrument.lower()
        if role in ("drums", "bass", "percussion"):
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
        key: Optional[str],
        chords: Optional[list[str]],
        preset_config: QualityPresetConfig,
        num_candidates: int,
        **kwargs,
    ) -> GenerationResult:
        """
        Generate with coupled generation and parallel rejection sampling.

        For drums: captures rhythm spine for bass coupling.
        For bass: uses rhythm spine if available.
        Candidates are dispatched concurrently via asyncio.gather so N Orpheus
        calls run in parallel rather than sequentially.
        """
        # Prepare kwargs for coupled generation
        gen_kwargs = dict(kwargs)

        # Bass coupling: pass rhythm spine if available
        if instrument == "bass" and preset_config.use_coupled_generation:
            if self._generation_context and self._generation_context.rhythm_spine:
                gen_kwargs["rhythm_spine"] = self._generation_context.rhythm_spine
                gen_kwargs["drum_kick_beats"] = self._generation_context.rhythm_spine.kick_onsets

        # Smarter candidate count — melodic instruments cap at 2 for Orpheus
        effective_candidates = self._candidates_for_role(instrument, preset_config, backend_type)

        use_rejection = preset_config.use_critic and effective_candidates > 1
        scorer = self._scorer_for_instrument(instrument, backend_type, bars, style) if use_rejection else None

        if scorer is not None:
            # Dispatch all candidates concurrently — the bottleneck is GPU inference
            # time on Orpheus, so parallel dispatch cuts wall-clock time from
            # N × T_inference to ≈ T_inference + network overhead.
            tasks = [
                backend.generate(instrument, style, tempo, bars, key, chords, **gen_kwargs)
                for _ in range(effective_candidates)
            ]
            candidates = await asyncio.gather(*tasks, return_exceptions=True)

            best_result: Optional[GenerationResult] = None
            best_score = -1.0
            all_scores: list[float] = []

            for res in candidates:
                if isinstance(res, Exception) or not res.success:
                    continue
                score, _ = scorer(res.notes)
                all_scores.append(score)
                if score > best_score:
                    best_score = score
                    best_result = res

            if best_result is not None:
                if instrument == "drums" and preset_config.use_coupled_generation:
                    self._capture_drum_context(best_result.notes, style, tempo, bars)
                best_result.metadata.update({
                    "critic_score": best_score,
                    "rejection_attempts": len(all_scores),
                    "all_scores": all_scores,
                    "parallel_candidates": effective_candidates,
                })
                return best_result

        # Single generation (no rejection sampling, or all candidates failed)
        result = await backend.generate(
            instrument, style, tempo, bars, key, chords, **gen_kwargs
        )

        if result.success and instrument == "drums" and preset_config.use_coupled_generation:
            self._capture_drum_context(result.notes, style, tempo, bars)

        return result
    
    @staticmethod
    def _apply_expressiveness(
        result: GenerationResult,
        instrument: str,
        style: str,
        bars: int,
    ) -> GenerationResult:
        """
        Enrich a successful generation with velocity curves, CC automation,
        pitch bends, and timing humanization.
        """
        expr = apply_expressiveness(
            notes=result.notes,
            style=style,
            bars=bars,
            instrument_role=instrument.lower(),
        )
        result.cc_events = (result.cc_events or []) + expr.get("cc_events", [])
        result.pitch_bends = (result.pitch_bends or []) + expr.get("pitch_bends", [])
        return result

    def _capture_drum_context(self, notes: list[dict], style: str, tempo: int, bars: int):
        """Capture rhythm spine from drum generation for bass coupling."""
        rhythm_spine = RhythmSpine.from_drum_notes(notes, tempo=tempo, bars=bars, style=style)
        self._generation_context = GenerationContext(
            rhythm_spine=rhythm_spine,
            drum_notes=notes,
            style=style,
            tempo=tempo,
            bars=bars,
        )
    
    async def _is_backend_available(self, backend: MusicGeneratorBackend) -> bool:
        """Check backend availability with caching.
        Only cache positive results so we re-check after Orpheus restarts or brief outages.
        """
        backend_type = backend.backend_type
        cache_key = backend_type.value

        if cache_key in self._availability_cache and self._availability_cache[cache_key]:
            return True

        available = await backend.is_available()
        if available:
            self._availability_cache[cache_key] = True
        return available
    
    def clear_cache(self):
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
_generator: Optional[MusicGenerator] = None


def get_music_generator(backend_priority: Optional[list[GeneratorBackend]] = None) -> MusicGenerator:
    """Get the singleton music generator instance."""
    global _generator
    if _generator is None:
        _generator = MusicGenerator(backend_priority)
    return _generator


def reset_music_generator():
    """Reset the singleton (useful for testing)."""
    global _generator
    _generator = None
