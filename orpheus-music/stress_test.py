#!/usr/bin/env python3
"""
Orpheus Stress Test — Comprehensive throughput, latency, and quality assessment.

Sends a matrix of generation requests spanning every genre, instrument combo,
bar count, quality preset, and intent vector extreme the system supports.
Captures per-request latency, token throughput, note density, and error rates.

Usage:
    # Quick smoke test (1 request per genre, fast preset)
    python stress_test.py --quick

    # Standard sweep (all genres × bar counts × presets)
    python stress_test.py

    # Full matrix (all genres × instruments × bars × presets × intent vectors)
    python stress_test.py --full

    # Concurrency test (how many parallel requests before degradation)
    python stress_test.py --concurrency

    # Custom target
    python stress_test.py --url http://my-orpheus:10002

Results are written to stress_results_{timestamp}.json with a human-readable
summary printed to stdout.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

ORPHEUS_URL = "http://localhost:10002"

GENRES = [
    "boom_bap", "trap", "house", "techno", "jazz", "neo_soul",
    "classical", "cinematic", "ambient", "reggae", "funk",
    "drum_and_bass", "dubstep", "drill", "lofi",
]

INSTRUMENT_COMBOS = [
    ["drums"],
    ["drums", "bass"],
    ["drums", "bass", "piano"],
    ["drums", "bass", "guitar"],
    ["drums", "bass", "piano", "guitar"],
    ["bass"],
    ["piano"],
]

BAR_COUNTS = [4, 8, 16, 32]

QUALITY_PRESETS = ["fast", "balanced", "quality"]

KEYS = [None, "C", "Am", "F#", "Bb", "Em"]

INTENT_VECTORS = {
    "neutral": {
        "tone_brightness": 0.0, "tone_warmth": 0.0,
        "energy_intensity": 0.0, "energy_excitement": 0.0,
        "complexity": 0.5,
    },
    "dark_intense": {
        "tone_brightness": -0.9, "tone_warmth": -0.5,
        "energy_intensity": 0.9, "energy_excitement": 0.7,
        "complexity": 0.8,
        "musical_goals": ["dark", "energetic", "intense"],
    },
    "bright_chill": {
        "tone_brightness": 0.8, "tone_warmth": 0.7,
        "energy_intensity": -0.6, "energy_excitement": -0.3,
        "complexity": 0.3,
        "musical_goals": ["bright", "chill", "peaceful"],
    },
    "cinematic_build": {
        "tone_brightness": 0.2, "tone_warmth": 0.4,
        "energy_intensity": 0.6, "energy_excitement": 0.8,
        "complexity": 0.9,
        "musical_goals": ["cinematic", "dense", "intense"],
    },
    "minimal_sparse": {
        "tone_brightness": -0.2, "tone_warmth": 0.1,
        "energy_intensity": -0.8, "energy_excitement": -0.7,
        "complexity": 0.1,
        "musical_goals": ["minimal", "calm"],
    },
    "club_ready": {
        "tone_brightness": 0.3, "tone_warmth": -0.2,
        "energy_intensity": 0.7, "energy_excitement": 0.9,
        "complexity": 0.4,
        "musical_goals": ["club", "energetic"],
    },
    "max_complexity": {
        "tone_brightness": 0.0, "tone_warmth": 0.0,
        "energy_intensity": 0.5, "energy_excitement": 0.5,
        "complexity": 1.0,
        "musical_goals": ["dense", "maximal"],
    },
    "extreme_low": {
        "tone_brightness": -1.0, "tone_warmth": -1.0,
        "energy_intensity": -1.0, "energy_excitement": -1.0,
        "complexity": 0.0,
    },
    "extreme_high": {
        "tone_brightness": 1.0, "tone_warmth": 1.0,
        "energy_intensity": 1.0, "energy_excitement": 1.0,
        "complexity": 1.0,
    },
}

TEMPO_MAP = {
    "boom_bap": 90, "trap": 140, "house": 124, "techno": 130,
    "jazz": 110, "neo_soul": 75, "classical": 100, "cinematic": 95,
    "ambient": 70, "reggae": 80, "funk": 105, "drum_and_bass": 174,
    "dubstep": 140, "drill": 145, "lofi": 82,
}


@dataclass
class RequestResult:
    """Captures everything about a single generation attempt."""
    genre: str
    tempo: int
    instruments: list[str]
    bars: int
    quality_preset: str
    intent_profile: str
    key: Optional[str]

    success: bool = False
    error: Optional[str] = None
    latency_ms: float = 0.0
    note_count: int = 0
    tool_call_count: int = 0
    track_count: int = 0
    region_count: int = 0
    cache_hit: bool = False
    policy_version: Optional[str] = None
    tokens_requested: int = 0
    http_status: int = 0

    temperature: float = 0.0
    top_p: float = 0.0


@dataclass
class StressReport:
    """Aggregated results from a full stress run."""
    started_at: str = ""
    finished_at: str = ""
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    cache_hits: int = 0
    errors: dict = field(default_factory=dict)

    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    latency_mean_ms: float = 0.0
    latency_max_ms: float = 0.0
    latency_min_ms: float = 0.0

    avg_notes_per_bar: float = 0.0
    avg_notes_per_request: float = 0.0
    total_notes_generated: int = 0

    per_genre: dict = field(default_factory=dict)
    per_preset: dict = field(default_factory=dict)
    per_bar_count: dict = field(default_factory=dict)
    per_intent: dict = field(default_factory=dict)
    per_instrument_combo: dict = field(default_factory=dict)
    concurrency_results: list = field(default_factory=list)

    results: list = field(default_factory=list)


def build_payload(
    genre: str,
    instruments: list[str],
    bars: int,
    quality_preset: str,
    intent_profile: str,
    key: Optional[str] = None,
    temperature_override: Optional[float] = None,
    top_p_override: Optional[float] = None,
) -> dict:
    intent = INTENT_VECTORS.get(intent_profile, INTENT_VECTORS["neutral"])
    payload = {
        "genre": genre,
        "tempo": TEMPO_MAP.get(genre, 120),
        "instruments": instruments,
        "bars": bars,
        "quality_preset": quality_preset,
        "tone_brightness": intent["tone_brightness"],
        "tone_warmth": intent["tone_warmth"],
        "energy_intensity": intent["energy_intensity"],
        "energy_excitement": intent["energy_excitement"],
        "complexity": intent["complexity"],
    }
    if key:
        payload["key"] = key
    if intent.get("musical_goals"):
        payload["musical_goals"] = intent["musical_goals"]
    if temperature_override is not None:
        payload["temperature"] = temperature_override
    if top_p_override is not None:
        payload["top_p"] = top_p_override
    return payload


async def send_request(
    client: httpx.AsyncClient,
    payload: dict,
    intent_profile: str,
    timeout: float = 300.0,
) -> RequestResult:
    result = RequestResult(
        genre=payload["genre"],
        tempo=payload["tempo"],
        instruments=payload["instruments"],
        bars=payload["bars"],
        quality_preset=payload["quality_preset"],
        intent_profile=intent_profile,
        key=payload.get("key"),
        temperature=payload.get("temperature", 0.0),
        top_p=payload.get("top_p", 0.0),
    )

    start = time.monotonic()
    try:
        resp = await client.post(
            "/generate",
            json=payload,
            timeout=timeout,
        )
        elapsed = (time.monotonic() - start) * 1000
        result.latency_ms = round(elapsed, 1)
        result.http_status = resp.status_code

        if resp.status_code != 200:
            result.error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            return result

        data = resp.json()
        result.success = data.get("success", False)
        result.error = data.get("error")
        result.tool_call_count = len(data.get("tool_calls", []))

        meta = data.get("metadata") or {}
        result.cache_hit = meta.get("cache_hit", False)
        result.policy_version = meta.get("policy_version")

        for tc in data.get("tool_calls", []):
            tool = tc.get("tool", "")
            if tool == "addNotes":
                result.note_count += len(tc.get("params", {}).get("notes", []))
            elif tool == "addMidiTrack":
                result.track_count += 1
            elif tool == "addMidiRegion":
                result.region_count += 1

    except httpx.TimeoutException:
        elapsed = (time.monotonic() - start) * 1000
        result.latency_ms = round(elapsed, 1)
        result.error = f"Timeout after {timeout}s"
    except httpx.ConnectError as e:
        result.error = f"Connection error: {e}"
    except Exception as e:
        result.error = f"Unexpected: {type(e).__name__}: {e}"

    return result


def compute_percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = (len(sorted_vals) - 1) * p
    lower = int(idx)
    upper = min(lower + 1, len(sorted_vals) - 1)
    weight = idx - lower
    return sorted_vals[lower] * (1 - weight) + sorted_vals[upper] * weight


def group_stats(results: list[RequestResult], key_fn) -> dict:
    groups: dict[str, list[RequestResult]] = {}
    for r in results:
        k = key_fn(r)
        groups.setdefault(k, []).append(r)

    stats = {}
    for k, items in sorted(groups.items()):
        successful = [r for r in items if r.success]
        latencies = [r.latency_ms for r in successful]
        notes = [r.note_count for r in successful]
        stats[k] = {
            "total": len(items),
            "success": len(successful),
            "failed": len(items) - len(successful),
            "cache_hits": sum(1 for r in items if r.cache_hit),
            "latency_mean_ms": round(statistics.mean(latencies), 1) if latencies else 0,
            "latency_p95_ms": round(compute_percentile(latencies, 0.95), 1) if latencies else 0,
            "avg_notes": round(statistics.mean(notes), 1) if notes else 0,
        }
    return stats


def build_report(results: list[RequestResult]) -> StressReport:
    report = StressReport()
    report.total_requests = len(results)
    report.successful = sum(1 for r in results if r.success)
    report.failed = sum(1 for r in results if not r.success)
    report.cache_hits = sum(1 for r in results if r.cache_hit)
    report.results = [asdict(r) for r in results]

    errors: dict[str, int] = {}
    for r in results:
        if r.error:
            bucket = r.error[:80]
            errors[bucket] = errors.get(bucket, 0) + 1
    report.errors = errors

    successful = [r for r in results if r.success]
    latencies = [r.latency_ms for r in successful]
    if latencies:
        report.latency_mean_ms = round(statistics.mean(latencies), 1)
        report.latency_p50_ms = round(compute_percentile(latencies, 0.50), 1)
        report.latency_p95_ms = round(compute_percentile(latencies, 0.95), 1)
        report.latency_p99_ms = round(compute_percentile(latencies, 0.99), 1)
        report.latency_max_ms = round(max(latencies), 1)
        report.latency_min_ms = round(min(latencies), 1)

    notes = [r.note_count for r in successful]
    if notes:
        report.total_notes_generated = sum(notes)
        report.avg_notes_per_request = round(statistics.mean(notes), 1)

    bars_notes = [(r.bars, r.note_count) for r in successful if r.bars > 0]
    if bars_notes:
        report.avg_notes_per_bar = round(
            sum(n for _, n in bars_notes) / sum(b for b, _ in bars_notes), 1
        )

    report.per_genre = group_stats(results, lambda r: r.genre)
    report.per_preset = group_stats(results, lambda r: r.quality_preset)
    report.per_bar_count = group_stats(results, lambda r: str(r.bars))
    report.per_intent = group_stats(results, lambda r: r.intent_profile)
    report.per_instrument_combo = group_stats(
        results, lambda r: "+".join(r.instruments)
    )

    return report


def print_report(report: StressReport) -> None:
    W = 72
    print("\n" + "=" * W)
    print("  ORPHEUS STRESS TEST REPORT")
    print("=" * W)
    print(f"  Started:  {report.started_at}")
    print(f"  Finished: {report.finished_at}")
    print(f"  Total requests:   {report.total_requests}")
    print(f"  Successful:       {report.successful}")
    print(f"  Failed:           {report.failed}")
    print(f"  Cache hits:       {report.cache_hits}")
    print()

    print("─" * W)
    print("  LATENCY (successful requests only)")
    print("─" * W)
    print(f"  Min:    {report.latency_min_ms:>10.1f} ms")
    print(f"  Mean:   {report.latency_mean_ms:>10.1f} ms")
    print(f"  P50:    {report.latency_p50_ms:>10.1f} ms")
    print(f"  P95:    {report.latency_p95_ms:>10.1f} ms")
    print(f"  P99:    {report.latency_p99_ms:>10.1f} ms")
    print(f"  Max:    {report.latency_max_ms:>10.1f} ms")
    print()

    print("─" * W)
    print("  NOTE OUTPUT")
    print("─" * W)
    print(f"  Total notes generated: {report.total_notes_generated}")
    print(f"  Avg notes/request:     {report.avg_notes_per_request}")
    print(f"  Avg notes/bar:         {report.avg_notes_per_bar}")
    print()

    def _print_breakdown(title: str, data: dict) -> None:
        print("─" * W)
        print(f"  {title}")
        print("─" * W)
        hdr = f"  {'Key':<22} {'OK':>4} {'Fail':>4} {'Mean ms':>9} {'P95 ms':>9} {'Notes':>6}"
        print(hdr)
        for k, v in data.items():
            print(
                f"  {k:<22} {v['success']:>4} {v['failed']:>4} "
                f"{v['latency_mean_ms']:>9.1f} {v['latency_p95_ms']:>9.1f} "
                f"{v['avg_notes']:>6.1f}"
            )
        print()

    _print_breakdown("BY GENRE", report.per_genre)
    _print_breakdown("BY QUALITY PRESET", report.per_preset)
    _print_breakdown("BY BAR COUNT", report.per_bar_count)
    _print_breakdown("BY INTENT PROFILE", report.per_intent)
    _print_breakdown("BY INSTRUMENT COMBO", report.per_instrument_combo)

    if report.errors:
        print("─" * W)
        print("  ERROR BREAKDOWN")
        print("─" * W)
        for err, count in sorted(report.errors.items(), key=lambda x: -x[1]):
            print(f"  [{count:>3}x] {err}")
        print()

    if report.concurrency_results:
        print("─" * W)
        print("  CONCURRENCY SCALING")
        print("─" * W)
        hdr = f"  {'Parallel':>8} {'OK':>4} {'Fail':>4} {'Mean ms':>10} {'P95 ms':>10} {'Total ms':>10}"
        print(hdr)
        for cr in report.concurrency_results:
            print(
                f"  {cr['concurrency']:>8} {cr['success']:>4} {cr['failed']:>4} "
                f"{cr['latency_mean_ms']:>10.1f} {cr['latency_p95_ms']:>10.1f} "
                f"{cr['wall_time_ms']:>10.1f}"
            )
        print()

    print("=" * W)


# ── Test scenario builders ───────────────────────────────────────────


def build_quick_scenarios() -> list[tuple[dict, str]]:
    """One request per genre, fast preset, 4 bars, drums+bass, neutral intent."""
    scenarios = []
    for genre in GENRES:
        payload = build_payload(
            genre=genre,
            instruments=["drums", "bass"],
            bars=4,
            quality_preset="fast",
            intent_profile="neutral",
        )
        scenarios.append((payload, "neutral"))
    return scenarios


def build_standard_scenarios() -> list[tuple[dict, str]]:
    """Every genre × every bar count × every preset. Drums+bass, neutral."""
    scenarios = []
    for genre in GENRES:
        for bars in BAR_COUNTS:
            for preset in QUALITY_PRESETS:
                payload = build_payload(
                    genre=genre,
                    instruments=["drums", "bass"],
                    bars=bars,
                    quality_preset=preset,
                    intent_profile="neutral",
                )
                scenarios.append((payload, "neutral"))
    return scenarios


def build_full_scenarios() -> list[tuple[dict, str]]:
    """Full matrix: genres × instruments × bars × presets × intent vectors."""
    scenarios = []
    for genre in GENRES:
        for instruments in INSTRUMENT_COMBOS:
            for bars in [4, 8, 16]:
                for preset in ["fast", "balanced"]:
                    for intent_name in INTENT_VECTORS:
                        payload = build_payload(
                            genre=genre,
                            instruments=instruments,
                            bars=bars,
                            quality_preset=preset,
                            intent_profile=intent_name,
                            key="Am" if intent_name == "dark_intense" else None,
                        )
                        scenarios.append((payload, intent_name))
    return scenarios


def build_parameter_sweep_scenarios() -> list[tuple[dict, str]]:
    """Sweep temperature and top_p extremes to find quality boundaries."""
    scenarios = []
    temperatures = [0.70, 0.80, 0.90, 1.00, 1.10]
    top_ps = [0.90, 0.95, 0.99]
    for temp in temperatures:
        for tp in top_ps:
            payload = build_payload(
                genre="jazz",
                instruments=["drums", "bass", "piano"],
                bars=8,
                quality_preset="balanced",
                intent_profile="neutral",
                temperature_override=temp,
                top_p_override=tp,
            )
            scenarios.append((payload, f"temp={temp}_tp={tp}"))
    return scenarios


async def run_concurrency_test(
    url: str, max_concurrent: int = 16
) -> list[dict]:
    """Send increasing numbers of parallel requests to find the throughput ceiling."""
    concurrency_levels = [1, 2, 4, 8, 12, max_concurrent]
    results = []

    for n in concurrency_levels:
        print(f"  Concurrency={n} ... ", end="", flush=True)
        async with httpx.AsyncClient(base_url=url) as client:
            payload = build_payload(
                genre="boom_bap",
                instruments=["drums", "bass"],
                bars=4,
                quality_preset="fast",
                intent_profile="neutral",
            )

            wall_start = time.monotonic()
            tasks = [
                send_request(client, payload, "neutral", timeout=300.0)
                for _ in range(n)
            ]
            batch_results = await asyncio.gather(*tasks)
            wall_ms = (time.monotonic() - wall_start) * 1000

            successful = [r for r in batch_results if r.success]
            latencies = [r.latency_ms for r in successful]
            entry = {
                "concurrency": n,
                "success": len(successful),
                "failed": n - len(successful),
                "latency_mean_ms": round(statistics.mean(latencies), 1) if latencies else 0,
                "latency_p95_ms": round(compute_percentile(latencies, 0.95), 1) if latencies else 0,
                "wall_time_ms": round(wall_ms, 1),
            }
            results.append(entry)
            print(
                f"OK={entry['success']} Fail={entry['failed']} "
                f"Mean={entry['latency_mean_ms']:.0f}ms Wall={entry['wall_time_ms']:.0f}ms"
            )

    return results


async def run_scenarios(
    url: str,
    scenarios: list[tuple[dict, str]],
    max_parallel: int = 4,
    timeout: float = 300.0,
) -> list[RequestResult]:
    """Run scenarios with bounded parallelism."""
    sem = asyncio.Semaphore(max_parallel)
    results: list[RequestResult] = []
    total = len(scenarios)
    completed = 0

    async def _run(payload: dict, intent: str, idx: int) -> RequestResult:
        nonlocal completed
        async with sem:
            async with httpx.AsyncClient(base_url=url) as client:
                r = await send_request(client, payload, intent, timeout=timeout)
                completed += 1
                status = "OK" if r.success else f"FAIL: {r.error or 'unknown'}"
                print(
                    f"  [{completed:>4}/{total}] {r.genre:<16} {r.bars}bar "
                    f"{r.quality_preset:<10} {intent:<20} "
                    f"{r.latency_ms:>8.0f}ms  {status}"
                )
                return r

    tasks = [
        _run(payload, intent, i)
        for i, (payload, intent) in enumerate(scenarios)
    ]
    results = await asyncio.gather(*tasks)
    return list(results)


async def check_health(url: str) -> bool:
    """Verify Orpheus is reachable before running the suite."""
    try:
        async with httpx.AsyncClient(base_url=url) as client:
            resp = await client.get("/health", timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                print(f"  Orpheus healthy: {data}")
                return True
            print(f"  Orpheus returned HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"  Cannot reach Orpheus at {url}: {e}")
        return False


async def check_diagnostics(url: str) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(base_url=url) as client:
            resp = await client.get("/diagnostics", timeout=10.0)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return None


async def main() -> None:
    parser = argparse.ArgumentParser(description="Orpheus Stress Test")
    parser.add_argument("--url", default=ORPHEUS_URL, help="Orpheus base URL")
    parser.add_argument("--quick", action="store_true", help="Quick smoke test (15 requests)")
    parser.add_argument("--full", action="store_true", help="Full matrix (thousands of requests)")
    parser.add_argument("--sweep", action="store_true", help="Parameter sweep (temperature × top_p)")
    parser.add_argument("--concurrency", action="store_true", help="Concurrency scaling test")
    parser.add_argument("--parallel", type=int, default=4, help="Max parallel requests")
    parser.add_argument("--timeout", type=float, default=300.0, help="Per-request timeout (s)")
    parser.add_argument("--output", default=None, help="Output JSON file path")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════════════════════╗")
    print("║         ORPHEUS STRESS TEST — the infinite riff          ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    print("▸ Checking Orpheus health...")
    healthy = await check_health(args.url)
    if not healthy:
        print("  ✗ Orpheus is not reachable. Aborting.")
        sys.exit(1)

    diag = await check_diagnostics(args.url)
    if diag:
        print(f"  Diagnostics: {json.dumps(diag, indent=2)}")
    print()

    # Build scenario list
    scenarios: list[tuple[dict, str]] = []
    mode = "standard"

    if args.quick:
        mode = "quick"
        scenarios = build_quick_scenarios()
    elif args.full:
        mode = "full"
        scenarios = build_full_scenarios()
    elif args.sweep:
        mode = "sweep"
        scenarios = build_parameter_sweep_scenarios()
    elif args.concurrency:
        mode = "concurrency"
    else:
        scenarios = build_standard_scenarios()

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report = StressReport()
    report.started_at = datetime.now(timezone.utc).isoformat()

    if mode == "concurrency":
        print(f"▸ Running concurrency scaling test against {args.url}")
        print()
        report.concurrency_results = await run_concurrency_test(
            args.url, max_concurrent=args.parallel * 2
        )
        # Also run a quick sweep for baseline metrics
        print()
        print("▸ Running baseline sweep (quick)...")
        scenarios = build_quick_scenarios()
        results = await run_scenarios(
            args.url, scenarios, max_parallel=args.parallel, timeout=args.timeout
        )
        report = build_report(results)
        report.concurrency_results = await run_concurrency_test(
            args.url, max_concurrent=args.parallel * 2
        )
    else:
        print(f"▸ Mode: {mode}")
        print(f"  Scenarios: {len(scenarios)}")
        print(f"  Parallel:  {args.parallel}")
        print(f"  Timeout:   {args.timeout}s")
        print()
        results = await run_scenarios(
            args.url, scenarios, max_parallel=args.parallel, timeout=args.timeout
        )
        report = build_report(results)

        if args.sweep:
            # Append a parameter sweep section
            print("\n▸ Appending parameter sweep...")
            sweep = build_parameter_sweep_scenarios()
            sweep_results = await run_scenarios(
                args.url, sweep, max_parallel=2, timeout=args.timeout
            )
            sweep_report = build_report(sweep_results)
            report.per_intent.update(sweep_report.per_intent)

    report.finished_at = datetime.now(timezone.utc).isoformat()

    # Post-run diagnostics
    diag_after = await check_diagnostics(args.url)
    if diag_after:
        print(f"\n▸ Post-run diagnostics: {json.dumps(diag_after, indent=2)}")

    print_report(report)

    # Save JSON
    out_path = args.output or f"stress_results_{ts}.json"
    report_dict = asdict(report)
    with open(out_path, "w") as f:
        json.dump(report_dict, f, indent=2, default=str)
    print(f"  Full results saved to: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
