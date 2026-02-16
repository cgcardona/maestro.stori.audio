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
        # Composer sends token in Authorization header; Orpheus uses env so the Gradio client has it.
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


def create_seed_midi(tempo: int, genre: str) -> str:
    """
    Create genre-appropriate seed MIDI with drums, bass, and melody patterns.
    
    The seed quality directly affects Orpheus output quality - good seeds = good generations.
    """
    # Check cache first
    cache_key = f"{genre}_{tempo}"
    if cache_key in _seed_cache:
        logger.info(f"✅ Seed cache hit for {cache_key}")
        return _seed_cache[cache_key]
    
    logger.info(f"❌ Seed cache miss for {cache_key}, creating new seed")
    
    midi = MIDIFile(3)  # 3 tracks for drums, bass, melody
    midi.addTempo(0, 0, tempo)
    
    # DRUMS (Channel 9) - These are working well, keep them
    if genre in ["boom_bap", "hip_hop"]:
        # Classic boom bap: kick on 1, snare on 2
        midi.addNote(0, 9, 36, 0, 0.5, 100)      # Kick beat 1
        midi.addNote(0, 9, 38, 1, 0.5, 90)       # Snare beat 2
        midi.addNote(0, 9, 36, 2, 0.5, 100)      # Kick beat 3
        midi.addNote(0, 9, 38, 3, 0.5, 90)       # Snare beat 4
        # Add hi-hats
        for i in [0, 0.5, 1.5, 2, 2.5, 3, 3.5]:
            midi.addNote(0, 9, 42, i, 0.25, 80)
    elif genre in ["trap"]:
        # Trap: 808s and hi-hats
        midi.addNote(0, 9, 36, 0, 0.25, 110)
        midi.addNote(0, 9, 36, 0.75, 0.25, 100)
        midi.addNote(0, 9, 38, 1, 0.5, 90)
        midi.addNote(0, 9, 42, 0, 0.125, 70)     # Closed hi-hat
        midi.addNote(0, 9, 42, 0.25, 0.125, 70)
        midi.addNote(0, 9, 42, 0.5, 0.125, 70)
        midi.addNote(0, 9, 42, 0.75, 0.125, 70)
    elif genre in ["house", "techno"]:
        # Four on the floor
        for beat in range(4):
            midi.addNote(0, 9, 36, beat, 0.5, 100)
        midi.addNote(0, 9, 42, 0.5, 0.25, 80)
        midi.addNote(0, 9, 42, 1.5, 0.25, 80)
    else:
        # Default pattern
        midi.addNote(0, 9, 36, 0, 0.5, 100)
        midi.addNote(0, 9, 38, 1, 0.5, 90)
    
    # BASS (Channel 0) - Add simple, musical bass patterns
    if genre in ["boom_bap", "hip_hop"]:
        # Boom bap bass: Root note on 1 and 3, with passing tones
        midi.addNote(1, 0, 48, 0, 1.0, 95)       # C3 (root)
        midi.addNote(1, 0, 48, 2, 0.75, 90)      # C3
        midi.addNote(1, 0, 50, 2.75, 0.25, 85)   # D3 (passing)
    elif genre in ["trap"]:
        # Trap bass: 808-style sustained notes with slides
        midi.addNote(1, 0, 48, 0, 1.5, 110)      # Long C3
        midi.addNote(1, 0, 50, 1.5, 0.5, 100)    # D3
        midi.addNote(1, 0, 46, 2, 1.5, 105)      # Bb2
    elif genre in ["house", "techno"]:
        # Four-on-floor bass
        for beat in range(4):
            midi.addNote(1, 0, 48, beat, 0.75, 100)
    else:
        # Default bass: Simple root pattern
        midi.addNote(1, 0, 48, 0, 0.75, 95)
        midi.addNote(1, 0, 48, 2, 0.75, 90)
    
    # MELODY (Channel 1) - Add simple, musical melody seeds
    if genre in ["boom_bap", "hip_hop"]:
        # Hip hop melody: Syncopated, sparse
        midi.addNote(2, 1, 60, 0.5, 0.5, 85)     # C4
        midi.addNote(2, 1, 63, 1, 0.75, 80)      # Eb4
        midi.addNote(2, 1, 65, 2.5, 0.5, 75)     # F4
    elif genre in ["trap"]:
        # Trap melody: Higher register, staccato
        midi.addNote(2, 1, 72, 0, 0.25, 90)      # C5
        midi.addNote(2, 1, 74, 0.5, 0.25, 85)    # D5
        midi.addNote(2, 1, 72, 1.5, 0.5, 88)     # C5
    elif genre in ["house", "techno"]:
        # House melody: Sustained chords/notes
        midi.addNote(2, 1, 60, 0, 2.0, 80)       # C4
        midi.addNote(2, 1, 64, 2, 2.0, 75)       # E4
    else:
        # Default melody: Simple motif
        midi.addNote(2, 1, 60, 0, 0.5, 85)
        midi.addNote(2, 1, 62, 1, 0.5, 80)
    
    # Write to temp file
    fd, path = tempfile.mkstemp(suffix=".mid")
    with os.fdopen(fd, 'wb') as f:
        midi.writeFile(f)
    
    # Cache the path
    _seed_cache[cache_key] = path
    logger.info(f"💾 Cached seed for {cache_key} (cache size: {len(_seed_cache)})")
    
    return path


def parse_midi_to_notes(midi_path: str, tempo: int) -> dict:
    """Parse MIDI file into notes grouped by channel"""
    mid = mido.MidiFile(midi_path)
    ticks_per_beat = mid.ticks_per_beat
    
    # Group notes by channel
    channels = {}
    current_time = {}  # Track time per channel
    
    for track in mid.tracks:
        time = 0
        for msg in track:
            time += msg.time
            if msg.type == 'note_on' and msg.velocity > 0:
                ch = msg.channel
                if ch not in channels:
                    channels[ch] = []
                    current_time[ch] = 0
                
                # Convert ticks to beats
                beat = time / ticks_per_beat
                
                channels[ch].append({
                    "pitch": msg.note,
                    "start_beat": round(beat, 3),
                    "duration_beats": 0.5,  # Will be updated by note_off
                    "velocity": msg.velocity
                })
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                ch = msg.channel
                if ch in channels:
                    # Find the matching note and update duration
                    beat = time / ticks_per_beat
                    for note in reversed(channels[ch]):
                        if note["pitch"] == msg.note and note.get("duration_beats") == 0.5:
                            note["duration_beats"] = round(beat - note["start_beat"], 3)
                            break
    
    return channels


# Map requested instrument (from Composer) to melodic channel index.
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


def filter_channels_for_instruments(channels: dict, instruments: List[str]) -> dict:
    """
    Keep only channels that correspond to the requested instruments.

    Composer calls Orpheus once per instrument (e.g. instruments=["drums"], then ["bass"]).
    Orpheus always returns multi-channel MIDI (drums + melodic). If we return all channels,
    Composer merges them and assigns the same combined notes to every track, so every
    track gets the same pattern. Filter to the single channel (or channels) for this
    request so each Composer track gets only its instrument's notes.
    """
    if not instruments:
        return channels
    requested = [i.lower().strip() for i in instruments]
    keep = set()
    # Drums = channel 9 (GM)
    if any(i in requested for i in ("drums",)):
        keep.add(9)
    # Melodic channels (non-9), in deterministic order
    melodic_channels = sorted(c for c in channels if c != 9)
    for inst in requested:
        if inst in ("drums",):
            continue
        idx = MELODIC_INDEX_BY_INSTRUMENT.get(inst, 0)
        if idx < len(melodic_channels):
            keep.add(melodic_channels[idx])
    if not keep:
        return channels
    return {ch: channels[ch] for ch in channels if ch in keep}


def generate_tool_calls(channels: dict, tempo: int, instruments: List[str]) -> List[dict]:
    """Convert parsed MIDI channels to Stori tool calls"""
    tool_calls = []
    
    # 1. Create project
    tool_calls.append({
        "tool": "createProject",
        "params": {
            "name": "AI Composition",
            "tempo": tempo
        }
    })
    
    track_refs = {}  # Map channel to track reference index
    
    # 2. Add tracks for each channel
    for ch, notes in sorted(channels.items()):
        if ch == 9:
            # Drum channel
            track_name = "Drums"
            program = 0
            is_drum = True
        else:
            # Melodic channel - assign instrument
            if instruments:
                # Pick from requested instruments (excluding drums)
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
            "params": {
                "name": track_name,
                "instrument": program if not is_drum else 0,
                "isDrum": is_drum
            }
        })
        
        # 3. Add region for this track
        # Calculate region length (round up to nearest 4 bars)
        if notes:
            max_beat = max(n["start_beat"] + n["duration_beats"] for n in notes)
            bars = int((max_beat / 4) + 1)
            bars = ((bars + 3) // 4) * 4  # Round to 4
        else:
            bars = 4
        
        region_idx = len(tool_calls)
        tool_calls.append({
            "tool": "addMidiRegion",
            "params": {
                "trackId": f"${track_idx}.trackId",
                "name": f"{track_name} Pattern",
                "startBar": 1,
                "lengthBars": bars
            }
        })
        
        # 4. Add notes to region
        if notes:
            tool_calls.append({
                "tool": "addNotes",
                "params": {
                    "regionId": f"${region_idx}.regionId",
                    "notes": notes
                }
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
        seed_path = create_seed_midi(request.tempo, request.genre)
        
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
        
        num_gen_tokens = min(request.bars * tokens_per_bar, 256)
        
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
        
        # Parse MIDI
        channels = parse_midi_to_notes(midi_path, request.tempo)
        
        # Filter notes to only include requested bars (4 beats per bar)
        max_beat = request.bars * 4
        filtered_channels = {}
        for ch, notes in channels.items():
            filtered_notes = [n for n in notes if n["start_beat"] < max_beat]
            if filtered_notes:
                filtered_channels[ch] = filtered_notes

        # Return only the channel(s) for the requested instrument(s). Composer calls
        # once per track (e.g. instruments=["drums"], then ["bass"]). Without this,
        # we would return all channels and Composer would merge them, putting the
        # same combined notes on every track.
        filtered_channels = filter_channels_for_instruments(filtered_channels, request.instruments)

        # Generate tool calls
        tool_calls = generate_tool_calls(filtered_channels, request.tempo, request.instruments)
        
        # Extract notes for quality analysis
        all_notes = []
        for tool_call in tool_calls:
            if tool_call.get("tool") == "addNotes":
                all_notes.extend(tool_call.get("params", {}).get("notes", []))
        
        # Analyze quality
        quality_metrics = analyze_quality(all_notes, request.bars, request.tempo)
        
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
