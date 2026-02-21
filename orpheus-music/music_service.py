"""
Orpheus Music Service
Generates MIDI using Orpheus Music Transformer and converts to Stori tool calls

Architecture:
- Policy Layer: Translates musical intent → generation controls
- Caching: Result caching (90% cost savings) + seed caching
- Optimization: Smart token allocation, parameter inference

This is where UX philosophy and musical taste live.
"""
from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional
from gradio_client import Client, handle_file
from midiutil import MIDIFile
from dataclasses import dataclass
from collections import OrderedDict
from time import time
import mido
import tempfile
import os
import json
import hashlib
import logging

# Import our policy layer and quality metrics
from generation_policy import (
    intent_to_controls,
    controls_to_orpheus_params,
    GenerationControlVector,
    get_policy_version,
)
from quality_metrics import analyze_quality, compare_generations

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Orpheus Music Service")

# Lazy-load Gradio client
gradio_client = None

def get_client():
    global gradio_client
    if gradio_client is None:
        # Pass HF token so the Space attributes GPU usage to our account (required for quota).
        # Maestro sends token in Authorization header; Orpheus uses env so the Gradio client has it.
        hf_token = os.environ.get("HF_TOKEN") or os.environ.get("STORI_HF_API_KEY")
        if not hf_token:
            logger.warning("No HF_TOKEN or STORI_HF_API_KEY set; Gradio Space may return GPU quota errors")
        gradio_client = Client("asigalov61/Orpheus-Music-Transformer", hf_token=hf_token)
    return gradio_client


# ============================================================================
# CACHING SYSTEM (with LRU + TTL)
# ============================================================================

from collections import OrderedDict
from time import time

# Cache configuration
MAX_CACHE_SIZE = 1000  # Maximum number of cached results
CACHE_TTL_SECONDS = 86400  # 24 hours

@dataclass
class CacheEntry:
    """Cache entry with TTL support."""
    result: dict
    timestamp: float
    hits: int = 0

# Result cache: stores complete generation results (LRU with TTL)
_result_cache: OrderedDict[str, CacheEntry] = OrderedDict()

# Seed MIDI cache: reuses seed files for same genre/tempo
_seed_cache = {}

def get_cache_key(request) -> str:
    """
    Generate cache key from request parameters.
    
    Includes intent vector fields so different intents don't collide.
    Rounds continuous values to avoid cache misses from tiny differences.
    """
    key_data = {
        "genre": request.genre,
        "tempo": request.tempo,
        "instruments": sorted(request.instruments),
        "bars": request.bars,
        # Include intent fields (rounded to avoid spurious misses)
        "musical_goals": sorted(request.musical_goals or []),
        "tone_brightness": round(request.tone_brightness, 1),
        "tone_warmth": round(request.tone_warmth, 1),
        "energy_intensity": round(request.energy_intensity, 1),
        "energy_excitement": round(request.energy_excitement, 1),
        "complexity": round(request.complexity, 1),
        "quality_preset": request.quality_preset,
    }
    key_str = json.dumps(key_data, sort_keys=True)
    return hashlib.md5(key_str.encode()).hexdigest()


def get_cached_result(cache_key: str) -> Optional[dict]:
    """
    Get cached generation result if available and not expired.
    
    Implements LRU: moves accessed item to end of OrderedDict.
    """
    if cache_key not in _result_cache:
        logger.info(f"❌ Cache miss for {cache_key}")
        return None
    
    entry = _result_cache[cache_key]
    
    # Check TTL
    age = time() - entry.timestamp
    if age > CACHE_TTL_SECONDS:
        logger.info(f"⏰ Cache expired for {cache_key} (age: {age:.0f}s)")
        del _result_cache[cache_key]
        return None
    
    # LRU: move to end (most recently used)
    _result_cache.move_to_end(cache_key)
    entry.hits += 1
    
    logger.info(f"✅ Cache hit for {cache_key} (hits: {entry.hits}, age: {age:.0f}s)")
    return entry.result


def cache_result(cache_key: str, result: dict):
    """
    Cache a generation result with LRU eviction.
    
    If cache is full, evicts least recently used item.
    """
    # Evict oldest if at capacity
    if len(_result_cache) >= MAX_CACHE_SIZE:
        oldest_key, oldest_entry = _result_cache.popitem(last=False)  # FIFO
        logger.info(f"🗑️ Evicted cache entry {oldest_key} (hits: {oldest_entry.hits})")
    
    _result_cache[cache_key] = CacheEntry(
        result=result,
        timestamp=time(),
        hits=0
    )
    logger.info(f"💾 Cached result {cache_key} (cache size: {len(_result_cache)})")


class GenerateRequest(BaseModel):
    """
    Music generation request with rich intent support.
    
    Can be called with simple params (genre, tempo) or rich intent data
    from the LLM intent system (musical_goals, tone/energy vectors).
    """
    # Basic params
    genre: str = "boom_bap"
    tempo: int = 90
    instruments: List[str] = ["drums", "bass"]
    bars: int = 4
    key: Optional[str] = None
    
    # Intent system outputs (from LLM classification)
    musical_goals: Optional[List[str]] = None          # ["dark", "energetic", "minimal"]
    tone_brightness: float = 0.0                       # -1 (dark) to +1 (bright)
    tone_warmth: float = 0.0                           # -1 (cold) to +1 (warm)
    energy_intensity: float = 0.0                      # -1 (calm) to +1 (intense)
    energy_excitement: float = 0.0                     # -1 (laid back) to +1 (exciting)
    complexity: float = 0.5                            # 0 (simple) to 1 (complex)
    
    # Quality control
    quality_preset: str = "balanced"                   # "fast" | "balanced" | "quality"
    
    # Advanced overrides (for power users / testing)
    temperature: Optional[float] = None                # Override computed temperature
    top_p: Optional[float] = None                      # Override computed top_p
    
    # Legacy support
    style_hints: Optional[List[str]] = None            # Deprecated: use musical_goals


class ToolCall(BaseModel):
    tool: str
    params: dict


class GenerateResponse(BaseModel):
    success: bool
    tool_calls: List[dict]
    error: Optional[str] = None
    metadata: Optional[dict] = None  # Quality metrics, policy version, etc.


# GM program numbers by instrument type
INSTRUMENT_PROGRAMS = {
    "piano": 0,
    "electric_piano": 4,
    "organ": 16,
    "guitar": 25,
    "acoustic_guitar": 25,
    "electric_guitar": 27,
    "bass": 33,
    "electric_bass": 33,
    "synth_bass": 38,
    "strings": 48,
    "synth": 80,
    "pad": 88,
    "lead": 80,
}

# Note: Musical parameter inference moved to generation_policy.py
# This keeps the policy logic separate and testable


# ---------------------------------------------------------------------------
# Seed MIDI: genre-specific patterns with full expressiveness
#
# Reference heuristics (200 MAESTRO performances + orchestral concerto):
#   CC density:   ~27 CC events per bar (pedal-heavy classical)
#   Velocity:     mean 64, stdev 17, full 5-127 range
#   Timing:       92.7% of notes off 16th grid (0.06 beat deviation)
#   Key CCs:      CC 64 (sustain), CC 67 (soft pedal), CC 11 (expression),
#                 CC 1 (mod wheel), CC 91 (reverb)
# ---------------------------------------------------------------------------

# Semitone offset from C for each key root (for transposition)
_KEY_OFFSETS: dict = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
    "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11,
}


def _key_offset(key: Optional[str]) -> int:
    """Semitone transposition from C for a key string like 'Am', 'F#', 'Eb'."""
    if not key:
        return 0
    root = key.rstrip("mM# ").rstrip("b")
    if len(key) > 1 and key[1] in ("#", "b"):
        root = key[:2]
    return _KEY_OFFSETS.get(root, 0)


def _add_sustain_pedal(midi: MIDIFile, track: int, channel: int, bars: int = 1):
    """Add sustain pedal down/up per bar — the single most common CC in pro MIDI."""
    for bar in range(bars):
        beat = bar * 4
        midi.addControllerEvent(track, channel, beat, 64, 127)
        midi.addControllerEvent(track, channel, beat + 3.75, 64, 0)


def _add_expression_curve(midi: MIDIFile, track: int, channel: int, bars: int = 1,
                          low: int = 80, high: int = 120):
    """CC 11 swell: rise to bar midpoint, fall back. ~8 events per bar."""
    for bar in range(bars):
        base = bar * 4
        steps = 8
        for i in range(steps):
            t = i / (steps - 1)
            # Triangle wave: 0→1→0
            shape = 1.0 - abs(2.0 * t - 1.0)
            val = int(low + (high - low) * shape)
            midi.addControllerEvent(track, channel, base + i * 4 / steps, 11, min(val, 127))


def _add_mod_wheel(midi: MIDIFile, track: int, channel: int, bars: int = 1,
                   depth: int = 40):
    """CC 1 (mod wheel / vibrato) — gentle rise on sustained passages."""
    for bar in range(bars):
        base = bar * 4
        midi.addControllerEvent(track, channel, base, 1, 0)
        midi.addControllerEvent(track, channel, base + 1, 1, depth // 3)
        midi.addControllerEvent(track, channel, base + 2, 1, depth)
        midi.addControllerEvent(track, channel, base + 3, 1, depth // 2)


# ---------------------------------------------------------------------------
# Genre seed definitions
# ---------------------------------------------------------------------------

def _seed_boom_bap(midi: MIDIFile, offset: int):
    # Drums — swung kick/snare with ghost hats
    midi.addNote(0, 9, 36, 0, 0.5, 105)
    midi.addNote(0, 9, 42, 0, 0.25, 75)
    midi.addNote(0, 9, 42, 0.5, 0.25, 55)        # ghost hat
    midi.addNote(0, 9, 38, 1, 0.5, 95)
    midi.addNote(0, 9, 42, 1.5, 0.25, 65)
    midi.addNote(0, 9, 36, 2, 0.5, 100)
    midi.addNote(0, 9, 42, 2, 0.25, 70)
    midi.addNote(0, 9, 42, 2.5, 0.25, 50)        # ghost hat
    midi.addNote(0, 9, 38, 3, 0.5, 90)
    midi.addNote(0, 9, 42, 3, 0.25, 60)
    midi.addNote(0, 9, 42, 3.5, 0.25, 72)
    # Bass (ch 0)
    midi.addNote(1, 0, 48 + offset, 0, 1.0, 100)
    midi.addNote(1, 0, 48 + offset, 2, 0.75, 90)
    midi.addNote(1, 0, 50 + offset, 2.75, 0.25, 80)
    # Melody (ch 1)
    midi.addNote(2, 1, 60 + offset, 0.5, 0.5, 85)
    midi.addNote(2, 1, 63 + offset, 1, 0.75, 75)
    midi.addNote(2, 1, 65 + offset, 2.5, 0.5, 70)
    _add_sustain_pedal(midi, 2, 1, 1)


def _seed_trap(midi: MIDIFile, offset: int):
    # Drums — 808 kick + rapid hats with velocity ramps
    midi.addNote(0, 9, 36, 0, 0.25, 115)
    midi.addNote(0, 9, 36, 0.75, 0.25, 105)
    midi.addNote(0, 9, 38, 1, 0.5, 95)
    midi.addNote(0, 9, 39, 1, 0.5, 55)           # clap layer
    for i in range(8):
        vel = 60 + (i % 3) * 12
        midi.addNote(0, 9, 42, i * 0.5, 0.125, vel)
    # Bass
    midi.addNote(1, 0, 48 + offset, 0, 1.5, 115)
    midi.addNote(1, 0, 50 + offset, 1.5, 0.5, 100)
    midi.addNote(1, 0, 46 + offset, 2, 1.5, 110)
    _add_expression_curve(midi, 1, 0, 1, 90, 127)
    # Melody — staccato high
    midi.addNote(2, 1, 72 + offset, 0, 0.25, 95)
    midi.addNote(2, 1, 74 + offset, 0.5, 0.25, 85)
    midi.addNote(2, 1, 72 + offset, 1.5, 0.5, 90)


def _seed_house(midi: MIDIFile, offset: int):
    # Drums — four on the floor + off-beat hats
    for beat in range(4):
        midi.addNote(0, 9, 36, beat, 0.5, 105 - beat * 3)
    for beat in range(4):
        midi.addNote(0, 9, 42, beat + 0.5, 0.25, 78 + beat * 2)
    midi.addNote(0, 9, 38, 1, 0.5, 90)
    midi.addNote(0, 9, 38, 3, 0.5, 85)
    # Bass — octave pumping
    midi.addNote(1, 0, 36 + offset, 0, 0.75, 105)
    midi.addNote(1, 0, 48 + offset, 1, 0.5, 85)
    midi.addNote(1, 0, 36 + offset, 2, 0.75, 100)
    midi.addNote(1, 0, 48 + offset, 3, 0.5, 80)
    # Chords (ch 1) — off-beat stabs
    for beat in range(4):
        midi.addNote(2, 1, 60 + offset, beat + 0.5, 0.25, 75 + beat * 3)
        midi.addNote(2, 1, 64 + offset, beat + 0.5, 0.25, 70 + beat * 3)
        midi.addNote(2, 1, 67 + offset, beat + 0.5, 0.25, 68 + beat * 3)


def _seed_techno(midi: MIDIFile, offset: int):
    for beat in range(4):
        midi.addNote(0, 9, 36, beat, 0.5, 110)
    for i in range(8):
        midi.addNote(0, 9, 42, i * 0.5, 0.125, 70 + (i % 2) * 15)
    midi.addNote(0, 9, 39, 1, 0.5, 85)
    midi.addNote(0, 9, 39, 3, 0.5, 80)
    # Acid bass line
    midi.addNote(1, 0, 36 + offset, 0, 0.25, 110)
    midi.addNote(1, 0, 36 + offset, 0.5, 0.25, 90)
    midi.addNote(1, 0, 39 + offset, 1, 0.5, 100)
    midi.addNote(1, 0, 36 + offset, 2, 0.25, 105)
    midi.addNote(1, 0, 41 + offset, 2.75, 0.25, 85)
    _add_expression_curve(midi, 1, 0, 1, 70, 127)
    # Sparse stab (ch 1)
    midi.addNote(2, 1, 60 + offset, 0.5, 0.125, 90)
    midi.addNote(2, 1, 63 + offset, 2, 0.125, 85)


def _seed_jazz(midi: MIDIFile, offset: int):
    # Drums — ride + kick/snare comping
    midi.addNote(0, 9, 51, 0, 0.5, 80)           # ride
    midi.addNote(0, 9, 51, 1, 0.5, 75)
    midi.addNote(0, 9, 51, 2, 0.5, 82)
    midi.addNote(0, 9, 51, 3, 0.5, 70)
    midi.addNote(0, 9, 36, 0, 0.5, 70)
    midi.addNote(0, 9, 36, 2.5, 0.5, 60)
    midi.addNote(0, 9, 38, 1.5, 0.25, 50)        # ghost snare
    midi.addNote(0, 9, 44, 1, 0.25, 55)          # pedal hh
    midi.addNote(0, 9, 44, 3, 0.25, 50)
    # Walking bass (ch 0) — chromatic passing
    midi.addNote(1, 0, 48 + offset, 0, 0.9, 85)
    midi.addNote(1, 0, 50 + offset, 1, 0.9, 80)
    midi.addNote(1, 0, 52 + offset, 2, 0.9, 82)
    midi.addNote(1, 0, 53 + offset, 3, 0.9, 78)
    # Chord voicing (ch 1) — rootless Dm9
    midi.addNote(2, 1, 64 + offset, 0, 3.5, 65)
    midi.addNote(2, 1, 67 + offset, 0, 3.5, 60)
    midi.addNote(2, 1, 72 + offset, 0, 3.5, 58)
    midi.addNote(2, 1, 74 + offset, 0, 3.5, 55)
    _add_sustain_pedal(midi, 2, 1, 1)
    _add_mod_wheel(midi, 2, 1, 1, 25)


def _seed_neosoul(midi: MIDIFile, offset: int):
    # Drums — lazy pocket
    midi.addNote(0, 9, 36, 0, 0.5, 90)
    midi.addNote(0, 9, 38, 1, 0.5, 70)
    midi.addNote(0, 9, 36, 2.5, 0.5, 85)
    midi.addNote(0, 9, 38, 3, 0.5, 65)
    for i in [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5]:
        midi.addNote(0, 9, 42, i, 0.25, 50 + int(i * 4) % 20)
    # Bass — Erykah-style
    midi.addNote(1, 0, 43 + offset, 0, 1.0, 90)
    midi.addNote(1, 0, 46 + offset, 1.5, 0.5, 80)
    midi.addNote(1, 0, 48 + offset, 2, 1.25, 85)
    midi.addNote(1, 0, 46 + offset, 3.5, 0.5, 75)
    # Rhodes chord (ch 1)
    for p in [60, 63, 67, 70]:
        midi.addNote(2, 1, p + offset, 0, 3.5, 55 + (p % 5) * 3)
    _add_sustain_pedal(midi, 2, 1, 1)
    _add_expression_curve(midi, 2, 1, 1, 60, 100)


def _seed_classical(midi: MIDIFile, offset: int):
    # Piano seed — Alberti bass + melody + heavy pedal (matches MAESTRO heuristics)
    # Left hand — Alberti (ch 0)
    pattern = [48, 55, 52, 55]  # C-G-E-G
    for i, p in enumerate(pattern):
        midi.addNote(1, 0, p + offset, i * 0.5, 0.45, 60 + (i % 2) * 8)
    pattern2 = [47, 55, 50, 55]
    for i, p in enumerate(pattern2):
        midi.addNote(1, 0, p + offset, 2 + i * 0.5, 0.45, 58 + (i % 2) * 10)
    # Right hand melody (ch 1)
    melody = [(67, 0, 0.75, 80), (65, 0.75, 0.25, 65), (64, 1, 1.0, 75),
              (62, 2, 0.5, 70), (64, 2.5, 0.5, 72), (67, 3, 1.0, 78)]
    for p, t, d, v in melody:
        midi.addNote(2, 1, p + offset, t, d, v)
    _add_sustain_pedal(midi, 1, 0, 1)
    _add_sustain_pedal(midi, 2, 1, 1)
    _add_expression_curve(midi, 2, 1, 1, 70, 110)


def _seed_cinematic(midi: MIDIFile, offset: int):
    # Strings pad + timpani + French horn melody
    midi.addNote(0, 9, 47, 0, 0.5, 100)          # timpani low-mid tom
    midi.addNote(0, 9, 49, 3, 0.5, 90)           # crash
    # Low strings (ch 0) — sustained fifths
    midi.addNote(1, 0, 36 + offset, 0, 4.0, 70)
    midi.addNote(1, 0, 43 + offset, 0, 4.0, 65)
    # Horn melody (ch 1)
    midi.addNote(2, 1, 60 + offset, 0, 1.5, 80)
    midi.addNote(2, 1, 64 + offset, 1.5, 1.0, 85)
    midi.addNote(2, 1, 67 + offset, 2.5, 1.5, 90)
    _add_expression_curve(midi, 1, 0, 1, 50, 100)
    _add_expression_curve(midi, 2, 1, 1, 60, 110)
    _add_mod_wheel(midi, 2, 1, 1, 50)


def _seed_ambient(midi: MIDIFile, offset: int):
    # Pads — very slow, sustained, soft
    midi.addNote(1, 0, 48 + offset, 0, 4.0, 45)
    midi.addNote(1, 0, 55 + offset, 0, 4.0, 40)
    midi.addNote(1, 0, 60 + offset, 0, 4.0, 38)
    midi.addNote(2, 1, 67 + offset, 1, 3.0, 50)
    midi.addNote(2, 1, 72 + offset, 2, 2.0, 45)
    _add_expression_curve(midi, 1, 0, 1, 30, 80)
    _add_mod_wheel(midi, 1, 0, 1, 60)
    _add_mod_wheel(midi, 2, 1, 1, 50)


def _seed_reggae(midi: MIDIFile, offset: int):
    # One-drop drums
    midi.addNote(0, 9, 38, 1, 0.5, 100)          # snare on 3 (half-time)
    midi.addNote(0, 9, 36, 1, 0.5, 95)           # kick on 3
    midi.addNote(0, 9, 42, 0, 0.25, 70)
    midi.addNote(0, 9, 42, 0.5, 0.25, 60)
    midi.addNote(0, 9, 42, 1.5, 0.25, 65)
    midi.addNote(0, 9, 42, 2, 0.25, 70)
    midi.addNote(0, 9, 42, 2.5, 0.25, 55)
    midi.addNote(0, 9, 42, 3, 0.25, 68)
    midi.addNote(0, 9, 42, 3.5, 0.25, 58)
    # Bass (ch 0) — syncopated roots
    midi.addNote(1, 0, 48 + offset, 0, 0.75, 100)
    midi.addNote(1, 0, 48 + offset, 1.5, 0.5, 85)
    midi.addNote(1, 0, 53 + offset, 2, 0.75, 90)
    midi.addNote(1, 0, 48 + offset, 3, 0.75, 80)
    # Skank organ (ch 1) — off-beats
    for beat in range(4):
        midi.addNote(2, 1, 60 + offset, beat + 0.5, 0.2, 65)
        midi.addNote(2, 1, 64 + offset, beat + 0.5, 0.2, 60)
        midi.addNote(2, 1, 67 + offset, beat + 0.5, 0.2, 58)


def _seed_funk(midi: MIDIFile, offset: int):
    # Drums — 16th note groove
    for i in range(16):
        beat = i * 0.25
        if i in (0, 4, 10):                       # kick
            midi.addNote(0, 9, 36, beat, 0.25, 105 - (i % 3) * 5)
        if i in (4, 12):                          # snare
            midi.addNote(0, 9, 38, beat, 0.25, 95)
        vel = 55 + (i % 4) * 10 if i not in (0, 4, 8, 12) else 80
        midi.addNote(0, 9, 42, beat, 0.125, vel)
    # Slap bass (ch 0)
    midi.addNote(1, 0, 48 + offset, 0, 0.2, 115)
    midi.addNote(1, 0, 48 + offset, 0.5, 0.2, 95)
    midi.addNote(1, 0, 50 + offset, 1, 0.25, 100)
    midi.addNote(1, 0, 53 + offset, 1.75, 0.2, 90)
    midi.addNote(1, 0, 48 + offset, 2, 0.2, 110)
    midi.addNote(1, 0, 55 + offset, 2.75, 0.2, 85)
    midi.addNote(1, 0, 53 + offset, 3, 0.5, 95)
    # Clavinet stab (ch 1)
    for beat in [0.5, 1.5, 2.5, 3.5]:
        midi.addNote(2, 1, 60 + offset, beat, 0.15, 90)
        midi.addNote(2, 1, 64 + offset, beat, 0.15, 85)


def _seed_dnb(midi: MIDIFile, offset: int):
    # Drums — broken beat at high tempo
    midi.addNote(0, 9, 36, 0, 0.25, 110)
    midi.addNote(0, 9, 38, 0.5, 0.25, 100)
    midi.addNote(0, 9, 36, 1.25, 0.25, 100)
    midi.addNote(0, 9, 38, 2, 0.25, 105)
    midi.addNote(0, 9, 36, 2.75, 0.25, 95)
    midi.addNote(0, 9, 38, 3.5, 0.25, 98)
    for i in range(8):
        midi.addNote(0, 9, 42, i * 0.5, 0.125, 65 + (i % 3) * 8)
    # Reese bass (ch 0) — sustained + movement
    midi.addNote(1, 0, 36 + offset, 0, 2.0, 110)
    midi.addNote(1, 0, 39 + offset, 2, 1.5, 100)
    midi.addNote(1, 0, 34 + offset, 3.5, 0.5, 105)
    _add_expression_curve(midi, 1, 0, 1, 80, 127)
    # Pad (ch 1)
    midi.addNote(2, 1, 60 + offset, 0, 4.0, 55)
    midi.addNote(2, 1, 63 + offset, 0, 4.0, 50)
    midi.addNote(2, 1, 67 + offset, 0, 4.0, 48)
    _add_mod_wheel(midi, 2, 1, 1, 40)


def _seed_dubstep(midi: MIDIFile, offset: int):
    midi.addNote(0, 9, 36, 0, 0.5, 115)
    midi.addNote(0, 9, 38, 1, 0.5, 105)
    midi.addNote(0, 9, 36, 2, 0.5, 110)
    midi.addNote(0, 9, 38, 3, 0.5, 100)
    for i in range(8):
        midi.addNote(0, 9, 42, i * 0.5, 0.125, 60 + (i % 2) * 20)
    # Wobble bass (ch 0)
    midi.addNote(1, 0, 36 + offset, 0, 2.0, 120)
    midi.addNote(1, 0, 34 + offset, 2, 2.0, 115)
    _add_expression_curve(midi, 1, 0, 1, 60, 127)
    # Stab (ch 1)
    midi.addNote(2, 1, 60 + offset, 0, 0.25, 100)
    midi.addNote(2, 1, 63 + offset, 0, 0.25, 95)
    midi.addNote(2, 1, 67 + offset, 0, 0.25, 90)


def _seed_drill(midi: MIDIFile, offset: int):
    # Sliding 808 + rapid hats
    midi.addNote(0, 9, 36, 0, 0.5, 115)
    midi.addNote(0, 9, 36, 1.5, 0.25, 105)
    midi.addNote(0, 9, 38, 1, 0.5, 95)
    midi.addNote(0, 9, 39, 1, 0.5, 60)
    midi.addNote(0, 9, 38, 3, 0.5, 90)
    for i in range(16):
        vel = 50 + (i % 4) * 10
        midi.addNote(0, 9, 42, i * 0.25, 0.1, vel)
    midi.addNote(1, 0, 36 + offset, 0, 1.5, 120)
    midi.addNote(1, 0, 38 + offset, 1.5, 1.0, 110)
    midi.addNote(1, 0, 34 + offset, 2.5, 1.5, 115)
    _add_expression_curve(midi, 1, 0, 1, 80, 127)
    midi.addNote(2, 1, 72 + offset, 0, 0.5, 90)
    midi.addNote(2, 1, 70 + offset, 0.75, 0.5, 80)
    midi.addNote(2, 1, 67 + offset, 1.5, 1.0, 85)


def _seed_lofi(midi: MIDIFile, offset: int):
    # Drums — dusty, low velocity
    midi.addNote(0, 9, 36, 0, 0.5, 75)
    midi.addNote(0, 9, 38, 1, 0.5, 60)
    midi.addNote(0, 9, 36, 2, 0.5, 70)
    midi.addNote(0, 9, 38, 3, 0.5, 55)
    for i in range(8):
        midi.addNote(0, 9, 42, i * 0.5, 0.25, 40 + (i % 3) * 8)
    # Muted bass (ch 0)
    midi.addNote(1, 0, 48 + offset, 0, 0.75, 70)
    midi.addNote(1, 0, 46 + offset, 1.5, 0.5, 60)
    midi.addNote(1, 0, 48 + offset, 2, 1.0, 65)
    midi.addNote(1, 0, 50 + offset, 3.25, 0.5, 55)
    # Piano/Rhodes chords (ch 1)
    for p in [60, 64, 67, 71]:
        midi.addNote(2, 1, p + offset, 0, 3.0, 45 + (p % 5) * 3)
    _add_sustain_pedal(midi, 2, 1, 1)
    _add_expression_curve(midi, 2, 1, 1, 40, 80)


_GENRE_SEEDS = {
    "boom_bap":   _seed_boom_bap,
    "hip_hop":    _seed_boom_bap,
    "trap":       _seed_trap,
    "house":      _seed_house,
    "techno":     _seed_techno,
    "jazz":       _seed_jazz,
    "neo_soul":   _seed_neosoul,
    "r_and_b":    _seed_neosoul,
    "classical":  _seed_classical,
    "cinematic":  _seed_cinematic,
    "ambient":    _seed_ambient,
    "reggae":     _seed_reggae,
    "funk":       _seed_funk,
    "drum_and_bass": _seed_dnb,
    "dnb":        _seed_dnb,
    "dubstep":    _seed_dubstep,
    "drill":      _seed_drill,
    "lofi":       _seed_lofi,
    "lo-fi":      _seed_lofi,
}


def create_seed_midi(tempo: int, genre: str, key: Optional[str] = None) -> str:
    """
    Create genre-appropriate seed MIDI with expressive CC, velocity curves,
    and optional key transposition.

    The seed quality directly affects Orpheus output quality.
    """
    offset = _key_offset(key)
    cache_key = f"{genre}_{tempo}_{offset}"
    if cache_key in _seed_cache:
        logger.info(f"Seed cache hit for {cache_key}")
        return _seed_cache[cache_key]

    midi = MIDIFile(3)
    midi.addTempo(0, 0, tempo)

    # Look up genre seed builder; fall back to boom_bap for unknown genres
    genre_key = genre.lower().replace(" ", "_").replace("-", "_")
    seed_fn = _GENRE_SEEDS.get(genre_key)

    # Fuzzy fallback: try substring matching
    if seed_fn is None:
        for gk, fn in _GENRE_SEEDS.items():
            if gk in genre_key or genre_key in gk:
                seed_fn = fn
                break

    if seed_fn is None:
        seed_fn = _seed_boom_bap

    seed_fn(midi, offset)

    fd, path = tempfile.mkstemp(suffix=".mid")
    with os.fdopen(fd, 'wb') as f:
        midi.writeFile(f)

    _seed_cache[cache_key] = path
    logger.info(f"Seed created: {genre} key_offset={offset} ({cache_key})")
    return path


def parse_midi_to_notes(midi_path: str, tempo: int) -> dict:
    """
    Parse MIDI file into notes AND expressive events grouped by channel.

    Returns ``{"notes": {ch: [...]}, "cc_events": {ch: [...]},
               "pitch_bends": {ch: [...]}, "aftertouch": {ch: [...]}}``.
    """
    mid = mido.MidiFile(midi_path)
    ticks_per_beat = mid.ticks_per_beat

    notes: dict = {}
    cc_events: dict = {}
    pitch_bends: dict = {}
    aftertouch: dict = {}

    for track in mid.tracks:
        time = 0
        for msg in track:
            time += msg.time
            beat = round(time / ticks_per_beat, 3)

            if msg.type == 'note_on' and msg.velocity > 0:
                ch = msg.channel
                notes.setdefault(ch, []).append({
                    "pitch": msg.note,
                    "start_beat": beat,
                    "duration_beats": 0.5,
                    "velocity": msg.velocity,
                })

            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                ch = msg.channel
                if ch in notes:
                    for note in reversed(notes[ch]):
                        if note["pitch"] == msg.note and note.get("duration_beats") == 0.5:
                            note["duration_beats"] = round(beat - note["start_beat"], 3)
                            break

            elif msg.type == 'control_change':
                ch = msg.channel
                cc_events.setdefault(ch, []).append({
                    "cc": msg.control,
                    "beat": beat,
                    "value": msg.value,
                })

            elif msg.type == 'pitchwheel':
                ch = msg.channel
                pitch_bends.setdefault(ch, []).append({
                    "beat": beat,
                    "value": msg.pitch,
                })

            elif msg.type == 'aftertouch':
                ch = msg.channel
                aftertouch.setdefault(ch, []).append({
                    "beat": beat,
                    "value": msg.value,
                })

            elif msg.type == 'polytouch':
                ch = msg.channel
                aftertouch.setdefault(ch, []).append({
                    "beat": beat,
                    "value": msg.value,
                    "pitch": msg.note,
                })

    return {
        "notes": notes,
        "cc_events": cc_events,
        "pitch_bends": pitch_bends,
        "aftertouch": aftertouch,
    }


# Map requested instrument (from Maestro) to melodic channel index.
# Seed MIDI has: ch9=drums, ch0=first melodic (bass), ch1=second (piano/melody), etc.
MELODIC_INDEX_BY_INSTRUMENT = {
    "bass": 0,
    "electric_bass": 0,
    "synth_bass": 0,
    "piano": 1,
    "chords": 1,
    "electric_piano": 1,
    "keys": 1,
    "melody": 2,
    "lead": 2,
    "guitar": 2,
    "arp": 2,
    "pads": 2,
    "fx": 2,
}


def _channels_to_keep(channel_keys: set, instruments: List[str]) -> set:
    """Determine which MIDI channels to keep for the requested instruments."""
    if not instruments:
        return channel_keys
    requested = [i.lower().strip() for i in instruments]
    keep: set = set()
    if any(i == "drums" for i in requested):
        keep.add(9)
    melodic_channels = sorted(c for c in channel_keys if c != 9)
    for inst in requested:
        if inst == "drums":
            continue
        idx = MELODIC_INDEX_BY_INSTRUMENT.get(inst, 0)
        if idx < len(melodic_channels):
            keep.add(melodic_channels[idx])
    return keep if keep else channel_keys


def filter_channels_for_instruments(parsed: dict, instruments: List[str]) -> dict:
    """
    Keep only channels that correspond to the requested instruments.

    Accepts the full parsed dict (notes, cc_events, pitch_bends, aftertouch)
    returned by ``parse_midi_to_notes`` and filters every sub-dict.
    """
    all_chs: set = set()
    for sub in ("notes", "cc_events", "pitch_bends", "aftertouch"):
        all_chs.update(parsed.get(sub, {}).keys())

    keep = _channels_to_keep(all_chs, instruments)

    return {
        sub_key: {ch: evts for ch, evts in parsed.get(sub_key, {}).items() if ch in keep}
        for sub_key in ("notes", "cc_events", "pitch_bends", "aftertouch")
    }


def generate_tool_calls(parsed: dict, tempo: int, instruments: List[str]) -> List[dict]:
    """Convert parsed MIDI (notes + expressive events) to Stori tool calls."""
    channels_notes = parsed.get("notes", {})
    channels_cc = parsed.get("cc_events", {})
    channels_pb = parsed.get("pitch_bends", {})
    channels_at = parsed.get("aftertouch", {})

    all_chs = sorted(set(channels_notes) | set(channels_cc) | set(channels_pb) | set(channels_at))

    tool_calls: List[dict] = []

    tool_calls.append({
        "tool": "createProject",
        "params": {"name": "AI Composition", "tempo": tempo},
    })

    track_refs: dict = {}

    for ch in all_chs:
        notes = channels_notes.get(ch, [])
        if ch == 9:
            track_name = "Drums"
            program = 0
            is_drum = True
        else:
            if instruments:
                melodic_instruments = [i for i in instruments if i.lower() != "drums"]
                if melodic_instruments:
                    idx = len([c for c in track_refs if c != 9]) % len(melodic_instruments)
                    inst = melodic_instruments[idx]
                else:
                    inst = "bass"
            else:
                inst = "piano"
            track_name = inst.replace("_", " ").title()
            program = INSTRUMENT_PROGRAMS.get(inst.lower(), 33)
            is_drum = False

        track_idx = len(tool_calls)
        track_refs[ch] = track_idx

        tool_calls.append({
            "tool": "addMidiTrack",
            "params": {"name": track_name, "instrument": program if not is_drum else 0, "isDrum": is_drum},
        })

        # Region length from notes + expressive events
        max_beat = 0.0
        if notes:
            max_beat = max(n["start_beat"] + n["duration_beats"] for n in notes)
        for evts in (channels_cc.get(ch, []), channels_pb.get(ch, []), channels_at.get(ch, [])):
            for ev in evts:
                max_beat = max(max_beat, ev.get("beat", 0))
        bars = max(int((max_beat / 4) + 1), 4)
        bars = ((bars + 3) // 4) * 4

        region_idx = len(tool_calls)
        tool_calls.append({
            "tool": "addMidiRegion",
            "params": {
                "trackId": f"${track_idx}.trackId",
                "name": f"{track_name} Pattern",
                "startBar": 1,
                "lengthBars": bars,
            },
        })

        region_ref = f"${region_idx}.regionId"

        if notes:
            tool_calls.append({
                "tool": "addNotes",
                "params": {"regionId": region_ref, "notes": notes},
            })

        # CC events — group by CC number for cleaner tool calls
        cc_evts = channels_cc.get(ch, [])
        if cc_evts:
            by_cc: dict = {}
            for ev in cc_evts:
                by_cc.setdefault(ev["cc"], []).append({"beat": ev["beat"], "value": ev["value"]})
            for cc_num, events in sorted(by_cc.items()):
                tool_calls.append({
                    "tool": "addMidiCC",
                    "params": {"regionId": region_ref, "cc": cc_num, "events": events},
                })

        # Pitch bends
        pb_evts = channels_pb.get(ch, [])
        if pb_evts:
            tool_calls.append({
                "tool": "addPitchBend",
                "params": {"regionId": region_ref, "events": pb_evts},
            })

        # Aftertouch
        at_evts = channels_at.get(ch, [])
        if at_evts:
            tool_calls.append({
                "tool": "addAftertouch",
                "params": {"regionId": region_ref, "events": at_evts},
            })

    return tool_calls


@app.get("/health")
async def health():
    return {"status": "ok", "service": "orpheus-music"}


@app.get("/cache/stats")
async def cache_stats():
    """
    Get cache statistics with LRU + TTL metrics.
    
    Shows cache effectiveness and which entries are hot.
    """
    now = time()
    entries_info = []
    total_hits = 0
    expired_count = 0
    
    for key, entry in list(_result_cache.items())[:20]:  # Top 20
        age = now - entry.timestamp
        is_expired = age > CACHE_TTL_SECONDS
        if is_expired:
            expired_count += 1
            
        entries_info.append({
            "key": key[:8],  # Abbreviated
            "hits": entry.hits,
            "age_seconds": int(age),
            "expired": is_expired,
        })
        total_hits += entry.hits
    
    return {
        "result_cache_size": len(_result_cache),
        "result_cache_max": MAX_CACHE_SIZE,
        "utilization": f"{len(_result_cache) / MAX_CACHE_SIZE * 100:.1f}%",
        "total_hits": total_hits,
        "expired_entries": expired_count,
        "ttl_seconds": CACHE_TTL_SECONDS,
        "seed_cache_size": len(_seed_cache),
        "top_entries": entries_info,
        "policy_version": get_policy_version(),
    }


@app.delete("/cache/clear")
async def clear_cache():
    """Clear all caches."""
    _result_cache.clear()
    _seed_cache.clear()
    logger.info("🗑️ Caches cleared")
    return {
        "status": "ok",
        "message": "Caches cleared",
        "policy_version": get_policy_version()
    }


# =============================================================================
# Quality Evaluation & A/B Testing Endpoints
# =============================================================================

class QualityEvaluationRequest(BaseModel):
    """Request to evaluate generation quality."""
    tool_calls: List[dict]
    bars: int
    tempo: int


@app.post("/quality/evaluate")
async def evaluate_quality(request: QualityEvaluationRequest):
    """
    Evaluate the quality of generated music.
    
    Used for:
    - A/B testing different policies
    - Monitoring quality over time
    - Automated quality gates
    """
    # Extract notes from tool calls
    all_notes = []
    for tool_call in request.tool_calls:
        if tool_call.get("tool") == "addNotes":
            all_notes.extend(tool_call.get("params", {}).get("notes", []))
    
    metrics = analyze_quality(all_notes, request.bars, request.tempo)
    
    return {
        "metrics": metrics,
        "quality_score": metrics.get("quality_score", 0.0),
        "note_count": len(all_notes),
    }


class ABTestRequest(BaseModel):
    """Request to A/B test two generation configs."""
    config_a: GenerateRequest
    config_b: GenerateRequest


@app.post("/quality/ab-test")
async def ab_test(request: ABTestRequest):
    """
    A/B test two generation configurations.
    
    Generates music with both configs and compares quality metrics.
    Useful for testing policy changes before deploying.
    """
    # Generate with both configs
    result_a = await generate(request.config_a)
    result_b = await generate(request.config_b)
    
    if not result_a.success or not result_b.success:
        return {
            "error": "One or both generations failed",
            "result_a_success": result_a.success,
            "result_b_success": result_b.success,
        }
    
    # Extract notes
    notes_a = []
    for tc in result_a.tool_calls:
        if tc.get("tool") == "addNotes":
            notes_a.extend(tc.get("params", {}).get("notes", []))
    
    notes_b = []
    for tc in result_b.tool_calls:
        if tc.get("tool") == "addNotes":
            notes_b.extend(tc.get("params", {}).get("notes", []))
    
    # Compare
    comparison = compare_generations(notes_a, notes_b, request.config_a.bars, request.config_a.tempo)
    
    return {
        "comparison": comparison,
        "config_a_cache_hit": result_a.metadata.get("cache_hit", False) if result_a.metadata else False,
        "config_b_cache_hit": result_b.metadata.get("cache_hit", False) if result_b.metadata else False,
    }


@app.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest):
    """Generate music with result caching and optimized parameters."""
    # Check cache first
    cache_key = get_cache_key(request)
    cached = get_cached_result(cache_key)
    if cached:
        # Mark as cache hit in metadata
        if "metadata" in cached:
            cached["metadata"]["cache_hit"] = True
        return GenerateResponse(**cached)
    
    try:
        client = get_client()
        
        # Create seed MIDI (with caching)
        seed_path = create_seed_midi(request.tempo, request.genre, key=request.key)
        
        # Map instruments to Orpheus format
        orpheus_instruments = []
        if "drums" in [i.lower() for i in request.instruments]:
            orpheus_instruments.append("Drums")
        if any(i.lower() in ["bass", "electric_bass", "synth_bass"] for i in request.instruments):
            orpheus_instruments.append("Electric Bass(finger)")
        if any(i.lower() in ["piano", "electric_piano"] for i in request.instruments):
            orpheus_instruments.append("Electric Piano 1")
        if any(i.lower() in ["guitar", "acoustic_guitar"] for i in request.instruments):
            orpheus_instruments.append("Acoustic Guitar(steel)")
        
        if not orpheus_instruments:
            orpheus_instruments = ["Drums", "Electric Bass(finger)"]
        
        # ============================================================================
        # POLICY LAYER: Intent → Controls → Generator Params
        # ============================================================================
        
        # Legacy support: merge style_hints into musical_goals
        musical_goals = request.musical_goals or []
        if request.style_hints:
            musical_goals = list(set(musical_goals + request.style_hints))
        
        # Step 1: Convert intent → abstract control vector
        controls = intent_to_controls(
            genre=request.genre,
            tempo=request.tempo,
            musical_goals=musical_goals,
            tone_brightness=request.tone_brightness,
            tone_warmth=request.tone_warmth,
            energy_intensity=request.energy_intensity,
            energy_excitement=request.energy_excitement,
            complexity_hint=request.complexity,
            quality_preset=request.quality_preset,
        )
        
        # Step 2: Convert controls → Orpheus-specific params
        orpheus_params = controls_to_orpheus_params(controls)
        
        # Step 3: Allow explicit overrides (for testing/power users)
        temperature = request.temperature if request.temperature is not None else orpheus_params["model_temperature"]
        top_p = request.top_p if request.top_p is not None else orpheus_params["model_top_p"]
        tokens_per_bar = orpheus_params["num_gen_tokens_per_bar"]
        num_prime_tokens = orpheus_params["num_prime_tokens"]
        
        num_gen_tokens = min(request.bars * tokens_per_bar, 1024)
        
        logger.info(f"🎵 Generating {request.genre} @ {request.tempo} BPM")
        logger.info(f"   Goals: {musical_goals}")
        logger.info(f"   Controls: creativity={controls.creativity:.2f}, "
                   f"density={controls.density:.2f}, "
                   f"complexity={controls.complexity:.2f}")
        logger.info(f"   Orpheus: temp={temperature:.2f}, top_p={top_p:.2f}, "
                   f"tokens={num_gen_tokens} ({tokens_per_bar}/bar), prime={num_prime_tokens}")
        logger.info(f"   Policy: {get_policy_version()}")
        
        # Generate with Orpheus using policy-computed parameters
        result = client.predict(
            input_midi=handle_file(seed_path),
            prime_instruments=orpheus_instruments,
            num_prime_tokens=num_prime_tokens,
            num_gen_tokens=num_gen_tokens,
            model_temperature=temperature,
            model_top_p=top_p,
            add_drums="drums" in [i.lower() for i in request.instruments],
            api_name="/generate_music_and_state"
        )
        
        # Get MIDI file from first batch
        midi_result = client.predict(batch_number=0, api_name="/add_batch")
        midi_path = midi_result[2]
        
        # Parse MIDI (notes + CC + pitch bends + aftertouch)
        parsed = parse_midi_to_notes(midi_path, request.tempo)

        # Trim to requested bar range
        max_beat = request.bars * 4
        for sub_key in ("notes", "cc_events", "pitch_bends", "aftertouch"):
            sub = parsed.get(sub_key, {})
            beat_field = "start_beat" if sub_key == "notes" else "beat"
            for ch in list(sub):
                sub[ch] = [ev for ev in sub[ch] if ev.get(beat_field, 0) < max_beat]
                if not sub[ch]:
                    del sub[ch]

        parsed = filter_channels_for_instruments(parsed, request.instruments)

        tool_calls = generate_tool_calls(parsed, request.tempo, request.instruments)
        
        # Extract notes and expressive event counts for quality analysis
        all_notes = []
        cc_count = 0
        pb_count = 0
        at_count = 0
        for tool_call in tool_calls:
            t = tool_call.get("tool", "")
            if t == "addNotes":
                all_notes.extend(tool_call.get("params", {}).get("notes", []))
            elif t == "addMidiCC":
                cc_count += len(tool_call.get("params", {}).get("events", []))
            elif t == "addPitchBend":
                pb_count += len(tool_call.get("params", {}).get("events", []))
            elif t == "addAftertouch":
                at_count += len(tool_call.get("params", {}).get("events", []))

        quality_metrics = analyze_quality(all_notes, request.bars, request.tempo)
        quality_metrics["cc_events"] = cc_count
        quality_metrics["pitch_bend_events"] = pb_count
        quality_metrics["aftertouch_events"] = at_count
        
        # Build response with metadata
        response_data = {
            "success": True,
            "tool_calls": tool_calls,
            "error": None,
            "metadata": {
                "policy_version": get_policy_version(),
                "quality_metrics": quality_metrics,
                "controls_used": {
                    "creativity": controls.creativity,
                    "density": controls.density,
                    "complexity": controls.complexity,
                },
                "cache_hit": False,
            }
        }
        
        # Cache the result
        cache_result(cache_key, response_data)
        
        logger.info(f"✅ Generated {len(tool_calls)} tool calls, quality_score={quality_metrics.get('quality_score', 0):.2f}")
        
        return GenerateResponse(**response_data)
        
    except Exception as e:
        logger.error(f"❌ Generation failed: {e}")
        error_response = {
            "success": False,
            "tool_calls": [],
            "error": str(e)
        }
        return GenerateResponse(**error_response)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10002)
