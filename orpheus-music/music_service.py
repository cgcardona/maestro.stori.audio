"""
Orpheus Music Service
Generates MIDI using Orpheus Music Transformer and converts to Stori tool calls

Architecture:
- Policy Layer: Translates musical intent → generation controls
- Caching: Result caching (90% cost savings) + seed caching
- Optimization: Smart token allocation, parameter inference

This is where UX philosophy and musical taste live.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from gradio_client import Client, handle_file
from midiutil import MIDIFile
from dataclasses import dataclass, field
from collections import OrderedDict
from enum import Enum
from time import time
import uuid
import asyncio
import math
import mido
import traceback
import tempfile
import os
import copy
import json
import hashlib
import logging
import pathlib

# Import our policy layer and quality metrics
from generation_policy import (
    intent_to_controls,
    controls_to_orpheus_params,
    allocate_token_budget,
    GenerationControlVector,
    get_policy_version,
)
from quality_metrics import analyze_quality, compare_generations

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def _lifespan(application: FastAPI):
    """Start job queue workers and keepalive; drain on shutdown."""
    global _job_queue
    _job_queue = JobQueue(max_queue=_MAX_QUEUE_DEPTH, max_workers=_MAX_CONCURRENT)
    await _job_queue.start()

    loaded = _load_cache_from_disk()
    if loaded:
        logger.info(f"✅ Startup: restored {loaded} cached results from disk")
    keepalive_task = asyncio.create_task(_keepalive_loop())

    yield

    keepalive_task.cancel()
    await _job_queue.shutdown()


app = FastAPI(title="Orpheus Music Service", lifespan=_lifespan)

# Gradio client with reconnection and keepalive
gradio_client = None
_client_lock = asyncio.Lock()
_last_keepalive: float = 0.0
_last_successful_gen: float = 0.0

_DEFAULT_SPACE = "asigalov61/Orpheus-Music-Transformer"
_KEEPALIVE_INTERVAL = int(os.environ.get("ORPHEUS_KEEPALIVE_INTERVAL", "600"))
_MAX_CONCURRENT = int(os.environ.get("ORPHEUS_MAX_CONCURRENT", "2"))
_MAX_QUEUE_DEPTH = int(os.environ.get("ORPHEUS_MAX_QUEUE_DEPTH", "20"))
_JOB_TTL_SECONDS = int(os.environ.get("ORPHEUS_JOB_TTL", "300"))  # 5 min

_CACHE_DIR = pathlib.Path(os.environ.get("ORPHEUS_CACHE_DIR", "/tmp/orpheus_cache"))
_CACHE_FILE = _CACHE_DIR / "result_cache.json"

_INTENT_QUANT_STEP = float(os.environ.get("ORPHEUS_INTENT_QUANT", "0.2"))
_FUZZY_EPSILON = float(os.environ.get("ORPHEUS_FUZZY_EPSILON", "0.35"))


def _create_client() -> Client:
    """Create a fresh Gradio client connection."""
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("STORI_HF_API_KEY")
    if not hf_token:
        logger.warning("⚠️ No HF_TOKEN or STORI_HF_API_KEY set; Gradio Space may return GPU quota errors")
    space_id = os.environ.get("STORI_ORPHEUS_SPACE", _DEFAULT_SPACE)
    logger.info(f"🔌 Connecting to Orpheus Space: {space_id}")
    return Client(space_id, hf_token=hf_token)


def get_client() -> Client:
    global gradio_client
    if gradio_client is None:
        gradio_client = _create_client()
    return gradio_client


def reset_client() -> None:
    """Force-recreate the Gradio client on next access (call after connection failures)."""
    global gradio_client
    logger.warning("🔄 Resetting Gradio client — will reconnect on next request")
    gradio_client = None


async def _keepalive_loop() -> None:
    """Periodic ping to keep the HF Space GPU awake (prevents gcTimeout eviction)."""
    global _last_keepalive
    while True:
        await asyncio.sleep(_KEEPALIVE_INTERVAL)
        try:
            client = get_client()
            await asyncio.wait_for(
                asyncio.to_thread(client.view_api, print_info=False),
                timeout=30.0,
            )
            _last_keepalive = time()
            logger.debug(f"💓 Orpheus keepalive ping OK")
        except Exception as e:
            logger.warning(f"⚠️ Orpheus keepalive ping failed: {e}")
            reset_client()


_job_queue: Optional["JobQueue"] = None


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
    """Cache entry with TTL support and key data for fuzzy matching."""
    result: dict
    timestamp: float
    hits: int = 0
    key_data: Optional[dict] = None

# Result cache: stores complete generation results (LRU with TTL)
_result_cache: OrderedDict[str, CacheEntry] = OrderedDict()

# Seed MIDI cache: reuses seed files for same genre/tempo
_seed_cache = {}

def _quantize(value: float, step: float = _INTENT_QUANT_STEP) -> float:
    """Snap a continuous value to the nearest grid step for cache-friendly quantization."""
    return round(round(value / step) * step, 4)


def _canonical_instruments(instruments: List[str]) -> List[str]:
    """Normalize and sort instruments for deterministic cache keys."""
    canonical_order = ["drums", "bass", "electric_bass", "synth_bass",
                       "piano", "electric_piano", "guitar", "acoustic_guitar",
                       "organ", "strings", "synth", "pad", "lead"]
    lowered = sorted(set(i.lower().strip() for i in instruments))
    return sorted(lowered, key=lambda x: canonical_order.index(x) if x in canonical_order else 999)


def _canonical_goals(goals: Optional[List[str]]) -> List[str]:
    return sorted(set(g.lower().strip() for g in (goals or [])))


def _cache_key_data(request) -> dict:
    """Build a canonical, quantized dict of the request's cache-relevant fields."""
    return {
        "genre": request.genre.lower().strip(),
        "tempo": request.tempo,
        "key": (request.key or "").lower().strip(),
        "instruments": _canonical_instruments(request.instruments),
        "bars": request.bars,
        "musical_goals": _canonical_goals(request.musical_goals),
        "tone_brightness": _quantize(request.tone_brightness),
        "tone_warmth": _quantize(request.tone_warmth),
        "energy_intensity": _quantize(request.energy_intensity),
        "energy_excitement": _quantize(request.energy_excitement),
        "complexity": _quantize(request.complexity),
        "quality_preset": request.quality_preset,
    }


def get_cache_key(request) -> str:
    """Generate a deterministic cache key from a canonicalized + quantized request."""
    key_data = _cache_key_data(request)
    key_str = json.dumps(key_data, sort_keys=True)
    return hashlib.md5(key_str.encode()).hexdigest()


def _intent_distance(a: dict, b: dict) -> float:
    """Euclidean distance between two cache-key dicts on intent vector axes only."""
    axes = ["tone_brightness", "tone_warmth", "energy_intensity",
            "energy_excitement", "complexity"]
    return math.sqrt(sum((a.get(k, 0) - b.get(k, 0)) ** 2 for k in axes))


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
    return copy.deepcopy(entry.result)


def cache_result(cache_key: str, result: dict, key_data: Optional[dict] = None):
    """
    Cache a generation result with LRU eviction + disk persistence.

    If cache is full, evicts least recently used item.
    """
    if len(_result_cache) >= MAX_CACHE_SIZE:
        oldest_key, oldest_entry = _result_cache.popitem(last=False)
        logger.info(f"🗑️ Evicted cache entry {oldest_key} (hits: {oldest_entry.hits})")

    _result_cache[cache_key] = CacheEntry(
        result=result,
        timestamp=time(),
        hits=0,
        key_data=key_data,
    )
    logger.info(f"💾 Cached result {cache_key} (cache size: {len(_result_cache)})")
    _save_cache_to_disk()


def fuzzy_cache_lookup(request, epsilon: float = _FUZZY_EPSILON) -> Optional[dict]:
    """
    Find the nearest cached result within ε distance on intent vector axes.

    Only considers entries with matching genre, instruments, bars, and preset.
    Returns the result dict (with 'approximate': True in metadata) or None.
    """
    if not _result_cache:
        return None

    now = time()
    req_data = _cache_key_data(request)
    best_dist = float("inf")
    best_entry: Optional[CacheEntry] = None

    for key, entry in _result_cache.items():
        if now - entry.timestamp > CACHE_TTL_SECONDS:
            continue
        if not hasattr(entry, "key_data") or entry.key_data is None:
            continue
        kd = entry.key_data
        if (kd.get("genre") != req_data["genre"]
                or kd.get("instruments") != req_data["instruments"]
                or kd.get("bars") != req_data["bars"]
                or kd.get("quality_preset") != req_data["quality_preset"]):
            continue

        dist = _intent_distance(req_data, kd)
        if dist < best_dist:
            best_dist = dist
            best_entry = entry

    if best_entry is not None and best_dist <= epsilon:
        best_entry.hits += 1
        logger.info(f"🎯 Fuzzy cache hit (dist={best_dist:.3f}, ε={epsilon})")
        result = dict(best_entry.result)
        if "metadata" in result and result["metadata"]:
            result["metadata"] = dict(result["metadata"])
            result["metadata"]["cache_hit"] = True
            result["metadata"]["approximate"] = True
            result["metadata"]["fuzzy_distance"] = round(best_dist, 4)
        return result

    return None


# ── Disk persistence ─────────────────────────────────────────────────

def _save_cache_to_disk() -> None:
    """Persist the result cache to disk so it survives restarts."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        serializable = {}
        for key, entry in _result_cache.items():
            serializable[key] = {
                "result": entry.result,
                "timestamp": entry.timestamp,
                "hits": entry.hits,
                "key_data": entry.key_data if hasattr(entry, "key_data") else None,
            }
        tmp = _CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(serializable, default=str))
        tmp.rename(_CACHE_FILE)
        logger.debug(f"💾 Cache persisted to disk ({len(serializable)} entries)")
    except Exception as e:
        logger.warning(f"⚠️ Failed to persist cache: {e}")


def _load_cache_from_disk() -> int:
    """Load cached results from disk on startup. Returns number of entries loaded."""
    if not _CACHE_FILE.exists():
        return 0
    try:
        data = json.loads(_CACHE_FILE.read_text())
        now = time()
        loaded = 0
        for key, entry_data in data.items():
            if now - entry_data["timestamp"] > CACHE_TTL_SECONDS:
                continue
            entry = CacheEntry(
                result=entry_data["result"],
                timestamp=entry_data["timestamp"],
                hits=entry_data.get("hits", 0),
            )
            entry.key_data = entry_data.get("key_data")
            _result_cache[key] = entry
            loaded += 1
        logger.info(f"📂 Loaded {loaded} cached results from disk (skipped {len(data) - loaded} expired)")
        return loaded
    except Exception as e:
        logger.warning(f"⚠️ Failed to load cache from disk: {e}")
        return 0


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
    
    # Correlation
    composition_id: Optional[str] = None               # Cross-service correlation ID from Maestro

    # Section continuity: previous section's notes used to build a
    # continuation seed instead of the generic genre seed.
    previous_notes: Optional[List[dict]] = None

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


# ============================================================================
# ASYNC JOB QUEUE
# ============================================================================


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELED = "canceled"


class QueueFullError(Exception):
    pass


@dataclass
class Job:
    id: str
    request: GenerateRequest
    status: JobStatus = JobStatus.QUEUED
    result: Optional[GenerateResponse] = None
    error: Optional[str] = None
    created_at: float = 0.0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    event: asyncio.Event = field(default_factory=asyncio.Event)
    position: int = 0
    dedupe_key: Optional[str] = None
    composition_id: Optional[str] = None


class JobQueue:
    """Bounded async job queue with a fixed-size worker pool.

    Replaces the semaphore model: callers submit jobs and poll for results
    instead of blocking on a single long HTTP request.
    """

    def __init__(self, max_queue: int = 20, max_workers: int = 2):
        self._queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=max_queue)
        self._jobs: dict[str, Job] = {}
        self._dedupe: dict[str, str] = {}  # dedupe_key -> job_id
        self._max_workers = max_workers
        self._max_queue = max_queue
        self._workers: list[asyncio.Task] = []  # type: ignore[type-arg]
        self._cleanup_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

    async def start(self) -> None:
        for i in range(self._max_workers):
            self._workers.append(asyncio.create_task(self._worker(i)))
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(
            f"✅ JobQueue started: {self._max_workers} workers, "
            f"max_queue={self._max_queue}"
        )

    async def shutdown(self) -> None:
        for task in self._workers:
            task.cancel()
        if self._cleanup_task:
            self._cleanup_task.cancel()
        await asyncio.gather(
            *self._workers,
            *([] if self._cleanup_task is None else [self._cleanup_task]),
            return_exceptions=True,
        )
        self._workers.clear()
        logger.info("🛑 JobQueue shut down")

    def submit(self, request: GenerateRequest, dedupe_key: Optional[str] = None) -> Job:
        """Enqueue a generation request. Raises QueueFullError when at capacity.

        If *dedupe_key* is provided and an in-flight job with the same key
        exists (queued or running), the existing job is returned instead of
        creating a duplicate.
        """
        if dedupe_key:
            existing_id = self._dedupe.get(dedupe_key)
            if existing_id:
                existing = self._jobs.get(existing_id)
                if existing and existing.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                    logger.info(
                        f"📥 Job {existing.id[:8]} deduplicated "
                        f"(key {dedupe_key[:8]})"
                    )
                    return existing

        job = Job(
            id=str(uuid.uuid4()),
            request=request,
            created_at=time(),
            dedupe_key=dedupe_key,
            composition_id=request.composition_id,
        )
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            raise QueueFullError(
                f"Generation queue is full ({self._max_queue} pending)"
            )
        job.position = self._queue.qsize()
        self._jobs[job.id] = job
        if dedupe_key:
            self._dedupe[dedupe_key] = job.id
        _cid = f"[{job.composition_id[:8]}]" if job.composition_id else ""
        logger.info(f"📥{_cid} Job {job.id[:8]} queued (position {job.position})")
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    @property
    def running_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.status == JobStatus.RUNNING)

    def status_snapshot(self) -> dict:
        return {
            "depth": self.depth,
            "running": self.running_count,
            "max_concurrent": self._max_workers,
            "max_queue": self._max_queue,
            "total_tracked": len(self._jobs),
        }

    # ── internal ────────────────────────────────────────────────────────

    def cancel(self, job_id: str) -> Optional[Job]:
        """Cancel a job. Queued jobs are skipped by workers; running jobs are
        marked canceled and their result is dropped."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.status in (JobStatus.COMPLETE, JobStatus.FAILED, JobStatus.CANCELED):
            return job
        job.status = JobStatus.CANCELED
        job.completed_at = time()
        job.event.set()
        if job.dedupe_key:
            self._dedupe.pop(job.dedupe_key, None)
        logger.info(f"🚫 Job {job.id[:8]} canceled")
        return job

    async def _worker(self, worker_id: int) -> None:
        logger.info(f"🔧 Worker {worker_id} started")
        while True:
            job = await self._queue.get()
            if job.status == JobStatus.CANCELED:
                self._queue.task_done()
                continue
            job.status = JobStatus.RUNNING
            job.started_at = time()
            try:
                job.result = await _do_generate(job.request)
                job.status = JobStatus.COMPLETE
            except Exception as exc:
                logger.error(f"❌ Worker {worker_id} job {job.id[:8]} failed: {exc}")
                job.status = JobStatus.FAILED
                job.error = str(exc)
                job.result = GenerateResponse(
                    success=False, tool_calls=[], error=str(exc),
                )
            finally:
                job.completed_at = time()
                job.event.set()
                self._queue.task_done()
                elapsed = job.completed_at - (job.started_at or job.created_at)
                icon = "✅" if job.status == JobStatus.COMPLETE else "❌"
                _cid = f"[{job.composition_id[:8]}]" if job.composition_id else ""
                logger.info(
                    f"{icon}{_cid} Worker {worker_id} job {job.id[:8]} "
                    f"{job.status.value} in {elapsed:.1f}s"
                )

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time()
            expired = [
                jid
                for jid, j in self._jobs.items()
                if j.status in (JobStatus.COMPLETE, JobStatus.FAILED)
                and j.completed_at
                and now - j.completed_at > _JOB_TTL_SECONDS
            ]
            if expired:
                for jid in expired:
                    job = self._jobs.pop(jid, None)
                    if job and job.dedupe_key:
                        self._dedupe.pop(job.dedupe_key, None)
                logger.info(f"🧹 Cleaned up {len(expired)} expired jobs")


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
    "pads": 88,
    "lead": 80,
    "melody": 0,
    "chords": 0,
    "keys": 4,
    "arp": 80,
    "fx": 96,
    "palmas": 0,
    "cajon": 0,
}

# Map Maestro instrument role → Orpheus GM instrument name for prime_instruments.
# Must match TMIDIX Number2patch names exactly (e.g. "Pad 2 (warm)", not "pads").
_ROLE_TO_ORPHEUS_INSTRUMENT: dict[str, str] = {
    # Drums / percussion
    "drums": "Drums",
    "cajon": "Drums",
    "palmas": "Woodblock",
    "percussion": "Drums",
    # Bass
    "bass": "Electric Bass(finger)",
    "electric_bass": "Electric Bass(finger)",
    "synth_bass": "Synth Bass 1",
    # Piano / keys
    "piano": "Acoustic Grand",
    "electric_piano": "Electric Piano 1",
    "keys": "Electric Piano 1",
    "organ": "Drawbar Organ",
    # Guitar
    "guitar": "Acoustic Guitar(steel)",
    "acoustic_guitar": "Acoustic Guitar(nylon)",
    "electric_guitar": "Electric Guitar(clean)",
    # Chords / harmony
    "chords": "Acoustic Grand",
    "harmony": "Acoustic Grand",
    # Melody / lead
    "melody": "Acoustic Grand",
    "lead": "Lead 1 (square)",
    "arp": "Lead 3 (calliope)",
    # Pads / synth
    "pads": "Pad 2 (warm)",
    "pad": "Pad 2 (warm)",
    "synth": "Lead 2 (sawtooth)",
    # Strings
    "strings": "String Ensemble 1",
    "violin": "Violin",
    "cello": "Cello",
    # FX
    "fx": "FX 1 (rain)",
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


def create_seed_midi(
    tempo: int,
    genre: str,
    key: Optional[str] = None,
    instruments: Optional[List[str]] = None,
) -> str:
    """
    Create genre-appropriate seed MIDI with expressive CC, velocity curves,
    optional key transposition, and GM program changes matching the requested
    instruments.

    The seed quality directly affects Orpheus output quality.
    When ``instruments`` is provided, each melodic track in the seed gets a
    program change event corresponding to the closest GM instrument, so the
    TMIDIX tokenizer on the HF Space encodes the correct instrument identity.
    """
    offset = _key_offset(key)
    inst_key = "_".join(sorted(i.lower() for i in instruments)) if instruments else "default"
    cache_key = f"{genre}_{tempo}_{offset}_{inst_key}"
    if cache_key in _seed_cache:
        logger.info(f"Seed cache hit for {cache_key}")
        return _seed_cache[cache_key]

    midi = MIDIFile(3)
    midi.addTempo(0, 0, tempo)

    # Embed GM program changes for the requested instruments so the TMIDIX
    # tokenizer encodes correct instrument identity into the token stream.
    if instruments:
        for inst in instruments:
            role = inst.lower().strip()
            if role in ("drums", "cajon", "percussion"):
                continue
            gm_program = INSTRUMENT_PROGRAMS.get(role)
            if gm_program is None:
                continue
            idx = MELODIC_INDEX_BY_INSTRUMENT.get(role)
            if idx is None:
                continue
            track = idx + 1
            channel = idx
            if track < 3:
                midi.addProgramChange(track, channel, 0, gm_program)

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
    logger.info(f"Seed created: {genre} key_offset={offset} instruments={inst_key} ({cache_key})")
    return path


def _create_continuation_seed(
    previous_notes: List[dict],
    tempo: int,
    instruments: Optional[List[str]] = None,
) -> str:
    """Build a seed MIDI from the previous section's notes for continuity.

    Takes the last ~2 bars of the previous section and writes them as a
    new MIDI file.  This lets the Orpheus transformer "continue" from
    familiar material instead of restarting from a generic genre seed.
    """
    midi = MIDIFile(3)
    midi.addTempo(0, 0, tempo)

    if instruments:
        for inst in instruments:
            role = inst.lower().strip()
            if role in ("drums", "cajon", "percussion"):
                continue
            gm_program = INSTRUMENT_PROGRAMS.get(role)
            if gm_program is None:
                continue
            idx = MELODIC_INDEX_BY_INSTRUMENT.get(role)
            if idx is None:
                continue
            track = idx + 1
            channel = idx
            if track < 3:
                midi.addProgramChange(track, channel, 0, gm_program)

    if not previous_notes:
        fd, path = tempfile.mkstemp(suffix=".mid")
        with os.fdopen(fd, 'wb') as f:
            midi.writeFile(f)
        return path

    max_beat = max(
        (n.get("start_beat", 0) + n.get("duration_beats", 0.5)) for n in previous_notes
    )
    # Keep last 8 beats (~2 bars in 4/4) for context
    context_start = max(0, max_beat - 8.0)

    tail_notes = [
        n for n in previous_notes
        if n.get("start_beat", 0) >= context_start
    ]

    for note in tail_notes:
        pitch = note.get("pitch", 60)
        start = note.get("start_beat", 0) - context_start
        dur = note.get("duration_beats", 0.5)
        vel = note.get("velocity", 80)
        is_drum = note.get("is_drum", False)

        if is_drum:
            midi.addNote(0, 9, pitch, max(0, start), max(0.01, dur), vel)
        else:
            track = 1
            channel = 0
            midi.addNote(track, channel, pitch, max(0, start), max(0.01, dur), vel)

    fd, path = tempfile.mkstemp(suffix=".mid")
    with os.fdopen(fd, 'wb') as f:
        midi.writeFile(f)
    logger.info(
        f"🔗 Continuation seed: {len(tail_notes)} notes from beat "
        f"{context_start:.1f}-{max_beat:.1f}"
    )
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


# Map requested instrument (from Maestro) to preferred melodic channel index.
# Seed MIDI has: ch9=drums, ch0=first melodic (bass), ch1=second (piano/melody), etc.
# Instruments sharing an index compete for the same channel, so when Orpheus
# generates fewer melodic channels than expected, the fallback logic in
# _channels_to_keep ensures we always return *something* instead of empty.
MELODIC_INDEX_BY_INSTRUMENT = {
    "bass": 0,
    "electric_bass": 0,
    "synth_bass": 0,
    "piano": 1,
    "chords": 1,
    "electric_piano": 1,
    "keys": 1,
    "organ": 1,
    "harmony": 1,
    "melody": 2,
    "lead": 2,
    "guitar": 2,
    "acoustic_guitar": 2,
    "electric_guitar": 2,
    "arp": 2,
    "pads": 2,
    "pad": 2,
    "synth": 2,
    "strings": 2,
    "violin": 2,
    "cello": 2,
    "fx": 2,
    "palmas": 2,
    "cajon": None,
    "percussion": None,
}


def _channels_to_keep(channel_keys: set, instruments: List[str]) -> set:
    """Determine which MIDI channels to keep for the requested instruments.

    When the preferred channel index doesn't exist in the generated MIDI,
    falls back to the nearest available melodic channel instead of returning
    an empty set (which caused silent "No notes found" failures).
    """
    if not instruments:
        return channel_keys
    requested = [i.lower().strip() for i in instruments]
    keep: set = set()
    melodic_channels = sorted(c for c in channel_keys if c != 9)

    for inst in requested:
        if inst in ("drums", "cajon", "percussion"):
            if 9 in channel_keys:
                keep.add(9)
            continue

        preferred_idx = MELODIC_INDEX_BY_INSTRUMENT.get(inst)
        if preferred_idx is not None and preferred_idx < len(melodic_channels):
            keep.add(melodic_channels[preferred_idx])
        elif melodic_channels:
            fallback_idx = min(preferred_idx or 0, len(melodic_channels) - 1)
            keep.add(melodic_channels[fallback_idx])
            logger.warning(
                f"⚠️ Instrument '{inst}' wanted melodic channel index "
                f"{preferred_idx}, but only {len(melodic_channels)} melodic "
                f"channel(s) exist — falling back to index {fallback_idx}"
            )

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


@app.get("/diagnostics")
async def diagnostics():
    """Structured diagnostics for the Orpheus service pipeline."""
    now = time()
    space_id = os.environ.get("STORI_ORPHEUS_SPACE", _DEFAULT_SPACE)

    gradio_status = "disconnected"
    hf_space_status = "unknown"
    if gradio_client is not None:
        gradio_status = "connected"
        try:
            await asyncio.wait_for(
                asyncio.to_thread(gradio_client.view_api, print_info=False),
                timeout=10.0,
            )
            hf_space_status = "awake"
        except asyncio.TimeoutError:
            hf_space_status = "unresponsive"
        except Exception as e:
            hf_space_status = f"error: {str(e)[:80]}"

    cache_size = len(_result_cache)
    total_hits = sum(e.hits for e in _result_cache.values())

    return {
        "service": "orpheus-music",
        "space_id": space_id,
        "gradio_client": gradio_status,
        "hf_space": hf_space_status,
        "active_generations": _job_queue.running_count if _job_queue else 0,
        "queue_depth": _job_queue.depth if _job_queue else 0,
        "last_successful_gen_ago_s": (
            round(now - _last_successful_gen, 1) if _last_successful_gen > 0 else None
        ),
        "last_keepalive_ago_s": (
            round(now - _last_keepalive, 1) if _last_keepalive > 0 else None
        ),
        "keepalive_interval_s": _KEEPALIVE_INTERVAL,
        "predict_timeout_s": float(os.environ.get("ORPHEUS_PREDICT_TIMEOUT", "180")),
        "max_concurrent": _MAX_CONCURRENT,
        "intent_quant_step": _INTENT_QUANT_STEP,
        "fuzzy_epsilon": _FUZZY_EPSILON,
        "cache": {
            "size": cache_size,
            "max_size": MAX_CACHE_SIZE,
            "total_hits": total_hits,
            "ttl_s": CACHE_TTL_SECONDS,
            "disk_persisted": _CACHE_FILE.exists(),
        },
    }


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


@app.post("/cache/warm")
async def warm_cache():
    """
    Pre-generate common genre × tempo combos in the background.

    Returns immediately with a job summary; generations happen async.
    """
    warm_combos = [
        ("boom_bap", 90), ("trap", 140), ("house", 124), ("techno", 130),
        ("jazz", 110), ("neo_soul", 75), ("classical", 100), ("cinematic", 95),
        ("ambient", 70), ("lofi", 82), ("funk", 105), ("reggae", 80),
        ("drum_and_bass", 174), ("drill", 145), ("dubstep", 140),
    ]

    already_cached = 0
    to_generate = []
    for genre, tempo in warm_combos:
        req = GenerateRequest(
            genre=genre, tempo=tempo, instruments=["drums", "bass"],
            bars=4, quality_preset="fast",
        )
        if get_cached_result(get_cache_key(req)) is not None:
            already_cached += 1
        else:
            to_generate.append(req)

    async def _warm():
        ok, fail = 0, 0
        for req in to_generate:
            try:
                resp = await generate(req)
                if resp.success:
                    ok += 1
                else:
                    fail += 1
            except Exception:
                fail += 1
        logger.info(f"🔥 Cache warm complete: {ok} generated, {fail} failed, {already_cached} already cached")

    asyncio.create_task(_warm())
    return {
        "status": "warming",
        "already_cached": already_cached,
        "queued": len(to_generate),
    }


@app.delete("/cache/clear")
async def clear_cache():
    """Clear all caches."""
    _result_cache.clear()
    _seed_cache.clear()
    if _CACHE_FILE.exists():
        _CACHE_FILE.unlink()
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


async def _do_generate(request: GenerateRequest) -> GenerateResponse:
    """Core GPU generation logic — called by JobQueue workers."""
    global _last_successful_gen

    cache_key = get_cache_key(request)

    try:
        client = get_client()

        if request.previous_notes:
            seed_path = _create_continuation_seed(
                request.previous_notes, request.tempo,
                instruments=request.instruments,
            )
            logger.info(
                f"🔗 Using continuation seed from {len(request.previous_notes)} "
                f"previous notes"
            )
        else:
            seed_path = create_seed_midi(
                request.tempo, request.genre,
                key=request.key, instruments=request.instruments,
            )

        orpheus_instruments = []
        for inst in request.instruments:
            mapped = _ROLE_TO_ORPHEUS_INSTRUMENT.get(inst.lower().strip())
            if mapped and mapped not in orpheus_instruments:
                orpheus_instruments.append(mapped)

        if not orpheus_instruments:
            orpheus_instruments = ["Drums", "Electric Bass(finger)"]
            logger.warning(
                f"⚠️ No instrument mapping found for {request.instruments}, "
                f"falling back to {orpheus_instruments}"
            )

        musical_goals = request.musical_goals or []
        if request.style_hints:
            musical_goals = list(set(musical_goals + request.style_hints))

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

        orpheus_params = controls_to_orpheus_params(controls)

        temperature = request.temperature if request.temperature is not None else orpheus_params["model_temperature"]
        top_p = request.top_p if request.top_p is not None else orpheus_params["model_top_p"]
        tokens_per_bar = orpheus_params["num_gen_tokens_per_bar"]

        num_prime_tokens, num_gen_tokens = allocate_token_budget(
            bars=request.bars,
            tokens_per_bar=tokens_per_bar,
            prime_from_policy=orpheus_params["num_prime_tokens"],
        )

        logger.info(f"🎵 Generating {request.genre} @ {request.tempo} BPM")
        logger.info(f"   Goals: {musical_goals}")
        logger.info(f"   Controls: creativity={controls.creativity:.2f}, "
                    f"density={controls.density:.2f}, "
                    f"complexity={controls.complexity:.2f}")
        _ctx_util = (num_prime_tokens + num_gen_tokens) / 8192 * 100
        logger.info(f"   Orpheus: temp={temperature:.2f}, top_p={top_p:.2f}, "
                    f"gen={num_gen_tokens} ({tokens_per_bar}/bar), "
                    f"prime={num_prime_tokens}, ctx={_ctx_util:.0f}%")
        logger.info(f"   Policy: {get_policy_version()}")

        _predict_timeout = float(os.environ.get("ORPHEUS_PREDICT_TIMEOUT", "180"))
        # Fresh session hash per call — the Space's generate_music_and_state
        # uses gr.State to accumulate tokens across calls in the same session.
        # Without this, composition tokens grow unboundedly (45 → 190 → … → 1100+)
        # until the model produces garbage and save_midi crashes with TypeError.
        client.session_hash = str(uuid.uuid4())
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    client.predict,
                    input_midi=handle_file(seed_path),
                    prime_instruments=orpheus_instruments,
                    num_prime_tokens=num_prime_tokens,
                    num_gen_tokens=num_gen_tokens,
                    model_temperature=temperature,
                    model_top_p=top_p,
                    add_drums="drums" in [i.lower() for i in request.instruments],
                    api_name="/generate_music_and_state",
                ),
                timeout=_predict_timeout,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            kind = "cancelled" if isinstance(exc, asyncio.CancelledError) else "timed out"
            logger.error(
                f"❌ Gradio /generate_music_and_state {kind} after {_predict_timeout}s"
            )
            return GenerateResponse(
                success=False,
                tool_calls=[],
                error=f"Orpheus generation {kind} after {_predict_timeout}s",
            )

        try:
            midi_result = await asyncio.wait_for(
                asyncio.to_thread(
                    client.predict, batch_number=0, api_name="/add_batch"
                ),
                timeout=_predict_timeout,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            kind = "cancelled" if isinstance(exc, asyncio.CancelledError) else "timed out"
            logger.error(
                f"❌ Gradio /add_batch {kind} after {_predict_timeout}s"
            )
            return GenerateResponse(
                success=False,
                tool_calls=[],
                error=f"Orpheus batch retrieval {kind} after {_predict_timeout}s",
            )
        midi_path = midi_result[2]

        parsed = await asyncio.to_thread(parse_midi_to_notes, midi_path, request.tempo)

        max_beat = request.bars * 4
        for sub_key in ("notes", "cc_events", "pitch_bends", "aftertouch"):
            sub = parsed.get(sub_key, {})
            beat_field = "start_beat" if sub_key == "notes" else "beat"
            for ch in list(sub):
                sub[ch] = [ev for ev in sub[ch] if ev.get(beat_field, 0) < max_beat]
                if not sub[ch]:
                    del sub[ch]

        pre_filter_note_count = sum(
            len(notes) for notes in parsed.get("notes", {}).values()
        )
        pre_filter_channels = set(parsed.get("notes", {}).keys())

        parsed = filter_channels_for_instruments(parsed, request.instruments)

        post_filter_note_count = sum(
            len(notes) for notes in parsed.get("notes", {}).values()
        )

        if pre_filter_note_count > 0 and post_filter_note_count == 0:
            logger.error(
                f"❌ Channel filtering removed all {pre_filter_note_count} notes! "
                f"Requested instruments={request.instruments}, "
                f"available channels={sorted(pre_filter_channels)}"
            )
            return GenerateResponse(
                success=False,
                tool_calls=[],
                error=(
                    f"Orpheus generated {pre_filter_note_count} notes on "
                    f"channels {sorted(pre_filter_channels)}, but channel "
                    f"filtering for instruments={request.instruments} "
                    f"removed all of them. The instrument may not be "
                    f"represented in the generated output."
                ),
            )

        tool_calls = generate_tool_calls(parsed, request.tempo, request.instruments)

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

        if tool_calls:
            cache_result(cache_key, response_data, key_data=_cache_key_data(request))
        else:
            logger.warning(f"⚠️ Skipping cache for {cache_key} — 0 notes generated")
        _last_successful_gen = time()

        logger.info(f"✅ Generated {len(tool_calls)} tool calls, quality_score={quality_metrics.get('quality_score', 0):.2f}")

        return GenerateResponse(**response_data)

    except (Exception, asyncio.CancelledError) as e:
        err_msg = str(e) or type(e).__name__
        logger.error(f"❌ Generation failed: {err_msg}")
        logger.debug(f"❌ Traceback:\n{traceback.format_exc()}")
        _err_str = err_msg.lower()
        if any(kw in _err_str for kw in ("connection", "refused", "reset", "eof", "broken pipe")):
            reset_client()
        return GenerateResponse(
            success=False,
            tool_calls=[],
            error=err_msg,
        )


def _job_response(job: Job) -> dict:
    """Serialize a Job to the wire format used by /generate and /jobs endpoints."""
    resp: dict = {
        "jobId": job.id,
        "status": job.status.value,
    }
    if job.status == JobStatus.QUEUED:
        resp["position"] = job.position
    if job.status in (JobStatus.COMPLETE, JobStatus.FAILED) and job.result:
        resp["result"] = job.result.model_dump()
    if job.error:
        resp["error"] = job.error
    if job.created_at:
        resp["elapsed"] = round(time() - job.created_at, 1)
    return resp


@app.post("/generate")
async def generate(request: GenerateRequest):
    """Submit a generation job.  Cache hits return immediately; misses enqueue."""
    assert _job_queue is not None, "JobQueue not initialized"

    cache_key = get_cache_key(request)
    cached = get_cached_result(cache_key)
    if cached:
        if "metadata" in cached:
            cached["metadata"]["cache_hit"] = True
        return {
            "jobId": str(uuid.uuid4()),
            "status": "complete",
            "result": cached,
        }

    fuzzy = fuzzy_cache_lookup(request)
    if fuzzy:
        return {
            "jobId": str(uuid.uuid4()),
            "status": "complete",
            "result": fuzzy,
        }

    try:
        job = _job_queue.submit(request, dedupe_key=cache_key)
    except QueueFullError:
        return JSONResponse(
            status_code=503,
            content={"error": "Generation queue is full — try again shortly"},
            headers={"Retry-After": "30"},
        )
    return _job_response(job)


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    """Return current status of a submitted job."""
    assert _job_queue is not None
    job = _job_queue.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    return _job_response(job)


@app.get("/jobs/{job_id}/wait")
async def wait_for_job(
    job_id: str,
    timeout: float = Query(default=30, ge=1, le=120),
):
    """Long-poll until the job completes or *timeout* seconds elapse."""
    assert _job_queue is not None
    job = _job_queue.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    if not job.event.is_set():
        try:
            await asyncio.wait_for(job.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    return _job_response(job)


@app.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancel a queued or running job."""
    assert _job_queue is not None
    job = _job_queue.cancel(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    return _job_response(job)


@app.get("/queue/status")
async def queue_status():
    """Diagnostics: current queue depth, running workers, limits."""
    if _job_queue is None:
        return {"error": "JobQueue not initialized"}
    return _job_queue.status_snapshot()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10002)
