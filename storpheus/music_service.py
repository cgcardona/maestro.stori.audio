"""
Orpheus Music Service
Generates MIDI using Orpheus Music Transformer with expressiveness layer

Architecture:
- Policy Layer: Translates musical intent → generation controls
- Caching: Result caching (90% cost savings) + seed caching
- Optimization: Smart token allocation, parameter inference

This is where UX philosophy and musical taste live.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from gradio_client import Client, handle_file
from dataclasses import dataclass, field
from collections import OrderedDict
from enum import Enum
from time import time
import uuid
import asyncio
import math
import mido
import random
import traceback
import os
import copy
import json
import hashlib
import logging
import pathlib
import shutil

# Import our policy layer and quality metrics
from generation_policy import (
    allocate_token_budget,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    get_policy_version,
    build_controls,
    build_fulfillment_report,
    quality_preset_to_batch_count,
    apply_controls_to_params,
)
from quality_metrics import analyze_quality, compare_generations, rejection_score
from seed_selector import select_seed, select_seed_with_key, SeedSelection
from midi_transforms import transpose_midi
from candidate_scorer import score_candidate, select_best_candidate, CandidateScore
from post_processing import build_post_processor
from storpheus_types import (
    BestCandidate,
    CacheKeyData,
    StorpheusAftertouch,
    StorpheusCCEvent,
    StorpheusNoteDict,
    StorpheusPitchBend,
    ParsedMidiResult,
    QualityEvalToolCall,
    ScoringParams,
    WireNoteDict,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
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

# Gradio client pool — one client per worker to avoid session corruption
_last_keepalive: float = 0.0
_last_successful_gen: float = 0.0

_DEFAULT_SPACE = "cgcardona/Orpheus-Music-Transformer"
_KEEPALIVE_INTERVAL = int(os.environ.get("STORPHEUS_KEEPALIVE_INTERVAL", "600"))
_MAX_CONCURRENT = int(os.environ.get("STORPHEUS_MAX_CONCURRENT", "1"))
_MAX_QUEUE_DEPTH = int(os.environ.get("STORPHEUS_MAX_QUEUE_DEPTH", "20"))
_JOB_TTL_SECONDS = int(os.environ.get("STORPHEUS_JOB_TTL", "300"))  # 5 min
_COOLDOWN_SECONDS = float(os.environ.get("STORPHEUS_COOLDOWN_SECONDS", "3"))

_CACHE_DIR = pathlib.Path(os.environ.get("STORPHEUS_CACHE_DIR", "/tmp/storpheus_cache"))
_CACHE_FILE = _CACHE_DIR / "result_cache.json"

# ── Storpheus config flags ─────────────────────────────────────────────
STORPHEUS_PRESERVE_ALL_CHANNELS = os.environ.get("STORPHEUS_PRESERVE_ALL_CHANNELS", "true").lower() in ("1", "true", "yes")
ENABLE_BEAT_RESCALING = os.environ.get("ENABLE_BEAT_RESCALING", "false").lower() in ("1", "true", "yes")
MAX_SESSION_TOKENS = int(os.environ.get("STORPHEUS_MAX_SESSION_TOKENS", "4096"))

_INTENT_QUANT_STEP = float(os.environ.get("STORPHEUS_INTENT_QUANT", "0.2"))
_FUZZY_EPSILON = float(os.environ.get("STORPHEUS_FUZZY_EPSILON", "0.35"))


def _create_client() -> Client:
    """Create a fresh Gradio client connection."""
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("STORI_HF_API_KEY")
    if not hf_token:
        logger.warning("⚠️ No HF_TOKEN or STORI_HF_API_KEY set; Gradio Space may return GPU quota errors")
    space_id = os.environ.get("STORI_STORPHEUS_SPACE", _DEFAULT_SPACE)
    logger.info(f"🔌 Connecting to Orpheus Space: {space_id}")
    return Client(space_id, hf_token=hf_token)


class _ClientPool:
    """Pool of Gradio clients — one per worker.

    IMPORTANT: each Gradio session accumulates ``final_composition``
    state via ``/add_batch``.  To get independent output per generation,
    callers must either use ``fresh()`` (creates a new disposable client)
    or call ``reset(worker_id)`` before generating so the next ``get()``
    returns a clean session.
    """

    def __init__(self) -> None:
        self._clients: dict[int, Client] = {}
        self._loops_clients: dict[int, Client] = {}

    def get(self, worker_id: int) -> Client:
        if worker_id not in self._clients:
            self._clients[worker_id] = _create_client()
        return self._clients[worker_id]

    def fresh(self, worker_id: int) -> Client:
        """Return a brand-new client for *worker_id*, discarding any
        previous session so ``final_composition`` state is empty."""
        self._clients.pop(worker_id, None)
        client = _create_client()
        self._clients[worker_id] = client
        return client

    def get_loops(self, worker_id: int) -> Client | None:
        loops_space = os.environ.get("STORI_STORPHEUS_LOOPS_SPACE", "")
        if not loops_space:
            return None
        if worker_id not in self._loops_clients:
            hf_token = os.environ.get("HF_TOKEN") or os.environ.get("STORI_HF_API_KEY")
            logger.info(f"🔌 Connecting to Orpheus Loops Space: {loops_space}")
            self._loops_clients[worker_id] = Client(loops_space, hf_token=hf_token)
        return self._loops_clients[worker_id]

    def fresh_loops(self, worker_id: int) -> Client | None:
        """Return a brand-new Loops client, discarding previous session."""
        loops_space = os.environ.get("STORI_STORPHEUS_LOOPS_SPACE", "")
        if not loops_space:
            return None
        self._loops_clients.pop(worker_id, None)
        hf_token = os.environ.get("HF_TOKEN") or os.environ.get("STORI_HF_API_KEY")
        logger.info(f"🔌 Connecting to Orpheus Loops Space: {loops_space}")
        client = Client(loops_space, hf_token=hf_token)
        self._loops_clients[worker_id] = client
        return client

    def reset(self, worker_id: int) -> None:
        self._clients.pop(worker_id, None)
        self._loops_clients.pop(worker_id, None)

    def reset_all(self) -> None:
        self._clients.clear()
        self._loops_clients.clear()

    def any_client(self) -> Client:
        """Return any live client for keepalive pings."""
        if self._clients:
            return next(iter(self._clients.values()))
        return _create_client()


_client_pool = _ClientPool()


def reset_client() -> None:
    """Reset all pooled clients (called on catastrophic connection failure)."""
    logger.warning("🔄 Resetting all Gradio clients — will reconnect on next request")
    _client_pool.reset_all()


async def _keepalive_loop() -> None:
    """Periodic ping to keep the HF Space GPU awake (prevents gcTimeout eviction)."""
    global _last_keepalive
    while True:
        await asyncio.sleep(_KEEPALIVE_INTERVAL)
        try:
            client = _client_pool.any_client()
            await asyncio.wait_for(
                asyncio.to_thread(client.view_api, print_info=False),
                timeout=30.0,
            )
            _last_keepalive = time()
            logger.debug(f"💓 Orpheus keepalive ping OK")
        except Exception as e:
            logger.warning(f"⚠️ Orpheus keepalive ping failed: {e}")
            _client_pool.reset_all()


_job_queue: "JobQueue" | None = None


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
    result: dict[str, object]
    timestamp: float
    hits: int = 0
    key_data: CacheKeyData | None = None

# Result cache: stores complete generation results (LRU with TTL)
_result_cache: OrderedDict[str, CacheEntry] = OrderedDict()


def _quantize(value: float, step: float = _INTENT_QUANT_STEP) -> float:
    """Snap a continuous value to the nearest grid step for cache-friendly quantization."""
    return round(round(value / step) * step, 4)


def _canonical_instruments(instruments: list[str]) -> list[str]:
    """Normalize and sort instruments for deterministic cache keys."""
    canonical_order = ["drums", "bass", "electric_bass", "synth_bass",
                       "piano", "electric_piano", "guitar", "acoustic_guitar",
                       "organ", "strings", "synth", "pad", "lead"]
    lowered = sorted(set(i.lower().strip() for i in instruments))
    return sorted(lowered, key=lambda x: canonical_order.index(x) if x in canonical_order else 999)



def _cache_key_data(request: GenerateRequest) -> CacheKeyData:
    """Build a canonical, quantized dict of the request's cache-relevant fields."""
    ev = request.emotion_vector
    return {
        "genre": request.genre.lower().strip(),
        "tempo": request.tempo,
        "key": (request.key or "").lower().strip(),
        "instruments": _canonical_instruments(request.instruments),
        "bars": request.bars,
        "intent_goals": sorted(g.name.lower() for g in (request.intent_goals or [])),
        "energy": _quantize(ev.energy if ev else 0.5),
        "valence": _quantize(ev.valence if ev else 0.0),
        "tension": _quantize(ev.tension if ev else 0.3),
        "intimacy": _quantize(ev.intimacy if ev else 0.5),
        "motion": _quantize(ev.motion if ev else 0.5),
        "quality_preset": request.quality_preset,
    }


def get_cache_key(request: GenerateRequest) -> str:
    """Generate a deterministic cache key from a canonicalized + quantized request."""
    key_data = _cache_key_data(request)
    key_str = json.dumps(key_data, sort_keys=True)
    return hashlib.md5(key_str.encode()).hexdigest()


def _intent_distance(a: CacheKeyData, b: CacheKeyData) -> float:
    """Euclidean distance between two cache-key dicts on emotion vector axes."""
    return math.sqrt(
        (a["energy"] - b["energy"]) ** 2
        + (a["valence"] - b["valence"]) ** 2
        + (a["tension"] - b["tension"]) ** 2
        + (a["intimacy"] - b["intimacy"]) ** 2
        + (a["motion"] - b["motion"]) ** 2
    )


def get_cached_result(cache_key: str) -> dict[str, object] | None:
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


def cache_result(cache_key: str, result: dict[str, object], key_data: CacheKeyData | None = None) -> None:
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


def fuzzy_cache_lookup(request: GenerateRequest, epsilon: float = _FUZZY_EPSILON) -> dict[str, object] | None:
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
    best_entry: CacheEntry | None = None

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
        result: dict[str, object] = {**best_entry.result}
        raw_meta = result.get("metadata")
        if isinstance(raw_meta, dict):
            meta: dict[str, object] = {**raw_meta}
            meta["cache_hit"] = True
            meta["approximate"] = True
            meta["fuzzy_distance"] = round(best_dist, 4)
            result["metadata"] = meta
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


class RoleProfileSummary(BaseModel):
    """Expressive subset of the 222K-track heuristic profile for one instrument role.

    Transmitted by Maestro so Orpheus can use data-driven priors
    without re-deriving them from scratch.
    """
    rest_ratio: float = 0.0
    syncopation_ratio: float = 0.0
    swing_ratio: float = 0.0
    pitch_range_semitones: float = 0.0
    contour_complexity: float = 0.0
    velocity_entropy: float = 0.0
    staccato_ratio: float = 0.0
    legato_ratio: float = 0.0
    sustained_ratio: float = 0.0
    motif_pitch_trigram_repeat: float = 0.0
    polyphony_mean: float = 1.0
    register_mean_pitch: float = 60.0


class EmotionVectorPayload(BaseModel):
    """Full 5-axis emotion vector as computed by Maestro.

    Transmitted losslessly so Orpheus never has to guess emotion.
    """
    energy: float = 0.5         # [0, 1]
    valence: float = 0.0        # [-1, 1]
    tension: float = 0.3        # [0, 1]
    intimacy: float = 0.5       # [0, 1]
    motion: float = 0.5         # [0, 1]


class GenerationConstraintsPayload(BaseModel):
    """Hard controls derived from the emotion vector by Maestro.

    When present Orpheus must treat these as authoritative and skip
    its own parallel derivation.
    """
    drum_density: float = 0.5
    subdivision: int = 8
    swing_amount: float = 0.0
    register_center: int = 60
    register_spread: int = 12
    rest_density: float = 0.3
    leap_probability: float = 0.2
    chord_extensions: bool = False
    borrowed_chord_probability: float = 0.0
    harmonic_rhythm_bars: float = 1.0
    velocity_floor: int = 60
    velocity_ceiling: int = 100


class IntentGoal(BaseModel):
    """A single weighted goal in the intent specification."""
    name: str
    weight: float = 1.0
    constraint_type: str = "soft"  # "hard" | "soft"


class GenerateRequest(BaseModel):
    """Music generation request.

    Carries the full canonical intent from Maestro: emotion_vector,
    role_profile_summary, generation_constraints, and weighted intent_goals.
    Orpheus consumes these directly — no parallel re-derivation.
    """
    # ── Core ──
    genre: str = "boom_bap"
    tempo: int = 90
    instruments: list[str] = ["drums", "bass"]
    bars: int = 4
    key: str | None = None

    # ── Canonical intent blocks ──
    emotion_vector: EmotionVectorPayload | None = None
    role_profile_summary: RoleProfileSummary | None = None
    generation_constraints: GenerationConstraintsPayload | None = None
    intent_goals: list[IntentGoal] | None = None

    # ── Observability ──
    seed: int | None = None
    trace_id: str | None = None
    intent_hash: str | None = None

    # ── Quality / overrides ──
    quality_preset: str = "balanced"
    temperature: float | None = None
    top_p: float | None = None

    # ── Correlation ──
    composition_id: str | None = None

    # ── Unified generation ──
    add_outro: bool = False
    unified_output: bool = False


class GenerateResponse(BaseModel):
    """Response from a generation request.

    ``notes`` and ``channel_notes`` use the camelCase wire format (``WireNoteDict``)
    for direct consumption by Maestro.  Internal processing uses ``StorpheusNoteDict``
    (snake_case) up until the response is assembled.
    """

    success: bool
    notes: list[WireNoteDict] | None = None
    channel_notes: dict[str, list[WireNoteDict]] | None = None
    tool_calls: list[dict[str, object]] | None = None
    error: str | None = None
    metadata: dict[str, object] | None = None


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
    result: GenerateResponse | None = None
    error: str | None = None
    created_at: float = 0.0
    started_at: float | None = None
    completed_at: float | None = None
    event: asyncio.Event = field(default_factory=asyncio.Event)
    position: int = 0
    dedupe_key: str | None = None
    composition_id: str | None = None


class JobQueue:
    """Bounded async job queue with a fixed-size worker pool.

    Replaces the semaphore model: callers submit jobs and poll for results
    instead of blocking on a single long HTTP request.
    """

    def __init__(self, max_queue: int = 20, max_workers: int = 2) -> None:
        self._queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=max_queue)
        self._jobs: dict[str, Job] = {}
        self._dedupe: dict[str, str] = {}  # dedupe_key -> job_id
        self._max_workers = max_workers
        self._max_queue = max_queue
        self._workers: list[asyncio.Task[None]] = []
        self._cleanup_task: asyncio.Task[None] | None = None

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

    def submit(self, request: GenerateRequest, dedupe_key: str | None = None) -> Job:
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

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    @property
    def running_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.status == JobStatus.RUNNING)

    def status_snapshot(self) -> dict[str, object]:
        return {
            "depth": self.depth,
            "running": self.running_count,
            "max_concurrent": self._max_workers,
            "max_queue": self._max_queue,
            "total_tracked": len(self._jobs),
        }

    # ── internal ────────────────────────────────────────────────────────

    def cancel(self, job_id: str) -> Job | None:
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
                job.result = await _do_generate(job.request, worker_id=worker_id)
                job.status = JobStatus.COMPLETE
            except Exception as exc:
                logger.error(f"❌ Worker {worker_id} job {job.id[:8]} failed: {exc}")
                job.status = JobStatus.FAILED
                job.error = str(exc)
                job.result = GenerateResponse(
                    success=False, error=str(exc),
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
                if _COOLDOWN_SECONDS > 0:
                    await asyncio.sleep(_COOLDOWN_SECONDS)

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


# =============================================================================
# GM instrument resolution — full 128-program coverage
#
# Full 128-program GM table.  Everything is keyed by GM program
# number (0-127); string names are only produced at the Gradio boundary
# via _TMIDIX_PATCH_NAMES.
# =============================================================================

# Authoritative TMIDIX Number2patch table.  Index = GM program number.
# Must match the list compiled into the Orpheus HF Space's TMIDIX module.
_TMIDIX_PATCH_NAMES: tuple[str, ...] = (
    # Piano (0-7)
    "Acoustic Grand", "Bright Acoustic", "Electric Grand", "Honky-Tonk",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clav",
    # Chromatic Percussion (8-15)
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
    "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    # Organ (16-23)
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    # Guitar (24-31)
    "Acoustic Guitar(nylon)", "Acoustic Guitar(steel)",
    "Electric Guitar(jazz)", "Electric Guitar(clean)",
    "Electric Guitar(muted)", "Overdriven Guitar",
    "Distortion Guitar", "Guitar Harmonics",
    # Bass (32-39)
    "Acoustic Bass", "Electric Bass(finger)", "Electric Bass(pick)",
    "Fretless Bass", "Slap Bass 1", "Slap Bass 2",
    "Synth Bass 1", "Synth Bass 2",
    # Strings (40-47)
    "Violin", "Viola", "Cello", "Contrabass",
    "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    # Ensemble (48-55)
    "String Ensemble 1", "String Ensemble 2", "SynthStrings 1", "SynthStrings 2",
    "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit",
    # Brass (56-63)
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
    "French Horn", "Brass Section", "SynthBrass 1", "SynthBrass 2",
    # Reed (64-71)
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax",
    "Oboe", "English Horn", "Bassoon", "Clarinet",
    # Pipe (72-79)
    "Piccolo", "Flute", "Recorder", "Pan Flute",
    "Blown Bottle", "Skakuhachi", "Whistle", "Ocarina",
    # Synth Lead (80-87)
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)",
    "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)",
    "Lead 7 (fifths)", "Lead 8 (bass+lead)",
    # Synth Pad (88-95)
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)",
    "Pad 4 (choir)", "Pad 5 (bowed)", "Pad 6 (metallic)",
    "Pad 7 (halo)", "Pad 8 (sweep)",
    # Synth Effects (96-103)
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)",
    "FX 4 (atmosphere)", "FX 5 (brightness)", "FX 6 (goblins)",
    "FX 7 (echoes)", "FX 8 (sci-fi)",
    # Ethnic (104-111)
    "Sitar", "Banjo", "Shamisen", "Koto",
    "Kalimba", "Bagpipe", "Fiddle", "Shanai",
    # Percussive (112-119)
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
    "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    # Sound Effects (120-127)
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet",
    "Telephone Ring", "Helicopter", "Applause", "Gunshot",
)

assert len(_TMIDIX_PATCH_NAMES) == 128

# Reverse lookup: TMIDIX name → program number (case-insensitive).
_TMIDIX_NAME_TO_PROGRAM: dict[str, int] = {
    name.lower(): idx for idx, name in enumerate(_TMIDIX_PATCH_NAMES)
}

# ---------------------------------------------------------------------------
# Alias table: natural-language role/instrument name → GM program number.
#
# Covers every alias from gm_instruments.py plus Stori prompt roles
# (djembe, gayageum, oud, etc.) mapped to the nearest GM proxy.
# Drums/percussion resolve to None (channel 10, no program change).
# ---------------------------------------------------------------------------
_DRUM_KEYWORDS: frozenset[str] = frozenset({
    "drums", "drum", "drum kit", "kit", "kick", "snare", "hihat", "hi-hat",
    "percussion", "perc", "beat", "cajon", "cajón", "djembe", "dundun",
    "shekere", "janggu", "kendang", "tabla", "riq", "qraqeb", "bombo",
    "guacharaca", "tumbadora", "congas", "bongos", "conga", "bongo",
    "clapping", "palmas", "taiko", "timbales", "cowbell",
    "808", "tr-808", "909", "tr-909",
})

_GM_ALIASES: dict[str, int] = {
    # --- Piano / keys (0-7) ---
    "piano": 0, "acoustic piano": 0, "grand piano": 0, "concert piano": 0,
    "bright piano": 1, "bright acoustic": 1,
    "electric grand": 2,
    "honky-tonk": 3, "honky tonk": 3, "honkytonk": 3, "ragtime piano": 3,
    "electric piano": 4, "electric piano 1": 4, "rhodes": 4, "fender rhodes": 4,
    "ep1": 4, "e-piano": 4,
    "electric piano 2": 5, "dx7": 5, "fm piano": 5, "ep2": 5,
    "harpsichord": 6, "clavecin": 6, "cembalo": 6,
    "clavinet": 7, "clav": 7, "d6": 7,
    # --- Chromatic Percussion (8-15) ---
    "celesta": 8, "celeste": 8,
    "glockenspiel": 9, "glock": 9, "bells": 9,
    "music box": 10, "musicbox": 10,
    "vibraphone": 11, "vibes": 11, "vibraharp": 11,
    "marimba": 12, "balafon": 12,
    "xylophone": 13, "xylo": 13,
    "tubular bells": 14, "chimes": 14, "orchestral chimes": 14,
    "dulcimer": 15, "hammered dulcimer": 15,
    # --- Organ (16-23) ---
    "organ": 16, "drawbar organ": 16, "hammond": 16, "b3": 16,
    "percussive organ": 17, "perc organ": 17,
    "rock organ": 18, "distorted organ": 18,
    "church organ": 19, "pipe organ": 19, "cathedral organ": 19,
    "reed organ": 20, "harmonium": 20,
    "accordion": 21, "accordian": 21,
    "harmonica": 22, "blues harp": 22, "mouth organ": 22,
    "tango accordion": 23, "bandoneon": 23, "bandonéon": 23,
    # --- Guitar (24-31) ---
    "nylon guitar": 24, "classical guitar": 24, "nylon": 24,
    "spanish guitar": 24,
    "acoustic guitar": 25, "steel guitar": 25, "steel string": 25,
    "folk guitar": 25, "mandolin": 25, "charango": 25,
    "jazz guitar": 26, "hollow body": 26,
    "electric guitar": 27, "clean guitar": 27, "clean electric": 27,
    "strat clean": 27,
    "muted guitar": 28, "palm mute": 28, "muted electric": 28,
    "overdriven guitar": 29, "overdrive guitar": 29, "crunchy guitar": 29,
    "distortion guitar": 30, "distorted guitar": 30, "heavy guitar": 30,
    "metal guitar": 30,
    "guitar harmonics": 31, "harmonics": 31,
    # --- Bass (32-39) ---
    "acoustic bass": 32, "upright bass": 32, "double bass": 32,
    "standup bass": 32, "contrabass": 32, "guembri": 32,
    "bass": 33, "electric bass": 33, "finger bass": 33,
    "bass guitar": 33, "fingered bass": 33,
    "pick bass": 34, "picked bass": 34,
    "fretless bass": 35, "fretless": 35,
    "slap bass": 36, "slap": 36, "slap bass 1": 36,
    "slap bass 2": 37, "pop bass": 37,
    "synth bass": 38, "synth bass 1": 38, "analog bass": 38,
    "bass drone": 38,
    "synth bass 2": 39, "digital bass": 39, "fm bass": 39,
    # --- Strings (40-47) ---
    "violin": 40, "fiddle": 40, "haegeum": 40,
    "viola": 41,
    "cello": 42, "violoncello": 42,
    "contrabass strings": 43, "string bass": 43,
    "tremolo strings": 44, "tremolo": 44,
    "pizzicato strings": 45, "pizzicato": 45, "pizz": 45,
    "harp": 46, "orchestral harp": 46, "concert harp": 46, "kora": 46,
    "timpani": 47, "kettle drums": 47, "kettle drum": 47,
    # --- Ensemble (48-55) ---
    "strings": 48, "string ensemble": 48, "orchestra strings": 48,
    "orchestral strings": 48,
    "slow strings": 49, "string ensemble 2": 49,
    "synth strings": 50, "string synth": 50,
    "synth strings 2": 51,
    "choir": 52, "choir aahs": 52, "vocal": 52, "vocals": 52, "aahs": 52,
    "voice": 52, "voice oohs": 53, "oohs": 53,
    "synth voice": 54, "vocoder": 54, "synth choir": 54,
    "vocal chop": 54, "vocal lead": 54,
    "orchestra hit": 55, "orch hit": 55, "stab": 55,
    # --- Brass (56-63) ---
    "trumpet": 56, "horn": 56,
    "trombone": 57,
    "tuba": 58,
    "muted trumpet": 59, "harmon mute": 59,
    "french horn": 60,
    "brass section": 61, "brass": 61, "horns": 61, "brass ensemble": 61,
    "synth brass": 62, "synth brass 1": 62,
    "synth brass 2": 63,
    # --- Reed (64-71) ---
    "soprano sax": 64, "soprano saxophone": 64,
    "alto sax": 65, "alto saxophone": 65, "alto": 65,
    "tenor sax": 66, "tenor saxophone": 66, "sax": 66, "saxophone": 66,
    "baritone sax": 67, "baritone saxophone": 67, "bari sax": 67,
    "oboe": 68,
    "english horn": 69, "cor anglais": 69,
    "bassoon": 70,
    "clarinet": 71,
    # --- Pipe (72-79) ---
    "piccolo": 72,
    "flute": 73,
    "recorder": 74,
    "pan flute": 75, "pan pipes": 75, "zampona": 75, "zampoña": 75,
    "quena": 75,
    "blown bottle": 76, "bottle": 76,
    "shakuhachi": 77, "skakuhachi": 77,
    "whistle": 78, "tin whistle": 78,
    "ocarina": 79,
    # --- Synth Lead (80-87) ---
    "lead": 80, "synth lead": 80, "square lead": 80, "square wave": 80,
    "saw lead": 81, "sawtooth lead": 81, "saw wave": 81,
    "calliope": 82, "calliope lead": 82, "arp": 82,
    "chiff lead": 83, "chiff": 83,
    "charang": 84, "distorted lead": 84,
    "voice lead": 85, "synth voice lead": 85,
    "fifths lead": 86, "power lead": 86,
    "bass lead": 87, "bass + lead": 87,
    # --- Synth Pad (88-95) ---
    "pad": 88, "pads": 88, "synth pad": 88, "new age pad": 88, "ambient pad": 88,
    "warm pad": 89, "analog pad": 89,
    "polysynth": 90, "poly pad": 90, "synth": 90,
    "choir pad": 91, "synth choir pad": 91,
    "bowed pad": 92, "bowed glass": 92,
    "metallic pad": 93, "metal pad": 93,
    "halo pad": 94, "halo": 94,
    "sweep pad": 95, "sweep": 95,
    # --- Synth Effects (96-103) ---
    "fx": 96, "rain": 96, "rain fx": 96,
    "soundtrack": 97, "cinematic": 97,
    "crystal": 98, "crystal fx": 98,
    "atmosphere": 99, "atmos": 99, "atmospheric": 99,
    "brightness": 100, "bright fx": 100,
    "goblins": 101, "goblin": 101,
    "echoes": 102, "echo fx": 102,
    "sci-fi": 103, "scifi": 103, "space": 103,
    # --- Ethnic (104-111) ---
    "sitar": 104,
    "banjo": 105,
    "shamisen": 106,
    "koto": 107, "gayageum": 107,
    "kalimba": 108, "thumb piano": 108,
    "bagpipe": 109, "bag pipe": 109, "bagpipes": 109,
    "shanai": 111, "shehnai": 111, "ney": 111,
    # --- Percussive (112-119) ---
    "tinkle bell": 112, "bell": 112,
    "agogo": 113,
    "steel drums": 114, "steel drum": 114, "steel pan": 114,
    "woodblock": 115, "wood block": 115,
    "melodic tom": 117, "tom": 117,
    "synth drum": 118, "electronic drum": 118,
    "reverse cymbal": 119, "cymbal reverse": 119,
    # --- Sound Effects (120-127) ---
    "fret noise": 120, "guitar noise": 120,
    "breath noise": 121, "breath": 121,
    "seashore": 122, "ocean": 122, "waves": 122,
    "bird": 123, "bird tweet": 123, "birds": 123,
    "telephone": 124, "phone ring": 124,
    "helicopter": 125, "chopper": 125,
    "applause": 126, "clapping": 126,
    "gunshot": 127, "gun": 127,
    # --- Abstract Maestro roles → sensible GM defaults ---
    "melody": 0, "chords": 0, "harmony": 0,
    "keys": 4, "keyboard": 0,
    "guitar": 25,
    "organ bubble": 16,
    "synth chord": 90,
    "tanpura drone": 104,
    "gaita": 75,
    "qanun": 15,
    "gangsa": 11, "reyong": 11, "jegogan": 12, "gong": 112,
}


def resolve_gm_program(role: str) -> int | None:
    """Resolve a Maestro instrument role to a GM program number (0-127).

    Returns None for drums/percussion (channel 10, no program change).
    Returns None if the role cannot be matched.
    """
    key = role.lower().strip()
    if key in _DRUM_KEYWORDS:
        return None
    program = _GM_ALIASES.get(key)
    if program is not None:
        return program
    # Substring match: try each alias as a substring of the input
    for alias, prog in _GM_ALIASES.items():
        if alias in key or key in alias:
            return prog
    return None


def resolve_tmidix_name(role: str) -> str | None:
    """Resolve a Maestro role to the TMIDIX Number2patch string.

    Returns "Drums" for drum/percussion roles.
    Returns None only if the role cannot be matched at all.
    """
    key = role.lower().strip()
    if key in _DRUM_KEYWORDS:
        return "Drums"
    program = resolve_gm_program(key)
    if program is not None:
        return _TMIDIX_PATCH_NAMES[program]
    return None


# GM family groups for human-readable channel labels (program → family name).
_GM_FAMILY: tuple[tuple[int, int, str], ...] = (
    (0,   7,  "piano"),
    (8,  15,  "chromatic_perc"),
    (16, 23,  "organ"),
    (24, 31,  "guitar"),
    (32, 39,  "bass"),
    (40, 47,  "strings"),
    (48, 55,  "ensemble"),
    (56, 63,  "brass"),
    (64, 71,  "reed"),
    (72, 79,  "pipe"),
    (80, 87,  "synth_lead"),
    (88, 95,  "synth_pad"),
    (96, 103, "synth_fx"),
    (104, 111, "ethnic"),
    (112, 119, "percussive"),
    (120, 127, "sfx"),
)


def _gm_family_for_program(program: int) -> str:
    """Return the GM family label for a program number (0-127)."""
    for lo, hi, family in _GM_FAMILY:
        if lo <= program <= hi:
            return family
    return "unknown"


def _channel_label(
    ch: int | str,
    program_changes: dict[int, int] | None = None,
) -> str:
    """Human-readable label for a MIDI channel.

    When ``program_changes`` is provided, the actual GM program number
    for the channel is used to derive the family label (bass, piano,
    guitar, etc.).  This produces correct labels for multi-instrument
    output where the model assigns distinct programs per channel.

    Falls back to channel-index heuristics when no program info is
    available (legacy single-instrument path).
    """
    if isinstance(ch, str):
        return ch
    if ch == 9:
        return "drums"

    if program_changes and ch in program_changes:
        prog = program_changes[ch]
        family = _gm_family_for_program(prog)
        seen: dict[str, int] = {}
        for c in sorted(program_changes):
            if c == 9:
                continue
            f = _gm_family_for_program(program_changes[c])
            seen[f] = seen.get(f, 0) + 1
        if seen.get(family, 0) > 1:
            return f"{family}_{ch}"
        return family

    _MELODIC_LABELS: dict[int, str] = {0: "bass", 1: "keys"}
    return _MELODIC_LABELS.get(ch, f"melody_{ch}" if ch > 2 else "melody")


def _resolve_melodic_index(role: str) -> int | None:
    """Map a role to a preferred melodic channel index (0-based, excluding ch9).

    Channel assignment by GM category:
      0 = bass family (GM 32-39)
      1 = piano/keys/organ/harmony (GM 0-7, 16-23)
      2 = everything else (melody, guitar, strings, brass, etc.)

    Returns None for drums/percussion.
    """
    key = role.lower().strip()
    if key in _DRUM_KEYWORDS:
        return None
    program = resolve_gm_program(key)
    if program is None:
        return 2  # unknown melodic → default to channel 2
    if 32 <= program <= 39:
        return 0  # bass family
    if program <= 7 or 16 <= program <= 23:
        return 1  # piano/keys/organ
    return 2  # everything else




# ---------------------------------------------------------------------------
# Seed provenance + quality analysis
# ---------------------------------------------------------------------------

_MIN_SEED_NOTES = 8
_MIN_SEED_BYTES = 200


def analyze_seed(path: str) -> dict[str, object]:
    """Analyze a seed MIDI file and return a provenance report.

    Cheap pre-check: note count, pitch range, polyphony, density, drum hits.
    """
    try:
        file_bytes = os.path.getsize(path)
        mid = mido.MidiFile(path)
    except Exception as e:
        return {"error": str(e), "seed_bytes": 0, "quality_ok": False}

    ppq = mid.ticks_per_beat
    has_tempo_meta = False
    note_count = 0
    drum_hits = 0
    track_count = 0
    pitches: list[int] = []
    simultaneous: dict[float, int] = {}

    for track in mid.tracks:
        track_has_notes = False
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "set_tempo":
                has_tempo_meta = True
            if msg.type == "note_on" and msg.velocity > 0:
                note_count += 1
                track_has_notes = True
                beat = abs_tick / ppq if ppq else 0
                simultaneous[round(beat, 2)] = simultaneous.get(round(beat, 2), 0) + 1
                if msg.channel == 9:
                    drum_hits += 1
                else:
                    pitches.append(msg.note)
        if track_has_notes:
            track_count += 1

    max_beat = max(simultaneous.keys()) if simultaneous else 0
    bars_estimate = round(max_beat / 4, 1) if max_beat > 0 else 0
    density_per_bar = note_count / max(bars_estimate, 0.25)

    pitch_range = (max(pitches) - min(pitches)) if pitches else 0
    polyphony_mean = (
        sum(simultaneous.values()) / len(simultaneous)
        if simultaneous else 0
    )

    # Rough token estimate: TMIDIX uses ~3 tokens per note + program changes
    token_estimate = note_count * 3 + track_count * 2

    quality_ok = (
        note_count >= _MIN_SEED_NOTES
        and file_bytes >= _MIN_SEED_BYTES
    )

    return {
        "seed_bytes": file_bytes,
        "seed_tracks": track_count,
        "seed_notes": note_count,
        "seed_drum_hits": drum_hits,
        "seed_bars_estimate": bars_estimate,
        "seed_ppq": ppq,
        "seed_has_tempo_meta": has_tempo_meta,
        "seed_token_count_estimate": token_estimate,
        "seed_pitch_range": pitch_range,
        "seed_density_per_bar": round(density_per_bar, 1),
        "seed_polyphony_mean": round(polyphony_mean, 2),
        "quality_ok": quality_ok,
    }


@dataclass
class ResolvedSeed:
    """Encapsulates a resolved seed with transposition metadata."""
    path: str
    source_type: str
    source_uri: str | None
    transpose_semitones: int = 0
    detected_key: str | None = None
    key_confidence: float = 0.0


def _resolve_seed(
    genre: str,
    target_key: str | None = None,
) -> ResolvedSeed:
    """Select a curated seed MIDI from the seed library.

    When *target_key* is provided (e.g. ``"Am"``), prefers seeds whose
    detected key is closest and returns transposition info so the caller
    can shift the seed into the exact target key before generation.

    Raises RuntimeError if no suitable seed is available.
    """
    selection = select_seed_with_key(
        genre, target_key=target_key, randomize=True,
    )
    if selection is not None:
        report = analyze_seed(selection.path)
        if report.get("quality_ok"):
            logger.info(
                f"🌱 Using curated seed: {selection.path} "
                f"({report['seed_notes']} notes, ~{report['seed_token_count_estimate']} tokens, "
                f"key={selection.detected_key or '?'}, transpose={selection.transpose_semitones:+d})"
            )
            return ResolvedSeed(
                path=selection.path,
                source_type="curated_library",
                source_uri=selection.path,
                transpose_semitones=selection.transpose_semitones,
                detected_key=selection.detected_key,
                key_confidence=selection.key_confidence,
            )
        else:
            logger.warning(
                f"⚠️ Curated seed quality check failed ({selection.path}): "
                f"{report.get('seed_notes', 0)} notes, {report.get('seed_bytes', 0)} bytes"
            )

    fallback = select_seed("general", randomize=True)
    if fallback is not None:
        logger.warning(f"⚠️ No seed for genre '{genre}', using general fallback")
        return ResolvedSeed(
            path=fallback,
            source_type="curated_library",
            source_uri=fallback,
        )

    raise RuntimeError(
        f"No curated seed available for genre '{genre}' or general fallback. "
        "Ensure the seed library is built (run build_seed_library.py)."
    )


def parse_midi_to_notes(midi_path: str, tempo: int) -> ParsedMidiResult:
    """
    Parse MIDI file into notes AND expressive events grouped by channel.

    Returns ``{"notes": {ch: [...]}, "cc_events": {ch: [...]},
               "pitch_bends": {ch: [...]}, "aftertouch": {ch: [...]},
               "program_changes": {ch: program_number}}``.
    """
    mid = mido.MidiFile(midi_path)
    ticks_per_beat = mid.ticks_per_beat

    notes: dict[int, list[StorpheusNoteDict]] = {}
    cc_events: dict[int, list[StorpheusCCEvent]] = {}
    pitch_bends: dict[int, list[StorpheusPitchBend]] = {}
    aftertouch: dict[int, list[StorpheusAftertouch]] = {}
    program_changes: dict[int, int] = {}

    for track in mid.tracks:
        time = 0
        for msg in track:
            time += msg.time
            beat = round(time / ticks_per_beat, 3)

            if msg.type == 'program_change':
                program_changes[msg.channel] = msg.program

            elif msg.type == 'note_on' and msg.velocity > 0:
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

    if program_changes:
        logger.debug(f"🎹 Parsed MIDI program changes: {program_changes}")

    return {
        "notes": notes,
        "cc_events": cc_events,
        "pitch_bends": pitch_bends,
        "aftertouch": aftertouch,
        "program_changes": program_changes,
    }




def _channels_to_keep(channel_keys: set[int], instruments: list[str]) -> set[int]:
    """Determine which MIDI channels to keep for the requested instruments.

    When the preferred channel index doesn't exist in the generated MIDI,
    falls back to the nearest available melodic channel instead of returning
    an empty set (which caused silent "No notes found" failures).
    """
    if not instruments:
        return channel_keys
    requested = [i.lower().strip() for i in instruments]
    keep: set[int] = set()
    melodic_channels = sorted(c for c in channel_keys if c != 9)

    for inst in requested:
        if inst in _DRUM_KEYWORDS:
            if 9 in channel_keys:
                keep.add(9)
            continue

        preferred_idx = _resolve_melodic_index(inst)
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


def filter_channels_for_instruments(parsed: ParsedMidiResult, instruments: list[str]) -> ParsedMidiResult:
    """
    Keep only channels that correspond to the requested instruments.

    Accepts the full parsed dict (notes, cc_events, pitch_bends, aftertouch)
    returned by ``parse_midi_to_notes`` and filters every sub-dict.
    """
    all_chs: set[int] = set()
    all_chs.update(parsed["notes"].keys())
    all_chs.update(parsed["cc_events"].keys())
    all_chs.update(parsed["pitch_bends"].keys())
    all_chs.update(parsed["aftertouch"].keys())

    keep = _channels_to_keep(all_chs, instruments)

    return {
        "notes": {ch: evts for ch, evts in parsed["notes"].items() if ch in keep},
        "cc_events": {ch: evts for ch, evts in parsed["cc_events"].items() if ch in keep},
        "pitch_bends": {ch: evts for ch, evts in parsed["pitch_bends"].items() if ch in keep},
        "aftertouch": {ch: evts for ch, evts in parsed["aftertouch"].items() if ch in keep},
        "program_changes": parsed["program_changes"],
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "storpheus"}


# ── Artifact download endpoints ──

@app.get("/artifacts/{comp_id}", response_model=None)
async def list_artifacts(comp_id: str) -> dict[str, object] | JSONResponse:
    """list artifact files for a composition."""
    from fastapi.responses import JSONResponse
    artifacts_dir = pathlib.Path(
        os.environ.get("STORPHEUS_CACHE_DIR", "/tmp")
    ) / "artifacts" / comp_id
    if not artifacts_dir.is_dir():
        return JSONResponse({"files": []}, status_code=200)
    files = sorted(f.name for f in artifacts_dir.iterdir() if f.is_file())
    return {"files": files, "path": str(artifacts_dir)}


@app.get("/artifacts/{comp_id}/{filename}", response_model=None)
async def download_artifact(comp_id: str, filename: str) -> object:
    """Download a single artifact file."""
    from fastapi.responses import FileResponse
    artifacts_dir = pathlib.Path(
        os.environ.get("STORPHEUS_CACHE_DIR", "/tmp")
    ) / "artifacts" / comp_id
    fpath = artifacts_dir / filename
    if not fpath.is_file():
        return {"error": f"Not found: {filename}"}
    media_types = {
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".mid": "audio/midi",
        ".midi": "audio/midi", ".webp": "image/webp", ".png": "image/png",
    }
    mt = media_types.get(fpath.suffix.lower(), "application/octet-stream")
    return FileResponse(str(fpath), media_type=mt, filename=filename)


@app.get("/diagnostics")
async def diagnostics() -> dict[str, object]:
    """Structured diagnostics for the Orpheus service pipeline."""
    now = time()
    space_id = os.environ.get("STORI_STORPHEUS_SPACE", _DEFAULT_SPACE)

    gradio_status = "disconnected"
    hf_space_status = "unknown"
    _diag_client = _client_pool.get(worker_id=0)
    if _diag_client is not None:
        gradio_status = "connected"
        try:
            await asyncio.wait_for(
                asyncio.to_thread(_diag_client.view_api, print_info=False),
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
        "service": "storpheus",
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
        "predict_timeout_s": float(os.environ.get("STORPHEUS_PREDICT_TIMEOUT", "180")),
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
async def cache_stats() -> dict[str, object]:
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

        "top_entries": entries_info,
        "policy_version": get_policy_version(),
    }


@app.post("/cache/warm")
async def warm_cache() -> dict[str, object]:
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

    async def _warm() -> None:
        ok, fail = 0, 0
        for req in to_generate:
            try:
                resp = await _do_generate(req)
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
async def clear_cache() -> dict[str, object]:
    """Clear all caches."""
    _result_cache.clear()
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

    tool_calls: list[QualityEvalToolCall]
    bars: int
    tempo: int


@app.post("/quality/evaluate")
async def evaluate_quality(request: QualityEvaluationRequest) -> dict[str, object]:
    """Evaluate the quality of generated music.

    Used for A/B testing policies, monitoring quality over time,
    and automated quality gates.
    """
    all_notes: list[StorpheusNoteDict] = []
    for tool_call in request.tool_calls:
        if tool_call["tool"] == "addNotes":
            all_notes.extend(tool_call["params"].get("notes", []))

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
async def ab_test(request: ABTestRequest) -> dict[str, object]:
    """
    A/B test two generation configurations.
    
    Generates music with both configs and compares quality metrics.
    Useful for testing policy changes before deploying.
    """
    result_a = await _do_generate(request.config_a)
    result_b = await _do_generate(request.config_b)
    
    if not result_a.success or not result_b.success:
        return {
            "error": "One or both generations failed",
            "result_a_success": result_a.success,
            "result_b_success": result_b.success,
        }

    def _to_storpheus(wire: list[WireNoteDict]) -> list[StorpheusNoteDict]:
        return [
            StorpheusNoteDict(
                pitch=n["pitch"], start_beat=n["startBeat"],
                duration_beats=n["durationBeats"], velocity=n["velocity"],
            )
            for n in wire
        ]

    comparison = compare_generations(
        _to_storpheus(result_a.notes or []),
        _to_storpheus(result_b.notes or []),
        request.config_a.bars,
        request.config_a.tempo,
    )
    
    return {
        "comparison": comparison,
        "config_a_cache_hit": bool(result_a.metadata.get("cache_hit")) if result_a.metadata else False,
        "config_b_cache_hit": bool(result_b.metadata.get("cache_hit")) if result_b.metadata else False,
    }


# ── Persistent session state per composition ──────────────────────────
# Replicates Gradio's gr.State accumulation across calls within the same
# composition, but with a token cap to prevent unbounded growth.

@dataclass
class CompositionState:
    """Tracks evolving composition state across sections and instruments."""
    composition_id: str
    session_id: str
    accumulated_midi_path: str | None = None
    last_token_estimate: int = 0
    created_at: float = field(default_factory=time)
    call_count: int = 0

_composition_states: dict[str, CompositionState] = {}
_COMPOSITION_STATE_TTL = 3600  # 1 hour


def _get_or_create_session(composition_id: str | None) -> tuple[str, CompositionState]:
    """Return (session_hash, CompositionState) for the composition.

    If the composition already has accumulated state, reuse the same
    session_id so Gradio's gr.State preserves token context.  When the
    accumulated tokens exceed MAX_SESSION_TOKENS, rotate the session to
    prevent model degradation (truncation via fresh session, not full reset).
    """
    now = time()
    # Evict stale entries
    stale = [k for k, v in _composition_states.items() if now - v.created_at > _COMPOSITION_STATE_TTL]
    for k in stale:
        _composition_states.pop(k, None)

    if not composition_id:
        fresh_id = str(uuid.uuid4())
        return fresh_id, CompositionState(composition_id="ephemeral", session_id=fresh_id)

    state = _composition_states.get(composition_id)
    if state is None:
        session_id = str(uuid.uuid4())
        state = CompositionState(composition_id=composition_id, session_id=session_id)
        _composition_states[composition_id] = state
        logger.info(f"🆕 New composition session: {composition_id[:8]} → {session_id[:8]}")
        return session_id, state

    if state.last_token_estimate >= MAX_SESSION_TOKENS:
        old_sid = state.session_id
        state.session_id = str(uuid.uuid4())
        state.last_token_estimate = 0
        logger.info(
            f"🔄 Session token cap reached ({MAX_SESSION_TOKENS}) for "
            f"{composition_id[:8]} — rotated {old_sid[:8]} → {state.session_id[:8]}"
        )

    state.call_count += 1
    return state.session_id, state


async def _do_generate(request: GenerateRequest, worker_id: int = 0) -> GenerateResponse:
    """Core GPU generation logic — called by JobQueue workers."""
    global _last_successful_gen

    _trace = request.trace_id or ""
    _log_prefix = ""
    if request.composition_id:
        _log_prefix += f"[{request.composition_id[:8]}]"
    if _trace:
        _log_prefix += f"[t:{_trace[:8]}]"

    cache_key = get_cache_key(request)

    try:
        _use_loops = (
            os.environ.get("STORI_STORPHEUS_USE_LOOPS_MODEL", "").lower() in ("1", "true", "yes")
            and request.bars <= 8
        )
        if _use_loops:
            _loops = _client_pool.fresh_loops(worker_id)
            if _loops:
                client = _loops
                logger.info(f"🔁 Routing to Loops model ({request.bars} bars)")
            else:
                client = _client_pool.fresh(worker_id)
        else:
            client = _client_pool.fresh(worker_id)

        resolved = _resolve_seed(
            genre=request.genre,
            target_key=request.key,
        )
        seed_path = resolved.path
        seed_source_type = resolved.source_type
        seed_uri = resolved.source_uri

        # Apply key transposition if needed
        if resolved.transpose_semitones != 0:
            seed_path = str(transpose_midi(seed_path, resolved.transpose_semitones))
            logger.info(
                f"{_log_prefix} 🎹 Transposed seed by {resolved.transpose_semitones:+d} semitones "
                f"({resolved.detected_key} → {request.key})"
            )

        seed_report = analyze_seed(seed_path)
        seed_hash = hashlib.sha256(open(seed_path, "rb").read()).hexdigest()[:16]

        if not seed_report.get("quality_ok"):
            logger.warning(
                f"⚠️ Seed quality below threshold: "
                f"{seed_report.get('seed_notes', 0)} notes, "
                f"{seed_report.get('seed_bytes', 0)} bytes "
                f"(source={seed_source_type})"
            )

        logger.info(
            f"{_log_prefix} 🌱 Seed: source={seed_source_type} "
            f"notes={seed_report.get('seed_notes', '?')} "
            f"tokens≈{seed_report.get('seed_token_count_estimate', '?')} "
            f"bytes={seed_report.get('seed_bytes', '?')} "
            f"hash={seed_hash}"
        )

        orpheus_instruments: list[str] = []
        _unresolved: list[str] = []
        for inst in request.instruments:
            tmidix_name = resolve_tmidix_name(inst)
            if tmidix_name and tmidix_name not in orpheus_instruments:
                orpheus_instruments.append(tmidix_name)
            elif tmidix_name is None:
                _unresolved.append(inst)

        if _unresolved:
            logger.warning(
                f"⚠️ Could not resolve GM mapping for: {_unresolved} "
                f"(from request.instruments={request.instruments})"
            )

        if not orpheus_instruments:
            orpheus_instruments = ["Drums", "Electric Bass(finger)"]
            logger.warning(
                f"⚠️ No instruments resolved for {request.instruments}, "
                f"falling back to {orpheus_instruments}"
            )

        logger.info(
            f"🎹 Instrument mapping: {request.instruments} → {orpheus_instruments}"
        )

        # ── Derive params from control vector (activated) ──
        _pre_controls = build_controls(
            genre=request.genre,
            tempo=request.tempo,
            emotion_vector=request.emotion_vector or None,
            role_profile_summary=request.role_profile_summary or None,
            generation_constraints=request.generation_constraints or None,
            intent_goals=request.intent_goals or None,
            quality_preset=request.quality_preset,
        )
        _derived = apply_controls_to_params(_pre_controls, request.bars)

        # Explicit overrides from the request take precedence
        temperature = request.temperature if request.temperature is not None else _derived["temperature"]
        top_p = request.top_p if request.top_p is not None else _derived["top_p"]
        num_prime_tokens = _derived["num_prime_tokens"]
        num_gen_tokens = _derived["num_gen_tokens"]

        # Check if prime token budget vastly exceeds seed content
        _seed_tok_est = seed_report.get("seed_token_count_estimate")
        effective_prime_tokens = min(
            num_prime_tokens,
            _seed_tok_est if isinstance(_seed_tok_est, int) else num_prime_tokens,
        )
        _ctx_util = (num_prime_tokens + num_gen_tokens) / 8192 * 100
        _prime_utilization = (
            effective_prime_tokens / num_prime_tokens * 100
            if num_prime_tokens > 0 else 0
        )

        if _prime_utilization < 10:
            logger.warning(
                f"⚠️ Prime utilization critically low: "
                f"~{effective_prime_tokens} seed tokens vs "
                f"{num_prime_tokens} requested ({_prime_utilization:.0f}%)"
            )

        logger.info(
            f"{_log_prefix} 🎵 Generating {request.genre} @ {request.tempo} BPM | "
            f"temp={temperature:.2f} top_p={top_p:.2f} "
            f"prime={num_prime_tokens} (effective≈{effective_prime_tokens}) "
            f"gen={num_gen_tokens} ctx={_ctx_util:.0f}%"
        )

        _predict_timeout = float(os.environ.get("STORPHEUS_PREDICT_TIMEOUT", "180"))
        session_hash, comp_state = _get_or_create_session(request.composition_id)

        # Section seeding: override seed with accumulated MIDI for section continuity
        if comp_state.accumulated_midi_path and os.path.exists(comp_state.accumulated_midi_path):
            seed_path = comp_state.accumulated_midi_path
            seed_source_type = "accumulated_composition"
            seed_uri = seed_path
            seed_report = analyze_seed(seed_path)
            seed_hash = hashlib.sha256(open(seed_path, "rb").read()).hexdigest()[:16]
            logger.info(
                f"{_log_prefix} 🔗 Using accumulated MIDI as seed for section continuity "
                f"(notes={seed_report.get('seed_notes', '?')}, hash={seed_hash})"
            )

        # Seed MIDI already provides drum context when present.  The HF Space's
        # add_drums flag appends a random drum-pitch token that conflicts with
        # seeds that already contain percussion.  Only enable when there is no
        # seed MIDI to provide drum context.
        _has_drums = "drums" in [i.lower() for i in request.instruments]
        _is_multi_instrument = request.unified_output and len(request.instruments) > 1
        add_drums = _has_drums and seed_path is None
        max_beat = request.bars * 4

        # Deterministic seed: if provided, control batch selection
        rng = random.Random(request.seed) if request.seed is not None else random

        # ── Log Gradio inputs for audit trail ──
        _gradio_inputs = {
            "input_midi_hash": seed_hash,
            "prime_instruments": orpheus_instruments,
            "num_prime_tokens": num_prime_tokens,
            "effective_prime_tokens_estimate": effective_prime_tokens,
            "num_gen_tokens": num_gen_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "add_drums": add_drums,
            "add_outro": request.add_outro,
        }
        logger.info(
            f"{_log_prefix} 🔧 Gradio inputs: {json.dumps(_gradio_inputs)}"
        )

        # ── Seed-first mode ──
        # The HF Space is a continuation model: seed MIDI provides 1000-2000
        # tokens of genre-appropriate context; prime_instruments alone produces
        # only ~8 tokens.  Always prefer seed MIDI for quality.  When seed MIDI
        # is provided the Space ignores prime_instruments (mutually exclusive
        # code paths in app.py), which is fine — we rely on channel extraction
        # (_extract_channel_for_role) to pick instruments from the output.
        _input_midi = handle_file(seed_path) if seed_path else None

        if _input_midi is not None:
            logger.info(
                f"{_log_prefix} 🌱 SEED-FIRST MODE: seed MIDI provides context, "
                f"prime_instruments={orpheus_instruments} (advisory, "
                f"Space uses seed tokens instead) | "
                f"multi_instrument={_is_multi_instrument}"
            )
            _gradio_inputs["mode"] = "seed_first"
        else:
            logger.info(
                f"{_log_prefix} 🎛️ PRIME-ONLY MODE (no seed available): "
                f"prime_instruments={orpheus_instruments}"
            )
            _gradio_inputs["mode"] = "prime_instruments_only"

        _client_id = id(client)
        logger.info(
            f"{_log_prefix} 🔌 Gradio client={_client_id} | "
            f"calling /generate_music_and_state"
        )

        try:
            _gen_result = await asyncio.wait_for(
                asyncio.to_thread(
                    client.predict,
                    input_midi=_input_midi,
                    apply_sustains=True,
                    remove_duplicate_pitches=True,
                    remove_overlapping_durations=True,
                    prime_instruments=orpheus_instruments,
                    num_prime_tokens=num_prime_tokens,
                    num_gen_tokens=num_gen_tokens,
                    model_temperature=temperature,
                    model_top_p=top_p,
                    add_drums=add_drums,
                    add_outro=request.add_outro,
                    api_name="/generate_music_and_state",
                ),
                timeout=_predict_timeout,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            kind = "cancelled" if isinstance(exc, asyncio.CancelledError) else "timed out"
            logger.error(f"❌ Gradio /generate_music_and_state {kind} after {_predict_timeout}s")
            return GenerateResponse(
                success=False,
                error=f"Orpheus generation {kind} after {_predict_timeout}s",
            )

        # Log what the Space returned (State is auto-managed by gradio_client)
        _gen_type = type(_gen_result).__name__
        _gen_len = len(_gen_result) if isinstance(_gen_result, (list, tuple)) else "N/A"
        logger.info(
            f"{_log_prefix} 📦 /generate_music_and_state returned: "
            f"type={_gen_type} len={_gen_len}"
        )

        # ── Batch selection with rejection sampling ──
        # The HF Space generates 10 parallel stochastic batches per
        # /generate_music_and_state call.  We pick a random batch index
        # (0-9) for variety, then score the result.  For non-fast presets,
        # we can retry with a fresh generation if the score is poor.
        _num_candidates = quality_preset_to_batch_count(request.quality_preset)
        _rejection_threshold = float(os.environ.get("STORPHEUS_REJECTION_THRESHOLD", "0.3"))

        # Extract scoring params from constraints for candidate scoring
        _gc = request.generation_constraints
        _scoring = ScoringParams(
            bars=request.bars,
            target_key=request.key,
            expected_channels=len(request.instruments),
            target_density=_pre_controls.density * 100.0,
            register_center=_gc.register_center if _gc else None,
            register_spread=_gc.register_spread if _gc else None,
            velocity_floor=_gc.velocity_floor if _gc else None,
            velocity_ceiling=_gc.velocity_ceiling if _gc else None,
        )

        best_candidate: BestCandidate | None = None
        best_score: CandidateScore | None = None
        all_candidate_scores: list[dict[str, object]] = []

        for _attempt in range(_num_candidates):
            batch_idx = rng.randint(0, 9)

            if _attempt > 0:
                client = _client_pool.fresh(worker_id)
                logger.info(
                    f"{_log_prefix} 🔄 Rejection retry {_attempt}/{_num_candidates}: "
                    f"fresh client, batch_idx={batch_idx}"
                )
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            client.predict,
                            input_midi=_input_midi,
                            apply_sustains=True,
                            remove_duplicate_pitches=True,
                            remove_overlapping_durations=True,
                            prime_instruments=orpheus_instruments,
                            num_prime_tokens=num_prime_tokens,
                            num_gen_tokens=num_gen_tokens,
                            model_temperature=temperature,
                            model_top_p=top_p,
                            add_drums=add_drums,
                            add_outro=request.add_outro,
                            api_name="/generate_music_and_state",
                        ),
                        timeout=_predict_timeout,
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    logger.warning(f"⚠️ Rejection retry {_attempt} timed out, using best so far")
                    break
                except Exception as exc:
                    logger.warning(f"⚠️ Rejection retry {_attempt} failed: {exc}")
                    break

            try:
                midi_result = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.predict,
                        batch_number=batch_idx,
                        api_name="/add_batch",
                    ),
                    timeout=60,
                )
                logger.info(f"{_log_prefix} ✅ /add_batch({batch_idx}) ok")
            except Exception as exc:
                logger.error(f"❌ /add_batch({batch_idx}) failed: {exc}")
                if best_candidate is None:
                    return GenerateResponse(
                        success=False,
                        error=f"Orpheus add_batch failed on batch {batch_idx}: {exc}",
                    )
                logger.warning(f"⚠️ Using best candidate from previous attempt")
                break

            if midi_result is None:
                continue

            _attempt_midi_path: str = midi_result[2]
            _attempt_parsed = await asyncio.to_thread(
                parse_midi_to_notes, _attempt_midi_path, request.tempo,
            )

            # Trim events beyond max_beat in-place on the typed sub-dicts
            for ch in list(_attempt_parsed["notes"]):
                _attempt_parsed["notes"][ch] = [
                    n for n in _attempt_parsed["notes"][ch]
                    if n.get("start_beat", 0.0) < max_beat
                ]
                if not _attempt_parsed["notes"][ch]:
                    del _attempt_parsed["notes"][ch]
            for ch in list(_attempt_parsed["cc_events"]):
                _attempt_parsed["cc_events"][ch] = [
                    e for e in _attempt_parsed["cc_events"][ch] if e["beat"] < max_beat
                ]
                if not _attempt_parsed["cc_events"][ch]:
                    del _attempt_parsed["cc_events"][ch]
            for ch in list(_attempt_parsed["pitch_bends"]):
                _attempt_parsed["pitch_bends"][ch] = [
                    e for e in _attempt_parsed["pitch_bends"][ch] if e["beat"] < max_beat
                ]
                if not _attempt_parsed["pitch_bends"][ch]:
                    del _attempt_parsed["pitch_bends"][ch]
            for ch in list(_attempt_parsed["aftertouch"]):
                _attempt_parsed["aftertouch"][ch] = [
                    e for e in _attempt_parsed["aftertouch"][ch] if e["beat"] < max_beat
                ]
                if not _attempt_parsed["aftertouch"][ch]:
                    del _attempt_parsed["aftertouch"][ch]

            _attempt_flat: list[StorpheusNoteDict] = [
                note
                for ch_notes in _attempt_parsed["notes"].values()
                for note in ch_notes
            ]

            if not _attempt_flat:
                logger.warning(f"⚠️ Batch {batch_idx} produced zero notes, skipping")
                all_candidate_scores.append({"batch": batch_idx, "score": 0.0, "notes": 0})
                continue

            candidate_score = score_candidate(
                _attempt_flat,
                _attempt_parsed["notes"],
                batch_index=batch_idx,
                params=_scoring,
            )

            all_candidate_scores.append({
                "batch": batch_idx,
                "score": candidate_score.total_score,
                "notes": candidate_score.note_count,
                "key": candidate_score.detected_key,
                "dims": candidate_score.dimensions,
            })

            logger.info(
                f"🎲 Candidate {_attempt}: batch={batch_idx} "
                f"score={candidate_score.total_score:.3f} "
                f"notes={candidate_score.note_count} "
                f"key={candidate_score.detected_key or '?'}"
            )

            if best_score is None or candidate_score.total_score > best_score.total_score:
                best_score = candidate_score
                best_candidate = BestCandidate(
                    midi_result=midi_result,
                    midi_path=_attempt_midi_path,
                    parsed=_attempt_parsed,
                    flat_notes=_attempt_flat,
                    batch_idx=batch_idx,
                )

            if candidate_score.total_score >= (1.0 - _rejection_threshold):
                logger.info(f"✅ Score {candidate_score.total_score:.3f} above threshold, accepting")
                break

        if best_candidate is None:
            return GenerateResponse(
                success=False,
                error="All candidates produced zero usable notes",
            )

        midi_result = best_candidate.midi_result
        midi_path = best_candidate.midi_path
        parsed = best_candidate.parsed
        batch_idx = best_candidate.batch_idx
        snake_notes: list[StorpheusNoteDict] = list(best_candidate.flat_notes)
        mvp_notes: list[WireNoteDict] = [
            {"pitch": n["pitch"], "startBeat": n["start_beat"],
             "durationBeats": n["duration_beats"], "velocity": n["velocity"]}
            for n in snake_notes
        ]

        all_channels = set(parsed["notes"].keys())
        pitches: list[int] = [n["pitch"] for n in snake_notes] if snake_notes else []
        _best_total = best_score.total_score if best_score else 0.0
        logger.info(
            f"🏆 Best candidate: batch={batch_idx} "
            f"score={_best_total:.3f} "
            f"notes={len(mvp_notes)} "
            f"channels={sorted(all_channels)} "
            f"pitch=[{min(pitches) if pitches else 0}-{max(pitches) if pitches else 0}] "
            f"(evaluated {len(all_candidate_scores)} candidates)"
        )

        # ── Capture all Gradio artifacts (WAV, plot, MIDI) ──
        wav_path: str | None = None
        plot_path: str | None = None
        _comp_id = request.composition_id or "ephemeral"
        _artifacts_dir = (
            pathlib.Path(os.environ.get("STORPHEUS_CACHE_DIR", "/tmp"))
            / "artifacts"
            / _comp_id
        )
        _artifacts_dir.mkdir(parents=True, exist_ok=True)
        _artifact_id = f"b{batch_idx}_{request.trace_id[:8] if request.trace_id else 'notrace'}"
        try:
            audio_raw = midi_result[0]
            if audio_raw and isinstance(audio_raw, str) and os.path.exists(audio_raw):
                ext = pathlib.Path(audio_raw).suffix or ".mp3"
                wav_path = str(_artifacts_dir / f"{_artifact_id}{ext}")
                shutil.copy2(audio_raw, wav_path)
                logger.info(f"🔊 Audio artifact saved: {wav_path}")
        except Exception as exc:
            logger.warning(f"⚠️ Failed to capture audio artifact: {exc}")
        try:
            plot_raw = midi_result[1]
            if isinstance(plot_raw, dict) and "plot" in plot_raw:
                import base64 as _b64
                data_uri: str = plot_raw["plot"]
                _header, _, b64_data = data_uri.partition(",")
                img_ext = ".webp" if "webp" in _header else ".png"
                plot_path = str(_artifacts_dir / f"{_artifact_id}{img_ext}")
                pathlib.Path(plot_path).write_bytes(_b64.b64decode(b64_data))
                logger.info(f"📊 Plot artifact saved: {plot_path}")
            elif plot_raw and isinstance(plot_raw, str) and os.path.exists(plot_raw):
                plot_path = str(_artifacts_dir / f"{_artifact_id}.png")
                shutil.copy2(plot_raw, plot_path)
                logger.info(f"📊 Plot artifact saved: {plot_path}")
        except Exception as exc:
            logger.warning(f"⚠️ Failed to capture plot artifact: {exc}")
        try:
            midi_copy = str(_artifacts_dir / f"{_artifact_id}.mid")
            shutil.copy2(midi_path, midi_copy)
            logger.info(f"🎵 MIDI artifact saved: {midi_copy}")
        except Exception as exc:
            logger.warning(f"⚠️ Failed to copy MIDI artifact: {exc}")

        # ── Post-processing pipeline ──
        _post_processor = build_post_processor(
            generation_constraints=request.generation_constraints,
            role_profile_summary=request.role_profile_summary,
        )
        snake_notes = _post_processor.process(snake_notes)

        # Rebuild wire-format notes from post-processed snake_notes
        mvp_notes = [
            WireNoteDict(pitch=n["pitch"], startBeat=n["start_beat"],
                         durationBeats=n["duration_beats"], velocity=n["velocity"])
            for n in snake_notes
        ]

        score = rejection_score(snake_notes, request.bars)

        comp_state.last_token_estimate += num_gen_tokens
        comp_state.accumulated_midi_path = midi_path

        _ctx_window = 8192
        _ctx_pct = (num_prime_tokens + num_gen_tokens) / _ctx_window * 100
        logger.info(
            f"✅ MVP: {len(mvp_notes)} notes, score={score:.3f}, "
            f"ctx={_ctx_pct:.0f}%, batch={batch_idx}"
        )

        # ── Build metadata ──
        controls = build_controls(
            genre=request.genre,
            tempo=request.tempo,
            emotion_vector=request.emotion_vector or None,
            role_profile_summary=request.role_profile_summary or None,
            generation_constraints=request.generation_constraints or None,
            intent_goals=request.intent_goals or None,
            quality_preset=request.quality_preset,
        )


        metadata: dict[str, object] = {
            "policy_version": get_policy_version(),
            "params_used": {
                "temperature": temperature,
                "top_p": top_p,
                "prime_tokens": num_prime_tokens,
                "gen_tokens": num_gen_tokens,
            },
            "cache_hit": False,
            "note_count": len(mvp_notes),
            "rejection_score": round(score, 3),
            "controls_used": {
                "creativity": round(controls.creativity, 3),
                "density": round(controls.density, 3),
                "complexity": round(controls.complexity, 3),
                "brightness": round(controls.brightness, 3),
                "tension": round(controls.tension, 3),
                "groove": round(controls.groove, 3),
            },
            "fulfillment_report": build_fulfillment_report(
                snake_notes, request.bars, controls,
                generation_constraints=request.generation_constraints,
            ),
            "candidate_selection": {
                "candidates_evaluated": len(all_candidate_scores),
                "quality_preset": request.quality_preset,
                "selected_batch": batch_idx,
                "selected_score": best_score.total_score if best_score else 0.0,
                "selected_key": best_score.detected_key if best_score else None,
                "score_dimensions": best_score.dimensions if best_score else {},
                "all_scores": all_candidate_scores,
            },
            "post_processing": {
                "transforms_applied": _post_processor.transforms_applied,
            },
            "seed_provenance": {
                "seed_source_type": seed_source_type,
                "seed_file_path": seed_path,
                "seed_uri": seed_uri,
                "seed_hash": seed_hash,
                "seed_detected_key": resolved.detected_key,
                "seed_key_confidence": resolved.key_confidence,
                "seed_transpose_semitones": resolved.transpose_semitones,
                "target_key": request.key,
                **{k: v for k, v in seed_report.items() if k != "quality_ok"},
            },
            "gradio_inputs": _gradio_inputs,
        }
        if wav_path:
            metadata["wav_path"] = wav_path
        if plot_path:
            metadata["plot_path"] = plot_path
        metadata["midi_path"] = midi_path
        if _trace:
            metadata["trace_id"] = _trace
        if request.intent_hash:
            metadata["intent_hash"] = request.intent_hash
        if request.seed is not None:
            metadata["seed"] = request.seed

        # ── Build notes: unified (labeled by channel) or flat ──
        if request.unified_output:
            _prog_changes = parsed["program_changes"]
            channel_notes: dict[str, list[WireNoteDict]] = {}
            for ch_key, ch_notes in parsed["notes"].items():
                label = _channel_label(ch_key, program_changes=_prog_changes)
                channel_notes[label] = [
                    WireNoteDict(
                        pitch=n["pitch"],
                        startBeat=n["start_beat"],
                        durationBeats=n["duration_beats"],
                        velocity=n["velocity"],
                    )
                    for n in ch_notes
                ]
            metadata["unified_channels"] = list(channel_notes.keys())
            metadata["program_changes"] = {
                str(k): v for k, v in _prog_changes.items()
            }
            response_notes_unified = channel_notes
        else:
            response_notes_unified = None

        _response = GenerateResponse(
            success=True,
            notes=mvp_notes,
            error=None,
            metadata=metadata,
            channel_notes=response_notes_unified,
        )
        cache_result(
            cache_key,
            _response.model_dump(),
            key_data=_cache_key_data(request),
        )
        _last_successful_gen = time()

        return _response

    except (Exception, asyncio.CancelledError) as e:
        err_msg = str(e) or type(e).__name__
        logger.error(f"❌ Generation failed: {err_msg}")
        logger.debug(f"❌ Traceback:\n{traceback.format_exc()}")
        _err_str = err_msg.lower()
        _is_transient = any(kw in _err_str for kw in (
            "connection", "refused", "reset", "eof", "broken pipe",
            "upstream gradio app has raised an exception",
        ))
        if _is_transient:
            _client_pool.reset(worker_id)
            logger.info(f"🔄 Reset client for worker {worker_id} after transient error")
        return GenerateResponse(
            success=False,
            error=err_msg,
        )


def _job_response(job: Job) -> dict[str, object]:
    """Serialize a Job to the wire format used by /generate and /jobs endpoints."""
    resp: dict[str, object] = {
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


@app.post("/generate", response_model=None)
async def generate(request: GenerateRequest) -> dict[str, object] | JSONResponse:
    """Submit a generation job.  Cache hits return immediately; misses enqueue."""
    assert _job_queue is not None, "JobQueue not initialized"

    cache_key = get_cache_key(request)
    cached = get_cached_result(cache_key)
    if cached:
        _cached_meta = cached.get("metadata")
        if isinstance(_cached_meta, dict):
            _cached_meta["cache_hit"] = True
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


@app.get("/jobs/{job_id}", response_model=None)
async def get_job(job_id: str) -> dict[str, object] | JSONResponse:
    """Return current status of a submitted job."""
    assert _job_queue is not None
    job = _job_queue.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    return _job_response(job)


@app.get("/jobs/{job_id}/wait", response_model=None)
async def wait_for_job(
    job_id: str,
    timeout: float = Query(default=30, ge=1, le=120),
) -> dict[str, object] | JSONResponse:
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


@app.post("/jobs/{job_id}/cancel", response_model=None)
async def cancel_job(job_id: str) -> dict[str, object] | JSONResponse:
    """Cancel a queued or running job."""
    assert _job_queue is not None
    job = _job_queue.cancel(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    return _job_response(job)


@app.get("/queue/status")
async def queue_status() -> dict[str, object]:
    """Diagnostics: current queue depth, running workers, limits."""
    if _job_queue is None:
        return {"error": "JobQueue not initialized"}
    return _job_queue.status_snapshot()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10002)
